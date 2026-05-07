# Session History

## deploy with railway
claude --resume "deploy-openemr-railway"

## module architecture
claude --resume 29bc85b5-a161-46f4-be48-a0fa3882d7b2

## deploy troubleshooting
claude --resume 6d42bbce-3d1d-4b3f-a5f8-a57ef4b2e36f

## hello-world Plan: AI-Generated Patient Summary Card on the Dashboard
claude --resume 29bc85b5-a161-46f4-be48-a0fa3882d7b2
 ~/.claude/plans/okay-so-i-m-in-silly-engelbart.md

# local dev workflow
1. Bring up the dev stack (pulls images + runs initial install, waits for healthchecks):
docker compose -f /Users/ryan/gauntlet/openemr/docker/development-easy/docker-compose.yml up --detach --wait

2. Reset DB + load demo data (wipes and reseeds):
docker compose -f /Users/ryan/gauntlet/openemr/docker/development-easy/docker-compose.yml exec -T openemr /root/devtools dev-reset-install-demodata

Plus the verification query I ran after:
docker compose -f /Users/ryan/gauntlet/openemr/docker/development-easy/docker-compose.yml exec -T mysql mariadb -uopenemr -popenemr openemr -e "SELECT COUNT(*) FROM patient_data;"

Notes:
- -T on exec disables TTY allocation — needed because I was running non-interactively. If you run these by hand from a terminal, you can drop -T.
- If you cd docker/development-easy first, you can shorten to docker compose up --detach --wait and docker compose exec openemr /root/devtools dev-reset-install-demodata (the form used in CONTRIBUTING.md).
- Step 2 is destructive — it drops and recreates the openemr database before reseeding.

## 2026-05-06 clinical guideline RAG for co-pilot chat
codex resume 019dfb5e-cb13-7ed0-8ae1-7ac11159d7f8

Implemented a basic hybrid RAG path in `oe-ai-agent` for a small clinical guideline corpus placed at `oe-ai-agent/corpora/clinical-guidelines/`.

What changed:
- Added markdown corpus parsing, section chunking, metadata preservation, BM25 keyword retrieval, Cohere embeddings/rerank support, SQLite embedding cache, and keyword-only fallback under `oe-ai-agent/src/oe_ai_agent/guidelines/`.
- Added `search_clinical_guidelines` as a model-callable chat tool. It returns `ClinicalGuidelineChunk` rows with source metadata and evidence snippets.
- Added `guideline` chat facts and verifier support for global guideline evidence (`patient_id="__global__"`), while keeping patient-bound checks for chart/FHIR evidence.
- Added prompt guidance so guideline/screening/prevention/pharmacology questions call the guideline tool.
- Adjusted guideline-only narrative fallback so verified guideline source cards display cleanly even if generated prose fails strict number/date grounding.
- Updated chat UI copy from misleading "fact dropped by verifier" to "verifier issue(s)".
- Wired local Docker with `COHERE_API_KEY`, mounted `corpora/` and `.rag_cache/`, copied `corpora/` into the sidecar Docker image, and updated Railway staging to include `oe-ai-agent/corpora`.

Local validation:
- Rebuilt/restarted `oe-ai-agent` in `docker/development-easy`.
- Verified the container has `COHERE_API_KEY` and can see `/app/corpora/clinical-guidelines`.
- Tested app chat with opioid guideline question; retrieved verified CDC opioid prescribing guideline cards.
- Ran sidecar checks: `uv run pytest -q` (96 passed), `uv run ruff check`, and `uv run mypy src`.

Deployment notes:
- Set `COHERE_API_KEY` on the `oe-ai-agent` service.
- Deploy the updated sidecar image so `corpora/` is present in `/app/corpora`.
- Optional persistent cache: set `AI_AGENT_GUIDELINE_INDEX_DIR` to a mounted volume path to avoid re-embedding after restarts.

## 2026-05-06 staging environment in deploy3
claude --resume 7b340f25-6c39-49b9-9513-e6dcb5d2bfbc

Stood up a `staging` environment alongside `production` in the `deploy3` Railway project (`1dcb624a-bc85-4329-912e-ca2beb04a2c1`) so deploys can be validated before reaching real users. Staging env id: `3d1c659f-d3e6-4db6-a49b-f95050163e32`. Public domain: `openemr-staging-79ff.up.railway.app`.

