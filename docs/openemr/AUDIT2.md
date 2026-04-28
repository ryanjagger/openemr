# OpenEMR System Audit 2 — Deep-Dive Expansion

**Date:** 2026-04-27
**Scope:** OpenEMR codebase (`/Users/ryan/gauntlet/openemr`)
**Branch:** `feature/docs`
**Status:** Supplemental to `docs/AUDIT.md`
**Agent/Model** Opencode / KimiK2.6

---

## About This Document

`docs/AUDIT.md` (830 lines) provides a comprehensive baseline covering Security, Performance, Architecture, Data Quality, and Compliance/Regulatory audits. This document (`AUDIT2.md`) augments that baseline with deeper findings from additional parallel codebase exploration, focusing on areas where the first audit could be expanded: background job architecture, Docker/DevOps security, template engine duality, encryption key storage posture, and ONC certification state.

**If you only read one audit, read `AUDIT.md` first.** Use this document for expanded technical detail on specific subsystems.

---

## Table of Contents

1. [Security Audit — Expanded](#1-security-audit--expanded)
2. [Performance Audit — Expanded](#2-performance-audit--expanded)
3. [Architecture Audit — Expanded](#3-architecture-audit--expanded)
4. [Data Quality Audit — Expanded](#4-data-quality-audit--expanded)
5. [Compliance & Regulatory Audit — Expanded](#5-compliance--regulatory-audit--expanded)
6. [Executive Summary](#6-executive-summary)

---

## 1. Security Audit — Expanded

### 1.1 Authentication — Additional Findings

| Area | Finding | Severity | File |
|---|---|---|---|
| OAuth2 key rotation | `OAuth2KeyConfig.php` manages RSA/EC key pairs at `sites/{site}/documents/certificates/`. Passphrase-protected via `CryptoInterface`. No automatic rotation schedule is enforced. | Low | `src/Common/Auth/OAuth2KeyConfig.php` |
| JWT signing algorithms | `RsaSha384Signer.php` uses RSA-SHA384. No algorithm confusion vulnerability (no `none`, no weak `RS256` downgrade). | OK | `src/Common/Auth/OpenIDConnect/JWT/RsaSha384Signer.php` |
| Session fixation prevention | `SessionUtil.php` sets `cookie_samesite=Strict` and regenerates ID on privilege change. | OK | `src/Common/Session/SessionUtil.php` |
| Redis session locking | `LockingRedisSessionHandler.php` prevents concurrent-write races on Redis-backed sessions. | OK | `src/Common/Session/Predis/LockingRedisSessionHandler.php` |
| Patient portal session | `PatientSessionUtil.php` isolates portal sessions from staff sessions. | OK | `src/Common/Session/PatientSessionUtil.php` |

### 1.2 Authorization / Access Control — Additional Findings

| Area | Finding | Severity | File |
|---|---|---|---|
| Break-glass DB isolation | `BreakglassChecker` uses a **separate database connection** from the main app connection to avoid circular dependencies when used as DBAL middleware. This is architecturally sound but means break-glass auth bypasses some connection-pool optimizations. | Low (architectural) | `src/Common/Logging/BreakglassChecker.php` |
| SMART app admin ACL | `ClientAdminController.php` enforces ACL + CSRF on all SMART client registration actions. | OK | `src/FHIR/SMART/ClientAdminController.php` |
| Authorization listener coverage | `AuthorizationListener.php` subscribes to kernel events, but the build does not statically verify that every REST controller method calls `request_authorization_check()` — spot-checks are the only defense. | Medium | `src/RestControllers/Subscriber/AuthorizationListener.php` |

### 1.3 Docker & Deployment Security

The first audit flags default credentials in dev-easy Docker. A deeper read of both compose files reveals additional concerns:

| Area | Finding | Severity |
|---|---|---|
| **Production compose hardcoded passwords** | `docker/production/docker-compose.yml` still contains `MYSQL_ROOT_PASSWORD: root`, `OE_USER: admin`, `OE_PASS: pass` in the example file. Operators may deploy unchanged. | **High** (deployment) |
| **Production MySQL lacks SSL** | Dev-easy compose mounts SSL certs for MySQL (`../library/sql-ssl-certs-keys/easy/`). Production compose does not mention MySQL SSL/TLS at all. | Medium (deployment) |
| **Dev-easy Xdebug profiler enabled** | `XDEBUG_PROFILER_ON: 1` in dev-easy. Acceptable for dev, but a container misconfiguration could expose profiler output. | Low |
| **Dev-easy CouchDB credentials** | `COUCHDB_USER: admin`, `COUCHDB_PASSWORD: password` — CouchDB used for document storage alternative. | Low (dev only) |
| **Dev-easy Selenium VNC password** | `SELENIUM_VNC_PASSWORD: openemr123` — VNC access to Selenium grid for E2E testing. | Low (dev only) |

### 1.4 PHI Handling — Additional Findings

| Area | Finding | Severity | File |
|---|---|---|---|
| Document-level encryption | `documents` table has `storagemethod` and `couch_docid` columns, but encryption is not enabled by default for files stored in `sites/*/documents/`. Relies on filesystem permissions only. | **High** | `sql/database.sql:1391-1432` |
| CouchDB document storage | Optional CouchDB backend for documents does not enforce encryption-at-rest in the application layer. | Medium | `docker/development-easy/docker-compose.yml` |
| `esign_signatures` integrity | E-signatures store `hash` and `signature_hash` columns. No HMAC key rotation documented. | Low | `sql/database.sql` |
| Temporary file cleanup | `deleter.php:264` `unlink()`s temporary CDA/CCDA files. No cryptographic overwrite; filesystem-level recovery may be possible on some storage layers. | Medium | `interface/patient_file/deleter.php` |

### 1.5 API Security — Additional Findings

| Area | Finding | Severity | File |
|---|---|---|---|
| Token introspection | `TokenIntrospectionRestController.php` implements RFC 7662 token introspection. This is correctly scoped to admin clients only. | OK | `src/RestControllers/TokenIntrospectionRestController.php` |
| FHIR bulk export | `$export` operation is supported (`FhirBulkExportDomainResourceTrait`). Bulk export can expose large patient populations; scope checks must be rigorous. | Medium | `src/Services/FHIR/` |
| Local API CSRF | `LocalApiAuthorizationController.php` validates `APICSRFTOKEN` for internal/local API calls. This bridges the legacy UI to modern API endpoints securely. | OK | `src/RestControllers/Authorization/LocalApiAuthorizationController.php` |
| Scope parsing | `ScopePermissionParser.php` correctly handles SMART v2.2.0 granular scopes (`.cruds` syntax) and context prefixes (`patient/`, `user/`, `system/`). | OK | `src/RestControllers/SMART/ScopePermissionParser.php` |

---

## 2. Performance Audit — Expanded

### 2.1 Background Job Architecture Detail

The first audit notes that background jobs run "sequentially and synchronously" via `library/ajax/execute_background_services.php`. While legacy cron callbacks remain synchronous, the **modern runner** (`BackgroundServiceRunner.php`) introduces significant improvements not fully characterized in the baseline audit:

| Feature | Implementation | Performance Impact |
|---|---|---|
| Lease-based locking | `lock_expires_at` column in `background_services` table. Survives worker crashes (SIGKILL, OOM, container restart). | Prevents duplicate job execution; avoids thundering-herd on cron overlap |
| Atomic acquire-or-steal | `UPDATE` with `lock_expires_at < NOW()` allows another worker to steal an expired lease automatically. | Self-healing; no manual intervention needed |
| Orchestrator advisory lock | `GET_LOCK('openemr.bg_orchestrator', ...)` in MySQL prevents concurrent `run-all-due` storms. | Eliminates race conditions when multiple containers trigger cron simultaneously |
| Subprocess isolation | `SymfonyBackgroundServiceSpawner.php` spawns PHP sub-processes for `run-all-due`, keeping the parent process responsive. | Prevents long-running jobs from blocking the orchestrator |
| Lease bounds | Floor: 60 minutes; ceiling: 1440 minutes; grace period: 60 seconds. | Jobs cannot hold locks indefinitely; predictable recovery time |

**Residual concern:** The legacy path (`library/ajax/execute_background_services.php:32`) still executes jobs sequentially inline. Any background service registered via the old callback path does not benefit from subprocess isolation or lease-based locking. A migration audit of all `background_services` rows is recommended.

### 2.2 Caching — Additional Findings

| Layer | Status | Evidence |
|---|---|---|
| APCu user cache | **Not used** — zero references in `/src/`. | `grep -r 'apcu_fetch\|apcu_store\|ApcuCache' src/` returns nothing |
| Symfony Cache component | Present in `composer.json` (`symfony/cache`) but **not wired into QueryUtils or service layer**. | `grep -r 'CacheItemPoolInterface\|TagAwareCacheInterface' src/` returns only test files |
| Redis (non-session) | `LockingRedisSessionHandler.php` uses Redis for sessions. No evidence of Redis for query result caching or object caching. | |
| Twig cache | No production cache directory explicitly configured in inspected config files. | |
| OPcache | Explicitly disabled in CI/Docker (`ci/nginx/php.ini:2-3`). This is the single largest missed performance opportunity. | |

**Recommendation:** Enable OPcache immediately (30–50% throughput gain). Then evaluate Symfony Cache with APCu for hot globals and Redis for cross-process query result caching.

### 2.3 N+1 Query Patterns — Expanded Evidence

The first audit lists several N+1 occurrences. Additional spots found during deeper exploration:

| Service | Line | Pattern |
|---|---|---|
| `PatientNameHistoryService.php` | 120 | Name history fetched per patient in a loop |
| `ObservationLabService.php` | 152 | Lab observations loaded per encounter |
| `AppointmentService.php` | 197, 387 | Appointment details fetched individually |
| `ConditionService.php` | 89 | Conditions queried per patient |
| `ImmunizationService.php` | 168 | Immunizations loaded per patient |
| `VitalsService.php` | 206 | Vitals fetched per encounter |
| `GroupService.php` | 112 | Group memberships queried per user |
| `FhirPatientService.php` | (multiple) | FHIR resource building triggers nested service calls |

These patterns inflate agent latency significantly: an agent reading a patient summary may trigger 6–10 sequential child queries, each with the 125–300ms baseline overhead documented in the first audit.

### 2.4 Database Schema & Indexing — Additional Notes

- **510 indexes** declared in `sql/database.sql`, but **zero `FOREIGN KEY` constraints** anywhere in the schema. This means:
  - MySQL's optimizer cannot use FK metadata for join ordering.
  - Orphaned rows are possible and are only prevented by application code.
  - Cascading deletes/updates must be implemented manually.

- **`SELECT *` is pervasive** — 148 occurrences in `/src/Services/` alone. On `patient_data` (90+ columns, several `LONGTEXT`), this is materially wasteful.

- **`form_encounter` and `patient_data`** both use `uuid BINARY(16)`. MySQL 8.0 handles binary UUIDs well, but ordering and range queries on UUIDs are less efficient than auto-increment integers for large datasets.

---

## 3. Architecture Audit — Expanded

### 3.1 The Dual-Timeline Problem — Deeper Characterization

OpenEMR contains two fully active codebases:

| Dimension | Legacy (`/library/`, `/interface/`) | Modern (`/src/`) |
|---|---|---|
| **Paradigm** | Procedural PHP, global functions | OOP, PSR-4, DI |
| **Database access** | `sqlStatement()`, `sqlQuery()`, `sqlStatementNoLog()` — direct ADODB | `QueryUtils` via Doctrine DBAL 4.x |
| **State management** | `$GLOBALS`, `$_SESSION`, `$_GET`, `$_POST` | `OEGlobalsBag`, injected services, PSR-7 requests |
| **Templating** | Smarty 4.5 | Twig 3.x |
| **Frontend** | Angular 1.8.3 (EOL), jQuery 3.7.1 | Bootstrap 4.6.2, some modern JS |
| **Error handling** | `die()`, `echo` error messages | Exceptions, PSR-3 logging, structured responses |
| **Type safety** | None — loose typing, `empty()`, `isset()` | `declare(strict_types=1)`, native types, PHPStan level 10 |

**Critical observation:** The legacy path is not deprecated — it is the primary execution path for most pages. Modern services are called from legacy controllers via `$GLOBALS['kernel']->getContainer()->get(PatientService::class)` or similar patterns. This means every request pays the overhead of both worlds.

### 3.2 Event System Detail

The Symfony EventDispatcher is wired into the API layer and available via `OEGlobalsBag::getKernel()`:

| Event Namespace | Count | Notable Events |
|---|---|---|
| `OpenEMR\Events\RestApiExtend\` | 7 | `RestApiScopeEvent`, `RestApiSecurityCheckEvent`, `RestApiCreateEvent`, `RestApiResourceServiceEvent` |
| `OpenEMR\Events\Patient\` | (multiple) | `patient.created`, `patient.updated`, `patient.before_save` |
| `OpenEMR\Events\Encounter\` | (multiple) | `encounter.created`, `encounter.updated` |
| `OpenEMR\Events\Appointment\` | (multiple) | `appointment.set`, `appointment.created` |
| `OpenEMR\Events\Core\Sanitize\` | 2 | `IsAcceptedFileFilterEvent` |

**For agent integration:** Custom modules should subscribe to these events rather than monkey-patching service classes. Example: listen to `patient.updated` to invalidate agent context caches.

### 3.3 Middleware Chain (API Requests)

The API middleware stack (`ApiApplication`) processes in this order:

1. `ExceptionHandlerListener` — catches `
Throwable`, returns generic JSON
2. `TelemetryListener` — optional usage telemetry (can be disabled)
3. `ApiResponseLoggerListener` — synchronous DB write to `api_log`
4. `SessionCleanupListener` — garbage-collects stale session data
5. `SiteSetupListener` — detects site from URL path (`/apis/{site}/...`)
6. `CORSListener` — CORS headers for browser clients
7. `OAuth2AuthorizationListener` — validates bearer tokens / OAuth2 flows
8. `AuthorizationListener` — enforces ACL / scope checks
9. `RoutesExtensionListener` — hand-rolled pattern matcher for REST routes
10. `ViewRendererListener` — JSON/Twig response rendering

**Latency note:** Items 1–8 all execute before any business logic. Items 3 and 7 are the heaviest (DB writes and token validation).

### 3.4 Custom Module Integration Points

The recommended architecture for adding AI agent capabilities:

```
interface/modules/custom_modules/oe-module-ai-agent/
├── openemr.bootstrap.php          # Module entry point — registers event listeners
├── src/
│   ├── AgentService.php           # Core agent orchestration
│   ├── LlmClient.php              # Outbound LLM call abstraction
│   ├── AuditLogService.php         # Per-call PHI exchange logging
│   └── PatientScopeValidator.php   # Validates user→patient access before agent acts
├── config/
│   └── module.config.php          # Service registration
└── README.md                      # BAA documentation, consent flows
```

**Bootstrap example:**

```php
// openemr.bootstrap.php
use OpenEMR\Events\RestApiExtend\RestApiResourceServiceEvent;
use OpenEMR\Core\ModulesApplication;

$eventDispatcher = $GLOBALS['kernel']->getEventDispatcher();
$eventDispatcher->addListener(
    RestApiResourceServiceEvent::EVENT_HANDLE,
    function (RestApiResourceServiceEvent $event) {
        // Register agent-specific REST endpoints
    }
);
```

### 3.5 Templating Engine Duality

| Engine | Location | Escaping Strategy | Risk |
|---|---|---|---|
| Twig 3.x | `/templates/` | Auto-escape by default; `|e`, `|e('html_attr')` filters | Low — modern escaping is enforced |
| Smarty 4.5 | `library/smarty_legacy/`, `library/templates/` | Manual `|escape` modifier; `htmlspecialchars` in PHP before assignment | Medium — consistency depends on developer discipline |
| Raw PHP | `interface/` (legacy forms) | `htmlspecialchars()`, `text()`, `attr()` helpers | Medium — helpers exist but are not compiler-enforced |

**Audit concern:** Angular 1.8.3 (used heavily in `interface/`) is EOL and has known CSP/escaping edge cases. The `ng-bind-html` directive and `$sanitize` service are used in some forms; a full inventory of `ng-bind-html` usage would be prudent.

---

## 4. Data Quality Audit — Expanded

### 4.1 Soft Delete / Activity / Active Semantics — Full Matrix

The first audit notes inconsistent flag semantics. The expanded matrix:

| Table | Flag | Type | Default | Semantics |
|---|---|---|---|---|
| `pnotes` | `deleted` | tinyint | 0 | soft delete |
| `forms` | `deleted` | tinyint | 0 | soft delete |
| `forms` | `activity` | tinyint | 0 | unclear — appears to mean "active in encounter workflow" |
| `lists` | `activity` | tinyint | 1 NOT NULL | active in problem list |
| `list_options` | `activity` | tinyint | 1 NOT NULL | visible in dropdowns |
| `drugs` | `active` | tinyint | 1 | visible in prescriber UIs |
| `background_services` | `active` | tinyint | 0 | enabled for cron |
| `users` | `active` | tinyint | 1 | user account enabled |
| `patient_data` | `deceased_date` | date | NULL | patient death — related to data quality but not a delete flag |
| `insurance_data` | `inactive` | tinyint | 0 | insurance policy no longer active |
| `amendments` | `status` | varchar | "NEW" | amendment workflow state |
| `documents` | `deleted` | tinyint | 0 | soft delete |

**Agent implication:** Any read-path must filter on the correct flag for the table. A generic "exclude deleted" predicate (`deleted = 0`) will miss `activity = 0` rows on `lists` and include inactive drugs on `drugs`.

### 4.2 Date/Time Handling — Expanded

- **Timezones:** Bootstrap sets `date_default_timezone_set('UTC')` globally. Appointment times in `openemr_postcalendar_events.pc_time` are `DATETIME` with **no timezone column**. The system assumes all times are in the site's local timezone, but this is not stored per-row.
- **Timestamp columns:** Some tables use `TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP` (auto-managed), others use `DATETIME` with manual management. This means:
  - `form_encounter.last_update` is automatic.
  - `patient_data.last_updated` is manual — may be stale if a writer forgets to set it.
- **String dates:** `PatientService.php:177-178` handles dates as PHP strings, not `\DateTimeImmutable`. This bypasses type safety and makes date arithmetic error-prone.

### 4.3 Duplicate Detection — Expanded

- **`PersonService::checkForDuplicates()`** (lines 489–527) detects duplicates by exact match on `first_name`, `last_name`, `birth_date` against the `person` table. No fuzzy matching (Soundex, Levenshtein, etc.).
- **`patient_data.dupscore`** exists (`INT NOT NULL default -9`) but **no code references it**. The column appears to be a vestige of a planned dedup feature that was never implemented.
- **Phone numbers:** Stored as raw strings across four fields (`phone_home`, `phone_biz`, `phone_contact`, `phone_cell`). No E.164 normalization. `libphonenumber` is a transitive dependency (via `giggsey/libphonenumber-for-php`) but is **not wired into the patient write path**.
- **Addresses:** No USPS normalization. A `USPS/AddressVerifyV3` client exists (`src/USPS/`) but is not invoked on patient writes.

### 4.4 Coded Fields vs Free Text — Medication Example

The `prescriptions` table stores medications three ways:

| Column | Type | Content | Example |
|---|---|---|---|
| `drug` | varchar(150) | Free text | "Lisinopril 10mg" |
| `drug_id` | int(11) | FK to `drugs` table (optional) | 42 |
| `rxnorm_drugcode` | varchar(25) | RxNorm code (optional) | "197361" |

**Nothing enforces consistency.** A prescription may have free text only, a drug ID only, an RxNorm code only, or any combination. The agent must read all three and reconcile heuristically.

### 4.5 Code System Coverage — Expanded

| System | Storage | Required? | Coverage Notes |
|---|---|---|---|
| CVX (vaccines) | `immunizations.cvx_code varchar(64)` | nullable | Used if populated; not enforced |
| RxNorm (medications) | `prescriptions.rxnorm_drugcode varchar(25)` | nullable | Used if populated; not enforced |
| SNOMED CT | `rule_criteria_data` references | nullable | Present in decision rules; not in core patient data |
| ICD-9 | `icd9_dx_code`, `icd9_sg_code` tables | n/a | Legacy; still present |
| ICD-10 | **Not found in schema** | absent | Significant gap for US billing |
| LOINC | `procedure_result.result_code` | nullable | Lab result codes |
| CPT | `billing.code` with `code_type = 'CPT4'` | n/a | Billing codes |
| HCPCS | `billing.code` with `code_type = 'HCPCS'` | n/a | Billing codes |
| NDC | `drugs.ndc_number varchar(11)` | nullable | Drug packaging codes |

**ICD-10 absence is notable:** The schema contains ICD-9 tables but no dedicated ICD-10 diagnosis code table. ICD-10 is likely handled via the generic `codes` table or `billing` table with `code_type` discrimination. This should be verified before any billing-related agent feature is built.

---

## 5. Compliance & Regulatory Audit — Expanded

### 5.1 HIPAA Technical Safeguards — Additional Detail

| Safeguard | 45 CFR Citation | Additional Notes |
|---|---|---|
| Audit controls | §164.312(b) | `api_log` captures `user_id`, `patient_id`, `ip_address`, `method`, `request_url`, `request_body`, `response`, `created_time`. This is **more detailed than many EHRs**, but the synchronous DB write is a performance cost. |
| Audit log integrity | §164.312(c)(1) | `log.checksum` is SHA1/SHA3-512. `audit_log_tamper_report.php` recomputes and flags discrepancies. **However**, a DBA with `UPDATE` privileges can modify both the row and the checksum if they know the algorithm. ATNA forwarding is the only immutable defense. |
| Unique user identification | §164.312(a)(2)(i) | `users.username` is unique. UUIDs are used in `users.uuid` and `patient_data.uuid`. No shared accounts are supported by the schema. |
| Automatic logoff | §164.312(a)(2)(iii) | `timeout` global (default 7200s = 2 hours). `portal_timeout` (1800s = 30 minutes). `SessionTracker::isSessionExpired()` enforces. |
| Encryption at rest | §164.312(a)(2)(iv) | `CryptoGen` (AES-256-CBC + HMAC-SHA384) is **opt-in per field**. Documents in `sites/*/documents/` are **not encrypted by default**. This is the largest HIPAA encryption gap. |
| Encryption in transit | §164.312(e)(1) | Application does not enforce HTTPS. Relies on reverse proxy (nginx, Apache) configuration. ATNA supports mTLS. |
| Data integrity | §164.312(c)(1) | `esign_signatures` table stores hash + signature_hash for electronic signatures. Amendment workflow via `amendments` and `amendments_history` tables. |
| Person/entity authentication | §164.312(d) | bcrypt default, Argon2id supported. MFA (TOTP/U2F). Failed-login lockout (`users_secure.login_fail_counter`). Password history (last 4). Password expiration configurable. |
| Emergency access (break-glass) | §164.312(a)(2)(ii) | `breakglass` ACL group. `BreakglassChecker`. `gbl_force_log_breakglass` global. **No time-limited break-glass session** — a break-glass user retains elevated access until manually downgraded or session expires. |
| Access control | §164.312(a)(1) | phpGACL ACO/ARO/AXO model. Granular to section-level (e.g., `admin`, `patients`, `encounters`). **No field-level ACL** — if a user can read `patient_data`, they see all columns. |

### 5.2 42 CFR Part 2 (SAMHSA — Substance Use Disorders)

**Status: Not explicitly implemented.**

- No SUD record segmentation.
- No separate audit trail for SUD data.
- No re-disclosure prohibition mechanism.
- No "QSO" (Qualified Service Organization) tracking.

If OpenEMR is used in an SUD treatment context, supplementary controls are required: either custom ACL categories for SUD records, a separate module, or deployment-level restrictions.

### 5.3 GDPR — Expanded

| Requirement | Status | Notes |
|---|---|---|
| Lawful basis | Partial | No consent management framework. Treatment record basis is implied, not explicit. |
| Right to access | OK | Patient portal provides access to own records. EHI Export module (`oe-module-ehi-exporter`) supports structured export. |
| Right to rectification | OK | Data can be edited through UI and API. |
| Right to erasure | Partial | Soft-delete is default (`deleted=1`). Hard delete exists (`deleter_row_delete()` in `interface/patient_file/deleter.php`) but must be explicitly invoked. GDPR's "right to be forgotten" may conflict with medical record retention laws. |
| Data portability | OK | EHI Export implements §170.315(b)(10), which aligns with GDPR portability requirements. |
| Privacy by design | Partial | Modern `/src/` code follows good practices; legacy `/interface/` and `/library/` do not. |
| DPA / BAA templates | **Absent** | No DPA templates in repo. BAA references exist for specific integrations (MedEx, fax/SMS) but no generic template. |

### 5.4 ONC Certification State

| Program | Status | Evidence |
|---|---|---|
| ONC Health IT Certification (45 CFR Part 170) | **Certified releases exist** | OpenEMR has ONC certification sponsors. `tests/certification/tests.md` references end-user device encryption tests. |
| EHI Export (§170.315(b)(10)) | **Implemented** | `interface/modules/custom_modules/oe-module-ehi-exporter/` with SchemaSpy-generated documentation in `Documentation/EHI_Export/docs/` |
| FHIR R4 + US Core 8.0 | **Implemented** | 41 FHIR resources in `src/Services/FHIR/` |
| SMART-on-FHIR v2.2.0 | **Implemented** | `src/RestControllers/SMART/`, `src/FHIR/SMART/` |
| Inferno Test Kit | **Integrated** | `ci/inferno/onc-certification-g10-test-kit/` |
| 21 CFR Part 11 (FDA) | **Capable, not certified** | E-signature workflow (`esign_signatures`) supports hash + signature. No formal Part 11 audit documented. |

**Certification caveat:** ONC certification is release-specific. Running `master` or a custom branch may void certification. The deployment should verify it is running a certified release build.

### 5.5 BAA Implications of Sending PHI to an LLM Provider — Expanded

The first audit outlines three paths. This section adds operational detail for each.

#### Path 1: De-Identified Data Only (Safe Harbor)

**Requirements under 45 CFR §164.502(b) / §164.514(b):**
1. Remove all 18 identifiers listed in §164.514(b)(2)(i).
2. Coarsen dates to year only (except ages ≥ 90 → aggregate to "90+").
3. Coarsen ZIP codes to first 3 digits (or remove if population < 20,000).
4. No actual knowledge that remaining information could be used alone or in combination to identify the individual.

**OpenEMR gap:** No built-in Safe Harbor de-identifier. A custom module would need to:
- Strip identifiers from `patient_data`, `form_encounter`, `billing`, `insurance_data`, `documents`.
- Coarsen dates in all clinical tables.
- Remove/document free-text fields that may contain embedded identifiers (notes, referral letters).
- Audit every de-identification run.

#### Path 2: Limited PHI with Targeted BAA

**Scenario:** Agent needs only medication list for drug-interaction checking.
**Outbound payload:** `drug`, `rxnorm_drugcode`, `dosage`, `route`, `frequency` from `prescriptions`. No demographics.
**Requirements:**
1. BAA with LLM provider (e.g., Anthropic HIPAA offering, Azure OpenAI with healthcare agreement).
2. Field-level filtering module that extracts only necessary fields.
3. Per-call audit log: what was sent, what was returned, timestamp, user, patient (if scoped).
4. Minimum necessary review by privacy officer.

#### Path 3: Full PHI Under BAA

**Scenario:** Agent reads full clinical summaries for diagnostic assistance.
**Requirements:**
1. BAA with LLM provider under §164.502(e).
2. Sub-processor BAAs all the way down (hosting, CDN, logging).
3. TLS 1.2+ for all transmissions.
4. Per-call audit logging (separate from OpenEMR's `api_log` — agent-specific table).
5. Patient consent or documented treatment exception per §164.506.
6. Data retention agreement: LLM provider must not retain inputs for model training (zero-retention policy).
7. Breach notification clause in BAA.

**Provider availability (as of 2026-04-27):**
- Anthropic: Offers HIPAA Business Associate Addendum for enterprise customers.
- OpenAI: Offers Business Associate Agreement for healthcare organizations on Enterprise tier.
- Azure OpenAI: Offers HIPAA BAA through Microsoft healthcare compliance program.
- Google Cloud Vertex AI: Offers BAA for healthcare workloads.

**Recommendation for this codebase:** Path 2 (limited PHI) is the pragmatic starting point. Build a field-level extraction module, execute a BAA with a healthcare-tier LLM provider, and log every exchange. Path 3 requires significantly more legal and operational infrastructure.

---

## 6. Executive Summary

### 6.1 What AUDIT.md Got Right (and You Should Read It First)

`docs/AUDIT.md` is a high-quality, actionable audit. Its "Five Things to Fix First" (§6.1) are correct and well-prioritized:

1. **`interface/globals.php:155-157`** — `pid` from `$_GET` without ownership check is the highest-impact security bug.
2. **PHP opcache disabled** — 30–50% throughput win for free.
3. **Synchronous API logging** — 10–20ms baseline cost on every call.
4. **No API rate limiting** — agent loops will saturate the system.
5. **`pid` in URLs** — HIPAA exposure to browser history, server logs, proxies.

### 6.2 What This Document Adds

| Area | Key Addition |
|---|---|
| **Background jobs** | Modern `BackgroundServiceRunner` has lease-based locking and subprocess isolation — but legacy cron path is still sequential and unprotected. |
| **Docker security** | Production compose ships with hardcoded passwords and lacks MySQL SSL. |
| **Caching** | APCu and Symfony Cache are present in dependencies but completely unused. Redis is session-only. |
| **Templating** | Angular 1.8.3 (EOL) in `interface/` is a latent XSS risk. Smarty/Twig duality requires ongoing developer discipline. |
| **Data quality** | `dupscore` column is unused. ICD-10 table is absent. Phone/address normalization is not wired into write paths. |
| **Compliance** | 42 CFR Part 2 (SAMHSA) is unimplemented. GDPR right-to-erasure conflicts with soft-delete defaults. No DPA/BAA templates. |
| **LLM BAA** | Three paths documented with operational requirements. Path 2 (limited PHI + targeted BAA) is the pragmatic recommendation. |

### 6.3 Updated Pre-Flight Checklist for AI Agent Integration

Before any PHI transits to an LLM:

- [ ] Read `docs/AUDIT.md` in full.
- [ ] Execute BAA with LLM provider, OR build and validate Safe Harbor de-identifier.
- [ ] Build agent as custom module (`interface/modules/custom_modules/oe-module-ai-agent/`).
- [ ] Implement per-call audit log (separate table: `llm_call_log` with `request_hash`, `response_hash`, `user_id`, `patient_id`, `scope`, `timestamp`, `integrity_checksum`).
- [ ] Implement field-level minimum-necessary filtering on outbound payloads.
- [ ] Validate patient `pid` ownership on every agent action (do not rely on session `pid` alone).
- [ ] Create OAuth2 confidential client for agent with narrowly scoped `system/*` scopes.
- [ ] Add API rate limiting (Symfony RateLimiter or nginx `limit_req`) — at minimum 100 req/min per client.
- [ ] Move LLM calls to background worker (queue-backed, never in request thread).
- [ ] Enable OPcache in production (`php.ini`).
- [ ] Enable `enable_auditlog_encryption = true`.
- [ ] Enable `enable_atna_audit = true` with external append-only store.
- [ ] Review and fix Docker production compose: remove hardcoded passwords, add MySQL SSL.
- [ ] Test against disabled-path scenarios: ATNA down, MFA enforcement, encryption key rotation.

### 6.4 Final Verdict

OpenEMR is a **mature, certifiable EHR** with strong foundations in authentication, audit logging, and encryption infrastructure. It is **not turnkey for PHI-bearing AI agents** — several structural issues (`pid` ACL hole, document encryption defaults, API rate limiting, soft-delete ambiguity) require real engineering. The dual legacy/modern codebase means the agent implementer must navigate both worlds carefully.

The recommended path remains:
1. **Fix the top 5 issues** from `AUDIT.md` §6.1.
2. **Build the agent as a custom module** using FHIR + OAuth2.
3. **Start with limited-PHI Path 2** for LLM integration.
4. **Log everything** to a dedicated, hash-chained audit table.
5. **Run LLM calls async** from a queue-backed worker.

With these controls in place, OpenEMR supports the intended AI agent use case. Without them, the compliance and security risks are unacceptably high.

---

*End of AUDIT2.md*
