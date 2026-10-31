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
from pathlib import Path
from typing import Optional, List, Dict, Any
import urllib.request
import urllib.parse
import requests

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

AUDIO_CACHE.mkdir(exist_ok=True)
BRAIN_DIR.mkdir(exist_ok=True)

VOICESTUDIO_URL = "http://127.0.0.1:3900"
OLLAMA_URL = "http://127.0.0.1:11434"
START_TIME = time.time()

# Configurable State
CURRENT_MODEL = "antigravity/gemini-3-flash"
CURRENT_VOICE = "michael_caine"  # Default: Michael Caine (Dark Knight Alfred)

# Home Assistant & OmniRoute Config
HASS_TOKEN = ""
HASS_URL = "http://100.93.110.2:8123"
OMNIROUTE_URL = "http://zimaos:20128/v1"
OMNIROUTE_KEY = ""

env_file = Path("/Users/jmrd/.hermes/.env")
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line.startswith("HASS_TOKEN="):
            HASS_TOKEN = line.split("=", 1)[1].strip()
        elif line.startswith("HASS_URL="):
            HASS_URL = line.split("=", 1)[1].strip().rstrip("/")
        elif line.startswith("HERMES_CUSTOM_ZIMAOS_20128_API_KEY="):
            OMNIROUTE_KEY = line.split("=", 1)[1].strip()

app = FastAPI(title="Batcomputer HUD - Jarvis Replication Core")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

def synthesize_voice(text: str, voice: Optional[str] = None) -> Optional[str]:
    """Generate audio using selected voice engine (Daniel, VoiceStudio, or Cloned Michael Caine)"""
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

    # 2. If native macOS neural butler voice
    if selected_voice in ["daniel", "oliver", "samantha"]:
        voice_map = {"daniel": "Daniel", "oliver": "Oliver", "samantha": "Samantha"}
        system_voice = voice_map.get(selected_voice, "Daniel")
        tmp_aiff = AUDIO_CACHE / f"tmp_{uuid.uuid4().hex[:8]}.aiff"
        try:
            subprocess.run(["say", "-v", system_voice, "-r", "195", "-o", str(tmp_aiff), clean_text], check=True, timeout=10)
            subprocess.run(["ffmpeg", "-y", "-i", str(tmp_aiff), "-codec:a", "libmp3lame", "-qscale:a", "2", str(filepath)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=5)
            if tmp_aiff.exists():
                tmp_aiff.unlink()
            return f"/api/audio/{filename}"
        except Exception as e:
            print(f"macOS say synthesis error: {e}", file=sys.stderr)
            if tmp_aiff.exists():
                tmp_aiff.unlink()

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


def transcribe_audio(audio_bytes: bytes, filename: str = "audio.wav") -> str:
    """Send recorded speech to VoiceStudio fast MLX/Faster-Whisper STT"""
    try:
        res = requests.post(
            f"{VOICESTUDIO_URL}/transcribe",
            files={"audio": (filename, audio_bytes, "audio/wav")},
            data={"mode": "fast"},
            timeout=10
        )
        if res.status_code == 200:
            return res.json().get("text", "").strip()
    except Exception as e:
        print(f"VoiceStudio /transcribe error: {e}", file=sys.stderr)

    try:
        res = requests.post(
            f"{VOICESTUDIO_URL}/v1/audio/transcriptions",
            files={"file": (filename, audio_bytes, "audio/wav")},
            data={"model": "Systran/faster-whisper-base"},
            timeout=12
        )
        if res.status_code == 200:
            return res.json().get("text", "").strip()
    except Exception as e:
        print(f"VoiceStudio /v1/audio/transcriptions error: {e}", file=sys.stderr)

    return ""


# ==========================================
# BRAIN: OMNIROUTE (ANTIGRAVITY) & OLLAMA (LOCAL)
# ==========================================

def is_omniroute_model(model_name: str) -> bool:
    m = model_name.lower()
    return "antigravity" in m or m.startswith("omniroute") or "claude" in m or "gemini" in m or "gpt" in m


def query_omniroute(messages: List[Dict[str, str]], model: str = "antigravity/gemini-3-flash") -> str:
    """Query OmniRoute proxy (Antigravity frontier models) with high-speed parameters"""
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.5,
        "max_tokens": 100
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OMNIROUTE_URL}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {OMNIROUTE_KEY}",
            "Content-Type": "application/json"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"OmniRoute inference error: {e}", file=sys.stderr)
        return "I apologize, Master Wayne. The OmniRoute Antigravity neural link experienced an uplink interruption."


