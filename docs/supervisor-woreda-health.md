# Supervisor Programme Health (DA-373)

Short summary of the Supervisor main dashboard backend.

## Approach (updated)

One dashboard summary API for all roles. Identity and scope come from the access token
(`ScopeContext`). Query params carry date range and Supervisor filters.

| Caller | `GET /dashboard/summary` |
|---|---|
| Development Agent | `view: "agent"` — visits / tasks / sync |
| Supervisor (no `da_id`) | `view: "woreda"` — assigned woreda KPIs + kebeles |
| Admin (no `da_id`) | `view: "woreda"` — pass `woreda=` when unrestricted |
| Any officer + `da_id=` | `view: "agent"` for that DA (scope-checked) |

The separate `/dashboard/woreda-health` route was removed. Use `/dashboard/summary` only.

## What shipped

| Piece | Location |
|---|---|
| Service | `da_services/services/dashboard.py` → `summary(...)` (internal helper `woreda_health`) |
| Route | `GET /api/v1/da-services/dashboard/summary` only |
| Tests | `da_services/tests/test_dashboard.py` |
| Screen contract | `docs/api-da-screens.md` §2.1 / §2.1b |
| Postman | `postman/Dashboard/` |

## Scope rules

- One `woreda_scope` → that woreda is used automatically.
- Multiple woreda grants → `woreda=` is required and must be inside the grant.
- Requesting another woreda → `PermissionError` (403).
- Kebele filter must belong to that woreda’s administrative children → otherwise 400.
- Response never includes kebeles or farmer totals from another woreda.

## Supervisor response shape (`view: "woreda"`)

```json
{
  "view": "woreda",
  "woreda": {"code": "ET04-W01", "name": "Bako Tibe"},
  "updated_at": "...",
  "range": {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"},
  "stats": {
    "farmers": 0,
    "active_agents": 0,
    "data_quality": 0,
    "open_grievances": 0
  },
  "kebeles": [
    {
      "kebele": "Dambi Gobu",
      "kebele_code": "ET04-W01-K03",
      "farmers": 0,
      "agents": 0,
      "visit_pct": 0,
      "last_sync": null,
      "status": "At risk"
    }
  ],
  "total": 0,
  "page": {"limit": 50, "start": 0}
}
```

Query params: `from`, `to`, `woreda`, `kebele`, `status`, `agents`, `q`, `limit`, `start`.

KPI **stats** are always full-woreda. Filters apply only to the `kebeles` table.

## Metric formulas

| Metric | Formula |
|---|---|
| Farmers | Count of `DA Farmer Reference` rows with that `woreda` |
| Active agents | Agents in woreda with `active_status=Active` |
| Visit % (kebele) | In date range: `completed / planned * 100`; 0 if planned=0 |
| Status | `>=80` On track · `60-79` Watch · `<60` At risk |
| Avg data quality (interim) | Round(mean of all kebele `visit_pct`) |
| Last sync | Latest `modified` on `DA Visit` for that kebele; `null` if none |
| Open grievances | Open / Submitted / Reopened / In Progress / More Info Needed (0 if client unavailable) |

## Frontend follow-up (DA-374)

Wire `SupervisorDashboard.tsx` to `GET /dashboard/summary` (Supervisor token, no `da_id`) and stop using `SUPERVISOR_WOREDA` mock.

Full FE guide (payloads, UI map, filters, errors, curls):
[supervisor-woreda-health-frontend.md](supervisor-woreda-health-frontend.md)

## Not committed / pushed

Changes are local for review until you approve a commit.
