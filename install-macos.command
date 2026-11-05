#!/usr/bin/env bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
exec /bin/bash "$DIR/install-macos.sh" "$@"
