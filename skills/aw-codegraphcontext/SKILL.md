---
name: aw-codegraphcontext
description: >-
  The workspace's local code graph — every repo under /opt/aw-workspace
  indexed into a queryable graph (functions, classes, imports, call chains),
  exposed as MCP tools to any agent on this tenant and as a graph visualizer
  UI. Use whenever asked to find where something is defined/called, trace
  call chains or class hierarchies, check how fresh the index is, or
  explain how this app's MCP tools reach an agent.
---

# CodeGraphContext (aw-app-codegraphcontext)

This app installs [CodeGraphContext](https://github.com/CodeGraphContext/CodeGraphContext)
(`cgc`) as an isolated CLI, keeps it indexing every repo under
`/opt/aw-workspace`, and exposes the resulting graph two ways: as MCP tools
any agent can call, and as a visual graph browser window.

## What's actually running

Three independent pieces, registered from one plugin
(`codegraphcontext_app/plugin.py`) — see `src/apps/services.py` and
`src/apps/watchdog.py` for how the runtime keeps them apart:

* **`visualizer` service** (`service:manage`) — a real subprocess,
  `cgc visualize`, bound to `127.0.0.1` only. Proxied into this app's own
  window (`Code Graph` in the app grid) via `GET /api/apps/codegraphcontext/graph`.
  Never reachable directly from outside the workspace.
* **`reconciler` watchdog task** (`watchdog:tasks`) — an in-process async
  tick, **not** a real-time file watcher. It runs `cgc update` (a light,
  incremental refresh) on a cadence (`reconcile_interval_s`, default 900s /
  15min) with a deliberately low `PARALLEL_WORKERS` count. This is a
  conscious choice: `cgc watch`'s native file watcher debounces per-file
  with no global concurrency cap, so a large burst of changes (a branch
  checkout touching thousands of files) can spike CPU hard. Polling on a
  gentle cadence instead guarantees the graph is never staler than
  ~`reconcile_interval_s`, comfortably under an hour, without ever
  competing for CPU the way a live watcher would.
* **initial index** — a one-shot background task on first activation,
  allowed to be aggressive (`initial_index_parallel_workers`, default 4)
  since it only ever runs once per fresh install.

Dotfiles/dot-directories (`.git`, `.venv`, `.nvm`, `.npm`, `.cache`, ...)
are always skipped — `IGNORE_HIDDEN_FILES` defaults to true in
CodeGraphContext itself.

## How the MCP tools actually reach an agent

This is the part worth understanding precisely, because it's a two-tier
mechanism, not a single config file:

1. **This app ships its own `mcp.json`** at the root of its package dir
   (`codegraphcontext_app/plugin.py`'s `write_mcp_json`, regenerated every
   boot) — a single stdio server entry pointing directly at the venv's own
   `cgc` binary (`<package_dir>/.data/venv/bin/cgc mcp start`), not the
   `AW_WORKSPACE_HOME/bin/cgc` PATH shim (that shim is what services/routes
   running in the HOST process use — see "Why isolated" below; the gateway
   is a *separate container* that only mounts `$AW_APPS_ROOT`, not
   `AW_WORKSPACE_HOME`, so the shim is invisible to it). This works because
   `aw-mcp-gateway` mounts `$AW_APPS_ROOT` at the exact same path it has on
   the host (`/opt/aw-workspace/apps`, no more gateway-specific
   `/workspace/apps` translation — fixed 2026-08-10, see
   `tekflox/aw-mcp-gateway`) — `ctx.package_dir` is valid from both sides.
   Same overall pattern `aw-app-mcp-tools` uses for Playwright — **not** a
   manual edit of any workspace-level `.mcp.json`.
2. **The `mcp-gateway` app scans every installed app's root `mcp.json`**
   and merges the servers it finds into the one MCP endpoint it serves.
   That's why this app declares `dependencies.apps: [{"id": "mcp-gateway"}]`
   — the gateway must be installed for this app's tools to actually surface
   anywhere.
3. **Any agent on the tenant** — this Telegram agent, a Claude Code session
   in this workspace, another app's agent — reaches the tools through that
   one gateway connection, not by knowing this app exists individually.
   `contributes.mcp.reload_on_save` is `false` here (unlike mcp-tools)
   because this app's server list is fixed, not user-editable config, so
   there's nothing for a config save to regenerate mid-session.

Tools exposed (see `contributes.mcp.provides` in `aw-app.json`):
`find_code`, `analyze_code_relationships`, `list_indexed_repositories`,
`execute_cypher_query`, `watch_directory`, `unwatch_directory` — the full
list is whatever `cgc mcp tools` reports for the installed version, this is
the commonly-used subset.

## Common actions

* **Check indexing status**: `GET /api/apps/codegraphcontext/status` (or
  the "Code Graph" window, which shows the same via its status widget) —
  reports the visualizer's service status plus the last index/reconcile
  result (in-memory, tracked by the plugin), not a live `cgc list` call
  (see below for why).
* **Force a full reindex**: `POST /api/apps/codegraphcontext/reindex`, or
  the "Force reindex" button in the window. Runs with the aggressive
  worker count, same as the initial index — use sparingly, this is a full
  rebuild, not the incremental `update` the reconciler does automatically.
* **Query the graph directly** (bypassing MCP, e.g. from a workspace
  terminal): `cgc find name <symbol>`, `cgc query "<cypher>"`.
* **Change what gets indexed**: `index_root` in this app's settings
  (default `/opt/aw-workspace`, i.e. everything, dotfiles excluded).

## Why the visualizer blips offline during indexing

CodeGraphContext's default storage (KuzuDB) is a single-writer embedded
file store — the `visualizer` service holds it open continuously, so any
OTHER `cgc` invocation (index/update/list) started while it's running
fails with a lock error ("Could not set lock on file"). Found live
(2026-08-10): switching the backend to FalkorDB doesn't reliably avoid
this either — `cgc update` still hit the kuzudb lock path even with
FalkorDB configured, an apparent inconsistency in the upstream tool
across commands. So `plugin.py`'s `_visualizer_paused()` stops the
service for the duration of any index/update and restarts it after —
only if it was actually running before, so `auto_start: false` stays
honored. Expect the "Code Graph" window to briefly 503 during the initial
index and every reconcile tick; this is normal, not a bug.

## Why isolated, not the shared workspace venv

`install_cgc.sh` creates its own venv under this app's `.data/venv` and a
PATH shim (`AW_WORKSPACE_HOME/bin/cgc`) that pins `HOME` to this app's own
`.data/cgc-home` before exec'ing the real binary — CodeGraphContext resolves
its config/db location via `Path.home()` with no env override, so pinning
`HOME` per-invocation is the only way to keep its state inside this app's
own data dir instead of depending on (or polluting) the ambient session's
`$HOME`. Never add `codegraphcontext` to the shared/ambient venv.
