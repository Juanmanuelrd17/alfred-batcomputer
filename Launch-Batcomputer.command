#!/usr/bin/env bash
# ==============================================================================
# WAYNE ENTERPRISES // BATCOMPUTER BCv1.2 LAUNCH PROTOCOL
# ==============================================================================
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$DIR"

# If invoked from Desktop or another location, locate repository
if [ ! -f "$REPO_DIR/alfred_daemon.py" ]; then
    if [ -f "$HOME/alfred-batcomputer/alfred_daemon.py" ]; then
        REPO_DIR="$HOME/alfred-batcomputer"
    elif [ -f "$HOME/Desktop/alfred-batcomputer/alfred_daemon.py" ]; then
        REPO_DIR="$HOME/Desktop/alfred-batcomputer"
    fi
fi

if [ ! -f "$REPO_DIR/alfred_daemon.py" ]; then
    echo "❌ Error: Could not locate alfred-batcomputer directory containing alfred_daemon.py."
    echo "   Searched: $DIR, $HOME/alfred-batcomputer"
    exit 1
fi

cd "$REPO_DIR"

if [ -f "$REPO_DIR/.venv/bin/python3" ]; then
    exec "$REPO_DIR/.venv/bin/python3" "$REPO_DIR/alfred_daemon.py" --server "https://batcomputer.jmrdserver.duckdns.org" --hud-url "https://batcomputer.jmrdserver.duckdns.org" "$@"
elif command -v python3 >/dev/null 2>&1; then
    exec python3 "$REPO_DIR/alfred_daemon.py" --server "https://batcomputer.jmrdserver.duckdns.org" --hud-url "https://batcomputer.jmrdserver.duckdns.org" "$@"
else
    echo "❌ Error: Python 3 runtime not found."
    exit 1
fi
