# Loading Synthea sample data

Notes for getting bulk synthetic patients into OpenEMR — locally and on a
Railway-deployed instance. The hand-crafted dataset in
`sql/example_patient_data.sql` (11 patients, demographics only) is too thin
for testing search, pagination, or anything that touches clinical data.
Synthea fills that gap: realistic demographics, encounters, problems,
medications, allergies, vitals.

## TL;DR

| Environment | Command | Time |
| --- | --- | --- |
| Local Docker | `docker compose exec openemr /root/devtools import-random-patients 50` | ~2 min for 50 |
| Railway | `tools/railway/import-synthea-demo-patients.sh --count 50` | ~7 min for 50 |

Both end up calling the same script: `contrib/util/ccda_import/import_ccda.php`.
The local devtool wraps it; on Railway you call it directly because the prod
image doesn't have Synthea or the devtools.

## Local Docker (the easy path)

The `openemr/openemr:flex` dev image ships with Synthea baked in at
`/root/synthea/synthea-with-dependencies.jar` and a `devtools` script that
wires Synthea → CCDA → import for you.

```sh
cd docker/development-easy
docker compose up --detach --wait
docker compose exec openemr /root/devtools import-random-patients 50
```

That's it. Defaults to `isDev=true` which skips CCDA-document storage and
audit-table writes (much faster, ~2 sec/patient instead of ~10).

Verify:

```sh
docker compose exec mysql mariadb -uopenemr -popenemr openemr -N -B -e \
  "SELECT COUNT(*) FROM patient_data;"
```

For 50 imported patients you'll see ~50 rows added. Encounters land in
`form_encounter`, problems/meds/allergies in `lists`, vitals in `form_vitals`.
Don't be alarmed by `form_observation` staying empty — vitals don't go
there.

### Side effect: the CCDA pack you just generated

`/root/synthea/output/ccda/` inside the container holds the 50 CCDA XML
files Synthea just produced. They're not cleaned up after import, so you
can extract them for reuse (e.g. importing the same cohort onto another
environment):

```sh
mkdir -p /tmp/synthea-ccdas
docker compose -f docker/development-easy/docker-compose.yml \
  cp openemr:/root/synthea/output/ccda/. /tmp/synthea-ccdas/
tar -czf /tmp/synthea-ccdas.tar.gz -C /tmp/synthea-ccdas .
```

The tarball is ~2MB for 50 patients. Keep it around if you want to
reproduce the cohort on Railway (next section).

## Railway

The deployed image (`Dockerfile.railway` based on `openemr/openemr:next`)
doesn't have Java, Synthea, or the devtools script. The CCDA importer
(`contrib/util/ccda_import/import_ccda.php`) **is** in the image — it
ships with OpenEMR. So we generate CCDAs locally and feed them to the
deployed importer.

The scripted path is:

```sh
tools/railway/import-synthea-demo-patients.sh --count 50
```

The script:

- requires the local Railway link to point at `deploy3` by default, so it
  does not accidentally target an older Railway project;
- generates CCDAs through the local `/root/devtools import-random-patients`
  workflow unless you pass `--ccda-tar /path/to/synthea-ccdas.tar.gz`;
- enables `OPENEMR_ENABLE_CCDA_IMPORT=1` on the deployed `openemr` service
  and redeploys that service if the flag is not already live;
- uploads the CCDA tarball over `railway ssh`, verifies the checksum, runs
  `import_ccda.php --isDev=true`, prints row counts before and after, and
  cleans up `/tmp`.

The local-generation path also imports those same patients into the local
development database as a side effect, because it intentionally reuses the
existing devtools workflow. Use `--ccda-tar` if you already have a CCDA
archive and want to skip local generation.

### 1. Have a tarball of CCDAs ready

Either reuse `/tmp/synthea-ccdas.tar.gz` from the local run above, or
generate fresh ones with the Synthea Docker image.

### 2. Enable the importer on Railway

The script is gated off by default:

```php
if (!getenv('OPENEMR_ENABLE_CCDA_IMPORT')) {
    die('Set OPENEMR_ENABLE_CCDA_IMPORT=1 environment variable to enable this script');
}
```

Set the env var on the `openemr` service. **Plain `1`** — no quotes, no
shell metacharacters (Railway entrypoints sometimes evaluate values, see
the existing memory note on env-var hygiene).

