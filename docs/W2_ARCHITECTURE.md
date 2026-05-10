# W2 Architecture

Architecture notes for the AI co-pilot built into OpenEMR. The system is split
between a PHP module (`interface/modules/custom_modules/oe-module-ai-agent/`)
that lives inside OpenEMR's request lifecycle and a Python FastAPI sidecar
(`oe-ai-agent/`) that hosts the LangGraph agents, retrieval, and LLM calls.

```
Browser ── PHP module ──HTTP+HMAC──▶ Python sidecar ──▶ LLM / Cohere / FHIR
                │                          │
                └── MySQL (jobs, audit)    └── corpora/clinical-guidelines/
```

## Document Ingestion Flow

Uploaded clinical documents (lab reports, intake forms) are ingested
asynchronously and routed to native OpenEMR tables (`procedure_result`,
`questionnaire_response`) so they show up in the chart and become available to
downstream FHIR tools.

1. **Enqueue (PHP, foreground).** The user picks documents in the chat UI and
   POSTs to `DocumentIngestionController::ingest`. The controller validates
   patient access, then `DocumentIngestionRepository::createJob` writes a job
   row plus one `job_document` row per selected `Document` id.
   ([`oe-module-ai-agent/src/Controller/DocumentIngestionController.php`](interface/modules/custom_modules/oe-module-ai-agent/src/Controller/DocumentIngestionController.php),
   [`Service/DocumentIngestionRepository.php`](interface/modules/custom_modules/oe-module-ai-agent/src/Service/DocumentIngestionRepository.php))

2. **Launch (PHP, background).** `DocumentIngestionLauncher::launch` shells out
   to `bin/console background:services run --name=AI_Document_Ingestion_Task`
   so the worker runs out-of-band from the HTTP response. If `exec()` is
   unavailable the job waits for the regular OpenEMR background-services cron.

3. **Process job (PHP worker).** `DocumentIngestionWorker::processPendingJobs`
   claims pending jobs, marks them `processing`, then iterates each
   job document. For each document it:
   - Loads the OpenEMR `Document`, checks deletion/expiration and that it is
     still attached to the same patient.
   - Reads bytes via `Document::get_data()`, enforces
     `AI_AGENT_MAX_DOCUMENT_BYTES`, base64-encodes.
   - Calls the sidecar `POST /v1/documents/extract` via `SidecarClient` with
     an HMAC `X-Internal-Auth` header.

4. **Extract (sidecar).** `oe_ai_agent.main.extract_document` validates the
   `DocumentExtractionRequest`, opens an observability trace, and delegates to
   `llm.document_extraction.extract_document_with_llm`. The LLM is called with
   a JSON schema response format and a per-call cap of
   `AI_AGENT_DOCUMENT_MAX_TOKENS`. The mock provider returns a deterministic
   placeholder so dev environments don't burn credits.
   ([`oe-ai-agent/src/oe_ai_agent/main.py:379`](oe-ai-agent/src/oe_ai_agent/main.py),
   [`llm/document_extraction.py`](oe-ai-agent/src/oe_ai_agent/llm/document_extraction.py))

5. **Localize sources.** `_localize_envelope_bboxes` runs each
   `SourceSnippet` back through the PDF/PNG via
   `documents/bbox_localizer.py` so the UI can deep-link to a page region for
   each cited fact.

6. **Route extraction (PHP).** `DocumentIngestionWorker::routeExtraction`
   dispatches by `document_type`:
   - `lab_report` → `AiLabIngestionService` → `procedure_result` rows.
   - `intake_form` → `AiIntakeIngestionService` → `questionnaire_response`
     rows.
   Anything else throws — the schema (`DocumentIngestionSchema`) constrains
   the type, so an unknown value is a programming error upstream.

7. **Audit.** Every extraction (success or failure) writes an
   `LlmCallLogEntry` via `AuditLogService` with hashes of request/response,
   token counts, latency, cost in micro-USD, and step trace JSON. PHI is never
   stored in the audit table — only SHA-256 hashes and metadata.

8. **Reach the agent.** Once ingestion completes, the document content is
   queryable through standard FHIR tools (lab observations,
   QuestionnaireResponse) — no special chat-context plumbing. While ingestion
   is still running, the chat agent's `extractor` worker can pick up
   `unindexed_documents` directly from the sidecar to answer in-flight
   questions before the job finishes.

