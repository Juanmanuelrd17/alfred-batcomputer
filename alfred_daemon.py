#!/usr/bin/env python3
"""
'PUTER // ALFRED BATCOMPUTER NATIVE DESKTOP APP, FLOATING HUD & VOICE DAEMON
Features:
- Wake words: "Computer", "Puter", "Hey Computer", "Hey Puter" (plus "Alfred")
- Live Desktop Notifications (notify-send / macOS Notification Center) when hearing you & with full response transcription
- Native PyQt6 Floating Cyberpunk Mini-HUD Widget + System Tray Icon (works natively on Hyprland/Wayland & macOS)
- 400ms Pre-Roll Ring Buffer so standalone "Computer" / "Puter" never clips the first syllable
- Smart PipeWire / PulseAudio Microphone Auto-Detection & Hot-Plug Switching (AirPods / Razer Barracuda X)
- Instant Barge-In Interruption (cuts off 'Puter's voice the moment you speak)
"""

import os
import io
import re
import sys

# Ensure QtWebEngine runs with rock-solid flags on Wayland/NVIDIA to avoid std::bad_alloc or GBM crashes
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-dev-shm-usage --in-process-gpu")
import math
import time
import wave
import json
import array
import base64
import shutil
import socket
import urllib.parse
import argparse
import tempfile
import threading
import subprocess
import fcntl
from datetime import datetime, date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import deque
from pathlib import Path
from typing import Optional, Tuple, Callable
import requests

BASE_DIR = Path(__file__).resolve().parent

DEFAULT_SERVER_URL = os.environ.get("ALFRED_SERVER_URL", "https://batcomputer.jmrdserver.duckdns.org")
FALLBACK_SERVER_URL = "http://100.77.249.27:7776"
DEFAULT_HUD_URL = os.environ.get("ALFRED_HUD_URL", "https://batcomputer.jmrdserver.duckdns.org")
LOCAL_BRIDGE_PORT = int(os.environ.get("ALFRED_LOCAL_BRIDGE_PORT", "7778"))

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_DURATION_S = 0.1  # 100ms frames
CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_DURATION_S)
PRE_ROLL_CHUNKS = 4     # 400ms pre-roll buffer so "Computer" / "Puter" never clips the first syllable

# VAD thresholds (generous timing so long sentences, natural pauses and complex queries are never cut off)
SPEECH_RMS_THRESHOLD = float(os.environ.get("ALFRED_VAD_THRESHOLD", "0.024"))
BARGE_IN_RMS_THRESHOLD = float(os.environ.get("ALFRED_BARGE_THRESHOLD", "0.055"))
SILENCE_LIMIT_S = float(os.environ.get("ALFRED_SILENCE_LIMIT", "1.85"))   # 1.85s natural pause tolerance between phrases
MAX_RECORD_S = float(os.environ.get("ALFRED_MAX_RECORD_S", "35.0"))        # 35s max recording length for lengthy directives

# Matches "Computer", "Puter", "Hey Computer", "Hey Puter", and common Whisper phonetic homophones
WAKE_WORD_REGEX = re.compile(
    r'\b(?:(?:hey|ok|yo|hi)\s+)?(?:computer|computa|commuter|puter|pewter|purer|putter|peter|tutor|tudor|ooter|cuter|scooter|alfred)\b',
    re.IGNORECASE
)
LEADING_WAKE_STRIP_REGEX = re.compile(
    r'^(?:(?:hey|ok|yo|hi)\s+)?(?:computer|computa|commuter|puter|pewter|purer|putter|peter|tutor|tudor|ooter|cuter|scooter|alfred)[\s,.!:;-]*',
    re.IGNORECASE
)


def detect_best_linux_mic_source() -> Tuple[Optional[str], bool]:
    """
    Inspect PipeWire/PulseAudio sources via pactl.
    Returns (source_name, is_real_mic).
    Prefers Bluetooth headsets (AirPods / Razer BT), USB headsets (Razer Barracuda X 2.4), or analog mics
    over 'JieLi_Technology_USB_Audio' (desktop USB speaker bar with a silent/loopback capture channel).
    """
    if not shutil.which("pactl"):
        return None, True
    try:
        res = subprocess.run(["pactl", "list", "sources", "short"], capture_output=True, text=True, timeout=2)
        if res.returncode != 0:
            return None, True
        real_mics = []
        jieli_fallback = None
        for line in res.stdout.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            name = parts[1]
            if ".monitor" in name:
                continue
            if "JieLi_Technology_USB_Audio" in name:
                jieli_fallback = name
            else:
                real_mics.append(name)

        if real_mics:
            for preferred in ("Razer", "Barracuda", "bluez_input"):
                for m in real_mics:
                    if preferred.lower() in m.lower():
                        return m, True
            return real_mics[0], True

        if jieli_fallback:
            return jieli_fallback, True
    except Exception:
        pass
    return None, True


