# Attendance punches — frontend guide

Maps the punch-in / punch-out screen and the team attendance list to DA Services APIs.
Backend contract: [attendance-punches-integration.md](attendance-punches-integration.md).

## Sequence (punch button)

1. SSO login → Bearer token for the signed-in user (DA, Supervisor, or Executive).
2. On screen load: `GET /api/v1/da-services/attendance-punches/today`.
3. Show the button from `data.next_action` (`"Punch In"` or `"Punch Out"`).
4. On tap: `POST /api/v1/da-services/attendance-punches` with device date/time, optional geo, optional lat/lng.
5. Refresh from GET today to update the button (`next_action` flips after each successful punch).

After midnight (site date), GET returns `next_action: "Punch In"` with null id/date/time even if yesterday ended on Punch In.

## Sequence (attendance list / Team attendance)

1. SSO login → Bearer.
2. Load table: `GET /api/v1/da-services/attendance-punches?user=<User>&from_date=&to_date=` (both for a day or range; omit both → site today). **`user` is required** (DA self or the DA the Supervisor is viewing).
3. Render one row per item: `user_name`, `kebele` (resolve display name in the FE), `status`, `check_in`, `check_out`, `hours_logged`.
4. Optional filters (get_list only): `da_id`, `region`, `zone`, `woreda`, `kebele`. Out-of-scope filters return an empty list.
5. Only days with at least one punch appear — do not expect “Not checked in” rows from this API.

## Field map (punch)

| UI | API | Notes |
| --- | --- | --- |
| Punch In / Punch Out button | `action` (POST) / `next_action` (GET) | `"Punch In"` or `"Punch Out"` |
| Device date | `punched_date` | Required `YYYY-MM-DD` |
| Device time | `punched_time` | Required `HH:MM:SS` (also the GPS capture time) |
| GPS | `lat`, `lng` | Optional; stored as `gps_lat` / `gps_lng` |
| Region | `region` | Optional Link name (`DA Region`) |
| Zone | `zone` | Optional Link name (`DA Zone`) |
| Woreda | `woreda` | Optional Link name (`DA Woreda`) |
| Kebele | `kebele` | Optional Link name (`DA Kebele`) |
| — | `user`, `da_id` | Do not send; server stamps from token session |

## Field map (list)

| UI (Figma) | API field | Notes |
| --- | --- | --- |
| Agent | `user_name` | From `User.full_name` (not the IAM user id) |
| Kebele | `kebele` | Link id; FE maps to display name |
| Status | `status` | `"Checked In"` when a Punch In exists that day |
| Check-in | `check_in` | Earliest Punch In `HH:MM:SS` |
| Check-out | `check_out` | Latest Punch Out; `null` if still open |
| Hours | `hours_logged` | `checkout − checkin` as `Nh Mm`; `null` until check-out |

## GET today shape

```json
{
  "next_action": "Punch Out",
  "id": "DA-ATP-2026-0277061",
  "punched_date": "2026-11-18",
  "punched_time": "14:31:52"
}
```

When none today: `next_action` is `"Punch In"` and `id` / `punched_date` / `punched_time` are `null`.

## GET list shape

```json
{
  "user": "…@iam.internal",
  "user_name": "Tadesse Alemu",
  "da_id": "DA-5167618350",
  "kebele": "ET01010101",
  "punched_date": "2026-11-17",
  "status": "Checked In",
  "check_in": "08:51:00",
  "check_out": null,
  "hours_logged": null
}
```

`meta`: `{ total, limit, start }`.

## POST

Success is message-only (`"Attendance punch recorded"`); no punch object in `data`. Re-fetch GET today for button state.

## Role geo expectations

Send the signed-in user's assigned geo when available (from `/me` / kebele assignment). User Permissions reject out-of-scope Links on create. List scope is the same Frappe User Permissions — do not hard-filter “my punches only” in the client beyond optional query args.

## Do not

- Do not rely on the backend to refuse a second Punch In without Punch Out (POST does not enforce alternation).
- Do not send `punched_at`, `accuracy_m`, or `captured_at`.
- Do not invent geo codes; use Desk master names the app already loaded for that user.
- Do not expect roster / not-checked-in rows from `GET /attendance-punches`.
