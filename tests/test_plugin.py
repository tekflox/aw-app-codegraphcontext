"""Real integration test — loads this app into the ACTUAL aw-workspace
runtime (src.apps.runtime.AppRuntime), the same harness
src/tests/integration/apps/test_enforcement.py uses. This is not a mock:
ctx.services/ctx.watchdog/ctx.commands are the real ServiceSupervisor /
WatchdogSupervisor / CommandInstaller, so this proves the multi-service
path (services.register called for "visualizer" while watchdog.register is
called for "reconciler", both under the same app id) actually works end to
end, not just that the manifest schema allows it.

auto_start is forced False so the visualizer subprocess never actually
starts during the test (avoids a real port bind / lingering process across
test runs); index_root points at a two-file scratch dir so the one-shot
initial index finishes in well under a second instead of indexing the real
workspace.
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest
from fastapi import FastAPI

from src.apps.journal import ActionJournal
from src.apps.runtime import AppRuntime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRANTED = [
    "commands:install", "fs:workspace-data", "routes:register",
    "service:manage", "watchdog:tasks",
]


def _async(coro):
    return asyncio.run(coro)


@pytest.fixture
def scratch_index_root(tmp_path):
    d = tmp_path / "tiny-repo"
    d.mkdir()
    (d / "a.py").write_text("def hello():\n    return 'hi'\n")
    (d / "b.py").write_text("from a import hello\n\ndef main():\n    return hello()\n")
    return str(d)


def test_activate_registers_visualizer_service_and_reconciler_watchdog(
    tmp_path, monkeypatch, scratch_index_root,
):
    monkeypatch.setenv("AW_WORKSPACE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AW_WORKSPACE_CONTAINER_DIR", str(tmp_path / "workspace-root"))

    async def run():
        rt = AppRuntime(FastAPI(), journal=ActionJournal())
        await rt.load(
            REPO_ROOT,
            granted_permissions=GRANTED,
            config={
                "auto_start": False,
                "index_root": scratch_index_root,
                "reconcile_interval_s": 3600,
                "visualizer_port": 8711,
            },
        )

        # --- multi-service path: two independent registrations, one app ---
        assert ("codegraphcontext", "visualizer") in rt.services.registered()
        status = rt.services.status("codegraphcontext", "visualizer")
        assert status["running"] is False  # auto_start=False honored

        assert "reconciler" in rt.watchdog.task_ids_for("codegraphcontext")

        # --- mcp.json actually written, pointing at the installed shim ---
        mcp_doc = json.loads(open(os.path.join(REPO_ROOT, "mcp.json")).read())
        server = mcp_doc["mcpServers"]["codegraphcontext"]
        assert server["args"] == ["mcp", "start"]
        assert os.path.isfile(server["command"])

        # --- cgc really got installed (system_clis path, self-verifying) ---
        cgc_shim = os.path.join(str(tmp_path / "home"), "bin", "cgc")
        assert os.path.isfile(cgc_shim)

        # --- skill really got copied into the (sandboxed) skills index ---
        skill_path = os.path.join(
            str(tmp_path / "workspace-root"), "skills", "aw-codegraphcontext", "SKILL.md")
        assert os.path.isfile(skill_path)

        # --- journal recorded both registrations (audit trail) ---
        kinds = [(e.kind, e.target) for e in rt.journal.entries_for("codegraphcontext")]
        assert ("service:register", "visualizer") in kinds
        assert ("watchdog:register", "reconciler") in kinds

        # let the fire-and-forget initial index (tiny scratch repo) actually
        # finish before unload cancels anything, so we're not racing it.
        app = rt.get("codegraphcontext")
        if app.plugin._initial_index_task is not None:
            await asyncio.wait_for(app.plugin._initial_index_task, timeout=60)

        await rt.unload("codegraphcontext")

        # unload stopped/dropped the service and cancelled the watchdog task
        assert ("codegraphcontext", "visualizer") not in rt.services.registered()
        assert "reconciler" not in rt.watchdog.task_ids_for("codegraphcontext")

    _async(run())
