# OpenEMR AI Agent — Implementation Tasks

Derived from `ARCHITECTURE.md`. Organized walking-skeleton-first: get an end-to-end request flowing with stubs, then fill in each layer. Decisions in `[brackets]` reflect the chosen flow (`POST /apis/default/api/ai/brief/{pid}` via OpenEMR's REST dispatch).

Scoping notes:
- Item types `recent_event` and `agenda_item` are scaffolded but gated behind a feature flag; the negation/temporality residual risk (ARCH §6.5 #4) is unresolved and may push them post-MVP.
- Synthetic data only. No BAA / Path 2 deployment hardening in scope.

---

## Phase 0 — Decisions & spikes (block implementation)

- **T0.1 — BearerTokenMinter feasibility spike.** Confirm a session-authenticated PHP request can mint a short-lived user-scoped OAuth2 bearer with the FHIR scopes listed in ARCH §2 step 5. Output: working PoC or pivot doc to system-client + scope-recheck design. *Blocks T2.3, T2.6, T2.7.*
- **T0.2 — Pick synthetic data source.** Recommend Synthea + ~10 hand-curated edge-case charts (verifier negative tests). Decide directory layout under `oe-ai-agent/tests/fixtures/synthetic_charts/`. *Blocks T4.1.*
- **T0.3 — Pick LLM provider for demo.** Default recommendation: Claude Sonnet 4.6 via LiteLLM. Cost target + budget cap. *Blocks T3.5 prompt-tuning sub-tasks.*
- **T0.4 — Confirm panel-injection event.** Verify `Events/Patient/Summary/PatientSummaryPageEvent` (or equivalent) exists in current OpenEMR. If not, identify the actual extension point. *Blocks T2.8.*
- **T0.5 — Confirm REST route registration shape.** Verify `RoutesExtensionListener` / `RestApiResourceServiceEvent` is the right path for `/api/ai/brief/{pid}` and that session auth flows through it. *Blocks T2.7.*

---

## Phase 1 — Walking skeleton (end-to-end with stubs)

Goal: physician clicks "Generate brief" → PHP → sidecar → returns one hardcoded `BriefItem` → renders. No auth beyond session, no FHIR, no LLM, no verifier.

- **T1.1 — PHP module skeleton.** `interface/modules/custom_modules/oe-module-ai-agent/`: `composer.json` (PSR-4 `OpenEMR\Modules\AiAgent\`), `info.txt`, `version.php`, `openemr.bootstrap.php`, `Module.php` (Laminas MVC if needed for routes). `declare(strict_types=1)` everywhere.
- **T1.2 — Python sidecar skeleton.** `oe-ai-agent/`: `pyproject.toml` (uv-managed, Python 3.12+), `Dockerfile`, `ruff.toml`, `mypy.ini` (strict), `src/oe_ai_agent/main.py` with `/healthz` and stub `POST /v1/brief` returning a hardcoded `BriefResponse`.
- **T1.3 — docker-compose wiring.** Add `oe-ai-agent` + `redis` services on `oe-internal` network per ARCH §13.1. Confirm OpenEMR container can reach `http://oe-ai-agent:8000/healthz`.
- **T1.4 — Internal auth header.** `INTERNAL_AUTH_SECRET` env var; FastAPI dependency rejects requests missing `X-Internal-Auth`. PHP `SidecarClient` injects it. (Defense in depth on the internal network; not a primary auth boundary.)
- **T1.5 — Stub PHP request flow.** `BriefController` registered at `/api/ai/brief/{pid}` (per T0.5), calls `SidecarClient`, returns the stub `BriefResponse` to the caller. No ACL check yet, no audit yet.
- **T1.6 — Stub UI panel.** Twig panel injected via T0.4's event; `brief_panel.js` does XHR to `/apis/default/api/ai/brief/{pid}` and renders `BriefItem.text`. Three states: idle / loading / rendered.
- **T1.7 — Smoke test.** Manual: log in, open patient chart, click button, see hardcoded item. Document in module README.

---

## Phase 2 — Real auth & data access

Goal: replace stubs with real ACL gating, real token minting, real FHIR fetches. Still no LLM (mock), still no verifier.

- **T2.1 — DTOs.** `final readonly` PHP DTOs: `BriefRequest`, `BriefResponse`, `BriefItem`. Symfony Serializer wiring.
- **T2.2 — PatientAccessValidator.** Wraps `AclMain::aclCheck` + `see_auth` per ARCH §9. Closes audit's `pid` HIGH finding. Isolated unit tests.
- **T2.3 — BearerTokenMinter.** Implementation per T0.1 outcome. Mints token with the seven read scopes in ARCH §2 step 5. Short TTL (≤5 min).
- **T2.4 — SidecarClient.** HTTP client (Symfony HttpClient), 30s timeout, no retries (idempotent retries deferred). Passes `pid`, `fhir_base_url`, `bearer_token`, `request_id` to sidecar.
- **T2.5 — Wire ACL + token into BriefController.** Validator first, then mint, then call sidecar. 403 on ACL fail (no agent call).
- **T2.6 — Python schemas.** Pydantic v2: `TypedRow`, `ToolResult`, `BriefItem`, `BriefResponse`, `AgentState` per ARCH §5.1, §7.1.
- **T2.7 — FHIR client.** `httpx` + `Authlib` + `fhir.resources`. Respx-mockable shape. Reads `bearer_token` from request body, attaches as `Authorization: Bearer ...`.
- **T2.8 — Tools (7).** Per ARCH §5.2: `get_demographics`, `get_active_problems`, `get_active_medications`, `get_allergies`, `get_recent_encounters`, `get_recent_observations`, `get_recent_notes`. Each tool returns `list[TypedRow]`.
- **T2.9 — Minimum-necessary filter.** Per-tool whitelist per ARCH §5.3, applied before the LLM sees data. Unit-tested per tool — assert no whitelisted-out fields leak.
- **T2.10 — Stub LangGraph (no LLM).** `fetch_context → return tool_results as fake brief items`. Confirms tools fire in parallel against real FHIR with the user's token, output renders in the panel.

---

## Phase 3 — Agent + verifier

Goal: real LLM produces structured output, verifier gates it.

- **T3.1 — LiteLLM client wrapper.** `llm/client.py` for the chosen provider (T0.3). Structured output via `response_format` JSON schema bound to `BriefResponse`.
- **T3.2 — MockLlmClient.** Deterministic, scripted responses per test case. Used by all sidecar tests — CI never calls a real LLM.
- **T3.3 — Prompts.** `llm/prompts.py`: system prompt, output-schema instructions, denylist phrasing reminder, claim-type→allowed-tables map serialized into the prompt.
- **T3.4 — LangGraph nodes.** `fetch_context`, `llm_call`, `parse_output`, `verify` per ARCH §7.3. `MemorySaver` checkpointer for MVP.
- **T3.5 — Graph builder.** `START → fetch_context → llm_call → parse_output → verify → END`. Wired into `/v1/brief` endpoint.
- **T3.6 — Verifier Tier 1 (structural).** Five rules per ARCH §6.1: citation existence, patient binding, type-table compatibility, typed-fact re-extraction, staleness. Pure function. One test per rule, both pass and fail cases.
- **T3.7 — Verifier Tier 2 (schema).** Closed-type enum (Pydantic), advisory-phrase denylist (regex), citation-count floor. Per ARCH §6.2.
- **T3.8 — Constraints map.** `verifier/constraints.py`: full `ALLOWED_TABLES_FOR_TYPE` map for all 7 claim types (ARCH §6.1 only shows three examples — fill in the rest).
- **T3.9 — Failure UX.** Failed items dropped silently, logged to `verification_failures`. Empty-brief case renders the "no verified items" message per ARCH §6.4.
- **T3.10 — Feature flag for `recent_event` / `agenda_item`.** Gate these item types behind a config flag pending Tier 3 decision (residual negation/temporality risk per ARCH §6.5 #4). _Done — `AI_AGENT_ENABLE_FREETEXT_TYPES` (default false) narrows both the prompt allow-list and the JSON-schema enum, with a Tier 2 `tier2_type_disabled` check as defense in depth._

---

## Phase 4 — Audit & polish

- **T4.1 — `llm_call_log` schema.** `sql/install.sql` per ARCH §8.1. Module install registers it.
- **T4.2 — AuditLogService.** Writes one row per call per ARCH §8.2. Hashes only — no raw prompt/response in MVP. `LLM_AUDIT_DEBUG=1` gate for non-prod raw payloads.
- **T4.3 — Integrity HMAC.** `HMAC-SHA256(secret, canonical_serialization(row))` per ARCH §8.3. `prev_log_hash` left NULL (hash chain post-MVP).
- **T4.4 — `request_id` propagation.** PHP generates UUID, passes to sidecar, sidecar attaches to FHIR call headers so `api_log` rows can be joined to `llm_call_log` per ARCH §8.4.
- **T4.5 — Verbatim excerpt UI.** Hover/expand reveals `verbatim_excerpts[]` next to each rendered item per ARCH §6.3 mitigation. _Done — `<details>` disclosure under each item; collapsed by default, monospaced quote block when open._
- **T4.6 — Error states.** Token mint failure, sidecar timeout, sidecar 5xx — all render a degraded panel with retry, all produce a `denied` or `failed` audit row. _Done — controller-side audit on every exit path landed in 4a; panel maps each error code to human copy, surfaces the request_id, and offers Retry on the recoverable ones (token_mint_failed, sidecar_unreachable, http_error, network)._

---

## Phase 5 — Tests & deployment

- **T5.1 — Synthetic chart fixtures.** Per T0.2 outcome. ~10 representative cases including injected-bad cases (cross-patient row, fabricated ID, stale data, denylist phrase) for negative tests.
- **T5.2 — Verifier unit tests (Python).** Per ARCH §12.2. One test per rule, plus property-based tests for the typed-fact re-extraction rule.
- **T5.3 — Tool filter tests (Python).** One test per tool asserting no whitelisted-out fields leak.
- **T5.4 — Golden brief suite (Python).** ~10 fixtures × expected items × expected verifier outcomes. Run with `MockLlmClient`.
- **T5.5 — LangGraph integration tests (Python).** Full graph runs with `respx` mocking FHIR and `MockLlmClient`. Asserts state transitions and final brief shape.
- **T5.6 — PHP isolated tests.** PHPUnit 11 isolated suite per `CLAUDE.md`: `PatientAccessValidator`, `BearerTokenMinter`, `AuditLogService`, `SidecarClient`. Mark data providers `@codeCoverageIgnore`.
- **T5.7 — PHP integration test.** `BriefController` end-to-end with sidecar mocked at HTTP layer.
- **T5.8 — E2E (Cypress or Playwright).** Docker-compose stack, login, navigate, click, assert verified item + verbatim excerpt reachable.
- **T5.9 — PHPStan level 10 clean.** No new baseline entries. Fix any existing baseline entries in files this work touches.
- **T5.10 — Module install runbook.** README + `sql/install.sql` walkthrough; admin → modules → install + activate flow verified end-to-end.

---

## Cross-cutting / non-task hygiene

- Langfuse tracing wired for demo environments via `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`; raw capture is synthetic data only.
- Inline call only; queue seam shape preserved in `BriefService` for post-MVP Arq+Redis migration.
- No write tools, no `human_approval` node, no Tier 3 LLM-as-judge in MVP. Path documented in ARCH §11 and §6.3.
- Per `CLAUDE.md`: PHPStan level 10, mypy strict + ruff, `final readonly` DTOs, `QueryUtils` for SQL, `OEGlobalsBag` over `$GLOBALS`, constructor DI.

---

## Suggested ordering & parallelism

```
Phase 0 (sequential, mostly research) ─┐
                                       ▼
Phase 1 (T1.1 || T1.2, then T1.3 → T1.4 → T1.5 || T1.6 → T1.7)
                                       ▼
Phase 2 (T2.1–2.5 PHP-side || T2.6–2.10 Python-side, sync at T2.10)
                                       ▼
Phase 3 (T3.1, T3.2 → T3.3, T3.4 → T3.5; T3.6–3.9 parallel to T3.4)
                                       ▼
Phase 4 (mostly sequential)
                                       ▼
Phase 5 (test suites parallel; T5.8 E2E last)
```

Phases 0–2 are the riskiest (token minting, event injection, FHIR auth shape). Phases 3–5 are well-understood once the substrate works.
