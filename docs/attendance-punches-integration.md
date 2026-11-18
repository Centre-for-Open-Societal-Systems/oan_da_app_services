# Attendance punches integration (DA Services)

Backend contract for DA / Supervisor / Executive punch-in and punch-out.
Screen map: [api-da-screens.md](api-da-screens.md) § Attendance punches.
Frontend guide: [attendance-punches-frontend.md](attendance-punches-frontend.md).

## Auth and access

1. Login via `POST /api/v1/da-services/sso/login`.
2. Send `Authorization: Bearer <access_token>` on every call. Native DocType — no staff CSRF.
3. Access is **Role DocPerm** on `DA Attendance Punch` (`create` + `read` for DA / Supervisor / Executive; admins have write/delete). Handlers do **not** use `require_roles` or ScopeContext. No custom permission hooks.

## DocType

`DA Attendance Punch` — one document per event.

| Field | Source | Required |
| --- | --- | --- |
| `user` | Session user | Yes (API stamps) |
| `da_id` | `User.da_registry_id` / `fetch_from` | Yes |
| `action` | FE: `Punch In` \| `Punch Out` | Yes |
| `punched_at` | FE datetime | Yes |
| `region` | FE Link → `DA Region` | Yes |
| `zone` | FE Link → `DA Zone` | Yes |
| `woreda` | FE Link → `DA Woreda` | No |
| `kebele` | FE Link → `DA Kebele` | No |

Autoname: `DA-ATP-{YYYY}-{#######}`. Frappe Link validation rejects unknown geo IDs. Geo Links honour User Permissions (RBAC / kebele sync): FE must send the caller's assigned region/zone/woreda/kebele. No Python punch-sequence checks and no parent/child geo checks beyond Link/`reqd` + UP.

## Routes

### `GET /api/v1/da-services/attendance-punches/today`

Latest punch for the **session user** where `punched_at` falls on the **site calendar today**.

- `data`: punch object, or `null` when none.
- Ordered by `punched_at desc`, limit 1.

### `POST /api/v1/da-services/attendance-punches`

Creates one punch. Returns `201`.

Body:

```json
{
  "action": "Punch In",
  "punched_at": "2026-11-18 09:15:00",
  "region": "ET04",
  "zone": "ET04-Z01",
  "woreda": "ET04-W01",
  "kebele": "ET04-W01-K03"
}
```

- `user` / `da_id` are not accepted from the body for identity; they come from the session.
- Omit `woreda` / `kebele` (or send blank) when the role has no lower level.
- Multiple punches the same day are allowed. Alternation is FE-owned.

## Errors

| Case | Behaviour |
| --- | --- |
| Role without DocPerm | Frappe PermissionError on `get_list` / `insert` |
| No `User.da_registry_id` | Frappe mandatory / fetch_from failure on `da_id` |
| Missing `action` / `punched_at` / `region` / `zone` or bad Link | Frappe DocType meta validation |

## Out of scope (v1)

- List / history endpoint
- Punch-in/out alternation enforcement
- Yesterday open punch auto-close
- Registry geo fill
