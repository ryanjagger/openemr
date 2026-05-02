# OpenEMR Audit — Summary

## Key Findings

I ran several audits of OpenEMR from a few different angles:

### Module Architecture:
I was trying to find the best place to integrate a new AI agent feature and came across OpenEMR's module system, Symfony EventDispatcher,  FHIR API. This seems perfect for implementing AI agent tools like tool/function calls.

### User Groups/Roles
I audited user types and summarized below. This will be useful for implementing security in AI agent, as well as user stories for new features.

| Question | Answer |
|---|---|
| Are there different user levels? | **Yes** — 6 built-in groups (Admin, Physician, Clinician, Front Office, Accounting, Emergency) |
| Is it role-based? | **Yes** — phpGACL with ARO groups mapped to ACO permissions |
| Can you restrict by function? | **Yes** — ~50+ granular permissions across 13 sections |
| Can you restrict by patient ownership? | **Partially** — `see_auth` controls encounter authorizations; but there's a known gap where `pid` can be set from `$_GET` without ownership verification |
| Is there a superuser? | **Yes** — `admin/super` bypasses all checks |
| How do APIs work? | **OAuth2 + SMART-on-FHIR scopes** — separate from phpGACL but the user identity flows through both |

### Deployment 
I have a branch `feature/deploy` deployed to Railway: openemr-production-d9e4.up.railway.app
Railway handles the antiquated lamp codebase but I am exploring a VPS as an alternative for future iterations.



OpenEMR is a **mature, ONC-certifiable EHR** with strong foundational security — bcrypt/Argon2id hashing, MFA, OAuth2 with SMART-on-FHIR v2.2.0 scopes, CSRF tokens, parameterized SQL via `sqlStatement()`, comprehensive XSS escaping helpers, AES-256-CBC + HMAC-SHA384 encryption infrastructure (`CryptoGen`), and a detailed event-based audit log with ATNA forwarding. The architecture is genuinely extensible via custom modules + Symfony EventDispatcher, making AI agent integration possible without forking core. **However, it is not deployment-ready as a PHI-handling backend out of the box** — several toggles are off by default and a small number of structural issues need real engineering.

**The single highest-impact security bug** is at `interface/globals.php:155-157`, where `$_GET['pid']` is bound to the session without any check that the authenticated user is actually allowed to access that patient. For an AI agent composing URLs from natural language, this is a direct path to unauthorized PHI access. Compounding this, `pid` lives in URL query strings (leaking to browser history, logs, proxies), there is **no application-layer rate limiting**, and document storage in `sites/*/documents/` is **not encrypted by default** — relying only on filesystem permissions.

**Performance is dominated by per-request overhead**, not query cost. The middleware stack, 481+ globals load, synchronous `api_log` write, and OAuth2 validation impose a **125–300ms baseline** before any business logic runs. **PHP opcache is disabled** in CI/Docker (`ci/nginx/php.ini:2-3`) — a 30–50% throughput win left on the table. APCu and Symfony Cache are present in `composer.json` but completely unused. Default `_limit = 0` on REST endpoints is unbounded. N+1 patterns are widespread (PatientNameHistory, ObservationLab, Appointment, Condition, Immunization, Vitals, Group services). 148 `SELECT *` occurrences hit a `patient_data` table with 90+ columns including LONGTEXT.

**Architecturally, two timelines coexist**: modern PSR-4 `/src/` with PHPStan level 10, DI, Twig, DBAL — versus legacy `/library/` and `/interface/` with globals, Smarty, ADODB, and Angular 1.8.3 (EOL). Most pages still go through the legacy entry point. Background jobs have a modern `BackgroundServiceRunner` with lease-based locking, but the legacy path (`execute_background_services.php`) remains sequential and synchronous.

**Data quality issues will surprise an agent implementer**: zero `FOREIGN KEY` constraints anywhere in 281 tables, ICD-10 absent from schema, medications stored three redundant ways (`drug` free-text + `drug_id` + `rxnorm_drugcode`) with nothing enforcing consistency, inconsistent `deleted`/`activity`/`active`/`inactive` flag semantics across tables, `dupscore` column with zero code references, no E.164 phone normalization, no USPS address validation on writes, and encounter "open vs closed" inferred from `date_end IS NULL` rather than an explicit state column.