def query_ollama(messages: List[Dict[str, str]], model: Optional[str] = None) -> str:
    """Query local Ollama instance with low latency"""
    active_model = model or "qwen2.5:7b"
    payload = json.dumps({
        "model": active_model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0.6,
            "num_predict": 250
        }
    }).encode("utf-8")
    
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("message", {}).get("content", "").strip()
    except Exception as e:
        print(f"Ollama inference error: {e}", file=sys.stderr)
        return "I apologize, Master Wayne. The local neural brain experienced a momentary pipeline hiccup."


def query_brain(messages: List[Dict[str, str]], model: Optional[str] = None) -> str:
    """Intelligently route to OmniRoute (Antigravity) or Local Ollama based on selected model"""
    active_model = model or CURRENT_MODEL
    if is_omniroute_model(active_model):
        return query_omniroute(messages, active_model)
    return query_ollama(messages, active_model)


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
    payload = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"HASS API Error ({endpoint}): {e}")
        return None


def get_ha_devices_summary() -> Dict[str, Any]:
    states = call_hass_api("states") or []
    by_id = {s["entity_id"]: s for s in states}

    # Primary TV: BRAVIA XR-85X90L, with fallback to legacy living_room
    bravia = by_id.get("media_player.tv_sala_2", {})
    tv_lr = by_id.get("media_player.tv_living_room", {})
    b_attrs = bravia.get("attributes", {})

    active_tv = bravia if bravia else tv_lr
    active_attrs = active_tv.get("attributes", {})
    
    # Sonos Audio
    sonos = by_id.get("media_player.sonos_home_theater", {})
    s_attrs = sonos.get("attributes", {})

    # Thermostat
    thermo = by_id.get("climate.jm_servers_thermostat", {})
    t_attrs = thermo.get("attributes", {})
    c_temp = t_attrs.get("current_temperature")
    f_temp = round(c_temp * 9/5 + 32, 1) if c_temp else None

    # Lights
    jm_light = by_id.get("light.dimmer_cuarto_jm", {})
    sala_light = by_id.get("light.luz_sala", {})
    ht_light = by_id.get("light.home_theater", {})
    mau_light = by_id.get("light.luz_cuarto_mau", {})

    # Fans
    mau_fan = by_id.get("switch.ventilador_mau", {})
    papas_fan = by_id.get("switch.ventilador_papas", {})
    jm_switch = by_id.get("switch.switch_cuarto_jm", {})

    return {
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
            "volume": round((s_attrs.get("volume_level") or 0.3) * 100)
        },
        "thermostat": {
            "entity_id": "climate.jm_servers_thermostat",
            "name": t_attrs.get("friendly_name", "Home Thermostat"),
            "temp_f": f_temp,
            "temp_c": c_temp,
            "humidity": t_attrs.get("current_humidity", 62),
            "mode": thermo.get("state", "heat_cool"),
            "action": t_attrs.get("hvac_action", "cooling")
        },
        "lights": [
            {"entity_id": "light.dimmer_cuarto_jm", "name": "JM Bedroom Dimmer", "state": jm_light.get("state", "off")},
            {"entity_id": "light.luz_sala", "name": "Living Room Light", "state": sala_light.get("state", "off")},
            {"entity_id": "light.home_theater", "name": "Home Theater Light", "state": ht_light.get("state", "off")},
            {"entity_id": "light.luz_cuarto_mau", "name": "Mau Room Light", "state": mau_light.get("state", "off")}
        ],
        "switches": [
            {"entity_id": "switch.ventilador_mau", "name": "Mau Fan", "state": mau_fan.get("state", "off")},
            {"entity_id": "switch.ventilador_papas", "name": "Parents Fan", "state": papas_fan.get("state", "off")},
            {"entity_id": "switch.switch_cuarto_jm", "name": "JM Room Switch", "state": jm_switch.get("state", "off")}
        ]
    }


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
# PERSONAL CALENDAR & ICAL SYNC ENGINE
# ==========================================
def parse_ics_text(ics_text: str) -> List[Dict[str, Any]]:
    events = []
    vevents = re.findall(r'BEGIN:VEVENT(.*?)END:VEVENT', ics_text, re.DOTALL)
    for v in vevents:
        summary_m = re.search(r'SUMMARY:(.*)', v)
        summary = summary_m.group(1).strip() if summary_m else 'Personal Event'
        summary = summary.replace('\,', ',').replace('\;', ';')
        
        start_m = re.search(r'DTSTART(?:;[^:]+)?:(\d+T\d+Z?|\d+)', v)
        start_raw = start_m.group(1).strip() if start_m else ''
        
        loc_m = re.search(r'LOCATION:(.*)', v)
        loc = loc_m.group(1).strip() if loc_m else 'Personal Location'
        loc = loc.replace('\,', ',').replace('\;', ';')
        
        time_str = 'All Day'
        date_str = 'Upcoming'
        if 'T' in start_raw:
            try:
                from datetime import datetime
                dt = datetime.strptime(start_raw[:15], '%Y%m%dT%H%M%S')
                time_str = dt.strftime('%I:%M %p')
                date_str = dt.strftime('%Y-%m-%d')
            except Exception:
                time_str = start_raw
        elif len(start_raw) == 8:
            try:
                from datetime import datetime
                dt = datetime.strptime(start_raw, '%Y%m%d')
                date_str = dt.strftime('%Y-%m-%d')
            except Exception:
                date_str = start_raw

        events.append({
            'id': f'cal_{uuid.uuid4().hex[:6]}',
            'title': summary,
            'time': time_str,
            'date': date_str,
            'location': loc,
            'priority': 'Personal',
            'status': 'CONFIRMED'
        })
    return events


