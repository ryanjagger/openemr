# oe-module-ai-agent

OpenEMR custom module for the AI Agent patient-brief feature. See
`/ARCHITECTURE.md` and `/TASKS.md` at the repo root for the full design.

## Phase 1 — walking-skeleton smoke test

The Phase 1 build wires the full request path with stubs. No ACL check, no
FHIR fetches, no LLM. Clicking the button should round-trip
`browser → PHP module → Python sidecar` and render one hardcoded item.

### Prerequisites

The dev-easy stack must be running with the new sidecar + redis services:

```bash
cd docker/development-easy
docker compose up --detach --wait
```

Confirm sidecar reachability from the host:

```bash
curl -fs http://localhost:8400/healthz
# → {"status":"ok"}
```

### 1. Install the module

1. Visit `https://localhost:9300/` and log in (`admin` / `pass`).
2. Open **Admin → Modules → Manage Modules**.
3. Find **AI Agent** under *Unregistered Modules*. Click **Register**.
4. Once it appears under *Installed but not Activated*, click **Install** then
   **Enable**.

### 2. Generate a brief

1. Navigate to any patient (e.g. **Patient/Client → Patients** → pick one).
2. Open the patient's **Summary** page.
3. The "AI Patient Brief" panel renders above the demographics card.
4. Click **Generate brief**. Within a second the content area should show
   one item with type `agenda_item` and text
   "Walking-skeleton stub for pid=&lt;numeric pid&gt;".

### Pass criteria

- Panel renders on the patient summary page with the **Generate brief** button.
- Clicking the button transitions through *loading* → *rendered*.
- The rendered item shows the patient's pid, proving the value flowed
  browser → PHP route → sidecar → response.

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| Panel does not appear | Module not enabled; revisit step 1 |
| Click does nothing | Browser console will show the fetch error |
| `HTTP 401` | API CSRF token didn't validate — confirm the session is fresh |
| `HTTP 502 / sidecar_unreachable` | `docker compose logs oe-ai-agent`; confirm the container is healthy |
| `HTTP 404` | Module installed but route not registered — `docker compose exec openemr tail -200 /var/log/apache2/error_log` for stack traces |

### Optional Langfuse tracing

For synthetic/demo runs, set Langfuse project keys before starting the stack:

```bash
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
export LANGFUSE_BASE_URL=https://us.cloud.langfuse.com
```

The sidecar still returns and stores the local `ResponseMeta` trace when
Langfuse is not configured. Raw Langfuse prompt/tool/output capture is for
synthetic data only.
