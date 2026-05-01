# Railway deployment gotchas

Notes from standing up `openemr-deploy` on Railway (OpenEMR + MariaDB + the
Python `oe-ai-agent` sidecar). These are non-obvious issues that cost real
time. None of them is in Railway's docs at the moment.

## TL;DR

The deployable artifacts at the repo root are:

| File | Purpose |
| --- | --- |
| `Dockerfile.railway` | Overlays this branch's source on top of `openemr/openemr:next` (v8), fixes Apache to listen dual-stack on port 80, copies in the `railway-entrypoint.sh` wrapper. (We started on `:7.0.3` but had to switch — see "Base image must match the module's APIs" below.) |
| `railway-entrypoint.sh` | Seeds the empty Railway volume from `/var/sites-template` on first boot, then `exec`s the upstream `./openemr.sh`. |
| `railway.json` | Pins the Dockerfile path. **No `healthcheckPath`** — see "Healthcheck timeout" below. |

The sidecar (`oe-ai-agent/Dockerfile`) and MariaDB (`mariadb:11.8.6` image)
deploy without custom Dockerfiles.

Manual deploys are wrapped by:

```sh
tools/railway/deploy.sh openemr
tools/railway/deploy.sh oe-ai-agent
tools/railway/deploy.sh all
```

The script creates the small staging directories described below and pushes
them with `railway up`; it also guards that the linked Railway project is
`deploy3` before deploying. If Railway reports "no changes detected in watch
paths" but you intentionally want a new image, pass `--force`; this modifies
only the temporary staged Dockerfile with a timestamp label and leaves the
worktree unchanged.

## 1. The empty volume shadows the image's site template

OpenEMR's image ships a `sites/default/sqlconf.php.example` (and a default
`sqlconf.php`) under `/var/www/localhost/htdocs/openemr/sites/`. Mounting an
empty Railway volume at that path **shadows** the entire baked tree, leaving
the auto-installer with nothing to read. You'll see the container crash-loop
on:

```
PHP Fatal error: Failed opening required '/var/www/localhost/htdocs/openemr/sites/default/sqlconf.php'
```

Fix is to copy the baked-in `sites/` somewhere safe at build time and seed
the volume on first boot. `Dockerfile.railway` does:

```Dockerfile
RUN cp -a /var/www/localhost/htdocs/openemr/sites /var/sites-template
```

…and `railway-entrypoint.sh` runs before the upstream entrypoint:

```sh
if [ -d "$TEMPLATE_DIR" ] && [ ! -f "$SITES_DIR/default/sqlconf.php" ]; then
    cp -a "$TEMPLATE_DIR/." "$SITES_DIR/"
    chown -R apache:apache "$SITES_DIR" 2>/dev/null || true
fi
exec /var/www/localhost/htdocs/openemr/openemr.sh "$@"
```

This pattern (save → seed-on-first-boot → exec-original) generalises to any
Docker image that bakes content into a directory the platform later mounts a
volume on top of.

## 2. Healthcheck timeout vs. OpenEMR's first-boot installer

The auto-installer creates the schema, writes `sqlconf.php`, and
`chmod 400`s the entire `vendor/` tree before Apache binds. On a fresh
deploy this took **~8 minutes** in my run. Railway's default
`healthcheckTimeout` is 300s, so the deploy gets marked failed before
Apache has even started.

Two safe options:

- Drop `healthcheckPath` from `railway.json` and rely on TCP readiness
  (what we did).
- Set `healthcheckTimeout: 900` and `healthcheckPath: /meta/health/readyz`
  (works, but adds 10+ minutes of waiting on every deploy).

After the first install, sqlconf.php is already on the volume so subsequent
boots are ~1 minute (just the chmod walk).

## 3. Apache `Listen 0.0.0.0:80` — IPv4 only

The `openemr/openemr:7.0.3` image's `httpd.conf` hardcodes:

```
Listen 0.0.0.0:80
```

That's IPv4-only. Railway's edge proxy reaches upstream over IPv6, so port
80 is unreachable. (Port 443 happens to be `Listen 443` which binds
dual-stack — but Railway can't proxy to a TLS upstream that only speaks
plaintext-after-handshake from a self-signed cert, so 443 isn't the answer
either.)

Fix lives in `Dockerfile.railway`:

```Dockerfile
RUN sed -i 's/^Listen 0.0.0.0:80$/Listen 80/' /etc/apache2/httpd.conf
```

After this, `netstat -tln` inside the container shows `:::80 LISTEN` — IPv6
dual-stack — and Railway can reach it.

## 4. **Service-domain `targetPort` can be silently null** (the worst one)

