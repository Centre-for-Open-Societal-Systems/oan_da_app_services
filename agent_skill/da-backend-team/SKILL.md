---
name: da-backend-team
description: >-
  Team-wide engineering standard for the DA Services Frappe/Python backend.
  Apply when implementing, modifying, debugging, testing, or reviewing APIs,
  services, DocTypes, permissions, background jobs, and external integrations.
  Gates: upsert Postman on API contract changes, scoped test cleanup only,
  scenario-driven minimal tests, Ruff + affected bench modules before done/push.
---

# DA Services Backend — Team Coding Standard

| | |
| --- | --- |
| **Purpose** | Backend engineering policy for DA Services (Frappe/Python). |
| **Audience** | Engineers, reviewers, and architects. |
| **Owner** | DA Services backend |
| **Companion** | `agent_skill/da-registry-integration/SKILL.md` for registry/Visits domain policy |

## Purpose and scope

Act as a senior Frappe/Python engineer working in the **DA Services backend**.
Optimize for correctness, security, maintainability, performance, and compatibility
with the existing repository. This is a **tool-neutral** standard: apply the same
rules regardless of which tooling is used.

This file defines **project policy**, not a replacement for reading the code.
Follow the actual repository's version, conventions, contracts, and documented
domain-specific exceptions. If an instruction conflicts with verified behavior,
security requirements, or a domain contract, surface the conflict rather than
silently changing behavior.

**Key principles:** Frappe owns framework responsibilities; services own product
logic; APIs remain thin; permissions are explicit and testable; changes are small
and verifiable.

Also load when relevant: `agent_skill/da-registry-integration/SKILL.md` (Crop /
Farmer / Livestock / Grievance Settings cleanup, FE CSRF, Visits hard rejects),
`docs/visits-integration.md`, `docs/rbac.md`.

## 1. Required working process

1. **Inspect before editing.** Check branch/status, relevant API handlers,
   services, DocType definitions/controllers, permissions, hooks, fixtures,
   tests, related integrations, and existing helpers. Read applicable docs such
   as `docs/rbac.md`, `docs/visits-integration.md`, survey/registry integration
   docs, or adjacent design notes when present.
2. **Establish the contract.** Identify the requested behavior, callers,
   required inputs, authorization model, response shape, and compatibility risks.
   Do not invent requirements, fields, endpoints, or master data.
3. **Choose the owner.** Decide what Frappe already handles and what belongs in
   the product service. Reuse established code before adding abstractions.
4. **Implement a focused change.** Preserve unrelated behavior; avoid broad
   refactors, speculative features, and unnecessary files.
5. **Verify.** Add/update focused tests, upsert Postman when the HTTP contract
   changed, run Ruff on touched Python and **affected** test modules, inspect
   the diff for regressions.
6. **Report accurately.** State the change, tests actually run (or not run),
   assumptions, and migration/deployment or compatibility risks.

Do not claim a test passed, an integration was verified, or a behavior exists
unless it was actually checked.

## 2. Architecture and ownership

| Layer | Owns | Should not own |
| --- | --- | --- |
| `api/v1/` | HTTP parsing, endpoint wiring, authentication entry point, response envelope | Business decisions, duplicate validation, geography rules |
| `services/` | Product rules, workflows, orchestration, application-specific validation, mapping | Re-implementing framework DocPerm or User Permissions |
| DocType JSON/controllers | Schema, required/Link fields, lifecycle validation, document events | HTTP transport and response formatting |
| `integrations/` | External client contracts, timeouts, safe retries, remote payloads | DA Services authorization policy |
| Permission hooks | Documented domain-specific permission extensions | Duplicate checks already covered by built-in permissions |
| `utils/` | Genuinely reusable helpers with clear ownership | One-off wrappers that obscure straightforward operations |

Use the existing project response envelope, naming, conventions, and error
handling. Validate each rule once at the responsible layer; do not add the same
check in an API handler and its service without a distinct reason.

Prefer **one external staff/registry round trip per user intent** (for example
one search for a crop list, one `get_tab_records` for a register tab). Do not
N× fetch detail rows to build a list. Domain call budgets live in the registry
skill.

