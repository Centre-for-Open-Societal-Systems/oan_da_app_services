# Visits integration (DA Services)

Backend contract for plan / list / detail / reschedule / cancel / start / submit / attachments
and the weekly activity report. Screen map: [api-da-screens.md](api-da-screens.md) §2.6–2.8.

## Auth and scope

1. Login via `POST /api/v1/da-services/sso/login` with `username`, `password`,
   `key`. Send `Authorization: Bearer <access_token>` on every visits call.
   Visits do not require `X-CSRF-Token` (native DocTypes, not staff registry).
2. `oan_auth_service` verifies the Bearer token and sets the session user.
3. **Access and row visibility** are Frappe DocType Role permissions + User
   Permissions via `frappe.get_list` / DocType `insert`. There are **no** custom
   DA Visit `permission_query_conditions` / `has_permission` hooks. Handlers and
   the visits service do **not** use `require_roles`, `ScopeContext`, or
   service-side permission/DA resolve helpers.

Optional list filters (`date` / `from`+`to`, `status`, `farmer_id`, `da_id`, `region`,
`zone`, `woreda`, `kebele`) only narrow the already-permitted set.

## `POST /visits` — plan (single or batch)

### Single (flat body)

Required from the frontend:

| Field | Notes |
| --- | --- |
| `user` | Frappe User Link for the assigned DA. **No** `ctx.user` / session fallback. `da_id` is `fetch_from: user.da_registry_id`. |
| `farmer_id` | Farmer id |
| `farmer_name` | Farmer display name snapshot from the FE (required DocType field; no registry lookup) |
| `scheduled_at` **or** `date` (+ optional `time`, default `09:00`) | Site-local datetime |

Optional: `visit_type`, `purpose`, `duration_min`, `priority`, `priority_reason`,
`location {lat,lng}`, `notify_farmer_sms`, `advisory_qa[]`, `client_op_id`, `da_id`
(legacy/envelope only — row `da_id` always comes from `user.da_registry_id`).
When the session user differs from plan `user`, `assigned_by` is the session user.

Response: one serialized visit (`physical_status: Planned`). Idempotent on
`client_op_id`.

### Batch (same route)

Body:

```json
{
  "user": "<da-user@example.com>",
  "da_id": null,
  "visits": [
    {
      "farmer_id": "FR-…",
      "farmer_name": "Abebe Kebede",
      "date": "2026-11-10",
      "time": "09:00",
      "visit_type": "Crop Survey",
      "purpose": "…",
      "client_op_id": "…"
    }
  ]
}
```

Rules:

- `visits` length **1–5**, unique non-empty `farmer_id`.
- Each item uses the same fields as single plan. Envelope `user` applies when an item
  omits `user`; still no session fallback.
- Server plans sequentially in one request. Partial success:
  `data = { created[], failed[{ status, error, code, request }], summary }`.
- `code` is `conflict` | `validation` | `error`. `failed[].request` echoes that item.
- HTTP **201** if any created; **400** if the envelope is invalid or zero created.
- There is no `/visits/batch` path.

Specialization conflict on open visits for the same farmer still blocks that item
(others may succeed).

## `GET /visits`

Query: `date` or `from`+`to`, `status`, `farmer_id`, `da_id`, `region`, `zone`,
`woreda`, `kebele`, `limit`, `start`. Meta: `{ total, limit, start }` (`total` is the
returned page length unless a cross-page total is added later).

## Other routes

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/visits/<id>` | Detail + `timeline[]` from visit timestamps |
| POST | `/visits/<id>/reschedule` | `{ scheduled_at \| date+time, reason }` |
| POST | `/visits/<id>/cancel` | `{ reason }` |
| POST | `/visits/<id>/start` | Check-in |
| POST | `/visits/<id>/submit` | Outcome |
| POST | `/visits/<id>/attachments` | Multipart evidence |
| GET | `/reports/activity` | Weekly report (`week`, optional `da_id`) |

## Related

- Frontend sequence: [visits-frontend.md](visits-frontend.md)
- Postman: `postman/Visits/OAN-DA-Visits.postman_collection.json`
