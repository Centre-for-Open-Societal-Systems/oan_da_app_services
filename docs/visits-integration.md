# Visits integration (DA-383)

Native DA Services DocType: **DA Visit**. No staff registry CSRF pair is required for these routes.

Prefix: `/api/v1/da-services`. Auth: `Authorization: Bearer <access_token>` from SSO Login.

## Auth and role

Sign in with SSO (same as Crop Sown):

```http
POST /api/v1/da-services/sso/login
Content-Type: application/json

{"username":"<usr>","password":"<pwd>","key":"<sso_key>"}
```

Response `data` includes `access_token`, `csrf_token`, and `user` (roles / scope). Role and geography come from that **token / session**, never from a client `role` flag.

1. SSO Login issues the Keycloak / IAM access token (+ CSRF).
2. `oan_auth_service` verifies the Bearer token and sets the session user.
3. `require_roles` → [`get_scope_context`](../da_services/utils/permissions.py) loads Frappe roles plus active `DA RBAC Assignment`.
4. [`list_visits`](../da_services/services/visits.py) branches on that `ScopeContext`:

| Caller | Default list |
| --- | --- |
| Development Agent | Own DA-ID visits; without `da_id`, also peers in assigned Kebeles |
| Supervisor | Visits for DAs mapped to the Supervisor’s Woreda in DB (`DA RBAC Assignment` / live `DA Kebele Assignment`) |
| Executive | Same pattern for Region grants |
| Admin | Unrestricted |

Supervisor/Executive scope is **Frappe DB DA↔Woreda mapping** only (live officer grant → RBAC / live kebele; not visit.woreda strings, not the registry mock). Live kebele mapping includes **Proposed, Validated, Flagged, and Effective**. Out-of-scope `woreda` / `kebele` / `da_id` → **403**. Farmer profile is not part of this list gate.

## `GET /visits`

Query params (all optional filters inside the caller’s scope):

| Param | Behavior |
| --- | --- |
| (no `status`) | Full history in scope (Planned, Completed, Missed, …) |
| `status=Planned` | UI chip → open states: Planned, Confirmed, In progress |
| `status=Completed` | `physical_status = Physical Visit Complete` |
| other `status` | Exact `physical_status` |
| `date` | Single calendar day on `scheduled_at` |
| `from` + `to` | Inclusive date range on `scheduled_at` (`date` wins if both present) |
| (no date) | Open date range |
| `kebele` / `woreda` / `zone` / `region` | Narrow; must be inside grant |
| `da_id` | One agent; Supervisor/Executive: must be in the Frappe DA↔Woreda mapping for the grant |
| `farmer_id` | Exact farmer id |
| `limit` / `start` | Page size (default 50, max 500) and offset |

Response: `data` = serialized visit rows; `meta` = `{ total, limit, start }`.

Row fields include `name`, `da_id`, `farmer_id`, `physical_status`, `ui_status`, `planner_status`, `purpose`, `scheduled_at`, `region`, `woreda`, `kebele`, `assigned_by`, and related plan/outcome columns from `serialize`.

## Related write routes

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/visits` | Plan; Supervisor must pass `da_id` in own Woreda (`assigned_by` recorded) |
| GET | `/visits/<id>` | Detail + `timeline[]` |
| POST | `/visits/<id>/reschedule` | New Planned visit; old → Rescheduled |
| POST | `/visits/<id>/cancel` | Cancel |
| POST | `/visits/<id>/start` | Check-in → In progress |
| POST | `/visits/<id>/submit` | Outcome → Physical Visit Complete |

## Screen and frontend

- Screen map: [api-da-screens.md](api-da-screens.md) §2.7
- Frontend sequence: [visits-frontend.md](visits-frontend.md)
- Postman: `postman/Visits/OAN-DA-Visits.postman_collection.json` (+ env). Collection schema is Postman **v2.1** (same as Crop Sown / Surveys / etc.). JSON parses cleanly; `postman collection lint` only scans **v3** collections (`filesScanned: 0` on v2.1) — do not treat that as a Visits-only gap.