What changed:
- `tools/railway/deploy.sh`: removed the silent `production` default; `--environment` is now **required** and the script exits with `--environment is required …` if omitted. Added a `[railway-deploy] deploying to environment: <name>` banner before each `railway up`. Updated `usage()` and examples to show explicit `--environment staging|production`.
- `tools/railway/import-synthea-demo-patients.sh`: dropped the `&& mkdir -p /root/synthea/output/ccda` from the pre-clear step. That `mkdir -p` was inadvertently re-creating `/root/synthea` and defeating the dev tools' lazy-install gate (`if [[ ! -d /root/synthea ]]` in `/root/devtoolsLibrary.source` line 500), causing `java: not found` on fresh containers.
- `docs/railway-deployment-gotchas.md`: added a "Staging environment" section covering the `--environment` flag, what differs across envs (volumes, DB, secrets), the `targetPort` reminder, and the `railway environment staging` switch needed before seeding.

Railway-side setup (one-time):
- Duplicated `production` → new env `staging` in the dashboard. All env vars carried over; volumes and MariaDB data fresh.
- Regenerated `INTERNAL_AUTH_SECRET` (set the same new value on `staging.openemr` and `staging.oe-ai-agent`) so a leaked staging secret can't authenticate to prod.
- Set `LANGFUSE_ENVIRONMENT=staging` and `OPENEMR_DOCKER_ENV_TAG=staging`.
- Cross-service URLs (`AI_AGENT_SIDECAR_URL`, `AI_AGENT_FHIR_BASE_URL`, `OPENEMR_FHIR_BASE`) already used `*.railway.internal` form, which Railway scopes per-environment automatically — no retargeting needed.
- `targetPort` on the auto-generated `staging.openemr` domain inherited `80` from the duplicate (gotcha #4 GraphQL fix not needed this time, but verify on any future env duplication).

Deploy commands:
```sh
tools/railway/deploy.sh openemr     --environment staging
tools/railway/deploy.sh oe-ai-agent --environment staging
tools/railway/deploy.sh all         --environment production
```

Seeding staging:
```sh
railway environment staging
RAILWAY_OPENEMR_SERVICE=openemr tools/railway/import-synthea-demo-patients.sh --count 50
```
Result on first seed: 48 patients / 2137 encounters / 2128 list rows (50 CCDAs imported, 2 deduped — documented gotcha).

Notable side issue resolved during seeding:
- Dev container had `/root/synthea` as an empty dir from a prior wedged run. Manually unblocked with `apk add openjdk17-jre` and `wget …/synthea-with-dependencies.jar` inside the container. The script patch above prevents this state from being created in the first place going forward.

## 2026-05-06 supervisor + workers chat graph (extractor for unindexed PDFs)
claude --resume 5a82a694-9589-4b99-9ff1-8dc00fabfcee

Replaced the linear chat LangGraph in `oe-ai-agent` with an inspectable supervisor + workers topology so the co-pilot can detect recently-uploaded PDFs and run extraction in-turn before answering.

Topology:
```
START → ensure_chat_context → supervisor ⇄ {extractor, evidence_retriever}
                                  ↓
                               finalize → parse_envelope → verify_chat → END
```

Python (`oe-ai-agent`):
- New nodes under `agent/nodes/`: `supervisor.py` (LLM-driven router with structured `SupervisorRoute` JSON, hard guardrails on `supervisor_turns_remaining`/`extractor_runs`/`evidence_runs`), `extractor.py`, `evidence_retriever.py`, `finalize.py`. Workers return `Command(goto="supervisor")` after running. Shared `_tool_loop.py` factored out of the deleted `llm_turn.py`.
- New tools `tools/unindexed_documents.py` — `list_unindexed_documents` wraps `GET /api/ai/documents/recent/:pid`; `extract_documents` POSTs `/ingest/:pid`, then blocking-polls `GET /:pid/jobs/:jobId` (60s default cap) and returns the resulting `IndexedDocumentFact` rows.
- `tools/chat_registry.py` partitioned into `EVIDENCE_TOOL_NAMES` and `EXTRACTOR_TOOL_NAMES` with `evidence_tools_schema()` / `extractor_tools_schema()`. `CHAT_TOOL_REGISTRY` stays the union dispatch table.
- `ChatState` extended with `unindexed_documents`, `supervisor_decisions`, `supervisor_turns_remaining`, `extractor_runs`, `evidence_runs`. New `schemas/unindexed_document.py` for the manifest shape.
- `FhirClient.api_post()` added.
- `ensure_chat_context` now does the cheap unindexed-doc lookup so the supervisor sees the list without spending an LLM iteration.
- `MockLlmClient.synthesizing()` learned the `SupervisorRoute` schema: deterministic mock-mode routing from prompt content (extractor when unindexed + document-intent words, else evidence, else finalize).
- Tests: `test_chat_graph.py` rewritten with a `_detect_role(messages)` helper that drives supervisor + workers correctly. New end-to-end test for the extractor path (POST ingest → poll job → fetch facts → finalize). 98/98 pass; ruff/mypy clean.

PHP module (`interface/modules/custom_modules/oe-module-ai-agent/`):
- `DocumentIngestionController`: user resolution moved from session-only reads (`$_SESSION['authUserID']`/`['authUser']`) to `HttpRestRequest::getRequestUser()` with session fallback. Source-of-truth is now the explicit, request-bound user array that every REST authorization strategy populates.
- Same controller: `recent`/`ingest`/`job` were passing the raw `$pid` (UUID for agent calls) to `PatientAccessValidator::canRead()` — but `canRead` requires `ctype_digit($pid)`. Switched all three to pass `(string) $patientId` (resolved int) so bearer-token + browser flows both work. `indexed`/`indexedFacts` already did this; the inconsistency was the bug.
- Route reshape: `GET /api/ai/documents/jobs/:pid/:jobId` → `GET /api/ai/documents/:pid/jobs/:jobId`. The old shape derived an invalid scope `user/:pid.r` because `HttpRestParsedRoute::parseRouteParams` only pops one trailing `:identifier`, leaving `:pid` as the resource. New shape derives `user/jobs.r` cleanly. Updated `Bootstrap.php`, Python `unindexed_documents.py`, and `public/js/chat_panel.js`.
- `BearerTokenMinter::CHAT_READ_SCOPES` and `sql/install.sql` extended with `user/recent.read user/ingest.write user/jobs.read user/document.write`. These are the scopes OpenEMR's URL-derived inference requires for the new sub-routes; they're internal scope identifiers for our private OAuth client, not real OpenEMR resources.
- `SidecarClient::CHAT_TIMEOUT_SECONDS` raised 90 → 180. Supervisor + evidence + (optional) extractor + finalize routinely runs longer than a single-LLM-call chat; observed 97s on one turn the 90s cap killed mid-flight. Override per-deploy with `AI_AGENT_CHAT_TIMEOUT_SECONDS`.
- Module version bumped 0.1.1 → 0.1.2; new upgrade `sql/0_1_1-to-0_1_2_upgrade.sql` updates the OAuth client scope set for existing installs.

Local validation:
- Rebuilt `oe-ai-agent` (source is baked into the image, not bind-mounted). Confirmed new code in container with `grep documents/.*jobs /app/src/...`.
- Applied the upgrade SQL to running MariaDB to refresh OAuth scopes without a full module reinstall.
- End-to-end on a real lipid-panel PDF: supervisor → extractor → ingest job → poll-until-completed → 5 lab facts (Total Chol 232, HDL 48, LDL 158, Trig 178, Non-HDL 184) → finalize → 6 verified facts (5 `lab_result` + 1 `document_fact`), `narrative_grounded: true`, 0 verification failures.
- 98/98 pytest, ruff clean, mypy clean. PHP phpcs/phpstan clean against pre-existing baseline.

Diagnostic gotchas surfaced during validation (kept here so they're easy to find next time):
- The `recentEligibleDocuments` SQL excludes documents whose `categories_to_documents` row points to a category that no longer exists. Two test PDFs (ids 2203, 2204) had orphan category 36 references and were silently invisible. Fix: re-categorize via UI or `UPDATE categories_to_documents SET category_id = 2 WHERE document_id IN (...)`.
- Apache mod_php sets `PHP_BINARY=""`. The core `SymfonyBackgroundServiceSpawner` (used by the periodic `BackgroundServiceRestController` triggered on every page view) fails with "First element must contain a non-empty program name" because of this. Orthogonal to our flow — `DocumentIngestionLauncher::launch()` uses a separate `exec()`-based path with a `'php'` fallback when `PHP_BINARY` is empty, so our ingestion still runs. Job rows confirm completion.

Open follow-ups (not blockers):
- `ADVISORY_DENYLIST` regex in `verifier/constraints.py` doesn't catch promise-of-future-action phrases like "let me retrieve", "I'll fetch", "checking now". The chat system prompt forbids these but the verifier passes them. Add to denylist.
- When `cached_context` is empty, the finalize node fabricates a "let me retrieve" narrative instead of saying "the chart does not have that information" plainly. Tweak the finalize prompt for the empty-context case.
- Latency: 25–40s typical with Sonnet for supervisor + 2 worker rounds + finalize. Move supervisor to Haiku for cheap routing wins (~2s × ≥2 calls per turn saved).
