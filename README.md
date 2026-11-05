# 🦇 Alfred Batcomputer: Cybernetic Voice Butler & Tactical HUD

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Windows%20%7C%20Linux%20%7C%20PWA-cyan.svg)](#client-installers)
[![License](https://img.shields.io/badge/Security-Wayne%20Enterprises-00e5ff.svg)](README.md)

An end-to-end, high-intelligence tactical AI butler system and sci-fi Batcomputer HUD designed for Wayne Enterprises tactical operations and smart-home command. Built with Wayne Tech AI architecture and Arkham Tech styling.

![Batcomputer HUD](static/batcomputer_wakeword_hud.png)

---

## ⚡ Quick Start: 1-Click Client Installers

The Batcomputer client companion can run on **macOS**, **Windows**, **Linux**, or as a **Progressive Web App (PWA)** on iPhone/iPad and Android.

Choose your operating system below for turnkey installation:

### 🍏 macOS (Apple Silicon M1/M2/M3/M4 & Intel)

1. Clone or download this repository:
   ```bash
   git clone https://github.com/Juanmanuelrd17/alfred-batcomputer.git
   cd alfred-batcomputer
   ```
2. **Double-click** `install-macos.command` in macOS Finder (or run in Terminal):
   ```bash
   ./install-macos.sh
   ```
3. **What happens automatically:**
   - Detects your Mac hardware architecture (Apple Silicon vs Intel).
   - Validates or installs Python 3.10+ via Homebrew.
   - Configures an isolated `.venv` and installs all audio and GUI dependencies (`PyQt6`, `sounddevice`, `fastapi`, `requests`).
   - Generates a `Launch-Batcomputer.command` shortcut on your **Desktop**.
   - *(Optional)* Prompts to install a LaunchAgent for automatic boot on macOS login.
4. **To launch:** Double-click `Launch-Batcomputer.command` on your Desktop!

> [!TIP]
> If macOS displays an *"unidentified developer"* prompt when opening `.command` for the first time, simply right-click `install-macos.command` in Finder and select **Open**.

---

### 🪟 Windows 10 & 11

1. Clone or download this repository:
   ```powershell
   git clone https://github.com/Juanmanuelrd17/alfred-batcomputer.git
   cd alfred-batcomputer
   ```
2. **Right-click** `install-windows.bat` and select **Run as administrator** (or execute in PowerShell):
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
   ```
3. **What happens automatically:**
   - Automatically bootstraps Python 3.11 via Windows `winget` if not detected.
   - Creates an isolated `.venv` virtual environment.
   - Installs client companion dependencies (`PyQt6`, `PyQt6-WebEngine`, `sounddevice`, `requests`).
   - Generates a **Batcomputer Companion** shortcut on your Windows **Desktop**.
   - *(Optional)* Offers to register the companion into your Windows Startup folder (`shell:startup`).
4. **To launch:** Double-click the **Batcomputer Companion** icon on your Desktop or run `launch-alfred.bat`!

---

### 🐧 Linux (Arch / Hyprland, Ubuntu, Debian, Fedora)

1. Clone this repository:
   ```bash
   git clone https://github.com/Juanmanuelrd17/alfred-batcomputer.git
   cd alfred-batcomputer
   ```
2. Run the turnkey installer:
   ```bash
   chmod +x install-linux.sh
   ./install-linux.sh
   ```
3. **What happens automatically:**
   - Verifies system Python 3.10+ and audio subsystem (PulseAudio/PipeWire).
   - Creates an isolated `.venv` with all necessary packages.
   - Generates a desktop entry in `~/.local/share/applications/batcomputer.desktop` for your application launcher (Rofi, Wofi, GNOME, KDE).
   - *(Optional)* Configures and enables a systemd user daemon (`batcomputer-desktop.service`).
4. **To launch:** Run `./launch-alfred.sh` or find **Batcomputer Companion** in your app launcher!

---

### 📱 iOS & Android (Universal PWA)

1. Open your Batcomputer server URL in Safari (iOS) or Chrome (Android):
   ```text
   https://<your-batcomputer-host>:7777
   ```
2. **iOS (Safari):** Tap the **Share** button (`⎋`) → Select **"Add to Home Screen"**.
3. **Android (Chrome):** Tap the **Menu** (`⋮`) → Select **"Install app"** or **"Add to Home screen"**.
4. Enjoy a full-screen, standalone cybernetic interface with local offline caching and touch controls.

---

## 🖥️ Server Deployment & Hosting

The Batcomputer server orchestrates the FastAPI REST API, brain routing, TTS/STT, smart home integrations, and the web interface.

### Prerequisites
* **OS:** macOS (Apple Silicon recommended for neural voice) or Linux (Ubuntu, Debian, Arch).
* **Python:** 3.10 or higher.
* **Node.js:** v18+ (required only if running the `proxy.js` gateway).

### Step-by-Step Server Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Juanmanuelrd17/alfred-batcomputer.git
   cd alfred-batcomputer
   ```

2. **Create Python virtual environment & install requirements:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. **Configure Authentication:**
   Copy the sample authentication configuration:
   ```bash
   cp auth_config.example.json auth_config.json
   ```
   Edit `auth_config.json` to configure your security credentials:
   ```json
   {
     "auth_enabled": true,
     "username": "admin",
     "passcode": "batcomputer2026",
     "salt": "wayne_tech_salt_2026",
     "session_lifetime_sec": 2592000
   }
   ```
   *(Note: `auth_config.json` is protected and ignored by `.gitignore` so your private credentials are never committed).*

4. **Launch the Server:**
   ```bash
   # Terminal 1: Start the FastAPI Backend (Port 7776)
   python3 server.py

   # Terminal 2 (Optional): Start the Reverse Proxy / HTTPS Gateway (Port 7777)
   node proxy.js
   ```

---

## 🔗 Connecting Clients to Your Server

By default, companion clients connect to `https://batcomputer.jmrdserver.duckdns.org`. To connect your client to your own local or remote server:

```bash
# Point to your LAN server:
./launch-alfred.sh --server "http://192.168.1.100:7776"

# Or on Windows:
.\launch-alfred.bat --server "http://192.168.1.100:7776"

# Or on macOS:
./Launch-Batcomputer.command --server "http://192.168.1.100:7776"
```

---

## ⌨️ Tactical Controls & Hotkeys

| Shortcut / Trigger | Action | Target Environment |
| :--- | :--- | :--- |
| **`Super + D`** | Auto-wake / sleep fullscreen desktop HUD | Linux / Hyprland |
| **`Super + W`** | Instant quick-question companion window pop-up | Linux / Hyprland |
| **`"Alfred"` or `"Computer"`** | Passive voice wake-word activation | Browser / Companion HUD |
| **Wake Word Button** | Toggle live acoustic wake-word listener | Bottom HUD Bar |
| **Microphone Button** | Push-to-talk audio transcription | Top Right Header |

---

## 🌐 System Topology & Ports

| Port | Service | Role |
| :--- | :--- | :--- |
| **`7776`** | FastAPI Backend | Python REST API, LLM Brain Dispatch, TTS/STT Orchestration |
| **`7777`** | Node.js Gateway Proxy | Dual HTTPS (Tailscale/DuckDNS TLS) & LAN HTTP Reverse Proxy |
| **`7778`** | Local IPC Daemon | Inter-process communication for desktop companion hotkeys |
| **`3900`** | VoiceStudio Backend | Local faster-whisper ASR & Kokoro neural voice synthesis |
| **`11434`**| Ollama Daemon | Local air-gapped LLM inference (`qwen2.5:7b`) |
| **`20128`**| OmniRoute Gateway | Hosted frontier model proxy |

---

## 🧠 Core Capabilities

- **Arkham Cybernetic HUD:** High-density neon cyan GUI featuring live Apple Silicon telemetry gauges, Web Audio oscilloscope visualizer, CRT scanlines, and centered glowing Arkham Bat-insignia watermark.
- **Always-On Hands-Free Wake Word (`"Alfred"` / `"Computer"`):** Passive speech recognition engine. Say *"Alfred, status report"* or *"Computer, dim the lights"* to trigger instant command execution.
- **Dual Voice Architecture:**
  - **Michael Caine (Zero-Shot Neural Clone):** VoiceStudio diffusion voice model trained on *The Dark Knight* audio profiles.
  - **Daniel (Native macOS British Butler):** Sub-second (0.4s) ultra-low-latency voice engine for instant interactive conversation.
- **Dual-Tier Brain Routing:**
  - **OmniRoute Frontier Gateway:** High-intelligence routing (`antigravity/gemini-3-flash`, `antigravity/gemini-3.5-flash-lite`, `alfred-antigravity`).
  - **Local Ollama Daemon:** Air-gapped fallback running `qwen2.5:7b`.
- **Integrated Smart Home Control:** Native Home Assistant integration with proactive intent dispatching:
  - Smart Lights (JM Bedroom Dimmer, Living Room, Home Theater, All Quarters).
  - Sony BRAVIA XR-85X90L 4K TV power and status control.
  - Server Room Climate & Thermostat telemetry.
- **Proactive Tactical Briefings:**
  - **Morning Briefing:** Auto-synthesizes weather, priority calendar agenda, system health, and headlines.
  - **Bedtime Routine:** Summary of completed tasks and debriefing.
  - **Calendar Countdown Watchdog:** Alerts you proactively 10 minutes prior to scheduled calendar engagements.
- **Dual-Tier Memory Vault:**
  - Dynamic 8-turn rolling conversational context buffer (`chat_history.json`).
  - Persistent factual memory bank (`memory.json`) with voice-directive auto-learning (*"Alfred, remember that..."*).
- **Tactical Calendar & ICS Ingestion:** Real-time agenda parser and `.ics` file synchronizer with recurring event (`RRULE`) support.

---

## 🔒 Security & Privacy

* **Zero Hardcoded Secrets:** Passwords, API keys, and private tokens are isolated into `auth_config.json`, which is permanently excluded via `.gitignore`.
* **Zero-Touch Idle Compute:** When the desktop HUD is tucked away or covered by applications, its render and animation loops drop to 0% CPU consumption.
* **Air-Gapped Compatibility:** Can operate completely offline using local Ollama and local Whisper models.

---

*Wayne Enterprises // Applied Sciences Division*
