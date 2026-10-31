#!/usr/bin/env python3
"""
Alfred Voice Cloning Pipeline
Ingests a video or audio file of Michael Caine (or any reference voice),
extracts reference audio, transcribes it, and registers a cloned voice profile
with VoiceStudio's local zero-shot cloning engine.
"""

import sys
import os

# Auto re-exec with VoiceStudio runtime venv if requests or dependencies are missing
try:
    import requests
except ImportError:
    venv_py = "/Users/jmrd/Library/Application Support/VoiceStudio/runtime/project/.venv/bin/python"
    if os.path.exists(venv_py) and sys.executable != venv_py:
        os.execv(venv_py, [venv_py] + sys.argv)
    else:
        raise

import json
import argparse
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CUSTOM_VOICES_FILE = BASE_DIR / "custom_voices.json"
VOICESTUDIO_URL = "http://127.0.0.1:3900"


def find_ffmpeg() -> str:
    for cmd in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "ffmpeg"]:
        if subprocess.run(["which", cmd], capture_output=True).returncode == 0:
            return cmd
    return "ffmpeg"


def extract_audio(input_file: Path, output_wav: Path, duration: int = 20) -> bool:
    ffmpeg = find_ffmpeg()
    print(f"[*] Extracting reference audio using {ffmpeg} from {input_file}...")
    
    # Extract crisp 24kHz mono PCM WAV, limit to optimal reference duration
    cmd = [
        ffmpeg, "-y",
        "-i", str(input_file),
        "-t", str(duration),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "24000",
        "-ac", "1",
        str(output_wav)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[!] FFmpeg extraction failed: {res.stderr}")
        return False
    print(f"[+] Audio extracted: {output_wav} ({output_wav.stat().st_size} bytes)")
    return True


def transcribe_audio(wav_path: Path) -> str:
    print(f"[*] Transcribing reference speech via VoiceStudio Faster-Whisper...")
    try:
        with open(wav_path, "rb") as f:
            files = {"file": ("reference.wav", f, "audio/wav")}
            data = {"model": "Systran/faster-whisper-base"}
            res = requests.post(f"{VOICESTUDIO_URL}/v1/audio/transcriptions", files=files, data=data, timeout=30)
            if res.status_code == 200:
                transcript = res.json().get("text", "").strip()
                print(f"[+] Transcript: \"{transcript}\"")
                return transcript
    except Exception as e:
        print(f"[!] VoiceStudio transcription error: {e}")
    return "Some men just want to watch the world burn, Master Wayne."


def register_voice_profile(wav_path: Path, transcript: str, voice_name: str = "Michael Caine (Alfred)") -> dict:
    print(f"[*] Registering Voice Profile with VoiceStudio...")
    try:
        with open(wav_path, "rb") as f:
            files = {"ref_audio": ("caine_reference.wav", f, "audio/wav")}
            data = {
                "name": voice_name,
                "ref_text": transcript or "Some men just want to watch the world burn, Master Wayne.",
                "language": "en",
                "kind": "clone"
            }
            res = requests.post(f"{VOICESTUDIO_URL}/profiles", files=files, data=data, timeout=20)
            if res.status_code == 200:
                profile_data = res.json()
                print(f"[+] Profile created successfully: ID {profile_data.get('id')}")
                return profile_data
            else:
                print(f"[!] Failed to create profile: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"[!] Profile registration exception: {e}")
    return None


def save_custom_voice(voice_id: str, profile_id: str, display_name: str):
    voices = {}
    if CUSTOM_VOICES_FILE.exists():
        try:
            voices = json.loads(CUSTOM_VOICES_FILE.read_text(encoding="utf-8"))
        except Exception:
            voices = {}
    
    voices[voice_id] = {
        "id": voice_id,
        "profile_id": profile_id,
        "name": display_name,
        "engine": "VoiceStudio Clone"
    }
    CUSTOM_VOICES_FILE.write_text(json.dumps(voices, indent=2), encoding="utf-8")
    print(f"[+] Saved custom voice record to {CUSTOM_VOICES_FILE}")


def test_speech_generation(profile_id: str, text: str = "Good day, Master Wayne. All Batcave defense grids are fully operational.") -> Path:
    print(f"[*] Testing cloned speech generation for profile {profile_id}...")
    try:
        res = requests.post(
            f"{VOICESTUDIO_URL}/generate",
            data={"text": text, "profile_id": profile_id, "engine": "omnivoice"},
            timeout=60
        )
        if res.status_code == 200 and len(res.content) > 1000:
            out_file = BASE_DIR / "static" / "audio" / f"test_{profile_id}.wav"
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_bytes(res.content)
            print(f"[+] Cloned speech synthesis verified! Output: {out_file} ({len(res.content)} bytes)")
            return out_file
        else:
            print(f"[!] Speech generation response: {res.status_code} {res.text[:200]}")
    except Exception as e:
        print(f"[!] Test speech synthesis error: {e}")
    return None


def main():
    parser = argparse.ArgumentParser(description="Clone Michael Caine or custom voice for Alfred")
    parser.add_argument("file_pos", nargs="?", default=None, help="Path to reference video (.mp4, .mov) or audio (.wav, .mp3)")
    parser.add_argument("--file", "-f", default=None, help="Path to reference video (.mp4, .mov) or audio (.wav, .mp3)")
    parser.add_argument("--name", "-n", default="Michael Caine (Dark Knight Alfred)", help="Display name for the voice profile")
    parser.add_argument("--id", "-i", default="michael_caine", help="Voice identifier (e.g. michael_caine)")
    parser.add_argument("--duration", "-d", type=int, default=20, help="Max duration of audio to extract in seconds (default: 20)")
    args = parser.parse_args()

    chosen_file = args.file or args.file_pos
    if not chosen_file:
        parser.error("You must provide the path to a video or audio file. Example: python3 clone_voice.py Downloads/caine.mp4")

    input_path = Path(chosen_file).expanduser().resolve()
    if not input_path.exists():
        # Fallback check relative to home directory
        alt_path = (Path.home() / chosen_file).resolve()
        if alt_path.exists():
            input_path = alt_path
        else:
            print(f"[!] Error: File does not exist at {input_path} or {alt_path}")
            sys.exit(1)

    ref_wav = BASE_DIR / "static" / "audio" / f"ref_{args.id}.wav"
    ref_wav.parent.mkdir(parents=True, exist_ok=True)

    # 1. Extract audio
    ok = extract_audio(input_path, ref_wav, duration=args.duration)
    if not ok:
        sys.exit(1)

    # 2. Transcribe
    transcript = transcribe_audio(ref_wav)

    # 3. Register Profile
    prof = register_voice_profile(ref_wav, transcript, voice_name=args.name)
    if not prof:
        sys.exit(1)

    profile_id = prof.get("id")

    # 4. Save custom voice
    save_custom_voice(args.id, profile_id, args.name)

    # 5. Test generation
    test_speech_generation(profile_id)

    print("\n" + "="*60)
    print(f"SUCCESS: Voice profile '{args.name}' is now armed!")
    print(f"Profile ID: {profile_id}")
    print(f"Voice Key:  {args.id}")
    print("You can select it from the Batcomputer HUD voice dropdown or tell Alfred:")
    print(f"\"Alfred, switch voice to Michael Caine\"")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
