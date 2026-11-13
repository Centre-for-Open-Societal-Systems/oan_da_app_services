# RBAC in DA Services

Deny by default, enforced in the backend on every call. A Frappe **Role** says which
actions exist for a user; a **DA RBAC Assignment** says over which Region, Woreda or DA
they may act. Both are required (HLD 3: "RBAC is necessary but not sufficient").

## Roles (FSD Appendix B)

| Role | Scope | Desk |
|---|---|---|
| Development Agent | own DA-ID | no (mobile app and API) |
| Supervisor | own Woreda | yes |
| Executive | own Region, read-only | yes |
| OAN Administrator | all | yes |
| Communications Officer | broadcast domain, no DA identity | yes |

"Woreda Officer" and "Senior DA" are titles, not roles: they get approval authority only
through a Supervisor assignment (HLD 3, 10.1). Roles are seeded by
`setup/install.py` on install and on every migrate.

## Assignment model

`DA RBAC Assignment`: `user`, `role`, `active`, `da_id`, `region_scope`, `zone_scope`,
`woreda_scope`, `effective_from`, `effective_to`, `assigned_by`, `notes`.

Validation: Supervisor needs a Woreda; Executive needs a Region; Development Agent needs
a DA-ID; a Woreda needs its Region; `effective_to >= effective_from`. Saving an active
grant adds the role to the User; revocation deactivates the row and closes the window
but never deletes it.

Region / Zone / Woreda are stored as administrative codes (`region_scope`, `zone_scope`,
`woreda_scope`). Zone is optional until the field list is final; a Zone needs its Region.

## Scope rule (`utils/permissions.py`)

`get_scope_context(user)` reads the user's active, in-window assignments once per
request. `require_roles(...)` and `require_da_access(da_id, region, woreda)` apply:

1. Unrestricted (OAN Administrator, System Manager, Administrator): always.
2. Supervisor / Executive: only if the DA's Region/Zone/Woreda, **as returned by the
   registry**, is inside their scope. The narrowest grant wins: a Woreda grant covers that
   Woreda, a Zone grant every Woreda in the Zone, a Region-only grant the whole Region.
3. Development Agent: only their own DA-ID.
4. Anyone else, including Communications Officer: denied.

Caller-supplied Region/Woreda values are never trusted for the check.

A grant is honoured only at the level it was issued. If the registry record has no value
at that level (a DA without a Zone checked against a Zone grant, or without a Woreda
against a Woreda grant), access is **denied, not widened** to the Region: widening would
hand a Zone Supervisor the whole Region whenever registry data is incomplete. The audit
row carries `reason = scope unresolved` (instead of `out of scope`) so operators can see
missing registry data rather than a wrong grant.

Zone grants are fully usable: `DA Visit`, `DA Task`, `DA Farmer Link`, `DA Farmer Reference`
and `DA Profile Change Request` carry a `zone` column stamped from the DA's registry
posting (or the farmer's reference) on insert, and the list hooks filter on it
(`permissions._officer_scope_clause`). A user holding both a Woreda and a Zone grant sees
the union of the two; a Region grant applies only when no narrower grant exists. Rows
created before the column existed are filled by the `backfill_zone` patch (Woreda → Zone
through `DA Administrative Area`, then the farmer reference, then the DA Registry); rows it
cannot resolve stay empty and remain denied to Zone-only grants.

Admin list endpoints clamp `limit` to 1..500 and `start` to >= 0 (`rbac_service.clamp_page`).

## Authentication

Login, refresh, logout and password reset come from the shared `oan_auth_service` app
(same as the Grievance service). Its JWT middleware protects two namespaces for us:

- `/api/v1/da-services/*` (REST routes declared with `@route` in `api/router.py`)
- `/api/method/da_services.*` (the RPC form of the same functions)

On each request the token's `sub` becomes the session user; our scope context is then
built from that user's assignments. `GET /api/v1/auth/me` (auth app) includes our profile
under `data.profiles.da_services`; `GET /api/v1/da-services/me` returns the full profile with the
DA record and the permitted menu.

Fayda sign-in and central OAN IAM are open decisions; the auth layer is isolated so
either can replace the interim login without touching the scope rule.

## Audit log

`DA Audit Event` is append-only (edits and deletes are refused in the controller, and no
role holds create/write/delete). `utils/audit.py` writes:

- `access.denied` for every refused `require_roles` / `require_da_access` call, with the
  reason (`missing role`, `out of scope`), the target DA and its Region/Zone/Woreda.
- `rbac.assignment.created` / `updated` / `revoked` from the doctype controller, with a
  field-level diff.

Denials happen inside a request that is about to fail, and the API layer rolls that
transaction back. Denial rows are therefore queued on `frappe.local` and written by the
`after_request` hook in their own commit (`audit.flush`). Allowed actions are written in
the same transaction as the change. Each row carries user, roles at the time, source
(`api` / `system`), request id and timestamp (HLD 10.3).

## Frappe permission hooks

`permissions.py` registers `permission_query_conditions` and `has_permission` for the
app's doctypes so list views, reports and `/api/resource` obey the same rule as the API.
Every new doctype must register both before it is exposed.

## Testing (FSD Appendix D)

Roles are verified by what they **cannot** do. `tests/test_rbac.py` and
`tests/test_api_auth.py` cover: Supervisor outside own Woreda, Executive outside own
Region, DA reading another DA, Communications Officer touching DA identity, expired
grant, Guest, token without any assignment. `tests/test_role_matrix.py` is the per-role negative matrix (one user per role; every
forbidden action in Appendix D that exists in the backend today, plus the positive case
so a test cannot pass by denying everyone). `tests/test_audit.py` covers denial and
assignment audit rows, immutability and the deferred write.

## Open points

- Zone codes: confirm the master list and whether Zone is mandatory.
- Role names vs the approved OAN RBAC model.
- Mapping of these roles to OpenG2P groups and Woreda scoping in registry views (with Yash).
- User provisioning: Registry approval → Services user + assignment.