**Compliance**: HIPAA technical safeguards are mostly strong but encryption is opt-in per field, break-glass has no time limit, audit log integrity is detective-only without ATNA forwarding offsite. **42 CFR Part 2 (SAMHSA) is unimplemented**. No de-identification utility exists — any PHI sent to an LLM is identifiable. **Production Docker compose ships with hardcoded `MYSQL_ROOT_PASSWORD: root` and no MySQL SSL.**

**Recommended path**: build the agent as a custom module, talk to data via FHIR + OAuth2 with narrow `system/*` scopes, run LLM calls in a queue-backed worker, log every PHI exchange to a dedicated hash-chained table, fix the top 5 issues from §6.1, and start with limited-PHI Path 2 under a healthcare-tier BAA.

---

## Detailed Findings by Domain

### Security
- **HIGH — `interface/globals.php:155-157`**: `pid` from `$_GET` bound to session without ownership check.
- **HIGH — Document storage**: `sites/*/documents/` not encrypted at rest by default.
- **HIGH — `pid` in URL query strings**: HIPAA exposure via browser history, logs, proxies.
- **HIGH (deployment) — Production Docker compose**: hardcoded `root`/`admin`/`pass` credentials, no MySQL SSL.
- **Medium**: No API rate limiting; `clearPass` persists in `$_POST` superglobal; no whitelist for `skip_timeout_reset`; default `admin/pass` shipped in dev-easy; field-level ACL absent (all columns visible once row returned); HTTPS not enforced at app layer; encryption keys stored on same host as data; PHI (`patient_id`) in logs; debug mode echoes SQL into logs; `HelpfulDie()` discloses query details; OAuth2 token lifetimes need deployment review.
- **OK**: bcrypt/Argon2id, `hash_equals()`, MFA (TOTP/U2F), failed-login lockout, HMAC-SHA256 CSRF, `unserialize` with `allowed_classes => false`, RsaSha384Signer (no algorithm confusion), SameSite=Strict cookies, Redis session locking, RFC 7662 token introspection scoped to admins.
- **Audit log tamper resistance**: detective only — DBA with UPDATE can modify both row and checksum unless ATNA forwards offsite.

### Performance
- **Critical — Opcache disabled** in `ci/nginx/php.ini:2-3` (30–50% throughput loss).
- **Critical — Synchronous `api_log` writes** on every request (10–20ms baseline).
- **Critical — Default `_limit = 0` unbounded** on REST endpoints.
- **Per-request floor**: 125–300ms before business logic; realistic API latency 200–600ms; agent making 5 sequential calls = 1–3s pure overhead.
- **N+1 patterns**: `PatientNameHistoryService:120`, `ObservationLabService:152`, `AppointmentService:197/387`, `ConditionService:89`, `ImmunizationService:168`, `VitalsService:206`, `GroupService:112`, `FhirPatientService` (multiple).
- **Schema**: 281 tables, 510 indexes, **zero FK constraints**, 148 `SELECT *` in services, `patient_data` has 90+ columns with LONGTEXT.
- **Caching gaps**: APCu unused (0 references), Symfony Cache unwired, Redis session-only, no Twig prod cache, no query result cache.
- **Frontend weight**: 77 named assets, ~120 npm deps, Angular 1.8.3 EOL, jQuery 3.7.1, ~2–5MB initial load, 15+ blocking script tags.
- **Background jobs**: legacy `execute_background_services.php:32` runs sequentially; modern `BackgroundServiceRunner` has lease-based locking, atomic acquire-or-steal, advisory locks, subprocess isolation (60–1440 min lease bounds).
- **Largest service classes** (cold-start cost): `CdaTemplateImportDispose.php` (2334 lines), `CdaTemplateParse.php` (1848), `SQLUpgradeService.php` (1663), `HistorySdohService.php` (1504), `ProcedureService.php` (1481), `FhirPatientService.php` (1082), `PatientService.php` (1007).

