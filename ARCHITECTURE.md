# OpenEMR AI Agent — Architecture

During an architecture audit, I found that OpenEMR has a mature plug-in architecture that allows for custom modules. It also includes an event architecture we can hook into. There are eight modules already in the codebase with patterns we can follow.

Given the nature of this application (healthcare) and future constraints (HIPPA, BAA rules), we should build on the module extensibilty patterns already in OpenEMR.

I'm going to start by adding a new custom OpenEMR PHP module. This will interface with a Python "sidecar" service that runs the agent, calls the LLM, and handles verification and observability services. We'll follow existing PHP module patterns for the new module, but the Python sidecar interface will allow us to work with more modern tooling.

LangChain to be used from day 1 for the agent to make future features (Observability logging, Verification steps) quicker to implement.

The below MVP draft is designed around implementing a single, single user story - generating a patient 'brief' for a physician on the patient dashboard UI. While just a single feature, it will implement the infrastructure needed (new module, python service sidecar, langgraph, a simple verification service) for future feature implementation.



**Date:** 2026-04-28
**Status:** Draft for MVP
**Related docs:** [`docs/users.md`](../users.md), [`docs/openemr/AUDIT.md`](../openemr/AUDIT.md), [`docs/openemr/AUDIT2.md`](../openemr/AUDIT2.md), [`docs/openemr/auth.md`](../openemr/auth.md), [`docs/openemr/module-architecture.md`](../openemr/module-architecture.md)

---

## Executive Summary

This document defines the architecture for an AI agent integrated into OpenEMR. The MVP wedge is a **read-only "5-line patient brief"** rendered in a panel on the physician patient summary page, targeted at the persona in `docs/users.md` §2: a primary care physician between patients who needs a chart-prep brief in under 60 seconds. Subsequent personas (MA med-rec, biller AR triage, ED resident, front office, admin) extend from this same substrate; design decisions favor the MVP without precluding them.

### Key decisions

**Topology — two halves.** A new OpenEMR custom module (`oe-module-ai-assistant`, PHP) is paired with a separate Python sidecar service (`oe-ai-agent`, FastAPI). The PHP module owns the UI panel, REST endpoint, `pid`-ownership pre-check, and per-call audit log. The Python sidecar runs the agent (LangGraph), calls the LLM, runs the verifier, and reads patient data from OpenEMR's FHIR API over OAuth2. The two halves communicate over internal HTTP with the user's bearer token passed through, so the agent inherits the user's FHIR scope automatically — closing the audit's HIGH `pid` ACL finding (`interface/globals.php:155-157`) by construction for any agent-initiated read.

**Custom module, not a fork.** All integration is via `interface/modules/custom_modules/oe-module-ai-assistant/`, Symfony EventDispatcher subscriptions, and registered REST routes. Core OpenEMR is not modified. Per `docs/openemr/module-architecture.md`, this is the supported extension surface.

**Read-only on day one; writes designed-for, not built.** The tool layer, agent state, and audit log all support write semantics, but no write tools are exposed in MVP. Adding writes is additive — new tools, a new approval-gate node in the LangGraph, no schema or topology changes.

**LangGraph for agent orchestration.** Pydantic v2 schemas, FastAPI service, Authlib for OAuth2/SMART-on-FHIR, `fhir.resources` for typed FHIR objects. LLM provider is pluggable behind LiteLLM with a deterministic mock for tests. LangSmith tracing enabled in non-test environments. Choice over Pydantic AI: longer-arc personas (biller denial clustering, MA med-rec, write-side approval gates) are graph-shaped; pay the LangGraph tax now to avoid migration later.

**Verification before output, two deterministic tiers.** Every brief item passes through the verifier before the user sees it. **Tier 1 (structural):** every citation ID came from a tool response the model actually saw; cited row's `pid` matches the current patient; cited table is allowed for the claim type; for typed facts (numbers, dates, drug names from coded fields), the value is re-extracted from the source row and string-compared. **Tier 2 (output schema):** brief items are constrained to a closed enum of claim types (`med_current`, `med_change`, `overdue`, `recent_event`, `agenda_item`, `code_status`, `allergy`); regex denylist on rendered text blocks advisory phrasing (`"I recommend"`, `"you should"`, `"consider"`). **Tier 3 (LLM-as-judge for paraphrase fidelity)** is documented but deferred. **Failure UX:** failed items are silently dropped from the rendered brief and logged to `llm_call_log` for offline review.

**Synthetic data only; designed for HIPAA Path 2 readiness.** Demo runs on synthetic charts; no BAA required today. Architectural seams (per-tool minimum-necessary filtering on outbound payloads, hash-stamped audit log, OAuth2 client posture) are in place from day one so a future Path 2 deployment requires configuration and a BAA, not a rewrite.

**Inline LLM call for MVP, queue seam designed in.** The PHP REST endpoint blocks on the sidecar's response (latency budget ~3–5s). The `BriefService` interface is shaped so an Arq + Redis worker can be inserted between PHP and sidecar later without API changes.

### What this buys

A working physician brief that is **verifiable** (every claim traceable to a source row), **auditable** (one log row per LLM call with hashes and verification status), **mockable end-to-end** (deterministic tests with no network), and **extensible** into the remaining personas without architectural rework.

---

