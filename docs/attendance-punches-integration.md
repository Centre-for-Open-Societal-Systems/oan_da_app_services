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
| `punched_date` | FE date (`YYYY-MM-DD`) | Yes |
| `punched_time` | FE time (`HH:MM:SS`) | Yes |
| `region` | FE Link → `DA Region` | No |
| `zone` | FE Link → `DA Zone` | No |
| `woreda` | FE Link → `DA Woreda` | No |
| `kebele` | FE Link → `DA Kebele` | No |
| `gps_lat` / `gps_lng` | FE `lat` / `lng` | No |

Date and time are separate columns so `punched_date` can be indexed and used for future table partitioning. Punch time is the GPS capture time (no separate accuracy / captured_at fields).

Autoname: `DA-ATP-{YYYY}-{#######}`. Frappe Link validation rejects unknown geo IDs. Geo Links honour User Permissions when a value is sent. No Python punch-sequence checks on create.

## Routes

### `GET /api/v1/da-services/attendance-punches/today`

Latest punch for the **session user** where `punched_date` equals the **site calendar today**.

Always returns an object:

```json
{
  "next_action": "Punch Out",
  "id": "DA-ATP-2026-0277061",
  "punched_date": "2026-11-18",
  "punched_time": "14:31:52"
}
```

- `next_action`: `"Punch In"` when none today or last action was Punch Out; `"Punch Out"` when last was Punch In.
- Ordered by `punched_date desc, punched_time desc, creation desc`, limit 1.
- When none: `id` / `punched_date` / `punched_time` are `null`.

### `POST /api/v1/da-services/attendance-punches`

Creates one punch. Returns `201` with message only (no punch payload).

Body:

```json
{
  "action": "Punch In",
  "punched_date": "2026-11-18",
  "punched_time": "14:31:52",
  "region": "ET01",
  "zone": "ET0101",
  "woreda": "ET010101",
  "kebele": "ET01010101",
  "lat": 9.032,
  "lng": 38.7469
}
```

- `user` / `da_id` are not accepted from the body for identity; they come from the session.
- All geo Links and `lat` / `lng` are optional.
- Multiple punches the same day are allowed. Alternation is hinted via GET `next_action` only.

## Errors

| Case | Behaviour |
| --- | --- |
| Role without DocPerm | Frappe PermissionError on `get_list` / `insert` |
| No `User.da_registry_id` | Frappe mandatory / fetch_from failure on `da_id` |
| Missing `action` / `punched_date` / `punched_time` or bad Link | Frappe DocType meta validation |

## Out of scope (v1)

- List / history endpoint
- Punch-in/out alternation enforcement on POST
- Yesterday open punch auto-close
- Registry geo fill
- DB partition DDL (ops may partition on `punched_date` later)