This was the bug that took the longest to find. After fixing 1–3 above, the
container was fully healthy:

- `netstat` showed Apache on `:::80` and `:::443`
- `curl http://localhost/` from inside the container returned `302 → /interface/login/login.php?site=default`
- From a sibling service (`oe-ai-agent`), `urllib.request.urlopen("http://openemr.railway.internal/")` returned `200 OK`

…but the public domain `openemr-production-d404.up.railway.app` kept
returning `HTTP 502` with header `x-railway-fallback: true`. Restarting,
redeploying, toggling the domain port between 80/443 — none of it worked.

Root cause: the service domain had `targetPort: null`. Railway's edge has
no idea what container port to forward to, so it serves the fallback page.

**Both of these tools claim to set the port and lie:**

- The Railway MCP `railway-agent`'s `updateServiceTool` returns `"applied"`
  but the GraphQL state still has `targetPort: null` afterwards.
- `railway domain --service openemr --port 80` from the CLI doesn't
  actually persist the port either.

**The fix** is a direct GraphQL call against
`https://backboard.railway.com/graphql/v2` using the auth token from
`~/.railway/config.json` (`user.accessToken`):

```graphql
# 1. Find the serviceDomainId
query Q($projectId: String!, $environmentId: String!, $serviceId: String!) {
  domains(projectId: $projectId, environmentId: $environmentId, serviceId: $serviceId) {
    serviceDomains { id domain targetPort }
  }
}

# 2. Update the targetPort
mutation U($input: ServiceDomainUpdateInput!) {
  serviceDomainUpdate(input: $input)
}
# Input shape:
# {
#   "domain":           "<existing domain>",
#   "environmentId":    "<env id>",
#   "serviceId":        "<service id>",
#   "serviceDomainId":  "<id from step 1>",
#   "targetPort":       80
# }
```

Returns `{"data": {"serviceDomainUpdate": true}}`. The 502 clears
**instantly** — no redeploy or restart needed.

Always verify after any domain-config tool action:

```bash
TOKEN=$(python3 -c "import json; print(json.load(open('$HOME/.railway/config.json'))['user']['accessToken'])")
curl -s -X POST https://backboard.railway.com/graphql/v2 \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"query":"query Q($p:String!,$e:String!,$s:String!){domains(projectId:$p,environmentId:$e,serviceId:$s){serviceDomains{id domain targetPort}}}","variables":{"p":"<projectId>","e":"<envId>","s":"<serviceId>"}}'
```

If `targetPort` comes back `null`, the domain is broken regardless of what
any tool's response said.

## 5. The Railway MCP agent confabulates

Twice during this deploy the `railway-agent` tool reported actions that
hadn't happened:

- "Old domain deleted, new domain created at `openemr.railway.app`" — the
  old domain was still bound, no new domain existed, and
  `openemr.railway.app` is Railway's corporate marketing site (not a service
  domain at all).
- "targetPort updated to 80" — `targetPort` stayed `null` afterwards.

Treat the agent's responses as proposals to verify, not facts. After any
configuration action, query the actual state via GraphQL or check the
Railway dashboard.

## 6. CLI panics: `railway volume add`

`railway volume --service <name> add --mount-path <path>` panics with:

```
thread 'main' panicked at src/commands/volume.rs:571:10:
called `Option::unwrap()` on a `None` value
```

Workaround: have the MCP `railway-agent` create the volume via
`createVolumeTool`. That works.

## 7. Set secrets as plain strings, not shell expansions (already documented)

See the existing `feedback_railway_env_vars.md` memory: never embed `$(...)`,
backticks, or `${...}` in `railway variables --set` values. The MariaDB
image evaluates the password through a shell during init, so command
substitutions get *executed* and the stored password becomes a different
random string than the one you intended.

Generate the secret in your **local** shell first, then pass the resolved
plain value:

```bash
INTERNAL_AUTH_SECRET="$(openssl rand -hex 32)"          # local shell expands
railway variables --service openemr \
  --set "INTERNAL_AUTH_SECRET=${INTERNAL_AUTH_SECRET}"  # Railway gets plain hex
```

## 8. `railway up` archive size — exclude with a staging dir

A naive `railway up` from the OpenEMR repo root tries to upload ~213MB
(post-gitignore). The upload times out against Railway's backend.

Workaround used here: stage only what `Dockerfile.railway` needs (the
module dir + `sql/` + the Dockerfile + entrypoint) into `/tmp/...` and run
`railway up <stagepath> --service openemr --path-as-root`. That dropped the
upload to 4.6 MB and finished in seconds.

For the sidecar, strip `.venv/`, `.mypy_cache/`, `.ruff_cache/`,
`__pycache__/` — those alone were 304 MB.

