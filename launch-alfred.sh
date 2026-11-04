#!/usr/bin/env bash
# Launch 'Puter // BCv1.2 Native Desktop Override Shell + Companion App + Background Voice Listener
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RUNTIME="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

# If WAYLAND_DISPLAY is not set or points to a dead socket, dynamically discover the active Wayland socket
if [ -z "$WAYLAND_DISPLAY" ] || [ ! -S "$RUNTIME/$WAYLAND_DISPLAY" ]; then
    unset WAYLAND_DISPLAY
    for i in {1..20}; do
        SOCK=$(ls -1 "$RUNTIME"/wayland-* 2>/dev/null | grep -E "wayland-[0-9]+$" | head -1)
        if [ -n "$SOCK" ] && [ -S "$SOCK" ]; then
            export WAYLAND_DISPLAY="$(basename "$SOCK")"
            break
        fi
        sleep 0.5
    done
fi

# Ensure Hyprland is active and responsive before initializing any GUI windows
HYPR_OK=0
for i in {1..20}; do
    if hyprctl instances >/dev/null 2>&1; then
        HYPR_OK=1
        break
    fi
    sleep 0.5
done

if [ "$HYPR_OK" -eq 0 ]; then
    echo "⚠️ [Batcomputer Startup]: Hyprland compositor is not active or not responding. Exiting cleanly to protect display manager session."
    exit 0
fi

export XDG_RUNTIME_DIR="$RUNTIME"
export XDG_CURRENT_DESKTOP="${XDG_CURRENT_DESKTOP:-Hyprland}"
export XDG_SESSION_TYPE="${XDG_SESSION_TYPE:-wayland}"
export DISPLAY="${DISPLAY:-:0}"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland}"
export QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-dev-shm-usage --in-process-gpu"

# If invoked without --daemon, check if daemon is already active:
# If active, focus monitor DP-1 and raise/open the Batcomputer HUD window.
IS_DAEMON=0
for arg in "$@"; do
    if [ "$arg" = "--daemon" ]; then
        IS_DAEMON=1
        break
    fi
done

if [ "$IS_DAEMON" -eq 0 ]; then
    if systemctl --user is-active --quiet batcomputer-desktop.service 2>/dev/null || pgrep -f "alfred_daemon.py" >/dev/null 2>&1; then
        echo "🦇 Batcomputer daemon is active. Showing native 'Puter Companion..."
        curl -s -X POST http://127.0.0.1:7778/local/companion -H "Content-Type: application/json" -d '{"action": "show"}' >/dev/null 2>&1
        exit 0
    fi
fi

echo "🦇 Starting 'Puter BCv1.2 Native Companion App & Voice Daemon..."
exec /usr/bin/python3 "$DIR/alfred_daemon.py" --server "https://batcomputer.jmrdserver.duckdns.org" --hud-url "https://batcomputer.jmrdserver.duckdns.org" "$@"
