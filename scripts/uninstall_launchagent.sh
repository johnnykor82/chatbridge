#!/usr/bin/env bash
# Compatibility entry point: use the guarded, standalone installer.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$ROOT/install.sh" uninstall "$@"