Failure handling: per-document errors are recorded on the `job_document` row
with a 1000-char truncated message; the job continues to the next document.
`finalizeJob` rolls the per-document statuses into a final job status.

## Supervisor / Worker Graph

The chat agent is a LangGraph state machine with a supervisor that routes per
turn between two workers and a finalizer.

```
START
  → ensure_chat_context
  → supervisor ⇄ { extractor, evidence_retriever }
              ↓ (when supervisor → finalize)
  → finalize
  → parse_envelope
  → verify_chat
  → END
```

([`oe-ai-agent/src/oe_ai_agent/agent/graph_chat.py`](oe-ai-agent/src/oe_ai_agent/agent/graph_chat.py))

### Nodes

- **`ensure_chat_context`** — hydrates `ChatState` for the turn:
  conversation history, cached context from prior turns, list of
  `unindexed_documents` for the patient, in-flight extraction jobs.

- **`supervisor`** — LLM-driven router that emits structured JSON
  (`{next, reason}`) conforming to `SupervisorRoute` and uses
  `Command(goto=...)` to dispatch. **Guardrails are deterministic and run
  before the LLM call:**
  - `supervisor_turns_remaining <= 0` → force `finalize` (default cap 6).
  - `extractor_runs >= 2` → strip `extractor` from the choice set.
  - `evidence_runs >= 3` → strip `evidence_retriever`.
  - `unindexed_documents == []` → strip `extractor` (no work to do).
  After the LLM responds, its choice is honored only if still in the allowed
  set; otherwise we fall back to the first allowed route.
  ([`agent/nodes/supervisor.py`](oe-ai-agent/src/oe_ai_agent/agent/nodes/supervisor.py))

- **`extractor` worker** — bounded tool-loop (`max_iterations=3`) restricted
  to `EXTRACTOR_TOOL_NAMES` (`list_unindexed_documents`,
  `extract_documents`). Pulls document bytes via the sidecar's extraction
  endpoint and merges resulting `IndexedDocumentFact` rows into
  `cached_context`. If the underlying job is still running the loop sees the
  `EXTRACTION_PENDING_SENTINEL` and surfaces `extraction_pending=true` so the
  UI can poll. Returns `Command(goto="supervisor")`.
  ([`agent/nodes/extractor.py`](oe-ai-agent/src/oe_ai_agent/agent/nodes/extractor.py))

- **`evidence_retriever` worker** — bounded tool-loop (`max_iterations=4`)
  restricted to `EVIDENCE_TOOL_NAMES` — every chat tool except the extractor
  ones (FHIR demographics/labs/meds/problems/etc., indexed-document search,
  clinical guideline retrieval). Mirrors the historical single-node
  `llm_turn`, but is now scoped per call by the supervisor and may run up to
  three times in a turn. Returns `Command(goto="supervisor")`.
  ([`agent/nodes/evidence_retriever.py`](oe-ai-agent/src/oe_ai_agent/agent/nodes/evidence_retriever.py))

- **`finalize`** — only writer of the response envelope. Calls the LLM with
  the full `cached_context` and a strict response format that enforces the
  citation-required, allowed-fact-types contract.

- **`parse_envelope` + `verify_chat`** — unchanged from the legacy linear
  graph. Verifier is **deterministic and LLM-free** (Tier 1 structural,
  Tier 2 schema). First failing rule drops the item; an LLM judge would land
  in a separate Tier 3 node, not here.

### Shared invariants

- **Field whitelists are HIPAA-load-bearing.** Every tool reaching FHIR has
  an entry in `filters/minimum_necessary.py:TOOL_FIELD_WHITELIST`. New tools
  without a whitelist will leak fields the LLM should not see.
- **`LlmClient` is a Protocol.** Graph code never branches on provider;
  selection happens once in `main._llm_client()`.
- **Trace context is a contextvar**, not a parameter — `async with use_trace()`
  / `async with step("name")`. State updates flow through `Command(update=...)`
  on a Pydantic `ChatState`; nodes never mutate state directly.

## RAG Design

Two retrieval surfaces, both presented to the agent as ordinary tools so the
supervisor → evidence_retriever loop can decide when to call them.

### 1. Clinical guideline corpus (USPSTF / CDC / NHLBI)