### Architecture
- **Dual timelines coexist**: modern `/src/` (PSR-4, DBAL, Twig, PHPStan level 10, strict types, DI) vs legacy `/library/` + `/interface/` (procedural, ADODB, Smarty, globals, `die()`/`echo`).
- **Namespaces**: 93 services, 92 REST controllers (41 FHIR), 79 event classes, plus FHIR/Billing/Rx/Appointment/CDR/Validators/Encryption.
- **Data layer**: MySQL 8.0+ via mysqli; Doctrine DBAL 4.x modern + ADODB legacy on same connection; `QueryUtils` bridges both.
- **Migrations**: legacy SQL upgrade files driven by `sql_upgrade.php`; Doctrine Migrations in `/db/Migrations/` "NOT fully integrated".
- **API middleware (10 listeners in order)**: ExceptionHandler → Telemetry → ApiResponseLogger → SessionCleanup → SiteSetup → CORS → OAuth2Authorization → Authorization → RoutesExtension → ViewRenderer.
- **Routes**: hand-rolled pattern matcher in PHP arrays (`apis/routes/_rest_routes_*.inc.php`), not Symfony Router.
- **Web UI**: no central router — each `/interface/` script self-dispatches via `$_GET`/`$_POST`.
- **Integration seams for AI agent**: REST API, FHIR API (R4 + US Core 8.0 + SMART v2.2.0, 41 resources, `$export`), Symfony EventDispatcher (79 event classes), custom modules at `interface/modules/custom_modules/{name}/openemr.bootstrap.php`, OAuth2 server, CDS Hooks. **No native webhook dispatcher.**
- **Templating**: Twig 3.x modern + Smarty 4.5 legacy + raw PHP in `interface/`; Angular 1.8.3 has CSP/escaping edge cases (`ng-bind-html` usage warrants inventory).
- **Config priority**: env vars → `globals` table → service defaults; `OEGlobalsBag` typed wrapper around `$GLOBALS`.
- **Multi-tenancy**: `/sites/{site}/sqlconf.php`, site detection via `$_GET['site']`/host/URL path; shared-DB isolation deployment-dependent.

### Data Quality
- **Zero FK constraints** — application code is the only thing keeping the graph connected.
- **Empty-string sentinels**: `patient_data.ss varchar(255) NOT NULL default ''` — can't distinguish "not collected" from "unknown".
- **Numeric `0` as missing-FK sentinel**: `prescriptions.drug_id NOT NULL default '0'`.
- **No CHECK constraints** — all range/format validation in PHP.
- **No uniqueness on natural keys**: `pubpid`, `ss` not unique; only `pid`/`uuid` unique.
- **Validation asymmetry**: `PatientValidator` enforces on insert; updates often skip; `library/options.inc.php` writes form fields without going through validator. Effectively "store anything, validate at display."
- **Date/time mixing**: some tables use `TIMESTAMP ON UPDATE CURRENT_TIMESTAMP` (auto), others `DATETIME` (manual); `patient_data.last_updated` (DATETIME, manual) vs `form_encounter.last_update` (TIMESTAMP, auto) — don't compare. `pc_time` stored without timezone. Dates handled as PHP strings, not `DateTimeImmutable` (`PatientService.php:177-178`).
- **Dedup absent**: `patient_data.dupscore` column has zero code references; no MPI; no merge tool. `PersonService::checkForDuplicates()` does exact-match only on fname/lname/DOB.
- **Phone numbers**: 4 fields (`phone_home`, `phone_biz`, `phone_contact`, `phone_cell`), no E.164 normalization despite `libphonenumber` being a transitive dep.
- **Addresses**: `USPS/AddressVerifyV3` exists but not invoked on patient writes.
- **Triple-redundant medications** in `prescriptions`: `drug` (free text), `drug_id` (FK), `rxnorm_drugcode`. Nothing enforces consistency. Same pattern in `lists` (problem list / allergies).
- **Code system coverage**: CVX, RxNorm, SNOMED, LOINC, NDC all nullable. ICD-9 tables present. **ICD-10 absent from schema** — significant gap for US billing.
- **Soft-delete inconsistency matrix**:

  | Table | Flag | Default | Semantics |
  |---|---|---|---|
  | `pnotes`, `forms`, `documents` | `deleted` | 0 | soft delete |
  | `forms` | `activity` | 0 | unclear |
  | `lists`, `list_options` | `activity` | 1 | active |
  | `drugs`, `users` | `active` | 1 | enabled/visible |
  | `background_services` | `active` | 0 | enabled |
  | `insurance_data` | `inactive` | 0 | inverted |
  | `amendments` | `status` | "NEW" | workflow state |