class DesktopNotifier:
    """Sends in-place updating desktop notifications on Linux (notify-send) and macOS (osascript)."""
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._last_id: Optional[str] = None

    def notify(self, title: str, body: str, timeout_ms: int = 6000, urgency: str = "normal"):
        if not self.enabled:
            return
        try:
            if sys.platform == "darwin":
                safe_title = title.replace('"', '\\"')
                safe_body = body.replace('"', '\\"')
                subprocess.Popen(
                    ["osascript", "-e", f'display notification "{safe_body}" with title "{safe_title}"'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            elif sys.platform == "win32":
                safe_title = title.replace('"', '`"').replace("'", "''")
                safe_body = body.replace('"', '`"').replace("'", "''")
                ps = (
                    f"[void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms');"
                    f"$n = New-Object System.Windows.Forms.NotifyIcon;"
                    f"$n.Icon = [System.Drawing.SystemIcons]::Information;"
                    f"$n.BalloonTipTitle = '{safe_title}';"
                    f"$n.BalloonTipText = '{safe_body}';"
                    f"$n.Visible = $True;"
                    f"$n.ShowBalloonTip({timeout_ms});"
                )
                subprocess.Popen(["powershell", "-NoProfile", "-Command", ps], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif shutil.which("notify-send"):
                cmd = [
                    "notify-send",
                    "-a", "'Puter // Batcomputer",
                    "-u", urgency,
                    "-t", str(timeout_ms),
                    "-h", "string:x-canonical-private-synchronous:puter-batcomputer",
                ]
                if self._last_id:
                    cmd.extend(["-r", self._last_id, "-p", title, body])
                else:
                    cmd.extend(["-p", title, body])
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5)
                if res.returncode == 0 and res.stdout.strip().isdigit():
                    self._last_id = res.stdout.strip()
        except Exception:
            pass


class AlfredBackgroundDaemon:
    def __init__(
        self,
        server_url: str,
        wake_word_required: bool = True,
        voice: str = "daniel",
        notify_enabled: bool = True,
        ui_callback: Optional[Callable[[dict], None]] = None
    ):
        self.server_url = server_url.rstrip("/")
        self.wake_word_required = wake_word_required
        self.voice = voice
        self.notifier = DesktopNotifier(enabled=notify_enabled)
        self.ui_callback = ui_callback
        self.session = requests.Session()

        # Load Wayne Tech Auth Token for secure Batcomputer communication
        self.auth_token = os.environ.get("BATCOMPUTER_AUTH_TOKEN", "")
        if not self.auth_token:
            auth_file = Path(__file__).resolve().parent / "auth_config.json"
            if auth_file.exists():
                try:
                    cfg = json.loads(auth_file.read_text(encoding="utf-8"))
                    self.auth_token = cfg.get("daemon_token", "")
                except Exception:
                    pass
        if self.auth_token:
            self.session.headers["Authorization"] = f"Bearer {self.auth_token}"
            self.session.headers["X-Batcomputer-Token"] = self.auth_token

        self.active_player: Optional[subprocess.Popen] = None
        self.awaiting_followup_until = 0.0
        self.force_next_utterance = False
        self.force_stop_recording = False
        self.muted = False
        self._shutdown_cancelled = False
        self.running = True
        self.current_mic_name = "Default Input"
        self.current_mic_is_real = True
        self.current_state = "STANDBY"
        self.last_user_text = ""
        self.last_reply_text = ""
        self._chime_wav_high = self._build_chime_wav([880, 1320], duration_s=0.065)
        self._chime_wav_low = self._build_chime_wav([740, 520], duration_s=0.055)

    def emit_ui(self, **kwargs):
        if "state" in kwargs:
            self.current_state = kwargs["state"]
        if "user_text" in kwargs:
            self.last_user_text = kwargs["user_text"]
        if "reply_text" in kwargs:
            self.last_reply_text = kwargs["reply_text"]
        if self.ui_callback:
            try:
                self.ui_callback(kwargs)
            except Exception:
                pass

    def _build_chime_wav(self, freqs: list, duration_s: float = 0.065) -> str:
        path = Path(tempfile.gettempdir()) / f"puter_chime_{'_'.join(map(str, freqs))}.wav"
        if path.exists():
            return str(path)
        try:
            samples = array.array("h")
            for freq in freqs:
                n = int(SAMPLE_RATE * duration_s)
                for i in range(n):
                    env = math.sin(math.pi * i / n)
                    val = int(32767 * 0.16 * env * math.sin(2 * math.pi * freq * i / SAMPLE_RATE))
                    samples.append(val)
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(samples.tobytes())
        except Exception:
            pass
        return str(path)

    def register_with_server(self):
        try:
            payload = {
                "client_id": socket.gethostname(),
                "hostname": socket.gethostname(),
                "platform": sys.platform,
                "bridge_url": f"http://127.0.0.1:{LOCAL_BRIDGE_PORT}",
                "capabilities": ["screen_capture", "mic_input", "desktop_apps", "gpu_telemetry"],
                "version": "1.2"
            }
            self.session.post(f"{self.server_url}/api/clients/register", json=payload, timeout=3)
        except Exception:
            pass

    def check_server_connectivity(self):
        try:
            r = self.session.get(f"{self.server_url}/api/telemetry", timeout=4)
            if r.status_code == 200:
                self.register_with_server()
                return
        except Exception as e:
            print(f"⚠️ [Primary URL {self.server_url} unreachable: {e}] — switching to {FALLBACK_SERVER_URL}")
            self.server_url = FALLBACK_SERVER_URL
            self.register_with_server()

    def is_speaking(self) -> bool:
        return self.active_player is not None and self.active_player.poll() is None

    def stop_speaking(self):
        if self.active_player and self.active_player.poll() is None:
            try:
                self.active_player.terminate()
                self.active_player.wait(timeout=1)
            except Exception:
                pass
        self.active_player = None
        self.emit_ui(state="STANDBY")
        try:
            self.session.post(f"{self.server_url}/api/audio/stop", timeout=1.5)
        except Exception:
            pass

    def trigger_push_to_talk(self):
        """Allow clicking TALK in the companion app to immediately listen without requiring the wake word."""
        self.stop_speaking()
        self.play_chime(high=True)
        self.awaiting_followup_until = time.time() + 10.0
        self.force_next_utterance = True
        self.emit_ui(state="LISTENING (TALK NOW)", user_text="Speak your directive now...")
        self.notifier.notify("🎙️ 'Puter Listening", "Speak your directive now, Master Ramos...", timeout_ms=4000)

    def play_chime(self, high: bool = True):
        wav_path = self._chime_wav_high if high else self._chime_wav_low
        if sys.platform == "darwin":
            snd = "/System/Library/Sounds/Tink.aiff" if high else "/System/Library/Sounds/Pop.aiff"
            target = snd if Path(snd).exists() else wav_path
            subprocess.Popen(["afplay", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform == "win32":
            try:
                import winsound
                winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            except Exception:
                pass
        else:
            for cmd in (["pw-play", "--volume", "0.35", wav_path], ["paplay", wav_path], ["aplay", "-q", wav_path], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", wav_path]):
                if shutil.which(cmd[0]):
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    break

    def _on_playback_ended(self):
        """Called immediately when TTS audio finishes playing to open the 10s seamless conversation window."""
        self.awaiting_followup_until = time.time() + 10.0
        print("🦇 ['Puter]: Conversation window open (10s) — speak freely without wake word...")
        self.emit_ui(
            state="CONVO OPEN (10s)",
            reply_text="🦇 'Puter: Listening... (Speak freely within 10s without wake word)"
        )
        self.notifier.notify(
            "🎙️ 'Puter Listening (10s Window)",
            "Speak your follow-up freely without repeating the wake word...",
            timeout_ms=5000
        )

    def play_audio_url(self, audio_path_or_url: str):
        if not audio_path_or_url:
            self._on_playback_ended()
            return
        full_url = audio_path_or_url if audio_path_or_url.startswith("http") else f"{self.server_url}{audio_path_or_url}"
        try:
            r = self.session.get(full_url, timeout=6)
            if r.status_code != 200:
                self._on_playback_ended()
                return
            suffix = ".wav" if ".wav" in audio_path_or_url else ".mp3"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            tmp.write(r.content)
            tmp.close()

            self.stop_speaking()
            self.emit_ui(state="SPEAKING")
            player_proc = None
            if sys.platform == "darwin":
                player_proc = subprocess.Popen(["afplay", tmp.name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif sys.platform == "win32":
                if shutil.which("ffplay"):
                    player_proc = subprocess.Popen(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", tmp.name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    ps_cmd = f"$p = New-Object -ComObject 'WMPlayer.OCX'; $p.URL = '{tmp.name}'; $p.controls.play(); while($p.playState -ne 1) {{ Start-Sleep -Milliseconds 100 }}"
                    player_proc = subprocess.Popen(["powershell", "-NoProfile", "-Command", ps_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                for player_cmd in [
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", tmp.name],
                    ["paplay", tmp.name],
                    ["aplay", "-q", tmp.name],
                ]:
                    if shutil.which(player_cmd[0]):
                        player_proc = subprocess.Popen(player_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        break

            self.active_player = player_proc

            if player_proc:
                def _wait_and_trigger(proc):
                    try:
                        proc.wait()
                    except Exception:
                        pass
                    if self.active_player == proc:
                        self.active_player = None
                        self._on_playback_ended()
                threading.Thread(target=_wait_and_trigger, args=(player_proc,), daemon=True).start()
            else:
                self._on_playback_ended()

        except Exception as e:
            print(f"['Puter Audio Warning]: {e}")
            self._on_playback_ended()

    def capture_local_screen_b64(self) -> Optional[str]:
        tmp_img = Path(tempfile.gettempdir()) / f"alfred_screen_{os.getpid()}.png"
        try:
            if sys.platform == "darwin":
                subprocess.run(["/usr/sbin/screencapture", "-x", "-t", "png", str(tmp_img)], timeout=3, check=True)
            else:
                for cmd in [
                    ["grim", str(tmp_img)],
                    ["gnome-screenshot", "-f", str(tmp_img)],
                    ["import", "-window", "root", str(tmp_img)],
                ]:
                    if shutil.which(cmd[0]):
                        subprocess.run(cmd, timeout=3, check=True)
                        break
            if tmp_img.exists():
                b64 = base64.b64encode(tmp_img.read_bytes()).decode("utf-8")
                tmp_img.unlink(missing_ok=True)
                return b64
        except Exception:
            tmp_img.unlink(missing_ok=True)
        return None

    def send_text_directive(self, directive: str, stt_ms: Optional[int] = None):
        """Execute a directive (from voice or typed into the companion app) and emit notifications + TTS."""
        directive = directive.strip()
        if not directive:
            return

        stt_tag = f" ({stt_ms}ms STT)" if stt_ms is not None else ""
        self.emit_ui(state="PROCESSING...", user_text=directive, reply_text="⚡ 'Puter is thinking...")
        self.notifier.notify(
            f"🗣️ 'Puter Heard You{stt_tag}",
            f"You: \"{directive}\"\n⚡ Processing directive...",
            timeout_ms=5000
        )

        # Check if user asked 'Puter to toggle Desktop Override Mode or open a local folder/app on this PC
        low_dir = directive.lower()
        if any(k in low_dir for k in ["desktop mode", "override my desktop", "full desktop", "batcomputer desktop", "show desktop shell"]):
            self.emit_ui(desktop_toggle="on", state="SPEAKING", user_text=directive, reply_text="Engaging BCv1.2 Full-Screen Desktop Override, Master Ramos.")
            self._speak_short_reply("Engaging BCv1.2 Full-Screen Desktop Override, Master Ramos.")
            return
        if any(k in low_dir for k in ["exit desktop", "close desktop mode", "minimize desktop", "normal desktop", "floating mode"]):
            self.emit_ui(desktop_toggle="off", state="SPEAKING", user_text=directive, reply_text="Returning to floating companion mode, Master Ramos.")
            self._speak_short_reply("Returning to floating companion mode, Master Ramos.")
            return
        local_voice_action = handle_local_voice_shortcut(low_dir)
        if local_voice_action:
            reply_msg = local_voice_action["reply"]
            ev_payload = {"state": "SPEAKING", "user_text": directive, "reply_text": reply_msg, "latency_ms": 12}
            self.emit_ui(**ev_payload)
            self.notifier.notify("🖥️ 'Puter Local Desktop Telemetry", reply_msg, timeout_ms=7000)
            self._speak_short_reply(reply_msg)
            return

        # Check if user cancelled shutdown
        if any(k in low_dir for k in ("cancel shutdown", "abort shutdown", "stop shutdown", "don't turn off", "dont turn off")):
            self._shutdown_cancelled = True
            reply = "Poweroff sequence aborted, Master Ramos. Systems remaining active."
            self.emit_ui(state="SPEAKING", user_text=directive, reply_text=reply)
            self._speak_short_reply(reply)
            return

        # Check if user invoked the Evening Shutdown sequence ("Goodnight Computer" / "Computer, turn yourself off")
        shutdown_triggers = (
            "goodnight computer", "goodnight 'puter", "goodnight alfred", "good night computer", "good night 'puter",
            "computer turn yourself off", "computer, turn yourself off", "turn yourself off",
            "shut down the batcomputer", "shutdown the batcomputer", "shut down my pc", "shutdown my pc",
            "turn off the batcomputer", "turn off the pc", "bedtime shutdown", "bedtime debrief"
        )
        if any(trig in low_dir for trig in shutdown_triggers):
            print(f"🌙 [Bedtime Shutdown]: Directive detected: \"{directive}\"")
            self.execute_evening_shutdown(directive)
            return

        # Check if user asked to expand the morning briefing or requested a briefing
        if any(k in low_dir for k in ("expand", "fleet details", "tell me more", "more info", "detailed briefing", "full fleet")):
            print(f"📊 [Detailed Briefing]: Directive detected: \"{directive}\"")
            try:
                b_res = self.session.get(f"{self.server_url}/api/briefing?mode=detailed&skip_tts=false", timeout=12).json()
                d_reply = b_res.get("reply", "")
                d_audio = b_res.get("audio_url")
                self.emit_ui(state="SPEAKING", user_text=directive, reply_text=d_reply)
                self.notifier.notify("📊 Batcomputer Detailed Fleet Briefing", d_reply, timeout_ms=12000)
                if d_audio:
                    self.play_audio_url(d_audio)
                else:
                    self._speak_short_reply(d_reply)
                return
            except Exception as e:
                print(f"Error fetching detailed briefing: {e}")

        if any(k in low_dir for k in ("give me a briefing", "tactical briefing", "morning report", "status report", "system briefing", "daily briefing")):
            print(f"🌅 [Tactical Briefing]: Directive detected: \"{directive}\"")
            try:
                b_res = self.session.get(f"{self.server_url}/api/briefing?mode=hybrid&skip_tts=false", timeout=12).json()
                b_reply = b_res.get("reply", "")
                b_audio = b_res.get("audio_url")
                self.emit_ui(state="SPEAKING", user_text=directive, reply_text=b_reply)
                self.notifier.notify("🌅 'Puter Tactical Briefing", b_reply, timeout_ms=10000)
                if b_audio:
                    self.play_audio_url(b_audio)
                else:
                    self._speak_short_reply(b_reply)
                return
            except Exception as e:
                print(f"Error fetching tactical briefing: {e}")

        # Check if user asked 'Puter to inspect their local screen
        if any(k in directive.lower() for k in ["look at my screen", "what is on my screen", "what's on my screen", "analyze my screen", "read my screen", "look at this error"]):
            print(f"👁️ [Screen Vision]: Capturing screen for directive: \"{directive}\"")
            self.emit_ui(state="SCANNING SCREEN...", user_text=directive)
            b64_screen = self.capture_local_screen_b64()
            try:
                v_res = self.session.post(
                    f"{self.server_url}/api/vision",
                    json={"image": b64_screen, "client_host": socket.gethostname(), "target": "client", "prompt": directive, "skip_tts": False},
                    timeout=18
                ).json()
                reply = v_res.get("reply", "")
                lat = v_res.get("latency_ms", 0)
                print(f"🦇 ['Puter ({lat}ms)]: \"{reply}\"")
                self.emit_ui(state="SPEAKING", user_text=directive, reply_text=reply, latency_ms=lat)
                self.notifier.notify(
                    f"👁️ 'Puter Vision ({lat}ms)",
                    f"You: \"{directive}\"\n'Puter: \"{reply}\"",
                    timeout_ms=9000
                )
                if v_res.get("audio_url"):
                    self.play_audio_url(v_res["audio_url"])
            except Exception as e:
                self.emit_ui(state="ERROR", reply_text=f"Vision error: {e}")
            return

        # Standard Sub-Second Directive (or Autonomous Hermes Delegation to Alfred) + TTS
        try:
            c_res = self.session.post(
                f"{self.server_url}/api/chat",
                json={"message": directive, "voice": self.voice, "skip_tts": True},
                timeout=45
            ).json()
            reply = c_res.get("reply", "")
            lat = c_res.get("latency_ms", 0)
            hermes_used = bool(c_res.get("hermes_actions"))
            badge = "🤖 'Puter + Alfred (Hermes)" if hermes_used else f"🦇 'Puter ({lat}ms)"
            print(f"🦇 ['Puter ({lat}ms)]: \"{reply}\"")
            self.emit_ui(state="SPEAKING", user_text=directive, reply_text=reply, latency_ms=lat, hermes=hermes_used)
            self.notifier.notify(
                badge,
                f"You: \"{directive}\"\n'Puter: \"{reply}\"",
                timeout_ms=10000
            )
            if reply:
                self._speak_short_reply(reply)
        except Exception as e:
            print(f"[Chat Error]: {e}")
            self.emit_ui(state="ERROR", reply_text=f"Error: {e}")

    def _speak_short_reply(self, reply: str):
        try:
            tts_res = self.session.post(
                f"{self.server_url}/api/tts",
                json={"text": reply, "voice": self.voice},
                timeout=10
            ).json()
            if tts_res.get("audio_url"):
                self.play_audio_url(tts_res["audio_url"])
            else:
                self.emit_ui(state="STANDBY")
        except Exception:
            self.emit_ui(state="STANDBY")

    def execute_evening_shutdown(self, directive: str):
        """
        Executes the Bedtime / Goodnight Computer routine:
        1. Queries /api/evening-debrief on host for today's summary and tomorrow's preview.
        2. Alfred speaks the summary of today and preview of tomorrow.
        3. Posts a countdown notification with cancel option.
        4. Waits for TTS audio completion + 3 seconds, then issues systemctl poweroff.
        """
        try:
            self._shutdown_cancelled = False
            self.emit_ui(state="BEDTIME SEQUENCE", user_text=directive, reply_text="Initiating system nightfall shutdown, Master Ramos...")
            debrief_data = {}
            try:
                res = self.session.post(
                    f"{self.server_url}/api/evening-debrief",
                    params={"powerdown_smart_home": "false", "skip_tts": "false"},
                    timeout=8
                ).json()
                debrief_data = res
            except Exception as e:
                print(f"Remote evening debrief warning: {e}")

            reply = debrief_data.get("reply")
            audio_url = debrief_data.get("audio_url")

            if not reply:
                reply = (
                    "Good evening, Master Ramos. Daily tactical operations have concluded. "
                    "Your schedule for tomorrow is registered. "
                    "Sleep well, Master Ramos. Powering down systems now."
                )

            self.emit_ui(state="SPEAKING", user_text=directive, reply_text=reply)
            self.notifier.notify(
                "🦇 Batcomputer Nightfall Shutdown",
                f"{reply}\n\n⚡ System poweroff will execute momentarily. Press Super+Escape to abort.",
                timeout_ms=12000
            )

            if audio_url:
                self.play_audio_url(audio_url)
            else:
                self._speak_short_reply(reply)

            def _poweroff_worker():
                time.sleep(2.0)
                while self.is_speaking():
                    if self._shutdown_cancelled:
                        print("🛑 [Batcomputer Poweroff]: Shutdown sequence cancelled by user.")
                        return
                    time.sleep(0.5)
                # 5-second countdown grace period
                for _ in range(10):
                    if self._shutdown_cancelled:
                        print("🛑 [Batcomputer Poweroff]: Shutdown sequence cancelled by user.")
                        return
                    time.sleep(0.5)
                if not self._shutdown_cancelled:
                    print("⚡ [Batcomputer Poweroff]: Executing systemctl poweroff...")
                    subprocess.run(["systemctl", "poweroff"])

            threading.Thread(target=_poweroff_worker, daemon=True).start()
        except Exception as e:
            print(f"Error in execute_evening_shutdown: {e}")
            self.emit_ui(state="ERROR", reply_text=f"Shutdown sequence error: {e}")

    def process_wav_bytes(self, wav_bytes: bytes):
        self.emit_ui(state="TRANSCRIBING (MLX)...")
        t0 = time.perf_counter()
        try:
            r = self.session.post(
                f"{self.server_url}/api/transcribe",
                files={"file": ("speech.wav", wav_bytes, "audio/wav")},
                timeout=25
            )
            if r.status_code != 200:
                self.emit_ui(state="STANDBY")
                return
            data = r.json()
            transcript = (data.get("text") or "").strip()
            stt_ms = data.get("latency_ms", round((time.perf_counter() - t0) * 1000))
        except Exception as e:
            print(f"[STT Connection Error]: {e}")
            self.emit_ui(state="STANDBY")
            return

        if not transcript or len(transcript) < 2:
            self.emit_ui(state="STANDBY")
            return

        lower = transcript.lower()
        # Self-echo guard: ignore speaker bleed from 'Puter's own TTS replies
        if any(echo in lower for echo in (
            "master ramos", "master juan", "master remmy",
            "bedroom lighting has been extinguished", "beszel fleet is online",
            "bessel fleet is online", "standing by, master"
        )):
            self.emit_ui(state="STANDBY")
            return

        print(f"🎙️ [Heard ({stt_ms}ms)]: \"{transcript}\"")

        # Check Wake Word ("Computer", "Puter", "Hey Computer", "Hey Puter", "Alfred") unless in follow-up window or --always mode
        in_followup = (time.time() < self.awaiting_followup_until) or self.force_next_utterance
        self.force_next_utterance = False

        if self.wake_word_required and not in_followup:
            match = WAKE_WORD_REGEX.search(lower)
            if not match:
                self.emit_ui(state="STANDBY", ignored_text=f"Heard (no wake word): \"{transcript}\"")
                return
            # Confirmed wake word! Play high wake chime
            self.play_chime(high=True)
            directive = transcript[match.end():].strip(" ,.!:;-?")
            if len(directive) < 3:
                # User said "Computer", "Puter", "Hey Computer", or "Hey Puter" — open 15-second follow-up window!
                print("🦇 ['Puter]: Standing by, Master Ramos! Speak your directive (15s follow-up window)...")
                self.awaiting_followup_until = time.time() + 15.0
                self.emit_ui(
                    state="AWAITING DIRECTIVE (15s)",
                    user_text=transcript,
                    reply_text="Standing by, Master Ramos — speak your command now!"
                )
                self.notifier.notify(
                    "🎙️ 'Puter Listening (15s Window)",
                    f"Heard \"{transcript}\" — Standing by, Master Ramos. Speak your directive!",
                    timeout_ms=7000
                )
                return
        else:
            directive = LEADING_WAKE_STRIP_REGEX.sub("", transcript).strip()
            if not directive:
                self.emit_ui(state="STANDBY")
                return

        self.awaiting_followup_until = 0.0
        self.send_text_directive(directive, stt_ms=stt_ms)

    def _compute_rms_int16(self, raw_bytes: bytes) -> float:
        if not raw_bytes or len(raw_bytes) < 2:
            return 0.0
        samples = array.array("h")
        samples.frombytes(raw_bytes[: len(raw_bytes) - (len(raw_bytes) % 2)])
        if not samples:
            return 0.0
        sum_sq = sum((s / 32768.0) ** 2 for s in samples)
        return math.sqrt(sum_sq / len(samples))

    def _emit_wav_and_process(self, pcm_chunks: list):
        raw_pcm = b"".join(pcm_chunks)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(raw_pcm)
        wav_bytes = buf.getvalue()
        threading.Thread(target=self.process_wav_bytes, args=(wav_bytes,), daemon=True).start()

    def run_audio_loop(self):
        self.check_server_connectivity()

        def _heartbeat_worker():
            while self.running:
                time.sleep(30)
                try:
                    self.register_with_server()
                except Exception:
                    pass
        threading.Thread(target=_heartbeat_worker, daemon=True).start()

        def _morning_briefing_worker():
            """
            Proactive Morning Hybrid Briefing Watchdog:
            Checks if this is the first login/boot of the day.
            Waits 5 seconds for desktop and audio initialization,
            then delivers a crisp hybrid briefing with follow-up handover.
            """
            time.sleep(5.0)
            cache_file = Path.home() / ".cache" / "alfred_morning_briefing.json"
            today_str = datetime.now().strftime("%Y-%m-%d")

            already_briefed = False
            if cache_file.exists():
                try:
                    data = json.loads(cache_file.read_text(encoding="utf-8"))
                    if data.get("last_date") == today_str:
                        already_briefed = True
                except Exception:
                    pass

            if already_briefed:
                return

            # Check if a fullscreen game is currently active
            try:
                clients_raw = subprocess.check_output(["hyprctl", "clients", "-j"], timeout=1).decode("utf-8")
                clients = json.loads(clients_raw) if clients_raw.strip() else []
                is_gaming = any(
                    c.get("fullscreen") == 1 or any(g in c.get("class", "").lower() for g in ("steam_app", "gamescope", "lutris", "heroic"))
                    for c in clients
                )
                if is_gaming:
                    print("🎮 [Briefing Postponed]: Fullscreen gaming session active.")
                    return
            except Exception:
                pass

            print(f"🌅 [Autonomous Morning Briefing]: Engaging first-boot briefing for {today_str}...")
            try:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps({"last_date": today_str, "timestamp": time.time()}), encoding="utf-8")

                self.play_chime(high=True)
                time.sleep(0.4)

                b_res = self.session.get(f"{self.server_url}/api/briefing?mode=hybrid&skip_tts=false", timeout=12).json()
                reply = b_res.get("reply", "")
                audio_url = b_res.get("audio_url")

                self.emit_ui(state="SPEAKING", user_text="[Autonomous Morning Briefing]", reply_text=reply)
                self.notifier.notify("🌅 'Puter Morning Tactical Briefing", reply, timeout_ms=10000)

                if audio_url:
                    self.play_audio_url(audio_url)
                else:
                    self._speak_short_reply(reply)
            except Exception as e:
                print(f"Error in morning briefing: {e}")

        def _calendar_countdown_worker():
            """
            Pre-Event Tactical Countdown Watchdog:
            Runs every 45 seconds, checking today's calendar objectives.
            If an event is exactly ~15 minutes away, delivers an acoustic alert
            and spoken notice with location reminder.
            """
            notified_events = set()
            while self.running:
                time.sleep(45)
                try:
                    now = datetime.now()
                    today_str = now.strftime("%Y-%m-%d")

                    cal_data = query_local_calendar_agenda()
                    events = cal_data.get("events", [])

                    for e in events:
                        eid = e.get("id")
                        if not eid or eid in notified_events:
                            continue
                        if e.get("date") != today_str:
                            continue
                        t_str = e.get("time", "")
                        if not t_str or t_str == "All Day":
                            continue

                        try:
                            dt = datetime.strptime(f"{today_str} {t_str}", "%Y-%m-%d %I:%M %p")
                            diff_min = (dt - now).total_seconds() / 60.0

                            # Target 15 minute warning window: 13.5 to 16.5 minutes prior
                            if 13.5 <= diff_min <= 16.5:
                                notified_events.add(eid)

                                is_gaming = False
                                try:
                                    clients_raw = subprocess.check_output(["hyprctl", "clients", "-j"], timeout=1).decode("utf-8")
                                    clients = json.loads(clients_raw) if clients_raw.strip() else []
                                    is_gaming = any(c.get("fullscreen") == 1 for c in clients)
                                except Exception:
                                    pass

                                title = e.get("title", "Tactical Objective")
                                loc = e.get("location", "Designated Location")
                                alert_msg = f"Master Ramos, you have {title} in 15 minutes at {loc}."

                                self.notifier.notify(
                                    f"⏰ Upcoming Event: {title} (in 15m)",
                                    f"📍 Location: {loc}\n⏰ Time: {t_str}",
                                    timeout_ms=12000
                                )
                                self.play_chime(high=True)
                                time.sleep(0.3)

                                if not is_gaming:
                                    self.emit_ui(state="SPEAKING", user_text=f"[Event Reminder: {title}]", reply_text=alert_msg)
                                    self._speak_short_reply(alert_msg)
                        except Exception:
                            pass
                except Exception:
                    pass

        threading.Thread(target=_morning_briefing_worker, daemon=True).start()
        threading.Thread(target=_calendar_countdown_worker, daemon=True).start()
        print("=================================================================")
        print("🦇 'PUTER // BCv1.2 NATIVE COMPANION & VOICE DAEMON ONLINE")
        print(f"🔗 Server: {self.server_url}")
        print(f"🗣️ Wake Words: {'\"Computer\", \"Puter\", \"Hey Computer\", \"Hey Puter\"' if self.wake_word_required else 'Always Listening (No Wake Word Required)'}")
        print("🔔 Notifications: Active (Hearing + Live Response Transcription)")
        print("🤖 Heavy Workloads: Autonomously delegated to Alfred (Hermes Agent)")
        print("=================================================================")

        pre_roll = deque(maxlen=PRE_ROLL_CHUNKS)
        speech_frames = []
        is_recording = False
        notified_hearing = False
        silence_time = 0.0
        record_time = 0.0
        bytes_per_chunk = CHUNK_SAMPLES * 2  # 16-bit mono

        # 1. Prefer sounddevice if installed (Cross-platform: Windows, macOS, Linux)
        if sys.platform == "win32":
            try:
                import sounddevice as sd
            except ImportError:
                print("⚠️ [Windows Audio]: sounddevice missing. Attempting auto-install...")
                try:
                    subprocess.run([sys.executable, "-m", "pip", "install", "sounddevice", "--quiet"], check=True)
                    import sounddevice as sd
                    print("✓ sounddevice installed successfully on Windows!")
                except Exception as e:
                    print(f"❌ Could not auto-install sounddevice: {e}. Please run: pip install sounddevice")

        try:
            import sounddevice as sd
            self.current_mic_name = "System Audio Input (sounddevice)"
            self.current_mic_is_real = True
            self.emit_ui(mic_name=self.current_mic_name, mic_real=True, state="STANDBY")
            with sd.RawInputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16", blocksize=CHUNK_SAMPLES) as stream:
                last_followup_sec = 0
                while self.running:
                    data, _ = stream.read(CHUNK_SAMPLES)
                    if self.muted:
                        time.sleep(0.05)
                        continue
                    raw_bytes = bytes(data)
                    rms = self._compute_rms_int16(raw_bytes)
                    self.emit_ui(rms=rms)
                    if self.is_speaking():
                        if rms > BARGE_IN_RMS_THRESHOLD:
                            print("⚡ [Barge-In Detected]: Interrupting 'Puter speech...")
                            self.stop_speaking()
                            is_recording = True
                            notified_hearing = False
                            speech_frames = list(pre_roll) + [raw_bytes]
                            silence_time = 0.0
                            record_time = CHUNK_DURATION_S
                        continue
                    elif self.active_player is not None and self.active_player.poll() is not None:
                        self.active_player = None
                        self._on_playback_ended()

                    # Follow-Up Window UI Countdown
                    in_followup = time.time() < self.awaiting_followup_until
                    if in_followup and not is_recording and not self.is_speaking():
                        rem_s = max(1, int(self.awaiting_followup_until - time.time()))
                        cur_sec = int(time.time())
                        if cur_sec != last_followup_sec:
                            last_followup_sec = cur_sec
                            self.emit_ui(state=f"CONVO OPEN ({rem_s}s)")
                    elif not in_followup and self.awaiting_followup_until > 0 and not is_recording:
                        self.awaiting_followup_until = 0.0
                        self.emit_ui(state="STANDBY")

                    if rms > SPEECH_RMS_THRESHOLD:
                        if not is_recording:
                            is_recording = True
                            notified_hearing = False
                            speech_frames = list(pre_roll)
                            record_time = len(speech_frames) * CHUNK_DURATION_S
                            st_msg = "🎙️ HEARING FOLLOW-UP..." if in_followup else "🎙️ HEARING YOU..."
                            self.emit_ui(state=st_msg)
                        speech_frames.append(raw_bytes)
                        silence_time = 0.0
                        record_time += CHUNK_DURATION_S
                        if record_time >= 0.45 and not notified_hearing and (not self.wake_word_required or in_followup or self.force_next_utterance):
                            notified_hearing = True
                            self.notifier.notify("🎙️ 'Puter Hearing You", "Listening to your speech...", timeout_ms=3000)
                    elif is_recording:
                        speech_frames.append(raw_bytes)
                        silence_time += CHUNK_DURATION_S
                        record_time += CHUNK_DURATION_S
                        if silence_time >= SILENCE_LIMIT_S or record_time >= MAX_RECORD_S or self.force_stop_recording:
                            is_recording = False
                            self.force_stop_recording = False
                            if record_time >= 0.35:
                                self._emit_wav_and_process(speech_frames)
                            else:
                                self.emit_ui(state="STANDBY")
                            speech_frames = []
                    else:
                        pre_roll.append(raw_bytes)
            return
        except ImportError:
            pass

        # 2. Linux PipeWire / PulseAudio / ALSA Loop with Hot-Plug Microphone Detection
        current_source, is_real_mic = detect_best_linux_mic_source()
        self.current_mic_name = current_source or "Default PipeWire Mic"
        self.current_mic_is_real = is_real_mic
        self.emit_ui(mic_name=self.current_mic_name, mic_real=is_real_mic, state="STANDBY")

        if current_source:
            if is_real_mic:
                print(f"🎤 [Microphone Active]: {current_source}")
                try:
                    subprocess.run(["pactl", "set-default-source", current_source], check=False, timeout=2)
                except Exception:
                    pass
            else:
                print("-----------------------------------------------------------------")
                print(f"⚠️  [MICROPHONE NOTICE]: Currently attached to '{current_source}'")
                print("   Your Razer Barracuda X 2.4 USB dongle and AirPods (Bluetooth)")
                print("   are currently disconnected/off, and JieLi USB Audio has no mic!")
                print("   👉 Plug in / turn on your Razer Barracuda X or connect AirPods:")
                print("      'Puter will hot-switch to your microphone automatically!")
                print("-----------------------------------------------------------------")

        while self.running:
            rec_cmd = (
                ["parec", "--format=s16le", f"--rate={SAMPLE_RATE}", "--channels=1", "--latency-msec=100"]
                + (["--device", current_source] if current_source else [])
                if shutil.which("parec")
                else ["arecord", "-q", "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1", "-t", "raw"]
            )
            proc = subprocess.Popen(rec_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            last_source_check = time.time()
            last_followup_sec = 0
            try:
                while self.running and proc.stdout:
                    raw_bytes = proc.stdout.read(bytes_per_chunk)
                    if not raw_bytes:
                        time.sleep(0.5)
                        break

                    if self.muted:
                        continue

                    # Every 3 seconds, check if user connected a real microphone (Razer Barracuda X / AirPods)
                    if time.time() - last_source_check > 3.0 and not is_recording:
                        last_source_check = time.time()
                        new_src, new_is_real = detect_best_linux_mic_source()
                        if new_src and new_src != current_source:
                            print(f"\n🔄 [Hot-Plug Mic Detected]: Switching audio input to '{new_src}'!")
                            current_source, is_real_mic = new_src, new_is_real
                            self.current_mic_name = current_source
                            self.current_mic_is_real = is_real_mic
                            self.emit_ui(mic_name=current_source, mic_real=is_real_mic)
                            try:
                                subprocess.run(["pactl", "set-default-source", current_source], check=False, timeout=2)
                            except Exception:
                                pass
                            self.notifier.notify("🎤 Microphone Connected", f"Switched audio input to {new_src}", timeout_ms=4000)
                            break

                    rms = self._compute_rms_int16(raw_bytes)
                    self.emit_ui(rms=rms)

                    if self.is_speaking():
                        if rms > BARGE_IN_RMS_THRESHOLD:
                            print("⚡ [Barge-In Detected]: Interrupting 'Puter speech...")
                            self.stop_speaking()
                            is_recording = True
                            notified_hearing = False
                            speech_frames = list(pre_roll) + [raw_bytes]
                            silence_time = 0.0
                            record_time = CHUNK_DURATION_S
                        continue
                    elif self.active_player is not None and self.active_player.poll() is not None:
                        self.active_player = None
                        self._on_playback_ended()

                    # Follow-Up Window UI Countdown
                    in_followup = time.time() < self.awaiting_followup_until
                    if in_followup and not is_recording and not self.is_speaking():
                        rem_s = max(1, int(self.awaiting_followup_until - time.time()))
                        cur_sec = int(time.time())
                        if cur_sec != last_followup_sec:
                            last_followup_sec = cur_sec
                            self.emit_ui(state=f"CONVO OPEN ({rem_s}s)")
                    elif not in_followup and self.awaiting_followup_until > 0 and not is_recording:
                        self.awaiting_followup_until = 0.0
                        self.emit_ui(state="STANDBY")

                    if rms > SPEECH_RMS_THRESHOLD:
                        if not is_recording:
                            is_recording = True
                            notified_hearing = False
                            speech_frames = list(pre_roll)
                            record_time = len(speech_frames) * CHUNK_DURATION_S
                            st_msg = "🎙️ HEARING FOLLOW-UP..." if in_followup else "🎙️ HEARING YOU..."
                            self.emit_ui(state=st_msg)
                        speech_frames.append(raw_bytes)
                        silence_time = 0.0
                        record_time += CHUNK_DURATION_S
                        if record_time >= 0.45 and not notified_hearing and (not self.wake_word_required or in_followup or self.force_next_utterance):
                            notified_hearing = True
                            self.notifier.notify("🎙️ 'Puter is Hearing You...", "Listening to your command...", timeout_ms=3000)
                    elif is_recording:
                        speech_frames.append(raw_bytes)
                        silence_time += CHUNK_DURATION_S
                        record_time += CHUNK_DURATION_S
                        if silence_time >= SILENCE_LIMIT_S or record_time >= MAX_RECORD_S or self.force_stop_recording:
                            is_recording = False
                            self.force_stop_recording = False
                            if record_time >= 0.35:
                                self._emit_wav_and_process(speech_frames)
                            else:
                                self.emit_ui(state="STANDBY")
                            speech_frames = []
                    else:
                        pre_roll.append(raw_bytes)
            finally:
                try:
                    proc.terminate()
                    proc.wait(timeout=1)
                except Exception:
                    pass


_DESKTOP_MODE_ACTIVE = False
_LAST_CPU_STAT = {"total": 0, "idle": 0, "pct": 4.2}
_HW_CACHE = {"ts": 0.0, "data": None}


def _read_cpu_load_pct() -> float:
    """Non-blocking CPU utilization percentage from /proc/stat delta."""
    try:
        line = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0]
        parts = [int(x) for x in line.split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        total = sum(parts)
        prev_total = _LAST_CPU_STAT["total"]
        prev_idle = _LAST_CPU_STAT["idle"]
        _LAST_CPU_STAT["total"] = total
        _LAST_CPU_STAT["idle"] = idle
        if prev_total > 0 and total > prev_total:
            pct = round((1.0 - (idle - prev_idle) / (total - prev_total)) * 100.0, 1)
            _LAST_CPU_STAT["pct"] = max(0.5, min(100.0, pct))
    except Exception:
        pass
    return _LAST_CPU_STAT["pct"]


def get_local_host_hardware_telemetry() -> dict:
    """
    Read live CPU, GPU (NVIDIA GeForce GTX 1080), RAM, NVMe, Motherboard temps, and Fan RPMs
    directly from the local host (jm-gaming-pc) with a 1.5s cache.
    """
    now = time.time()
    if _HW_CACHE["data"] is not None and (now - _HW_CACHE["ts"]) < 1.5:
        return _HW_CACHE["data"]

    cpu_load = _read_cpu_load_pct()
    cpu_model = "AMD Ryzen 7 5800X"
    cpu_freq_ghz = 3.8
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8")
        m_name = re.search(r"model name\s*:\s*(.+)", cpuinfo)
        if m_name:
            cpu_model = m_name.group(1).strip().replace("8-Core Processor", "").strip()
        freqs = [float(x) for x in re.findall(r"cpu MHz\s*:\s*([0-9.]+)", cpuinfo)]
        if freqs:
            cpu_freq_ghz = round(max(freqs) / 1000.0, 2)
    except Exception:
        pass

    # Read /proc/meminfo for local RAM
    ram_total_gb, ram_used_gb, ram_pct = 16.0, 6.8, 42.5
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
        mt = re.search(r"MemTotal:\s+(\d+)", meminfo)
        ma = re.search(r"MemAvailable:\s+(\d+)", meminfo)
        if mt and ma:
            total_kb = int(mt.group(1))
            avail_kb = int(ma.group(1))
            used_kb = max(0, total_kb - avail_kb)
            ram_total_gb = round(total_kb / (1024 * 1024), 1)
            ram_used_gb = round(used_kb / (1024 * 1024), 1)
            ram_pct = round((used_kb / total_kb) * 100.0, 1) if total_kb else 0.0
    except Exception:
        pass

    # Read /sys/class/hwmon for CPU (k10temp), NVMe, Motherboard (nct6798), and Fan RPMs
    cpu_temp_c = 0.0
    nvme_temp_c = 0.0
    mobo_temp_c = 0.0
    fans_rpm = []
    try:
        hwmon_root = Path("/sys/class/hwmon")
        if hwmon_root.exists():
            for hw in hwmon_root.iterdir():
                name_file = hw / "name"
                hw_name = name_file.read_text(encoding="utf-8").strip().lower() if name_file.exists() else ""
                if hw_name in ("k10temp", "coretemp", "zenpower"):
                    t1 = hw / "temp1_input"
                    if t1.exists():
                        cpu_temp_c = round(int(t1.read_text().strip()) / 1000.0, 1)
                elif hw_name == "nvme":
                    t1 = hw / "temp1_input"
                    if t1.exists():
                        nvme_temp_c = round(int(t1.read_text().strip()) / 1000.0, 1)
                elif "nct" in hw_name or "it87" in hw_name or "asus" in hw_name:
                    t1 = hw / "temp1_input"
                    if t1.exists():
                        mobo_temp_c = round(int(t1.read_text().strip()) / 1000.0, 1)
                    if cpu_temp_c <= 0 and (hw / "temp2_input").exists():
                        cpu_temp_c = round(int((hw / "temp2_input").read_text().strip()) / 1000.0, 1)
                    for fan_in in sorted(hw.glob("fan*_input")):
                        try:
                            rpm = int(fan_in.read_text().strip())
                            if rpm > 0:
                                fans_rpm.append(rpm)
                        except Exception:
                            pass
    except Exception:
        pass

    # Query NVIDIA GPU via nvidia-smi
    gpu_info = {
        "name": "NVIDIA GeForce GTX 1080",
        "temp_c": 0.0,
        "load_pct": 0.0,
        "vram_used_mb": 0,
        "vram_total_mb": 8192,
        "vram_used_gb": 0.0,
        "vram_total_gb": 8.0,
        "vram_pct": 0.0,
        "power_w": 0.0,
        "fan_pct": 0,
    }
    if shutil.which("nvidia-smi"):
        try:
            res = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw,fan.speed",
                    "--format=csv,noheader,nounits"
                ],
                capture_output=True, text=True, timeout=1.2
            )
            if res.returncode == 0 and res.stdout.strip():
                parts = [p.strip() for p in res.stdout.strip().splitlines()[0].split(",")]
                if len(parts) >= 7:
                    v_used = float(parts[3]) if parts[3].replace(".", "").isdigit() else 0.0
                    v_tot = float(parts[4]) if parts[4].replace(".", "").isdigit() else 8192.0
                    gpu_info = {
                        "name": parts[0].replace("NVIDIA ", ""),
                        "temp_c": float(parts[1]) if parts[1].replace(".", "").isdigit() else 0.0,
                        "load_pct": float(parts[2]) if parts[2].replace(".", "").isdigit() else 0.0,
                        "vram_used_mb": int(v_used),
                        "vram_total_mb": int(v_tot),
                        "vram_used_gb": round(v_used / 1024.0, 2),
                        "vram_total_gb": round(v_tot / 1024.0, 1),
                        "vram_pct": round((v_used / v_tot) * 100.0, 1) if v_tot else 0.0,
                        "power_w": float(parts[5]) if parts[5].replace(".", "").isdigit() else 0.0,
                        "fan_pct": int(float(parts[6])) if parts[6].replace(".", "").isdigit() else 0,
                    }
        except Exception:
            pass

    data = {
        "cpu": {
            "name": cpu_model,
            "load_pct": cpu_load,
            "freq_ghz": cpu_freq_ghz,
            "temp_c": cpu_temp_c,
        },
        "gpu": gpu_info,
        "ram": {
            "used_gb": ram_used_gb,
            "total_gb": ram_total_gb,
            "percent": ram_pct,
        },
        "thermals": {
            "cpu_temp_c": cpu_temp_c,
            "gpu_temp_c": gpu_info["temp_c"],
            "nvme_temp_c": nvme_temp_c,
            "mobo_temp_c": mobo_temp_c,
            "fans_rpm": fans_rpm,
            "max_fan_rpm": max(fans_rpm) if fans_rpm else 0,
        }
    }
    _HW_CACHE["ts"] = now
    _HW_CACHE["data"] = data
    return data


def get_local_disk_stats(mount_path: str) -> dict:
    try:
        u = shutil.disk_usage(mount_path)
        total_gb = round(u.total / (1024 ** 3), 1)
        used_gb = round(u.used / (1024 ** 3), 1)
        free_gb = round(u.free / (1024 ** 3), 1)
        pct = round((u.used / u.total) * 100, 1) if u.total else 0
        return {"total_gb": total_gb, "used_gb": used_gb, "free_gb": free_gb, "percent": pct}
    except Exception:
        return {"total_gb": 500.0, "used_gb": 180.0, "free_gb": 320.0, "percent": 36.0}


def focus_hyprland_window(app_classes: list) -> bool:
    """If a window matching one of app_classes is already running in Hyprland,
    instantly focus its workspace and raise it to top (sub-10ms response)."""
    if sys.platform != "linux" or not shutil.which("hyprctl"):
        return False
    try:
        res = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True, timeout=0.8)
        if res.returncode == 0 and res.stdout.strip():
            clients = json.loads(res.stdout)
            for c in clients:
                c_class = (c.get("class") or "").lower()
                c_init = (c.get("initialClass") or "").lower()
                if any(ac.lower() in (c_class, c_init) for ac in app_classes):
                    ws = c.get("workspace", {}).get("name") or str(c.get("workspace", {}).get("id"))
                    # Instantly switch workspace to the window
                    subprocess.run(
                        ["hyprctl", "dispatch", f'hl.dsp.focus({{ workspace = "{ws}" }})'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=0.8
                    )
                    # Raise window to the top
                    subprocess.run(
                        ["hyprctl", "dispatch", "hl.dsp.window.bring_to_top()"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=0.8
                    )
                    return True
    except Exception as e:
        print(f"⚠️ [Hyprland Focus Error]: {e}")
    return False


def spawn_detached_desktop_command(cmd_list: list):
    """Spawns an application completely detached from the daemon's cgroup and environment."""
    env = os.environ.copy()
    # Strip webview-specific variables so spawned desktop apps start cleanly
    for k in ("WEBKIT_DMABUF_RENDERER_FORCE_SHM", "WEBKIT_DISABLE_DMABUF_RENDERER", "QTWEBENGINE_CHROMIUM_FLAGS"):
        env.pop(k, None)
    try:
        # Prefer native Omarchy / Wayland uwsm launcher for instantaneous graphical launch
        if shutil.which("uwsm") and os.environ.get("XDG_SESSION_TYPE") == "wayland":
            full_cmd = ["uwsm", "app", "--"] + cmd_list
            subprocess.Popen(full_cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return
        if shutil.which("systemd-run") and os.environ.get("INVOCATION_ID"):
            full_cmd = ["systemd-run", "--user", "--scope", "--quiet", "--collect"] + cmd_list
            subprocess.Popen(full_cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return
    except Exception:
        pass
    subprocess.Popen(cmd_list, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def open_local_file_or_dir(target_path: str, app_mode: str = "default") -> dict:
    p = Path(target_path).expanduser()
    if not p.exists():
        return {"success": False, "error": f"Path not found: {p}"}
    try:
        if app_mode == "code" and shutil.which("code"):
            spawn_detached_desktop_command(["code", str(p)])
        elif app_mode == "nautilus" and shutil.which("nautilus"):
            spawn_detached_desktop_command(["nautilus", str(p)])
        elif app_mode == "terminal":
            term_dir = str(p if p.is_dir() else p.parent)
            for term in (["foot", "-D", term_dir], ["kitty", "--directory", term_dir], ["gnome-terminal", f"--working-directory={term_dir}"]):
                if shutil.which(term[0]):
                    spawn_detached_desktop_command(term)
                    break
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        else:
            spawn_detached_desktop_command(["xdg-open", str(p)])
        return {"success": True, "path": str(p), "app": app_mode}
    except Exception as e:
        return {"success": False, "error": str(e)}


def launch_local_desktop_application(app_key: str, optional_path: str = "") -> dict:
    key = app_key.lower().strip()
    target_path = str(Path(optional_path).expanduser()) if optional_path else str(Path.home())
    try:
        if key in ("steam", "games"):
            if shutil.which("steam"):
                # If Steam is already open on any workspace, instantly switch to it and bring it to top!
                focused = focus_hyprland_window(["steam"])
                # Always send IPC command so Steam unhides main window from tray
                spawn_detached_desktop_command(["steam", "steam://open/main"])
                msg = "Focused Steam on your desktop, Master Ramos." if focused else "Launching Steam client on your desktop, Master Ramos."
                return {"success": True, "message": msg}
            return open_local_file_or_dir(str(Path.home() / "Games"), "default")
        if key in ("code", "vscode"):
            if not optional_path:
                focused = focus_hyprland_window(["code", "com.microsoft.vscode"])
                if focused:
                    return {"success": True, "message": "Focused Visual Studio Code on your desktop, Master Ramos."}
            cmd = ["code", target_path] if optional_path else ["code"]
            spawn_detached_desktop_command(cmd)
            return {"success": True, "message": "Opening Visual Studio Code on your desktop, Master Ramos."}
        if key in ("nautilus", "files", "computer", "explorer"):
            if not optional_path or optional_path == str(Path.home()):
                focused = focus_hyprland_window(["org.gnome.nautilus", "nautilus"])
                if focused:
                    return {"success": True, "message": "Focused file manager on your desktop, Master Ramos."}
            if shutil.which("nautilus"):
                spawn_detached_desktop_command(["nautilus", target_path])
            else:
                spawn_detached_desktop_command(["xdg-open", target_path])
            return {"success": True, "message": f"Opening file manager at {Path(target_path).name or target_path}, Master Ramos."}
        if key in ("browser", "web", "zen", "zen-browser"):
            focused = focus_hyprland_window(["zen", "zen-browser", "firefox", "brave-browser", "google-chrome", "chromium"])
            if focused:
                return {"success": True, "message": "Focused web browser on your desktop, Master Ramos."}
            for br in ("zen-browser", "brave-browser", "firefox", "google-chrome", "chromium"):
                if shutil.which(br):
                    spawn_detached_desktop_command([br])
                    return {"success": True, "message": f"Launching {br} on your desktop, Master Ramos."}
        if key in ("terminal", "term", "foot", "shell"):
            for term in (["foot", "-D", target_path], ["kitty", "--directory", target_path], ["alacritty", "--working-directory", target_path]):
                if shutil.which(term[0]):
                    spawn_detached_desktop_command(term)
                    return {"success": True, "message": "Opening terminal on your desktop, Master Ramos."}
        return open_local_file_or_dir(target_path, "default")
    except Exception as e:
        return {"success": False, "message": f"Failed to launch {app_key}: {e}"}


def get_wireplumber_audio_status() -> dict:
    vol_pct = 100
    is_muted = False
    sinks = []
    try:
        v_out = subprocess.check_output(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], text=True, timeout=2).strip()
        m_vol = re.search(r"Volume:\s*([\d\.]+)", v_out)
        if m_vol:
            vol_pct = int(round(float(m_vol.group(1)) * 100))
        is_muted = "[MUTED]" in v_out
    except Exception:
        pass

    try:
        s_out = subprocess.check_output(["wpctl", "status"], text=True, timeout=2)
        in_sinks = False
        for line in s_out.splitlines():
            if "Sinks:" in line:
                in_sinks = True
                continue
            if in_sinks:
                if line.strip().startswith(("├─", "└─", "Audio", "Sources:", "Filters:", "Video", "Streams", "Settings")):
                    in_sinks = False
                    continue
                m = re.search(r"([* ])\s*(\d+)\.\s+(.*?)\s+\[vol:\s*([\d\.]+)(?:\s+\[MUTED\])?\]", line)
                if m:
                    sinks.append({
                        "id": int(m.group(2)),
                        "name": m.group(3).strip(),
                        "is_default": m.group(1) == "*",
                        "volume": int(round(float(m.group(4)) * 100)),
                        "muted": "[MUTED]" in line
                    })
    except Exception:
        pass

    return {"volume": vol_pct, "muted": is_muted, "sinks": sinks}


def set_wireplumber_audio(action: str, value: Any = None) -> dict:
    try:
        if action == "set_volume":
            vol_val = float(value)
            if vol_val > 1.5:
                vol_val = vol_val / 100.0
            subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{vol_val:.2f}"], timeout=2, check=True)
        elif action == "toggle_mute":
            subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"], timeout=2, check=True)
        elif action == "mute":
            subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"], timeout=2, check=True)
        elif action == "unmute":
            subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"], timeout=2, check=True)
        elif action == "set_sink":
            sink_id = str(value)
            subprocess.run(["wpctl", "set-default", sink_id], timeout=2, check=True)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "status": get_wireplumber_audio_status()}


def get_hyprland_status() -> dict:
    clients = []
    workspaces = []
    try:
        c_out = subprocess.check_output(["hyprctl", "clients", "-j"], text=True, timeout=2)
        clients = json.loads(c_out)
    except Exception:
        pass
    try:
        w_out = subprocess.check_output(["hyprctl", "workspaces", "-j"], text=True, timeout=2)
        workspaces = json.loads(w_out)
    except Exception:
        pass
    return {"clients": clients, "workspaces": workspaces}


def dispatch_hyprland(dispatcher: str, args: str = "") -> dict:
    try:
        cmd = ["hyprctl", "dispatch", str(dispatcher)]
        if args:
            cmd.append(str(args))
        subprocess.run(cmd, timeout=2, check=True)
        return {"ok": True, "dispatcher": dispatcher, "args": args}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def save_quick_note(content: str) -> dict:
    try:
        brain_dir = Path("/home/jmrd/.gemini/antigravity/scratch/alfred-batcomputer/second_brain")
        brain_dir.mkdir(parents=True, exist_ok=True)
        target_file = brain_dir / "quick_notes.md"
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"\n\n### ⚡ Tactical Note — {ts}\n{content.strip()}\n"
        if not target_file.exists():
            target_file.write_text("# 🦇 Batcomputer Second Brain // Tactical Quick Notes\n" + entry, encoding="utf-8")
        else:
            with open(target_file, "a", encoding="utf-8") as f:
                f.write(entry)
        return {"ok": True, "file": "quick_notes.md"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def query_local_calendar_agenda() -> dict:
    cal_file = Path("/home/jmrd/.gemini/antigravity/scratch/alfred-batcomputer/calendar.json")
    
    # Attempt quick background refresh from Batcomputer primary host if reachable
    try:
        cfg_file = Path("/home/jmrd/.gemini/antigravity/scratch/alfred-batcomputer/auth_config.json")
        tok = ""
        if cfg_file.exists():
            tok = json.loads(cfg_file.read_text(encoding="utf-8")).get("daemon_token", "")
        srv = os.environ.get("ALFRED_SERVER_URL", "http://100.77.249.27:7776")
        url = f"{srv}/api/calendar?token={tok}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Batcomputer-Alfred-Local)"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if "events" in data and isinstance(data["events"], list):
                cal_file.write_text(json.dumps(data["events"], indent=2), encoding="utf-8")
    except Exception:
        pass

    if not cal_file.exists():
        return {"events": [], "summary": "No calendar events loaded yet, Master Ramos."}
    try:
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        
        events = json.loads(cal_file.read_text(encoding="utf-8"))
        if not events:
            return {"events": [], "summary": "Your tactical agenda is currently clear, Master Ramos."}

        # Filter for upcoming vs today vs past
        upcoming = []
        today_events = []
        for e in events:
            d = e.get("date", "")
            if d == today_str:
                today_events.append(e)
                upcoming.append(e)
            elif d > today_str:
                upcoming.append(e)

        if today_events:
            summaries = [f"{e.get('time', 'TBD')}: {e.get('title', 'Event')}" for e in today_events[:3]]
            return {
                "events": events,
                "upcoming": upcoming,
                "next_event": today_events[0],
                "summary": f"You have {len(today_events)} objective{'s' if len(today_events) > 1 else ''} scheduled for today, Master Ramos. Next: {', '.join(summaries)}."
            }
        elif upcoming:
            summaries = [f"{e.get('date')} at {e.get('time', 'TBD')}: {e.get('title', 'Event')}" for e in upcoming[:3]]
            return {
                "events": events,
                "upcoming": upcoming,
                "next_event": upcoming[0],
                "summary": f"You have {len(upcoming)} upcoming event{'s' if len(upcoming) > 1 else ''} on your calendar, Master Ramos. Next: {', '.join(summaries)}."
            }
        else:
            return {
                "events": events,
                "upcoming": [],
                "next_event": None,
                "summary": "Your tactical agenda is currently clear with no upcoming events scheduled, Master Ramos. Archived past semester events are retained in memory."
            }
    except Exception as e:
        return {"events": [], "summary": f"Could not read calendar: {e}"}


def handle_local_voice_shortcut(low_dir: str) -> Optional[dict]:
    """Allow voice commands for local hardware temps, audio volume, hyprland workspaces, quick notes, and apps."""
    if any(k in low_dir for k in (
        "gpu", "cpu temp", "my temps", "pc temp", "hardware temp", "system temp",
        "how hot is my", "vram", "fan speed", "graphics card", "ryzen", "1080"
    )):
        hw = get_local_host_hardware_telemetry()
        cpu = hw["cpu"]
        gpu = hw["gpu"]
        th = hw["thermals"]
        reply = (
            f"On your local rig, {cpu['name']} is at {cpu['temp_c']}°C ({cpu['load_pct']}% load), "
            f"your {gpu['name']} is at {gpu['temp_c']}°C ({gpu['load_pct']}% load, {gpu['vram_used_gb']}GB VRAM, {gpu['power_w']}W), "
            f"NVMe is {th['nvme_temp_c']}°C, and chassis fans peak at {th['max_fan_rpm']} RPM, Master Ramos."
        )
        return {"reply": reply, "keep_desktop": True}

    # Audio Control via WirePlumber
    if any(k in low_dir for k in ("mute volume", "mute audio", "silence sound", "mute system")):
        set_wireplumber_audio("mute")
        return {"reply": "System audio muted, Master Ramos."}
    if any(k in low_dir for k in ("unmute volume", "unmute audio", "restore sound", "unmute system")):
        set_wireplumber_audio("unmute")
        return {"reply": "System audio restored, Master Ramos."}
    
    m_vol = re.search(r"(?:set\s+)?volume\s+(?:to\s+)?(\d+)(?:\s*%)?", low_dir)
    if m_vol:
        v_num = int(m_vol.group(1))
        set_wireplumber_audio("set_volume", v_num)
        return {"reply": f"Master audio volume set to {v_num} percent, Master Ramos."}

    if "switch audio to headphone" in low_dir or "switch sound to headphone" in low_dir or "audio to usb" in low_dir:
        st = get_wireplumber_audio_status()
        target_sink = next((s for s in st["sinks"] if "usb" in s["name"].lower() or "jieli" in s["name"].lower()), None)
        if target_sink:
            set_wireplumber_audio("set_sink", target_sink["id"])
            return {"reply": f"Audio output switched to {target_sink['name']}, Master Ramos."}
        return {"reply": "Switched audio output to default headphones, Master Ramos."}

    if "switch audio to speaker" in low_dir or "switch audio to hdmi" in low_dir or "switch sound to monitor" in low_dir:
        st = get_wireplumber_audio_status()
        target_sink = next((s for s in st["sinks"] if "hdmi" in s["name"].lower() or "gp104" in s["name"].lower() or "rsgm" in s["name"].lower()), None)
        if target_sink:
            set_wireplumber_audio("set_sink", target_sink["id"])
            return {"reply": f"Audio output switched to HDMI Monitor speakers, Master Ramos."}
        return {"reply": "Switched audio output to HDMI display, Master Ramos."}

    # Hyprland Workspace & Window Management
    m_ws = re.search(r"(?:switch to\s+|go to\s+|workspace\s+)(\d+)", low_dir)
    if m_ws:
        ws_num = m_ws.group(1)
        dispatch_hyprland("workspace", ws_num)
        return {"reply": f"Switching to workspace {ws_num}, Master Ramos."}

    if "toggle fullscreen" in low_dir or "go fullscreen" in low_dir or low_dir.strip() == "fullscreen":
        dispatch_hyprland("fullscreen", "1")
        return {"reply": "Toggling fullscreen on active window, Master Ramos."}

    if any(k in low_dir for k in ("close window", "kill window", "close active", "kill active")):
        dispatch_hyprland("killactive", "")
        return {"reply": "Closing active application window, Master Ramos."}

    if "split window" in low_dir or "toggle split" in low_dir:
        dispatch_hyprland("togglesplit", "")
        return {"reply": "Toggled window layout split orientation, Master Ramos."}

    # Quick Note / Second Brain
    for note_prefix in ("take a note", "note that", "remember that", "write down"):
        if low_dir.startswith(note_prefix) or f" {note_prefix}" in low_dir:
            idx = low_dir.find(note_prefix) + len(note_prefix)
            note_text = low_dir[idx:].strip(" :,.-")
            if note_text:
                save_quick_note(note_text)
                return {"reply": f"Note captured in your Second Brain vault: \"{note_text}\", Master Ramos."}

    # Calendar / Schedule Query
    if any(k in low_dir for k in ("what's on my agenda", "what is on my agenda", "my schedule", "upcoming meetings", "next meeting", "my calendar", "what do i have today")):
        cal_data = query_local_calendar_agenda()
        return {"reply": cal_data["summary"]}

    if not any(low_dir.startswith(prefix) or f" {prefix}" in low_dir for prefix in ("open ", "launch ", "start ", "browse ", "show my ")):
        return None
    if "download" in low_dir:
        launch_local_desktop_application("nautilus", str(Path.home() / "Downloads"))
        return {"reply": "Opening your Downloads folder on the desktop, Master Ramos."}
    if "document" in low_dir:
        launch_local_desktop_application("nautilus", str(Path.home() / "Documents"))
        return {"reply": "Opening your Documents folder on the desktop, Master Ramos."}
    if "nextcloud" in low_dir:
        launch_local_desktop_application("nautilus", str(Path.home() / "Nextcloud"))
        return {"reply": "Opening your Nextcloud vault on the desktop, Master Ramos."}
    if "file" in low_dir or "home folder" in low_dir or "my computer" in low_dir:
        launch_local_desktop_application("nautilus", str(Path.home()))
        return {"reply": "Opening your local file manager, Master Ramos."}
    if "steam" in low_dir or "games" in low_dir:
        launch_local_desktop_application("steam")
        return {"reply": "Launching Steam on your gaming rig, Master Ramos."}
    if "vs code" in low_dir or "vscode" in low_dir or "code editor" in low_dir:
        launch_local_desktop_application("code", "/home/jmrd/.gemini/antigravity/scratch/alfred-batcomputer")
        return {"reply": "Launching Visual Studio Code on your desktop, Master Ramos."}
    if "browser" in low_dir or "zen browser" in low_dir:
        launch_local_desktop_application("browser")
        return {"reply": "Launching Zen Browser on your desktop, Master Ramos."}
    if "terminal" in low_dir:
        launch_local_desktop_application("terminal")
        return {"reply": "Opening a local terminal window, Master Ramos."}
    return None


def start_local_desktop_bridge_server(daemon: AlfredBackgroundDaemon, port: int = LOCAL_BRIDGE_PORT):
    """
    Lightweight HTTP bridge on 127.0.0.1:7778 so the BCv1.2 HUD can browse real local files,
    preview documents/code, launch local apps (Steam, VS Code, Nautilus, Zen Browser, Foot),
    and toggle the Full-Screen 2560x1440 Desktop Override Mode.
    """
    class BridgeHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send_json(self, payload: dict, status: int = 200):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_OPTIONS(self):
            self._send_json({"ok": True})

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)

            # Serve the BCv1.2 HUD directly on http://127.0.0.1:7778/ for instant zero-latency local loading
            if parsed.path in ("/", "/index.html"):
                local_html = Path(__file__).resolve().parent / "static" / "index.html"
                if local_html.exists():
                    body = local_html.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(body)
                    return

            # Proxy /api/* GET requests to the Mac Mini Batcomputer server so http://127.0.0.1:7778 is 100% self-contained
            if parsed.path.startswith("/api/"):
                try:
                    fwd_headers = {}
                    for h in ("authorization", "cookie", "content-type"):
                        for kh, vh in self.headers.items():
                            if kh.lower() == h:
                                fwd_headers[kh] = vh
                    if not any(k.lower() == "authorization" for k in fwd_headers) and daemon.auth_token:
                        fwd_headers["Authorization"] = f"Bearer {daemon.auth_token}"
                    r = daemon.session.get(f"{daemon.server_url}{self.path}", headers=fwd_headers, timeout=12)
                    self.send_response(r.status_code)
                    self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
                    self.send_header("Content-Length", str(len(r.content)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    try:
                        self.wfile.write(r.content)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                except (BrokenPipeError, ConnectionResetError):
                    return
                except Exception as e:
                    try:
                        self._send_json({"error": str(e)}, status=502)
                    except Exception:
                        pass
                return

            if parsed.path == "/local/status":
                self._send_json({
                    "ok": True,
                    "host": socket.gethostname(),
                    "desktop_mode": _DESKTOP_MODE_ACTIVE,
                    "state": daemon.current_state,
                    "user_text": daemon.last_user_text,
                    "reply_text": daemon.last_reply_text,
                    "hardware": get_local_host_hardware_telemetry(),
                    "disks": {
                        "root": get_local_disk_stats("/"),
                        "home": get_local_disk_stats(str(Path.home())),
                    }
                })
                return

            if parsed.path == "/local/files":
                raw_path = (qs.get("path") or [str(Path.home())])[0]
                query = (qs.get("query") or [""])[0].strip().lower()
                target = Path(raw_path).expanduser()
                if not target.exists() or not target.is_dir():
                    target = Path.home()
                items = []
                try:
                    entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
                    for entry in entries:
                        if entry.name.startswith(".") and entry.name not in (".gemini", ".config", ".local"):
                            continue
                        if query and query not in entry.name.lower():
                            continue
                        try:
                            st = entry.stat()
                            items.append({
                                "name": entry.name,
                                "path": str(entry),
                                "is_dir": entry.is_dir(),
                                "size": st.st_size if not entry.is_dir() else 0,
                                "mtime": int(st.st_mtime),
                            })
                        except Exception:
                            continue
                        if len(items) >= 100:
                            break
                except Exception as e:
                    self._send_json({"path": str(target), "parent": str(target.parent), "items": [], "error": str(e)})
                    return

                self._send_json({
                    "host": socket.gethostname(),
                    "path": str(target),
                    "parent": str(target.parent) if target.parent != target else str(target),
                    "items": items
                })
                return

            if parsed.path == "/local/file-preview":
                raw_path = (qs.get("path") or [""])[0]
                p = Path(raw_path).expanduser()
                if not p.exists() or not p.is_file():
                    self._send_json({"error": "File not found", "content": ""}, status=404)
                    return
                try:
                    content = p.read_text(encoding="utf-8", errors="replace")[:14000]
                    self._send_json({"name": p.name, "path": str(p), "size": p.stat().st_size, "content": content})
                except Exception as e:
                    self._send_json({"name": p.name, "path": str(p), "error": str(e), "content": ""})
                return

            if parsed.path == "/local/screenshot":
                b64_img = daemon.capture_local_screen_b64()
                if b64_img:
                    self._send_json({
                        "ok": True,
                        "image": b64_img,
                        "host": socket.gethostname(),
                        "platform": sys.platform
                    })
                else:
                    self._send_json({"ok": False, "error": "Screen capture failed on client"}, status=500)
                return

            if parsed.path == "/local/audio":
                self._send_json(get_wireplumber_audio_status())
                return

            if parsed.path == "/local/hyprland":
                self._send_json(get_hyprland_status())
                return

            if parsed.path == "/local/calendar":
                self._send_json(query_local_calendar_agenda())
                return

            self._send_json({"error": "Not found"}, status=404)

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            try:
                data = json.loads(raw_body) if raw_body else {}
            except Exception:
                data = {}

            if parsed.path.startswith("/api/"):
                try:
                    fwd_headers = {}
                    for h in ("authorization", "cookie", "content-type"):
                        for kh, vh in self.headers.items():
                            if kh.lower() == h:
                                fwd_headers[kh] = vh
                    if not any(k.lower() == "authorization" for k in fwd_headers) and daemon.auth_token:
                        fwd_headers["Authorization"] = f"Bearer {daemon.auth_token}"
                    r = daemon.session.post(
                        f"{daemon.server_url}{self.path}",
                        data=raw_body.encode("utf-8") if isinstance(raw_body, str) else raw_body,
                        headers=fwd_headers,
                        timeout=30
                    )
                    self.send_response(r.status_code)
                    self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
                    self.send_header("Content-Length", str(len(r.content)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    try:
                        self.wfile.write(r.content)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                except (BrokenPipeError, ConnectionResetError):
                    return
                except Exception as e:
                    try:
                        self._send_json({"error": str(e)}, status=502)
                    except Exception:
                        pass
                return

            if parsed.path == "/local/open":
                res = open_local_file_or_dir(data.get("path", ""), data.get("app", "default"))
                self._send_json(res)
                return

            if parsed.path == "/local/launch":
                res = launch_local_desktop_application(data.get("app", ""), data.get("path", ""))
                self._send_json(res)
                return

            if parsed.path == "/local/audio":
                res = set_wireplumber_audio(data.get("action", ""), data.get("value"))
                self._send_json(res)
                return

            if parsed.path == "/local/hyprland/dispatch":
                res = dispatch_hyprland(data.get("dispatcher", "workspace"), data.get("args", "1"))
                self._send_json(res)
                return

            if parsed.path == "/local/note":
                res = save_quick_note(data.get("content", ""))
                self._send_json(res)
                return

            if parsed.path in ("/local/talk", "/local/listen"):
                daemon.trigger_push_to_talk()
                self._send_json({"ok": True, "state": "LISTENING"})
                return

            if parsed.path == "/local/companion":
                action = data.get("action", "show")
                daemon.emit_ui(companion_action=action)
                self._send_json({"ok": True, "action": action})
                return

            if parsed.path == "/local/desktop-mode":
                action = data.get("action", "toggle")
                daemon.emit_ui(desktop_toggle=action)
                time.sleep(0.08)
                self._send_json({"ok": True, "desktop_mode": _DESKTOP_MODE_ACTIVE})
                return

            if parsed.path == "/local/screenshot":
                b64_img = daemon.capture_local_screen_b64()
                if b64_img:
                    self._send_json({
                        "ok": True,
                        "image": b64_img,
                        "host": socket.gethostname(),
                        "platform": sys.platform
                    })
                else:
                    self._send_json({"ok": False, "error": "Screen capture failed on client"}, status=500)
                return

            if parsed.path == "/local/command":
                cmd = data.get("command", "").strip()
                if cmd:
                    threading.Thread(target=daemon.send_text_directive, args=(cmd,), daemon=True).start()
                self._send_json({"ok": True, "command": cmd})
                return

            self._send_json({"error": "Unknown endpoint"}, status=404)

    def _serve():
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), BridgeHandler)
            print(f"🖥️  [Local Desktop Bridge]: Listening on http://127.0.0.1:{port} (Files, Apps & Fullscreen Shell)")
            httpd.serve_forever()
        except Exception as e:
            print(f"⚠️  [Local Desktop Bridge Warning]: Port {port} busy or unavailable ({e})")

    threading.Thread(target=_serve, daemon=True).start()


def configure_hyprland_overlay_rules():
    """Apply floating top-right overlay rules on Hyprland so the companion app never steals focus or tiles,
    plus full-screen rules for the BCv1.2 Desktop Shell Override windows across all connected monitors."""
    if sys.platform != "linux" or not shutil.which("hyprctl"):
        return
    rules = [
        "float,title:^(Puter // BCv1.2 Companion)$",
        "pin,title:^(Puter // BCv1.2 Companion)$",
        "move 100%-435 42,title:^(Puter // BCv1.2 Companion)$",
        "size 420 265,title:^(Puter // BCv1.2 Companion)$",
        # True Desktop Background rules: Floating, pinned, no border, no shadow, never tile
        "float,title:^(Puter // BCv1.2 Desktop Shell.*)$",
        "pin,title:^(Puter // BCv1.2 Desktop Shell.*)$",
        "noborder,title:^(Puter // BCv1.2 Desktop Shell.*)$",
        "noshadow,title:^(Puter // BCv1.2 Desktop Shell.*)$",
        "noinitialfocus,title:^(Puter // BCv1.2 Desktop Shell.*)$",
        "focusonactivate false,title:^(Puter // BCv1.2 Desktop Shell.*)$",
    ]
    for r in rules:
        try:
            subprocess.run(["hyprctl", "keyword", "windowrulev2", r], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
        except Exception:
            pass


def run_pyqt_companion_app(daemon: AlfredBackgroundDaemon, hud_url: str, start_in_desktop_mode: bool = False):
    """Launch the native PyQt6 BCv1.2 Floating Companion Widget + Full-Screen Dual-Monitor Desktop Override Shells + System Tray."""
    global _DESKTOP_MODE_ACTIVE

    # CRITICAL: Import PyQt6.QtWebEngineWidgets BEFORE creating QApplication!
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        from PyQt6.QtWebEngineCore import QWebEngineSettings
        has_webengine = True
    except Exception as e:
        print(f"⚠️ [WebEngine Import Warning]: {e}")
        has_webengine = False

    from PyQt6.QtWidgets import (
        QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
        QPushButton, QLineEdit, QProgressBar, QSystemTrayIcon, QMenu
    )
    from PyQt6.QtCore import Qt, pyqtSignal, QObject, QUrl, QPointF, QTimer
    from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QPolygonF, QAction, QKeySequence, QShortcut

    configure_hyprland_overlay_rules()

    class UiBridge(QObject):
        event_signal = pyqtSignal(dict)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    def create_bat_icon() -> QIcon:
        pix = QPixmap(64, 64)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#00e5ff"))
        painter.setPen(QColor("#00e5ff"))
        pts = [
            QPointF(4, 20), QPointF(24, 22), QPointF(28, 12), QPointF(32, 18),
            QPointF(36, 12), QPointF(40, 22), QPointF(60, 20), QPointF(48, 38),
            QPointF(32, 52), QPointF(16, 38)
        ]
        painter.drawPolygon(QPolygonF(pts))
        painter.end()
        return QIcon(pix)

    bat_icon = create_bat_icon()

    desktop_shells = []

    def _start_hyprland_desktop_watcher():
        """
        Watches Hyprland IPC socket2 in real-time.
        When user switches to an empty workspace (or has no window covering the monitor),
        wakes up the Batcomputer HUD on that monitor's desktop.
        When user opens or switches to an application window, puts HUD to sleep immediately.
        """
        def _watcher_loop():
            sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
            runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
            sock_path = f"{runtime}/hypr/{sig}/.socket2.sock" if sig else None
            if not sock_path or not os.path.exists(sock_path):
                candidates = list(Path(f"{runtime}/hypr").glob("*/.socket2.sock"))
                if candidates:
                    sock_path = str(candidates[0])

            if not sock_path or not os.path.exists(sock_path):
                return

            last_states = {}

            def evaluate_desktop():
                nonlocal last_states
                try:
                    mons_raw = subprocess.check_output(["hyprctl", "monitors", "-j"], timeout=1).decode("utf-8")
                    mons = json.loads(mons_raw) if mons_raw.strip() else []
                    clients_raw = subprocess.check_output(["hyprctl", "clients", "-j"], timeout=1).decode("utf-8")
                    clients = json.loads(clients_raw) if clients_raw.strip() else []

                    # Count real application windows per workspace (strictly ignoring Batcomputer windows and micro-overlays)
                    ws_app_windows = {}
                    for c in clients:
                        title = c.get("title", "")
                        cls = c.get("class", "")
                        if "Puter // BCv1.2" in title or (cls == "python3" and "Puter" in title):
                            continue
                        # If a window is mapped and has visible dimensions (>50px), it is an active application
                        if c.get("mapped") and c.get("size", [0, 0])[0] > 50 and c.get("size", [0, 0])[1] > 50:
                            ws_id = c.get("workspace", {}).get("id")
                            if ws_id is not None:
                                ws_app_windows[ws_id] = ws_app_windows.get(ws_id, 0) + 1

                    for m in mons:
                        m_name = m["name"]
                        ws_id = m.get("activeWorkspace", {}).get("id")
                        real_app_count = ws_app_windows.get(ws_id, 0)
                        
                        # Strict rule: Desktop HUD is ONLY active if the workspace is completely empty (0 application windows).
                        # If ANY app (e.g. Zen Browser, Discord, Code, Terminal) exists on this workspace, HUD remains SLEEPING.
                        is_desktop = (real_app_count == 0)
                        current_state = (is_desktop, ws_id)

                        if last_states.get(m_name) != current_state:
                            last_states[m_name] = current_state
                            print(f"🖥️ [Desktop Auto-Wake]: Monitor {m_name} (ws {ws_id}, apps {real_app_count}) -> {'WAKE (Empty Desktop)' if is_desktop else 'SLEEP (App Active)'}")
                            daemon.emit_ui(auto_desktop_wake={"monitor": m_name, "state": is_desktop, "workspace": str(ws_id)})
                except Exception:
                    pass

            time.sleep(1.0)
            evaluate_desktop()

            while daemon.running:
                try:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.connect(sock_path)
                    buf = ""
                    while daemon.running:
                        chunk = s.recv(2048).decode("utf-8", errors="ignore")
                        if not chunk:
                            break
                        buf += chunk
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            # Only re-evaluate on actual workspace switches or window open/close/move.
                            # NEVER re-evaluate on tab switching (activewindow) so browsing between tabs is 100% uninterrupted.
                            if any(ev in line for ev in ("workspace>>", "workspacev2>>", "focusedmon>>", "closewindow>>", "openwindow>>", "movewindow>>")):
                                time.sleep(0.08)
                                evaluate_desktop()
                except Exception:
                    time.sleep(2)
                    continue

        t = threading.Thread(target=_watcher_loop, daemon=True)
        t.start()

    # Full-Screen Dual/Multi-Monitor BCv1.2 Desktop Override Window (QWebEngineView)
    class DesktopShellWindow(QWidget):
        def __init__(self, target_url: str, screen):
            super().__init__()
            self.screen_obj = screen
            s_name = screen.name()
            self.screen_name = s_name
            self.setWindowTitle(f"Puter // BCv1.2 Desktop Shell - {s_name}")
            self.setWindowIcon(bat_icon)
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setStyleSheet("background-color: #01060d; border: none;")

            self.setScreen(screen)
            self.setGeometry(screen.geometry())

            lyt = QVBoxLayout(self)
            lyt.setContentsMargins(0, 0, 0, 0)
            lyt.setSpacing(0)

            self.web = None
            if has_webengine:
                try:
                    self.web = QWebEngineView(self)
                    self.web.page().setBackgroundColor(QColor("#01060d"))
                    settings = self.web.settings()
                    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
                    settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
                    # Load directly from the local bridge with screen identifier and auth token
                    token_arg = f"&token={daemon.auth_token}" if hasattr(daemon, "auth_token") and daemon.auth_token else ""
                    local_shell_url = f"http://127.0.0.1:{LOCAL_BRIDGE_PORT}/?desktop=1&screen={s_name}{token_arg}"
                    self.web.setUrl(QUrl(local_shell_url))
                    lyt.addWidget(self.web)
                except Exception as e:
                    fallback_lbl = QLabel(f"WebEngine load error ({e})")
                    fallback_lbl.setStyleSheet("color: #00e5ff; font-size: 16px; padding: 40px;")
                    lyt.addWidget(fallback_lbl)
            else:
                fallback_lbl = QLabel("WebEngine not available on system.")
                fallback_lbl.setStyleSheet("color: #00e5ff; font-size: 16px; padding: 40px;")
                lyt.addWidget(fallback_lbl)

            self._is_visible_on_screen = False
            self.show()

            # Start dormant in a hidden special workspace so it never covers apps at startup
            QTimer.singleShot(250, self.hide_desktop_shell)

            # Keyboard shortcuts inside Desktop Shell: F11 or Escape exits full-screen desktop override across all screens
            esc_short = QShortcut(QKeySequence("Escape"), self)
            esc_short.activated.connect(self._exit_all_desktops)
            f11_short = QShortcut(QKeySequence("F11"), self)
            f11_short.activated.connect(self._exit_all_desktops)

        def is_shell_visible(self) -> bool:
            return getattr(self, "_is_visible_on_screen", False)

        def _exit_all_desktops(self):
            if hasattr(self, "_companion_ref") and self._companion_ref:
                self._companion_ref.toggle_desktop_override(force_state="off")
            else:
                for s in desktop_shells:
                    s.hide_desktop_shell()

        def get_hypr_address(self) -> Optional[str]:
            if getattr(self, "_hypr_address", None):
                return self._hypr_address
            try:
                clients_raw = subprocess.check_output(["hyprctl", "clients", "-j"], timeout=1).decode("utf-8")
                clients = json.loads(clients_raw)
                my_pid = os.getpid()
                target_title = f"Puter // BCv1.2 Desktop Shell - {self.screen_name}"
                for c in clients:
                    if c.get("pid") == my_pid and c.get("title") == target_title:
                        self._hypr_address = c.get("address")
                        return self._hypr_address
            except Exception:
                pass
            return None

        def show_desktop_shell(self, target_ws: Optional[str] = None):
            addr = self.get_hypr_address()
            ws = str(target_ws) if target_ws else "1"
            win_match = f"address:{addr}" if addr else f"title:Puter // BCv1.2 Desktop Shell - {self.screen_name}"
            lua = f'return hl.dispatch(hl.dsp.window.move({{ window = "{win_match}", workspace = "{ws}", follow = false }}))'
            subprocess.run(["hyprctl", "repl", lua], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
            self._is_visible_on_screen = True

        def hide_desktop_shell(self):
            addr = self.get_hypr_address()
            special_ws = f"special:batdesk_{self.screen_name}"
            win_match = f"address:{addr}" if addr else f"title:Puter // BCv1.2 Desktop Shell - {self.screen_name}"
            lua = f'return hl.dispatch(hl.dsp.window.move({{ window = "{win_match}", workspace = "{special_ws}", follow = false }}))'
            subprocess.run(["hyprctl", "repl", lua], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
            self._is_visible_on_screen = False

        def closeEvent(self, event):
            event.ignore()
            # Intercept Super+W so it never destroys the desktop HUD.
            # Instead, smoothly return user to their previous active workspace (e.g. Antigravity or Zen Browser)
            lua = 'return hl.dispatch(hl.dsp.focus({ workspace = "previous" }))'
            subprocess.run(["hyprctl", "repl", lua], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)

    # Instantiate DesktopShellWindow on every detected monitor
    for scr in app.screens():
        desktop_shells.append(DesktopShellWindow(hud_url, scr))

    class CompanionWidget(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Puter // BCv1.2 Companion")
            self.setWindowIcon(bat_icon)
            self.setWindowFlags(
                Qt.WindowType.Window
                | Qt.WindowType.WindowStaysOnTopHint
            )
            self.setFixedSize(420, 265)
            self._drag_pos = None

            # Position in top-right of target screen
            target_screen = app.primaryScreen()
            for scr in app.screens():
                if "DP-1" in scr.name() or scr.geometry().width() >= 2560:
                    target_screen = scr
                    break
            geom = target_screen.geometry()
            self.move(geom.x() + geom.width() - 440, geom.y() + 45)

            self.setStyleSheet("""
                QWidget {
                    background-color: #030d1a;
                    color: #e0faff;
                    font-family: 'Share Tech Mono', 'JetBrains Mono', monospace;
                    border: 1px solid #00e5ff;
                }
                QLabel {
                    border: none;
                    background: transparent;
                }
                QPushButton {
                    background-color: #05223d;
                    border: 1px solid #00e5ff;
                    color: #00e5ff;
                    font-weight: bold;
                    font-size: 9.5px;
                    padding: 4px 6px;
                    border-radius: 2px;
                }
                QPushButton:hover {
                    background-color: #00e5ff;
                    color: #000000;
                }
                QLineEdit {
                    background-color: #021324;
                    border: 1px solid rgba(0, 229, 255, 0.45);
                    color: #ffffff;
                    font-size: 11px;
                    padding: 4px 6px;
                }
                QProgressBar {
                    border: 1px solid rgba(0, 229, 255, 0.35);
                    background-color: #010912;
                    height: 8px;
                    text-align: center;
                }
                QProgressBar::chunk {
                    background-color: #00ff88;
                }
            """)

            layout = QVBoxLayout(self)
            layout.setContentsMargins(10, 8, 10, 8)
            layout.setSpacing(6)

            # Top Header Row
            hdr = QHBoxLayout()
            self.title_lbl = QLabel("🦇 'PUTER // BCv1.2 VOICE COMPANION")
            self.title_lbl.setStyleSheet("color: #00e5ff; font-weight: bold; font-size: 11px;")
            self.state_lbl = QLabel("STANDBY")
            self.state_lbl.setStyleSheet(
                "background-color: rgba(0, 229, 255, 0.15); border: 1px solid #00e5ff; "
                "color: #00ff88; font-weight: bold; font-size: 10px; padding: 2px 6px;"
            )
            hdr.addWidget(self.title_lbl)
            hdr.addStretch()
            hdr.addWidget(self.state_lbl)
            layout.addLayout(hdr)

            # Mic Info + Live RMS Waveform Meter
            mic_row = QHBoxLayout()
            self.mic_lbl = QLabel("🎤 Mic: Checking...")
            self.mic_lbl.setStyleSheet("color: #99d9ea; font-size: 9.5px;")
            self.rms_bar = QProgressBar()
            self.rms_bar.setRange(0, 100)
            self.rms_bar.setValue(0)
            self.rms_bar.setTextVisible(False)
            self.rms_bar.setFixedWidth(120)
            mic_row.addWidget(self.mic_lbl, 1)
            mic_row.addWidget(self.rms_bar)
            layout.addLayout(mic_row)

            # Live User Transcript Box
            self.user_box = QLabel("🗣️ YOU: Say \"Computer\", \"Puter\", \"Hey Computer\", or \"Hey Puter\"...")
            self.user_box.setWordWrap(True)
            self.user_box.setStyleSheet(
                "background-color: #05192e; border-left: 3px solid #00e5ff; "
                "color: #a8f0ff; font-size: 11px; padding: 5px 7px;"
            )
            self.user_box.setFixedHeight(42)
            layout.addWidget(self.user_box)

            # Live 'Puter Response Transcription Box
            self.reply_box = QLabel("🦇 'PUTER: Standing by, Master Ramos. Click 🖥️ DESKTOP to override your full desktop!")
            self.reply_box.setWordWrap(True)
            self.reply_box.setStyleSheet(
                "background-color: #041d29; border-left: 3px solid #00ff88; "
                "color: #ffffff; font-size: 11.5px; padding: 6px 8px;"
            )
            self.reply_box.setFixedHeight(78)
            layout.addWidget(self.reply_box)

            # Quick Command Input Row
            inp_row = QHBoxLayout()
            self.cmd_input = QLineEdit()
            self.cmd_input.setPlaceholderText("Type command to 'Puter or press Enter...")
            self.cmd_input.returnPressed.connect(self.on_send_clicked)
            self.send_btn = QPushButton("SEND")
            self.send_btn.clicked.connect(self.on_send_clicked)
            inp_row.addWidget(self.cmd_input, 1)
            inp_row.addWidget(self.send_btn)
            layout.addLayout(inp_row)

            # Bottom Action Buttons
            btn_row = QHBoxLayout()
            self.talk_btn = QPushButton("🎙️ TALK")
            self.talk_btn.clicked.connect(lambda: threading.Thread(target=daemon.trigger_push_to_talk, daemon=True).start())

            self.stop_btn = QPushButton("⏹️ STOP")
            self.stop_btn.clicked.connect(lambda: threading.Thread(target=daemon.stop_speaking, daemon=True).start())

            self.mode_btn = QPushButton("WAKE: PUTER")
            self.mode_btn.clicked.connect(self.toggle_wake_mode)

            self.desktop_btn = QPushButton("🖥️ DESKTOP")
            self.desktop_btn.setStyleSheet("border-color: #00ff88; color: #00ff88;")
            self.desktop_btn.clicked.connect(self.toggle_desktop_override)

            self.hide_btn = QPushButton("HIDE")
            self.hide_btn.clicked.connect(self.hide)

            btn_row.addWidget(self.talk_btn)
            btn_row.addWidget(self.stop_btn)
            btn_row.addWidget(self.mode_btn)
            btn_row.addWidget(self.desktop_btn)
            btn_row.addWidget(self.hide_btn)
            layout.addLayout(btn_row)

            # Keyboard shortcut: Escape dismisses the mini companion
            esc_short = QShortcut(QKeySequence("Escape"), self)
            esc_short.activated.connect(self.hide)

        def closeEvent(self, event):
            event.ignore()
            self.hide()

        def mousePressEvent(self, event):
            if event.button() == Qt.MouseButton.LeftButton:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()

        def mouseMoveEvent(self, event):
            if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
                self.move(event.globalPosition().toPoint() - self._drag_pos)
                event.accept()

        def on_send_clicked(self):
            txt = self.cmd_input.text().strip()
            if not txt:
                return
            self.cmd_input.clear()
            threading.Thread(target=daemon.send_text_directive, args=(txt,), daemon=True).start()

        def toggle_wake_mode(self):
            daemon.wake_word_required = not daemon.wake_word_required
            if daemon.wake_word_required:
                self.mode_btn.setText("WAKE: PUTER")
                self.mode_btn.setStyleSheet("")
            else:
                self.mode_btn.setText("⚡ ALWAYS ON")
                self.mode_btn.setStyleSheet("background-color: rgba(0,255,136,0.25); color: #00ff88; border-color: #00ff88;")

        def toggle_desktop_override(self, force_state: Optional[str] = None):
            global _DESKTOP_MODE_ACTIVE
            try:
                mons_raw = subprocess.check_output(["hyprctl", "monitors", "-j"], timeout=1).decode("utf-8")
                mons = json.loads(mons_raw) if mons_raw.strip() else []
                clients_raw = subprocess.check_output(["hyprctl", "clients", "-j"], timeout=1).decode("utf-8")
                clients = json.loads(clients_raw) if clients_raw.strip() else []

                focused_mon = next((m for m in mons if m.get("focused")), mons[0] if mons else None)
                if not focused_mon:
                    return

                curr_ws = focused_mon.get("activeWorkspace", {}).get("id")

                app_windows = [
                    c for c in clients
                    if c.get("mapped")
                    and "Puter // BCv1.2" not in c.get("title", "")
                    and "batdesk" not in str(c.get("workspace", {}).get("name", ""))
                    and c.get("size", [0, 0])[0] > 50
                ]
                curr_ws_apps = [c for c in app_windows if c.get("workspace", {}).get("id") == curr_ws]

                if len(curr_ws_apps) > 0 or force_state == "on":
                    # Current workspace has active applications (e.g. Antigravity, Zen Browser).
                    # Move user smoothly to an empty workspace so Batcomputer Full HUD launches there without covering apps!
                    used_ws = {c.get("workspace", {}).get("id") for c in app_windows}
                    target_ws = next((cand for cand in [3, 4, 6, 7, 8, 9, 10] if cand not in used_ws), 9)
                    lua = f'return hl.dispatch(hl.dsp.focus({{ workspace = "{target_ws}" }}))'
                    subprocess.run(["hyprctl", "repl", lua], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
                    self.desktop_btn.setText("🖥️ EXIT DESK")
                    self.desktop_btn.setStyleSheet("background-color: rgba(0,255,136,0.28); color: #00ff88; border-color: #00ff88;")
                    _DESKTOP_MODE_ACTIVE = True
                else:
                    # Current workspace is already an empty desktop workspace.
                    # Switch user back to their previous active workspace (where Antigravity was)!
                    lua = 'return hl.dispatch(hl.dsp.focus({ workspace = "previous" }))'
                    subprocess.run(["hyprctl", "repl", lua], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
                    self.desktop_btn.setText("🖥️ DESKTOP")
                    self.desktop_btn.setStyleSheet("border-color: #00ff88; color: #00ff88;")
                    _DESKTOP_MODE_ACTIVE = False
            except Exception as e:
                print(f"Error in toggle_desktop_override: {e}")

        def handle_event(self, ev: dict):
            if "auto_desktop_wake" in ev:
                wake_info = ev["auto_desktop_wake"]
                target_mon = wake_info.get("monitor")
                is_wake = wake_info.get("state")
                target_ws = wake_info.get("workspace")
                for s in desktop_shells:
                    s_name = s.screen_name
                    if target_mon == "all" or s_name == target_mon:
                        if is_wake:
                            s.show_desktop_shell(target_ws=target_ws)
                        else:
                            s.hide_desktop_shell()
                any_vis = any(s.is_shell_visible() for s in desktop_shells)
                if any_vis:
                    self.desktop_btn.setText("🖥️ EXIT DESK")
                    self.desktop_btn.setStyleSheet("background-color: rgba(0,255,136,0.28); color: #00ff88; border-color: #00ff88;")
                else:
                    self.desktop_btn.setText("🖥️ DESKTOP")
                    self.desktop_btn.setStyleSheet("border-color: #00ff88; color: #00ff88;")
            if "companion_action" in ev:
                act = ev["companion_action"]
                if act == "show":
                    self.show()
                    self.raise_()
                    self.activateWindow()
                    self.cmd_input.setFocus()
                    self.cmd_input.selectAll()
                elif act == "hide":
                    self.hide()
                elif act == "toggle":
                    if self.isVisible():
                        self.hide()
                    else:
                        self.show()
                        self.raise_()
                        self.activateWindow()
                        self.cmd_input.setFocus()
                        self.cmd_input.selectAll()
            if "desktop_toggle" in ev:
                act = ev["desktop_toggle"]
                if act == "on":
                    self.toggle_desktop_override(force_state="on")
                elif act == "off":
                    self.toggle_desktop_override(force_state="off")
                else:
                    self.toggle_desktop_override(force_state=None)
            if "rms" in ev:
                pct = min(100, int((ev["rms"] / 0.08) * 100))
                self.rms_bar.setValue(pct)
            if "mic_name" in ev:
                is_real = ev.get("mic_real", True)
                short_mic = ev["mic_name"].replace("alsa_input.", "").replace("usb-", "")[:34]
                if is_real:
                    self.mic_lbl.setText(f"🎤 {short_mic}")
                    self.mic_lbl.setStyleSheet("color: #00ff88; font-size: 9.5px;")
                else:
                    self.mic_lbl.setText("⚠️ No Mic (Connect AirPods / Razer!)")
                    self.mic_lbl.setStyleSheet("color: #ffaa00; font-weight: bold; font-size: 9.5px;")
            if "state" in ev:
                st = ev["state"]
                self.state_lbl.setText(st)
                if "CONVO" in st or "OPEN" in st:
                    self.state_lbl.setStyleSheet(
                        "background-color: rgba(0, 255, 136, 0.28); border: 1px solid #00ff88; "
                        "color: #00ff88; font-weight: bold; font-size: 10px; padding: 2px 6px;"
                    )
                elif "HEARING" in st or "LISTENING" in st or "AWAITING" in st:
                    self.state_lbl.setStyleSheet(
                        "background-color: rgba(255, 50, 80, 0.28); border: 1px solid #ff3355; "
                        "color: #ff6680; font-weight: bold; font-size: 10px; padding: 2px 6px;"
                    )
                elif "SPEAKING" in st:
                    self.state_lbl.setStyleSheet(
                        "background-color: rgba(0, 255, 136, 0.22); border: 1px solid #00ff88; "
                        "color: #00ff88; font-weight: bold; font-size: 10px; padding: 2px 6px;"
                    )
                else:
                    self.state_lbl.setStyleSheet(
                        "background-color: rgba(0, 229, 255, 0.15); border: 1px solid #00e5ff; "
                        "color: #00e5ff; font-weight: bold; font-size: 10px; padding: 2px 6px;"
                    )
            if "ignored_text" in ev:
                self.user_box.setText(f"🔇 {ev['ignored_text']}")
            if "user_text" in ev:
                self.user_box.setText(f"🗣️ YOU: \"{ev['user_text']}\"")
            if "reply_text" in ev:
                lat_str = f" [{ev['latency_ms']}ms]" if ev.get("latency_ms") else ""
                prefix = "🤖 ALFRED // HERMES" if ev.get("hermes") else f"🦇 'PUTER{lat_str}"
                self.reply_box.setText(f"{prefix}: \"{ev['reply_text']}\"")

    bridge = UiBridge()
    win = CompanionWidget()
    for s in desktop_shells:
        s._companion_ref = win
    bridge.event_signal.connect(win.handle_event)
    daemon.ui_callback = lambda d: bridge.event_signal.emit(d)

    # System Tray Icon
    tray = QSystemTrayIcon(bat_icon, app)
    tray.setToolTip("'Puter // Batcomputer Desktop Shell & Voice Companion")
    menu = QMenu()
    act_desktop = QAction("🖥️ Toggle Full-Screen BCv1.2 Desktop Override", menu)
    act_desktop.triggered.connect(lambda: win.toggle_desktop_override())
    act_show = QAction("Show / Hide 'Puter Companion Pill", menu)
    act_show.triggered.connect(lambda: win.hide() if win.isVisible() else win.show())
    act_talk = QAction("🎙️ Talk Now (No Wake Word)", menu)
    act_talk.triggered.connect(lambda: threading.Thread(target=daemon.trigger_push_to_talk, daemon=True).start())
    act_quit = QAction("❌ Quit 'Puter Daemon", menu)
    act_quit.triggered.connect(app.quit)
    menu.addAction(act_desktop)
    menu.addAction(act_show)
    menu.addAction(act_talk)
    menu.addSeparator()
    menu.addAction(act_quit)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: win.show() if not win.isVisible() else win.hide())
    tray.show()

    # Auto-wake Batcomputer Desktop HUD whenever an empty workspace/desktop is active
    if os.environ.get("WAYLAND_DISPLAY"):
        _start_hyprland_desktop_watcher()

    if start_in_desktop_mode:
        win.toggle_desktop_override(force_state="on")

    win.show()
    win.raise_()

    # Run audio listener in background thread
    audio_thread = threading.Thread(target=daemon.run_audio_loop, daemon=True)
    audio_thread.start()

    sys.exit(app.exec())


def launch_frameless_hud_window(hud_url: str):
    """Open the BCv1.2 HUD in a frameless native desktop app window."""
    if sys.platform == "darwin":
        for app_bin in [
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]:
            if Path(app_bin).exists():
                subprocess.Popen([app_bin, f"--app={hud_url}", "--window-size=1600,960"])
                return
        subprocess.Popen(["open", hud_url])
    else:
        for browser in ["brave-browser", "google-chrome", "chromium", "chromium-browser", "microsoft-edge"]:
            if shutil.which(browser):
                subprocess.Popen([browser, f"--app={hud_url}", "--window-size=1600,960"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
        subprocess.Popen(["xdg-open", hud_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


_INSTANCE_LOCK_FD = None

def acquire_single_instance_lock() -> bool:
    """Enforce a strict single-instance lock across the system to prevent dual voice listeners/speakers."""
    global _INSTANCE_LOCK_FD
    lock_file = Path(tempfile.gettempdir()) / "alfred_batcomputer_daemon.lock"
    try:
        _INSTANCE_LOCK_FD = open(lock_file, "w")
        fcntl.flock(_INSTANCE_LOCK_FD, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _INSTANCE_LOCK_FD.write(f"{os.getpid()}\n")
        _INSTANCE_LOCK_FD.flush()
        return True
    except (BlockingIOError, OSError):
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="'Puter // Alfred BCv1.2 Native Desktop Override & Background Voice Daemon")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL, help="Batcomputer backend URL")
    parser.add_argument("--hud-url", default=DEFAULT_HUD_URL, help="Batcomputer HTTPS HUD URL")
    parser.add_argument("--always", action="store_true", help="Treat every utterance as a command without needing 'Computer'/'Puter' wake word")
    parser.add_argument("--desktop", action="store_true", help="Override the full desktop with the BCv1.2 Shell window on startup")
    parser.add_argument("--hud", action="store_true", help="Alias for --desktop")
    parser.add_argument("--no-gui", action="store_true", help="Run headless in terminal (desktop notifications still active)")
    parser.add_argument("--no-notify", action="store_true", help="Disable OS desktop notifications")
    parser.add_argument("--briefing", action="store_true", help="Trigger proactive JARVIS briefing immediately on startup")
    parser.add_argument("--daemon", action="store_true", help="Run under systemd background daemon service")
    args = parser.parse_args()

    if not acquire_single_instance_lock():
        print("🦇 ['Puter Notice]: Another instance of Batcomputer daemon is already active. Exiting duplicate process to prevent double voice responses.")
        sys.exit(0)

    daemon = AlfredBackgroundDaemon(
        server_url=args.server,
        wake_word_required=not args.always,
        notify_enabled=not args.no_notify
    )

    # Start the Local Desktop Bridge HTTP Server (127.0.0.1:7778) for local file & app browsing
    start_local_desktop_bridge_server(daemon, port=LOCAL_BRIDGE_PORT)

    if args.briefing:
        def _run_startup_briefing():
            time.sleep(1.2)
            try:
                b = daemon.session.get(f"{daemon.server_url}/api/briefing?skip_tts=false", timeout=12).json()
                reply = b.get("reply", "")
                print(f"🌅 [Briefing]: {reply}")
                daemon.emit_ui(state="SPEAKING", user_text="Morning Briefing", reply_text=reply)
                daemon.notifier.notify("🌅 'Puter Proactive Briefing", reply, timeout_ms=10000)
                if b.get("audio_url"):
                    daemon.play_audio_url(b["audio_url"])
            except Exception as e:
                print(f"Briefing error: {e}")
        threading.Thread(target=_run_startup_briefing, daemon=True).start()

    # Use PyQt6 Companion + Desktop Shell GUI by default if DISPLAY/WAYLAND_DISPLAY is present and --no-gui wasn't passed
    has_display = bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY") or sys.platform == "darwin")
    if has_display and not args.no_gui:
        try:
            import PyQt6  # noqa: F401
            run_pyqt_companion_app(daemon, args.hud_url, start_in_desktop_mode=(args.desktop or args.hud))
        except ImportError:
            pass

    try:
        daemon.run_audio_loop()
    except KeyboardInterrupt:
        print("\n['Puter Daemon Offline]")