## 1. System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser (physician on patient summary page)                    │
│   - jQuery panel, click "Generate brief"                        │
└──────────────────────────────┬──────────────────────────────────┘
                               │ POST /apis/default/api/ai/brief/{pid}
                               │ (OpenEMR session auth)
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  OpenEMR (PHP, existing process)                                │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ Custom module: oe-module-ai-assistant                   │    │
│  │  • BriefController          (REST handler)              │    │
│  │  • PatientAccessValidator   (closes audit pid HIGH bug) │    │
│  │  • BriefService             (orchestration; sync→async  │    │
│  │                              seam)                      │    │
│  │  • SidecarClient            (HTTP to Python)            │    │
│  │  • AuditLogService          (writes llm_call_log)       │    │
│  │  • Twig panel + jQuery widget                           │    │
│  └────────────────────┬────────────────────────────────────┘    │
└───────────────────────┼─────────────────────────────────────────┘
                        │ POST http://oe-ai-agent:8000/v1/brief
                        │ Body: { pid, fhir_base, bearer_token }
                        │ Header: X-Internal-Auth: <shared secret>
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│  Python sidecar: oe-ai-agent (FastAPI, separate container)      │
│   ┌───────────────────────────────────────────────────────┐     │
│   │ LangGraph StateGraph                                  │     │
│   │  START → fetch_context → llm_call → parse → verify → END │
│   └───────────────────────────────────────────────────────┘     │
│   • Tools (FHIR fetches, OAuth2 with user's token)              │
│   • LiteLLM provider client + MockClient for tests              │
│   • Verifier (Tier 1 structural, Tier 2 schema)                 │
│   • Minimum-necessary filter (per-tool field whitelist)         │
└────────────────────┬────────────────────────────────────────────┘
                     │ FHIR R4 (US Core 8.0) + OAuth2
                     ▼
              [OpenEMR FHIR API at /apis/default/fhir]
```

**Why two services, not one PHP module:**
- Python's agent ecosystem (LangGraph, Pydantic AI, LiteLLM, LangSmith) is materially better-supported than PHP's. The MVP loop is small, but the system grows toward multi-step graphs (writes, biller persona, Tier 3 verifier) where this matters.
- Forces all sidecar→OpenEMR data access through the official FHIR + OAuth2 surface — no internal backchannel. This is the integration shape the audits push for and bakes HIPAA-relevant scoping in from day one.
- Independent scaling and deployment lifecycle. The agent can be redeployed without restarting OpenEMR.

**Why not a fully external service** (browser → sidecar directly):
- The browser would need to manage OAuth2 to two services, and the agent's actions would not be visible to OpenEMR's session-based UI permissions. PHP module as broker keeps the user-facing trust boundary at OpenEMR.

---

## 2. Wedge: Physician Patient Summary (MVP)

**User story.** A primary care physician opens a patient chart 60 seconds before walking into the room. They click "Generate brief" on the patient summary page. Within ~3–5 seconds, a panel renders 3–7 short items: what's changed since last visit, what's overdue, what they wrote on the pre-visit form, code status if relevant, and one thing not to forget. Each item is paraphrased by the model but accompanied by a verbatim excerpt from the cited source row, so the doc can spot-check.

**Inputs:** `pid` (patient ID), authenticated user (from OpenEMR session).
**Outputs:** A list of `BriefItem` objects, each with `type`, `text` (paraphrase), `verbatim_excerpts[]`, `citations[]`, `verified=true`. Items that fail verification are dropped silently and logged.

**Flow:**
1. Doc opens chart. Patient summary page renders.
2. Twig panel injected via `Events/Patient/Summary/PatientSummaryPageEvent` shows a "Generate brief" button.
3. Click → `POST /apis/default/api/ai/brief/{pid}` (OpenEMR session-authenticated).
4. PHP `PatientAccessValidator` runs `AclMain::aclCheck(...)` and verifies user has access to that `pid` per their phpGACL roles and `see_auth` setting (`docs/openemr/auth.md` §4). On fail: 403, no agent call, audit row marked `denied`.
5. PHP mints a short-lived OAuth2 bearer token on the user's behalf with narrow read scopes (`patient/Patient.read patient/Condition.read patient/MedicationRequest.read patient/AllergyIntolerance.read patient/Encounter.read patient/Observation.read patient/DocumentReference.read`) and posts the request to the sidecar.
6. Sidecar runs the LangGraph (`fetch_context → llm_call → parse → verify`) and returns the verified brief.
7. PHP writes the `llm_call_log` row (hashes, token counts, verification status, integrity HMAC), returns the brief to the browser.
8. Browser renders verified items with verbatim excerpts available on hover/expand.

---

## 3. Component Architecture

### 3.1 OpenEMR custom module (`oe-module-ai-assistant`)

**Location:** `interface/modules/custom_modules/oe-module-ai-assistant/`

```
oe-module-ai-assistant/
├── openemr.bootstrap.php        # registers event listeners with kernel dispatcher
├── composer.json                # PSR-4: OpenEMR\Modules\AiAssistant\
├── info.txt                     # module name + version for `modules` table
├── version.php
├── Module.php                   # optional Laminas MVC config (REST routes)
├── sql/
│   └── install.sql              # CREATE TABLE llm_call_log (existing convention)
├── src/
│   ├── Controller/
│   │   └── BriefController.php  # POST /api/ai/brief/{pid}
│   ├── Service/
│   │   ├── BriefService.php             # orchestration; today sync, queue-seam
│   │   ├── PatientAccessValidator.php   # closes audit pid HIGH bug locally
│   │   ├── SidecarClient.php            # HTTP to Python, retries, timeout
│   │   ├── AuditLogService.php          # llm_call_log writes
│   │   └── BearerTokenMinter.php        # user-scoped OAuth2 token
│   ├── DTO/
│   │   ├── BriefRequest.php
│   │   ├── BriefResponse.php
│   │   └── BriefItem.php
│   └── Event/
│       └── PatientSummaryPanelSubscriber.php  # injects Twig panel
├── templates/
│   └── patient_summary_panel.html.twig
├── public/
│   └── js/brief_panel.js        # jQuery + XHR + render
└── config/
    └── services.php             # DI wiring per CLAUDE.md
```

**Event subscriptions (MVP):**
- `Events/Patient/Summary/PatientSummaryPageEvent` → render Twig panel
- `Events/RestApiExtend/RestApiResourceServiceEvent` → register `/api/ai/brief/{pid}` route

**Conventions per `CLAUDE.md`:**
- `declare(strict_types=1)` on every file
- Constructor DI; no `$GLOBALS` access in business logic
- `OEGlobalsBag` for any settings reads
- `QueryUtils` (Doctrine DBAL path) for all SQL
- PHPStan level 10 clean, no baseline entries
- All DTOs are `final readonly`

### 3.2 Python sidecar (`oe-ai-agent`)

**Location:** new top-level `oe-ai-agent/` directory in repo (separate Python project).

```
oe-ai-agent/
├── pyproject.toml               # uv-managed; Python 3.12+
├── Dockerfile
├── ruff.toml                    # mirrors PHPStan-level-10 strictness in spirit
├── mypy.ini                     # strict mode
├── src/oe_ai_agent/
│   ├── main.py                  # FastAPI app + /v1/brief endpoint
│   ├── config.py                # env, secrets, internal auth
│   ├── schemas/
│   │   ├── brief.py             # BriefItem, BriefResponse, AgentState
│   │   └── tool_results.py      # TypedRow, ToolResult
│   ├── agent/
│   │   ├── graph.py             # LangGraph StateGraph builder
│   │   └── nodes/
│   │       ├── fetch_context.py
│   │       ├── llm_call.py
│   │       ├── parse_output.py
│   │       └── verify.py
│   ├── tools/
│   │   ├── fhir_client.py       # httpx + Authlib + fhir.resources
│   │   ├── demographics.py
│   │   ├── active_problems.py
│   │   ├── active_medications.py
│   │   ├── allergies.py
│   │   ├── recent_encounters.py
│   │   └── recent_observations.py
│   ├── llm/
│   │   ├── client.py            # LiteLLM wrapper
│   │   ├── mock_client.py       # deterministic mock for tests
│   │   └── prompts.py           # system prompt, output-schema instructions
│   ├── verifier/
│   │   ├── tier1_structural.py
│   │   ├── tier2_schema.py
│   │   └── constraints.py       # claim-type → allowed-tables map; denylist
│   └── filters/
│       └── minimum_necessary.py  # per-tool field whitelist
└── tests/
    ├── test_verifier.py
    ├── test_tools.py
    ├── test_agent_graph.py
    └── fixtures/
        └── synthetic_charts/    # ~10 representative cases
```

### 3.3 Inter-component communication

**PHP module → sidecar:**
```
POST http://oe-ai-agent:8000/v1/brief
Headers:
  X-Internal-Auth: <shared secret from env>
  Content-Type: application/json
Body:
  {
    "pid": "12345",
    "fhir_base_url": "http://openemr/apis/default/fhir",
    "bearer_token": "<user-scoped short-lived OAuth2 token>",
    "request_id": "<uuid>"
  }
Timeout: 30s
Retry: none for MVP (idempotent retries possible later)
```

**Sidecar → OpenEMR FHIR:**
```
GET /apis/default/fhir/Patient/{id}
Authorization: Bearer <user's token, passed through>
```

The sidecar never holds long-lived credentials. Each request gets a fresh short-lived token minted by the PHP module on the user's behalf. This means agent reads inherit the user's ACL (`see_auth`, facility scope, sensitivities) automatically — there is no privileged path around the existing access-control surface.

---

## 4. Framework Choices

| Layer | Choice | Why | Alternatives considered |
|---|---|---|---|
| **Agent orchestration** | **LangGraph** | Graph shape fits the longer arc (writes → human-in-loop, biller persona → multi-step, Tier 3 verifier → conditional branch). LangSmith tracing for evals from day one. | Pydantic AI (right-sized for MVP but migration cost when shape grows); Claude Agent SDK / OpenAI Agents SDK (provider lock-in); thin custom (no migration story). |
| **LLM provider abstraction** | **LiteLLM** | Provider-agnostic; same call shape for Anthropic, OpenAI, Azure, Vertex, local. Mockable via custom `LLMRouter` class. | Provider-native SDKs (lock-in); raw httpx (reinventing). |
| **Sidecar web framework** | **FastAPI** | Pydantic-native, async, auto-OpenAPI, dominant ecosystem. | Litestar (newer, smaller); Flask (less ergonomic for typed APIs). |
| **HTTP client (sidecar)** | **httpx** | Async, modern, mockable via `respx`. | aiohttp (less ergonomic). |
| **OAuth2 client** | **Authlib** | Mature, handles SMART-on-FHIR flows, integrates with httpx. | Manual token management (avoid). |
| **FHIR object types** | **fhir.resources** | Pydantic models for FHIR R4. Typed FHIR objects vs dict-soup. | Raw dicts (loses verifier guarantees); google's fhirclient (less Pythonic). |
| **Schema validation** | **Pydantic v2** | Already in via Pydantic AI / fhir.resources. One source of truth across schemas, tools, output. | Marshmallow (older); attrs + cattrs (less ecosystem). |
| **PHP REST framework** | **Existing OpenEMR REST infrastructure** (`apis/dispatch.php` + `RoutesExtensionListener`) | Don't add a new HTTP layer when the module pattern already handles routing. | Standalone Symfony app inside module (overkill). |
| **PHP schema/validation** | **Symfony Validator + Symfony Serializer** | Already in stack per `CLAUDE.md`. | Hand-rolled (regression risk). |
| **DB access (PHP)** | **Doctrine DBAL via `QueryUtils`** | Per `CLAUDE.md`. | ADODB legacy surface (avoid for new code). |
| **Migration (PHP)** | **`sql/install.sql`** in module + `sql_upgrade.php` hook | Existing module convention; simpler to ship. Doctrine Migrations are "NOT fully integrated" per `db/README.md`. | Doctrine Migrations (preferred long-term but not yet wired for modules). |
| **Frontend** | **Twig template + jQuery + vanilla XHR** | jQuery is already loaded on the page. Panel has 3 states. No need for SPA. | React/Vue (ceremony); htmx (small ecosystem in OpenEMR). |
| **Worker/queue (when async)** | **Arq (Redis)** | Lighter than Celery; sufficient for the workload. | Celery (operational weight); Symfony Messenger (PHP-side, but the work is in Python). |
| **LangGraph checkpointer** | **`MemorySaver`** for MVP; **`RedisSaver`** when async | MVP is single-process; Redis when we need persistence across worker restarts or human-in-the-loop pauses. | Postgres saver (more weight than needed). |
| **Testing (PHP)** | **PHPUnit 11** isolated tests + integration tests via OpenEMR's test harness | Per `CLAUDE.md`. | — |
| **Testing (Python)** | **pytest + pytest-asyncio + respx + LangGraph test models** | Async, deterministic, no-network. | unittest (less ergonomic). |
| **Static analysis** | **PHPStan level 10** (PHP), **mypy strict + ruff** (Python) | Mirrors CLAUDE.md's discipline on both sides. | — |

---

## 5. Tool Layer & Data Access

### 5.1 Tool result shape

Every tool returns labeled rows so the verifier can resolve citations:

```python
class TypedRow(BaseModel):
    resource_type: str        # FHIR resource type, e.g., "Condition"
    resource_id: str          # FHIR id
    patient_id: str           # FHIR Patient/{id} reference; verifier checks == current
    last_updated: datetime    # from FHIR meta.lastUpdated
    fields: dict[str, Any]    # whitelisted fields per minimum-necessary filter
    verbatim_excerpt: str | None  # for free-text fields, raw text for verifier display
```

### 5.2 MVP tools

| Tool | FHIR resource | Whitelisted fields |
|---|---|---|
| `get_demographics(pid)` | `Patient` | `name`, `birthDate`, `gender` (no SSN, no address, no phone) |
| `get_active_problems(pid)` | `Condition` (filter `clinicalStatus=active`) | `code`, `recordedDate`, `clinicalStatus` |
| `get_active_medications(pid)` | `MedicationRequest` (filter `status=active`) | `medicationCodeableConcept`, `dosageInstruction`, `authoredOn` |
| `get_allergies(pid)` | `AllergyIntolerance` | `code`, `reaction`, `criticality` |
| `get_recent_encounters(pid, limit=5)` | `Encounter` (sort `-period.start`) | `period`, `type`, `reasonCode`, `participant.individual` |
| `get_recent_observations(pid, days=90)` | `Observation` (lab category) | `code`, `valueQuantity`, `effectiveDateTime`, `interpretation` |
| `get_recent_notes(pid, limit=3)` | `DocumentReference` (clinical note type) | `description`, `date`, `author`, `content[].attachment.title` + `verbatim_excerpt` |

### 5.3 Minimum-necessary filter

Each tool declares its whitelist statically. The filter (`filters/minimum_necessary.py`) applies the whitelist to the FHIR response *before* the LLM sees the data. On synthetic data this is mostly discipline; for HIPAA Path 2 deployment, this is the exact mechanism that satisfies §164.502(b) minimum-necessary on outbound payloads.

### 5.4 Why FHIR, not direct services-layer

For an in-process PHP module, calling `\OpenEMR\Services\PatientService` directly would be ~125–300ms faster per call (audit §2.1). We accept the cost for MVP because:
- Sidecar is out-of-process — services-layer access would require an internal RPC anyway.
- FHIR enforces resource shape, scope checking, and audit logging that we'd otherwise re-implement.
- When writes arrive, FHIR PUT/PATCH inherits OpenEMR's existing validators and event dispatch.

If sidecar latency becomes a problem, the path forward is response caching keyed on `Patient/{id}` + `Resource.meta.lastUpdated`, not collapsing to services-layer direct.

---

## 6. Verification Strategy

Verification runs *after* the LLM produces structured output and *before* the user sees anything. It is deterministic — no second LLM call in MVP.

### 6.1 Tier 1 — Structural (always enforced)

For each `BriefItem`:
1. **Citation existence.** Every `citation.resource_id` must appear in the tool-response row set the model saw on this turn. Forbids fabricated IDs.
2. **Patient binding.** Every cited row's `patient_id` must equal the request's `pid`. Forbids cross-patient leakage.
3. **Type-table compatibility.** `citation.resource_type` must be in `ALLOWED_TABLES_FOR_TYPE[item.type]`. For example:
   - `med_current` → `MedicationRequest`
   - `med_change` → `MedicationRequest` and `DocumentReference` (note documenting the change)
   - `allergy` → `AllergyIntolerance` only
   - `code_status` → `Observation` (LOINC 75320-2) or `DocumentReference`
4. **Typed-fact re-extraction.** For `BriefItem.text` containing a number, date, or coded drug name, re-extract the value from the cited row and string-compare. Catches digit transposition (`creatinine 1.8` vs `8.1`).
5. **Staleness.** If `cited_row.last_updated < now - max_age[item.type]`, the item is rendered with a "stale" badge or dropped, depending on type. Code status: never drop, always show with date. Recent labs: drop if older than 90 days.

### 6.2 Tier 2 — Output-schema constraints (always enforced)

Enforced by Pydantic schema + LLM system prompt + post-parse validation:
1. **Closed type enum.** `BriefItem.type` ∈ `{med_current, med_change, overdue, recent_event, agenda_item, code_status, allergy}`. No free-form `recommendation`, `diagnosis`, `assessment` types. The model literally cannot emit them through the schema.
2. **Advisory denylist on rendered text.** Regex blocks: `r"\b(I recommend|you should|consider stopping|consider starting|rule out|likely has|probably|might want to)\b"`. Blocks the model from drifting into advice or diagnostic conclusions, which are out of scope for a *summary* agent.
3. **Citation count floor.** Every item must have ≥1 citation. Items with zero citations are dropped — they cannot be a fact about this chart.

### 6.3 Tier 3 — Paraphrase fidelity (deferred)

Documented for post-MVP. A second cheap-model LLM-as-judge pass reads each surviving item plus its cited rows and answers "is this claim supported by these rows?" Disagreements get flagged, not auto-blocked. Why deferred:
- Adds cost, latency, and another hallucination surface.
- Tier 1 + Tier 2 catch most mechanical errors; paraphrase drift is the remaining gap.
- Mitigation already in MVP: rendered output shows verbatim excerpts alongside paraphrase, so the doc can spot-check.

### 6.4 Failure UX

- Items that fail any Tier 1 or Tier 2 check are **silently dropped** from the rendered brief.
- All failures are logged to `llm_call_log.verification_failures` (JSON array) for offline review.
- If *every* item fails, the brief renders as: "No verified items to report — generate again or open chart manually." The user is never shown unverified material framed as fact.

### 6.5 Known limitations

These belong in this document, not buried in code:

1. **Citation-but-misinterpretation.** The model can pick a real row that exists but doesn't actually support its paraphrase. Tier 1 catches structural mismatches; verbatim excerpt rendering is the human-facing mitigation. Tier 3 is the systemic fix.
2. **Source data is itself wrong.** Cited rows can be stale, misclassified, or contain typos. The verifier confirms "this came from the chart"; it cannot confirm "the chart is right."
3. **Constraint enumeration is incomplete.** Clinical rules are vast; we encode the obvious ones. Missing rules → false negatives (verifier passes something it shouldn't).
4. **Negation and temporality on free text.** "Patient denies chest pain" cited as "patient has chest pain" is a verifier blind spot. Mitigated by structured-output schema (only certain claim types allowed) but not eliminated. Likely the largest residual risk for MVP.
5. **Tool-response context window.** If a tool returns 200 rows and the model cites #167, the verifier confirms #167 exists in the response set, but the model may have only meaningfully attended to the first 50. Mitigation: tools cap response sizes (e.g., observations limited to most recent 25 in 90 days) and surface this cap in the prompt.

---

## 7. LangGraph Agent Design

### 7.1 State

```python
class AgentState(BaseModel):
    pid: str
    user_token: SecretStr
    request_id: str

    # Populated by fetch_context
    tool_results: list[TypedRow] = []
    fetch_errors: list[ToolError] = []

    # Populated by llm_call
    raw_llm_output: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0

    # Populated by parse_output
    parsed_items: list[BriefItem] = []
    parse_errors: list[ParseError] = []

    # Populated by verify
    verified_items: list[BriefItem] = []
    verification_failures: list[VerificationFailure] = []
```

### 7.2 Graph

```
START → fetch_context → llm_call → parse_output → verify → END
```

For MVP this is a linear chain. The LangGraph investment pays off in v2 when:
- A `paraphrase_check` (Tier 3) node is inserted between `verify` and `END`.
- Branching after `verify` on `verification_status` routes to alternate render paths.
- For writes: `propose_change` → `human_approval` (LangGraph `interrupt_before` — graph pauses, browser shows approval UI, user confirms or rejects, graph resumes) → `apply_change`.

### 7.3 Node responsibilities

**`fetch_context`** — Runs the MVP tools in parallel (they're independent FHIR calls). Each tool failure is captured but does not abort the graph; a brief on partial data is still useful. Populates `tool_results`.

**`llm_call`** — Composes the system prompt (closed-type enum, citation-required instruction, denylist phrasing reminder), serializes `tool_results` into a structured context block keyed by FHIR resource ID, and calls LiteLLM with `response_format={"type": "json_schema", "json_schema": BriefResponse.schema()}`. Records token counts.

**`parse_output`** — Pydantic-validates `raw_llm_output` into `list[BriefItem]`. On schema failure, populates `parse_errors`, leaves `parsed_items` empty. Verifier still runs but produces an empty brief — failure UX renders the "no verified items" message.

**`verify`** — Runs Tier 1 + Tier 2. Populates `verified_items` and `verification_failures`. Pure function — given the same state, always produces the same result.

### 7.4 Tracing

LangSmith is enabled in non-test environments via `LANGSMITH_API_KEY` env var. Each brief generates one trace with all four nodes visible, including tool inputs, LLM call payload, parse outcome, and verifier decisions. Tests use `langgraph.pregel.test` utilities and never hit LangSmith.

---

## 8. Audit Logging

### 8.1 Schema (`llm_call_log`)

Doctrine Migration / `sql/install.sql` for the module:

```sql
CREATE TABLE llm_call_log (
    id                       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    request_id               CHAR(36)        NOT NULL,
    user_id                  BIGINT UNSIGNED NOT NULL,
    patient_id               BIGINT UNSIGNED NOT NULL,
    action_type              VARCHAR(32)     NOT NULL,        -- "brief.read" today; "*.write" later
    model_id                 VARCHAR(128)    NOT NULL,
    prompt_tokens            INT             NOT NULL DEFAULT 0,
    completion_tokens        INT             NOT NULL DEFAULT 0,
    request_hash             CHAR(64)        NOT NULL,        -- sha256 of canonical request payload
    response_hash            CHAR(64)        NOT NULL,        -- sha256 of canonical response payload
    tool_calls               JSON            NULL,            -- [{tool, input_hash, row_count}]
    verification_status      ENUM('passed', 'partial', 'failed', 'denied') NOT NULL,
    verification_failures    JSON            NULL,
    integrity_checksum       CHAR(64)        NOT NULL,        -- HMAC-SHA256 over canonical row
    prev_log_hash            CHAR(64)        NULL,            -- for hash-chaining (post-MVP)
    created_at               TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_patient_user_time (patient_id, user_id, created_at),
    INDEX idx_request_id (request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 8.2 What gets logged

- **Always:** request hash, response hash, user, patient, model, token counts, verification status, integrity HMAC, timestamp.
- **Never (in MVP):** raw prompt, raw response, raw tool output. Only hashes.
- **Optional via flag (non-prod only):** raw payloads under `LLM_AUDIT_DEBUG=1`. Never enabled in production paths because it would meaningfully expand PHI exposure surface.

### 8.3 Integrity

`integrity_checksum` is `HMAC-SHA256(secret_key, canonical_serialization(row_minus_checksum))`. Detects single-row tampering by anyone without the key. **This is detective, not preventive** — the same caveat as OpenEMR's existing `log.checksum` (audit §1.7). Real defense for production is ATNA forwarding to an external append-only store, deferred to deployment hardening.

`prev_log_hash` is reserved (NULL) for MVP. Post-MVP, populating this with the previous row's `integrity_checksum` extends to a hash chain — tamper-evident even if the attacker has the HMAC key, as long as the chain is periodically anchored externally.

### 8.4 Reconciliation with OpenEMR's existing audit

Every agent FHIR call already produces an `api_log` row through OpenEMR's existing middleware (`ApiResponseLoggerListener`). `llm_call_log` is supplementary, not replacement. The `request_id` is propagated as a header on FHIR calls so `api_log.request_url` rows for a given brief can be joined to the `llm_call_log` row.

---

## 9. Security Posture vs Audit Findings

Mapping against `docs/openemr/AUDIT.md` §6.1 ("Five Things to Fix First") and §6.4 (pre-flight checklist):

| Audit finding | Severity | Mitigation in this architecture |
|---|---|---|
| `interface/globals.php:155-157` — `pid` from `$_GET` without ownership check | **HIGH** | `PatientAccessValidator` runs `AclMain::aclCheck` before any agent call. Sidecar additionally enforces via user-token scope. Two layers. |
| `pid` in URL query strings | High | Brief endpoint uses `pid` in URL *path*, not query. Acknowledged: matches existing OpenEMR convention. Mitigation depends on TLS + reverse proxy log scrubbing in deployment. |
| No application-layer rate limiting | Medium | nginx `limit_req` on `/apis/default/api/ai/*` documented in deployment guide. Sidecar has internal `asyncio.Semaphore` cap on concurrent LLM calls. Per-user rate limit deferred to v2. |
| Document encryption opt-in | High | MVP does not read raw documents from disk; `DocumentReference` access is via FHIR which respects existing scope checks. Filesystem encryption is out of scope for this architecture. |
| Synchronous `api_log` writes | Medium | Our `llm_call_log` write is also synchronous in MVP. Documented for queue migration. |
| OPcache disabled | Medium | Out of scope for this architecture; flagged in deployment guide. |
| `pid` ACL — agent can compose URLs | HIGH (audit's stated concern about AI agents) | User-token pass-through means the agent literally cannot fetch what the user couldn't. No system-level scope in MVP. |
| No de-identification utility | n/a for MVP | Synthetic data only. Minimum-necessary filter is the seam for Path 2 deployment. |
| Synthetic vs PHI | n/a for MVP | This architecture is HIPAA Path 2-ready (filter, audit log, OAuth2). Path 2 requires a BAA and the §5.7 deployment checklist; not built-in. |

### 9.1 Authorization model in this architecture

- **PHP module trusts OpenEMR session.** Standard OpenEMR auth applies to `BriefController`.
- **Agent inherits user scope via token pass-through.** No new identity, no system-level backdoor in MVP.
- **Sidecar trusts the PHP module via shared secret** (`X-Internal-Auth` header). Defense in depth on internal Docker network. For multi-host deployment, upgrade to mTLS.
- **No scope expansion within sidecar.** The bearer token is used as-is; sidecar cannot mint or upgrade scopes.

### 9.2 What this architecture does *not* solve

These audit findings are deployment-time or out of scope for an agent module:
- Document encryption at rest (filesystem-level concern)
- ATNA log forwarding (deployment configuration)
- HTTPS / HSTS enforcement (reverse proxy)
- Hardcoded passwords in production Docker compose (ops)
- OPcache / globals caching / N+1 query elimination (core OpenEMR perf)

They remain on the deployment hardening checklist in `docs/openemr/AUDIT.md` §5.7.

---

## 10. Performance & Async

### 10.1 Latency budget per brief (synthetic data, single user)

| Stage | Estimate |
|---|---|
| OpenEMR middleware + auth (audit §2.1) | 125–300ms |
| `PatientAccessValidator` ACL check | ~20ms |
| Token mint | ~30ms |
| HTTP to sidecar (LAN) | ~5ms |
| Sidecar: parallel FHIR fetches (~6 calls, hits middleware floor each) | 400–1200ms (parallel, bound by slowest) |
| LLM call (model-dependent) | 1500–3500ms |
| Verifier + parse | ~20ms |
| Audit log write | ~15ms |
| HTTP back to PHP + render | ~10ms |
| **Total** | **~2.1–5.1s** |

For a single user clicking "Generate brief" on a synthetic-data demo: acceptable inline.

For production load (multiple physicians, real-time): introduce Arq + Redis between PHP `BriefService` and sidecar. PHP returns `job_id`; browser polls `GET /api/ai/brief/job/{id}`. Sidecar publishes results to Redis keyed by `job_id`. PHP serves polled responses from Redis without re-calling the sidecar.

### 10.2 Concurrency

- Sidecar runs FastAPI with uvicorn workers. Default 2 workers per container.
- Internal `asyncio.Semaphore(N)` caps concurrent LLM calls per worker (default `N=4`) to prevent runaway loops from saturating the LLM API.
- LangGraph nodes within a single graph run are sequential; the only parallelism is inside `fetch_context`'s tool fan-out.

### 10.3 Caching (post-MVP)

- FHIR responses are cacheable by `(resource_type, resource_id, meta.lastUpdated)`. Redis with short TTL would absorb the per-request middleware floor for repeat reads within a session.
- LLM responses are not cached — each brief is per-patient, per-moment.

---

## 11. Read-Write Extension Path

When write capability arrives (post-MVP):

1. **New write tools.** E.g., `propose_problem_status_change(pid, condition_id, new_status)`. Tools call OpenEMR FHIR with PUT/PATCH using the user's bearer token, so existing OpenEMR ACLs, validators, and audit logs apply automatically.
2. **New LangGraph nodes.** Insert `propose_change` (LLM emits structured proposed change with citations) → `human_approval` (uses LangGraph `interrupt_before` — graph pauses, browser polls or receives push, user confirms/rejects, graph resumes) → `apply_change` (executes the FHIR write).
3. **New BriefItem subtype.** `ProposedChange { target_resource: TypedRow, current_value: ..., proposed_value: ..., rationale: str, citations: [...] }`. Verifier enforces that `target_resource.patient_id == pid` and that `rationale` cites at least one source row.
4. **Audit log gains an `action_type` value.** `brief.write_proposed`, `brief.write_applied`, `brief.write_rejected`. Same schema, no migration.
5. **Deployment-time gate.** Writes are gated behind a per-deployment feature flag and a phpGACL permission (`ai_assistant.write`). MVP module ships with the flag off and the permission unassigned.

No topology changes. No schema changes to `llm_call_log`. The investment in LangGraph specifically pays off here — `interrupt_before` is a first-class primitive for human-in-the-loop approval.

---

## 12. Testing Strategy

### 12.1 PHP module (PHPUnit 11)

- **Isolated unit tests** for `PatientAccessValidator`, `BearerTokenMinter`, `AuditLogService`, `SidecarClient`. Each runs without Docker per `CLAUDE.md`'s isolated test convention.
- **Integration tests** for the `BriefController` end-to-end with sidecar mocked at the HTTP layer.

### 12.2 Python sidecar (pytest)

- **Unit tests** for each Tier 1 and Tier 2 verifier rule. Deterministic, fast — these are the safety net.
- **Unit tests** for each tool's minimum-necessary filter. Asserts no whitelisted-out fields leak.
- **Integration tests** for full LangGraph runs with `MockLlmClient` returning canned tool calls and final outputs, plus `respx` mocking FHIR responses.
- **Golden brief suite.** ~10 synthetic charts × per-chart expected brief items × per-item expected verifier outcomes. Asserts both that good items pass and that injected bad items fail (e.g., a synthetic chart where the model is fed a `Patient/{wrong_pid}` row to confirm Tier 1 catches the cross-patient mismatch).

### 12.3 End-to-end (Cypress or Playwright)

- Docker compose stack (OpenEMR + sidecar + Redis). Synthetic chart loaded.
- Test: log in as physician, open patient chart, click "Generate brief", assert ≥1 verified item renders and verbatim excerpt is reachable on hover.

### 12.4 Mock LLM strategy

A `ScriptedLlmClient` returns pre-canned responses per test case. For each canned response, the test asserts:
- The graph completes without exceptions.
- The verifier rejects items it should reject.
- The audit log row is correctly populated (status, hashes, token counts).

This means **the MVP can be developed and CI-tested without ever calling a real LLM API.**

---

## 13. Deployment Topology

### 13.1 docker-compose additions

```yaml
services:
  oe-ai-agent:
    build: ./oe-ai-agent
    environment:
      LLM_PROVIDER: ${LLM_PROVIDER:-mock}
      OPENEMR_FHIR_BASE: http://openemr/apis/default/fhir
      INTERNAL_AUTH_SECRET: ${INTERNAL_AUTH_SECRET}
      LANGSMITH_API_KEY: ${LANGSMITH_API_KEY:-}
      LANGSMITH_PROJECT: oe-ai-agent-dev
    networks:
      - oe-internal
    depends_on:
      - openemr

  redis:
    image: redis:7-alpine
    networks:
      - oe-internal

networks:
  oe-internal:
    internal: true   # not exposed to host
```

### 13.2 Module installation

1. Place module at `interface/modules/custom_modules/oe-module-ai-assistant/`.
2. Admin → Modules → Manage Modules → install + activate (sets `mod_active=1` in `modules` table).
3. Module bootstrap registers event listeners on next request.
4. `sql/install.sql` creates `llm_call_log`.

### 13.3 Production hardening (deferred — see deployment guide, not built into MVP)

Per audit §5.7:
- ATNA log forwarding (`enable_atna_audit=true`)
- `enable_auditlog_encryption=true`
- nginx `limit_req` on `/apis/default/api/ai/*`
- TLS + HSTS at reverse proxy
- OPcache enabled (audit's #1 free win)
- Strip dev-easy / production compose hardcoded passwords
- BAA with LLM provider before flipping `LLM_PROVIDER` away from `mock`

---

## 14. Known Tradeoffs & Open Questions

### 14.1 Tradeoffs we explicitly accepted

1. **PHP-Python polyglot.** Two languages, two test stacks, two CI lanes. Cost vs benefit: Python's agent ecosystem (LangGraph, Pydantic, LiteLLM, LangSmith) is meaningfully better. Cost is one Dockerfile and a sidecar deploy.
2. **LangGraph day one over Pydantic AI.** MVP is a single-LLM-call problem; LangGraph is over-engineered for it. We pay the over-engineering tax now in exchange for no migration when the shape grows (writes → human-in-loop, biller persona → multi-step, Tier 3 verifier → conditional branch).
3. **FHIR over OAuth2 vs in-process services-layer.** ~125–300ms-per-call latency tax (audit §2.1) for every FHIR fetch. Acceptable for MVP. Caching layer is the path forward, not collapsing to direct services-layer access.
4. **User-token pass-through over agent-as-system-client.** Pass-through inherits user ACL automatically (good — closes the audit's `pid` HIGH finding by construction). System-client is more typical for service-to-service but requires re-implementing scope checks. We chose pass-through; system-client is the long-term answer for background/scheduled agent runs (no logged-in user).
5. **Inline LLM call over queue.** ~3–5s blocking HTTP request. Fine for one user clicking; doesn't scale. Queue seam is in the `BriefService` interface.
6. **Tier 3 LLM-as-judge deferred.** Paraphrase drift on free-text notes is not caught by Tier 1 + 2 alone. Mitigated by displaying verbatim excerpt alongside paraphrase (the doc can spot-check). Likely the largest residual verification risk for MVP.
7. **Synthetic data assumption.** Most audit recommendations (encryption-at-rest, ATNA forwarding, OPcache, hardcoded prod passwords) are deferred to a deployment hardening doc, not enforced by this architecture. Path 2 is reachable but requires real ops work.
8. **No streaming UI.** Brief is short; no token-streaming. Adds 1–3s perceived latency vs streaming UX. Acceptable for MVP.
9. **Hash audit log, not full payload.** Cannot replay LLM exchanges from logs alone. Tradeoff is exposure footprint of stored prompts/responses. Debug flag exists for non-prod.
10. **No per-user rate limiting.** Sidecar has only a global concurrency cap. A single user in a loop could exhaust the cap. Acceptable for MVP demo; per-user limit needed for production.

### 14.2 Open questions to resolve before implementation

- **Synthetic data source.** Synthea? Hand-crafted? OpenEMR's existing demo dataset? We need ~50 representative charts with known ground truth for the golden brief suite. Recommend Synthea + 10 hand-curated edge-case charts for the verifier suite.
- **LLM provider for live demo.** Claude Sonnet 4.6 is the current default for this kind of structured-output task; GPT-4o-mini or Haiku 4.5 are cheaper. Cost target?
- **Auto-render vs click-to-render.** MVP defaults to click-to-render. Auto-render matches the persona's "60 seconds before walking in" need but costs more. Decision point after first usage data.
- **LangSmith account.** Project setup needed before first traced run.
- **Internal auth between PHP and sidecar.** Shared secret for MVP; mTLS for multi-host deploy. Confirm deployment shape.

---

## 15. File Layout Summary

```
openemr/                                                        # existing repo root
├── docs/
│   ├── agent/
│   │   └── ARCHITECTURE.md                                     # this document
│   ├── openemr/
│   │   ├── AUDIT.md
│   │   ├── AUDIT2.md
│   │   ├── audit-concise.md
│   │   ├── auth.md
│   │   └── module-architecture.md
│   └── users.md
├── interface/modules/custom_modules/oe-module-ai-assistant/    # PHP module
│   ├── openemr.bootstrap.php
│   ├── composer.json
│   ├── info.txt
│   ├── version.php
│   ├── Module.php
│   ├── sql/install.sql
│   ├── src/
│   │   ├── Controller/BriefController.php
│   │   ├── Service/{BriefService,PatientAccessValidator,SidecarClient,AuditLogService,BearerTokenMinter}.php
│   │   ├── DTO/{BriefRequest,BriefResponse,BriefItem}.php
│   │   └── Event/PatientSummaryPanelSubscriber.php
│   ├── templates/patient_summary_panel.html.twig
│   ├── public/js/brief_panel.js
│   └── config/services.php
└── oe-ai-agent/                                                # Python sidecar (new)
    ├── pyproject.toml
    ├── Dockerfile
    ├── ruff.toml
    ├── mypy.ini
    ├── src/oe_ai_agent/
    │   ├── main.py
    │   ├── config.py
    │   ├── schemas/{brief,tool_results}.py
    │   ├── agent/graph.py
    │   ├── agent/nodes/{fetch_context,llm_call,parse_output,verify}.py
    │   ├── tools/{fhir_client,demographics,active_problems,active_medications,allergies,recent_encounters,recent_observations}.py
    │   ├── llm/{client,mock_client,prompts}.py
    │   ├── verifier/{tier1_structural,tier2_schema,constraints}.py
    │   └── filters/minimum_necessary.py
    └── tests/
        ├── test_verifier.py
        ├── test_tools.py
        ├── test_agent_graph.py
        └── fixtures/synthetic_charts/
```
