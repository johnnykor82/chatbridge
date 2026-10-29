#!/usr/bin/env bash
# Usage: ./install.sh [install|upgrade|status|doctor|uninstall] [options]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required to run the installer. The uv tool installs ChatBridge's own Python 3.13." >&2
  exit 1
fi
if [[ $# -eq 0 || "$1" == --* ]]; then
  exec python3 "$ROOT/scripts/installer.py" install "$@"
fi
exec python3 "$ROOT/scripts/installer.py" "$@"