- **Encounter state**: no explicit state column — inferred from `date_end IS NULL`. `pc_apptstatus` defaults to `'-'` ("no status set").
- **Stale data signals**: not all clinical tables have `last_updated`; manual maintenance unreliable.

### Compliance & Regulatory
- **HIPAA §164.312 strengths**: audit controls (`log`, `api_log`, `extended_log`, `audit_master`, `EventAuditLogger`, ATNA), unique user ID, automatic logoff, e-signature integrity, MFA + bcrypt + lockout + password history.
- **HIPAA partial**: audit log integrity (detective only), encryption at rest (opt-in per field; documents unencrypted by default), encryption in transit (no app-layer enforcement), break-glass (no time limit), no field-level ACL.
- **42 CFR Part 2 (SAMHSA)**: not explicitly implemented — no SUD segmentation, no separate audit, no re-disclosure prohibition, no QSO tracking.
- **GDPR**: partial — no consent management, soft-delete conflicts with right-to-erasure, no DPA templates.
- **ONC certification**: certified releases exist; EHI Export §170.315(b)(10) implemented; FHIR R4 + US Core 8.0; Inferno test kit integrated. Running master/custom branches voids certification.
- **21 CFR Part 11**: capable, not certified.
- **PHI categories**: all 18 HIPAA identifiers present; full clinical content (diagnoses, meds, labs, allergies, immunizations, vitals, notes, insurance, billing).
- **No de-identification utility, no anonymisation module, no scrubber** anywhere in codebase.
- **No application-enforced minimum-necessary filter** on FHIR responses.
- **No existing LLM integrations** — net-new implementation.
- **3 BAA paths**: (1) Safe Harbor de-identifier (custom build required), (2) limited PHI + targeted BAA (pragmatic recommendation), (3) full PHI + comprehensive BAA + sub-processor BAAs + zero-retention + consent.
- **BAA availability**: Anthropic, OpenAI Enterprise, Azure OpenAI, Google Vertex all offer healthcare-tier BAAs.

### Top 5 Fixes (from AUDIT.md §6.1)
1. Fix `interface/globals.php:155-157` — validate `pid` ownership before binding to session.
2. Enable PHP opcache in CI/Docker.
3. Move `api_log` writes async.
4. Add application-level API rate limiting.
5. Stop putting `pid` in URLs — move to opaque session handles or POST bodies.

### AI Agent Pre-Flight Checklist
- BAA executed OR Safe Harbor de-identifier built.
- Agent built as custom module (`interface/modules/custom_modules/oe-module-ai-agent/`).
- Per-call audit log table (`llm_call_log` with hashes, user_id, patient_id, scope, integrity_checksum).
- Field-level minimum-necessary filtering on outbound payloads.
- Patient `pid` ownership validated on every agent action.
- OAuth2 confidential client with narrow `system/*` scopes.
- Rate limit + concurrency cap (≥100 req/min/client).
- LLM calls in queue-backed background worker, never request thread.
- `enable_auditlog_encryption = true`, `enable_atna_audit = true` with external append-only store.
- Production Docker compose: remove hardcoded passwords, add MySQL SSL.
- Test against disabled paths (ATNA down, MFA, key rotation).
