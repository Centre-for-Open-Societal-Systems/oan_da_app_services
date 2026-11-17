# Gatherings integration (DA Services)

Backend contract for plan / list / detail / cancel of farmer gatherings (max 20
farmers; FE picker is kebele-scoped). Screen map: [api-da-screens.md](api-da-screens.md)
§2.Gatherings. Frontend field map: [gatherings-frontend.md](gatherings-frontend.md).

## Auth and scope

1. Login via `POST /api/v1/da-services/sso/login` with `username`, `password`,
   `key`. Send `Authorization: Bearer <access_token>` on every gatherings call.
   Gatherings do not require `X-CSRF-Token` (native DocTypes, not staff registry).
2. `oan_auth_service` verifies the Bearer token and sets the session user.
3. **Access and row visibility** are Frappe DocType Role permissions + User
   Permissions via `frappe.get_list` / DocType `insert` / `get_doc` / `save`.
   There are **no** custom DA Gathering `permission_query_conditions` /
   `has_permission` hooks. Handlers and the gatherings service do **not** use
   `require_roles`, `ScopeContext`, or service-side permission/DA resolve helpers.

Optional list filters (`date` / `from`+`to`, `status`, `da_id`, `region`, `zone`,
`woreda`, `kebele`) only narrow the already-permitted set.

## DocTypes

| DocType | Role |
| --- | --- |
| `DA Gathering` | Parent: plan identity, schedule, address-first location, status |
| `DA Gathering Attendee` | Child: farmer_id + name/phone snapshot + sms_status |
| `DA Gathering SMS Log` | Child: per-farmer delivery attempt (Queued / Skipped / …) |

Geo Links (`region` / `zone` / `woreda` / `kebele`) point at Desk masters. They do
**not** use `ignore_user_permissions`. No registry `get_da` geo fill.

Status machine (v1): `Planned` → `Cancelled` only. New rows must start as `Planned`.

## `POST /gatherings` — plan

Required from the frontend:

| Field | Notes |
| --- | --- |
| `user` | Frappe User Link for the assigned DA. **No** session fallback. Row `da_id` is `fetch_from: user.da_registry_id`. |
| `title` | Gathering title |
| `scheduled_at` **or** `date` (+ optional `time`, default `09:00`) | Site-local datetime |
| `location.name` | **Venue** (editable). Required. |
| `farmers` | List of 1–20 objects: `farmer_id`, `farmer_name`, `phone` — **all required** from FE |

Optional client location: `location.landmark`, `location.lat` / `location.lng` (map only).

**Not accepted from the client (read-only, stamped server-side):** `location.address`,
`region`, `zone`, `woreda`, `kebele`. These come from the DA's **Effective**
`DA Kebele Assignment` via `ancestors_of_kebele`. Composed `address` =
`Venue, Kebele, Woreda, Zone, Region` (display names when available).

Also optional: `purpose`, `notify_farmers_sms` (default true), `client_op_id`,
`da_id` (legacy/envelope only).

Product rules (service / DocType):

- User must have `da_registry_id` and an Effective Kebele Assignment.
- Invitee snapshots come from the FE (`farmer_id` / `farmer_name` / `phone`). No
  DA Farmer Reference lookup and no server-side farmer-kebele re-check (FE picker
  is already kebele-scoped).
- Idempotent on `client_op_id`.
- When session user ≠ plan `user` (and not Guest), `assigned_by` = session user.

### SMS (v1)

When `notify_farmers_sms` is true, the service builds a human-readable message
(title, date, time, composed address, optional landmark — **no coordinates**),
writes one SMS log row per attendee (`Queued`), and enqueues `deliver_gathering_sms`
after commit. The gateway is **not wired**; the job is a no-op until provider
credentials exist. The HTTP request never blocks on send.

### Example body

```json
{
  "user": "da.user@example.com",
  "title": "Fertilizer timing training",
  "purpose": "Seasonal advisory",
  "scheduled_at": "2026-11-20 09:00:00",
  "location": {
    "name": "Kebele meeting hall",
    "landmark": "Beside the church",
    "lat": 9.032,
    "lng": 38.7469
  },
  "farmers": [
    {"farmer_id": "FR-…", "farmer_name": "Abebe Kebede", "phone": "911234567"},
    {"farmer_id": "FR-…", "farmer_name": "Tigist Hailu", "phone": "911234568"}
  ],
  "notify_farmers_sms": true,
  "client_op_id": "optional-uuid"
}
```

Response: one serialized gathering (`status: Planned`) including `attendees`,
`sms_logs`, `farmer_ids` (derived), and nested `location`.

## `GET /gatherings`

Query: `date` or `from`+`to`, `status`, `da_id`, `region`, `zone`, `woreda`,
`kebele`, `limit`, `start`. Meta: `{ total, limit, start }` (`total` is the
returned page length).

## `GET /gatherings/<id>`

Plain `frappe.get_doc` → serialize (attendees + sms_logs + location).

## `POST /gatherings/<id>/cancel`

Body: `{ "reason": "…" }`. Transitions `Planned` → `Cancelled`. Re-cancel is
rejected by the status machine. v1 does not re-SMS on cancel.

## Errors

| Case | Shape |
| --- | --- |
| Missing `user` / title / schedule / location / farmers | 400 ValidationError |
| More than 20 farmers / duplicates | 400 ValidationError |
| Farmer outside DA kebele | 400 ValidationError |
| User has no `da_registry_id` | 400 ValidationError |
| Out-of-scope get/list | Frappe PermissionError / empty `get_list` |
| Duplicate `client_op_id` on a different row | DuplicateEntryError |

## Out of scope (v1)

- Live SMS provider / site_config SMS keys
- Attendance, start/complete, GPS check-in
- Reschedule + re-notify
- Frontend repository changes
