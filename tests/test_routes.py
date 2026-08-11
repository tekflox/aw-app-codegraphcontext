"""TestClient coverage for codegraphcontext_app/routes.py's build_routes(ctx,
plugin) — isolated from the full AppRuntime harness (that's
tests/test_plugin.py's job); here we hand it a minimal fake ctx + a bare
plugin instance, same pattern aw-app-template uses.

Run: .venv/bin/python -m pytest tests/test_routes.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from codegraphcontext_app.plugin import CodeGraphContextAppPlugin  # noqa: E402
from codegraphcontext_app.routes import build_routes  # noqa: E402


class _FakeServices:
    def __init__(self):
        self.running = False

    def status(self, service_id):
        return {"service": service_id, "running": self.running, "pid": None, "autostart": False}

    def stop(self, service_id):
        self.running = False

    def start(self, service_id):
        self.running = True


class _FakeCtx:
    def __init__(self, config):
        self.config = config
        self.services = _FakeServices()


@pytest.fixture
def fake_bin_dir(tmp_path, monkeypatch):
    """A fake `cgc` shim so routes.py's subprocess calls have something real
    (but fast/deterministic) to exec, without depending on a real
    CodeGraphContext install for this isolated route test."""
    home = tmp_path / "home"
    (home / "bin").mkdir(parents=True)
    shim = home / "bin" / "cgc"
    shim.write_text("#!/usr/bin/env bash\necho '{\"ok\": true}'\n")
    shim.chmod(0o755)
    monkeypatch.setenv("AW_WORKSPACE_HOME", str(home))
    # cgc_shim_path() returns a bare "cgc", resolved via PATH — same as the
    # real deployment (install_cgc.sh's shim dir is on the host process's
    # PATH), so the fake shim's dir must be on PATH here too.
    monkeypatch.setenv("PATH", f"{home / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")
    return str(home)


def _app(fake_bin_dir, **config):
    import asyncio

    ctx = _FakeCtx({"index_root": "/tmp/does-not-matter", **config})
    plugin = CodeGraphContextAppPlugin()
    plugin.ctx = ctx
    plugin.last_index = {"ok": None, "at": None, "detail": ""}
    plugin.last_reconcile = {"ok": None, "at": None, "detail": ""}
    plugin._cgc_write_lock = asyncio.Lock()
    return build_routes(ctx, plugin), plugin


def test_status(fake_bin_dir):
    app, _ = _app(fake_bin_dir)
    client = TestClient(app)
    resp = client.get("/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["index_root"] == "/tmp/does-not-matter"
    assert body["visualizer"]["running"] is False
    # not a live cgc-list shell-out (would compete with the visualizer for
    # the DB lock) — just the in-memory last-run state the plugin tracks.
    assert body["last_index"] == {"ok": None, "at": None, "detail": ""}
    assert body["last_reconcile"] == {"ok": None, "at": None, "detail": ""}


def test_reindex_starts_without_blocking(fake_bin_dir):
    app, _ = _app(fake_bin_dir)
    client = TestClient(app)
    resp = client.post("/reindex")
    assert resp.status_code == 200
    assert resp.json()["started"] is True


def test_reindex_pauses_and_resumes_a_running_visualizer(fake_bin_dir):
    import time

    app, plugin = _app(fake_bin_dir)
    plugin.ctx.services.running = True

    # A context-managed TestClient keeps its portal's event loop alive
    # across calls, so the fire-and-forget reindex task (asyncio.create_task
    # inside the request handler) actually gets to run to completion instead
    # of being orphaned when a bare TestClient() call's own request-scoped
    # loop tears down.
    with TestClient(app) as client:
        resp = client.post("/reindex")
        assert resp.status_code == 200

        for _ in range(50):
            if plugin.last_index.get("at") is not None:
                break
            time.sleep(0.05)

    assert plugin.last_index["ok"] is True
    assert plugin.ctx.services.running is True  # restarted after the pause


def test_graph_proxy_returns_503_when_visualizer_not_running(fake_bin_dir):
    app, _ = _app(fake_bin_dir, visualizer_port=8712)
    client = TestClient(app)
    resp = client.get("/graph")
    assert resp.status_code == 503


def test_visualizer_paused_serializes_concurrent_cgc_writes(fake_bin_dir):
    """Found live 2026-08-10: a reconcile tick firing while the initial
    index was still running (a large workspace can take well over
    reconcile_interval_s) raced a second `cgc` write process against the
    first — a DIFFERENT conflict than the visualizer-pause one, since
    neither write is the visualizer. `_visualizer_paused()`'s
    `_cgc_write_lock` must serialize every caller against every other,
    not just against the visualizer."""
    import asyncio

    _, plugin = _app(fake_bin_dir)
    concurrent = 0
    max_concurrent = 0

    async def fake_write():
        nonlocal concurrent, max_concurrent
        async with plugin._visualizer_paused():
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
            await asyncio.sleep(0.05)
            concurrent -= 1

    async def run():
        await asyncio.gather(fake_write(), fake_write(), fake_write())

    asyncio.run(run())
    assert max_concurrent == 1
