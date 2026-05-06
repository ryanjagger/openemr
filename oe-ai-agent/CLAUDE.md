# OE AI Agent — Python Sidecar Guide

This file is the Python delta for `oe-ai-agent/`. The repo-root
`/CLAUDE.md` (PHP standards, dev-easy compose, devtools, commit format)
still applies — this file only adds what's specific to the FastAPI
sidecar. For the eval harness see `evals/README.md`. For the PHP wrapper
that calls this service see
`/interface/modules/custom_modules/oe-module-ai-agent/README.md`.

## Module Map

```
src/oe_ai_agent/
  agent/             - LangGraph builders + node fns (graph.py, graph_chat.py,
                       nodes/, state.py, chat_state.py)
  conversation/      - In-memory ephemeral store; 30 min TTL, 20 turns/conv,
                       single-process (sticky routing if scaled out)
  filters/           - Per-tool field whitelists (HIPAA minimum-necessary)
  llm/               - LlmClient Protocol + MockLlmClient + LiteLLMClient
                       + prompts.py / prompts_chat.py
  observability/     - TraceCollector (contextvar), Langfuse, structlog
  schemas/           - Pydantic v2 API contracts (BriefRequest/Response,
                       ChatRequest/Response, BriefItem, ChatFact, TypedRow)
  tools/             - FHIR-backed tools + _common.py helpers
                       + chat_registry.py + fhir_client.py
  verifier/          - Tier 1 structural + Tier 2 schema, deterministic
  auth.py, config.py, main.py
```

## Local Dev

```bash
uv sync                  # install (Python 3.12)
uv run pytest            # unit tests (no live LLM, no network)
uv run ruff check        # lint
uv run ruff format       # auto-format
uv run mypy src          # strict type-check
```

The sidecar runs at host port 8400 inside the dev-easy compose stack;
see root `/CLAUDE.md` for compose details.

## Coding Conventions

- **Python 3.12**, line length 100 (`ruff.toml`).
- **Ruff** enforces E/W/F/I/N/UP/B/C4/ANN/ASYNC/RUF/SIM/PL. Tests are
  exempt from ANN and PLR2004 — everything in `src/` is fully annotated.
- **`mypy --strict`** with the pydantic plugin (`mypy.ini`). Tests are
  exempt from `disallow_untyped_defs`; `src/` is not.
- **Pydantic v2** for every API contract — no untyped dicts cross
  module/network boundaries.
- **`async def` everywhere** on the request path. `pytest-asyncio`
  `mode = auto`, so `async def test_*` works without a decorator.
- Same **"narrow, don't cast"** rule as the root file: prefer `isinstance`
  / `assert` narrowing over `cast()` or `# type: ignore`. If mypy
  complains, fix the source type, don't paper over it.

## Testing Patterns

- **`respx` mocks all FHIR HTTP traffic.** Tests must never hit a real
  network. See `tests/test_chat_tools.py` for the canonical pattern.
- **Mock LLM** has two modes (`src/oe_ai_agent/llm/mock_client.py`):
  - `MockLlmClient.synthesizing()` — parses the CONTEXT block in the
    prompt and emits a valid response that cites real rows. Use for
    graph integration tests where the verifier should still pass.
  - `MockLlmClient(scripted=...)` / `MockLlmClient(chat_scripted=...)` —
    returns a fixed payload. Use for unit tests of a single node.
- **No `conftest.py`.** Keep fixtures local to each test file.
- **Verifier-only tests** import `verify_items` directly and skip the
  graph for speed (see `tests/test_verifier.py`).

## Architecture Invariants

These are load-bearing. Don't change them without an architectural reason
recorded somewhere durable.

- **Graphs are linear chains, not DAGs.** `agent/graph.py` and
  `agent/graph_chat.py` are intentionally flat so future Tier 3 /
  human-approval nodes land additively. The tool-calling loop lives
  *inside* `llm_turn`, not as graph branching.
- **Verifier is deterministic and LLM-free.** Two tiers, first-failing-
  rule drops the item (`verifier/__init__.py`). Don't add an "LLM judge"
  here — that would belong in a separate Tier 3 node.