## 9. Module `COPY --chown` must use `apache`, not `root`

The image's `openemr.sh` boot script `chmod 400`s every file under
`/var/www/localhost/htdocs/openemr/`. Files copied in with
`COPY --chown=root:root` end up `root:root` mode `400`, and Apache (running
as `apache`) literally can't read them — so the module manager doesn't
list the module under "Unregistered". Sibling modules baked into the image
are `apache:root`. Match that:

```Dockerfile
COPY --chown=apache:root interface/modules/custom_modules/oe-module-ai-agent \
     /var/www/localhost/htdocs/openemr/interface/modules/custom_modules/oe-module-ai-agent
```

## 10. Base image must match the module's API surface

The AI agent module is written against OpenEMR master (v8) APIs:
- `OpenEMR\Common\Session\SessionWrapperFactory`
- `CsrfUtils::collectCsrfToken(SessionInterface $session, string $subject): string`
- `OEGlobalsBag` patterns
- `HttpRestRequest::createFromGlobals()`

None of those exist in `openemr/openemr:7.0.3`. On v7 the patient summary
page crashes with:

```
PHP Fatal error: Class "OpenEMR\Common\Session\SessionWrapperFactory" not found
```

Fix is to use `openemr/openemr:next` (v8 nightly) as the base image, not
`:7.0.3`. **Switching base versions requires wiping both volumes** — the v7
mariadb schema is incompatible with the v8 auto-installer, and the v7
`sqlconf.php` in the sites volume confuses v8 boot. See "Volume deletion
is staged, not immediate" below.

## 11. SQL splitter wants one statement per line

OpenEMR's `SQLUpgradeService::upgradeFromSqlFile()` parses the install
script by splitting on **semicolons followed by a newline**, then sends
each chunk to MariaDB as a single query. MariaDB does not allow multiple
statements in one query (no `multi_statements` flag is set), so any line
like:

```sql
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
```

…gets sent as one query and fails with:

```
SQL syntax error near 'EXECUTE stmt; DEALLOCATE PREPARE stmt'
```

The whole module install aborts and the UI surfaces the unhelpful
"ERROR: could not open table.sql, broken form?" (which is the generic
`install_sql` failure message, regardless of the actual error — the real
error only shows up in the Apache PHP error log).

Always write idempotent ALTER blocks with one statement per line:

```sql
SET @sql := IF(@col_exists = 0, 'ALTER TABLE ...', 'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
```

(not `PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;` on
one line, which the SQL splitter treats as a single multi-statement
query.)

## 12. Volume deletion is staged, not immediate (and `volumeDelete` lies)

Calling the GraphQL `volumeDelete(volumeId)` mutation returns
`{"data": {"volumeDelete": true}}` but **does not actually delete a volume
that's currently bound to a service**. The volume stays in `state: READY`
in `project.volumes`, the volume instance stays mounted on the service,
and re-running the same mutation keeps returning `true` without any
state change.

Even calling `accept-deploy` on the environment to commit pending changes
does not free the old volumes — they linger forever once they have a
volume instance attached.

What does work: deleting via the Railway dashboard. From the API alone we
were unable to fully orphan an old volume after the service had picked
up a freshly-created replacement volume. After redeploys the new volumes
take over the mount path, but the dashboard is still the cleanup step.

Practical implication: **do not iterate on volume layout via API** when
data hasn't been committed yet. If you create a volume with the wrong
mount path or wrong service binding, immediately delete it via the
dashboard before doing anything else, or you'll end up with multiple
volumes claiming the same mount path on the same service.

## 14. `AI_AGENT_FHIR_BASE_URL` must be cross-container, not `localhost`

The OpenEMR PHP module passes `AI_AGENT_FHIR_BASE_URL` to the sidecar in
the request body — the sidecar then uses it as `base_url` for `httpx`
calls to FHIR. Naive value:

```
AI_AGENT_FHIR_BASE_URL=http://localhost/apis/default/fhir
```

