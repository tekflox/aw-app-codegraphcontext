"""Entrypoint referenced by aw-app.json's runtime.entrypoint
("codegraphcontext_app.plugin:CodeGraphContextAppPlugin").

Two background contribution points, deliberately different mechanisms for
different jobs (see src/apps/services.py vs src/apps/watchdog.py):

* ``visualizer`` — a real subprocess with a port (``ctx.services``,
  service:manage). Autostart is user-configurable.
* ``reconciler`` — an in-process periodic async tick (``ctx.watchdog``,
  watchdog:tasks), NOT a real-time file watcher. `cgc watch`'s per-file
  debounce (2s, unbounded concurrent threads on a burst of changes — see
  codegraphcontext/core/watcher.py) is too CPU-spiky for a background app;
  a low-frequency `cgc update` tick with a low worker count instead
  guarantees the graph is never more than ~reconcile_interval_s stale
  without ever competing hard for CPU.

The one-time initial index is intentionally NOT the reconciler's first tick
— it's a separate fire-and-forget task so it can run with a higher worker
count (aggressive is fine, it only happens once) while every recurring tick
after it stays gentle.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path

from . import routes as routes_mod

log = logging.getLogger("aw_apps.codegraphcontext")

SERVICE_ID = "visualizer"
WATCHDOG_ID = "reconciler"


def cgc_shim_path() -> str:
    """The `cgc` command install_cgc.sh drops on the persistent bin dir.

    Deliberately a bare name, resolved via PATH at spawn time by every
    subprocess this plugin starts (and by whatever spawns the mcp.json
    entry) — NOT an absolute path computed by importing the host runtime's
    own ``src.apps.paths`` module. A Tier-1 app must only touch the host
    through the ``ctx`` facades (see codegraphcontext_app/plugin.py's own
    activate()); importing ``src.apps`` directly would also break this
    app's own standalone test/CI checkout, which has no ``src/`` tree.
    install_cgc.sh already guarantees `$AW_WORKSPACE_HOME/bin` (where the
    shim lives) is on PATH for the whole host process and everything it
    spawns — see paths.py's own docstring on the persistent bin dir.
    """
    return "cgc"


def mcp_command_path(package_dir: str) -> str:
    """Absolute path to the venv's own `cgc`, as seen from INSIDE the
    mcp-gateway's container — not this (Tier-1, host-process) container.

    The gateway (tier: container) spawns stdio MCP servers itself, in its
    own container — a bare "cgc" (PATH-resolved on the HOST) is invisible
    to it. aw-mcp-gateway now mounts $AW_APPS_ROOT at the SAME path it has
    on the host (/opt/aw-workspace/apps/<id>, no more gateway-specific
    /workspace/apps translation — see tekflox/aw-mcp-gateway's
    aw-app.json), so `package_dir` (== `ctx.package_dir`) is valid from
    both sides and this is now just a plain path join, no hardcoded
    gateway-side convention to maintain separately.
    """
    return os.path.join(package_dir, ".data", "venv", "bin", "cgc")


def build_mcp_doc(package_dir: str) -> dict:
    """This app's own root mcp.json content — static (no user-editable
    server list, unlike aw-app-mcp-tools), so reload_on_save is false and
    there's no on_config_saved hook; activate() re-writes it every boot
    purely to self-heal the shim path."""
    return {
        "mcpServers": {
            "codegraphcontext": {
                "enabled": True,
                "type": "stdio",
                "command": mcp_command_path(package_dir),
                "args": ["mcp", "start"],
            }
        }
    }


def write_mcp_json(package_dir: str) -> dict:
    doc = build_mcp_doc(package_dir)
    Path(package_dir, "mcp.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


class CodeGraphContextAppPlugin:
    async def activate(self, ctx) -> None:
        self.ctx = ctx
        self._initial_index_task: asyncio.Task | None = None

        ctx.commands.install_system_cli(
            "cgc", "scripts/install_cgc.sh", uninstall="scripts/uninstall_cgc.sh"
        )

        mcp_doc = write_mcp_json(ctx.package_dir)

        ctx.routes.register(routes_mod.build_routes(ctx))

        port = int(ctx.config.get("visualizer_port") or 8010)
        index_root = str(ctx.config.get("index_root") or "/opt/aw-workspace")
        start_cmd = f"{cgc_shim_path()} visualize --host 127.0.0.1 --port {port}"
        ctx.services.register(SERVICE_ID, start_cmd, autostart=ctx.config.get("auto_start", True))

        self._initial_index_task = asyncio.create_task(self._run_initial_index(index_root))

        ctx.watchdog.register(
            WATCHDOG_ID,
            self._reconcile_tick,
            interval_s=lambda: float(self.ctx.config.get("reconcile_interval_s", 900)),
            run_immediately=False,
        )

        log.info(
            "aw-app-codegraphcontext activated (visualizer port=%s, index_root=%s, mcp servers=%s)",
            port, index_root, list(mcp_doc["mcpServers"]),
        )

    async def _run_initial_index(self, index_root: str) -> None:
        """One-shot, fire-and-forget. Only actually indexes if index_root
        isn't already indexed — `cgc index` is safe/cheap to call on an
        already-indexed repo (it's the recurring reconciler that should
        stay off `index` and use the lighter `update`)."""
        workers = int(self.ctx.config.get("initial_index_parallel_workers", 4))
        env = {**os.environ, "PARALLEL_WORKERS": str(workers)}
        try:
            proc = await asyncio.create_subprocess_exec(
                cgc_shim_path(), "index", index_root, "--no-progress", "--summarize",
                env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await proc.communicate()
            if proc.returncode != 0:
                log.warning("codegraphcontext: initial index exited %s: %s",
                            proc.returncode, out.decode(errors="replace")[-2000:])
            else:
                log.info("codegraphcontext: initial index of %s complete", index_root)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("codegraphcontext: initial index failed")

    async def _reconcile_tick(self) -> None:
        """The watchdog cadence body — deliberately `cgc update` (a light,
        hook-friendly refresh), never `cgc watch` (unbounded-concurrency
        file watcher) or a repeated full `cgc index`."""
        index_root = str(self.ctx.config.get("index_root") or "/opt/aw-workspace")
        workers = int(self.ctx.config.get("reconcile_parallel_workers", 1))
        env = {**os.environ, "PARALLEL_WORKERS": str(workers)}
        argv = [cgc_shim_path(), "update", index_root, "--quiet"]
        if shutil.which("nice"):
            argv = ["nice", "-n", "19"] + argv
        proc = await asyncio.create_subprocess_exec(
            *argv, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"cgc update exited {proc.returncode}: {out.decode(errors='replace')[-2000:]}")

    async def deactivate(self) -> None:
        if self._initial_index_task is not None and not self._initial_index_task.done():
            self._initial_index_task.cancel()
        log.info("aw-app-codegraphcontext deactivated")
