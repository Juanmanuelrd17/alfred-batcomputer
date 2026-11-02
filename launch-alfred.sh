#!/usr/bin/env bash
# Launch 'Puter // BCv1.2 Native Desktop Override Shell + Companion App + Background Voice Listener
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pkill -f "alfred_daemon\.py --" 2>/dev/null || true
sleep 0.3
export QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-dev-shm-usage --in-process-gpu"
echo "🦇 Starting 'Puter BCv1.2 Full-Screen Desktop Override Shell & Voice Companion..."
exec python3 "$DIR/alfred_daemon.py" --desktop --server "https://batcomputer.jmrdserver.duckdns.org" --hud-url "https://batcomputer.jmrdserver.duckdns.org" "$@"
