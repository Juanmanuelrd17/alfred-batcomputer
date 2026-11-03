#!/usr/bin/env python3
"""
Batcomputer HUD Backend Server - Full Jarvis Feature Replication
- Local LLM Brain (Ollama) with Dynamic Model Switching
- Local Voice Engine (VoiceStudio & Daniel British Butler) with Dynamic Voice Switching
- Local Speech-to-Text (Faster-Whisper via VoiceStudio)
- Second Brain / Knowledge Vault & File Retrieval
- Tactical Calendar & Daily Agenda Tool
- Website Reverse Engineer & Tech Stack Architecture Teardown
- Smart Home / Batcave Automation (Home Assistant Living Room TV & Devices)
- System Telemetry & Live M1 Apple Silicon Diagnostics
"""

import os
import sys
import time
import socket
import json
import asyncio
import uuid
import re
import subprocess
import threading
import imaplib
import email
from email.header import decode_header
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
import urllib.request
import urllib.parse
import requests
import hashlib
import secrets

# Ensure Homebrew and Hermes binaries (ffmpeg, node, hermes, etc.) are on PATH for mlx_whisper & subprocesses
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/Users/jmrd/.local/bin:/Users/jmrd/.hermes/bin:" + os.environ.get("PATH", "")

from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import psutil
import uvicorn

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
AUDIO_CACHE = BASE_DIR / "audio_cache"
BRAIN_DIR = BASE_DIR / "second_brain"
CALENDAR_FILE = BASE_DIR / "calendar.json"
CALENDAR_CONFIG_FILE = BASE_DIR / "calendar_config.json"
CUSTOM_VOICES_FILE = BASE_DIR / "custom_voices.json"
MEMORY_FILE = BASE_DIR / "memory.json"
HISTORY_FILE = BASE_DIR / "chat_history.json"
SUMMARY_FILE = BASE_DIR / "conversation_summary.json"
TOOLS_CONFIG_FILE = BASE_DIR / "tools_config.json"
AUTH_CONFIG_FILE = BASE_DIR / "auth_config.json"

ACTIVE_SESSIONS: Dict[str, Dict[str, Any]] = {}

def get_auth_config() -> dict:
    default_config = {
        "auth_enabled": True,
        "username": "admin",
        "passcode": "batcomputer2026",
        "salt": "wayne_tech_salt_2026",
        "passcode_hash": "e8929700e73146971eda1fa07817c42277f9b5bae9413e3a9692c9091f7d7a81",
        "daemon_token": "wayne-tech-daemon-7f91a0b3e218c54d193d0498a54687bb",
        "session_lifetime_sec": 2592000
    }
    if not AUTH_CONFIG_FILE.exists():
        try:
            AUTH_CONFIG_FILE.write_text(json.dumps(default_config, indent=2), encoding="utf-8")
            return default_config
        except Exception as e:
            print(f"[AUTH] Could not write default auth_config.json: {e}")
            return default_config
    try:
        cfg = json.loads(AUTH_CONFIG_FILE.read_text(encoding="utf-8"))
        changed = False
        if "daemon_token" not in cfg:
            cfg["daemon_token"] = "wayne-tech-daemon-" + secrets.token_hex(16)
            changed = True
        if "passcode_hash" not in cfg and "passcode" in cfg:
            salt = cfg.get("salt", "wayne_tech_salt_2026")
            cfg["passcode_hash"] = hashlib.sha256(f"{salt}:{cfg['passcode']}".encode()).hexdigest()
            changed = True
        if changed:
            try:
                AUTH_CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            except Exception:
                pass
        return cfg
    except Exception as e:
        print(f"[AUTH] Error reading auth config: {e}")
        return default_config

def verify_token(token: Optional[str]) -> bool:
    if not token:
        return False
    cfg = get_auth_config()
    if not cfg.get("auth_enabled", True):
        return True
    
    daemon_token = cfg.get("daemon_token")
    if daemon_token and token == daemon_token:
        return True
        
    now = time.time()
    if token in ACTIVE_SESSIONS:
        sess = ACTIVE_SESSIONS[token]
        lifetime = cfg.get("session_lifetime_sec", 2592000)
        if now - sess.get("created_at", 0) < lifetime:
            return True
        else:
            try:
                del ACTIVE_SESSIONS[token]
            except KeyError:
                pass
            
    return False

def verify_password(password: str) -> bool:
    cfg = get_auth_config()
    if not cfg.get("auth_enabled", True):
        return True
    if cfg.get("passcode") and password == cfg.get("passcode"):
        return True
    salt = cfg.get("salt", "wayne_tech_salt_2026")
    input_hash = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    if cfg.get("passcode_hash") and input_hash == cfg.get("passcode_hash"):
        return True
    if cfg.get("passcode_hash") and hashlib.sha256(password.encode()).hexdigest() == cfg.get("passcode_hash"):
        return True
    return False

AUDIO_CACHE.mkdir(exist_ok=True)
BRAIN_DIR.mkdir(exist_ok=True)

VOICESTUDIO_URL = "http://127.0.0.1:3900"
OLLAMA_URL = "http://127.0.0.1:11434"
START_TIME = time.time()

# Configurable State
CURRENT_MODEL = "antigravity/gemini-3.1-flash-lite"
CURRENT_VOICE = "daniel"  # Default: Daniel (Ultra-fast 0.3s macOS British Butler)

# Persistent HTTP session for connection pooling (saves TCP handshake latency to ZimaOS & HA)
HTTP_SESSION = requests.Session()
_HA_CACHE: Dict[str, Any] = {"data": None, "ts": 0.0}

# Home Assistant & OmniRoute Config
HASS_TOKEN = os.environ.get("HASS_TOKEN", "")
HASS_URL = os.environ.get("HASS_URL", "http://100.93.110.2:8123")
OMNIROUTE_URL = os.environ.get("OMNIROUTE_URL", "http://zimaos:20128/v1")
OMNIROUTE_KEY = os.environ.get("OMNIROUTE_KEY", "")

for env_candidate in [BASE_DIR / ".env", Path.home() / ".hermes" / ".env", Path("/Users/jmrd/.hermes/.env")]:
    if env_candidate.exists():
        for line in env_candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("HASS_TOKEN=") and not HASS_TOKEN:
                HASS_TOKEN = line.split("=", 1)[1].strip()
            elif line.startswith("HASS_URL="):
                HASS_URL = line.split("=", 1)[1].strip().rstrip("/")
            elif line.startswith("HERMES_CUSTOM_ZIMAOS_20128_API_KEY=") and not OMNIROUTE_KEY:
                OMNIROUTE_KEY = line.split("=", 1)[1].strip()
            elif line.startswith("OMNIROUTE_KEY=") and not OMNIROUTE_KEY:
                OMNIROUTE_KEY = line.split("=", 1)[1].strip()
        break

app = FastAPI(title="Batcomputer HUD - Jarvis Replication Core")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def batcomputer_auth_gate(request: Request, call_next):
    # Allow CORS preflight requests without authentication
    if request.method == "OPTIONS":
        return await call_next(request)
        
    path = request.url.path
    
    # Whitelist public HUD frontend, static assets, manifest, PWA sw, and auth endpoints
    if (
        path == "/" or
        path.startswith("/static/") or
        path in ("/manifest.json", "/sw.js", "/favicon.ico", "/api/auth/login", "/api/auth/status")
    ):
        return await call_next(request)
        
    # Extract token from Header, Cookie, or Query Parameter
    token = None
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
    elif request.headers.get("x-batcomputer-token") or request.headers.get("X-Batcomputer-Token"):
        token = (request.headers.get("x-batcomputer-token") or request.headers.get("X-Batcomputer-Token")).strip()
        
    if not token and "bat_session" in request.cookies:
        token = request.cookies.get("bat_session")
        
    if not token:
        token = request.query_params.get("token")
        
    if verify_token(token):
        return await call_next(request)
        
    return JSONResponse(
        status_code=401,
        content={
            "detail": "WAYNE ENTERPRISES // Security Clearance Required. Please log in.",
            "authenticated": False
        }
    )

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def get_network_info():
    hostname = socket.gethostname()
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    
    tailscale_ip = "100.77.249.27"
    try:
        for iface, addrs in psutil.net_if_addrs().items():
            if "utun" in iface or "tailscale" in iface.lower():
                for a in addrs:
                    if a.family == socket.AF_INET and a.address.startswith("100."):
                        tailscale_ip = a.address
                        break
    except Exception:
        pass
        
    return {
        "hostname": hostname,
        "local_ip": local_ip,
        "tailscale_ip": tailscale_ip,
        "gateway": "192.168.1.254"
    }


# ==========================================
# VOICE SYNTHESIS & TRANSCRIPTION
# ==========================================


