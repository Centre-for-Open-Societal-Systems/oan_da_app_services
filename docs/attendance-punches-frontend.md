# Attendance punches — frontend guide

Maps the punch-in / punch-out screen to DA Services APIs.
Backend contract: [attendance-punches-integration.md](attendance-punches-integration.md).

## Sequence

1. SSO login → Bearer token for the signed-in user (DA, Supervisor, or Executive).
2. On screen load: `GET /api/v1/da-services/attendance-punches/today`.
3. Choose button from `data`:
   - `null` → show **Punch In**
   - `action === "Punch In"` → show **Punch Out**
   - `action === "Punch Out"` → show **Punch In**
4. On tap: `POST /api/v1/da-services/attendance-punches` with device time + geo the role has.
5. Refresh from GET today (or use POST response) to update the button.

After midnight (site date), GET returns `null` even if yesterday ended on Punch In — show Punch In again.

## Field map

| UI | API | Notes |
| --- | --- | --- |
| Punch In / Punch Out button | `action` | `"Punch In"` or `"Punch Out"` |
| Device / local clock | `punched_at` | Required; site-local datetime string |
| Region | `region` | Required Link name (`DA Region`) |
| Zone | `zone` | Required Link name (`DA Zone`) |
| Woreda | `woreda` | Optional; Supervisor typically sends this |
| Kebele | `kebele` | Optional; DA typically sends this |
| — | `user`, `da_id` | Do not send; server stamps from token session |

## Role geo expectations

| Role | Typical body |
| --- | --- |
| Development Agent | region, zone, woreda, kebele |
| Supervisor | region, zone, woreda (omit kebele) |
| Executive | region, zone (omit woreda/kebele if not held) |

Always send **region** and **zone**. Use the signed-in user's assigned geo (from `/me` / kebele assignment), not an arbitrary master code — User Permissions will reject out-of-scope Links.

## Do not

- Do not rely on the backend to refuse a second Punch In without Punch Out.
- Do not send server-clock requests without `punched_at`.
- Do not invent geo codes; use Desk master names the app already loaded for that user.
