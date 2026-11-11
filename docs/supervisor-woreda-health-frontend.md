# Supervisor Programme Health — frontend integration (DA-373 / DA-374)

Wire the Supervisor dashboard (`Adea Woreda - Programme Health` style screen) to DA Services.
Do **not** call a separate `/dashboard/woreda-health` route — it was removed. Use the same summary
API as the DA home; the **access token role** selects the payload shape.

Backend contract summary: [supervisor-woreda-health.md](supervisor-woreda-health.md)  
Screen map: [api-da-screens.md](api-da-screens.md) §2.1b  
Postman: `postman/Dashboard/OAN-DA-Dashboard.postman_collection.json`

## Setup

| | |
|---|---|
| Base URL (local) | `http://127.0.0.1:8200` |
| Route prefix | `/api/v1/da-services` |
| Auth | `Authorization: Bearer <access_token>` |
| Accept | `application/json` |

Login (auth service, not under `da-services`):

```http
POST /api/v1/auth/login
Content-Type: application/json

{"usr":"sup.demo@da.local","pwd":"Demo#2026"}
```

Use `data.access_token` on every dashboard call. Roles come from the token / session — **never** trust a client-sent “I am supervisor” flag.

## Which view you get

Call **one** URL for all roles:

```http
GET /api/v1/da-services/dashboard/summary?from=YYYY-MM-DD&to=YYYY-MM-DD
```

| Caller | Query | `data.view` | UI |
|---|---|---|---|
| Supervisor / Admin, **no** `da_id` | `from`, `to`, optional filters | `"woreda"` | Programme Health (this guide) |
| Development Agent | `from`, `to` (`da_id` ignored) | `"agent"` | Visits / tasks / sync tiles |
| Officer + `da_id` | `from`, `to`, `da_id` | `"agent"` | That DA’s agent tiles (scope-checked) |

Branch the page on `data.view`. If a DA somehow hits this screen with a DA token, you will get `view: "agent"` even if they pass `woreda=` — query params cannot unlock woreda KPIs.

## Sequence (Supervisor)

```mermaid
sequenceDiagram
  actor Sup as Supervisor
  participant Page as Supervisor Dashboard
  participant DA as DA Services

  Sup->>Page: Open /dashboard
  Page->>DA: POST /api/v1/auth/login (or use SSO token)
  DA-->>Page: access_token, roles includes Supervisor
  Page->>DA: GET /dashboard/summary?from&to
  DA-->>Page: view=woreda, stats, kebeles[]
  Page->>Page: Render heading, KPI cards, table
  Sup->>Page: Search / Kebele / Status / Agents filter
  Page->>DA: GET /dashboard/summary?from&to&q&kebele&status&agents
  DA-->>Page: Same stats; filtered kebeles + total
```

## Success envelope

Every success looks like:

```json
{
  "status": "success",
  "message": "Success",
  "data": { },
  "meta": { "api_version": "v1" },
  "request_id": "..."
}
```

Read the screen model from **`data`** only.

## Supervisor payload (`view: "woreda"`)

### Request

```http
GET /api/v1/da-services/dashboard/summary?from=2026-10-13&to=2026-11-11
Authorization: Bearer <supervisor_access_token>
Accept: application/json
```

| Query | Required | Notes |
|---|---|---|
| `from` | Recommended | `YYYY-MM-DD`. Default ≈ last 30 days ending today if omitted |
| `to` | Recommended | `YYYY-MM-DD` |
| `woreda` | Only if multi-woreda | Administrative **code** (e.g. `ET04-W01`). Single-woreda supervisors omit this |
| `kebele` | No | Kebele **code** (e.g. `ET04-W01-K03`), not display name |
| `status` | No | Exact: `On track` \| `Watch` \| `At risk` |
| `agents` | No | Exact integer agent count on the row |
| `q` | No | Free-text search over kebele name/code, farmers, agents, visit %, status, last_sync |
| `limit` | No | Page size (default 50, max 500) |
| `start` | No | Offset (default 0) |

**Important:** `kebele` / `status` / `agents` / `q` filter the **table only**. KPI `stats` stay full-woreda.

### Example response

```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "view": "woreda",
    "woreda": {
      "code": "ET04-W01",
      "name": "Bako Tibe"
    },
    "updated_at": "2026-11-11T14:32:49.291882",
    "range": {
      "from": "2026-10-13",
      "to": "2026-11-11"
    },
    "stats": {
      "farmers": 12490,
      "active_agents": 22,
      "data_quality": 84,
      "open_grievances": 22
    },
    "kebeles": [
      {
        "kebele": "Dhankaka",
        "kebele_code": "ET04-W01-K01",
        "farmers": 1240,
        "agents": 2,
        "visit_pct": 89,
        "last_sync": "2026-09-15T14:30:00",
        "status": "On track"
      },
      {
        "kebele": "Godino",
        "kebele_code": "ET04-W01-K02",
        "farmers": 1110,
        "agents": 1,
        "visit_pct": 51,
        "last_sync": "2026-09-15T14:30:00",
        "status": "At risk"
      }
    ],
    "total": 7,
    "page": {
      "limit": 50,
      "start": 0
    }
  },
  "meta": { "api_version": "v1" },
  "request_id": "..."
}
```

Numbers above are illustrative; local demo data will differ.

## UI → API field map

Matches the Programme Health screen (heading, four KPI cards, kebele breakdown table).