def load_custom_voices() -> dict:
    if CUSTOM_VOICES_FILE.exists():
        try:
            return json.loads(CUSTOM_VOICES_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_custom_voice(voice_id: str, profile_id: str, name: str, engine: str = "VoiceStudio Clone"):
    voices = load_custom_voices()
    voices[voice_id] = {"id": voice_id, "profile_id": profile_id, "name": name, "engine": engine}
    CUSTOM_VOICES_FILE.write_text(json.dumps(voices, indent=2), encoding="utf-8")

def cleanup_audio_cache(max_files: int = 30):
    """Keep audio_cache bounded by removing oldest generated mp3/wav/upload files."""
    try:
        files = sorted(
            [p for p in AUDIO_CACHE.iterdir() if p.is_file() and p.name != ".gitkeep" and not p.name.startswith("ref_")],
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        for old_file in files[max_files:]:
            old_file.unlink(missing_ok=True)
    except Exception:
        pass


def synthesize_voice(text: str, voice: Optional[str] = None) -> Optional[str]:
    """Generate audio using selected voice engine (Daniel, VoiceStudio, or Cloned Michael Caine)"""
    cleanup_audio_cache()
    selected_voice = voice or CURRENT_VOICE
    filename = f"alfred_{uuid.uuid4().hex[:8]}.mp3"
    filepath = AUDIO_CACHE / filename
    
    # Clean text of markdown characters for speech synthesis
    clean_text = re.sub(r'[*_#`\[\]]', '', text)
    clean_text = re.sub(r'https?://\S+', 'link', clean_text).strip()
    if not clean_text:
        clean_text = "Directive acknowledged."

    # 1. Check if custom cloned voice (Michael Caine or custom profile)
    custom_voices = load_custom_voices()
    if selected_voice in custom_voices:
        profile_id = custom_voices[selected_voice].get("profile_id")
        try:
            res = requests.post(
                f"{VOICESTUDIO_URL}/generate",
                data={"text": clean_text, "profile_id": profile_id, "num_step": 5, "speed": 1.05},
                timeout=45
            )
            if res.status_code == 200 and len(res.content) > 1000:
                tmp_wav = AUDIO_CACHE / f"tmp_{uuid.uuid4().hex[:8]}.wav"
                tmp_wav.write_bytes(res.content)
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(tmp_wav), "-codec:a", "libmp3lame", "-qscale:a", "2", str(filepath)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=5
                )
                if tmp_wav.exists():
                    tmp_wav.unlink()
                return f"/api/audio/{filename}"
            else:
                print(f"Cloned voice generation returned {res.status_code}: {res.text[:200]}")
        except Exception as e:
            print(f"Cloned voice generation error: {e}")
            
        # Resilient fallback to Daniel so communication link never hangs
        try:
            tmp_aiff = AUDIO_CACHE / f"fallback_{uuid.uuid4().hex[:8]}.aiff"
            subprocess.run(["say", "-v", "Daniel", "-r", "195", "-o", str(tmp_aiff), clean_text], check=True, timeout=8)
            subprocess.run(["ffmpeg", "-y", "-i", str(tmp_aiff), "-codec:a", "libmp3lame", "-qscale:a", "2", str(filepath)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=5)
            if tmp_aiff.exists():
                tmp_aiff.unlink()
            return f"/api/audio/{filename}"
        except Exception:
            pass

    # 2. If native macOS neural butler voice (Direct 22kHz WAV output — zero FFmpeg overhead, ~300ms)
    if selected_voice in ["daniel", "oliver", "samantha"]:
        voice_map = {"daniel": "Daniel", "oliver": "Oliver", "samantha": "Samantha"}
        system_voice = voice_map.get(selected_voice, "Daniel")
        wav_filename = f"alfred_{uuid.uuid4().hex[:8]}.wav"
        wav_filepath = AUDIO_CACHE / wav_filename
        try:
            subprocess.run(
                ["say", "-v", system_voice, "-r", "200", "--data-format=LEI16@22050", "-o", str(wav_filepath), clean_text],
                check=True, timeout=8
            )
            return f"/api/audio/{wav_filename}"
        except Exception as e:
            print(f"macOS say synthesis error: {e}", file=sys.stderr)
            wav_filepath.unlink(missing_ok=True)

    # VoiceStudio Kokoro Fallback / Selection
    payload = json.dumps({
        "model": "tts-1",
        "input": clean_text[:500],
        "voice": selected_voice if selected_voice not in ["daniel", "oliver", "samantha"] else "alloy"
    }).encode("utf-8")
    
    req = urllib.request.Request(
        f"{VOICESTUDIO_URL}/v1/audio/speech",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            audio_data = resp.read()
            filepath.write_bytes(audio_data)
            return f"/api/audio/{filename}"
    except Exception as e:
        print(f"VoiceStudio synthesis error: {e}", file=sys.stderr)
        # Fallback to macOS say Daniel if VoiceStudio fails
        try:
            tmp_aiff = AUDIO_CACHE / f"fallback_{uuid.uuid4().hex[:8]}.aiff"
            subprocess.run(["say", "-v", "Daniel", "-o", str(tmp_aiff), clean_text], check=True, timeout=10)
            subprocess.run(["ffmpeg", "-y", "-i", str(tmp_aiff), "-codec:a", "libmp3lame", "-qscale:a", "2", str(filepath)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=5)
            if tmp_aiff.exists():
                tmp_aiff.unlink()
            return f"/api/audio/{filename}"
        except Exception:
            return None


# ==========================================
# ULTRA-FAST APPLE SILICON METAL WHISPER STT
# ==========================================
WHISPER_INITIAL_PROMPT = (
    "Computer, Hey Computer, Puter, Hey Puter, Alfred, Hermes, Master Ramos, Master Juan, Master Remmy, "
    "Batcomputer, living room, bedroom, lights, dimmer, Sony BRAVIA TV, Sonos, thermostat, garage doors, "
    "outdoor lock, Anycubic Kobra X, 3D printer, Beszel, ZimaOS, OptiPlex, GitHub repo, teardown, schedule, calendar, email."
)
_MLX_WHISPER_READY = False
WHISPER_MLX_MODEL = "mlx-community/whisper-base.en-mlx"
try:
    import mlx_whisper
    _MLX_WHISPER_READY = True
except Exception:
    mlx_whisper = None


def transcribe_audio(audio_bytes: bytes, filename: str = "audio.wav") -> str:
    """Transcribe speech in ~160ms using in-process Apple Silicon Metal GPU mlx_whisper (with VoiceStudio fallback)"""
    if not audio_bytes or len(audio_bytes) < 400:
        return ""

    if _MLX_WHISPER_READY and mlx_whisper is not None:
        tmp_audio = AUDIO_CACHE / f"stt_{uuid.uuid4().hex[:8]}_{Path(filename).name}"
        try:
            tmp_audio.write_bytes(audio_bytes)
            res = mlx_whisper.transcribe(
                str(tmp_audio),
                path_or_hf_repo=WHISPER_MLX_MODEL,
                language="en",
                initial_prompt=WHISPER_INITIAL_PROMPT,
                condition_on_previous_text=False
            )
            tmp_audio.unlink(missing_ok=True)
            text = (res.get("text") or "").strip()
            # Filter common Whisper silence hallucinations
            if text.lower() in ("you", "thank you.", "thanks for watching!", ".", ""):
                return ""
            # Normalize phonetic spellings of "Puter" and "Computer" (both standalone and with "Hey")
            text = re.sub(r'\b(?:hey|ok|yo|hi)\s+(?:pewter|purer|putter|peter|tutor|tudor|ooter|cuter|scooter)\b', 'Hey Puter', text, flags=re.I)
            text = re.sub(r'\b(?:hey|ok|yo|hi)\s+(?:commuter|computa)\b', 'Hey Computer', text, flags=re.I)
            text = re.sub(r'^\s*(?:pewter|purer|putter|peter|tutor|tudor|ooter|cuter|scooter)\b', 'Puter', text, flags=re.I)
            text = re.sub(r'^\s*(?:commuter|computa)\b', 'Computer', text, flags=re.I)
            text = re.sub(r'\b(?:pewter|purer|putter)\b', 'Puter', text, flags=re.I)
            return text
        except Exception as e:
            print(f"mlx_whisper error: {e}", file=sys.stderr)
            tmp_audio.unlink(missing_ok=True)

    try:
        res = HTTP_SESSION.post(
            f"{VOICESTUDIO_URL}/v1/audio/transcriptions",
            files={"file": (filename, audio_bytes, "audio/wav")},
            data={"model": "Systran/faster-whisper-base"},
            timeout=10
        )
        if res.status_code == 200:
            return res.json().get("text", "").strip()
    except Exception as e:
        print(f"VoiceStudio /v1/audio/transcriptions error: {e}", file=sys.stderr)

    return ""


# ==========================================
# BRAIN: OMNIROUTE (ANTIGRAVITY GEMINI FRONTIER)
# ==========================================

def is_omniroute_model(model_name: str) -> bool:
    return True


def query_omniroute(messages: List[Dict[str, str]], model: str = "antigravity/gemini-3.1-flash-lite") -> str:
    """Query OmniRoute proxy (Antigravity Gemini models) with persistent keep-alive pooling"""
    try:
        resp = HTTP_SESSION.post(
            f"{OMNIROUTE_URL}/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 180
            },
            headers={"Authorization": f"Bearer {OMNIROUTE_KEY}"},
            timeout=12
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        # Fast automatic fallback to gemini-3-flash if primary model returns non-200
        if model != "antigravity/gemini-3-flash":
            resp2 = HTTP_SESSION.post(
                f"{OMNIROUTE_URL}/chat/completions",
                json={"model": "antigravity/gemini-3-flash", "messages": messages, "temperature": 0.3, "max_tokens": 180},
                headers={"Authorization": f"Bearer {OMNIROUTE_KEY}"},
                timeout=12
            )
            if resp2.status_code == 200:
                return resp2.json()["choices"][0]["message"]["content"].strip()
        return f"OmniRoute returned status {resp.status_code}, Master Ramos."
    except Exception as e:
        print(f"OmniRoute inference error: {e}", file=sys.stderr)
        return "I apologize, Master Ramos. The OmniRoute Antigravity neural link experienced an uplink interruption."


def query_brain(messages: List[Dict[str, str]], model: Optional[str] = None) -> str:
    """Route directly to OmniRoute Antigravity Gemini frontier neural core"""
    active_model = model or CURRENT_MODEL
    return query_omniroute(messages, active_model)


def get_second_brain_summary() -> str:
    """Collect snippets of notes in Second Brain"""
    snippets = []
    if BRAIN_DIR.exists():
        for f in BRAIN_DIR.glob("*.md"):
            try:
                content = f.read_text(encoding="utf-8")[:400]
                snippets.append(f"[{f.name}]:\n{content}")
            except Exception:
                pass
    return "\n---\n".join(snippets)


def load_calendar_events() -> List[Dict[str, Any]]:
    if CALENDAR_FILE.exists():
        try:
            return json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_calendar_events(events: List[Dict[str, Any]]):
    CALENDAR_FILE.write_text(json.dumps(events, indent=2), encoding="utf-8")


# ==========================================
# LONG-TERM & CONVERSATIONAL MEMORY HELPERS
# ==========================================
def load_memories() -> List[Dict[str, Any]]:
    if MEMORY_FILE.exists():
        try:
            return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_memories(memories: List[Dict[str, Any]]):
    MEMORY_FILE.write_text(json.dumps(memories, indent=2), encoding="utf-8")


def add_memory_fact(fact: str, category: str = "General") -> Dict[str, Any]:
    mems = load_memories()
    item = {
        "id": f"mem_{uuid.uuid4().hex[:6]}",
        "category": category,
        "fact": fact,
        "timestamp": time.strftime("%Y-%m-%d %H:%M")
    }
    mems.append(item)
    save_memories(mems)
    return item


def delete_memory_fact(mem_id: str) -> bool:
    mems = load_memories()
    orig = len(mems)
    mems = [m for m in mems if m.get("id") != mem_id]
    if len(mems) != orig:
        save_memories(mems)
        return True
    return False


def format_memories_for_prompt() -> str:
    mems = load_memories()
    if not mems:
        return "No persistent memories recorded."
    return "\n".join([f"- [{m.get('category', 'Fact')}] {m.get('fact')}" for m in mems])


def load_chat_history() -> List[Dict[str, str]]:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_chat_history(history: List[Dict[str, str]]):
    HISTORY_FILE.write_text(json.dumps(history[-20:], indent=2), encoding="utf-8")


def clear_chat_history():
    HISTORY_FILE.write_text("[]", encoding="utf-8")
    if SUMMARY_FILE.exists():
        try:
            SUMMARY_FILE.unlink()
        except Exception:
            pass


def get_running_session_summary() -> str:
    """
    Load the persistent rolling summary if it exists to maintain long-term context.
    """
    if SUMMARY_FILE.exists():
        try:
            data = json.loads(SUMMARY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("summary"):
                ts = data.get("timestamp", "")
                prefix = f"RUNNING SESSION CONTEXT ({ts})" if ts else "RUNNING SESSION CONTEXT"
                return f"{prefix}:\n{data.get('summary')}"
        except Exception:
            pass
    return ""


def execute_llm_memory_tags(reply: str) -> Tuple[str, List[Dict[str, str]]]:
    """
    Parse [MEMORY_ADD: <category> | <fact>] emitted directly by the LLM,
    store in memory.json, and strip from the spoken response.
    """
    actions = []
    pattern = re.compile(r'\[MEMORY_ADD:\s*([^|]+)\|\s*([^\]]+)\]', re.IGNORECASE)
    matches = list(pattern.finditer(reply))
    for m in matches:
        cat = m.group(1).strip().capitalize()
        fact = m.group(2).strip()
        if fact and len(fact) > 3:
            add_memory_fact(fact, category=cat)
            actions.append({"category": cat, "fact": fact})
            print(f"🧠 [Direct LLM Memory Tag]: [{cat}] {fact}")
    clean_reply = pattern.sub('', reply).strip()
    return clean_reply, actions


def get_relevant_second_brain_context(query: str) -> str:
    """
    Retrieve relevant notes or quick notes from second_brain/*.md matching query keywords.
    """
    if not BRAIN_DIR.exists():
        return ""
    words = [w.lower() for w in re.findall(r'\b[a-zA-Z0-9_-]{3,}\b', query) if w.lower() not in ("what", "when", "where", "how", "the", "and", "can", "you", "tell", "please", "puter", "computer", "hey")]
    snippets = []

    # Always check quick_notes.md for recent captures
    qn = BRAIN_DIR / "quick_notes.md"
    if qn.exists():
        try:
            lines = [l.strip() for l in qn.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
            if lines:
                snippets.append("[quick_notes.md]: " + " | ".join(lines[-4:]))
        except Exception:
            pass

    if words:
        for f in BRAIN_DIR.glob("*.md"):
            if f.name == "quick_notes.md":
                continue
            try:
                content = f.read_text(encoding="utf-8")
                matched_lines = []
                for line in content.splitlines():
                    if any(w in line.lower() for w in words):
                        matched_lines.append(line.strip())
                if matched_lines:
                    snippets.append(f"[{f.name}]: " + " | ".join(matched_lines[:3]))
            except Exception:
                pass

    return "\n".join(snippets[:4])


def update_conversation_summary_async(history: List[Dict[str, str]]):
    """
    Summarize past conversational turns into a concise situational context
    so 'Puter retains context across long sessions beyond the rolling window.
    """
    try:
        dialogue = "\n".join([f"{h['role'].upper()}: {h['content']}" for h in history[-16:]])
        prompt = f"""Summarize the key events, topics, and actions taken in this ongoing conversation in 2-3 concise sentences:
{dialogue}

Focus on: what the user is working on, commands recently executed, devices toggled, and current user focus.
Output ONLY the 2-3 sentence summary."""

        summary_text = query_brain([
            {"role": "system", "content": "You are a concise conversation summarizer."},
            {"role": "user", "content": prompt}
        ], model="antigravity/gemini-3.1-flash-lite").strip()

        if summary_text:
            SUMMARY_FILE.write_text(json.dumps({
                "summary": summary_text,
                "timestamp": time.strftime("%Y-%m-%d %H:%M")
            }, indent=2), encoding="utf-8")
            print(f"📝 [Updated Session Summary]: {summary_text[:80]}...")
    except Exception as e:
        print(f"[Summary Update Error]: {e}", file=sys.stderr)


def extract_and_learn_memories_async(user_message: str, reply: str):
    """
    Background worker that runs after each conversational turn.
    Uses OmniRoute Gemini Flash to extract persistent user facts, preferences,
    habits, ongoing projects, or corrections from the conversation turn and
    appends them to memory.json if novel.
    """
    try:
        clean_user = user_message.strip()
        if len(clean_user) < 5 or clean_user.lower() in ("hi", "hello", "hey", "bye", "goodbye", "thanks", "thank you", "ok", "okay"):
            return

        extract_prompt = f"""You are the long-term memory engine for 'Puter (the Batcomputer).
Analyze this interaction between User (Master Ramos) and 'Puter:
User: "{user_message}"
'Puter: "{reply}"

Identify if the user stated or revealed any persistent knowledge, such as:
- Personal identity, habits, schedule, relationships, or university/classes (e.g. CSE 1321).
- Work, coding, 3D printing, or project state (e.g. Anycubic Kobra, OpenGrid hooks).
- Hardware, monitor, gaming, or smart home preferences (e.g. Razer Barracuda mic, 144Hz screen, chime preferences).
- User corrections (e.g. "no, I meant X", "don't do Y", "call me Z").

Rules:
- DO NOT record trivial transient commands (e.g. "turn on the light", "play music", "what time is it").
- DO NOT duplicate existing known facts.
- Keep facts crisp, factual, and 1 sentence.
- Output ONLY a JSON array of objects: [{{"fact": "...", "category": "Identity|Preference|Project|Correction|Hardware|Habit"}}]
- If no persistent knowledge was shared, output []."""

        raw = query_brain([
            {"role": "system", "content": "You are a memory extractor. Output only valid JSON or []."},
            {"role": "user", "content": extract_prompt}
        ], model="antigravity/gemini-3.1-flash-lite")

        clean_json = raw.strip()
        if clean_json.startswith("```"):
            clean_json = re.sub(r"^```(?:json)?\s*", "", clean_json)
            clean_json = re.sub(r"\s*```$", "", clean_json)

        extracted = json.loads(clean_json)
        if not isinstance(extracted, list):
            return

        mems = load_memories()
        existing_facts_lower = [m.get("fact", "").lower() for m in mems]
        added_any = False

        for item in extracted:
            fact = (item.get("fact") or "").strip()
            cat = (item.get("category") or "General").capitalize()
            if not fact or len(fact) < 5:
                continue

            fact_lower = fact.lower()
            if any(fact_lower in ef or ef in fact_lower for ef in existing_facts_lower):
                continue

            mem_entry = {
                "id": f"mem_{uuid.uuid4().hex[:6]}",
                "category": cat,
                "fact": fact,
                "timestamp": time.strftime("%Y-%m-%d %H:%M"),
                "source": "autonomous_learning"
            }
            mems.append(mem_entry)
            existing_facts_lower.append(fact_lower)
            added_any = True
            print(f"🧠 [Auto-Learned Memory]: [{cat}] {fact}")

        if added_any:
            save_memories(mems)

        hist = load_chat_history()
        if len(hist) >= 10 and len(hist) % 4 == 0:
            update_conversation_summary_async(hist)

    except Exception as e:
        print(f"[Memory Extraction Error]: {e}", file=sys.stderr)


# ==========================================
# HOME ASSISTANT INTEGRATION & DEVICE HUB
# ==========================================
def call_hass_api(endpoint: str, method: str = "GET", data: Optional[dict] = None) -> Any:
    if not HASS_TOKEN:
        return None
    url = f"{HASS_URL.rstrip('/')}/api/{endpoint.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {HASS_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        if method.upper() == "POST":
            _HA_CACHE["ts"] = 0.0  # Invalidate state cache on any device mutation
            resp = HTTP_SESSION.post(url, json=data or {}, headers=headers, timeout=4)
        else:
            resp = HTTP_SESSION.get(url, headers=headers, timeout=4)
        if resp.status_code in (200, 201):
            return resp.json()
        return None
    except Exception as e:
        print(f"HASS API Error ({endpoint}): {e}")
        return None


def get_ha_devices_summary(use_cache: bool = True) -> Dict[str, Any]:
    now = time.time()
    if use_cache and _HA_CACHE["data"] is not None and (now - _HA_CACHE["ts"]) < 3.0:
        return _HA_CACHE["data"]
    states = call_hass_api("states") or []
    by_id = {s["entity_id"]: s for s in states}

    # Primary TV: BRAVIA XR-85X90L, with fallback to legacy living_room
    bravia = by_id.get("media_player.tv_sala_2", {})
    tv_lr = by_id.get("media_player.tv_living_room", {})

    active_tv = bravia if bravia else tv_lr
    active_attrs = active_tv.get("attributes", {})
    
    # Sonos Audio
    sonos = by_id.get("media_player.sonos_home_theater", {})
    s_attrs = sonos.get("attributes", {})

    # Thermostat & Sensors
    thermo = by_id.get("climate.jm_servers_thermostat", {})
    t_attrs = thermo.get("attributes", {})
    c_temp = t_attrs.get("current_temperature")
    sensor_temp = by_id.get("sensor.jm_servers_thermostat_temperature", {}).get("state")
    sensor_hum = by_id.get("sensor.jm_servers_thermostat_humidity", {}).get("state")
    try:
        f_temp = round(float(sensor_temp), 1) if sensor_temp not in (None, "unavailable", "unknown") else (round(c_temp * 9/5 + 32, 1) if c_temp else 72.5)
    except Exception:
        f_temp = 72.5
    try:
        humidity_val = round(float(sensor_hum), 1) if sensor_hum not in (None, "unavailable", "unknown") else t_attrs.get("current_humidity", 60)
    except Exception:
        humidity_val = t_attrs.get("current_humidity", 60)

    # Lights
    jm_light = by_id.get("light.dimmer_cuarto_jm", {})
    sala_light = by_id.get("light.luz_sala", {})
    ht_light = by_id.get("light.home_theater", {})
    mau_light = by_id.get("light.luz_cuarto_mau", {})
    papas_light = by_id.get("light.luz_papas", {})
    buro_mau_light = by_id.get("light.buro_cuarto_mau", {})
    anybuddy_light = by_id.get("light.anybuddy_display_backlight", {})

    # Fans & Switches
    mau_fan = by_id.get("switch.ventilador_mau", {})
    papas_fan = by_id.get("switch.ventilador_papas", {})
    jm_switch = by_id.get("switch.switch_cuarto_jm", {})
    ext_switch = by_id.get("switch.apagador_externo", {})
    thermo_fan = by_id.get("fan.jm_servers_thermostat", {})

    # Security: Garages & Locks
    garage_1 = by_id.get("cover.garage_1", {})
    garage_2 = by_id.get("cover.garage_2", {})
    outdoor_lock = by_id.get("lock.outdoor_lock_lock", {})

    # 3D Printer: Anycubic Kobra X
    kobra_online = by_id.get("binary_sensor.anycubic_kobra_x_printer_online", {}).get("state", "off")
    kobra_state = by_id.get("sensor.anycubic_kobra_x_job_state", {}).get("state", "idle")
    kobra_progress = by_id.get("sensor.anycubic_kobra_x_job_progress", {}).get("state", "0")
    kobra_job = by_id.get("sensor.anycubic_kobra_x_job_name", {}).get("state", "None")
    kobra_nozzle = by_id.get("sensor.anycubic_kobra_x_nozzle_temperature", {}).get("state", "0")
    kobra_bed = by_id.get("sensor.anycubic_kobra_x_hotbed_temperature", {}).get("state", "0")
    kobra_layer = by_id.get("sensor.anycubic_kobra_x_job_current_layer", {}).get("state", "0")
    kobra_total_layers = by_id.get("sensor.anycubic_kobra_x_job_total_layers", {}).get("state", "0")
    kobra_rem = by_id.get("sensor.anycubic_kobra_x_job_time_remaining", {}).get("state", "0")

    # HP Printer & Mauricio iPhone
    hp_black = by_id.get("sensor.hp_envy_4520_series_black_ink", {}).get("state", "unknown")
    hp_color = by_id.get("sensor.hp_envy_4520_series_tri_color_ink", {}).get("state", "unknown")
    mau_batt = by_id.get("sensor.iphone_mauricio_battery_level", {}).get("state", "unknown")

    summary = {
        "tv": {
            "entity_id": active_tv.get("entity_id", "media_player.tv_sala_2"),
            "name": active_attrs.get("friendly_name", "Sony BRAVIA 85\" TV"),
            "state": active_tv.get("state", "off"),
            "volume": round((active_attrs.get("volume_level") or 0.3) * 100),
            "source": active_attrs.get("source", "Sonos Arc")
        },
        "sonos": {
            "entity_id": "media_player.sonos_home_theater",
            "name": s_attrs.get("friendly_name", "Sonos Home Theater"),
            "state": sonos.get("state", "off"),
            "volume": round((s_attrs.get("volume_level") or 0.3) * 100),
            "night_sound": by_id.get("switch.sonos_home_theater_night_sound", {}).get("state", "off"),
            "speech_enhancement": by_id.get("switch.sonos_home_theater_speech_enhancement", {}).get("state", "off")
        },
        "thermostat": {
            "entity_id": "climate.jm_servers_thermostat",
            "name": t_attrs.get("friendly_name", "Home Thermostat"),
            "temp_f": f_temp,
            "temp_c": c_temp,
            "humidity": humidity_val,
            "mode": thermo.get("state", "heat_cool"),
            "action": t_attrs.get("hvac_action", "cooling"),
            "fan_state": thermo_fan.get("state", "off")
        },
        "lights": [
            {"entity_id": "light.dimmer_cuarto_jm", "name": "JM Bedroom Dimmer", "state": jm_light.get("state", "off"), "brightness": jm_light.get("attributes", {}).get("brightness")},
            {"entity_id": "light.luz_sala", "name": "Living Room Light (Sala)", "state": sala_light.get("state", "off")},
            {"entity_id": "light.home_theater", "name": "Home Theater Light", "state": ht_light.get("state", "off")},
            {"entity_id": "light.luz_cuarto_mau", "name": "Mau Room Light", "state": mau_light.get("state", "off")},
            {"entity_id": "light.buro_cuarto_mau", "name": "Mau Nightstand (Buró)", "state": buro_mau_light.get("state", "off")},
            {"entity_id": "light.luz_papas", "name": "Parents Room Light (Papás)", "state": papas_light.get("state", "off")},
            {"entity_id": "light.anybuddy_display_backlight", "name": "AnyBuddy Display Backlight", "state": anybuddy_light.get("state", "off")}
        ],
        "switches": [
            {"entity_id": "switch.ventilador_mau", "name": "Mau Fan", "state": mau_fan.get("state", "off")},
            {"entity_id": "switch.ventilador_papas", "name": "Parents Fan", "state": papas_fan.get("state", "off")},
            {"entity_id": "switch.switch_cuarto_jm", "name": "JM Room Switch", "state": jm_switch.get("state", "off")},
            {"entity_id": "switch.apagador_externo", "name": "External Switch (Apagador Externo)", "state": ext_switch.get("state", "off")}
        ],
        "security": {
            "garage_1": {"entity_id": "cover.garage_1", "name": "Garage Door 1", "state": garage_1.get("state", "closed")},
            "garage_2": {"entity_id": "cover.garage_2", "name": "Garage Door 2", "state": garage_2.get("state", "closed")},
            "outdoor_lock": {"entity_id": "lock.outdoor_lock_lock", "name": "Outdoor Lock", "state": outdoor_lock.get("state", "locked")}
        },
        "printer_3d": {
            "name": "Anycubic Kobra X",
            "online": kobra_online == "on",
            "state": kobra_state,
            "progress_pct": kobra_progress,
            "job_name": kobra_job,
            "layer": f"{kobra_layer}/{kobra_total_layers}",
            "time_remaining_min": kobra_rem,
            "nozzle_temp_c": kobra_nozzle,
            "bed_temp_c": kobra_bed,
            "pause_entity": "button.anycubic_kobra_x_pause_print",
            "resume_entity": "button.anycubic_kobra_x_resume_print",
            "cancel_entity": "button.anycubic_kobra_x_cancel_print"
        },
        "diagnostics": {
            "hp_printer_black_ink_pct": hp_black,
            "hp_printer_color_ink_pct": hp_color,
            "mauricio_iphone_battery_pct": mau_batt
        }
    }
    _HA_CACHE["data"] = summary
    _HA_CACHE["ts"] = now
    return summary


def format_ha_context_for_prompt(ha_summary: Optional[Dict[str, Any]] = None) -> str:
    """Compact live Home Assistant state + entity IDs for sub-second LLM processing."""
    d = ha_summary or get_ha_devices_summary()
    tv = d.get("tv", {})
    sonos = d.get("sonos", {})
    th = d.get("thermostat", {})
    sec = d.get("security", {})
    p3d = d.get("printer_3d", {})
    diag = d.get("diagnostics", {})

    lights_str = ", ".join([f"{l['entity_id']}({l['name']})={l['state']}" for l in d.get("lights", [])])
    switches_str = ", ".join([f"{s['entity_id']}({s['name']})={s['state']}" for s in d.get("switches", [])])

    return (
        f"TV: {tv.get('entity_id')}={tv.get('state')} vol={tv.get('volume')}% src={tv.get('source')} | "
        f"Sonos: media_player.sonos_home_theater={sonos.get('state')} vol={sonos.get('volume')}% night={sonos.get('night_sound')}(switch.sonos_home_theater_night_sound)\n"
        f"Climate: climate.jm_servers_thermostat={th.get('temp_f')}F hum={th.get('humidity')}% action={th.get('action')} fan={th.get('fan_state')}(fan.jm_servers_thermostat)\n"
        f"Lights: {lights_str}\n"
        f"Switches/Fans: {switches_str}\n"
        f"Security: cover.garage_1={sec.get('garage_1', {}).get('state')}, cover.garage_2={sec.get('garage_2', {}).get('state')}, lock.outdoor_lock_lock={sec.get('outdoor_lock', {}).get('state')}\n"
        f"3DPrinter(Anycubic Kobra X): online={p3d.get('online')} state={p3d.get('state')} progress={p3d.get('progress_pct')}% layer={p3d.get('layer')} rem={p3d.get('time_remaining_min')}m job='{p3d.get('job_name')}' nozzle={p3d.get('nozzle_temp_c')}C bed={p3d.get('bed_temp_c')}C (buttons: button.anycubic_kobra_x_pause_print, button.anycubic_kobra_x_resume_print, button.anycubic_kobra_x_cancel_print)\n"
        f"Misc: HP_Ink_Black={diag.get('hp_printer_black_ink_pct')}% Color={diag.get('hp_printer_color_ink_pct')}% | Mau_iPhone_Batt={diag.get('mauricio_iphone_battery_pct')}%"
    )


def execute_llm_ha_tags(raw_reply: str) -> tuple[str, List[Dict[str, Any]]]:
    """Parse and execute any [HA_CALL: domain.service | entity_id | optional_json] tags emitted by the LLM."""
    executed_actions = []
    pattern = re.compile(r'\[HA_CALL:\s*([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\s*\|\s*([a-zA-Z0-9_.,\s]+?)(?:\s*\|\s*(\{.*?\}))?\s*\]')
    
    for match in pattern.finditer(raw_reply):
        domain, service, entities_raw, extra_raw = match.groups()
        extra = {}
        if extra_raw:
            try:
                extra = json.loads(extra_raw)
            except Exception:
                extra = {}
        for ent in [e.strip() for e in entities_raw.split(",") if e.strip()]:
            res = call_hass_device_control(domain.strip(), service.strip(), ent, extra)
            executed_actions.append(res)

    clean_reply = pattern.sub("", raw_reply).strip()
    return clean_reply, executed_actions


def call_hass_device_control(domain: str, service: str, entity_id: str, extra: Optional[dict] = None) -> dict:
    if not HASS_TOKEN:
        return {"success": False, "error": "HASS_TOKEN not configured"}
    payload = {"entity_id": entity_id}
    if extra:
        payload.update(extra)
    res = call_hass_api(f"services/{domain}/{service}", method="POST", data=payload)
    return {"success": res is not None, "domain": domain, "service": service, "entity_id": entity_id}


def call_hass_tv(action: str) -> Dict[str, Any]:
    """Control Sony BRAVIA XR-85X90L TV or fallback"""
    entity_id = "media_player.tv_sala_2"
    if action == "status":
        state_data = call_hass_api(f"states/{entity_id}")
        if state_data:
            return {"success": True, "state": state_data.get("state"), "friendly_name": state_data.get("attributes", {}).get("friendly_name", "Sony BRAVIA 85\" TV")}
        # fallback
        state_data = call_hass_api("states/media_player.tv_living_room")
        if state_data:
            return {"success": True, "state": state_data.get("state"), "friendly_name": state_data.get("attributes", {}).get("friendly_name", "Living Room TV")}
        return {"success": False, "error": "Unable to reach Home Assistant TV"}

    service = "turn_on" if action == "turn_on" else ("turn_off" if action == "turn_off" else "toggle")
    res = call_hass_device_control("media_player", service, entity_id)
    # Also forward to tv_living_room for sync
    call_hass_device_control("media_player", service, "media_player.tv_living_room")
    return res


# ==========================================
# PERSONAL CALENDAR & DUAL ICAL SYNC ENGINE (ICLOUD & GOOGLE CALENDAR)
# ==========================================
def parse_ics_text(ics_text: str, source_label: str = "Personal") -> List[Dict[str, Any]]:
    events = []
    vevents = re.findall(r'BEGIN:VEVENT(.*?)END:VEVENT', ics_text, re.DOTALL)
    for v in vevents:
        summary_m = re.search(r'SUMMARY:(.*)', v)
        summary = summary_m.group(1).strip() if summary_m else 'Calendar Event'
        summary = summary.replace(r'\,', ',').replace(r'\;', ';').replace(r'\n', ' ').strip()
        
        start_m = re.search(r'DTSTART(?:;[^:]+)?:(\d+T\d+Z?|\d+)', v)
        start_raw = start_m.group(1).strip() if start_m else ''
        
        loc_m = re.search(r'LOCATION:(.*)', v)
        loc = loc_m.group(1).strip() if loc_m else ''
        loc = loc.replace(r'\,', ',').replace(r'\;', ';').replace(r'\n', ' ').strip()
        
        time_str = 'All Day'
        date_str = 'Upcoming'
        sort_key = '9999-99-99 99:99'
        if 'T' in start_raw:
            try:
                from datetime import datetime
                dt = datetime.strptime(start_raw[:15], '%Y%m%dT%H%M%S')
                time_str = dt.strftime('%I:%M %p')
                date_str = dt.strftime('%Y-%m-%d')
                sort_key = dt.strftime('%Y-%m-%d %H:%M')
            except Exception:
                time_str = start_raw
        elif len(start_raw) == 8:
            try:
                from datetime import datetime
                dt = datetime.strptime(start_raw, '%Y%m%d')
                date_str = dt.strftime('%Y-%m-%d')
                sort_key = dt.strftime('%Y-%m-%d 00:00')
            except Exception:
                date_str = start_raw

        events.append({
            'id': f'cal_{uuid.uuid4().hex[:6]}',
            'title': summary,
            'time': time_str,
            'date': date_str,
            'location': loc or 'Virtual / No Location',
            'priority': source_label,
            'source': source_label,
            'status': 'CONFIRMED',
            '_sort_key': sort_key
        })
    return events


def load_calendar_config() -> dict:
    if CALENDAR_CONFIG_FILE.exists():
        try:
            return json.loads(CALENDAR_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "icloud_url": "",
        "google_url": "",
        "last_synced": None,
        "sources": []
    }


def save_calendar_config(cfg: dict):
    CALENDAR_CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def fetch_ics_content(url_or_path: str) -> Tuple[str, Optional[str]]:
    target = url_or_path.strip()
    if not target:
        return "", None
    if target.startswith("http://") or target.startswith("https://") or target.startswith("webcal://"):
        fetch_url = target.replace("webcal://", "https://")
        req = urllib.request.Request(fetch_url, headers={"User-Agent": "Mozilla/5.0 (Batcomputer-Alfred)"})
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                return resp.read().decode("utf-8", errors="ignore"), None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "", "404 Not Found (For Google Calendar, copy 'Secret address in iCal format' from settings)"
            return "", f"HTTP {e.code}: {e.reason}"
        except Exception as e:
            print(f"Error fetching remote iCal {target}: {e}")
            return "", str(e)
    else:
        p = Path(target)
        if p.exists():
            return p.read_text(encoding="utf-8", errors="ignore"), None
    return "", "File not found"


def sync_calendar(icloud_url: Optional[str] = None, google_url: Optional[str] = None, url: Optional[str] = None) -> List[Dict[str, Any]]:
    cfg = load_calendar_config()
    
    # Update URLs if passed
    if icloud_url is not None:
        cfg["icloud_url"] = icloud_url.strip()
    if google_url is not None:
        cfg["google_url"] = google_url.strip()
    if url is not None:
        u = url.strip()
        if "google.com" in u:
            cfg["google_url"] = u
        elif "icloud.com" in u:
            cfg["icloud_url"] = u
        else:
            cfg["ical_url"] = u

    all_events = []
    active_sources = []
    errors = []

    # 1. Fetch iCloud Calendar
    ic_target = cfg.get("icloud_url", "")
    if ic_target:
        txt, err = fetch_ics_content(ic_target)
        if txt:
            ic_events = parse_ics_text(txt, source_label="iCloud")
            all_events.extend(ic_events)
            active_sources.append(f"iCloud ({len(ic_events)} events)")
        elif err:
            active_sources.append(f"iCloud (Error: {err})")
            errors.append(f"iCloud: {err}")

    # 2. Fetch Google Calendar
    g_target = cfg.get("google_url", "")
    if g_target:
        txt, err = fetch_ics_content(g_target)
        if txt:
            g_events = parse_ics_text(txt, source_label="Google")
            all_events.extend(g_events)
            active_sources.append(f"Google ({len(g_events)} events)")
        elif err:
            active_sources.append(f"Google (Error: {err})")
            errors.append(f"Google: {err}")

    # 3. Fallback generic iCal URL if neither above matched
    legacy_url = cfg.get("ical_url", "")
    if not ic_target and not g_target and legacy_url:
        txt, err = fetch_ics_content(legacy_url)
        if txt:
            gen_events = parse_ics_text(txt, source_label="Calendar")
            all_events.extend(gen_events)
            active_sources.append(f"Calendar ({len(gen_events)} events)")
        elif err:
            errors.append(f"Calendar: {err}")

    # Deduplicate by (title.lower(), date, time)
    deduped = []
    seen = set()
    for e in all_events:
        key = (e['title'].lower(), e['date'], e['time'])
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    # Sort chronologically by sort key
    deduped.sort(key=lambda x: x.get('_sort_key', '9999-99-99 99:99'))
    for e in deduped:
        e.pop('_sort_key', None)

    save_calendar_events(deduped)
    cfg["last_synced"] = time.strftime("%Y-%m-%d %I:%M %p")
    cfg["sources"] = active_sources
    cfg["errors"] = errors
    cfg["count"] = len(deduped)
    save_calendar_config(cfg)
    return deduped


# ==========================================
# TOOLS & INTEGRATIONS HUB (BESZEL, MAIL, CALENDAR, GITHUB, HA)
# ==========================================
DEFAULT_TOOLS_CONFIG = {
    "home_assistant": {
        "name": "Home Assistant Core",
        "enabled": True,
        "url": HASS_URL,
        "description": "Smart lights, Sony BRAVIA 85\" TV, Sonos Arc, Thermostat, Anycubic Kobra X 3D Printer & Locks"
    },
    "beszel": {
        "name": "Beszel Fleet Telemetry",
        "enabled": True,
        "url": "http://zimaos:8090",
        "ssh_host": "jmrd@zimaos",
        "description": "Real-time CPU, RAM, Disk & Docker container metrics across ZimaOS and OptiPlex nodes"
    },
    "calendar": {
        "name": "Tactical Calendar (iCloud & Google)",
        "enabled": True,
        "icloud_url": "",
        "google_url": "",
        "description": "Live sync across iCloud Calendar and Google Calendar feeds"
    },
    "mail": {
        "name": "Wayne Mail (IMAP Inbox)",
        "enabled": False,
        "imap_host": "imap.gmail.com",
        "email": "juanmanuelrd1017@gmail.com",
        "app_password": "",
        "description": "Unread priority inbox headers and sender triage via IMAP SSL"
    },
    "github": {
        "name": "GitHub Repo Teardown Engine",
        "enabled": True,
        "default_repo": "Juanmanuelrd17/alfred-batcomputer",
        "token": "",
        "description": "Live GitHub repository tree, language breakdown, README & source code architecture analyzer"
    },
    "hermes": {
        "name": "Alfred // Hermes Agent (Heavy Workloads)",
        "enabled": True,
        "bin_path": "/Users/jmrd/.local/bin/hermes",
        "model": "antigravity/gemini-3.1-flash-lite",
        "description": "Autonomous Hermes Agent (Alfred) with terminal, SSH, file editing, cron, and multi-step engineering tools"
    }
}
_BESZEL_CACHE: Dict[str, Any] = {"data": None, "ts": 0.0}
_MAIL_CACHE: Dict[str, Any] = {"data": None, "ts": 0.0}
_HERMES_TASKS: List[Dict[str, Any]] = []


def invoke_hermes_agent(task_prompt: str, model: Optional[str] = None) -> Dict[str, Any]:
    """Delegate a heavy / multi-step workload to Alfred (the Hermes Agent on Mac Mini)."""
    t0 = time.perf_counter()
    cfg = load_tools_config().get("hermes", {})
    if not cfg.get("enabled", True):
        return {"success": False, "output": "Alfred (Hermes Agent) is currently disabled in Tools config.", "session_id": None, "latency_ms": 0}

    hermes_bin = cfg.get("bin_path") or "/Users/jmrd/.local/bin/hermes"
    active_model = model or cfg.get("model") or "antigravity/gemini-3.1-flash-lite"
    try:
        res = subprocess.run(
            [
                hermes_bin, "chat", "-Q", "--oneshot",
                "-m", active_model,
                "-q", f"{task_prompt} (Keep your final summary concise, under 35 words, suitable for voice readout to Master Ramos.)"
            ],
            capture_output=True, text=True, timeout=45
        )
        raw_out = (res.stdout or "") + ("\n" + res.stderr if not res.stdout and res.stderr else "")
        lines = []
        session_id = None
        for line in raw_out.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("Antigravity (agy CLI):") or s.startswith("Warning: Unknown toolsets:"):
                continue
            if s.startswith("session_id:"):
                session_id = s.split(":", 1)[1].strip()
                continue
            lines.append(s)

        clean_output = " ".join(lines).strip() or "Alfred completed the Hermes workload."
        elapsed = round((time.perf_counter() - t0) * 1000)
        record = {
            "id": session_id or f"hermes_{uuid.uuid4().hex[:6]}",
            "task": task_prompt[:120],
            "output": clean_output,
            "latency_ms": elapsed,
            "timestamp": time.strftime("%I:%M:%S %p")
        }
        _HERMES_TASKS.insert(0, record)
        del _HERMES_TASKS[10:]
        return {"success": True, "output": clean_output, "session_id": session_id, "latency_ms": elapsed, "record": record}
    except Exception as e:
        elapsed = round((time.perf_counter() - t0) * 1000)
        return {"success": False, "output": f"Hermes Agent execution error: {str(e)[:120]}", "session_id": None, "latency_ms": elapsed}


def execute_llm_hermes_tags(reply_text: str) -> tuple[str, List[Dict[str, Any]]]:
    """Parse and execute [HERMES_CALL: <task>] tags emitted by 'Puter when it decides a workload requires Alfred (Hermes Agent)."""
    pattern = r'\[HERMES_CALL:\s*(.*?)\]'
    matches = re.findall(pattern, reply_text, flags=re.IGNORECASE | re.DOTALL)
    clean_reply = re.sub(pattern, '', reply_text, flags=re.IGNORECASE | re.DOTALL).strip()
    hermes_results = []
    for task_str in matches:
        task_clean = task_str.strip()
        if task_clean:
            h_res = invoke_hermes_agent(task_clean)
            hermes_results.append(h_res)
            if h_res.get("output"):
                clean_reply = f"{clean_reply} [Alfred // Hermes]: {h_res['output']}".strip()
    return clean_reply, hermes_results


def load_tools_config() -> Dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_TOOLS_CONFIG))
    if TOOLS_CONFIG_FILE.exists():
        try:
            saved = json.loads(TOOLS_CONFIG_FILE.read_text(encoding="utf-8"))
            for k, v in saved.items():
                if k in cfg and isinstance(v, dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception:
            pass
    return cfg


def save_tools_config(cfg: Dict[str, Any]):
    TOOLS_CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def get_beszel_telemetry(use_cache: bool = True) -> List[Dict[str, Any]]:
    now = time.time()
    if use_cache and _BESZEL_CACHE["data"] is not None and (now - _BESZEL_CACHE["ts"]) < 10.0:
        return _BESZEL_CACHE["data"]

    cfg = load_tools_config().get("beszel", {})
    if not cfg.get("enabled", True):
        return []

    systems = []
    try:
        # Query Beszel SQLite database on ZimaOS directly over SSH key (zero password needed)
        res = subprocess.run(
            [
                "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", "-o", "StrictHostKeyChecking=no",
                cfg.get("ssh_host", "jmrd@zimaos"),
                'sqlite3 /DATA/AppData/big-bear-beszel/data/data.db "SELECT name, host, status, info FROM systems;"'
            ],
            capture_output=True, text=True, timeout=4
        )
        if res.returncode == 0 and res.stdout.strip():
            for line in res.stdout.strip().splitlines():
                parts = line.split("|", 3)
                if len(parts) == 4:
                    name, host, status, info_raw = parts
                    info = json.loads(info_raw) if info_raw else {}
                    systems.append({
                        "name": name,
                        "host": host,
                        "status": status,
                        "cpu_pct": info.get("cpu", 0),
                        "mem_pct": info.get("mp", 0),
                        "disk_pct": info.get("dp", 0),
                        "temp_c": info.get("dt", 0),
                        "load_avg": info.get("la", []),
                        "version": info.get("v", "")
                    })
    except Exception as e:
        print(f"Beszel telemetry fetch warning: {e}")

    _BESZEL_CACHE["data"] = systems
    _BESZEL_CACHE["ts"] = now
    return systems


def get_mail_summary(use_cache: bool = True) -> Dict[str, Any]:
    now = time.time()
    if use_cache and _MAIL_CACHE["data"] is not None and (now - _MAIL_CACHE["ts"]) < 30.0:
        return _MAIL_CACHE["data"]

    cfg = load_tools_config().get("mail", {})
    if not cfg.get("enabled") or not cfg.get("email") or not cfg.get("app_password"):
        return {"connected": False, "unread_count": 0, "messages": [], "status": "Not configured (Add IMAP App Password in TOOLS tab)"}

    try:
        mail = imaplib.IMAP4_SSL(cfg.get("imap_host", "imap.gmail.com"), timeout=5)
        mail.login(cfg["email"], cfg["app_password"])
        mail.select("inbox", readonly=True)
        _, unseen_data = mail.search(None, "UNSEEN")
        unseen_ids = unseen_data[0].split() if unseen_data and unseen_data[0] else []
        _, all_data = mail.search(None, "ALL")
        all_ids = all_data[0].split() if all_data and all_data[0] else []
        recent_ids = all_ids[-5:][::-1]

        messages = []
        for mid in recent_ids:
            _, msg_data = mail.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
            if msg_data and msg_data[0]:
                msg = email.message_from_bytes(msg_data[0][1])
                subj_raw = decode_header(msg.get("Subject", "No Subject"))[0]
                subj = subj_raw[0].decode(subj_raw[1] or "utf-8", errors="ignore") if isinstance(subj_raw[0], bytes) else str(subj_raw[0])
                sender_raw = decode_header(msg.get("From", "Unknown"))[0]
                sender = sender_raw[0].decode(sender_raw[1] or "utf-8", errors="ignore") if isinstance(sender_raw[0], bytes) else str(sender_raw[0])
                messages.append({"subject": subj[:80], "from": sender[:60], "date": msg.get("Date", "")[:25]})
        mail.logout()
        result = {"connected": True, "unread_count": len(unseen_ids), "messages": messages, "status": "ONLINE"}
        _MAIL_CACHE["data"] = result
        _MAIL_CACHE["ts"] = now
        return result
    except Exception as e:
        return {"connected": False, "unread_count": 0, "messages": [], "status": f"IMAP Error: {str(e)[:80]}"}


_WEATHER_CACHE: Dict[str, Any] = {"data": None, "ts": 0.0}


def get_live_weather(use_cache: bool = True) -> Dict[str, Any]:
    """Fetch live outdoor weather (Open-Meteo for Georgia / local coordinates with 10-min cache)"""
    now = time.time()
    if use_cache and _WEATHER_CACHE["data"] is not None and (now - _WEATHER_CACHE["ts"]) < 600.0:
        return _WEATHER_CACHE["data"]
    try:
        # Cumming / Atlanta, GA coordinates (34.2073, -84.1402)
        r = HTTP_SESSION.get(
            "https://api.open-meteo.com/v1/forecast?latitude=34.2073&longitude=-84.1402&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m&temperature_unit=fahrenheit&wind_speed_unit=mph",
            timeout=3.5
        )
        if r.status_code == 200:
            cur = r.json().get("current", {})
            wcode = cur.get("weather_code", 0)
            cond_map = {
                0: "Clear skies", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
                45: "Foggy", 51: "Light drizzle", 61: "Light rain", 63: "Moderate rain",
                65: "Heavy rain", 71: "Snow", 80: "Rain showers", 95: "Thunderstorm"
            }
            result = {
                "temp_f": cur.get("temperature_2m", 70),
                "feels_like_f": cur.get("apparent_temperature", 70),
                "humidity": cur.get("relative_humidity_2m", 55),
                "wind_mph": cur.get("wind_speed_10m", 5),
                "condition": cond_map.get(wcode, "Clear"),
                "location": "Cumming / Atlanta, GA"
            }
            _WEATHER_CACHE["data"] = result
            _WEATHER_CACHE["ts"] = now
            return result
    except Exception as e:
        print(f"Weather fetch warning: {e}")
    return _WEATHER_CACHE["data"] or {"temp_f": 72, "condition": "Clear", "humidity": 55, "wind_mph": 4, "location": "Georgia"}


def search_live_web(query: str) -> List[Dict[str, str]]:
    """Search live web via DuckDuckGo HTML & Wikipedia API for up-to-the-minute facts, news, and lookups."""
    results: List[Dict[str, str]] = []
    try:
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        resp = HTTP_SESSION.get(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}, timeout=4.5)
        if resp.status_code == 200:
            snippets = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', resp.text, re.I | re.S)
            titles = re.findall(r'<a rel="nofollow" class="result__a"[^>]*>(.*?)</a>', resp.text, re.I | re.S)
            for i in range(min(4, len(snippets))):
                clean_snip = re.sub(r'<[^>]+>', '', snippets[i]).strip()
                clean_title = re.sub(r'<[^>]+>', '', titles[i]).strip() if i < len(titles) else "Web Result"
                results.append({"title": clean_title, "snippet": clean_snip})
    except Exception as e:
        print(f"Web search warning: {e}")
    return results


def analyze_vision_frame(
    image_b64: Optional[str] = None,
    prompt: str = "What am I looking at? Describe it concisely as Alfred.",
    client_host: Optional[str] = None,
    target: str = "auto"
) -> Dict[str, Any]:
    """Analyze a client screen capture, server screen capture, webcam frame, or uploaded image using multimodal Gemini."""
    import base64
    t0 = time.perf_counter()

    source_type = "webcam_or_upload"
    captured_host = client_host or "client"

    if image_b64:
        source_type = f"client_screen ({client_host})" if client_host else "client_screen"
    else:
        # If no client image provided, capture server host screen (Mac Mini M1 or host machine)
        tmp_shot = AUDIO_CACHE / f"screen_{uuid.uuid4().hex[:6]}.png"
        try:
            if sys.platform == "darwin":
                subprocess.run(["/usr/sbin/screencapture", "-x", "-t", "png", str(tmp_shot)], timeout=3, check=True)
            else:
                for sc_cmd in [["grim", str(tmp_shot)], ["gnome-screenshot", "-f", str(tmp_shot)], ["import", "-window", "root", str(tmp_shot)]]:
                    if shutil.which(sc_cmd[0]):
                        subprocess.run(sc_cmd, timeout=3, check=True)
                        break
            if tmp_shot.exists():
                image_b64 = base64.b64encode(tmp_shot.read_bytes()).decode("utf-8")
                source_type = f"server_screen ({socket.gethostname()})"
                captured_host = socket.gethostname()
                tmp_shot.unlink(missing_ok=True)
        except Exception as e:
            print(f"Server screen capture fallback warning: {e}")
            tmp_shot.unlink(missing_ok=True)

    if not image_b64:
        return {"error": "No camera frame or screen capture available.", "reply": "I do not have an active visual feed on the channel, Master Ramos.", "source": "none", "captured_host": captured_host}

    if "," in image_b64[:40]:
        image_b64 = image_b64.split(",", 1)[1]

    vision_messages = [
        {
            "role": "system",
            "content": f"You are Alfred Pennyworth, loyal British butler to Master Ramos (Master Juan / Master Remmy). You are currently inspecting the visual feed from [{source_type}]. Analyze what you see in 1-2 sharp, helpful sentences (<30 words) with refined British wit."
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
            ]
        }
    ]

    reply = ""
    for v_model in ["antigravity/gemini-3-flash", "antigravity/gemini-3.1-flash-lite"]:
        try:
            r = HTTP_SESSION.post(
                f"{OMNIROUTE_URL}/chat/completions",
                json={"model": v_model, "messages": vision_messages, "temperature": 0.3, "max_tokens": 180},
                headers={"Authorization": f"Bearer {OMNIROUTE_KEY}"},
                timeout=12
            )
            if r.status_code == 200:
                reply = r.json()["choices"][0]["message"]["content"].strip()
                break
        except Exception as e:
            print(f"Vision model {v_model} error: {e}")

    if not reply:
        reply = "Visual feed received, Master Ramos, though the multimodal neural link returned an empty analysis."

    return {
        "reply": reply,
        "source": source_type,
        "captured_host": captured_host,
        "is_server_screen": "server_screen" in source_type,
        "latency_ms": round((time.perf_counter() - t0) * 1000)
    }


# Reverse Engineering & GitHub Repo Teardown Tool
def reverse_engineer_target(target: str) -> Dict[str, Any]:
    """Inspect and tear down either a real GitHub Repository (owner/repo or github.com URL) OR a live website."""
    raw = target.strip()
    gh_match = re.match(r'^(?:https?://github\.com/)?([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git|/|$)', raw)
    if gh_match and ("github.com" in raw.lower() or "/" in raw and not raw.startswith("http")):
        repo_slug = gh_match.group(1)
        gh_cfg = load_tools_config().get("github", {})
        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "Batcomputer-Alfred-HUD"}
        if gh_cfg.get("token"):
            headers["Authorization"] = f"token {gh_cfg['token']}"

        try:
            meta_r = HTTP_SESSION.get(f"https://api.github.com/repos/{repo_slug}", headers=headers, timeout=8)
            if meta_r.status_code != 200:
                return {"error": f"GitHub API returned {meta_r.status_code} for {repo_slug}"}
            meta = meta_r.json()
            default_branch = meta.get("default_branch", "main")

            langs_r = HTTP_SESSION.get(f"https://api.github.com/repos/{repo_slug}/languages", headers=headers, timeout=6)
            langs = list(langs_r.json().keys()) if langs_r.status_code == 200 else []

            tree_r = HTTP_SESSION.get(f"https://api.github.com/repos/{repo_slug}/git/trees/{default_branch}?recursive=1", headers=headers, timeout=8)
            tree_items = tree_r.json().get("tree", []) if tree_r.status_code == 200 else []
            file_paths = [item["path"] for item in tree_items if item.get("type") == "blob"][:60]

            commits_r = HTTP_SESSION.get(f"https://api.github.com/repos/{repo_slug}/commits?per_page=5", headers=headers, timeout=6)
            commits = [
                f"{c['sha'][:7]}: {c['commit']['message'].splitlines()[0]}"
                for c in (commits_r.json() if commits_r.status_code == 200 and isinstance(commits_r.json(), list) else [])
            ]

            readme_text = ""
            readme_r = HTTP_SESSION.get(f"https://raw.githubusercontent.com/{repo_slug}/{default_branch}/README.md", timeout=6)
            if readme_r.status_code == 200:
                readme_text = readme_r.text[:1500]

            # Fetch key entrypoint snippet if available
            code_sample = ""
            for entry_candidate in ["server.py", "main.py", "app.py", "index.js", "src/index.ts", "package.json"]:
                if entry_candidate in file_paths:
                    cr = HTTP_SESSION.get(f"https://raw.githubusercontent.com/{repo_slug}/{default_branch}/{entry_candidate}", timeout=5)
                    if cr.status_code == 200:
                        code_sample = f"[{entry_candidate}]:\n" + cr.text[:1200]
                        break

            prompt = f"""You are Alfred's tactical software architecture teardown module.
Analyze this REAL GitHub Repository:
- Repository: {repo_slug} ({meta.get('html_url')})
- Description: {meta.get('description') or 'N/A'}
- Stars / Forks / Branch: ⭐ {meta.get('stargazers_count', 0)} | 🍴 {meta.get('forks_count', 0)} | branch: {default_branch}
- Languages: {', '.join(langs) if langs else 'Unknown'}
- File Tree ({len(file_paths)} files): {', '.join(file_paths[:40])}
- Recent Commits: {'; '.join(commits)}
- README Excerpt:
{readme_text[:800]}
- Entrypoint Code Sample:
{code_sample[:800]}

Provide a crisp 4-section tactical engineering teardown:
1. Core Architecture & Language Stack
2. Key Modules & File Tree Breakdown
3. Data Flow & Runtime Topology
4. High-Impact Next Upgrades & Security Recommendations"""

            teardown_report = query_brain([
                {"role": "system", "content": "You are Alfred's tactical repository reverse-engineering module."},
                {"role": "user", "content": prompt}
            ])

            return {
                "mode": "github_repo",
                "type": "github_repo",
                "repo": repo_slug,
                "url": meta.get("html_url", f"https://github.com/{repo_slug}"),
                "title": f"{repo_slug} ({meta.get('description') or 'GitHub Repository'})",
                "languages": langs or ["Python", "JavaScript", "HTML"],
                "tech_stack": langs or ["GitHub Repository"],
                "tree_sample": file_paths,
                "files": file_paths,
                "recent_commits": commits,
                "stars": meta.get("stargazers_count", 0),
                "forks": meta.get("forks_count", 0),
                "report": teardown_report
            }
        except Exception as e:
            return {"error": f"GitHub repo teardown failed: {str(e)}"}

    return reverse_engineer_website(raw)


def reverse_engineer_website(url: str) -> Dict[str, Any]:
    """Inspect and tear down website tech stack and structure"""
    if not url.startswith("http"):
        url = "https://" + url
    
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    req = urllib.request.Request(url, headers=headers)
    
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            resp_headers = dict(resp.getheaders())
    except Exception as e:
        return {"error": f"Failed to retrieve target URL: {str(e)}"}
    
    # Detect frontend tech
    tech_stack = []
    if "react" in html.lower() or "_next" in html or "__next" in html:
        tech_stack.append("React / Next.js")
    if "vue" in html.lower() or "__nuxt" in html:
        tech_stack.append("Vue.js / Nuxt")
    if "tailwind" in html.lower() or "tw-" in html:
        tech_stack.append("Tailwind CSS")
    if "bootstrap" in html.lower():
        tech_stack.append("Bootstrap")
    if "wp-content" in html or "wordpress" in html.lower():
        tech_stack.append("WordPress")
    if "shopify" in html.lower():
        tech_stack.append("Shopify")
    if "cloudflare" in str(resp_headers).lower():
        tech_stack.append("Cloudflare CDN/Edge")
        
    title_m = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    title = title_m.group(1).strip() if title_m else "Unknown Title"
    
    # Color extraction (hex codes)
    hex_colors = list(set(re.findall(r'#(?:[0-9a-fA-F]{3}){1,2}\b', html)))[:8]
    
    # Meta tags
    meta_desc = ""
    desc_m = re.search(r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']', html, re.I)
    if desc_m:
        meta_desc = desc_m.group(1).strip()
        
    # Analyze with Ollama
    prompt = f"""You are a master software architect and reverse engineer.
Target Website: {url}
Page Title: {title}
Detected Technologies: {', '.join(tech_stack) if tech_stack else 'Custom Vanilla HTML/CSS/JS'}
Extracted Color Palette: {', '.join(hex_colors)}
Server Headers: {resp_headers.get('Server', 'Standard Reverse Proxy')}
Meta Description: {meta_desc}

Provide a concise, 4-section tactical architectural teardown:
1. Tech Stack & Frontend Frameworks
2. Design System & Layout Architecture (Colors, Typography, Layout grids)
3. Key Components & Features
4. Step-by-Step Reconstruction Blueprint
Keep the tone professional, direct, and actionable."""

    teardown_report = query_brain([
        {"role": "system", "content": "You are Alfred's tactical engineering analysis module."},
        {"role": "user", "content": prompt}
    ])
    
    return {
        "url": url,
        "title": title,
        "tech_stack": tech_stack if tech_stack else ["Custom HTML5 / CSS3 / JavaScript"],
        "colors": hex_colors,
        "meta_description": meta_desc,
        "report": teardown_report
    }


# ==========================================
# REST API ENDPOINTS
# ==========================================

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_index(request: Request):
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(
            content=index_file.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
        )
    return HTMLResponse("<h1>Batcomputer HUD Initializing...</h1>")


@app.get("/manifest.json")
async def serve_manifest():
    manifest_file = STATIC_DIR / "manifest.json"
    if manifest_file.exists():
        return FileResponse(str(manifest_file), media_type="application/manifest+json")
    return JSONResponse({})


@app.get("/sw.js")
async def serve_service_worker():
    sw_file = STATIC_DIR / "sw.js"
    if sw_file.exists():
        return FileResponse(str(sw_file), media_type="application/javascript")
    return HTMLResponse("", media_type="application/javascript")


# ==========================================
# AUTHENTICATION & ACCESS CONTROL
# ==========================================

@app.post("/api/auth/login")
async def auth_login(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    password = str(body.get("password") or body.get("passcode") or "").strip()
    if not password:
        raise HTTPException(status_code=400, detail="Clearance passcode is required.")

    if verify_password(password):
        token = "wayne-sess-" + secrets.token_hex(24)
        client_ip = request.client.host if request.client else "unknown"
        ACTIVE_SESSIONS[token] = {
            "created_at": time.time(),
            "user": "Master Ramos",
            "remote": client_ip
        }
        print(f"🔒 [AUTH] Successful Wayne Tech login from {client_ip}. Session created: {token[:16]}...")
        resp = JSONResponse({
            "ok": True,
            "authenticated": True,
            "token": token,
            "user": "Master Ramos",
            "message": "Clearance Level 5 Verified. Welcome, Master Ramos."
        })
        resp.set_cookie(
            key="bat_session",
            value=token,
            max_age=30 * 86400,
            path="/",
            samesite="lax",
            httponly=False
        )
        return resp
    else:
        client_ip = request.client.host if request.client else "unknown"
        print(f"⚠️ [AUTH] Failed login attempt from {client_ip}.")
        return JSONResponse(
            status_code=401,
            content={
                "ok": False,
                "authenticated": False,
                "detail": "SECURITY BREACH ATTEMPT DETECTED: Invalid Wayne Tech Clearance Passcode."
            }
        )


@app.get("/api/auth/status")
async def auth_status(request: Request):
    token = None
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
    elif request.headers.get("x-batcomputer-token") or request.headers.get("X-Batcomputer-Token"):
        token = (request.headers.get("x-batcomputer-token") or request.headers.get("X-Batcomputer-Token")).strip()
        
    if not token and "bat_session" in request.cookies:
        token = request.cookies.get("bat_session")
        
    if not token:
        token = request.query_params.get("token")
        
    is_valid = verify_token(token)
    cfg = get_auth_config()
    auth_enabled = cfg.get("auth_enabled", True)
    
    return {
        "authenticated": is_valid or not auth_enabled,
        "auth_enabled": auth_enabled,
        "user": "Master Ramos" if is_valid else None
    }


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    token = request.cookies.get("bat_session")
    if token and token in ACTIVE_SESSIONS:
        try:
            del ACTIVE_SESSIONS[token]
        except KeyError:
            pass
    resp = JSONResponse({"ok": True, "authenticated": False, "message": "Terminal Locked. Clearance Revoked."})
    resp.delete_cookie("bat_session", path="/")
    return resp


@app.api_route("/api/telemetry", methods=["GET", "HEAD"])
async def get_telemetry(request: Request):
    cpu_pct = psutil.cpu_percent(interval=None)
    cpu_freq = psutil.cpu_freq()
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net_io = psutil.net_io_counters()
    net_info = get_network_info()
    uptime_sec = int(time.time() - START_TIME)
    
    return {
        "cpu": {
            "percent": cpu_pct,
            "cores": psutil.cpu_count(logical=True),
            "freq_mhz": round(cpu_freq.current) if cpu_freq else 3200
        },
        "memory": {
            "used_gb": round(mem.used / (1024**3), 1),
            "total_gb": round(mem.total / (1024**3), 1),
            "percent": mem.percent
        },
        "disk": {
            "used_gb": round(disk.used / (1024**3), 1),
            "total_gb": round(disk.total / (1024**3), 1),
            "percent": disk.percent
        },
        "network": {
            "bytes_sent": net_io.bytes_sent,
            "bytes_recv": net_io.bytes_recv,
            "local_ip": net_info["local_ip"],
            "tailscale_ip": net_info["tailscale_ip"],
            "gateway": net_info["gateway"],
            "hostname": net_info["hostname"]
        },
        "uptime": {
            "seconds": uptime_sec,
            "formatted": f"{uptime_sec // 3600:02d}:{(uptime_sec % 3600) // 60:02d}:{uptime_sec % 60:02d}"
        },
        "active_model": CURRENT_MODEL,
        "active_voice": CURRENT_VOICE,
        "memory_items": len(load_memories()),
        "history_turns": len(load_chat_history()),
        "mission": {
            "arkham_progress": 72,
            "riddler_trophies": "440/440",
            "security_clearance": "ALPHA-1",
            "protocol": "NIGHTFALL PROTOCOL ARMED"
        }
    }


# Models
@app.get("/api/models")
def list_models():
    # OmniRoute Antigravity Gemini Models
    gemini_models = [
        "antigravity/gemini-3.1-flash-lite",
        "antigravity/gemini-3-flash",
        "antigravity/gemini-3.5-flash-lite",
        "antigravity/gemini-3.8-flash-high",
        "antigravity/gemini-3.8-flash-medium",
        "antigravity/gemini-3.8-flash-tiered",
    ]

    # Query live OmniRoute catalog for Gemini models
    if OMNIROUTE_KEY:
        try:
            r = HTTP_SESSION.get(
                f"{OMNIROUTE_URL}/models",
                headers={"Authorization": f"Bearer {OMNIROUTE_KEY}"},
                timeout=3
            )
            if r.status_code == 200:
                data = r.json()
                found = [
                    m.get("id") for m in data.get("data", [])
                    if "gemini" in m.get("id", "").lower()
                ]
                if found:
                    priority_order = [
                        "antigravity/gemini-3.1-flash-lite",
                        "antigravity/gemini-3-flash",
                        "antigravity/gemini-3.5-flash-lite",
                        "antigravity/gemini-3.8-flash-high",
                        "antigravity/gemini-3.8-flash-medium",
                        "antigravity/gemini-3.8-flash-tiered",
                    ]
                    ordered = [m for m in priority_order if m in found]
                    remaining = [m for m in found if m not in priority_order]
                    gemini_models = ordered + remaining
        except Exception:
            pass

    return {
        "current_model": CURRENT_MODEL,
        "available_models": gemini_models,
        "omniroute_models": gemini_models,
        "ollama_models": []
    }


@app.post("/api/models/set")
async def set_model(data: dict):
    global CURRENT_MODEL
    new_model = data.get("model")
    if new_model:
        CURRENT_MODEL = new_model
        return {"success": True, "current_model": CURRENT_MODEL}
    raise HTTPException(status_code=400, detail="Missing model name")


# Voices
@app.get("/api/voices")
async def list_voices():
    voices = [
        {"id": "daniel", "name": "Daniel (Classic British Butler / Alfred)", "engine": "macOS Neural"},
        {"id": "alloy", "name": "Alloy (VoiceStudio Kokoro Neural)", "engine": "VoiceStudio"},
        {"id": "echo", "name": "Echo (Tactical Comm)", "engine": "VoiceStudio"},
        {"id": "fable", "name": "Fable (British Studio Accent)", "engine": "VoiceStudio"},
        {"id": "samantha", "name": "Samantha (Tactical AI)", "engine": "macOS Neural"},
        {"id": "oliver", "name": "Oliver (Crisp British)", "engine": "macOS Neural"}
    ]
    custom_voices = load_custom_voices()
    for v_id, v_data in custom_voices.items():
        voices.insert(0, {
            "id": v_id,
            "name": f"{v_data.get('name', 'Custom Voice')} (Zero-Shot Clone)",
            "engine": "VoiceStudio Clone"
        })
    return {"current_voice": CURRENT_VOICE, "available_voices": voices}


@app.post("/api/voices/set")
async def set_voice(data: dict):
    global CURRENT_VOICE
    new_voice = data.get("voice")
    if new_voice:
        CURRENT_VOICE = new_voice
        return {"success": True, "current_voice": CURRENT_VOICE}
    raise HTTPException(status_code=400, detail="Missing voice name")


# Second Brain
@app.get("/api/brain/list")
async def brain_list():
    notes = []
    for p in BRAIN_DIR.glob("*.md"):
        notes.append({
            "name": p.stem,
            "filename": p.name,
            "size": p.stat().st_size,
            "modified": int(p.stat().st_mtime)
        })
    return {"notes": notes}


@app.get("/api/brain/get")
@app.get("/api/brain/file")
def brain_get(name: str):
    safe_name = Path(name).name
    p = BRAIN_DIR / f"{safe_name}.md" if not safe_name.endswith(".md") else BRAIN_DIR / safe_name
    if p.exists() and p.resolve().parent == BRAIN_DIR.resolve():
        return {"name": p.stem, "content": p.read_text(encoding="utf-8")}
    raise HTTPException(status_code=404, detail="Note not found")


@app.post("/api/brain/save")
def brain_save(data: dict):
    raw_name = data.get("name", "note") or "note"
    safe_name = Path(raw_name).name
    content = data.get("content", "")
    filename = f"{safe_name}.md" if not safe_name.endswith(".md") else safe_name
    p = BRAIN_DIR / filename
    if p.resolve().parent != BRAIN_DIR.resolve():
        raise HTTPException(status_code=400, detail="Invalid note filename")
    p.write_text(content, encoding="utf-8")
    return {"success": True, "filename": filename}


@app.post("/api/brain/search")
def brain_search(data: dict):
    query = data.get("query", "").lower()
    matches = []
    for p in BRAIN_DIR.glob("*.md"):
        try:
            text = p.read_text(encoding="utf-8")
            if query in text.lower():
                matches.append({"name": p.stem, "content": text[:300] + "..."})
        except Exception:
            pass
    return {"query": query, "matches": matches}


# Calendar & Personal iCal Sync
@app.get("/api/calendar")
def get_calendar():
    events = load_calendar_events()
    if not events:
        events = sync_calendar()
    return {"events": events, "config": load_calendar_config()}


@app.post("/api/calendar/sync")
def api_sync_calendar(data: dict = None):
    data = data or {}
    icloud_url = data.get("icloud_url")
    google_url = data.get("google_url")
    url = data.get("url")
    events = sync_calendar(icloud_url=icloud_url, google_url=google_url, url=url)
    return {"success": True, "count": len(events), "events": events, "config": load_calendar_config()}


@app.post("/api/calendar/clear")
def api_clear_calendar():
    save_calendar_events([])
    cfg = load_calendar_config()
    cfg["count"] = 0
    cfg["last_synced"] = time.strftime("%Y-%m-%d %I:%M %p")
    save_calendar_config(cfg)
    return {"success": True, "count": 0, "events": []}


@app.post("/api/calendar/add")
async def add_calendar_event(data: dict):
    events = load_calendar_events()
    new_event = {
        "id": f"cal_{uuid.uuid4().hex[:6]}",
        "title": data.get("title", "New Tactical Objective"),
        "time": data.get("time", "12:00"),
        "date": data.get("date", "2026-10-30"),
        "priority": data.get("priority", "MEDIUM"),
        "location": data.get("location", "Batcave"),
        "description": data.get("description", "")
    }
    events.append(new_event)
    save_calendar_events(events)
    return {"success": True, "event": new_event}


@app.delete("/api/calendar/{event_id}")
async def delete_calendar_event(event_id: str):
    events = load_calendar_events()
    events = [e for e in events if e.get("id") != event_id]
    save_calendar_events(events)
    return {"success": True}


# Memory Endpoints
@app.get("/api/memory")
async def get_memories():
    return {"memories": load_memories()}


@app.post("/api/memory/add")
async def api_add_memory(data: dict):
    fact = data.get("fact", "").strip()
    if not fact:
        raise HTTPException(status_code=400, detail="Missing memory fact")
    cat = data.get("category", "General")
    item = add_memory_fact(fact, cat)
    return {"success": True, "memory": item}


@app.delete("/api/memory/{mem_id}")
@app.post("/api/memory/delete")
async def api_delete_memory(mem_id: str = None, data: dict = None):
    target_id = mem_id or (data.get("id") if data else None)
    if not target_id:
        raise HTTPException(status_code=400, detail="Missing memory ID")
    ok = delete_memory_fact(target_id)
    return {"success": ok}


@app.get("/api/history")
async def get_history():
    return {"history": load_chat_history()}


@app.post("/api/history/clear")
async def api_clear_history():
    clear_chat_history()
    return {"success": True, "message": "Conversation history cleared"}


# Tools & Integrations Hub Endpoints
@app.get("/api/tools/config")
def api_get_tools_config():
    cfg = load_tools_config()
    beszel_nodes = get_beszel_telemetry() if cfg.get("beszel", {}).get("enabled") else []
    mail_info = get_mail_summary() if cfg.get("mail", {}).get("enabled") else {"connected": False, "unread_count": 0, "messages": [], "status": "Disabled"}
    # Mask app password in UI response
    masked_cfg = json.loads(json.dumps(cfg))
    if masked_cfg.get("mail", {}).get("app_password"):
        masked_cfg["mail"]["app_password_set"] = True
        masked_cfg["mail"]["app_password"] = ""
    return {
        "tools": masked_cfg,
        "config": {"tools": masked_cfg},
        "beszel_nodes": beszel_nodes,
        "beszel": {"systems": beszel_nodes},
        "mail": mail_info,
        "hermes_tasks": _HERMES_TASKS
    }


@app.post("/api/tools/config")
def api_update_tools_config(data: dict):
    global WHISPER_MLX_MODEL
    cfg = load_tools_config()
    tool_id = data.get("tool_id")
    updates = data.get("updates", {})
    if tool_id and tool_id in cfg and isinstance(updates, dict):
        if tool_id == "mail" and "app_password" in updates and not updates["app_password"]:
            updates.pop("app_password")
        cfg[tool_id].update(updates)
        save_tools_config(cfg)
        _BESZEL_CACHE["ts"] = 0.0
        _MAIL_CACHE["ts"] = 0.0
        return {"success": True, "tool": cfg[tool_id], "config": {"tools": cfg}}

    tools_dict = data.get("tools")
    if isinstance(tools_dict, dict):
        for k, v in tools_dict.items():
            if isinstance(v, dict):
                if k == "whisper" and v.get("model"):
                    WHISPER_MLX_MODEL = v["model"]
                if k not in cfg:
                    cfg[k] = v
                else:
                    if k == "mail" and "app_password" in v and not v["app_password"]:
                        v = {kk: vv for kk, vv in v.items() if kk != "app_password"}
                    cfg[k].update(v)
        save_tools_config(cfg)
        _BESZEL_CACHE["ts"] = 0.0
        _MAIL_CACHE["ts"] = 0.0
        return {"success": True, "config": {"tools": cfg}}

    raise HTTPException(status_code=400, detail="Invalid tool_id or updates")


@app.get("/api/tools/beszel")
def api_get_beszel():
    nodes = get_beszel_telemetry(use_cache=False)
    return {"nodes": nodes, "systems": nodes}


@app.get("/api/tools/mail")
def api_get_mail():
    return get_mail_summary(use_cache=False)


@app.get("/api/tools/weather")
def api_get_weather():
    return get_live_weather(use_cache=False)


@app.post("/api/tools/search")
def api_web_search(data: dict):
    q = data.get("query", "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Missing query")
    return {"query": q, "results": search_live_web(q)}


@app.get("/api/hermes")
def api_get_hermes_status():
    """Return status and recent task log of Alfred (the Hermes Agent)."""
    cfg = load_tools_config().get("hermes", {})
    bin_path = cfg.get("binary", "/Users/jmrd/.local/bin/hermes")
    return {
        "enabled": cfg.get("enabled", True),
        "installed": Path(bin_path).exists() or bool(shutil.which("hermes")),
        "binary": bin_path,
        "model": cfg.get("model", "antigravity/gemini-3.1-flash-lite"),
        "tasks": _HERMES_TASKS
    }


@app.post("/api/hermes")
def api_run_hermes_task(data: dict):
    """Directly dispatch a heavy workload to Alfred (the Hermes Agent)."""
    task = data.get("task", "").strip() or data.get("prompt", "").strip()
    if not task:
        raise HTTPException(status_code=400, detail="Missing task prompt for Hermes Agent (Alfred)")
    res = invoke_hermes_agent(task, model=data.get("model"))
    return res


# Multi-Client Registry (Server + Multiple Clients or All-in-One Configuration)
CLIENTS_REGISTRY: Dict[str, Any] = {}

@app.post("/api/clients/register")
def register_client_node(data: dict):
    """Register or heartbeat an active client node (Gaming PC, Surface, Laptop, etc.)"""
    cid = data.get("client_id") or data.get("hostname") or f"client_{uuid.uuid4().hex[:6]}"
    CLIENTS_REGISTRY[cid] = {
        "client_id": cid,
        "hostname": data.get("hostname", cid),
        "platform": data.get("platform", "unknown"),
        "bridge_url": data.get("bridge_url", ""),
        "capabilities": data.get("capabilities", []),
        "version": data.get("version", "1.2"),
        "last_seen": time.time(),
        "last_seen_str": time.strftime("%Y-%m-%d %I:%M:%S %p")
    }
    return {"status": "ok", "client_id": cid, "total_clients": len(CLIENTS_REGISTRY)}


@app.get("/api/clients")
def list_clients():
    """List registered Batcomputer client nodes and central server node status."""
    now = time.time()
    active_clients = []
    for cid, info in CLIENTS_REGISTRY.items():
        is_online = (now - info.get("last_seen", 0)) < 120
        active_clients.append({**info, "online": is_online})
    return {
        "server": {
            "hostname": socket.gethostname(),
            "platform": sys.platform,
            "role": "central_server",
            "model": CURRENT_MODEL,
            "voice": CURRENT_VOICE
        },
        "clients": active_clients
    }


@app.post("/api/vision")
def api_vision_endpoint(data: dict):
    """
    JARVIS Multimodal Vision endpoint:
    Distinguishes client screen capture (Gaming PC / Laptop) vs server screen capture (Mac Mini M1),
    as well as webcam and uploaded images.
    """
    image_b64 = data.get("image")
    prompt = data.get("prompt") or "Look at this visual feed and tell me what you see or how to solve it in 1-2 sentences, Master Ramos."
    client_host = data.get("client_host") or data.get("host")
    target = data.get("target", "client" if image_b64 else "server")
    skip_tts = bool(data.get("skip_tts", True))

    res = analyze_vision_frame(image_b64, prompt, client_host=client_host, target=target)
    audio_url = None if skip_tts else synthesize_voice(res.get("reply", ""), CURRENT_VOICE)
    res["audio_url"] = audio_url
    return res


@app.api_route("/api/briefing", methods=["GET", "POST"])
def api_proactive_briefing(speak_on_host: bool = False, skip_tts: bool = True):
    """Proactive JARVIS Morning / Arrival Briefing (callable from HUD, voice, or Home Assistant motion/door webhook)."""
    t0 = time.perf_counter()
    hour = int(time.strftime("%H"))
    greeting = "Good morning, Master Ramos" if hour < 12 else ("Good afternoon, Master Juan" if hour < 18 else "Good evening, Master Remmy")
    weather = get_live_weather()
    ha = get_ha_devices_summary()
    thermo = ha.get("thermostat", {})
    printer = ha.get("printer_3d", {})
    beszel = get_beszel_telemetry()
    events = load_calendar_events()
    mail = get_mail_summary()

    b_summary = ", ".join([f"{n['name']} at {n['cpu_pct']}% CPU" for n in beszel]) if beszel else "all servers nominal"
    cal_part = f"You have {len(events)} scheduled items today, starting with {events[0]['title']} at {events[0]['time']}." if events else "Your schedule is clear today."
    print_part = f"Your Anycubic 3D printer is {printer.get('state', 'idle')} ({printer.get('progress_pct', 0)}%)." if printer.get("online") else ""
    mail_part = f"You have {mail.get('unread_count', 0)} unread emails." if mail.get("connected") else ""

    reply = (
        f"{greeting}. It is {time.strftime('%I:%M %p')}, {weather.get('temp_f', 72)}°F and {weather.get('condition', 'clear').lower()} outside in Georgia, "
        f"with indoor climate at {thermo.get('temp_f', 72)}°F. "
        f"Beszel fleet is online ({b_summary}). {print_part} {cal_part} {mail_part}"
    ).strip()

    audio_url = None if skip_tts else synthesize_voice(reply, CURRENT_VOICE)
    if speak_on_host:
        try:
            subprocess.Popen(["/usr/bin/say", "-v", "Daniel", "-r", "198", reply])
        except Exception:
            pass

    return {
        "reply": reply,
        "audio_url": audio_url,
        "weather": weather,
        "latency_ms": round((time.perf_counter() - t0) * 1000)
    }


@app.post("/api/audio/stop")
def api_stop_host_audio():
    """Immediately stop any active speech synthesis or playback on the host (Barge-In interruption)."""
    try:
        subprocess.run(["pkill", "-f", "/usr/bin/say"], check=False)
        subprocess.run(["pkill", "-f", "afplay"], check=False)
    except Exception:
        pass
    return {"stopped": True}


@app.get("/manifest.json")
def get_pwa_manifest():
    return {
        "name": "BCv1.2 // Alfred Batcomputer HUD",
        "short_name": "Alfred HUD",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#01060d",
        "theme_color": "#00e5ff",
        "description": "Jarvis-grade Batcomputer Voice & Smart Home Command Center"
    }


# Reverse Engineering & GitHub Repo Teardown
@app.post("/api/tools/reverse-engineer")
def run_reverse_engineer(data: dict):
    url = data.get("url", "").strip()
    if not url:
        url = load_tools_config().get("github", {}).get("default_repo", "Juanmanuelrd17/alfred-batcomputer")
    res = reverse_engineer_target(url)
    return res


# Smart Home Device Hub & Endpoints
@app.get("/api/smart-home/devices")
def get_smart_home_devices():
    return get_ha_devices_summary()


@app.post("/api/smart-home/control")
def control_smart_home_device(data: dict):
    domain = data.get("domain", "light")
    service = data.get("service", "toggle")
    entity_id = data.get("entity_id", "")
    extra = data.get("extra", {})
    if not entity_id:
        raise HTTPException(status_code=400, detail="Missing entity_id")
    res = call_hass_device_control(domain, service, entity_id, extra)
    return res


@app.get("/api/smart-home/tv")
def get_tv_status():
    return call_hass_tv("status")


@app.post("/api/smart-home/tv")
def control_tv(data: dict):
    action = data.get("action", "toggle")
    return call_hass_tv(action)


# Voice Cloning Endpoint
@app.post("/api/voice/clone")
def api_clone_voice(file: UploadFile = File(...), name: str = Form("Michael Caine"), voice_id: str = Form("michael_caine")):
    safe_voice_id = re.sub(r'[^a-zA-Z0-9_-]', '_', voice_id.strip()) or "michael_caine"
    tmp_input = AUDIO_CACHE / f"upload_{uuid.uuid4().hex[:8]}{Path(file.filename or 'sample.wav').suffix}"
    content_bytes = file.file.read()
    tmp_input.write_bytes(content_bytes)
    
    ref_wav = AUDIO_CACHE / f"ref_{safe_voice_id}.wav"
    ffmpeg = "/opt/homebrew/bin/ffmpeg" if Path("/opt/homebrew/bin/ffmpeg").exists() else "ffmpeg"
    cmd = [ffmpeg, "-y", "-i", str(tmp_input), "-t", "6", "-vn", "-acodec", "pcm_s16le", "-ar", "24000", "-ac", "1", str(ref_wav)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    tmp_input.unlink(missing_ok=True)
    if res.returncode != 0:
        raise HTTPException(status_code=400, detail=f"FFmpeg extraction failed: {res.stderr}")
        
    # Transcribe
    transcript = "Some men just want to watch the world burn, Master Ramos."
    try:
        with open(ref_wav, "rb") as f:
            files = {"file": ("ref.wav", f, "audio/wav")}
            tr_res = requests.post(f"{VOICESTUDIO_URL}/v1/audio/transcriptions", files=files, data={"model": "Systran/faster-whisper-base"}, timeout=30)
            if tr_res.status_code == 200:
                transcript = tr_res.json().get("text", "").strip() or transcript
    except Exception as e:
        print(f"Clone transcription warning: {e}")
        
    # Register in VoiceStudio
    try:
        with open(ref_wav, "rb") as f:
            files = {"ref_audio": ("ref.wav", f, "audio/wav")}
            data_prof = {"name": name, "ref_text": transcript, "language": "en", "kind": "clone"}
            prof_res = requests.post(f"{VOICESTUDIO_URL}/profiles", files=files, data=data_prof, timeout=20)
            if prof_res.status_code == 200:
                prof_data = prof_res.json()
                profile_id = prof_data.get("id")
                save_custom_voice(safe_voice_id, profile_id, name)
                return {"success": True, "voice_id": safe_voice_id, "profile_id": profile_id, "name": name, "transcript": transcript}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"VoiceStudio cloning registration error: {str(e)}")
        
    raise HTTPException(status_code=500, detail="Voice profile creation failed")


# Chat Core with AI and Tools
@app.post("/api/chat")
def chat_endpoint(data: dict):
    global CURRENT_MODEL, CURRENT_VOICE
    t_start = time.perf_counter()
    user_message = data.get("message", "").strip()
    if not user_message:
        return {"reply": "I am standing by, Master Ramos. All Batcave telemetry links are online.", "audio_url": None, "latency_ms": 0}

    req_model = data.get("model")
    req_voice = data.get("voice")
    skip_tts = bool(data.get("skip_tts", False))
    active_model = req_model or CURRENT_MODEL
    active_voice = req_voice or CURRENT_VOICE

    def maybe_tts(reply_text: str, v: Optional[str] = None) -> Optional[str]:
        if skip_tts:
            return None
        return synthesize_voice(reply_text, v or active_voice)

    lower = user_message.lower()
    
    # 1. Voice switching trigger
    if "switch voice to" in lower or "change voice to" in lower:
        for v in ["daniel", "alloy", "echo", "fable", "samantha", "oliver", "michael_caine"]:
            if v.replace("_", " ") in lower or v in lower:
                CURRENT_VOICE = v
                reply = f"Voice profile successfully transferred to {v.replace('_', ' ').title()}. How may I assist you now, Master Juan?"
                audio_url = maybe_tts(reply, CURRENT_VOICE)
                return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "action": {"voice": CURRENT_VOICE}, "latency_ms": round((time.perf_counter() - t_start) * 1000)}

    # 2. Model switching trigger
    if "switch model to" in lower or "switch brain to" in lower:
        if "antigravity" in lower or "alfred" in lower:
            CURRENT_MODEL = "antigravity/gemini-3.1-flash-lite"
            reply = "Neural processing core successfully linked to OmniRoute Antigravity Flash-Lite brain. Awaiting directives, Master Ramos."
            audio_url = maybe_tts(reply)
            return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "action": {"model": CURRENT_MODEL}, "latency_ms": round((time.perf_counter() - t_start) * 1000)}
        for m in ["antigravity/gemini-3.1-flash-lite", "antigravity/gemini-3-flash", "antigravity/gemini-3.5-flash-lite", "antigravity/gemini-3.8-flash-high", "antigravity/gemini-3.8-flash-medium", "antigravity/gemini-3.8-flash-tiered"]:
            if m.split(":")[0] in lower or m.lower() in lower:
                CURRENT_MODEL = m
                reply = f"Neural processing core switched to {m}. Awaiting directives, Master Remmy."
                audio_url = maybe_tts(reply)
                return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "action": {"model": CURRENT_MODEL}, "latency_ms": round((time.perf_counter() - t_start) * 1000)}

    # 3. Comprehensive Home Assistant Directives (0ms Reflex Fast-Paths)
    # TV Directives
    if any(q in lower for q in ["turn on the tv", "turn on tv", "power on tv", "prende la tv", "encender tv"]):
        call_hass_tv("turn_on")
        reply = "Powering on the Sony BRAVIA 85-inch display in the living room, Master Ramos."
        audio_url = maybe_tts(reply)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "action": {"device": "tv", "state": "on"}, "latency_ms": round((time.perf_counter() - t_start) * 1000)}

    if any(q in lower for q in ["turn off the tv", "turn off tv", "power off tv", "apaga la tv", "apagar tv"]):
        call_hass_tv("turn_off")
        reply = "Living room Sony BRAVIA television powered down, Master Juan."
        audio_url = maybe_tts(reply)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "action": {"device": "tv", "state": "off"}, "latency_ms": round((time.perf_counter() - t_start) * 1000)}

    # Smart Home Directives (Lights, TV, Fans)
    clean_lower = re.sub(r'^(?:(?:hey|ok|yo|hi)\s+)?(?:computer|computa|commuter|puter|pewter|purer|putter|peter|tutor|tudor|ooter|cuter|alfred)[\s,.!:;-]*', '', lower.strip(), flags=re.IGNORECASE)
    if not clean_lower:
        reply = "Standing by, Master Ramos. What is your directive?"
        audio_url = maybe_tts(reply)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "latency_ms": round((time.perf_counter() - t_start) * 1000)}
    
    is_question = any(q in clean_lower for q in ["why ", "why'd", "cant ", "can't ", "cannot ", "doesnt ", "doesn't ", "how to ", "how do ", "which ", "what ", "are "])

    if not is_question:
        has_light = any(w in clean_lower for w in ["light", "lights", "luz", "luces", "dimmer", "lampara"])
        has_off = any(w in clean_lower for w in ["turn off", "turn of", "switch off", "shut off", "kill", "lights off", "light off", "apaga", "apagar", "power down", "extinguish"])
        has_on = any(w in clean_lower for w in ["turn on", "switch on", "lights on", "light on", "prende", "prender", "enciende", "encender", "illuminate", "power on"])

        if (has_light and (has_off or has_on)) or clean_lower in ["lights off", "lights on", "kill the lights"]:
            is_all = any(w in clean_lower for w in ["all", "todas", "todo", "every"])
            is_lr = any(w in clean_lower for w in ["living room", "sala", "theater", "theatre"])

            if has_off or "off" in clean_lower or "kill" in clean_lower or "apaga" in clean_lower:
                if is_all:
                    call_hass_device_control("light", "turn_off", "light.luz_sala")
                    call_hass_device_control("light", "turn_off", "light.dimmer_cuarto_jm")
                    call_hass_device_control("light", "turn_off", "light.home_theater")
                    call_hass_device_control("light", "turn_off", "light.luz_cuarto_mau")
                    reply = "All quarters secured. Every lighting array has been extinguished, Master Ramos."
                    target = "all_lights"
                elif is_lr:
                    target_ent = "light.home_theater" if ("theater" in clean_lower or "theatre" in clean_lower) else "light.luz_sala"
                    call_hass_device_control("light", "turn_off", target_ent)
                    reply = "Living room lighting extinguished, Master Remmy."
                    target = target_ent
                else:
                    call_hass_device_control("light", "turn_off", "light.dimmer_cuarto_jm")
                    reply = "Your bedroom lighting has been extinguished, Master Juan."
                    target = "light.dimmer_cuarto_jm"
                
                audio_url = maybe_tts(reply, active_voice)
                return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "action": {"device": target, "state": "off"}, "latency_ms": round((time.perf_counter() - t_start) * 1000)}

            if has_on or "on" in clean_lower or "prende" in clean_lower:
                if is_all:
                    call_hass_device_control("light", "turn_on", "light.luz_sala")
                    call_hass_device_control("light", "turn_on", "light.dimmer_cuarto_jm")
                    reply = "All perimeter and quarter lights illuminated, Master Ramos."
                    target = "all_lights"
                elif is_lr:
                    target_ent = "light.home_theater" if ("theater" in clean_lower or "theatre" in clean_lower) else "light.luz_sala"
                    call_hass_device_control("light", "turn_on", target_ent)
                    reply = "Living room illumination active, Master Remmy."
                    target = target_ent
                else:
                    call_hass_device_control("light", "turn_on", "light.dimmer_cuarto_jm")
                    reply = "Illuminating your bedroom quarters, Master Juan."
                    target = "light.dimmer_cuarto_jm"

                audio_url = maybe_tts(reply, active_voice)
                return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "action": {"device": target, "state": "on"}, "latency_ms": round((time.perf_counter() - t_start) * 1000)}

    # Thermostat / Climate Queries
    if any(q in lower for q in ["what's the temperature", "what is the temperature", "thermostat status", "how warm is it", "how cold is it", "temperatura"]):
        ha_data = get_ha_devices_summary()
        t = ha_data.get("thermostat", {})
        temp_f = t.get("temp_f", 72.5)
        hum = t.get("humidity", 62)
        act = t.get("action", "cooling")
        reply = f"Indoor temperature is {temp_f} degrees Fahrenheit with {hum}% humidity, and HVAC is actively {act}, Master Ramos."
        audio_url = maybe_tts(reply)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "thermostat": t, "latency_ms": round((time.perf_counter() - t_start) * 1000)}

    # Fans
    if any(q in lower for q in ["turn on fan", "turn on the fan"]):
        call_hass_device_control("switch", "turn_on", "switch.ventilador_mau")
        reply = "Circulation fan activated, Master Remmy."
        audio_url = maybe_tts(reply)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "latency_ms": round((time.perf_counter() - t_start) * 1000)}

    if any(q in lower for q in ["turn off fan", "turn off the fan"]):
        call_hass_device_control("switch", "turn_off", "switch.ventilador_mau")
        call_hass_device_control("switch", "turn_off", "switch.ventilador_papas")
        reply = "Fans powered down, Master Juan."
        audio_url = maybe_tts(reply)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "latency_ms": round((time.perf_counter() - t_start) * 1000)}

    # 3b. Cinema / Movie Mode & Media Playback Reflexes
    if any(q in lower for q in ["movie mode", "cinema mode", "theater mode", "modo cine"]):
        call_hass_tv("turn_on")
        call_hass_device_control("light", "turn_off", "light.luz_sala")
        call_hass_device_control("light", "turn_off", "light.home_theater")
        call_hass_device_control("switch", "turn_on", "switch.sonos_home_theater_speech_enhancement")
        reply = "Cinema protocol engaged, Master Ramos. Living room lights extinguished, Sony BRAVIA 85-inch display online, and Sonos speech enhancement active."
        audio_url = maybe_tts(reply)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "ha_actions": [{"entity_id": "media_player.tv_sala_2", "service": "turn_on"}], "latency_ms": round((time.perf_counter() - t_start) * 1000)}

    if any(q in lower for q in ["pause the tv", "pause tv", "resume tv", "play the tv", "pause media", "pause sonos"]):
        call_hass_device_control("media_player", "media_play_pause", "media_player.tv_sala_2")
        call_hass_device_control("media_player", "media_play_pause", "media_player.sonos_home_theater")
        reply = "Media playback toggled on the living room display and Sonos array, Master Remmy."
        audio_url = maybe_tts(reply)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "latency_ms": round((time.perf_counter() - t_start) * 1000)}

    # 3c. Proactive Morning / Arrival Briefing Trigger
    if any(q in lower for q in ["morning briefing", "daily briefing", "arrival briefing", "give me my briefing", "full briefing"]):
        b_res = api_proactive_briefing(speak_on_host=False, skip_tts=skip_tts)
        b_res["user_message"] = user_message
        return b_res

    # 3d. Computer Vision / Screen Inspection Trigger ("Alfred, look at my screen" / "what is on my screen")
    if any(q in lower for q in ["look at my screen", "what is on my screen", "what's on my screen", "analyze my screen", "read my screen"]):
        target_mode = "server" if "server" in lower else "client"
        v_res = analyze_vision_frame(None, user_message, target=target_mode)
        reply = v_res.get("reply", "Screen inspected, Master Ramos.")
        audio_url = maybe_tts(reply)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "vision": True, "latency_ms": round((time.perf_counter() - t_start) * 1000)}

    # 4. Reverse Engineer / GitHub Repo Teardown Command
    if lower.startswith("reverse engineer ") or "reverse engineer " in lower or "teardown " in lower:
        m = re.search(r'(?:https?://[^\s]+|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)', user_message.split("engineer")[-1] if "engineer" in lower else user_message.split("teardown")[-1])
        target_url = m.group(0).strip(".,!?") if m else "Juanmanuelrd17/alfred-batcomputer"
        res = reverse_engineer_target(target_url)
        reply = f"Teardown complete for {target_url}. Primary stack detected: {', '.join(res.get('tech_stack', [])[:4])}. Blueprint rendered in the Teardown drawer, Master Ramos."
        audio_url = maybe_tts(reply)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "reverse_engineer_data": res, "latency_ms": round((time.perf_counter() - t_start) * 1000)}

    # 5. Calendar Query
    if "what's on my schedule" in lower or "today's calendar" in lower or "my agenda" in lower:
        events = load_calendar_events()
        if events:
            agenda_str = ", ".join([f"{e['title']} at {e['time']}" for e in events[:3]])
            reply = f"You have {len(events)} objectives scheduled today, Master Ramos: {agenda_str}."
        else:
            reply = "Your tactical schedule is currently clear for the remainder of the day, Master Juan."
        audio_url = maybe_tts(reply)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "latency_ms": round((time.perf_counter() - t_start) * 1000)}

    # 6. Memory Commands & Directives
    if "clear history" in lower or "clear memory" in lower or "reset conversation" in lower or "forget our conversation" in lower:
        clear_chat_history()
        reply = "Active conversation buffer purged, Master Ramos. Long-term memory vaults remain intact."
        audio_url = maybe_tts(reply)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "action": {"memory": "cleared_history"}, "latency_ms": round((time.perf_counter() - t_start) * 1000)}

    clean_directive = re.sub(r'^(?:(?:hey|ok|yo|hi)\s+)?(?:computer|computa|commuter|puter|pewter|purer|putter|peter|tutor|tudor|ooter|cuter|alfred)[\s,.!:;-]*', '', user_message.strip(), flags=re.IGNORECASE)

    # 6b. Explicit Delegation to Alfred (Hermes Agent) when user says "ask alfred...", "have alfred...", "tell alfred...", "use hermes..."
    explicit_hermes = re.match(r'^(?:please\s+)?(?:ask\s+alfred\s+to|have\s+alfred|tell\s+alfred\s+to|use\s+alfred\s+to|delegate\s+to\s+alfred|use\s+hermes\s+to|ask\s+hermes\s+to|hermes[,:]?)\s+(.+)', clean_directive, re.IGNORECASE)
    if explicit_hermes:
        hermes_task = explicit_hermes.group(1).strip()
        h_res = invoke_hermes_agent(hermes_task)
        reply = f"I delegated that workload to Alfred on Hermes, Master Ramos. Alfred reports: {h_res.get('output') or h_res.get('reply', 'Task completed.')}"
        audio_url = maybe_tts(reply)
        hist = load_chat_history()
        hist.append({"role": "user", "content": user_message})
        hist.append({"role": "assistant", "content": reply})
        save_chat_history(hist)
        return {
            "user_message": user_message,
            "reply": reply,
            "audio_url": audio_url,
            "hermes_actions": [h_res],
            "latency_ms": round((time.perf_counter() - t_start) * 1000)
        }

    rem_match = re.match(r'^(?:please\s+)?(?:remember\s+that|remember\s+to|remember|take\s+note\s+that|memorize\s+that)\s+(.+)', clean_directive, re.IGNORECASE)
    if rem_match and not clean_directive.endswith("?") and len(rem_match.group(1).strip()) > 3:
        fact = rem_match.group(1).strip()
        add_memory_fact(fact, category="User Fact")
        reply = f"Committed to long-term memory, Master Remmy: '{fact[:60]}'."
        audio_url = maybe_tts(reply)
        # record turn
        hist = load_chat_history()
        hist.append({"role": "user", "content": user_message})
        hist.append({"role": "assistant", "content": reply})
        save_chat_history(hist)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "action": {"memory": "added", "fact": fact}, "latency_ms": round((time.perf_counter() - t_start) * 1000)}

    events = load_calendar_events()
    calendar_summary = "\n".join([f"- {e.get('time')} - {e.get('title')} ({e.get('priority')}) at {e.get('location')}" for e in events]) if events else "No events scheduled."

    # 7. General Reasoning & Agentic Home Assistant + Hermes Tool Calling via OmniRoute or Local Ollama
    req_model = data.get("model")
    req_voice = data.get("voice")
    active_model = req_model or CURRENT_MODEL
    active_voice = req_voice or CURRENT_VOICE

    ha_summary = get_ha_devices_summary()
    ha_live_context = format_ha_context_for_prompt(ha_summary)
    beszel_nodes = get_beszel_telemetry()
    beszel_str = ", ".join([f"{n['name']}({n['host']}): status={n['status']} cpu={n['cpu_pct']}% mem={n['mem_pct']}% disk={n['disk_pct']}% temp={n['temp_c']}C" for n in beszel_nodes]) if beszel_nodes else "No Beszel nodes active"
    mail_info = get_mail_summary()
    mail_str = f"Unread={mail_info.get('unread_count', 0)} | Recent: " + "; ".join([f"{m['subject']} (from {m['from']})" for m in mail_info.get("messages", [])[:3]]) if mail_info.get("connected") else mail_info.get("status", "Disconnected")
    weather_info = get_live_weather()
    weather_str = f"{weather_info.get('temp_f')}F ({weather_info.get('condition')}), feels like {weather_info.get('feels_like_f', weather_info.get('temp_f'))}F, humidity {weather_info.get('humidity')}%, wind {weather_info.get('wind_mph')} mph in {weather_info.get('location')}"

    # On-demand Live Web Search if user asks for external facts, news, weather, or web lookup
    web_search_str = ""
    if any(k in lower for k in ["search for", "search the web", "look up", "latest news", "who won", "stock price", "price of", "what happened in", "google "]):
        web_hits = search_live_web(clean_directive)
        if web_hits:
            web_search_str = "\nLIVE WEB SEARCH RESULTS:\n" + "\n".join([f"- {h['title']}: {h['snippet']}" for h in web_hits])

    engine_desc = f"OmniRoute Antigravity Gemini Gateway ({active_model})"
    session_summary = get_running_session_summary()
    session_summary_str = f"\n{session_summary}\n" if session_summary else ""

    second_brain_ctx = get_relevant_second_brain_context(clean_directive)
    second_brain_str = f"\nRELEVANT SECOND BRAIN KNOWLEDGE:\n{second_brain_ctx}\n" if second_brain_ctx else ""

    system_prompt = f"""You are 'Puter (Computer), the fast, witty Batcomputer AI assistant to Juan Manuel Ramos (inspired by The Lego Batman Movie 'Puter + JARVIS).
Wake words: "Computer", "Puter", "Hey Computer", or "Hey Puter".
Address him naturally as 'Master Ramos', 'Master Juan', or 'Master Remmy' (NEVER call him Master Wayne or Bruce).
Reply in 1-2 crisp sentences (<25 words).

CONTINUOUS MULTI-TURN CONVERSATION & MEMORY AWARENESS:
- You are engaged in a fluid, continuous conversation with Master Ramos.
- Maintain strict contextual awareness across turns and sessions.
- In addition to recent dialogue turns, use RUNNING SESSION CONTEXT and RELEVANT SECOND BRAIN KNOWLEDGE below to remember ongoing tasks, preferences, and discussed topics.
- Self-Learning & Memory: You automatically learn and retain facts. If you learn an important persistent fact, preference, hardware setup, or correction from Master Ramos, you can record it to permanent memory by appending:
[MEMORY_ADD: <Category> | <Fact>]
(Categories: Identity, Preference, Project, Hardware, Habit, Correction).
Example: [MEMORY_ADD: Hardware | Master Ramos uses Razer Barracuda headset on 144Hz DP-1 monitor]
- Pronouns ("it", "they", "that", "those", "the other one", "both", "all of them") refer directly to entities, devices, rooms, or topics discussed in recent messages or running context.
- If Master Ramos clarifies or corrects a previous command (e.g., "I meant the monitors", "no, the bedroom lights", "make it 10%", "turn that off too", "is it still running?"), immediately apply the correction using full context from recent messages.
- During an ongoing exchange, do not restart conversations with greetings or generic pleasantries. Speak naturally as a seamless dialogue partner.

ALFRED // HERMES AGENT DELEGATION PROTOCOL (AUTONOMOUS HEAVY WORKLOADS):
- Remember that ALFRED is your autonomous Hermes Agent (`/Users/jmrd/.local/bin/hermes`) running on Mac-mini-M1 with full terminal, shell, SSH, file editing, code execution, cron, and deep research capabilities.
- You ('Puter) handle fast conversational replies, instant HUD status checks, and Home Assistant device toggles directly.
- DECIDE AUTONOMOUSLY when a request is a greater/heavier workload (e.g., running shell/terminal commands, checking host processes/uptime/ports/logs, inspecting files or git repos on disk, SSH diagnostics across servers, writing scripts, or multi-step automation—OR when the user explicitly asks to use Alfred/Hermes).
- When you decide Alfred (Hermes) should handle the workload, briefly state that you are dispatching Alfred and append this exact tag at the end of your reply:
[HERMES_CALL: <clear, self-contained instruction for Alfred to execute>]
Example: Deploying Alfred via Hermes to inspect the Mac Mini uptime and top CPU processes, Master Ramos. [HERMES_CALL: Run uptime and ps aux on this Mac Mini and summarize the system load in one sentence.]

HOME ASSISTANT PROTOCOL:
- For status questions already covered in LIVE HA STATE or BESZEL FLEET TELEMETRY below, answer directly without any tags.
- ONLY when asked to change/control a smart home device or scene, append tags at the end:
[HA_CALL: domain.service | entity_id | {{"optional_param": value}}]
Examples: [HA_CALL: light.turn_on | light.luz_papas], [HA_CALL: light.turn_on | light.dimmer_cuarto_jm | {{"brightness_pct": 25}}], [HA_CALL: media_player.volume_set | media_player.sonos_home_theater | {{"volume_level": 0.3}}], [HA_CALL: media_player.media_play_pause | media_player.tv_sala_2], [HA_CALL: button.press | button.anycubic_kobra_x_pause_print]

Time: {time.strftime('%I:%M %p')}
OUTDOOR WEATHER: {weather_str}{session_summary_str}{second_brain_str}
LIVE HA STATE:
{ha_live_context}
BESZEL FLEET TELEMETRY:
{beszel_str}
MAIL INBOX:
{mail_str}{web_search_str}
Memories:
{format_memories_for_prompt()}
Schedule: {calendar_summary}"""

    history = load_chat_history()
    messages = [{"role": "system", "content": system_prompt}] + history[-14:] + [{"role": "user", "content": user_message}]
    
    raw_reply = query_brain(messages, active_model)
    reply, mem_actions = execute_llm_memory_tags(raw_reply)
    reply, ha_actions = execute_llm_ha_tags(reply)
    reply, hermes_actions = execute_llm_hermes_tags(reply)
    if not reply:
        reply = "Directive executed, Master Ramos."
    audio_url = maybe_tts(reply, active_voice)

    # Persist rolling conversation turns
    history.append({"role": "user", "content": user_message})
    history.append({"role": "assistant", "content": reply})
    save_chat_history(history)

    # Autonomous Background Learning & Memory Consolidation (runs non-blocking)
    threading.Thread(
        target=extract_and_learn_memories_async,
        args=(user_message, reply),
        daemon=True
    ).start()
    
    return {
        "user_message": user_message,
        "reply": reply,
        "audio_url": audio_url,
        "ha_actions": ha_actions,
        "hermes_actions": hermes_actions,
        "memory_actions": mem_actions,
        "latency_ms": round((time.perf_counter() - t_start) * 1000)
    }


