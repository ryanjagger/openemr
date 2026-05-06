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
