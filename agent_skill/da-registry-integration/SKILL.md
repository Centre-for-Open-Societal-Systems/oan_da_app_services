---
name: da-registry-integration
description: >-
  Committed team skill for DA Services registry integrations (Crop Sown, Farmer,
  Livestock, Grievance) and Visits permission contract. Use with
  agent_skill/da-backend-team. Includes the Registry Settings cleanup playbook
  (Crop Sown / DA-435): Settings-owned hosts and register ids, no site_config
  fallback after migrate, FE CSRF forward, no discovery hop, HTTP-only clients.
---

# DA registry integration

| | |
| --- | --- |
| **Purpose** | Domain policy for registry integrations and the Visits permission contract. |
| **Audience** | Engineers, reviewers, and architects. |
| **Owner** | DA Services backend |
| **Companion** | `agent_skill/da-backend-team/SKILL.md` for coding standard, Postman, tests, and verification gates |

## Scope

This skill defines production rules for Crop Sown, Farmer, Livestock, Grievance,
and Visits / attendance / gathering permission contracts. Apply it together with
the team coding standard.

**Working principles**

- Inspect the existing integration path before changing it. Prefer the simplest
  correct change that matches Frappe and this codebase.
- Do not invent master data, dual configuration trees, or shortcuts that paper
  over an empty master or a missing sync.
- Keep imports at the top of the module (stdlib → third-party → local). Defer an
  import only for a documented circular dependency.
- This is a government production system. A wrong code, a dropped field, or a
  sample treated as a live value is a defect. Do not guess.

## Git writes

Do not commit, push, merge, rebase, reset, or stage for commit unless the
**current** user message authorizes that exact action. Follow
[`AGENTS.md`](../../AGENTS.md) and team process. Feature-branch pushes go to the
configured `fork` remote only. Never attribute commits or PRs to AI tools.

## Registry Settings cleanup playbook (reuse for each module)

**Reference implementation:** Crop Sown (Jira **DA-435**). Apply the same
cleanup when optimizing Farmer, Livestock, Grievance, or DA Registry wrappers.
Do not invent a second pattern.

### Goal

Move operator-owned host / register configuration from `site_config` onto Desk
**DA Services Settings**, fail closed, drop unused discovery and mock/site_config
sample files, and forward the FE SSO session to staff (never invent CSRF).

### Done when (checklist)

| Step | Rule | Crop Sown reference |
| --- | --- | --- |
| 1. Settings fields | Human/SI adds Desk fields on `DA Services Settings` (AI updates JSON after fields exist). Domain + register id. Descriptions do **not** mention site_config or “http backend”. | `crop_registry_domain`, `crop_registry_form_register_id` |
| 2. Shared resolvers | Use `da_services.services.utils`: `SETTINGS_DOCTYPE`, `base_url_from_domain`, `required_setting_value`, `StaffAPIError`. Do not re-implement domain→URL or blank checks. Map `StaffAPIError` at the module boundary if needed (Grievance → `GrievanceServiceError`). | `client.py` helpers + factory |
| 3. Factory | Package `__init__.py` (or factory) builds **HTTP only**. No `*_backend` mock/http switch. No `frappe.conf.get("…")` for host/register/keycloak/api_key when Settings owns those values. | `get_crop_registry_client` |
| 4. No discovery hop | If Settings stores `register_id`, do **not** call `get_all_registers`. One staff call per user intent. | removed `_discover_register_id` |
| 5. Auth / CSRF | Forward FE `Authorization` and `X-CSRF-Token` as header **and** cookie. Missing either → 401. **Never** self-generated CSRF. Test token providers may override bearer only. | `http._auth_and_csrf_headers` |
| 6. Optional catalogs | Settings `master_data_domain` (optional). Empty → `catalog_loaded: false`. | `_master_data_url` / `_catalogs` |
| 7. Delete dead config | Remove unused `config.example.json` / mock clients; grep and drop unused `frappe.conf.get("<module>_")`. | crop mock + sample configs removed |
| 8. No duplicate validation | Factory validates Settings once; do not re-check the same URL in HTTP `__init__`. | dropped crop `_validate_config` URL check |
| 9. Imports | Module-top imports; avoid client↔http cycles via factory in `__init__.py`. | crop `__init__.py` |
| 10. API errors | Catch `StaffAPIError` with client errors → validation; server → 503. | `api/v1/crop_sown.py` |
| 11. Docs / Postman / tests | Same change set per **da-backend-team** Postman upsert + scoped tests + Ruff. | Crop docs / Postman / `test_crop_registry` |