@app.post("/api/transcribe")
def api_transcribe_only(file: UploadFile = File(...)):
    """Ultra-fast STT endpoint using Apple Silicon Metal GPU MLX-Whisper (~160ms) with domain prompt."""
    t0 = time.perf_counter()
    audio_bytes = file.file.read()
    text = transcribe_audio(audio_bytes, file.filename or "chunk.webm")
    return {
        "text": text,
        "latency_ms": round((time.perf_counter() - t0) * 1000),
        "engine": "MLX-GPU" if _MLX_WHISPER_READY else "VoiceStudio"
    }


@app.post("/api/listen")
def listen_endpoint(
    file: UploadFile = File(...),
    voice: Optional[str] = Form(None),
    model: Optional[str] = Form(None)
):
    global CURRENT_VOICE, CURRENT_MODEL
    audio_bytes = file.file.read()
    transcription = transcribe_audio(audio_bytes, file.filename or "recording.wav")
    
    if not transcription or not transcription.strip():
        transcription = "Status report"
        
    active_voice = voice or CURRENT_VOICE
    active_model = model or CURRENT_MODEL
    
    chat_result = chat_endpoint({
        "message": transcription,
        "voice": active_voice,
        "model": active_model
    })
    chat_result["transcription"] = transcription
    return chat_result