## 3. DocTypes and Frappe lifecycle

- **Do not create a new DocType or invent its schema.** A designated human/SI
  provides and creates the initial definition (via Desk or committed JSON).
  Once that definition exists, update its fields, controller, permission wiring,
  and related code only as the task requires.
- Prefer DocType metadata for `reqd`, Link validation, `fetch_from`, and other
  standard field behavior. Add Python validation only for a separate product
  rule or a documented framework gap.
- Prefer `doc.insert()` / `doc.save()` and their lifecycle hooks over direct DB
  writes. Respect submit/cancel/amend semantics and transaction boundaries.
- Before adding hooks, consider repeated execution, recursion, concurrent
  writes, background jobs, and side effects. Do not bypass lifecycle hooks
  accidentally with direct updates.
- Use existing geographical master data and standard fixtures. Never invent
  master codes or parallel/shadow schemas.

## 4. Permissions and data access — security-critical

**Do not confuse fetching a record with authorizing access to it.**

- For user-visible lists, use permission-aware `frappe.get_list()` (or the
  equivalent approved scoped API). Filters may **narrow**, never expand,
  the records the current user may see.
- `frappe.get_all()`, `frappe.db.get_value()`, `frappe.db.count()`, and other
  low-level DB lookups must **not** be treated as user-scoped permission checks.
  Use them for internal/trusted operations only when access is already
  authorized and the scope is intentional.
- For writes, use the normal document APIs with permissions enabled. Do not
  introduce `ignore_permissions=True` to get past a failing authorization check.
  Privileged bypasses require an established, documented domain exception.
- Prefer DocPerm, User Permissions, and existing approved hooks to hard-coded
  role lists or duplicated geographic scope checks. Never infer authorization
  from a client-supplied user ID, role, farmer ID, or geography filter.
- On restricted list queries, an inaccessible geography ordinarily yields an
  empty result; an unauthorized detail/write operation must not expose data.
- Ensure exceptions and API responses do not leak sensitive fields, internal
  queries, tokens, or private implementation details.

### Domain-specific detail access

Authorization for a single document follows **that domain’s contract**:

| Domain | Detail / mutate | Do not add |
| --- | --- | --- |
| **DA Visit** (and attendance/gathering when docs match) | Role DocPerm + User Permissions via `get_list` / plain `frappe.get_doc` / `insert` / `save` | `_load_visit`, service `check_permission` / `has_permission` mirrors, Visit query hooks |
| Surveys and other documented privileged domains | Follow that domain’s doc (may use hooks + approved `ignore_permissions`) | Blind Visit rules or inventing a second gate |
| Other DocTypes without a special contract | Prefer DocPerm-aware APIs; use explicit `doc.check_permission("read")` only when that domain’s contract requires it | Hard-coded role lists that duplicate DocPerm |

### DA Visit / attendance / gathering contract

For **DA Visit**, and matching attendance/gathering paths where repository docs
specify the same policy:

- Use Role DocPerm + User Permissions for row visibility and document access.
  Do not add separate `require_roles`, geography scope helpers, or custom Visit
  query/permission hooks when the framework policy already governs access.
- List/plan calls require the client `user` parameter under the current API
  contract. Validate its presence and type **once in the service**; return a
  specific validation error for missing/blank values. Do **not** silently
  replace it with `frappe.session.user`.
- Treat that `user` value as **input/filter context, not proof of identity**.
  Never let it authorize access beyond the authenticated session's grants.
- Do not use `ignore_user_permissions` on Visit geographical Link fields or
  unscoped `get_all` / `db.count` for user-visible Visit results.
- Avoid introducing `da_visit_query_conditions`,
  `has_da_visit_permission`, or `_load_visit`-style permission mirrors.

**Scope exception:** Surveys and other domains can have different, documented
permission models (including privileged operations and approved scope hooks).
Inspect the specific domain contract; do not apply Visit rules globally.

## 5. Validation and API contracts

