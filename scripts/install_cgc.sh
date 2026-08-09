#!/usr/bin/env bash
# Installs CodeGraphContext's `cgc` CLI into an isolated venv owned by this
# app (never the workspace's shared/ambient venv or $HOME) and drops a PATH
# shim that points `cgc` at it with an app-scoped HOME. Idempotent — safe to
# re-run on install and on every reconcile pass / heal check.
set -euo pipefail

# Resolve the app's own package dir from this script's location, not $PWD —
# robust whether the runtime invokes us with cwd=package_dir (the normal
# case, see src/apps/commands.py's `_run`) or a test runs us directly.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

DATA_DIR="${PACKAGE_DIR}/.data"
VENV_DIR="${DATA_DIR}/venv"
CGC_HOME="${DATA_DIR}/cgc-home"
AW_BIN_DIR="${AW_WORKSPACE_HOME:-$HOME/.aw-workspace}/bin"

mkdir -p "${DATA_DIR}" "${CGC_HOME}" "${AW_BIN_DIR}"

if [ ! -x "${VENV_DIR}/bin/python" ]; then
  echo "install_cgc.sh: creating isolated venv at ${VENV_DIR}"
  # --copies, not the default symlinks: this venv lives under the app's
  # package dir, which multiple containers can see at DIFFERENT mount
  # points (the host process directly, the mcp-gateway's own container via
  # its $AW_APPS_ROOT -> /workspace/apps mount, ...). A symlinked python
  # bakes in the ABSOLUTE path of whatever `python3` resolved to at
  # creation time (e.g. /usr/local/bin/python3.12) — a path that may not
  # exist, or may differ, in every container that later tries to run it.
  # A real copy is self-contained and portable across all of them.
  python3 -m venv --copies "${VENV_DIR}"
fi

"${VENV_DIR}/bin/pip" install --upgrade pip -q
"${VENV_DIR}/bin/pip" install --upgrade codegraphcontext -q

# Wrapper shim on PATH: sets HOME to this app's own data dir so
# CodeGraphContext's config/db (which it resolves via Path.home(), no env
# override) never depends on — or pollutes — the ambient session $HOME.
cat > "${AW_BIN_DIR}/cgc" <<SHIM
#!/usr/bin/env bash
# aw-app-codegraphcontext shim — auto-generated; do not edit.
export HOME="${CGC_HOME}"
exec "${VENV_DIR}/bin/cgc" "\$@"
SHIM
chmod +x "${AW_BIN_DIR}/cgc"

# Self-verify — a broken install fails this (set -e) and thus fails the
# install_system_cli call / the healer's next re-run.
"${AW_BIN_DIR}/cgc" --version