@app.post("/api/tts")
def api_tts_endpoint(data: dict):
    """Decoupled ultra-fast TTS endpoint so text replies render in <1s while audio streams immediately after."""
    text = data.get("text", "").strip()
    voice = data.get("voice") or CURRENT_VOICE
    if not text:
        raise HTTPException(status_code=400, detail="Missing text")
    t0 = time.perf_counter()
    audio_url = synthesize_voice(text, voice)
    return {"audio_url": audio_url, "tts_ms": round((time.perf_counter() - t0) * 1000)}


@app.api_route("/api/audio/{filename}", methods=["GET", "HEAD"])
async def get_audio(filename: str):
    safe_filename = Path(filename).name
    file_path = AUDIO_CACHE / safe_filename
    if file_path.exists() and file_path.resolve().parent == AUDIO_CACHE.resolve():
        media_type = "audio/wav" if safe_filename.endswith(".wav") else "audio/mpeg"
        return FileResponse(str(file_path), media_type=media_type)
    raise HTTPException(status_code=404, detail="Audio file not found")


@app.get("/api/files")
def api_list_host_files(path: Optional[str] = None, query: Optional[str] = None):
    """Fallback file explorer endpoint if local desktop bridge (127.0.0.1:7778) is not reachable."""
    home_dir = Path.home()
    target = Path(path).expanduser() if path else home_dir
    if not target.exists() or not target.is_dir():
        target = home_dir
    items = []
    q_lower = (query or "").strip().lower()
    try:
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        for entry in entries:
            if entry.name.startswith(".") and entry.name not in (".gemini", ".config"):
                continue
            if q_lower and q_lower not in entry.name.lower():
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
            if len(items) >= 80:
                break
    except Exception as e:
        return {"path": str(target), "parent": str(target.parent), "items": [], "error": str(e)}
    return {
        "host": platform.node(),
        "path": str(target),
        "parent": str(target.parent) if target.parent != target else str(target),
        "items": items
    }


@app.get("/api/files/preview")
def api_preview_host_file(path: str):
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content = p.read_text(encoding="utf-8", errors="replace")[:12000]
        return {"name": p.name, "path": str(p), "size": p.stat().st_size, "content": content}
    except Exception as e:
        return {"name": p.name, "path": str(p), "error": str(e), "content": ""}


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=7776, reload=False)