The dashboard works. If you're driving via the Railway MCP / CLI, the
update **stages** the change. A `railway redeploy` will fail with
`Cannot redeploy without a snapshot` until you commit the staged change
via `accept-deploy` (or the equivalent dashboard "Deploy" button). Wait
for the new deployment to reach SUCCESS before continuing — the env var
is only live in the **new** replica.

Verify it landed:

```sh
railway ssh -s openemr -- 'printenv OPENEMR_ENABLE_CCDA_IMPORT'
# expect: 1
```

### 3. Upload the tarball into the container

Railway has no file-transfer command. The trick that works is base64 over
`railway ssh`:

```sh
base64 < /tmp/synthea-ccdas.tar.gz | \
  railway ssh -s openemr -- 'base64 -d > /tmp/synthea-ccdas.tar.gz'
```

`railway ssh` accepts a command argument (everything after `--`) so it can
run non-interactively with stdin piped in. ~2MB of base64-encoded payload
goes through fine; if you push much more (hundreds of patients), split or
upload to a bucket and `curl` instead.

Verify with md5:

```sh
md5sum /tmp/synthea-ccdas.tar.gz                          # local
railway ssh -s openemr -- 'md5sum /tmp/synthea-ccdas.tar.gz'  # remote
# expect identical hashes
```

### 4. Extract and run the import

You SSH in as **root**, and the OpenEMR web root on the
`openemr/openemr:next` image is `/var/www/localhost/htdocs/openemr`:

```sh
railway ssh -s openemr -- 'set -e
mkdir -p /tmp/ccdas
tar -xzf /tmp/synthea-ccdas.tar.gz -C /tmp/ccdas
cd /var/www/localhost/htdocs/openemr
php contrib/util/ccda_import/import_ccda.php \
  --sourcePath=/tmp/ccdas \
  --site=default \
  --openemrPath=/var/www/localhost/htdocs/openemr \
  --isDev=true'
```

Roughly 7 sec/patient on Railway (vs 2 sec locally) — the latency to
MariaDB is higher. 50 patients ≈ 6 minutes. The script writes a `log.txt`
next to `--sourcePath` with per-file results.

**`--isDev=true` is required.** The script's header comment notes
non-dev mode is currently broken. Dev mode also disables the OpenEMR
audit log during the import — only run this on environments with no real
patient data.

### 5. Verify

```sh
railway ssh -s openemr -- 'mariadb -h"$MYSQL_HOST" -P"$MYSQL_PORT" \
  -u"$MYSQL_USER" -p"$MYSQL_PASS" openemr -N -B -e "
SELECT \"patients\", COUNT(*) FROM patient_data
UNION ALL SELECT \"encounters\", COUNT(*) FROM form_encounter
UNION ALL SELECT \"lists\", COUNT(*) FROM lists;"'
```

For 50 patients you'll see roughly 50 / 2200 / 1700 across the three
tables. Don't try to use `globals.php`/`sqlStatement` from a one-off
`php -r` — it bails with `Site ID is missing from session data!`. Just
hit MariaDB directly with the `MYSQL_*` env vars the openemr service
already has.

### 6. Cleanup

```sh
railway ssh -s openemr -- 'rm -rf /tmp/ccdas /tmp/synthea-ccdas.tar.gz'
```

Optional: unset `OPENEMR_ENABLE_CCDA_IMPORT` on the service. The script
self-gates to CLI only (`php_sapi_name() === 'cli'`), so leaving the var
set isn't web-reachable — but unsetting is good hygiene. It triggers
another redeploy.

## Gotchas

- **Patient count off by one or two.** Local imports may show 49/50
  successes if a generated Synthea name collides with an existing demo
  patient and gets deduped or skipped silently. Railway, starting empty,
  imports a clean 50/50.
- **`sh: syntax error: unterminated quoted string` mid-import.** Cosmetic.
  Triggered by Synthea-generated names with apostrophes / Spanish
  characters being passed through some shell call inside the import
  pipeline. The patient still imports successfully on the next line.
- **`PHP Notice: ob_flush(): Failed to flush buffer`** spam. Also
  cosmetic — the script tries to flush an output buffer that doesn't
  exist when run from CLI. Ignore.
- **Synthea generation runs on first import per container.** The dev
  container lazily installs OpenJDK and downloads
  `synthea-with-dependencies.jar` (~190 MB) the first time you call
  `import-random-patients`. Subsequent runs are fast.
- **Railway env-var changes don't auto-deploy.** Setting a var via the
  MCP/CLI stages it; you need `accept-deploy` to ship it. The dashboard
  hides this distinction.
