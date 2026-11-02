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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import deque
from pathlib import Path
from typing import Optional, Tuple, Callable
import requests

DEFAULT_SERVER_URL = os.environ.get("ALFRED_SERVER_URL", "https://batcomputer.jmrdserver.duckdns.org")
FALLBACK_SERVER_URL = "http://100.77.249.27:7776"
DEFAULT_HUD_URL = os.environ.get("ALFRED_HUD_URL", "https://batcomputer.jmrdserver.duckdns.org")
LOCAL_BRIDGE_PORT = int(os.environ.get("ALFRED_LOCAL_BRIDGE_PORT", "7778"))

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_DURATION_S = 0.1  # 100ms frames
CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_DURATION_S)
PRE_ROLL_CHUNKS = 4     # 400ms pre-roll buffer so "Computer" / "Puter" never clips the first syllable

# VAD thresholds
SPEECH_RMS_THRESHOLD = float(os.environ.get("ALFRED_VAD_THRESHOLD", "0.018"))
BARGE_IN_RMS_THRESHOLD = float(os.environ.get("ALFRED_BARGE_THRESHOLD", "0.055"))
SILENCE_LIMIT_S = 0.70
MAX_RECORD_S = 8.0

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
        self.active_player: Optional[subprocess.Popen] = None
        self.awaiting_followup_until = 0.0
        self.force_next_utterance = False
        self.force_stop_recording = False
        self.muted = False
        self.running = True
        self.current_mic_name = "Default Input"
        self.current_mic_is_real = True
        self._chime_wav_high = self._build_chime_wav([880, 1320], duration_s=0.065)
        self._chime_wav_low = self._build_chime_wav([660], duration_s=0.06)

    def emit_ui(self, **kwargs):
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

    def check_server_connectivity(self):
        try:
            r = self.session.get(f"{self.server_url}/api/telemetry", timeout=4)
            if r.status_code == 200:
                return
        except Exception as e:
            print(f"⚠️ [Primary URL {self.server_url} unreachable: {e}] — switching to {FALLBACK_SERVER_URL}")
            self.server_url = FALLBACK_SERVER_URL

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
            for cmd in (["paplay", wav_path], ["aplay", "-q", wav_path], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", wav_path]):
                if shutil.which(cmd[0]):
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    break

    def _on_playback_ended(self):
        """Called immediately when TTS audio finishes playing to open the 10s seamless conversation window."""
        self.awaiting_followup_until = time.time() + 10.0
        self.play_chime(high=True)
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
        tmp_img = Path(tempfile.gettempdir()) / f"alfred_screen_{os.getpid()}.jpg"
        try:
            if sys.platform == "darwin":
                subprocess.run(["/usr/sbin/screencapture", "-x", "-t", "jpg", str(tmp_img)], timeout=3, check=True)
            else:
                for cmd in [
                    ["gnome-screenshot", "-f", str(tmp_img)],
                    ["grim", str(tmp_img)],
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
            if not local_voice_action.get("keep_desktop"):
                ev_payload["desktop_toggle"] = "off"
            self.emit_ui(**ev_payload)
            self.notifier.notify("🖥️ 'Puter Local Desktop Telemetry", reply_msg, timeout_ms=7000)
            self._speak_short_reply(reply_msg)
            return

        # Check if user asked 'Puter to inspect their local screen
        if any(k in directive.lower() for k in ["look at my screen", "what is on my screen", "what's on my screen", "analyze my screen", "read my screen", "look at this error"]):
            print(f"👁️ [Screen Vision]: Capturing screen for directive: \"{directive}\"")
            self.emit_ui(state="SCANNING SCREEN...", user_text=directive)
            b64_screen = self.capture_local_screen_b64()
            try:
                v_res = self.session.post(
                    f"{self.server_url}/api/vision",
                    json={"image": b64_screen, "prompt": directive, "skip_tts": False},
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

    def process_wav_bytes(self, wav_bytes: bytes):
        self.emit_ui(state="TRANSCRIBING (MLX)...")
        t0 = time.perf_counter()
        try:
            r = self.session.post(
                f"{self.server_url}/api/transcribe",
                files={"file": ("speech.wav", wav_bytes, "audio/wav")},
                timeout=8
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
            directive = transcript[match.end():].strip(" ,.!:;-?")
            if len(directive) < 3:
                # User said "Computer", "Puter", "Hey Computer", or "Hey Puter" — chime and open 8-second follow-up window!
                self.play_chime(high=True)
                print("🦇 ['Puter]: Standing by, Master Ramos! Speak your directive (8s follow-up window)...")
                self.awaiting_followup_until = time.time() + 8.0
                self.emit_ui(
                    state="AWAITING DIRECTIVE (8s)",
                    user_text=transcript,
                    reply_text="Standing by, Master Ramos — speak your command now!"
                )
                self.notifier.notify(
                    "🎙️ 'Puter Listening (8s Window)",
                    f"Heard \"{transcript}\" — Standing by, Master Ramos. Speak your directive!",
                    timeout_ms=5000
                )
                return
        else:
            directive = LEADING_WAKE_STRIP_REGEX.sub("", transcript).strip()
            if not directive:
                self.emit_ui(state="STANDBY")
                return

        self.awaiting_followup_until = 0.0
        self.play_chime(high=True)
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
                            self.play_chime(high=True)
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


def open_local_file_or_dir(target_path: str, app_mode: str = "default") -> dict:
    p = Path(target_path).expanduser()
    if not p.exists():
        return {"success": False, "error": f"Path not found: {p}"}
    try:
        if app_mode == "code" and shutil.which("code"):
            subprocess.Popen(["code", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif app_mode == "nautilus" and shutil.which("nautilus"):
            subprocess.Popen(["nautilus", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif app_mode == "terminal":
            term_dir = str(p if p.is_dir() else p.parent)
            for term in (["foot", "-D", term_dir], ["kitty", "--directory", term_dir], ["gnome-terminal", f"--working-directory={term_dir}"]):
                if shutil.which(term[0]):
                    subprocess.Popen(term, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    break
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"success": True, "path": str(p), "app": app_mode}
    except Exception as e:
        return {"success": False, "error": str(e)}


def launch_local_desktop_application(app_key: str, optional_path: str = "") -> dict:
    key = app_key.lower().strip()
    target_path = str(Path(optional_path).expanduser()) if optional_path else str(Path.home())
    try:
        if key in ("steam", "games"):
            if shutil.which("steam"):
                subprocess.Popen(["steam"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return {"success": True, "message": "Launching Steam on your desktop, Master Ramos."}
            return open_local_file_or_dir(str(Path.home() / "Games"), "default")
        if key in ("code", "vscode"):
            cmd = ["code", target_path] if optional_path else ["code"]
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"success": True, "message": "Opening Visual Studio Code on your desktop, Master Ramos."}
        if key in ("nautilus", "files", "computer", "explorer"):
            if shutil.which("nautilus"):
                subprocess.Popen(["nautilus", target_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", target_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"success": True, "message": f"Opening file manager at {Path(target_path).name or target_path}, Master Ramos."}
        if key in ("browser", "web", "zen", "zen-browser"):
            for br in ("zen-browser", "brave-browser", "firefox", "google-chrome", "chromium"):
                if shutil.which(br):
                    subprocess.Popen([br], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return {"success": True, "message": f"Launching {br} on your desktop, Master Ramos."}
        if key in ("terminal", "term", "foot", "shell"):
            for term in (["foot", "-D", target_path], ["kitty", "--directory", target_path], ["alacritty", "--working-directory", target_path]):
                if shutil.which(term[0]):
                    subprocess.Popen(term, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return {"success": True, "message": "Opening terminal on your desktop, Master Ramos."}
        return open_local_file_or_dir(target_path, "default")
    except Exception as e:
        return {"success": False, "message": f"Failed to launch {app_key}: {e}"}


def handle_local_voice_shortcut(low_dir: str) -> Optional[dict]:
    """Allow voice commands for local hardware temps (GPU/CPU/NVMe/Fans) and opening local folders/apps."""
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
            self.wfile.write(body)

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
                    r = daemon.session.get(f"{daemon.server_url}{self.path}", timeout=12)
                    self.send_response(r.status_code)
                    self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
                    self.send_header("Content-Length", str(len(r.content)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(r.content)
                except Exception as e:
                    self._send_json({"error": str(e)}, status=502)
                return

            if parsed.path == "/local/status":
                self._send_json({
                    "ok": True,
                    "host": socket.gethostname(),
                    "desktop_mode": _DESKTOP_MODE_ACTIVE,
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

            self._send_json({"error": "Not found"}, status=404)

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            try:
                data = json.loads(raw_body) if raw_body else {}
            except Exception:
                data = {}

            if parsed.path == "/local/open":
                res = open_local_file_or_dir(data.get("path", ""), data.get("app", "default"))
                if res.get("success"):
                    daemon.emit_ui(desktop_toggle="off")
                self._send_json(res)
                return

            if parsed.path == "/local/launch":
                res = launch_local_desktop_application(data.get("app", ""), data.get("path", ""))
                if res.get("success"):
                    daemon.emit_ui(desktop_toggle="off")
                self._send_json(res)
                return

            if parsed.path == "/local/desktop-mode":
                action = data.get("action", "toggle")
                daemon.emit_ui(desktop_toggle=action)
                time.sleep(0.08)
                self._send_json({"ok": True, "desktop_mode": _DESKTOP_MODE_ACTIVE})
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
    plus full-screen 2560x1440 rules for the BCv1.2 Desktop Shell Override window."""
    if not shutil.which("hyprctl"):
        return
    rules = [
        "float,title:^(Puter // BCv1.2 Companion)$",
        "pin,title:^(Puter // BCv1.2 Companion)$",
        "move 100%-435 42,title:^(Puter // BCv1.2 Companion)$",
        "size 420 265,title:^(Puter // BCv1.2 Companion)$",
        "float,title:^(Puter // BCv1.2 Desktop Shell)$",
        "move 0 0,title:^(Puter // BCv1.2 Desktop Shell)$",
        "size 100% 100%,title:^(Puter // BCv1.2 Desktop Shell)$",
        "noborder,title:^(Puter // BCv1.2 Desktop Shell)$",
        "noshadow,title:^(Puter // BCv1.2 Desktop Shell)$",
    ]
    for r in rules:
        try:
            subprocess.run(["hyprctl", "keyword", "windowrulev2", r], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
        except Exception:
            pass


def run_pyqt_companion_app(daemon: AlfredBackgroundDaemon, hud_url: str, start_in_desktop_mode: bool = False):
    """Launch the native PyQt6 BCv1.2 Floating Companion Widget + Full-Screen Desktop Override Shell + System Tray."""
    global _DESKTOP_MODE_ACTIVE

    # CRITICAL: Import PyQt6.QtWebEngineWidgets BEFORE creating QApplication!
    # Chromium requires Qt.ApplicationAttribute.AA_ShareOpenGLContexts which is automatically configured when
    # QWebEngineWidgets is imported prior to QApplication instantiation.
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
    from PyQt6.QtCore import Qt, pyqtSignal, QObject, QUrl, QPointF
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

    # Full-Screen 2560x1440 BCv1.2 Desktop Override Window (QWebEngineView)
    class DesktopShellWindow(QWidget):
        def __init__(self, target_url: str):
            super().__init__()
            self.setWindowTitle("Puter // BCv1.2 Desktop Shell")
            self.setWindowIcon(bat_icon)
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
            self.setStyleSheet("background-color: #01060d; border: none;")

            target_screen = app.primaryScreen()
            for scr in app.screens():
                if "DP-1" in scr.name() or scr.geometry().width() >= 2560:
                    target_screen = scr
                    break
            self.setScreen(target_screen)
            self.setGeometry(target_screen.geometry())

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
                    # Load directly from the local bridge (http://127.0.0.1:7778/?desktop=1) for instant local rendering
                    local_shell_url = f"http://127.0.0.1:{LOCAL_BRIDGE_PORT}/?desktop=1"
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

            # Keyboard shortcuts inside Desktop Shell: F11 or Escape exits full-screen desktop override
            esc_short = QShortcut(QKeySequence("Escape"), self)
            esc_short.activated.connect(self.hide_desktop_shell)
            f11_short = QShortcut(QKeySequence("F11"), self)
            f11_short.activated.connect(self.hide_desktop_shell)

        def show_desktop_shell(self):
            global _DESKTOP_MODE_ACTIVE
            target_screen = app.primaryScreen()
            for scr in app.screens():
                if "DP-1" in scr.name() or scr.geometry().width() >= 2560:
                    target_screen = scr
                    break
            self.setScreen(target_screen)
            self.setGeometry(target_screen.geometry())
            self.showFullScreen()
            self.raise_()
            self.activateWindow()
            _DESKTOP_MODE_ACTIVE = True

        def hide_desktop_shell(self):
            global _DESKTOP_MODE_ACTIVE
            self.hide()
            _DESKTOP_MODE_ACTIVE = False

    desktop_shell = DesktopShellWindow(hud_url)

    class CompanionWidget(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Puter // BCv1.2 Companion")
            self.setWindowIcon(bat_icon)
            self.setWindowFlags(
                Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
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
            if force_state == "on" or (force_state is None and not desktop_shell.isVisible()):
                desktop_shell.show_desktop_shell()
                self.raise_()
                self.desktop_btn.setText("🖥️ EXIT DESK")
                self.desktop_btn.setStyleSheet("background-color: rgba(0,255,136,0.28); color: #00ff88; border-color: #00ff88;")
            else:
                desktop_shell.hide_desktop_shell()
                self.desktop_btn.setText("🖥️ DESKTOP")
                self.desktop_btn.setStyleSheet("border-color: #00ff88; color: #00ff88;")

        def handle_event(self, ev: dict):
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="'Puter // Alfred BCv1.2 Native Desktop Override & Background Voice Daemon")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL, help="Batcomputer backend URL")
    parser.add_argument("--hud-url", default=DEFAULT_HUD_URL, help="Batcomputer HTTPS HUD URL")
    parser.add_argument("--always", action="store_true", help="Treat every utterance as a command without needing 'Computer'/'Puter' wake word")
    parser.add_argument("--desktop", action="store_true", help="Override the full desktop with the BCv1.2 Shell window on startup")
    parser.add_argument("--hud", action="store_true", help="Also launch frameless full BCv1.2 Web HUD desktop window")
    parser.add_argument("--no-gui", action="store_true", help="Run headless in terminal (desktop notifications still active)")
    parser.add_argument("--no-notify", action="store_true", help="Disable OS desktop notifications")
    parser.add_argument("--briefing", action="store_true", help="Trigger proactive JARVIS briefing immediately on startup")
    args = parser.parse_args()

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
                b = requests.get(f"{args.server.rstrip('/')}/api/briefing?skip_tts=false", timeout=12).json()
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
