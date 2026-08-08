"""Backend sub-app mounted at /api/apps/codegraphcontext (ADR Decision 4).

Three surfaces:
* GET  /status   — shell out to `cgc list` for a quick indexed-repos summary.
* POST /reindex   — force a fresh full index of the configured index_root.
* GET|POST /graph{path} — reverse proxy into the `visualizer` service
  (127.0.0.1:<port>, never exposed directly) so windows/main.json's iframe
  can embed `cgc visualize`'s own web UI under this app's own route.
"""
from __future__ import annotations

import asyncio
import os

import httpx
from fastapi import FastAPI, Request, Response

from . import plugin as plugin_mod


def build_routes(ctx) -> FastAPI:
    app = FastAPI()

    def _visualizer_base_url() -> str:
        port = int(ctx.config.get("visualizer_port") or 8010)
        return f"http://127.0.0.1:{port}"

    @app.get("/status")
    async def status():
        index_root = str(ctx.config.get("index_root") or "/opt/aw-workspace")
        proc = await asyncio.create_subprocess_exec(
            plugin_mod.cgc_shim_path(), "list",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        return {
            "index_root": index_root,
            "visualizer": ctx.services.status(plugin_mod.SERVICE_ID),
            "cgc_list_output": out.decode(errors="replace"),
        }

    @app.post("/reindex")
    async def reindex():
        index_root = str(ctx.config.get("index_root") or "/opt/aw-workspace")
        workers = int(ctx.config.get("initial_index_parallel_workers", 4))
        env = {**os.environ, "PARALLEL_WORKERS": str(workers)}
        asyncio.create_task(asyncio.create_subprocess_exec(
            plugin_mod.cgc_shim_path(), "index", index_root, "--force", "--no-progress",
            env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        ))
        return {"started": True, "index_root": index_root}

    @app.api_route("/graph", methods=["GET", "POST"])
    @app.api_route("/graph/{path:path}", methods=["GET", "POST"])
    async def graph_proxy(request: Request, path: str = ""):
        url = f"{_visualizer_base_url()}/{path}"
        body = await request.body()
        async with httpx.AsyncClient() as client:
            try:
                upstream = await client.request(
                    request.method, url, params=request.query_params,
                    content=body, timeout=30.0,
                )
            except httpx.ConnectError:
                return Response(
                    content="codegraphcontext visualizer service is not running",
                    status_code=503,
                )
        return Response(
            content=upstream.content, status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
        )

    return app
