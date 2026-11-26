# Attendance punches integration (DA Services)

Backend contract for DA / Supervisor / Executive punch-in and punch-out, plus the
daily attendance list derived from those punches.
Screen map: [api-da-screens.md](api-da-screens.md) § Attendance punches.
Frontend guide: [attendance-punches-frontend.md](attendance-punches-frontend.md).

## Auth and access

1. Login via `POST /api/v1/da-services/sso/login`.
2. Send `Authorization: Bearer <access_token>` on every call. Native DocType — no staff CSRF.
3. Access is **Role DocPerm** on `DA Attendance Punch` (`create` + `read` for DA / Supervisor / Executive; admins have write/delete). Row visibility on lists uses **User Permissions** via `frappe.get_list`. Handlers do **not** use `require_roles` or ScopeContext. No custom permission hooks.

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

### `GET /api/v1/da-services/attendance-punches`

Daily attendance rows built only from punches the caller may see (Role DocPerm + User Permissions). One row per `(user, punched_date)`. Agents with no punches that day do **not** appear.

Query (map to `frappe.get_list` filters only):

| Param | Notes |
| --- | --- |
| `user` | **Required.** Frappe User Link whose attendance to list. Missing/blank → 400 `user is required.` |
| `from_date`, `to_date` | Inclusive range on `punched_date`. **Both required** together (use the same day for a single day); one alone → 400. Omit both → **site today**. |
| `da_id` | Optional narrower on `da_id` |
| `region`, `zone`, `woreda`, `kebele` | Geo Link filters; out-of-grant → empty page |
| `limit`, `start` | Page of **daily rows** (default 50 / 0; limit clamped 1..500) |

Pairing:

- `check_in` = earliest `Punch In` that day
- `check_out` = latest `Punch Out` that day
- `hours_logged` = check-out − check-in as `Nh Mm` when both exist; otherwise `null`
- `status` = `"Checked In"` when `check_in` is set
- `user_name` from `User.full_name`; `kebele` is the Link id (FE resolves the display name)

Example `data[]` item:

```json
{
  "user": "63c05efc…@iam.internal",
  "user_name": "Tadesse Alemu",
  "da_id": "DA-5167618350",
  "kebele": "ET01010101",
  "punched_date": "2026-11-17",
  "status": "Checked In",
  "check_in": "08:51:00",
  "check_out": "13:18:00",
  "hours_logged": "4h 27m"
}
```

`meta`: `{ "total", "limit", "start" }` where `total` is the length of the returned page.

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
| Out-of-grant list filter | Empty `data` page (not a service-layer 403) |

## Out of scope (v1)

- Team roster / “Not checked in” agents with zero punches
- Summary cards (checked-in vs not-checked-in counts)
- Punch-in/out alternation enforcement on POST
- Yesterday open punch auto-close
- Registry geo fill
- DB partition DDL (ops may partition on `punched_date` later)
