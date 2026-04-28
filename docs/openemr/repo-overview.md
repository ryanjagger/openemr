# OpenEMR Repo Overview

OpenEMR is a large, long-lived open-source **electronic health records (EHR) and medical practice management** application written primarily in PHP. The codebase mixes legacy procedural code with modern PSR-4 / strictly-typed code.

## Stack

- **PHP 8.2+**, MySQL via Doctrine DBAL (with an ADODB-compatible surface for legacy code)
- **Backend frameworks:** Laminas MVC + Symfony components
- **Templates:** Twig 3 (modern) and Smarty 4 (legacy)
- **Frontend:** Angular 1.8, jQuery 3.7, Bootstrap 4.6, Gulp 4 + SASS
- **Testing:** PHPUnit 11, Jest 29; PHPStan level 10, Rector, custom PHPStan rules in `tests/PHPStan/Rules/`

## Layout

- `src/` — modern code under the `OpenEMR\` namespace (PSR-4)
- `library/` — legacy procedural helpers
- `interface/` — web UI controllers/templates
- `templates/` — Smarty/Twig templates
- `apis/`, `oauth2/`, `FHIR_README.md`, `API_README.md` — REST + FHIR APIs
- `ccdaservice/`, `ccr/` — clinical document exchange (C-CDA, CCR)
- `sql/`, `db/` — schema and migrations (Doctrine Migrations for new changes)
- `tests/` — unit, e2e, api, services, isolated PHPUnit suites
- `docker/development-easy/` — primary local dev environment (`docker compose up`)
- `gacl/` — legacy access control library
- `Documentation/`, `contrib/`, `modules/` — docs and pluggable modules

## Coding Standards

New code is held to a much stricter bar than legacy code:

- `declare(strict_types=1)`, native types everywhere
- Readonly value objects, enums over constants
- `QueryUtils` instead of raw DB calls
- `OEGlobalsBag` instead of `$GLOBALS`
- Constructor DI (no static service locators)
- PSR-3 logging with context arrays
- Exhaustive `match` on enums
- No new PHPStan baseline entries

Legacy patterns (`$_SESSION`/`$GLOBALS`, untyped arrays, `empty()`) are tolerated but not a model for new code. See `CLAUDE.md` for the full standards.

## Common Workflows

### Local development

```bash
cd docker/development-easy
docker compose up --detach --wait
```

App at http://localhost:8300/ — login `admin` / `pass`.

### Tests

```bash
# Inside Docker
docker compose exec openemr /root/devtools clean-sweep-tests
docker compose exec openemr /root/devtools unit-test
docker compose exec openemr /root/devtools api-test
docker compose exec openemr /root/devtools e2e-test
docker compose exec openemr /root/devtools services-test

# Isolated tests on host (no Docker)
composer phpunit-isolated
```

### Code quality

```bash
composer code-quality        # phpstan, phpcs, rector, codespell, …
composer phpstan
composer phpcs
composer rector-check
npm run lint:js
npm run stylelint
```

### Build

```bash
npm run build     # production build
npm run dev       # watch mode
```

### Commits

Follow [Conventional Commits](https://www.conventionalcommits.org/). AI-assisted commits get an `Assisted-by:` trailer.