def load_calendar_config() -> dict:
    if CALENDAR_CONFIG_FILE.exists():
        try:
            return json.loads(CALENDAR_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"ical_url": "", "last_synced": None, "source": "local"}


def save_calendar_config(cfg: dict):
    CALENDAR_CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def sync_calendar(source: Optional[str] = None) -> List[Dict[str, Any]]:
    cfg = load_calendar_config()
    target = source or cfg.get("ical_url")
    
    if not target:
        default_local = Path("/Users/jmrd/.hermes/sawnee_sunrise_2026-08-15.ics")
        if default_local.exists():
            target = str(default_local)
            
    if not target:
        return load_calendar_events()

    ics_text = ""
    if target.startswith("http://") or target.startswith("https://") or target.startswith("webcal://"):
        fetch_url = target.replace("webcal://", "https://")
        req = urllib.request.Request(fetch_url, headers={"User-Agent": "Mozilla/5.0 (Batcomputer-Alfred)"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                ics_text = resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"Error fetching remote iCal {target}: {e}")
            return load_calendar_events()
    else:
        p = Path(target)
        if p.exists():
            ics_text = p.read_text(encoding="utf-8", errors="ignore")

    if ics_text:
        parsed = parse_ics_text(ics_text)
        if parsed:
            save_calendar_events(parsed)
            cfg["ical_url"] = target
            cfg["last_synced"] = time.strftime("%Y-%m-%d %I:%M %p")
            cfg["source"] = "Remote iCal / Google Calendar" if target.startswith("http") else "Local .ics File"
            cfg["count"] = len(parsed)
            save_calendar_config(cfg)
            return parsed

    return load_calendar_events()


# Reverse Engineering Tool
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
async def list_models():
    # OmniRoute Antigravity Models
    omni_models = [
        "alfred-antigravity",
        "antigravity/claude-sonnet-5-5-medium",
        "antigravity/claude-opus-5-5-high",
        "antigravity/gemini-3.8-flash-high",
        "antigravity/gemini-3-flash",
        "antigravity/gpt-oss-120b-medium"
    ]
    # Local Ollama Models
    ollama_models = ["qwen2.5:7b", "llama3.1:8b", "qwen2.5-coder:7b-instruct", "mistral:7b-instruct-v0.3-q4_K_M"]
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
            tags = [m["name"] for m in data.get("models", [])]
            if tags:
                ollama_models = tags
    except Exception:
        pass

    # Query live OmniRoute catalog
    if OMNIROUTE_KEY:
        try:
            req = urllib.request.Request(
                f"{OMNIROUTE_URL}/models",
                headers={"Authorization": f"Bearer {OMNIROUTE_KEY}"}
            )
            with urllib.request.urlopen(req, timeout=3) as r:
                data = json.loads(r.read().decode("utf-8"))
                found = [m.get("id") for m in data.get("data", []) if "antigravity" in m.get("id", "").lower() or "alfred" in m.get("id", "").lower()]
                if found:
                    priority_order = [
                        "antigravity/gemini-3-flash",
                        "antigravity/gemini-3.5-flash-lite",
                        "antigravity/gemini-3.8-flash-tiered",
                        "antigravity/gemini-3.8-flash-high",
                        "alfred-antigravity",
                        "antigravity/claude-sonnet-5-5-medium"
                    ]
                    ordered = [m for m in priority_order if m in found]
                    remaining = [m for m in found if m not in priority_order]
                    omni_models = ordered + remaining
        except Exception:
            pass

    all_models = omni_models + [m for m in ollama_models if m not in omni_models]
    return {
        "current_model": CURRENT_MODEL,
        "available_models": all_models,
        "omniroute_models": omni_models,
        "ollama_models": ollama_models
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
async def brain_get(name: str):
    p = BRAIN_DIR / f"{name}.md" if not name.endswith(".md") else BRAIN_DIR / name
    if p.exists():
        return {"name": p.stem, "content": p.read_text(encoding="utf-8")}
    raise HTTPException(status_code=404, detail="Note not found")


@app.post("/api/brain/save")
async def brain_save(data: dict):
    name = data.get("name", "note")
    content = data.get("content", "")
    filename = f"{name}.md" if not name.endswith(".md") else name
    p = BRAIN_DIR / filename
    p.write_text(content, encoding="utf-8")
    return {"success": True, "filename": filename}


@app.post("/api/brain/search")
async def brain_search(data: dict):
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
async def get_calendar():
    events = load_calendar_events()
    if not events:
        events = sync_calendar()
    return {"events": events, "config": load_calendar_config()}


@app.post("/api/calendar/sync")
async def api_sync_calendar(data: dict = None):
    url = data.get("url") if data else None
    events = sync_calendar(url)
    return {"success": True, "count": len(events), "events": events, "config": load_calendar_config()}


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


# Reverse Engineering
@app.post("/api/tools/reverse-engineer")
async def run_reverse_engineer(data: dict):
    url = data.get("url", "")
    if not url:
        raise HTTPException(status_code=400, detail="Missing target URL")
    res = reverse_engineer_website(url)
    return res


# Smart Home Device Hub & Endpoints
@app.get("/api/smart-home/devices")
async def get_smart_home_devices():
    return get_ha_devices_summary()


@app.post("/api/smart-home/control")
async def control_smart_home_device(data: dict):
    domain = data.get("domain", "light")
    service = data.get("service", "toggle")
    entity_id = data.get("entity_id", "")
    extra = data.get("extra", {})
    if not entity_id:
        raise HTTPException(status_code=400, detail="Missing entity_id")
    res = call_hass_device_control(domain, service, entity_id, extra)
    return res


@app.get("/api/smart-home/tv")
async def get_tv_status():
    return call_hass_tv("status")


@app.post("/api/smart-home/tv")
async def control_tv(data: dict):
    action = data.get("action", "toggle")
    return call_hass_tv(action)


# Voice Cloning Endpoint
@app.post("/api/voice/clone")
async def api_clone_voice(file: UploadFile = File(...), name: str = Form("Michael Caine"), voice_id: str = Form("michael_caine")):
    tmp_input = AUDIO_CACHE / f"upload_{uuid.uuid4().hex[:8]}{Path(file.filename).suffix}"
    content_bytes = await file.read()
    tmp_input.write_bytes(content_bytes)
    
    ref_wav = AUDIO_CACHE / f"ref_{voice_id}.wav"
    ffmpeg = "/opt/homebrew/bin/ffmpeg" if Path("/opt/homebrew/bin/ffmpeg").exists() else "ffmpeg"
    cmd = [ffmpeg, "-y", "-i", str(tmp_input), "-t", "6", "-vn", "-acodec", "pcm_s16le", "-ar", "24000", "-ac", "1", str(ref_wav)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise HTTPException(status_code=400, detail=f"FFmpeg extraction failed: {res.stderr}")
        
    # Transcribe
    transcript = "Some men just want to watch the world burn, Master Wayne."
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
                save_custom_voice(voice_id, profile_id, name)
                return {"success": True, "voice_id": voice_id, "profile_id": profile_id, "name": name, "transcript": transcript}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"VoiceStudio cloning registration error: {str(e)}")
        
    raise HTTPException(status_code=500, detail="Voice profile creation failed")


# Chat Core with AI and Tools
@app.post("/api/chat")
async def chat_endpoint(data: dict):
    global CURRENT_MODEL, CURRENT_VOICE
    user_message = data.get("message", "").strip()
    if not user_message:
        return {"reply": "I am standing by, Master Wayne. All Batcave telemetry links are online.", "audio_url": None}

    req_model = data.get("model")
    req_voice = data.get("voice")
    active_model = req_model or CURRENT_MODEL
    active_voice = req_voice or CURRENT_VOICE

    lower = user_message.lower()
    
    # 1. Voice switching trigger
    if "switch voice to" in lower or "change voice to" in lower:
        for v in ["daniel", "alloy", "echo", "fable", "samantha", "oliver"]:
            if v in lower:
                CURRENT_VOICE = v
                reply = f"Voice profile successfully transferred to {v.capitalize()}. How may I assist you now, sir?"
                audio_url = synthesize_voice(reply, CURRENT_VOICE)
                return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "action": {"voice": CURRENT_VOICE}}

    # 2. Model switching trigger
    if "switch model to" in lower or "switch brain to" in lower:
        if "antigravity" in lower or "alfred" in lower:
            CURRENT_MODEL = "alfred-antigravity"
            reply = "Neural processing core successfully linked to OmniRoute Antigravity brain. Awaiting directives, Master Wayne."
            audio_url = synthesize_voice(reply)
            return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "action": {"model": CURRENT_MODEL}}
        for m in ["alfred-antigravity", "antigravity/claude-sonnet-5-5-medium", "antigravity/claude-opus-5-5-high", "antigravity/gemini-3.8-flash-high", "qwen2.5:7b", "llama3.1:8b", "qwen2.5-coder:7b-instruct", "mistral:7b-instruct-v0.3-q4_K_M"]:
            if m.split(":")[0] in lower or m.lower() in lower:
                CURRENT_MODEL = m
                reply = f"Neural processing core switched to {m}. Awaiting directives, Master Wayne."
                audio_url = synthesize_voice(reply)
                return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "action": {"model": CURRENT_MODEL}}

    # 3. Comprehensive Home Assistant Directives
    # TV Directives
    if any(q in lower for q in ["turn on the tv", "turn on tv", "power on tv", "prende la tv", "encender tv"]):
        call_hass_tv("turn_on")
        reply = "Powering on the Sony BRAVIA 85-inch display in the living room, Master Wayne."
        audio_url = synthesize_voice(reply)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "action": {"device": "tv", "state": "on"}}

    if any(q in lower for q in ["turn off the tv", "turn off tv", "power off tv", "apaga la tv", "apagar tv"]):
        call_hass_tv("turn_off")
        reply = "Living room Sony BRAVIA television powered down, sir."
        audio_url = synthesize_voice(reply)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "action": {"device": "tv", "state": "off"}}

    # Smart Home Directives (Lights, TV, Fans)
    clean_lower = lower.strip()
    if clean_lower.startswith("alfred"): clean_lower = clean_lower[6:].strip(" ,.!?")
    if clean_lower.startswith("hey alfred"): clean_lower = clean_lower[10:].strip(" ,.!?")
    
    is_question = any(q in clean_lower for q in ["why ", "why'd", "cant ", "can't ", "cannot ", "doesnt ", "doesn't ", "how to ", "how do "])

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
                    reply = "All quarters secured. Every lighting array has been extinguished, Master Wayne."
                    target = "all_lights"
                elif is_lr:
                    target_ent = "light.home_theater" if ("theater" in clean_lower or "theatre" in clean_lower) else "light.luz_sala"
                    call_hass_device_control("light", "turn_off", target_ent)
                    reply = "Living room lighting extinguished, sir."
                    target = target_ent
                else:
                    call_hass_device_control("light", "turn_off", "light.dimmer_cuarto_jm")
                    reply = "Your bedroom lighting has been extinguished, Master Wayne."
                    target = "light.dimmer_cuarto_jm"
                
                audio_url = synthesize_voice(reply, active_voice)
                return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "action": {"device": target, "state": "off"}}

            if has_on or "on" in clean_lower or "prende" in clean_lower:
                if is_all:
                    call_hass_device_control("light", "turn_on", "light.luz_sala")
                    call_hass_device_control("light", "turn_on", "light.dimmer_cuarto_jm")
                    reply = "All perimeter and quarter lights illuminated, sir."
                    target = "all_lights"
                elif is_lr:
                    target_ent = "light.home_theater" if ("theater" in clean_lower or "theatre" in clean_lower) else "light.luz_sala"
                    call_hass_device_control("light", "turn_on", target_ent)
                    reply = "Living room illumination active, sir."
                    target = target_ent
                else:
                    call_hass_device_control("light", "turn_on", "light.dimmer_cuarto_jm")
                    reply = "Illuminating your bedroom quarters, Master Wayne."
                    target = "light.dimmer_cuarto_jm"

                audio_url = synthesize_voice(reply, active_voice)
                return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "action": {"device": target, "state": "on"}}

    # Thermostat / Climate Queries
    if any(q in lower for q in ["what's the temperature", "what is the temperature", "thermostat status", "how warm is it", "how cold is it", "temperatura"]):
        ha_data = get_ha_devices_summary()
        t = ha_data.get("thermostat", {})
        temp_f = t.get("temp_f", 72.5)
        hum = t.get("humidity", 62)
        act = t.get("action", "cooling")
        reply = f"Indoor temperature is {temp_f} degrees Fahrenheit with {hum}% humidity, and HVAC is actively {act}, sir."
        audio_url = synthesize_voice(reply)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "thermostat": t}

    # Fans
    if any(q in lower for q in ["turn on fan", "turn on the fan"]):
        call_hass_device_control("switch", "turn_on", "switch.ventilador_mau")
        reply = "Circulation fan activated, sir."
        audio_url = synthesize_voice(reply)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url}

    if any(q in lower for q in ["turn off fan", "turn off the fan"]):
        call_hass_device_control("switch", "turn_off", "switch.ventilador_mau")
        call_hass_device_control("switch", "turn_off", "switch.ventilador_papas")
        reply = "Fans powered down, sir."
        audio_url = synthesize_voice(reply)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url}

    # 4. Reverse Engineer Command
    if lower.startswith("reverse engineer ") or "reverse engineer https" in lower:
        m = re.search(r'https?://[^\s]+', user_message)
        if m:
            target_url = m.group(0)
            res = reverse_engineer_website(target_url)
            reply = f"Teardown complete for {target_url}. Primary stack detected: {', '.join(res.get('tech_stack', []))}. I have rendered the architectural blueprint in the Reverse Engineer drawer, sir."
            audio_url = synthesize_voice(reply)
            return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "reverse_engineer_data": res}

    # 5. Calendar Query
    if "what's on my schedule" in lower or "today's calendar" in lower or "my agenda" in lower:
        events = load_calendar_events()
        if events:
            agenda_str = ", ".join([f"{e['title']} at {e['time']}" for e in events[:3]])
            reply = f"You have {len(events)} objectives scheduled today, Master Wayne: {agenda_str}."
        else:
            reply = "Your tactical schedule is currently clear for the remainder of the day, sir."
        audio_url = synthesize_voice(reply)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url}

    # 6. Memory Commands & Directives
    if "clear history" in lower or "clear memory" in lower or "reset conversation" in lower or "forget our conversation" in lower:
        clear_chat_history()
        reply = "Active conversation buffer purged, Master Wayne. Long-term memory vaults remain intact."
        audio_url = synthesize_voice(reply)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "action": {"memory": "cleared_history"}}

    rem_match = re.search(r'(?:please\s+)?(?:remember\s+that|remember|take\s+note\s+that|memorize\s+that)\s+(.+)', user_message, re.IGNORECASE)
    if rem_match:
        fact = rem_match.group(1).strip()
        add_memory_fact(fact, category="User Fact")
        reply = f"Committed to long-term memory, Master Wayne: '{fact[:60]}'."
        audio_url = synthesize_voice(reply)
        # record turn
        hist = load_chat_history()
        hist.append({"role": "user", "content": user_message})
        hist.append({"role": "assistant", "content": reply})
        save_chat_history(hist)
        return {"user_message": user_message, "reply": reply, "audio_url": audio_url, "action": {"memory": "added", "fact": fact}}

    events = load_calendar_events()
    calendar_summary = "\n".join([f"- {e.get('time')} - {e.get('title')} ({e.get('priority')}) at {e.get('location')}" for e in events]) if events else "No events scheduled."

    # 7. General Reasoning via OmniRoute or Local Ollama
    req_model = data.get("model")
    req_voice = data.get("voice")
    active_model = req_model or CURRENT_MODEL
    active_voice = req_voice or CURRENT_VOICE

    engine_desc = f"OmniRoute Antigravity Neural Gateway ({active_model})" if is_omniroute_model(active_model) else f"Local Ollama Core ({active_model})"
    system_prompt = f"""You are Alfred Pennyworth, loyal British butler to Bruce Wayne (Batman) and tactical AI orchestrator of the Batcomputer.
You speak with refined British dry wit, utmost loyalty, and razor-sharp intellect.
CRITICAL CONSTRAINT: Keep replies under 25 words in 1-2 punchy sentences for high-speed voice delivery. No preamble or fluff.
Current time: {time.strftime('%I:%M %p')}.
Host: Apple Silicon M1 Mac Mini, 8GB Unified RAM, macOS.
Active Neural Brain: {engine_desc}.

Persistent Long-Term Memories:
{format_memories_for_prompt()}

Today's Schedule / Tactical Calendar:
{calendar_summary}

Second Brain Vault:
{get_second_brain_summary()[:800]}"""

    history = load_chat_history()
    messages = [{"role": "system", "content": system_prompt}] + history[-8:] + [{"role": "user", "content": user_message}]
    
    reply = query_brain(messages, active_model)
    audio_url = synthesize_voice(reply, active_voice)

    # Persist rolling conversation turns
    history.append({"role": "user", "content": user_message})
    history.append({"role": "assistant", "content": reply})
    save_chat_history(history)
    
    return {
        "user_message": user_message,
        "reply": reply,
        "audio_url": audio_url
    }


@app.post("/api/listen")
async def listen_endpoint(
    file: UploadFile = File(...),
    voice: Optional[str] = Form(None),
    model: Optional[str] = Form(None)
):
    global CURRENT_VOICE, CURRENT_MODEL
    audio_bytes = await file.read()
    transcription = transcribe_audio(audio_bytes, file.filename or "recording.wav")
    
    if not transcription or not transcription.strip():
        transcription = "Status report"
        
    active_voice = voice or CURRENT_VOICE
    active_model = model or CURRENT_MODEL
    
    chat_result = await chat_endpoint({
        "message": transcription,
        "voice": active_voice,
        "model": active_model
    })
    chat_result["transcription"] = transcription
    return chat_result


@app.api_route("/api/audio/{filename}", methods=["GET", "HEAD"])
async def get_audio(filename: str):
    file_path = AUDIO_CACHE / filename
    if file_path.exists():
        return FileResponse(str(file_path), media_type="audio/mpeg")
    raise HTTPException(status_code=404, detail="Audio file not found")


if __name__ == "__main__":
    uvicorn.run("server:app", host="127.0.0.1", port=7776, reload=False)