…works in `docker compose` (all services share `localhost` in the dev
host's network) but **breaks on Railway** because `localhost` resolves
to the sidecar's own container, where no FHIR server is running. Symptom:
every tool call in the trace logs `ConnectError: All connection attempts
failed`, and the LLM responds with the in-prompt fallback "No chart
context has been loaded for this patient…".

Set it to a hostname the sidecar can resolve:

```
AI_AGENT_FHIR_BASE_URL=http://openemr.railway.internal/apis/default/fhir
```

(Same value as `OPENEMR_FHIR_BASE` on the sidecar — the redundancy is
because the PHP side is the source of truth for the per-request URL.)

## 13a. `${{<service-name>.RAILWAY_PRIVATE_DOMAIN}}` silently doesn't interpolate

When you set a Railway env var to a value like:

```
AI_AGENT_SIDECAR_URL=http://${{oe-ai-agent.RAILWAY_PRIVATE_DOMAIN}}:8000
```

…via `railway variables --set` (or `railway add --variables`), Railway
*accepts the value* but doesn't actually resolve the template
reference if the upstream service is referred to by **name**. The CLI
shows the value back as `http://oe-ai-agent.railway.internal:8000` (so
it looks correct in `railway variables`), but inside the running
container the env var is `http://:8000` — empty hostname.

The dependent service then fails with cryptic transport-layer errors
(in our case the OpenEMR module logged `Sidecar transport error` to
`llm_call_log` because the Symfony HttpClient can't resolve an empty
host).

What works:

- Service references by **service ID** UUID (the form Railway uses
  internally): `${{<svc-id>.RAILWAY_PRIVATE_DOMAIN}}`. We saw this work
  for `MYSQL_HOST` because the agent had set it that way.
- Or just hardcode the literal hostname:
  ```
  AI_AGENT_SIDECAR_URL=http://oe-ai-agent.railway.internal:8000
  ```
  Railway's private domains are stable per-service-per-environment, so
  there's no real upside to the template form once the project is
  deployed.

After fixing, run `railway ssh --service <service> -- env | grep <VAR>`
to confirm the *resolved* value the container actually sees, not just
what the CLI reports.

## 13. Service domain ID can change across redeploys

If you delete-and-recreate domain configuration (or if Railway's deploy
churn re-allocates a service domain), the auto-generated hostname
changes — e.g. `openemr-production-d404.up.railway.app` →
`openemr-production-885b.up.railway.app`. The old hostname starts
returning `502` with `x-railway-fallback: true` because Railway's edge
no longer has a routing record for it; the new hostname is published in
Railway's `RAILWAY_PUBLIC_DOMAIN` environment variable on the service.

After any redeploy that involved the service domain, re-query the live
hostname before sharing a link:

```bash
TOKEN=$(python3 -c "import json; print(json.load(open('$HOME/.railway/config.json'))['user']['accessToken'])")
curl -s -X POST https://backboard.railway.com/graphql/v2 \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"query":"query Q($p:String!,$e:String!,$s:String!){domains(projectId:$p,environmentId:$e,serviceId:$s){serviceDomains{id domain targetPort}}}","variables":{"p":"<projectId>","e":"<envId>","s":"<serviceId>"}}'
```

Don't trust bookmarks across deploy churn.

## 15. Repo-root `railway.json` applies to every service in a monorepo

When more than one Railway service is connected to the same GitHub repo
(here: `openemr` and `oe-ai-agent` both pointed at `railway/main`), the
repo-root `railway.json` is **auto-loaded for every service**, even when
each service has its own Root Directory. Railway's docs spell this out:

> "the Railway Config File does not follow the root directory path
> automatically, so you must specify the absolute path for files like
> `railway.json` or `railway.toml`."
> — Railway monorepo deploy docs

Symptom: the sidecar service built using `Dockerfile.railway` (which is
openemr's), with `oe-ai-agent/` as the build context, and failed on:

```
failed to compute cache key: failed to calculate checksum of ref ...
"/railway-entrypoint.sh": not found
```

…because `railway-entrypoint.sh` lives at the repo root and isn't in the
sidecar's build context. Setting Dockerfile Path to empty/auto-detect on
the sidecar service didn't help — `railway.json`'s `dockerfilePath` wins
over auto-detect.

**Quick fix** (what we did): on the sidecar service, add a service variable

```
RAILWAY_DOCKERFILE_PATH=Dockerfile
```

This is Railway's documented per-service override and takes precedence
over `railway.json`. The path resolves relative to the build context (i.e.
`oe-ai-agent/Dockerfile`), so just `Dockerfile` is correct.

**Cleaner long-term options** if a third service joins the repo:

- Rename root `railway.json` → `openemr.railway.json` and point openemr's
  Config Path setting at it explicitly. Other services then auto-detect
  and get nothing applied.
- Or: move openemr's settings into the Railway dashboard and delete
  `railway.json` entirely (drift risk: settings stop being in git).

How to verify the override is live before redeploying:

```bash
railway ssh --service oe-ai-agent -- printenv RAILWAY_DOCKERFILE_PATH
# Should print: Dockerfile
```

Then redeploy the failed sidecar build (or push a no-op change under
`oe-ai-agent/`) and confirm the build log loads `oe-ai-agent/Dockerfile`,
not `Dockerfile.railway`.
