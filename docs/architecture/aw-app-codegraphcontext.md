---
repo: architecture
path: docs/architecture/aw-app-codegraphcontext.md
source: generated
edited: false
checksum: sha256:db6b67d491a8b3713058c580b7f7599951ab985f16220c5140f48b20a97ee4f6
---
# CodeGraphContext

- **repo**: aw-app-codegraphcontext
- **layer**: app
- **technologies**: python
- **health** (derived): planned

Indexes every repo under the workspace into a local code graph (functions, classes, imports, call chains) and exposes it to any agent as MCP tools, plus a graph visualizer UI. Installs its own isolated `cgc` CLI, keeps the graph fresh via a low-priority periodic reconciler (never a real-time file watcher), and runs the visualizer as a managed background service.

## Connections
- `http` → **aw-workspace** — routes mounted at /api/apps/codegraphcontext
- `stdio-mcp` → **mcp-gateway** — MCP surface aggregated by the gateway

## MCP tools
- `analyze_code_relationships`
- `execute_cypher_query`
- `find_code`
- `list_indexed_repositories`
- `unwatch_directory`
- `watch_directory`

## Requirements
_none documented_