### Order of work (repeat per module)

1. Confirm operator-edited values → Settings fields.
2. Wire factory to Settings via shared utils; fail closed; remove site_config reads.
3. Remove discovery / mock / sample config / generated CSRF / duplicate validates.
4. Update API mapping, docs, Postman, tests; Ruff + affected bench modules.
5. Jira acceptance criteria match the checklist (Crop template: DA-435).

### Module status

| Module | Settings-owned host / register | Cleanup status |
| --- | --- | --- |
| Crop Sown | `crop_registry_domain`, `crop_registry_form_register_id`; catalogs via `master_data_domain` | **Done** (DA-435) |
| Grievance | `grievance_base_url` via `base_url_from_domain` | Partial |
| Farmer | Still largely site_config / keycloak | **Pending** |
| Livestock | Still site_config; CSRF forward aligned with Crop | **Pending** host/register Settings |
| DA Registry / Master sync | Domains on Settings for sync paths | Keep shared utils; do not reintroduce site_config hosts |

### Do not regress

- Do not restore module `*_registry_*` site_config keys after Settings owns them.
- Do not Desk-allowlist staff `source` / `tab_id` / form names the FE must send.
- Do not reintroduce mock backends once HTTP-only was chosen.
- Do not generate CSRF for SSO-forwarded staff POSTs.

## Desk settings vs site_config vs Python

| Kind of config | Where | Examples |
| --- | --- | --- |
| Legacy hosts / secrets (modules not yet cleaned) | `site_config.json` / `DA Site Config` | Farmer/Livestock Keycloak until migrated |
| Operator-editable hosts, domains, register ids | Desk **DA Services Settings** | `crop_registry_domain`, `crop_registry_form_register_id`, `grievance_base_url`, `master_data_domain` |
| Staff path wiring | Python only | `source=submission` → `search_in_intake_form_submissions` |

1. Prefer Desk Settings for staff host + register id after the cleanup playbook.
2. site_config remains for true secrets / legacy Keycloak until that module migrates.
3. Do not put Crop `source` or register `tab_id` allowlists on Desk or in Python.

## Staff session (caller-forwarded)

On registry routes that forward the caller:

- `Authorization: Bearer <Keycloak access token>`
- `X-CSRF-Token: <csrf>` (same value as cookie)
- Cookie double-submit: `X-CSRF-Token=<csrf>`

Crop Sown and Livestock HTTP clients forward those headers. Missing either → 401.
**Do not generate a CSRF token.** Do not fall back to stored username/password /
client secret / API key on SSO-forwarded paths.

Server-to-server sync uses Settings sync account via `services.utils.sync_account_login`
/ `post_staff_api` — CSRF from the login result, not `secrets.token_urlsafe`.

**Crop Sown:** host + register id from Settings; no `get_all_registers`; catalogs
from `master_data_domain` when set. Farmer/Livestock may still use site_config
until they follow the playbook. Field maps come from live metadata — do not
hardcode.

## Desk geography (Frappe masters)

Local geography for Desk Links, RBAC scopes, Visit stamps, and User Permissions
is synced into `DA Country` / `DA Region` / `DA Zone` / `DA Woreda` / `DA Kebele`
via `geo_master_sync` (Settings sync account + `master_data_domain`). Prefer
`da_services.services.geo` helpers. Do not invent master codes. Do not call
DA Registry `get_da` only to stamp Visit/Task geo Links.

