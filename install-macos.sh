#!/usr/bin/env bash
# ==============================================================================
# WAYNE ENTERPRISES // BATCOMPUTER BCv1.2 MACOS INSTALLER
# Native Voice Companion & Tactical HUD Installer for Apple Silicon & Intel Macs
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "================================================================="
echo "🦇 WAYNE ENTERPRISES // BATCOMPUTER MACOS INSTALLATION PROTOCOL"
echo "================================================================="
echo ""

ARCH="$(uname -m)"
echo "📡 Hardware Architecture: $ARCH ($(sw_vers -productName 2>/dev/null || echo 'macOS') $(sw_vers -productVersion 2>/dev/null || echo ''))"

# 1. Verify Homebrew / Python 3
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    PY_MAJOR="$(python3 -c 'import sys; print(sys.version_info.major)')"
    PY_MINOR="$(python3 -c 'import sys; print(sys.version_info.minor)')"
    if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 10 ]; then
        PYTHON_BIN="$(command -v python3)"
        echo "✓ Detected Python $PY_VER at $PYTHON_BIN"
    fi
fi

if [ -z "$PYTHON_BIN" ]; then
    echo "⚠️  Python 3.10+ not detected or older than 3.10."
    if command -v brew >/dev/null 2>&1; then
        echo "📦 Installing Python via Homebrew..."
        brew install python@3.11
        PYTHON_BIN="$(brew --prefix python@3.11)/bin/python3.11"
    else
        echo "❌ Homebrew is not installed. Please install Homebrew from https://brew.sh or download Python 3.11+ from https://python.org"
        exit 1
    fi
fi

# 2. Setup Virtual Environment
echo ""
echo "⚙️  [1/3] Initializing Wayne Tech Virtual Environment..."
VENV_DIR="$DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    echo "✓ Created isolated environment in $VENV_DIR"
else
    echo "✓ Using existing virtual environment at $VENV_DIR"
fi

VENV_PY="$VENV_DIR/bin/python3"
VENV_PIP="$VENV_DIR/bin/pip"

# 3. Install Dependencies
echo ""
echo "📦 [2/3] Installing Python dependencies (PyQt6, sounddevice, requests, fastapi)..."
"$VENV_PIP" install --upgrade pip --quiet
"$VENV_PIP" install -r "$DIR/requirements.txt" --quiet

# 4. Make scripts executable
chmod +x "$DIR"/*.sh 2>/dev/null || true
chmod +x "$DIR"/*.py 2>/dev/null || true

# 5. Create Desktop Launcher (.command)
echo ""
echo "🚀 [3/3] Generating macOS Finder Launchers..."
LAUNCHER="$DIR/Launch-Batcomputer.command"
cat << 'EOF' > "$LAUNCHER"
#!/usr/bin/env bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
if [ -f "$DIR/.venv/bin/python3" ]; then
    exec "$DIR/.venv/bin/python3" "$DIR/alfred_daemon.py" --server "https://batcomputer.jmrdserver.duckdns.org" --hud-url "https://batcomputer.jmrdserver.duckdns.org" "$@"
else
    exec python3 "$DIR/alfred_daemon.py" --server "https://batcomputer.jmrdserver.duckdns.org" --hud-url "https://batcomputer.jmrdserver.duckdns.org" "$@"
fi
EOF
chmod +x "$LAUNCHER"

# Optionally place shortcut on user's Desktop
if [ -d "$HOME/Desktop" ]; then
    cp -f "$LAUNCHER" "$HOME/Desktop/Launch-Batcomputer.command" 2>/dev/null || true
    echo "✓ Placed 'Launch-Batcomputer.command' on your Desktop."
fi

# 6. Optional: Create LaunchAgent for Auto-Start on Login
read -r -p "⚡ Would you like Batcomputer to start automatically upon macOS login? (y/N): " AUTO_START || AUTO_START="n"
if [[ "$AUTO_START" =~ ^[Yy]$ ]]; then
    AGENT_PLIST="$HOME/Library/LaunchAgents/com.wayne.batcomputer.plist"
    mkdir -p "$HOME/Library/LaunchAgents"
    cat << EOF > "$AGENT_PLIST"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.wayne.batcomputer</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_PY</string>
        <string>$DIR/alfred_daemon.py</string>
        <string>--server</string>
        <string>https://batcomputer.jmrdserver.duckdns.org</string>
        <string>--hud-url</string>
        <string>https://batcomputer.jmrdserver.duckdns.org</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>$DIR</string>
    <key>StandardOutPath</key>
    <string>$HOME/Library/Logs/batcomputer.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/Library/Logs/batcomputer-err.log</string>
</dict>
</plist>
EOF
    launchctl unload "$AGENT_PLIST" 2>/dev/null || true
    launchctl load "$AGENT_PLIST" 2>/dev/null || true
    echo "✓ LaunchAgent installed: com.wayne.batcomputer"
fi

echo ""
echo "================================================================="
echo "🦇 INSTALLATION COMPLETE // WAYNE ENTERPRISES PROTOCOLS ACTIVE"
echo "================================================================="
echo "To start the Batcomputer voice companion manually, double-click:"
echo "   $LAUNCHER"
echo "Or run in terminal:"
echo "   $DIR/Launch-Batcomputer.command"
echo "================================================================="
