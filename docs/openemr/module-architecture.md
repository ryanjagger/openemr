# OpenEMR Module Architecture for AI Features

Yes — there's a real, mature plugin architecture. Here's the practical summary.

## The Three Hook Points

### 1. Custom modules

Custom modules live at `interface/modules/custom_modules/{your-module}/`.
Eight modules already ship in-tree (Comlink Telehealth, EHI Exporter, FaxSMS,
Weno e-prescribing, Dorn, ClaimRev, Prior Authorizations, Dashboard Context).
Each has the same shape:

```
oe-module-yours/
├── openemr.bootstrap.php   # entry point — wires event listeners
├── composer.json           # PSR-4 namespace
├── Module.php              # optional Laminas MVC config
├── info.txt                # name + version for the modules table
├── version.php
├── sql/                    # install/upgrade DDL
└── src/                    # your code
```

Modules are auto-discovered by `OpenEMR\Core\ModulesApplication` and
activated through Admin → Modules → Manage Modules (sets `mod_active=1`
in the `modules` table). No core fork required.

### 2. Symfony EventDispatcher

The Symfony EventDispatcher exposes 79+ event classes across `src/Events/`.
The bootstrap file just registers listeners against the kernel's
dispatcher. Working example at
`tests/eventdispatcher/oe-patient-create-update-hooks-example/openemr.bootstrap.php:64-66`:

```php
$eventDispatcher = OEGlobalsBag::getInstance()->getKernel()->getEventDispatcher();
$eventDispatcher->addListener(PatientCreatedEvent::EVENT_HANDLE, 'your_handler');
$eventDispatcher->addListener(PatientUpdatedEvent::EVENT_HANDLE, 'your_handler');
```

For an AI feature, the events that matter are:

- `src/Events/Patient/` — Before/After create+update
  (`BeforePatientCreatedEvent`, `PatientCreatedEvent`,
  `PatientUpdatedEvent`, plus a `Summary` subdir for the patient summary
  page)
- `src/Events/Encounter/`, `src/Events/Appointments/`,
  `src/Events/PatientDocuments/`, `src/Events/Messaging/`
- `src/Events/RestApiExtend/` — `RestApiCreateEvent`,
  `RestApiResourceServiceEvent`, `RestApiScopeEvent`,
  `RestApiSecurityCheckEvent` for adding new REST/FHIR endpoints from a
  module
- `src/Events/UserInterface/` — for surfacing AI output in the existing
  UI (sidebar widgets, summary cards)
- `src/Events/Main/` — section/menu injection
- `src/Events/PatientReport/`, `src/Events/PatientFinder/` — useful spots
  to inject AI-generated context

### 3. CDS Hooks

`src/RestControllers/CDS/`, `src/ClinicalDecisionRules/` — the HL7
standard for inserting decision-support cards into the clinical workflow
at well-defined trigger points (`patient-view`, `order-select`, etc.).
This is the *cleanest* fit if your AI feature is "show me a
recommendation when a clinician opens a patient" — the protocol is
already designed for an external service that returns cards, and
OpenEMR already speaks it.

There's also a third example worth reading:
`tests/eventdispatcher/RestApiEventHookExample/` shows the
module-as-Laminas-app pattern for adding API endpoints.

## Recommended Path for an AI Module

Build `oe-module-ai-assistant/` as a custom module that:

1. Subscribes to `PatientUpdatedEvent`, `EncounterUpdatedEvent` etc. for
   write-side context invalidation.
2. Adds REST endpoints via `RestApiResourceServiceEvent` (e.g.
   `POST /apis/default/api/ai/summarize-patient/:pid`).
3. Pushes the actual LLM call onto a queue/worker — never in-line, since
   the request thread already has 125–300 ms of platform overhead before
   your code runs.
4. For clinician-facing recommendations, expose a CDS Hooks service so
   cards land in the existing UI surfaces with no front-end work.
5. Use the Dashboard Context module (`oe-module-dashboard-context/`) as
   the closest existing template — it adds UI, persists state, and ships
   SQL migrations.

The CLAUDE.md-flagged file conventions (PSR-4, `declare(strict_types=1)`,
`BaseService`, dependency injection, no `$GLOBALS` access) all apply
inside the module.
