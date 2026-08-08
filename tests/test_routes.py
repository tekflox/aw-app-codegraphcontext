"""TestClient coverage for codegraphcontext_app/routes.py's build_routes(ctx)
— isolated from the full AppRuntime harness (that's tests/test_plugin.py's
job); here we hand it a minimal fake ctx, same pattern aw-app-template uses.

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

from codegraphcontext_app.routes import build_routes  # noqa: E402


class _FakeServices:
    def status(self, service_id):
        return {"service": service_id, "running": False, "pid": None, "autostart": False}


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
    return str(home)


def _client(fake_bin_dir, **config):
    ctx = _FakeCtx({"index_root": "/tmp/does-not-matter", **config})
    return TestClient(build_routes(ctx))


def test_status(fake_bin_dir):
    client = _client(fake_bin_dir)
    resp = client.get("/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["index_root"] == "/tmp/does-not-matter"
    assert body["visualizer"]["running"] is False
    assert "ok" in body["cgc_list_output"]


def test_reindex_starts_without_blocking(fake_bin_dir):
    client = _client(fake_bin_dir)
    resp = client.post("/reindex")
    assert resp.status_code == 200
    assert resp.json()["started"] is True


def test_graph_proxy_returns_503_when_visualizer_not_running(fake_bin_dir):
    client = _client(fake_bin_dir, visualizer_port=8712)
    resp = client.get("/graph")
    assert resp.status_code == 503
