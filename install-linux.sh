#!/usr/bin/env bash
# ==============================================================================
# WAYNE ENTERPRISES // BATCOMPUTER BCv1.2 LINUX INSTALLER
# Native Voice Companion & Tactical HUD Installer for Linux (Wayland & X11)
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "================================================================="
echo "🦇 WAYNE ENTERPRISES // BATCOMPUTER LINUX INSTALLATION PROTOCOL"
echo "================================================================="
echo ""

ARCH="$(uname -m)"
OS_NAME="Linux"
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS_NAME="$PRETTY_NAME"
fi
echo "📡 System: $OS_NAME ($ARCH)"
echo "🖥️  Session: ${XDG_SESSION_TYPE:-unknown} (${XDG_CURRENT_DESKTOP:-unknown})"

# 1. Verify Python 3.10+
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PY_MAJOR="$(python3 -c 'import sys; print(sys.version_info.major)')"
    PY_MINOR="$(python3 -c 'import sys; print(sys.version_info.minor)')"
    if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 10 ]; then
        PYTHON_BIN="$(command -v python3)"
        echo "✓ Detected Python ${PY_MAJOR}.${PY_MINOR} at $PYTHON_BIN"
    fi
fi

if [ -z "$PYTHON_BIN" ]; then
    echo "❌ Python 3.10+ is required but was not found."
    echo "   Please install Python 3.10 or higher using your package manager:"
    echo "   - Arch:   sudo pacman -S python python-pip"
    echo "   - Ubuntu: sudo apt install python3 python3-pip python3-venv"
    echo "   - Fedora: sudo dnf install python3 python3-pip"
    exit 1
fi

# 2. Setup Virtual Environment
echo ""
echo "⚙️  [1/4] Initializing Wayne Tech Virtual Environment..."
VENV_DIR="$DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR" --system-site-packages || "$PYTHON_BIN" -m venv "$VENV_DIR"
    echo "✓ Created virtual environment at $VENV_DIR"
else
    echo "✓ Using existing virtual environment at $VENV_DIR"
fi

VENV_PIP="$VENV_DIR/bin/pip"
VENV_PY="$VENV_DIR/bin/python3"

# 3. Install Dependencies
echo ""
echo "📦 [2/4] Installing Python dependencies..."
"$VENV_PIP" install --upgrade pip --quiet 2>/dev/null || true
"$VENV_PIP" install -r "$DIR/requirements.txt" --quiet

# 4. Make scripts executable
chmod +x "$DIR"/*.sh 2>/dev/null || true
chmod +x "$DIR"/*.py 2>/dev/null || true

# 5. Create Desktop Menu Entry (.desktop)
echo ""
echo "🚀 [3/4] Generating Linux Desktop Launcher..."
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$APPS_DIR"
DESKTOP_FILE="$APPS_DIR/batcomputer.desktop"

cat << EOF > "$DESKTOP_FILE"
[Desktop Entry]
Name=Batcomputer Companion
Comment=Wayne Tech BCv1.2 Tactical Voice Companion & HUD
Exec=$DIR/launch-alfred.sh
Icon=utilities-terminal
Terminal=false
Type=Application
Categories=Utility;AudioVideo;
Keywords=batcomputer;alfred;companion;hud;
EOF

chmod +x "$DESKTOP_FILE"
echo "✓ Installed application launcher at $DESKTOP_FILE"

# 6. Optional: Setup systemd user service for auto-start
echo ""
echo "⚡ [4/4] Auto-Start Configuration"
read -r -p "Would you like Batcomputer to start automatically as a background user service? (y/N): " AUTO_START || AUTO_START="n"

if [[ "$AUTO_START" =~ ^[Yy]$ ]]; then
    SYSTEMD_DIR="$HOME/.config/systemd/user"
    mkdir -p "$SYSTEMD_DIR"
    SERVICE_FILE="$SYSTEMD_DIR/batcomputer-desktop.service"

    cat << EOF > "$SERVICE_FILE"
[Unit]
Description=Batcomputer BCv1.2 Desktop HUD & Voice Companion Daemon
Documentation=https://batcomputer.jmrdserver.duckdns.org
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$DIR
ExecStart=$DIR/launch-alfred.sh --daemon
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=QT_QPA_PLATFORM=wayland

[Install]
WantedBy=graphical-session.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable batcomputer-desktop.service
    echo "✓ Enabled batcomputer-desktop.service for user $(whoami)"
    echo "  To start immediately: systemctl --user start batcomputer-desktop.service"
fi

echo ""
echo "================================================================="
echo "🦇 INSTALLATION COMPLETE"
echo "================================================================="
echo "Launch Batcomputer anytime with:"
echo "  $DIR/launch-alfred.sh"
echo "Or find 'Batcomputer Companion' in your application menu."
echo "================================================================="
