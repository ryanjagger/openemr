# OpenEMR System Audit

**Date:** 2026-04-27
**Scope:** OpenEMR codebase (`/Users/ryan/gauntlet/openemr`), evaluated as a backend
for an AI agent that will read and write PHI on behalf of clinical users.
**Branch:** `feature/docs`
**Agent/Model** Claude Code / Opus 4.7

This audit covers five domains: Security, Performance, Architecture, Data
Quality, and Compliance & Regulatory. Each section is intended to be read
independently — there is intentional overlap (e.g., HIPAA appears in both
Security and Compliance) because the angles differ.

The TL;DR is at the bottom. If you only have five minutes, read that first.

---

## Table of Contents

1. [Security Audit](#1-security-audit)
2. [Performance Audit](#2-performance-audit)
3. [Architecture Audit](#3-architecture-audit)
4. [Data Quality Audit](#4-data-quality-audit)
5. [Compliance & Regulatory Audit](#5-compliance--regulatory-audit)
6. [TL;DR — Findings Across All Audits](#6-tldr--findings-across-all-audits)

---

## 1. Security Audit

OpenEMR has a **mature security baseline** — modern password hashing, CSRF
tokens, parameterised SQL, comprehensive XSS escaping helpers, MFA, OAuth2
with SMART-on-FHIR scopes, and an event-based audit log. The concerns are
not "is the foundation rotten?" — it isn't — but rather "where does the
foundation give way under an AI-agent workload that will read and mutate PHI
at machine speed?"

### 1.1 Authentication

| Area | Finding | Severity | File |
|---|---|---|---|
| Password hashing | bcrypt by default; Argon2id supported; `hash_equals()` used for comparison | OK | `src/Common/Auth/AuthHash.php` |
| Legacy hash support | MD5/SHA1 paths exist for backwards compatibility | Low | `src/Common/Auth/AuthHash.php:216-224` |
| Plaintext password in `$_POST` | `clearPass` lives in `$_POST` briefly; `sodium_memzero()` clears local copies but the superglobal copy persists for the request lifecycle | Medium | `library/auth.inc.php:49` |
| MFA | TOTP and U2F supported, optional per user | OK | `src/Common/Auth/MfaUtils.php` |
| Session timeout | Configurable (`timeout` global, default 7200s; portal `portal_timeout` 1800s); enforced via `SessionTracker::isSessionExpired()` | OK | `src/Common/Session/SessionTracker.php` |
| `skip_timeout_reset` parameter | Background tasks can bypass session timeout reset — no whitelist on which callers may pass it | Medium | `library/auth.inc.php:113` |
| Failed-login lockout | `users_secure.login_fail_counter`; per-user and per-IP lockout configurable via `password_max_failed_logins` and `ip_time_reset_password_max_failed_logins` | OK | `library/ajax/login_counter_ip_tracker.php` |
| Default credentials | `admin / pass` shipped in dev-easy compose setup — fine for dev, dangerous if anyone follows that pattern in deploy | Medium (deployment) | `CLAUDE.md`, `docker/development-easy/` |

### 1.2 Authorization / Access Control

The ACL layer is **phpGACL** — a venerable role-based system with ACO/ARO/AXO
objects. It works, but has two structural weaknesses for an AI-agent
workload.

| Area | Finding | Severity | File |
|---|---|---|---|
| **Patient ID accepted from `$_GET` without ownership check** | `if (!empty($_GET['pid']) && empty($session->get('pid'))) { setSession('pid', $_GET['pid']); }` — no verification that the current user is allowed to access that patient | **HIGH** | `interface/globals.php:155-157` |
| Patient-scope ACL | Per-patient access is largely a session convention, not a constraint — once `pid` is in session, controllers trust it | High | broad, `interface/` |
| Facility / multi-tenant scoping | `pc_facility` cookie selects facility; no enforced data isolation between facilities — a user with cross-facility role can see across | Medium (deployment-dependent) | `interface/login/login.php:183` |
| SMART-on-FHIR scope parsing | Scope structure correct (`patient/*.read`, `user/*.cruds`, `system/*.read`) | OK | `src/RestControllers/SMART/ScopePermissionParser.php` |
| Scope **enforcement** at every endpoint | Scope checks present at controllers; need spot audit to confirm all 92 REST controllers actually call `RestConfig::request_authorization_check()` before returning data | Medium — verify | `src/RestControllers/Subscriber/AuthorizationListener.php` |
| Field-level ACL | None — once a row is returned, all columns are visible to the caller | Medium (PHI minimisation) | broad |

The first row is the single most consequential security finding in this
audit. An AI agent that takes natural-language input ("show me the labs
for Mrs. Patel") will compose URLs and parameters; if `pid` isn't bound
to the authenticated user's allowed patients, prompt injection or a buggy
agent loop becomes a path to unauthorized PHI access.

### 1.3 Data Exposure Vectors

| Area | Finding | Severity | File |
|---|---|---|---|
| SQL injection | `sqlStatement()` is parameterised; widespread bind-array usage | OK | `library/sql.inc.php:96-103` |
| Legacy GACL admin | String-concatenated SQL in `gacl/admin/edit_object_sections.php` and friends — admin-only, but still | Low | `gacl/admin/` |
| XSS escaping helpers | `attr()`, `text()`, `xlt()`, `js_escape()`, `csvEscape()`, `safe_href()` — comprehensive, used in 471+ places | OK | `library/htmlspecialchars.inc.php` |
| XSS escaping consistency | Helpers exist; **consistent application across all templates** is not verified — spot checks needed | Medium | `interface/`, `templates/` |
| CSRF | HMAC-SHA256 tokens per session, separate `api` and `default` subjects, `hash_equals()` validation | OK | `src/Common/Csrf/CsrfUtils.php` |
| Error message disclosure | `HelpfulDie("query failed: $statement", $e->sqlError)` echoes SQL into logs | Medium | `library/sql.inc.php:101` |
| Debug mode | `user_debug` global > 1 enables `display_errors` — admin-toggleable | Medium | `interface/globals.php:245` |
| Patient ID in URL (`?pid=123`) | Visible to browser history, server logs, upstream proxies | High (HIPAA) | broad |

### 1.4 PHI Handling

| Area | Finding | Severity | File |
|---|---|---|---|
| Encryption-at-rest infrastructure | `CryptoGen` — AES-256-CBC + HMAC-SHA384, dual-key (drive key + DB key), versioned cipher | OK | `src/Common/Crypto/CryptoGen.php` |
| Encryption coverage | Encryption is **opt-in** per field/feature (audit comment encryption, document encryption, etc.); **not all PHI columns are encrypted** | Medium | broad |
| Document storage encryption | Files in `sites/default/documents/` rely on filesystem permissions, not application-level encryption | High | `sites/default/config.php:24-26` |
| TLS enforcement | Application does not enforce HTTPS; no HSTS header at app layer | Medium | infra-dependent |
| Key storage | Drive keys at `sites/<site>/documents/logs_and_misc/methods/`; DB keys in `keys` table — both compromised together if the host is compromised | Medium | `src/Common/Crypto/CryptoGen.php` |
| PHI in logs | `EventAuditLogger` logs `patient_id` (PHI by itself); log files in `sites/default/documents/logs_and_misc/` | Medium | `src/Common/Logging/EventAuditLogger.php` |

### 1.5 Dangerous Patterns

| Area | Finding | Severity |
|---|---|---|
| `eval` / `exec` / `system` | None found in production paths; `escapeshellarg()` used for HylaFAX shellouts | OK |
| `unserialize()` | Used with `['allowed_classes' => false]` — class instantiation disabled | OK |
| Hardcoded credentials | Not found outside test fixtures | OK |
| Test secrets | Real-looking webhook keys in `tests/Tests/Unit/PaymentProcessing/Rainforest/Webhooks/VerifierTest.php` — exclude tests from prod deploy | Low |

### 1.6 API Security (Most Relevant for the Agent Use Case)

| Area | Finding | Severity | File |
|---|---|---|---|
| Bearer token auth | OAuth2 + JWT Bearer tokens via League OAuth2 server | OK | `src/RestControllers/Authorization/BearerTokenAuthorizationStrategy.php` |
| Token expiration / rotation | Refresh tokens supported; expiration enforced — but default lifetimes need a deployment review | Medium | `oauth2/`, `api_token` table |
| **API rate limiting** | None at the application layer | Medium | n/a |
| Input validation on REST endpoints | Search-field whitelist on patient endpoint; type/range validation inconsistent | Medium | `src/RestControllers/PatientRestController.php:137-150` |

### 1.7 Audit Log Tamper Resistance

The `log` table has a `checksum` column and an "audit log tamper report"
exists at `interface/reports/audit_log_tamper_report.php`. ATNA forwarding
to an external syslog sink is supported (`AtnaSink`). However, **a user
with database access can still modify the `log` table directly** — the
checksum is detective, not preventive. For a real defense, ATNA forwarding
to an immutable external store is the only honest answer.

### 1.8 Security Recommendations (Ordered)

1. **Fix `interface/globals.php:155-157`**: validate that the authenticated user
   has access to the requested `pid` before binding it to session.
2. **Enforce HTTPS at the application layer** with HSTS, secure-cookie,
   `httponly` flags, and a HTTP→HTTPS redirect.
3. **Rate-limit the REST and FHIR APIs**, especially under an AI-agent
   workload that can fan out hundreds of requests per turn.
4. **Stop putting `pid` in URLs.** Move to opaque per-session handles or
   POST-bodies for patient-scoped operations.
5. **Encrypt document storage** (`sites/*/documents/`) at rest, ideally via
   filesystem-level encryption and document-level encryption for
   high-sensitivity classes (mental health, substance use, HIV).
6. **Enable ATNA log forwarding** to an external append-only audit store.
7. **Move encryption keys to a KMS** (AWS KMS, HashiCorp Vault) rather than
   storing both halves of the key pair on the same host.
8. **Audit every REST controller for scope enforcement** — write a phpstan
   rule that fails the build if a controller method does not call
   `request_authorization_check()` before returning data.

---

## 2. Performance Audit

OpenEMR's request-path is heavy. For the AI-agent use case — where every
user turn translates to several API calls — the baseline overhead per
request is the dominant constraint, not per-query cost.

### 2.1 Estimated Per-Request Overhead

The middleware stack and globals load happen on every API call, before
any actual business logic runs.

| Component | Estimated latency | Frequency |
|---|---|---|
| Middleware stack (8 listeners: telemetry, logging, session, site setup, CORS, OAuth2, authz, routing) | 50–100 ms | every request |
| Globals loading (4,583 lines, 481+ vars from `library/globals.inc.php`) | 20–50 ms | every request |
| DB connection + site setup | 10–30 ms | every request |
| API logging — synchronous DB write to `api_log` | 10–20 ms | every request (when `api_log_option > 0`) |
| ACL/OAuth2 authorization | 30–80 ms | every request |
| Session I/O (file-backed by default) | 5–20 ms | every request |
| **Baseline before any business logic** | **125–300 ms** | every request |
| Primary query | 20–100 ms | every request |
| N+1 child queries | 50–200 ms | ~30% of requests |
| **Realistic API call latency** | **200–600 ms** | typical |

For an agent making 5 sequential API calls per turn, that is **1–3 seconds
of pure platform overhead** before model latency. Parallelising those calls
helps but doesn't dodge the per-request floor.

### 2.2 Database Schema and Queries

- **281 tables** in `sql/database.sql` (~15,400 lines).
- **Zero `FOREIGN KEY` constraints declared anywhere in the schema.** Indexes
  exist (510 of them), but referential integrity is enforced only in
  application code.
- `SELECT *` is pervasive — **148 occurrences** across the services
  directory. `patient_data` alone has 90+ columns including `LONGTEXT`
  fields, so a `SELECT *` on that table is meaningfully larger than a
  selective query.
- N+1 query patterns are widespread. Documented examples:
  - `src/Services/PatientNameHistoryService.php:120`
  - `src/Services/ObservationLabService.php:152`
  - `src/Services/AppointmentService.php:197`, `:387`
  - `src/Services/ConditionService.php:89`
  - `src/Services/ImmunizationService.php:168`
  - `src/Services/VitalsService.php:206`
  - `src/Services/GroupService.php:112`

### 2.3 API-Level Pagination

- `_offset` and `_limit` exist on REST endpoints.
- Max page size capped at 200 (e.g. `PatientRestController.php:459-470`).
- **Default `_limit = 0` means unbounded** — a poorly-formed agent query
  can pull entire patient populations.
- No HTTP-level caching headers (ETag, Cache-Control, Last-Modified) on
  most FHIR resources; only ~30 such references across 32 REST controllers.

### 2.4 Caching

| Layer | Status | Notes |
|---|---|---|
| PHP opcache | **Disabled in CI/Docker** — explicitly commented out in `ci/nginx/php.ini:2-3`. Estimated 30–50% throughput loss. | Critical fix. |
| APCu | Not used (0 references). | Missed opportunity for per-process cache. |
| Redis | Supported only for sessions (`LockingRedisSessionHandler`); not used for query result caching. | |
| Twig cache | No explicit production cache config found. | |
| Query result cache | None. | |

### 2.5 Frontend Asset Weight

Worth noting because — even though the agent is API-driven — ops staff
will still load the UI:

- 77 named assets in `config/config.yaml`.
- Stack: jQuery 3.7.1, **Angular 1.8.3 (legacy, EOL)**, Bootstrap 4.6.2,
  jQuery UI, DataTables, CKEditor 5, FontAwesome 6, DWV imaging, Flot
  charting, plus 50+ libraries.
- 120+ npm dependencies. Initial page load: ~2–5 MB, 15+ blocking script
  tags in head.

### 2.6 Background Work

- `library/ajax/execute_background_services.php` runs background jobs
  **sequentially and synchronously** (line 32).
- No queue system (no Symfony Messenger, Beanstalk, Redis queues).
- A long-running task (5-minute email batch) can block all subsequent
  service runs, including ones triggered by API requests in the same window.

### 2.7 Largest Service Classes

| Service | Lines |
|---|---|
| `CdaTemplateImportDispose.php` | 2,334 |
| `CdaTemplateParse.php` | 1,848 |
| `SQLUpgradeService.php` | 1,663 |
| `HistorySdohService.php` | 1,504 |
| `ProcedureService.php` | 1,481 |
| `FhirPatientService.php` | 1,082 |
| `PatientService.php` | 1,007 |

Loading these triggers autoloader work and slows cold starts. Opcache
mitigates this — and opcache is disabled.

### 2.8 Performance Recommendations (Ordered)

1. **Enable PHP opcache.** This is the single highest-leverage change.
2. **Move API logging async.** Either a write-behind queue or a
   non-blocking pipe.
3. **Cap default `_limit`.** Never default to unbounded — agents will
   abuse this.
4. **Add HTTP caching headers** to FHIR resources where the underlying
   data has clear last-modified semantics.
5. **Eliminate the N+1 patterns** listed above with batched / `IN (?)`
   queries.
6. **Replace `SELECT *`** in services with explicit column lists,
   especially on `patient_data`, `documents`, `forms`.
7. **Cache `globals` per-request** via APCu or Redis — they rarely
   change, and 480+ vars per request is dead weight.
8. **Add foreign keys.** Even informational FKs would let MySQL's
   optimiser choose better plans on multi-table joins.

---

## 3. Architecture Audit

### 3.1 Top-Level Layout

```
/src/                Modern PSR-4 (OpenEMR\ namespace) — ~350 .php files
/library/            Legacy procedural helpers — 72 .inc.php files
/interface/          Web UI controllers — ~1,001 .php files
/apis/               REST API dispatch
/oauth2/             OAuth2 authorization server
/templates/          Twig (modern)
/library/templates/  Smarty (legacy)
/sql/                Schema (281 tables) and SQL upgrade scripts
/db/                 Doctrine Migrations (incomplete rollout)
/sites/{site}/       Per-tenant config and document storage
/config/             Application-level config (services.php, app.php, ...)
/vendor/             Composer deps
```

Two timelines coexist in this repo: the modern PSR-4 service-and-event
architecture under `/src/`, and a procedural-PHP-with-globals world under
`/library/` and `/interface/`. Both are alive — most pages still go
through the legacy entry point even when they call into modern services.

### 3.2 Major Namespaces (`/src/`)

**Domain:** `OpenEMR\Services\` (93 services), `OpenEMR\RestControllers\`
(92 controllers, 41 of them FHIR), `OpenEMR\Events\` (79 event classes),
`OpenEMR\FHIR\`, `OpenEMR\Billing\`, `OpenEMR\Rx\`, `OpenEMR\Appointment\`,
`OpenEMR\ClinicalDecisionRules\`, `OpenEMR\Validators\`, `OpenEMR\Encryption\`.

**Infrastructure:** `OpenEMR\Core\` (Kernel, OEGlobalsBag,
ModulesApplication, OEHttpKernel), `OpenEMR\Common\` (database, logging,
HTTP, UUID, validation), `OpenEMR\BC\` (backwards-compat:
`DatabaseConnectionFactory`, `ServiceContainer`, `FallbackRouter`).

### 3.3 Data Layer

**Engine:** MySQL 8.0+ (mysqli driver).
**Abstractions:** Doctrine DBAL 4.x (modern path) + ADODB (legacy surface
API — kept for backwards compatibility with `library/` and `interface/`).
Both ultimately point at the same connection.

**Connection management:**
- New code: `ConnectionManager::get(ConnectionType::DEFAULT)` returns a
  Doctrine DBAL `Connection`.
- Legacy code: `$GLOBALS['adodb']['db']` and `sqlStatement(...)` from
  `library/sql.inc.php`.

**Query helper:** `OpenEMR\Common\Database\QueryUtils` — works against
both ADODB and DBAL, used by services.

**Migrations:**
- Legacy: per-version SQL upgrade files in `sql/` (e.g.
  `8_0_0-to-8_1_0_upgrade.sql`), driven by `sql_upgrade.php`.
- Modern: Doctrine Migrations in `/db/Migrations/` — explicitly marked
  "NOT fully integrated" in `db/README.md`. Most schema changes still
  go through the legacy path.

### 3.4 Service Layer

All domain services extend `OpenEMR\Services\BaseService`:

```php
class PatientService extends BaseService {
    public const TABLE_NAME = "patient_data";
    public function __construct() {
        parent::__construct(self::TABLE_NAME);
    }
}
```

`BaseService` provides table metadata, FHIR-style search query building,
event dispatch, session injection, and UUID handling.

**Wiring:** Symfony DependencyInjection container, configured in
`config/services.php`.

### 3.5 HTTP Layer

**API entry point:** `apis/dispatch.php` → `ApiApplication::run($request)`.

The ApiApplication subscribes a chain of EventSubscribers (in this
order): `ExceptionHandlerListener`, `TelemetryListener`,
`ApiResponseLoggerListener`, `SessionCleanupListener`, `SiteSetupListener`,
`CORSListener`, `OAuth2AuthorizationListener`, `AuthorizationListener`,
`RoutesExtensionListener`, `ViewRendererListener`. Each can short-circuit.

**Routes:** Defined as PHP arrays in
`apis/routes/_rest_routes_standard.inc.php` and
`_rest_routes_fhir_r4_us_core_3_1_0.inc.php`. The router is a
hand-rolled pattern matcher in `RoutesExtensionListener`, not Symfony Router.

**Web UI:** No central router — each `/interface/` script handles its
own dispatch via `$_GET` / `$_POST`.

### 3.6 Integration Points (Most Relevant for the Agent)

These are the seams where new agent capability should be grafted in
**without modifying core files**:

1. **REST API** — `/apis/default/api/...`, OAuth2 protected. Add
   endpoints by registering routes in
   `apis/routes/_rest_routes_standard.inc.php` (or via the
   `RestApiResourceServiceEvent` event from a custom module).
2. **FHIR API** — `/apis/default/fhir/...`, R4 + US Core 8.0 +
   SMART-on-FHIR v2.2.0. 41 resources supported. Bulk export via
   `$export`.
3. **Event system** — Symfony EventDispatcher; 79 event classes. Listen
   to `patient.created`, `patient.updated`, `encounter.*`,
   `appointment.set`, etc., from a custom module's
   `openemr.bootstrap.php`.
4. **Custom modules** — `interface/modules/custom_modules/{name}/` with
   `openemr.bootstrap.php`. Activated via the `modules` table. The
   recommended way to extend without forking.
5. **OAuth2 authorization server** — `oauth2/authorize.php`, full
   SMART-on-FHIR launch (EHR launch and patient-initiated). Refresh
   tokens supported. Scopes follow `patient/*.read` etc.
6. **CDS Hooks** (CDR Engine) — `src/ClinicalDecisionRules/` and
   `src/RestControllers/CDS/`. Useful for surfacing agent recommendations
   in the clinical workflow.
7. **No native webhook dispatcher.** To send outbound events, listen to
   domain events and POST from the listener.

### 3.7 Templating & Frontend

- **Twig 3.x** (modern) at `/templates/`. Render tests exist with
  fixture files at
  `tests/Tests/Isolated/Common/Twig/fixtures/render/`.
- **Smarty 4.5** (legacy) at `library/smarty_legacy/`,
  `library/templates/`. Migration to Twig is gradual.
- **Angular 1.8.3** (long EOL) for forms and calendar UIs.
- **Build pipeline:** Gulp 4 + SASS. `npm run build`, `npm run dev`.

### 3.8 Configuration Sources

In priority order:
1. Environment variables (`.env`).
2. Database `globals` table (per-site settings via the OpenEMR admin UI).
3. Code defaults in service classes.

`OpenEMR\Core\OEGlobalsBag` is a typed wrapper around `$GLOBALS`. New
code uses `$globals->getString('key')` instead of touching `$GLOBALS`
directly. The bag exposes the `Kernel` instance for accessing the
container and event dispatcher.

### 3.9 Multi-Tenancy

Site files at `/sites/{site_id}/sqlconf.php` (per-site DB) and
`/sites/{site_id}/config.php`. Site detection on the web side via
`$_GET['site']` or `HTTP_HOST`; on the API side via URL path
(`/apis/{site}/api/...`). Same database can be shared across sites with
`site_id` columns; **isolation between sites in shared-DB mode is
deployment-configuration-dependent**.

### 3.10 Architecture Recommendations for AI Agent Integration

- **Build the agent as a custom module**, not a core fork.
- **Talk to data via FHIR** wherever possible — semantically richer than
  the OpenEMR REST API and well-typed.
- **Subscribe to events** for write-side feedback (e.g. listen to
  `patient.updated` to invalidate agent context caches).
- **Run a separate process for outbound LLM calls** (a worker behind a
  queue), not in-line in the request thread — every agent call would
  otherwise inflate request latency by the LLM round-trip time.
- **Use the OAuth2 server** for agent authentication. Treat the agent
  as a confidential client with `system/*` scopes, scoped down per
  feature.

---

## 4. Data Quality Audit

OpenEMR's schema and validation patterns reflect 20+ years of accreted
requirements: lots of nullable columns, sentinel values, free-text
alongside coded fields, and minimal database-level integrity. For an AI
agent that will read this data, **most failure modes will come from data
shape, not data wrongness** — the agent will hallucinate confidently
about fields that are technically nullable but practically always
populated, and miss patients whose data lives in a free-text field rather
than a coded one.

### 4.1 Schema Constraint Patterns

| Concern | Evidence |
|---|---|
| **Zero `FOREIGN KEY` constraints in `sql/database.sql`** | Provider IDs, facility IDs, pharmacy IDs, encounter IDs are all bare ints with no DB-enforced relationship. Application code is the only thing keeping the graph connected. |
| Empty-string sentinels | `patient_data.ss varchar(255) NOT NULL default ''` (line 8350) — cannot distinguish "not collected" from "unknown". The same pattern recurs across name, address, phone, email fields. |
| No uniqueness on natural keys | `patient_data.pubpid` (public ID) — no unique constraint. `patient_data.ss` — no unique constraint. Only the system-generated `pid` and `uuid` are unique. |
| Numeric `0` as sentinel for missing FK | `prescriptions.drug_id int(11) NOT NULL default '0'` — implies "no drug" via a value that overlaps with potential real IDs. |
| No CHECK constraints | All range/format constraints live in PHP. |

### 4.2 Validation Patterns

`src/Validators/PatientValidator.php` enforces validation on **insert**:

```php
$context->required("fname", "First Name")->lengthBetween(1, 255);
$context->required("DOB", 'Date of Birth')->datetime('Y-m-d');
```

But:
- Most non-name fields aren't in the validator (occupation, phone\*,
  address, drivers license).
- Update paths often skip validation.
- `library/options.inc.php` writes form-defined fields without going
  through the validator.

Effectively, the system follows a **"store anything, validate at
display"** pattern. The AI agent must assume anything in `varchar` /
`text` / `longtext` columns may be malformed.

### 4.3 Date/Time Handling

- Bootstrap sets `date_default_timezone_set('UTC')` globally
  (`bootstrap.php:30`).
- No `'0000-00-00'` zero-date sentinels in current schema (good).
- **Mixed timestamp types**: some tables use `timestamp ON UPDATE
  CURRENT_TIMESTAMP` (auto-managed); others use `DATETIME` with manual
  management. This means "last updated" semantics differ table to table.
- `patient_data.last_updated` is `DATETIME`; `form_encounter.last_update`
  is `timestamp`. Don't compare them.
- Dates are commonly handled as PHP strings, not `\DateTimeImmutable`,
  bypassing the type system's protection (`PatientService.php:177-178`).
- Appointment times in `openemr_postcalendar_events.pc_time` are stored
  as datetime **without timezone information**.

### 4.4 Duplicates, Dedup, and the Master Patient Index

- `patient_data.dupscore INT NOT NULL default -9` exists, suggesting an
  intent to compute fuzzy-match dedup scores.
- **No code references to `dupscore` were found.** No merge tool, no
  master patient index, no cleanup utility.
- Phone numbers stored as raw strings, four fields (`phone_home`,
  `phone_biz`, `phone_contact`, `phone_cell`), no E.164 normalisation.
  libphonenumber is a transitive dependency but not wired into the
  `patient_data` write path.
- Address normalisation is absent. A USPS AddressVerifyV3 client exists
  in `src/USPS/` but is not invoked on patient writes.

### 4.5 Coded Fields vs Free Text

Triple-redundant medication storage in `prescriptions`:
- `drug` — varchar(150) free text;
- `drug_id` — int reference to `drugs` table;
- `rxnorm_drugcode` — varchar(25) RxNorm code.

Nothing in the schema or validators ensures these agree. The same
pattern recurs in `lists` (problem list / allergies) where `type`
(free text) and `list_option_id` (coded) coexist.

### 4.6 Code System Coverage

| System | Storage | Required? |
|---|---|---|
| CVX (vaccines) | `immunizations.cvx_code varchar(64)` | nullable |
| RxNorm (meds) | `prescriptions.rxnorm_drugcode varchar(25)` | nullable |
| SNOMED CT | `rule_criteria_data` references | nullable |
| ICD-9 | `icd9_dx_code`, `icd9_sg_code` tables | n/a |
| ICD-10 | **Not found in schema** | absent |
| LOINC | observation result codes | nullable |

**The schema permits 0% coding.** Whether actual records are coded is
a deployment-time question — the agent shouldn't assume.

### 4.7 Soft Delete vs Active vs Deleted

OpenEMR uses inconsistent flags:

| Table | Flag | Type | Semantics |
|---|---|---|---|
| `pnotes` | `deleted` | tinyint default 0 | soft delete |
| `forms` | `deleted` | tinyint default 0 | soft delete |
| `forms` | `activity` | tinyint default 0 | unclear (see below) |
| `lists` | `activity` | tinyint default 1 NOT NULL | active in problem list |
| `list_options` | `activity` | tinyint default 1 NOT NULL | option visible in dropdowns |
| `drugs` | `active` | tinyint default 1 | visible in prescriber UIs |
| `background_services` | `active` | tinyint default 0 | enabled |

`activity` and `active` are not synonyms. `deleted=1` and `activity=0`
sometimes overlap. Document the intended semantics per table before any
agent reasoning about "is this current?" is reliable.

### 4.8 Encounter "Open vs Closed" State

There is no explicit state column. Calling code infers state from
`date_end IS NULL` (open) or `date_end IS NOT NULL` (closed). The
appointment status in `openemr_postcalendar_events.pc_apptstatus`
defaults to `'-'` — meaning "no status set" — and uses single-character
codes whose semantics are governed by the `apptstat` list in
`list_options`.

### 4.9 Stale Data Signals

- Not all clinically-relevant tables have a `last_updated` (e.g.
  `lists_medication`).
- Some `last_updated` columns are `DATETIME` with no automatic
  maintenance — they only update if the writing code remembers.
- `patient_data` does maintain `last_updated`, but its accuracy depends
  on every write path setting it.

### 4.10 Data Quality Recommendations (Ordered)

1. **Document the soft-delete / activity convention** per table — the
   agent's read-path needs to filter consistently.
2. **Resolve medication triple-representation** — pick a canonical
   source (RxNorm) and reconcile.
3. **Add NPI / phone / SSN normalisation** at the service layer before
   write, even if the schema can't enforce it.
4. **Add explicit encounter state** — open / closed / billed —
   instead of inferring from `date_end`.
5. **Survey actual coding coverage** before an agent feature ships —
   query `COUNT(*)` and `COUNT(rxnorm_drugcode)` on prescriptions,
   `COUNT(cvx_code)` on immunizations, etc., and have the agent's
   prompts acknowledge the gap.
6. **Backfill foreign keys.** Even non-enforcing FKs would help MySQL's
   optimiser and would document the graph.
7. **Refuse free-text-only writes** for fields where a coded option is
   available, at the API boundary.

---

## 5. Compliance & Regulatory Audit

### 5.1 HIPAA Technical Safeguards (45 CFR §164.312)

| Safeguard | Citation | Implementation | Status |
|---|---|---|---|
| Audit controls | §164.312(b) | `log`, `api_log`, `extended_log`, `audit_master`, `audit_details` tables. `EventAuditLogger` class. ATNA syslog forwarding (`AtnaSink`). | **Strong** |
| Audit log integrity | §164.312(c)(1) | `log.checksum` column; tamper-detection report at `interface/reports/audit_log_tamper_report.php`. **Detective only — DBA can still tamper unless ATNA is forwarding offsite.** | Partial |
| Unique user identification | §164.312(a)(2)(i) | Unique `users.username`; UUID; no shared accounts. | Strong |
| Automatic logoff | §164.312(a)(2)(iii) | Configurable `timeout` (default 7200s) and `portal_timeout` (1800s); enforced via `SessionTracker`. | Strong |
| Encryption at rest | §164.312(a)(2)(iv) | `CryptoGen` (AES-256-CBC + HMAC-SHA384, dual-key); **opt-in per field**. Documents in `sites/*/documents/` not encrypted by default. | Partial |
| Encryption in transit | §164.312(e)(1) | Application does not enforce HTTPS — relies on web server. mTLS supported for ATNA. | Partial |
| Data integrity | §164.312(c)(1) | `esign_signatures` (hash + signature_hash); soft-delete pattern; amendment workflow via `amendments` and `amendments_history`. | Strong |
| Person/entity authentication | §164.312(d) | bcrypt hashing; MFA (TOTP/U2F); failed-login lockout; password expiration; password history (last 4). | Strong |
| Emergency access (break-glass) | §164.312(a)(2)(ii) | `breakglass` ACL group; `BreakglassChecker`; `gbl_force_log_breakglass` global. **No time-limited break-glass session.** | Partial |

### 5.2 Audit Log Coverage

The system logs (configurable via `audit_events_*` globals):

- Login / logout / timeout / lockout
- Patient record CRUD (`patient_data`, `form_encounter`, `forms`,
  `prescriptions`, `immunizations`, `insurance_data`, billing)
- Scheduling operations
- Lab orders and results
- Security administration (user, group, ACL changes)
- Document access
- Delete operations (special handling)
- API requests (in `api_log`: `user_id`, `patient_id`, `ip_address`,
  `method`, `request_url`, `request_body`, `response`, `created_time`)

The forensic answer to "who saw what, when?" is reconstructible from
this — assuming no tampering.

### 5.3 Data Retention and Disposal

| Topic | Status |
|---|---|
| Application-level retention policy | **Not implemented.** Logs accumulate indefinitely. |
| Backup procedures | Documented in `README-Log-Backup.txt`; weekly cron via `interface/main/backuplog.php`. |
| Soft-delete pattern | Pervasive; PHI marked `deleted=1` rather than removed — good for audit, bad for "right to erasure" workflows. |
| Secure deletion of files | Temporary CDA/CCDA files explicitly `unlink()`-ed (`deleter.php:264`). No cryptographic-overwrite library; standard filesystem deletion may leave recoverable data. |

### 5.4 Breach Notification Readiness

- **Audit trail sufficient for §164.404 breach notifications**: the
  `api_log` and `log` tables capture user, patient, time, method, URL,
  and (optionally) request/response bodies. A 60-day breach assessment
  is feasible.
- **No documented breach response procedures** in repo (no playbook in
  `docs/`, README, or CLAUDE.md).
- **No automated anomaly detection** — no thresholds on per-user
  per-patient access volume that would flag suspicious patterns.

### 5.5 BAA Implications of Sending PHI to an LLM Provider

This is the section that matters most for the user's stated goal.

#### 5.5.1 PHI Categories Present in the System

OpenEMR stores **all 18 HIPAA identifiers** plus full clinical content:

1. Names — `patient_data.fname`, `mname`, `lname`, `suffix`
2. Geographic subdivisions — `patient_data.street`, `city`, `state`,
   `postal_code`, `country_code`
3. Dates — DOB, encounter dates, appointment dates, deceased_date,
   admission/discharge in form_encounter, prescription dates
4. Phone numbers — `phone_home`, `phone_biz`, `phone_contact`,
   `phone_cell`, `phonew1`, `phonew2`, `phonecell`
5. Fax numbers — `users.fax`
6. Email addresses — `email`, `email_direct`, `google_signin_email`
7. Social Security Numbers — `patient_data.ss`
8. Medical record numbers — `patient_data.pid`, `pubpid`
9. Health plan beneficiary numbers — in `insurance_data`
10. Account numbers — `billing.account_number`
11. Certificate/license numbers — `users.state_license_number`,
    `users.npi`
12. Vehicle identifiers — not commonly stored
13. Device identifiers — `devices` table
14. URLs — `users.url`
15. IP addresses — logged in `api_log`, `audit_master`
16. Biometric records — possible via `documents`
17. Full-face photographs — possible via `documents`
18. Other unique identifiers — module-dependent

Plus: diagnoses (`lists`, `form_encounter.diagnosis`), medications
(`prescriptions`), labs (`procedure_result`, `procedure_report`),
allergies, immunizations, vital signs, clinical notes (`pnotes`,
`onotes`, `form_soap`), insurance details, billing/payment.

#### 5.5.2 No Built-in De-Identification

There is **no de-identification utility, no anonymisation module, no
scrubber** in the codebase. Any data leaving OpenEMR for an LLM call
is identifiable PHI.

#### 5.5.3 No Built-in Minimum-Necessary Filtering

The FHIR API can return all available fields on every resource. There
is no application-enforced minimum-necessary filter for outbound API
responses.

#### 5.5.4 No Existing LLM Integrations

A grep across the codebase finds:
- No calls to `openai`, `anthropic`, or other LLM API endpoints.
- "Claude.ai" / "GitHub Copilot" appear only as authorship comments
  on AI-assisted commits, not as runtime integrations.

A new LLM integration is therefore net-new and entirely up to
implementation choices.

#### 5.5.5 Regulatory Path Forward

Three viable paths, in increasing order of compliance burden:

| Path | Description | Required |
|---|---|---|
| **De-identified data only** | Run a Safe-Harbor de-identifier (45 CFR §164.502(b)) before transmission. Strip the 18 identifiers; coarsen dates to year; coarsen ZIP to first 3 digits. | Custom de-identification module — not built. |
| **Limited PHI with targeted BAA** | Transmit only the minimum-necessary fields for the use case (e.g. medication interactions: drug list only, no demographics). | BAA with LLM provider; field-level filtering; per-call audit logging. |
| **Full PHI under BAA** | Treat the LLM provider as a Business Associate. Pass identifiable data over TLS. | BAA with LLM provider; signed under §164.502(e); subprocessor BAAs all the way down; per-call audit logging; patient consent or documented treatment exception per §164.506. |

Anthropic, OpenAI, and Azure OpenAI all offer BAAs to customers on
their healthcare-tier offerings, so path 3 is practically achievable —
but it commits the deployment to the LLM provider's HIPAA program in
perpetuity.

### 5.6 Other Regulatory Frameworks

| Framework | Status |
|---|---|
| **ONC certification (45 CFR Part 170)** | OpenEMR has ONC certification sponsors and certified releases. EHI Export module implements §170.315(b)(10). FHIR R4 + US Core 8.0 + SMART-on-FHIR v2.2.0. Inferno test kit integration (`ci/inferno/onc-certification-g10-test-kit`). |
| **21 CFR Part 11 (FDA electronic signatures)** | E-signature workflow (`esign_signatures`) supports hash + signature_hash + amendment workflow. Formal Part 11 audit not evidenced; this is "Part 11–capable", not "Part 11–certified". |
| **42 CFR Part 2 (SAMHSA — substance use)** | **Not explicitly implemented.** No segmentation, no separate audit trail, no re-disclosure prohibition. If used for SUD treatment records, supplementary controls are required. |
| **State-specific (mental health, HIV, genetic)** | ACL system *can* be configured to restrict access to sensitive categories, but there is no built-in differential protection by data class. Configuration responsibility falls on the deployment. |
| **GDPR** | Partial. Audit logging (yes), encryption (available), right-to-erasure (technically possible via hard-delete, but soft-delete is the default and complicates this). No consent management, no DPA templates. |

### 5.7 Deployment Configuration Checklist

These are settings the deployment should set before any PHI flows
through an AI agent:

- [ ] `enable_auditlog_encryption = true`
- [ ] `enable_atna_audit = true` and `atna_audit_host` pointing at an
      external append-only audit store
- [ ] `gbl_force_log_breakglass = true`
- [ ] `http_verify_ssl = true` (default; do not disable)
- [ ] HTTPS enforced at the reverse proxy with HSTS
- [ ] `password_max_failed_logins` configured (e.g. 5)
- [ ] `password_expiration_days` configured per organisational policy
- [ ] MFA required for users with system-level scope
- [ ] OAuth2 token lifetime reviewed (short access tokens, refresh
      token rotation)
- [ ] Database `users` separated: app user with INSERT-only grant on
      `log` table; audit-reader role with SELECT-only
- [ ] `sites/*/documents/` on encrypted filesystem
- [ ] Encryption keys rotated; KMS-backed if available

---

## 6. TL;DR — Findings Across All Audits

### 6.1 The Five Things to Fix First

1. **`interface/globals.php:155-157`** — `pid` is taken from `$_GET`
   and bound to session without verifying the user can access that
   patient. This is the highest-impact security bug surfaced by the
   audit.
2. **PHP opcache is disabled** in CI/Docker (`ci/nginx/php.ini:2-3`).
   Enabling it is a 30–50% throughput win for free.
3. **API logging is synchronous** to the `api_log` table on every
   request. Move it async or accept a 10–20 ms baseline cost on every
   call — and an agent makes a lot of calls.
4. **No application-level rate limiting on the API.** An agent in a
   loop will saturate the system; an attacker will discover that.
5. **`pid` lives in URL query strings.** It leaks to browser history,
   server logs, and upstream proxies — a HIPAA exposure on every page
   load.

### 6.2 What Is Actually Strong

- Audit logging is comprehensive, structured, and forwardable to ATNA.
  Forensic reconstruction of "who accessed what, when?" is feasible.
- Authentication: bcrypt, MFA, lockout, password history. This part
  is grown-up.
- CSRF, XSS escaping helpers, and parameterised SQL are all present and
  consistently used in the modern code paths.
- The encryption infrastructure (`CryptoGen`) is well-designed —
  versioned ciphers, dual-key, HMAC-authenticated. It just isn't
  applied to enough fields by default.
- The architecture is genuinely extensible via custom modules + events
  — building an AI agent as a custom module avoids forking core.

### 6.3 What Will Surprise an Agent Implementer

- **No foreign keys anywhere.** The schema's "graph" lives in
  application code only.
- **481+ globals load on every request**, regardless of what the
  request is doing.
- **N+1 queries** in many services. If your agent reads a patient's
  observations, expect tens of extra round trips behind the scenes.
- **Free text and coded values coexist** in the same table for
  medications, problems, allergies. The agent must read both.
- **Default `_limit = 0` is unbounded.** You can accidentally pull
  every patient.
- **Encounter "is it open?" is implicit** (no state column —
  inferred from `date_end IS NULL`).

### 6.4 Pre-Flight Checklist for AI Agent Integration

Before any PHI transits to an LLM:
- [ ] BAA executed with LLM provider, or de-identifier built and
      validated.
- [ ] Custom module containing the agent — do not fork core.
- [ ] Per-call audit log (separate from OpenEMR's audit trail) of
      what was sent, what was returned, which user, which patient.
- [ ] Field-level minimum-necessary filtering on outbound payloads.
- [ ] Patient `pid` ownership check on every agent action that names
      a patient.
- [ ] OAuth2 client for the agent with narrowly scoped `system/*`
      scopes.
- [ ] Rate limit and concurrency cap so a runaway agent loop cannot
      saturate the system.
- [ ] LLM call moved to a background worker — never in the request
      thread.
- [ ] Test against the disabled paths: `enable_atna_audit`,
      `enable_auditlog_encryption`, MFA enforcement.

### 6.5 Final Verdict

OpenEMR is a **serious, certifiable EHR** with the bones of HIPAA
compliance and the extensibility to host an AI agent without forking.
It is **not deployment-ready as a PHI-handling system out of the box**
— several toggles are off by default, several gaps are
deployment-configuration-dependent, and a small number of structural
issues (the `pid` ACL hole, document encryption, application-level
TLS, rate limiting) need real engineering to close.

The practical path is: build the agent as a custom module, wire it
through OAuth2 + FHIR, log every PHI exchange to a dedicated table
with hash-chained integrity, run all LLM calls async out of a
queue-backed worker, and gate the deployment on the configuration
checklist in §5.7. With those in place, the system supports the
intended use case. Without them, it doesn't.