Hybrid keyword + dense + reranker retrieval over 47 markdown documents
organized into 7 topic categories
(`oe-ai-agent/corpora/clinical-guidelines/`). Each document carries YAML
front matter (`source_organization`, `grade`, `population`, `topic_tags`,
…) used both for filtering and for prepending to the rerank input.

**Pipeline** (`oe-ai-agent/src/oe_ai_agent/guidelines/retriever.py`):

1. **Load + chunk.** `corpus.load_guideline_documents` parses front matter;
   `chunk_guideline_documents` splits at H2/H3 headers, retaining metadata
   per chunk. Chunks and embeddings are persisted to a SQLite cache
   (`AI_AGENT_GUIDELINE_INDEX_DIR`, default
   `.rag_cache/clinical_guidelines.sqlite`) so cold-starts only re-embed
   missing chunks.

2. **Keyword leg.** Local BM25 (`guidelines/bm25.py`) over the chunk text,
   filtered by optional `category` / `topic_tag`. Top-25.

3. **Dense leg.** Cohere `embed-v4.0` for both documents and queries, gated
   on `COHERE_API_KEY`. Cosine similarity over the same filtered chunk set,
   top-25. If Cohere is unavailable we fall through to keyword-only and
   surface `cohere_not_configured_keyword_only` /
   `cohere_embedding_failed_keyword_only` warnings.

4. **Fuse.** Reciprocal Rank Fusion (`RRF_K = 60`) combines the two ranked
   lists.

5. **Rerank.** Top-40 fused candidates go to Cohere `rerank-v4.0-fast` with
   the original query. Fallback to fused order on failure
   (`cohere_rerank_failed_fused_results`).

6. **Snippet + return.** Up to 10 results (default 6), each with a
   ~700-char snippet windowed around the first matched query term, plus a
   `retrieval_method` tag (`keyword_only` / `hybrid_fused` /
   `hybrid_rerank`) so callers and traces can see which path served the
   answer.

Exposed to the agent as the `clinical_guidelines` tool
(`tools/clinical_guidelines.py`); facts cite back into the corpus via
`source_organization`, `title`, and `publication_date`.

### 2. Patient document index (per-patient, ephemeral)

Documents extracted by the ingestion flow are stored in their **native**
OpenEMR tables (`procedure_result`, `questionnaire_response`).
The agent retrieves them through targeted FHIR tools
(`lab_trend`, `observation_search`, `questionnaire_responses`, etc.). The
extractor worker also handles the just-uploaded case where ingestion is
still in flight: it lists `unindexed_documents` from the sidecar and pulls
extracted facts on demand, so a clinician can ask about a freshly uploaded
PDF before the job has reached the chart.

This is intentional — the design avoids a parallel patient-document vector
store and reuses the chart as the source of truth.

### Why hybrid + rerank for guidelines?

Guideline language is dense and consistent (USPSTF "Grade B recommendation",
CDC "ACIP recommends…"), which favors keyword recall, but clinician queries
are paraphrastic, which favors dense recall. RRF + cross-encoder rerank
gives us both without committing to either being primary, and the keyword
fallback keeps the system functional when the embedding provider is down or
unconfigured.

## Evals

> _Placeholder — to be expanded._

The eval harness lives in `oe-ai-agent/evals/` and runs the **real LLM**
against curated golden fixtures for both the brief agent
(`run_brief_eval.py`, `brief_fixtures/`) and the chat agent
(`run_chat_eval.py`, `chat_fixtures/`). Fixtures form a Stage-1 golden set:
hand-curated input/output pairs that define correctness; failures mean
either a real regression or a fixture that needs updating.

Topics to cover here once the section is written out:

- Fixture schema (FHIR snapshots + loose expectations over verified items;
  the chat fixtures additionally exercise tool-routing and citation
  expectations).
- Determinism strategy — chat fixtures also run under the
  `MockLlmClient.synthesizing` mock in CI to catch harness / tool-routing
  / citation regressions without spending API credits.
- A/B prompt and model comparisons via `--label` runs and the artifacts
  written to `evals/runs/`.
- Document-extraction evals (not yet wired) — what a golden set should
  look like for lab and intake extraction, including bbox localization
  accuracy.
- Verifier-only tests as a fast inner loop separate from the live-LLM
  evals (`tests/test_verifier.py`).
- How eval results gate prompt / model bumps before merge.

See `oe-ai-agent/evals/README.md` for current fixtures, expectation keys,
and `jq` snippets for inspecting runs.