- **`LlmClient` is a Protocol** (`llm/client.py`). Graph code must not
  branch on which provider is wired; provider selection happens once in
  `main._llm_client()`.
- **Field whitelists are HIPAA-load-bearing.** Every tool that reaches
  FHIR MUST have an entry in
  `filters/minimum_necessary.py:TOOL_FIELD_WHITELIST`. Adding a tool
  without one will leak fields the LLM shouldn't see.
- **Trace context is a contextvar, not a parameter.** Use
  `async with use_trace()` and `async with step("name")`
  (`observability/trace.py`). Don't thread a collector through function
  args.

## Adding a Tool

Canonical example: `tools/active_medications.py`. Checklist:

1. `TOOL_NAME = "get_..."` constant at the top.
2. `async def get_X(client: FhirClient, patient_uuid: str) -> list[TypedRow]`.
3. Fetch via `client.search(...)`; extract entries with
   `bundle_resources()` from `tools/_common.py`.
4. Convert each resource via
   `to_typed_row(TOOL_NAME, resource, patient_uuid)`.
5. Register in `_TOOL_REGISTRY` in `agent/nodes/fetch_context.py` so the
   brief agent picks it up.
6. Add the field whitelist entry to `TOOL_FIELD_WHITELIST` in
   `filters/minimum_necessary.py`.
7. If the chat agent should also call it, register in
   `tools/chat_registry.py`.
8. Add a respx-mocked test under `tests/`.

## Observability

- One `agent.request.complete` JSON line per request — the grep-me line.
  Don't add `info`-level structured-log spam in hot paths; use trace
  steps instead.
- `bearer_token` on agent state is `SecretStr` by design. Don't downgrade
  it to `str`, log it, or persist it.
- LLM cost is best-effort. `litellm.completion_cost()` returns `0.0` for
  models it can't price — that's expected, not an error to handle.

## Env Vars

| Var | Purpose | Required? |
|---|---|---|
| `INTERNAL_AUTH_SECRET` | HMAC for `X-Internal-Auth` header | yes |
| `LLM_PROVIDER` | `mock` (default) or `anthropic` | no |
| `LLM_MODEL` | LiteLLM model id (e.g. `anthropic/claude-sonnet-4-6`) | no |
| `LLM_MAX_TOKENS` | Completion cap (default 4096) | no |
| `AI_AGENT_DOCUMENT_MAX_TOKENS` | Document extraction completion cap (default 8192) | no |
| `ANTHROPIC_API_KEY` | Required when `LLM_PROVIDER=anthropic` | conditional |
| `COHERE_API_KEY` | Enables clinical guideline dense retrieval and rerank | no |
| `AI_AGENT_GUIDELINE_CORPUS_DIR` | Clinical guideline corpus directory (default `corpora/clinical-guidelines`) | no |
| `AI_AGENT_GUIDELINE_INDEX_DIR` | Directory or SQLite file for guideline RAG cache (default `.rag_cache/clinical_guidelines.sqlite`) | no |
| `AI_AGENT_GUIDELINE_EMBED_MODEL` | Guideline embedding model (default `embed-v4.0`) | no |
| `AI_AGENT_GUIDELINE_RERANK_MODEL` | Guideline rerank model (default `rerank-v4.0-fast`) | no |
| `AI_AGENT_ENABLE_FREETEXT_TYPES` | Gate `recent_event` / `agenda_item` | no |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | Prod observability | no |

## Evals

`evals/` runs the **real LLM** against golden fixtures. It costs API
credits and is non-deterministic — it is **not** a CI test. Run it
before merging prompt or model changes. See `evals/README.md` for
fixtures, expectation keys, and `jq` snippets to inspect runs.

## Pointers

- Root `/CLAUDE.md` — PHP standards, dev-easy compose, devtools
- `evals/README.md` — eval harness and golden-set authoring
- `/interface/modules/custom_modules/oe-module-ai-agent/README.md` —
  PHP wrapper module that calls this sidecar
