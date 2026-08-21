---
repo: architecture
path: docs/architecture/aw-app-codegraphcontext.md
source: generated
edited: false
checksum: sha256:e2311b885f567a4fc044eb89224955cad5943ee92ce449f7c08ea9dd0574c52c
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
### Toda escrita no grafo é serializada contra qualquer outra, não só contra o visualizador
- Given um workspace grande pode levar bem mais que o intervalo de reconcile para indexar, então uma passada de reconcile dispara enquanto a indexação inicial ainda roda
- When as escritas concorrentes passam pelo lock compartilhado (repos/aw-app-codegraphcontext/codegraphcontext_app/plugin.py::_visualizer_paused e seu _cgc_write_lock, verificado por repos/aw-app-codegraphcontext/tests/test_routes.py::test_visualizer_paused_serializes_concurrent_cgc_writes:127)
- Then todo chamador é serializado contra todo outro, e não apenas contra o visualizador — foi achado ao vivo em 10/08: dois processos de escrita do cgc correndo um contra o outro, um conflito DIFERENTE do de pausar o visualizador, já que nenhuma das duas escritas era o visualizador. Um lock que só protege contra um participante específico protege contra o caso que se imaginou, e não contra concorrência
- intended_status: `not_implemented` · derived health: `not_implemented`
- tests: `repos/aw-app-codegraphcontext/tests/test_routes.py` (passing)

### O visualizador é pausado durante a reindexação e religado depois
- Given o visualizador segura o grafo aberto enquanto a reindexação precisa reescrevê-lo
- When a reindexação roda com o visualizador ativo (repos/aw-app-codegraphcontext/codegraphcontext_app/plugin.py, via tests/test_routes.py::test_reindex_pauses_and_resumes_a_running_visualizer:96)
- Then o serviço é parado antes da escrita e volta a rodar ao final, com o resultado marcado como ok — religar faz parte da garantia, não é um detalhe: uma pausa sem retomada deixa a janela do visualizador permanentemente morta depois da primeira reindexação, e como a reindexação é agendada isso acontece sozinho, sem ninguém ter feito nada
- intended_status: `not_implemented` · derived health: `not_implemented`
- tests: `repos/aw-app-codegraphcontext/tests/test_routes.py` (passing)

### Pedir reindexação retorna na hora, sem segurar a requisição
- Given indexar um workspace grande leva muito mais tempo que qualquer limite razoável de requisição HTTP
- When a reindexação é disparada como tarefa e a resposta volta imediatamente (repos/aw-app-codegraphcontext/codegraphcontext_app/routes.py, rota /reindex, via tests/test_routes.py::test_reindex_starts_without_blocking:88)
- Then a resposta é 200 com started=true enquanto o trabalho segue em background — a borda do túnel corta requisição em torno de 30s e devolve "workspace offline", então uma rota que aguardasse o fim reportaria falha para uma indexação que está indo bem. Rota de app aqui nunca pode esperar trabalho longo, e este é o caso mais claro disso
- intended_status: `not_implemented` · derived health: `not_implemented`
- tests: `repos/aw-app-codegraphcontext/tests/test_routes.py` (passing)

### Grafo pedido com o visualizador parado responde 503
- Given o proxy do grafo depende do visualizador estar de pé numa porta conhecida, o que pode não ser o caso durante uma pausa de reindexação
- When a rota de grafo é chamada nesse estado (repos/aw-app-codegraphcontext/codegraphcontext_app/routes.py, via tests/test_routes.py::test_graph_proxy_returns_503_when_visualizer_not_running:120)
- Then a resposta é 503 e não 500 nem uma espera até timeout — 503 diz "indisponível agora, tente de novo", que é literalmente verdade durante uma reindexação e leva o cliente a repetir em vez de tratar como quebra. Um 500 mandaria alguém investigar um app que está funcionando exatamente como deveria
- intended_status: `not_implemented` · derived health: `not_implemented`
- tests: `repos/aw-app-codegraphcontext/tests/test_routes.py` (passing)
