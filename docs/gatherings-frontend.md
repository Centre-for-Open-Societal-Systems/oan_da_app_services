# Gatherings frontend mapping

Backend contract: [gatherings-integration.md](gatherings-integration.md).
Screen map: [api-da-screens.md](api-da-screens.md) §2.Gatherings.

The FE portal is expected under `DA-registry-frontend`. This doc maps UI → API.
Do not hardcode fixture farmers or invent kebele codes in the page.

## Sequence

1. SSO login → Bearer token.
2. Farmer picker: `GET /api/v1/da-services/farmers` (kebele-scoped list already used
   elsewhere). Select **1–20** farmers and keep each row's **name** and **phone**.
3. DA enters **Venue** (required), optional landmark + map pin. Do **not** collect
   free-text address or region/zone/woreda/kebele — server stamps those from the
   DA's Effective Kebele Assignment as `Venue, Kebele, Woreda, Zone, Region`.
4. `POST /api/v1/da-services/gatherings` with required `user` and `farmers[]`
   (`farmer_id`, `farmer_name`, `phone` each required). No session fallback.
5. List / detail / cancel as needed.

## UI → API field map

| UI field | API field | Notes |
| --- | --- | --- |
| Assigned DA | `user` | **Required.** Frappe User Link. |
| Title | `title` | Required |
| Purpose / notes | `purpose` | Optional |
| Date + time | `scheduled_at` or `date` + `time` | Site-local |
| Venue | `location.name` | **Required.** Editable. |
| Landmark | `location.landmark` | Optional; appended to SMS when set |
| Map pin | `location.lat`, `location.lng` | Optional; **not** sent in SMS |
| Selected farmers | `farmers[]` | 1–20; each needs `farmer_id`, `farmer_name`, `phone` |
| Notify by SMS | `notify_farmers_sms` | Default true |
| Idempotency | `client_op_id` | Optional UUID per create |
| Address / Region / Zone / Woreda / Kebele | — | **Do not send.** Read-only on response after stamp. |

## What the page must stop hardcoding

- Farmer ids / names from fixtures — use `GET /farmers` and send name + phone from that list.
- Collecting free-text address or editable geo — only venue + landmark + lat/lng.
- Omitting `user` / `farmer_name` / `phone` — server fails closed.

## Response fields the UI can show

| API | UI use |
| --- | --- |
| `name` | Gathering id |
| `status` | Planned / Cancelled chip |
| `scheduled_at` | Calendar / detail |
| `location` | Venue card |
| `attendees[].farmer_id/name/phone/sms_status` | Invitee list + SMS state |
| `sms_logs[]` | Ops / debug (optional) |

## Open gaps

- Portal route for gatherings is not confirmed in the FE repo yet.
- Live SMS delivery is not available; show Queued from `sms_status`.
- Attendance / check-in is out of v1.