- APIs handle transport parsing, malformed JSON, file/multipart concerns,
  and the established response envelope. Services enforce product rules,
  required query parameters, and workflow constraints.
- Required caller inputs are truly required. Use honest type annotations
  (`str` for required values, optional types only for optional filters) and
  runtime checks at the trust boundary. Do not call `.strip()` on unverified
  non-string input.
- Fail with a clear, stable error for missing/invalid inputs. Do not hide
  errors through silent defaults, broad exception catches, or session fallbacks.
- Preserve endpoint contracts unless the task explicitly authorizes changes.
  Consider frontend compatibility, pagination, filtering, and status codes.
- Make retryable mutations idempotent where the product contract requires it;
  do not assume retries are harmless.

### API create or modify → Postman upsert (hard rule)

When you **add or change** a DA Services HTTP API (new route, request/response
shape, auth, filters, or status codes), the **same change set** must:

1. **Upsert** `postman/<Feature>/OAN-DA-<Feature>.postman_collection.json`
   (create if missing; update if present). Update the matching
   `*.postman_environment.json` when new variables are required.
2. Cover SSO/login (when the surface needs it), happy path, primary filters,
   and the main validation/denial case.
3. Keep `usr`, `pwd`, and tokens **empty**. Placeholders stay `{{variables}}`.
4. Parse the JSON; run `postman collection lint` when the CLI is installed.
5. Update `docs/<feature>-integration.md` and `docs/api-da-screens.md` when the
   screen contract changed.

A new or changed API without an updated Postman collection is **incomplete**.
Do not defer Postman to “later.”

## 6. Python quality and maintainability (clean code)

- Write small, cohesive functions with descriptive names and simple control
  flow. Prefer explicit Python over clever abstractions or deep nesting.
- Reuse standard-library, Frappe, and existing project helpers before adding
  dependencies or utilities.
- **Do not add trivial one-off wrappers** such as `_strip()` or `_as_time()`.
  Inline a simple expression. Extract a helper when its behavior is genuinely
  reusable, sufficiently complex, independently testable, or improves clarity.
- Keep module imports at the top. Use local imports only for a real circular
  dependency, optional dependency, or other documented reason.
- Catch specific exceptions when possible. Never swallow failures or log
  secrets. Use `frappe.throw` / appropriate framework errors consistently.
- Do not create unnecessary docs, files, new layers, or unrelated refactors.
- Prefer less code that matches the contract: drop dead fallbacks, duplicated
  validation, and work that falls outside the agreed scope.

## 7. Database and performance

This project uses **MariaDB locally** and **PostgreSQL in shared/dev
environments**. Keep code portable between both engines.

- Prefer Frappe ORM/document/query APIs over raw SQL. Do not introduce
  database-specific SQL syntax or string-interpolated queries.
  `frappe.db.sql` is banned in app code unless there is a documented,
  engine-portable reason and no ORM equivalent (see `pyproject.toml`).
- Assume large datasets: use database-side filters, stable ordering,
  pagination, bounded result sets, efficient joins/indexes, and suitable
  transactions. Avoid N+1 queries and loading entire DocTypes into Python.
- Use `frappe.enqueue` for appropriate long-running or bulk work. Make jobs
  retry-safe where possible, pass identifiers rather than large objects, and
  reload fresh documents in the worker.
- Review API/Redis calls, cache invalidation, locks, and migration effects
  for non-trivial changes.

## 8. External registries and authentication

- Reuse existing registry clients, DTOs/mappings, settings, and integration
  conventions. Validate external responses and handle timeouts, failures,
  rate limits, and idempotent retries explicitly.
- For migrated Registry integrations (Farmer, Crop Sown, Livestock, Grievance),
  prefer Desk-managed **DA Services Settings** and existing helpers such as
  `base_url_from_domain` / `required_setting_value` when present. Do not
  reintroduce undocumented `site_config` fallbacks.
- Where the existing staff API contract requires caller-forwarded credentials,
  forward the approved `Authorization` and `X-CSRF-Token` values (including
  required cookie behavior) **only to the configured trusted destination**.
  Do not invent CSRF values, leak headers to arbitrary hosts, or bypass the
  application's authentication and authorization checks.