| UI | API field | Notes |
|---|---|---|
| Title `{woreda} - Programme Health` | `data.woreda.name` | Fall back to `data.woreda.code` if name empty |
| Live / updated banner | `data.updated_at` | ISO datetime from server |
| Farmer registered | `data.stats.farmers` | Full woreda |
| Active agents | `data.stats.active_agents` | Full woreda |
| Avg. data quality | `data.stats.data_quality` | 0–100 interim (= mean kebele `visit_pct`) |
| Open grievances | `data.stats.open_grievances` | Full woreda |
| Table row S.No. | client index | Use `page.start + index + 1` |
| KEBELE | `kebeles[].kebele` | Display name |
| (filter / advanced) | `kebele=` | Send `kebeles[].kebele_code` |
| Farmers | `kebeles[].farmers` | |
| Agents | `kebeles[].agents` | Advanced filter: `agents=` exact count |
| Visit % | `kebeles[].visit_pct` | 0–100; drive progress bar colour from this or `status` |
| Last sync | `kebeles[].last_sync` | ISO string or `null` → show “—” |
| STATUS chip | `kebeles[].status` | Server already classifies; do not recompute unless offline |
| Search box | `q=` | Debounce ~300–400 ms |
| Status column funnel | `status=` | `On track` / `Watch` / `At risk` (URL-encode spaces) |
| Advanced Filters | `kebele`, `status`, `agents` | Same params |
| Date range control | `from`, `to` | If UI is still static “Last 30 days”, still send matching `from`/`to` |
| Table paging | `limit`, `start` + `total` | |

### Status colours (server values)

| `status` | Meaning (server rule on `visit_pct`) |
|---|---|
| `On track` | ≥ 80 |
| `Watch` | 60–79 |
| `At risk` | &lt; 60 |

Prefer rendering the chip from `status`. Recomputing from `visit_pct` must use the same thresholds.

## Filter examples

```http
# Search
GET .../dashboard/summary?from=2026-10-13&to=2026-11-11&q=Dhankaka

# Kebele column / advanced (CODE, not name)
GET .../dashboard/summary?from=2026-10-13&to=2026-11-11&kebele=ET04-W01-K03

# Status
GET .../dashboard/summary?from=2026-10-13&to=2026-11-11&status=At%20risk

# Agents exact count
GET .../dashboard/summary?from=2026-10-13&to=2026-11-11&agents=2

# Combined
GET .../dashboard/summary?from=2026-10-13&to=2026-11-11&kebele=ET04-W01-K03&status=At%20risk&q=Dambi&agents=1
```

Multi-woreda supervisor:

```http
GET .../dashboard/summary?from=2026-10-13&to=2026-11-11&woreda=ET04-W01
```

## What the page must stop hardcoding

| Stop using | Use instead |
|---|---|
| `SUPERVISOR_WOREDA` / mock kebele rows | `GET /dashboard/summary` → `data` |
| Hardcoded woreda title (e.g. Adea) | `data.woreda.name` |
| Mock KPI numbers | `data.stats.*` |
| Client-only filter over a full mock array (as the only source of truth) | Server query params; keep client filter only as optimistic UX if needed |
| Passing `da_id` on Supervisor Programme Health | Omit `da_id` or you get `view: "agent"` |
| Calling `/dashboard/woreda-health` | Removed → 404 |

Likely FE files (portal): `features/dashboard/supervisor.ts`, `SupervisorDashboard.tsx` (or equivalent under DA-registry-frontend). Replace mock imports with this API client.

## Errors

| HTTP | When | FE action |
|---|---|---|
| 400 | Bad `status`, bad `agents`, kebele not in woreda, `from` after `to`, multi-woreda missing `woreda` | Show `message` / `details` |
| 401 | Missing/expired token | Refresh once, else login |
| 403 | `woreda=` outside supervisor scope | “Outside your scope”; do not show empty fake data |
| 404 | Old `/dashboard/woreda-health` | Update client to `/dashboard/summary` |

Example foreign woreda:

```json
{
  "status": "error",
  "code": "PERMISSION_DENIED",
  "message": "Woreda ET03-W02 is outside your scope.",
  "details": {}
}
```

## Agent payload (for comparison — not this screen)

Same route with a DA token returns:

```json
{
  "view": "agent",
  "da_id": "DA-000001",
  "range": { "from": "...", "to": "..." },
  "visits": { "planned": 6, "completed": 4, "missed": 0, "rescheduled": 0, "in_progress": 0 },
  "tasks": { "open": 4, "done": 3, "urgent": 1, "due_today": 2 },
  "sync": {
    "server_pending": 0,
    "failed": 0,
    "conflicts": 0,
    "last_received_at": "..."
  }
}
```

`GET /dashboard/today` remains the DA day plan only — not used for Supervisor Programme Health.

## Manual check (curl)

```bash
TOKEN=$(curl -s -X POST "http://127.0.0.1:8200/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"usr":"sup.demo@da.local","pwd":"Demo#2026"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")

curl -s "http://127.0.0.1:8200/api/v1/da-services/dashboard/summary?from=2026-10-13&to=2026-11-11" \
  -H "Authorization: Bearer $TOKEN" -H "Accept: application/json"
```

Expect `"view":"woreda"` and `kebeles` array. Foreign woreda should be HTTP 403:

```bash
curl -s -w "\nHTTP:%{http_code}\n" \
  "http://127.0.0.1:8200/api/v1/da-services/dashboard/summary?from=2026-10-13&to=2026-11-11&woreda=ET03-W02" \
  -H "Authorization: Bearer $TOKEN"
```

## Open gaps / FE notes

- Date range UI may still be static “Last 30 days”; still send real `from`/`to` so Visit % and status stay consistent.
- `data_quality` is interim (mean of visit %). A dedicated quality DocType may replace it later without changing the field name.
- Kebele filter dropdowns should be built from the unfiltered (or lightly filtered) `kebeles[].kebele_code` / `kebele` pairs, or from administrative area APIs if you already use them elsewhere — do not invent codes.
- Ticket DA-374: replace mock supervisor dashboard with this contract.