## Visits — hard rules

Canonical coding standard: `agent_skill/da-backend-team/SKILL.md`. Senior review
rejects re-adding forbidden items.

### Forbidden

- `require_roles` on visit API routes
- `ScopeContext` / `get_scope_context` / `ctx=` on visit API or `services/visits.py`
- `da_visit_query_conditions` / `has_da_visit_permission`
- `_resolve_da_*` / `_session_da_id` / `resolve_da_for_read`
- `_load_visit` or list-by-name permission gates before `get_doc`
- Service-side `has_permission` / `check_permission` to gate visits
- `ignore_user_permissions: 1` on Visit geo Links
- Plan `user` fallbacks to session / `ctx.user`
- Registry / `get_da` geo fill for Visit stamps

### Required

- Plan: FE sends `user` (required). `da_id` from `fetch_from: user.da_registry_id`.
- Lists/reports: `get_list`. Detail/mutations: plain `frappe.get_doc` then save/insert.
- Product rules only in the service (status machine, evidence, `client_op_id`, …).
- Tests: `user=`; `frappe.set_user` before scoped asserts; **scoped** cleanup only
  (never wipe whole DocTypes). Postman plan sends `user`.

## Crop Sown farmer profile reads

| Mode | Query | Staff call |
| --- | --- | --- |
| List submissions | `?source=submission` | One `search_in_intake_form_submissions` |
| List register | `?source=register` | One `search_in_a_register` |
| Submission detail | `?submission_id=` | `get_intake_form_submission` + tabs |
| Register detail | `?internal_record_id=` **and** `?tab_id=` | One `get_tab_records` |

- List without `source` → 400. Do not Desk-allowlist `source` or `tab_id`.
- Do not pass `submission_id` and `internal_record_id` together.
- No synthetic top-level `location` object; GPS stays in staff sections/tabs.
- Intake: `GET /crop-sown/intake-form?form=` with **required** `form`.

### Call budget (planning form)

1. Settings `crop_registry_form_register_id` (no `get_all_registers`).
2. `get_all_intake_forms` once for that register.
3. `render_intake_form` once for the selected form.
4. `get_register_fields` once per distinct `section_register_id`.
5. `get_attribute_values` per attribute (master-data host from Settings), every page.
6. Geo only for the level the user opens.

## Live metadata before claiming fields

Allowed sources, in order: live registry response → runtime metadata
(`render_intake_form`, `get_register_fields`, `get_attribute_values`) → registry
source repo when runtime is unavailable. Seed SQL and frontend option arrays are
not the live catalog. If unverified, say so and stop.

Keep facts separate: echoed vs submittable vs widget-required vs read-only vs
catalog (`value_id`) vs static option.

## Postman collections in this repo

Upsert rules live in **da-backend-team**. Feature folders under `postman/`:

| Folder | Notes |
| --- | --- |
| `CropSown` | Registry / crop profile |
| `Farmers` | Farmer registry |
| `Grievance` | Grievance |
| `Livestock` | Livestock registry |
| `Visits` | Visits permission contract |
| `Dashboard` | Dashboard |
| `DA-Registry` | DA Registry / master sync |
| `Surveys` | Surveys |
| `AttendancePunches` | Attendance |
| `Advisors` | Advisors |
| `Gatherings` | Gatherings |
| `Broadcast` | Broadcast |
| `Charts` | Charts |
| `DA-Services` | Shared collection / local server environment |

When adding a new feature folder, create collection + environment and list it in
the PR. Empty secrets only; use `{{variables}}`.

## Analysis before code

When asked whether a screen or flow is covered, report the gap first. Implement
only when requested: screen needs → DA route today → staff requirement → missing
piece. Prefer metadata-driven mappings; tests use fixture metadata, not copied
production codes.
