# OpenEMR Authorization & Access Control

This document describes OpenEMR's authorization and access control systems, user roles, permission levels, and how they interact.

---

## Table of Contents

1. [User Groups / Roles](#1-user-groups--roles)
2. [What Can Be Protected (ACO Sections)](#2-what-can-be-protected-aco-sections)
3. [Permission Levels (Return Values)](#3-permission-levels-return-values)
4. [The `see_auth` Column](#4-the-see_auth-column)
5. [Superuser](#5-superuser)
6. [Emergency Access (Break-Glass)](#6-emergency-access-break-glass)
7. [Portal vs. Staff Access](#7-portal-vs-staff-access)
8. [SMART-on-FHIR Scopes (API Authorization)](#8-smart-on-fhir-scopes-api-authorization)
9. [Key Files](#9-key-files)
10. [Summary](#10-summary)

---

## 1. User Groups / Roles

OpenEMR uses **phpGACL** (Generic Access Control List). At installation, it creates these default **ARO groups** (roles):

| Group | Label | Description |
|---|---|---|
| `admin` | **Administrators** | Full system access — superuser |
| `doc` | **Physicians** | Clinical provider role |
| `clin` | **Clinicians** | Clinical staff (nurses, PAs, etc.) |
| `front` | **Front Office** | Scheduling, registration, reception |
| `back` | **Accounting** | Billing, financial reporting |
| `breakglass` | **Emergency Login** | Emergency access — everything |
| `users` | **OpenEMR Users** | Parent group containing all above |

Users are assigned to one or more groups via the admin UI (`Admin → Users → User Groups`).

**Default setup happens in:**
- `library/classes/Installer.class.php:1094+`

---

## 2. What Can Be Protected (ACO Sections)

There are ~13 **ACO sections** with granular sub-permissions:

| Section | Examples of What It Controls |
|---|---|
| **`admin`** | `super`, `calendar`, `database`, `forms`, `practice`, `users`, `drugs`, `acl`, `manage_modules` |
| **`acct`** | `bill`, `disc` (discount), `eob`, `rep`, `rep_a` |
| **`patients`** | `appt`, `demo`, `med`, `trans`, `docs`, `notes`, `rx`, `lab`, `amendment` |
| **`encounters`** | `auth` (my encounters), `auth_a` (any encounters), `coding`, `notes`, `date_a` |
| **`sensitivities`** | `normal`, `high` — record sensitivity levels |
| **`lists`** | `default`, `state`, `country`, `language` |
| **`inventory`** | `lots`, `sales`, `purchases`, `transfers`, `reporting` |
| **`groups`** | Group therapy: `gadd`, `gcalendar`, `glog`, `gm` |
| **`patientportal`** | `portal` — patient portal access |
| **`menus`** | `modle` — menu module control |

---

## 3. Permission Levels (Return Values)

Each permission can have one of these **return values**:

| Value | Meaning |
|---|---|
| **`view`** | Read-only |
| **`write`** | Full read + add + modify |
| **`wsome`** | Read + limited modify |
| **`addonly`** | Read + add, but **not** modify |

**Example from the installer:**
- **Administrators** get `write` on everything.
- **Physicians** get `view` on `patients/pat_rep`, `write` on most clinical functions, but only `view` or `addonly` on some admin items.
- **Clinicians** get narrower access — for example, `sensitivities: normal` only (no `high`).

---

## 4. The `see_auth` Column

On top of group ACLs, each user has a **`see_auth`** setting in the `users` table:

| `see_auth` | Meaning |
|---|---|
| `1` | **None** — cannot see encounter authorizations |
| `2` | **Only Mine** — can only see authorizations for their own patients |
| `3` | **All** — can see all encounter authorizations |

This controls the **Authorizations** screen specifically (`Main → Authorizations`).

**Relevant file:**
- `interface/main/authorizations/authorizations.php:33-191`

---

## 5. Superuser

A critical rule in `AclMain::aclCheckCore()`:

```php
// Superuser always gets access to everything.
if (($section != 'admin' || $value != 'super') && self::aclCheckCore('admin', 'super', $user)) {
    return true;
}
```

Any user with `admin/super` permission **bypasses all other ACL checks**. This is the highest privilege level.

---

## 6. Emergency Access (Break-Glass)

The **`breakglass`** group is special:
- Gets `write` on **everything** (same as admin).
- Used for emergency situations where normal authentication may fail.
- Can be forced to always log via the `gbl_force_log_breakglass` global setting.
- Checked by `BreakglassChecker.php` using a separate DB connection.

**Relevant files:**
- `src/Common/Logging/BreakglassChecker.php`
- `interface/usergroup/usergroup_admin.php:77,260-275`

---

## 7. Portal vs. Staff Access

There are **two distinct access paths**:

| Path | Who | Auth Mechanism | API Scope |
|---|---|---|---|
| **Staff** | Doctors, nurses, admins, etc. | Username/password + MFA (optional) | `api:oemr`, `api:fhir`, SMART scopes |
| **Patient Portal** | Individual patients | Portal username/password | `api:port` |

Patient portal users are managed via the `patient_access_onsite` table and the `patient_data.allow_patient_portal` flag. Portal API routes require the `api:port` scope.

**Relevant files:**
- `src/Services/PatientAccessOnsiteService.php`
- `src/Services/PatientPortalService.php`
- `src/RestControllers/Authorization/BearerTokenAuthorizationStrategy.php:370-387`

---

## 8. SMART-on-FHIR Scopes (API Authorization)

For API/OAuth2 access, there's a **second authorization layer** orthogonal to phpGACL:

| Scope Pattern | Meaning |
|---|---|
| `patient/*.read` | Read any resource for the selected patient |
| `user/*.write` | Write resources scoped to the authenticated user |
| `system/*.read` | System-wide read (backend services) |
| `patient/Resource.cruds` | Granular CRUDS per resource type |
| `api:oemr` | Standard REST API |
| `api:fhir` | FHIR API |
| `api:port` | Portal API |

These are validated by `ScopeRepository.php` and `ScopePermissionParser.php`.

**Relevant files:**
- `src/Common/Auth/OpenIDConnect/Repositories/ScopeRepository.php`
- `src/RestControllers/SMART/ScopePermissionParser.php`
- `src/RestControllers/OpenApi/OpenApiDefinitions.php`

---

## 9. Key Files

| File | Purpose |
|---|---|
| `src/Common/Acl/AclMain.php` | Core ACL checks (`aclCheckCore`) |
| `src/Common/Acl/AclExtended.php` | Group/user management |
| `src/Common/Logging/BreakglassChecker.php` | Emergency access detection |
| `library/classes/Installer.class.php:1094+` | Default group/ACL setup |
| `interface/usergroup/usergroup_admin.php` | Admin UI for assigning users to groups |
| `interface/usergroup/usergroup_admin_add.php` | Admin UI for adding users to groups |
| `src/Common/Auth/OpenIDConnect/Repositories/ScopeRepository.php` | OAuth2/SMART scope validation |
| `src/RestControllers/SMART/ScopePermissionParser.php` | SMART scope parsing |
| `src/RestControllers/Authorization/BearerTokenAuthorizationStrategy.php` | Bearer token validation and role setup |

---

## 10. Summary

| Question | Answer |
|---|---|
| Are there different user levels? | **Yes** — 6 built-in groups (Admin, Physician, Clinician, Front Office, Accounting, Emergency) |
| Is it role-based? | **Yes** — phpGACL with ARO groups mapped to ACO permissions |
| Can you restrict by function? | **Yes** — ~50+ granular permissions across 13 sections |
| Can you restrict by patient ownership? | **Partially** — `see_auth` controls encounter authorizations; but there's a known gap where `pid` can be set from `$_GET` without ownership verification |
| Is there a superuser? | **Yes** — `admin/super` bypasses all checks |
| How do APIs work? | **OAuth2 + SMART-on-FHIR scopes** — separate from phpGACL but the user identity flows through both |