- When refactoring a registry integration, load
  `agent_skill/da-registry-integration/SKILL.md` (Settings cleanup playbook).
  Keep code, tests, docs, and Postman aligned in the same change set.
- Log useful identifiers and failure context, never passwords, access tokens,
  private keys, auth headers, or sensitive personal data.

## 9. Testing and verification

### Scenario-first, then minimal tests

Before writing tests, enumerate the scenarios that matter for the change:

- Happy path
- Missing/invalid required inputs (validation)
- Unauthorized / out-of-scope (where the domain uses Frappe permissions)
- Empty results / not found
- Duplicates / idempotency / retries when the contract cares
- Edge cases that would hide a real regression

Then add **only the necessary** unit/integration tests that lock those
behaviors. Do not add speculative or duplicate coverage.

### Cleanup — test-owned rows only (hard rule)

- Clean up **only records the test created** (by name, id, or a filter the
  test owns, e.g. `cleanup_test_rows(users=…, da_ids=…)`).
- **Never** truncate DocTypes, wipe shared masters, or `DELETE` / clear an
  entire table.
- Prefer stubbing external HTTP (as Crop Sown registry tests do) over writing
  shared Desk data when the behavior under test is the client/contract.

### Fixtures and execution

- Follow `da_services/tests/` and standard fixtures (shared geographical
  masters). Do not invent one-off master codes when fixtures already define them.
- Include a test proving a caller cannot access another user's record where
  IDOR is a real risk for that surface.
- Act as the right user with `frappe.set_user(...)` before scoped asserts.

### Ruff and affected tests before done / push (hard rule)

On touched Python, match `pyproject.toml` (line length 110, tabs, double
quotes, target Python 3.14):

```bash
# Host (typical)
py -3 -m ruff check <touched files>
py -3 -m ruff format <touched files>
py -3 -m ruff check <touched files>
py -3 -m ruff format --check <touched files>

# Affected modules only (inside Frappe container)
docker exec oan_da_services_dev-frappe-1 bash -lc \
  "cd /workspace/development/frappe-bench && bench --site da-services.localhost \
   run-tests --app da_services --module da_services.tests.test_<feature>"
```

- Format **only** files in the change. Do not repo-wide format unrelated files.
- Do not invent a passing test result if the bench is unavailable — say tests
  were not run.
- Do not push (when push is authorized) until Ruff is clean on touched files
  and affected modules have been run or explicitly reported as not run.

## 10. Debugging, reviews, and Git safety

- Debug in this order: reproduce/understand → trace the owning layer → find
  root cause → smallest correct fix → regression test.
- In reviews, prioritize **security, correctness, permissions, data integrity,
  performance, compatibility, and tests** over cosmetic style; classify
  findings by severity and cite the affected code.
- Inspect `git status` and the current branch before editing. Preserve user
  changes. Do not rewrite history, force-push, reset, delete branches, or
  commit/push unless the task authorizes that action.
- **Fork-only feature pushes:** when explicitly asked to push, inspect the
  configured remotes and push feature work through the team's `fork` remote;
  raise a blocker if the expected fork remote is missing. Do not push feature
  branches directly to upstream `origin`; use a PR into the approved target.
- Never commit credentials, environment files, unrelated generated artifacts,
  or secrets. Never attribute commits/PRs to AI tools.

## 11. Completion response

Keep the final explanation concise and useful to another engineer:

1. **Changed:** important files/modules and resulting behavior.
2. **Design:** what Frappe owns vs. what the service implements; assumptions.
3. **Verified:** exact tests, Ruff checks, and manual checks performed; clearly
   mark anything not run. Confirm Postman and docs when the API/screen contract
   changed.
4. **Risks:** backward compatibility, migration, deployment, permissions,
   pending review, or follow-up tasks.

**Definition of done:** a focused, permission-safe, portable implementation;
matching tests (scoped cleanup only); Postman and docs updated when the HTTP
or screen contract changed; Ruff clean on touched Python; affected tests run
or honestly marked not run; no unrelated changes.
