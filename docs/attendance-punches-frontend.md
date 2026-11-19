# Attendance punches — frontend guide

Maps the punch-in / punch-out screen to DA Services APIs.
Backend contract: [attendance-punches-integration.md](attendance-punches-integration.md).

## Sequence

1. SSO login → Bearer token for the signed-in user (DA, Supervisor, or Executive).
2. On screen load: `GET /api/v1/da-services/attendance-punches/today`.
3. Show the button from `data.next_action` (`"Punch In"` or `"Punch Out"`).
4. On tap: `POST /api/v1/da-services/attendance-punches` with device date/time, optional geo, optional lat/lng.
5. Refresh from GET today to update the button (`next_action` flips after each successful punch).

After midnight (site date), GET returns `next_action: "Punch In"` with null id/date/time even if yesterday ended on Punch In.

## Field map

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

## POST

Success is message-only (`"Attendance punch recorded"`); no punch object in `data`. Re-fetch GET today for button state.

## Role geo expectations

Send the signed-in user's assigned geo when available (from `/me` / kebele assignment). User Permissions reject out-of-scope Links. Omit any level the role does not hold.

## Do not

- Do not rely on the backend to refuse a second Punch In without Punch Out (POST does not enforce alternation).
- Do not send `punched_at`, `accuracy_m`, or `captured_at`.
- Do not invent geo codes; use Desk master names the app already loaded for that user.
