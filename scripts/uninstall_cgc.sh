#!/usr/bin/env bash
# Reverts install_cgc.sh — removes the PATH shim and the app's isolated venv.
# The indexed graph data itself lives under .data/ and is removed by the
# runtime along with the app's package dir on uninstall.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
AW_BIN_DIR="${AW_WORKSPACE_HOME:-$HOME/.aw-workspace}/bin"

rm -f "${AW_BIN_DIR}/cgc"
rm -rf "${PACKAGE_DIR}/.data/venv"

echo "uninstall_cgc.sh: removed cgc shim and isolated venv"
