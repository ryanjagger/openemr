# oe-module-ai-agent
Custom OpenEMR module enabling AI features in OpenEMR.

### Confirm AI Agent sidecar reachability from the host:

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

### 2. Generate a brief or start a chat

1. Use Patient Finder (**Finder** navigation in header) to find and retrieve a patient.
2. On the patient's **Dashboard** page, look for **AI Patient Brief** card and select **Generate brief** for a summary of that patient's records
3. To the right of **Dashboard**, select **Co-pilot** for AI chat with patient records.
4. In **Uploaded document context**, select **Load recent documents**, classify recent PDF/PNG uploads as **Lab report** or **Intake form**, then select **Ingest selected documents**. Completed ingestions are added to subsequent co-pilot chat turns as evidence-backed `DocumentReference` context.

### Document ingestion settings

- `AI_AGENT_MAX_DOCUMENT_BYTES` controls the maximum PDF/PNG size sent to the AI sidecar for extraction. The default is `10485760` bytes.
