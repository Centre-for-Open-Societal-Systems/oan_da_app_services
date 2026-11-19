# Visits frontend mapping

Wire the Supervisor `/visits` screen (and DA list from the dashboard tile) and the plan
modal / My Farmers batch plan to DA Services.

Backend contract: [visits-integration.md](visits-integration.md). Screen map:
[api-da-screens.md](api-da-screens.md) §2.6–2.7.

## Auth

Sign in with `POST /api/v1/da-services/sso/login` (`username`, `password`, `key`). Use the
returned Bearer `access_token` on visit routes (no CSRF header needed for visits). Role
and geography come from the session (User Permissions + DocType hooks). Do **not** send a
client role flag or invent Woreda/DA mapping in the FE beyond optional equality filters.

## Plan visit (single)

`POST /api/v1/da-services/visits` with flat body. Always send:

- `user` — Frappe User of the DA who owns the visit (usually the signed-in DA; Supervisor
  sends the target DA's User). Do not omit and expect the backend to use the session user.
- `farmer_id`
- `farmer_name` — display name from the selected farmer (required FE snapshot; no registry lookup)
- `scheduled_at` **or** `date` + `time`

Map UI fields → API as in §2.6 of the screen contract. Store `name` from the response as
`visit_id` for start / submit / reschedule.

## Plan visits (batch, My Farmers grid)

One `POST /visits` with:

```json
{ "user": "<da user>", "visits": [ /* 1–5 items, unique farmer_id, each with farmer_name */ ] }
```

No `/visits/batch`. Render `created` and `failed` separately; for each failed row show
`error` and let the DA edit using `request` (the echoed item) then resubmit only the
failures. Treat HTTP 400 with `summary.created === 0` as "nothing planned".

## List visits

`GET /api/v1/da-services/visits` with optional `from`/`to`, `status`, `farmer_id`,
`da_id`, `region`, `zone`, `woreda`, `kebele`, `limit`, `start`.

Scope is already applied server-side. Optional filters only narrow. Empty list means no
permitted rows matched the filters — not a signal to invent a second scope call.

## Gaps

- FE batch UI on My Farmers is pending.
- Stats tiles may still be mock; prefer counts derived from the list response until a
  dedicated stats route exists.
