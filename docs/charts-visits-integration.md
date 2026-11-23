# Visits chart feeder (central dashboard)

DA Services exposes one Visit aggregate feeder that matches the Confluence **Dashboard APIs: how to use** contract used by Farmer / Crop / Livestock dashboard-apis. The central BFF calls this feeder; we do **not** rebuild the BFF envelope.

## Endpoint

| Method | Path | Body |
|--------|------|------|
| `GET` | `/api/v1/da-services/charts/visits` | **Raw JSON array** (no `{status,data}` envelope) |

Chart id for BFF mapping: **`visits`**.

Portal `GET /api/v1/da-services/dashboard/*` is unchanged (DA/Supervisor session JWT).

Health stays open: `GET /api/v1/da-services/health` (no token).

## Auth (staff Keycloak client-credentials)

| Rule | Detail |
|------|--------|
| Who calls | Dashboard **service account**, not a DA/Supervisor user |
| Grant | Keycloak **client-credentials** in the **staff** realm |
| Role | Client role **`charts:read`** on this API’s charts client |
| Local | `charts_auth_enabled: false` (or omit) — Confluence “empty clientId” |
| Fail closed | When enabled: missing / invalid token → 401; wrong role → 403 |

### `site_config.json`

```json
{
  "charts_auth_enabled": true,
  "charts_auth_issuer": "https://<keycloak>/realms/staff",
  "charts_auth_audience": "da-services-dashboard-api",
  "charts_auth_role": "charts:read"
}
```

Optional: `charts_auth_jwks_url` (defaults to `{issuer}/protocol/openid-connect/certs`).

Audience may appear as JWT `aud` or Keycloak `azp`. Role is read from `resource_access[<audience>].roles`.

## Filters

| Param | Notes |
|-------|--------|
| `region`, `zone`, `woreda`, `kebele` | Empty / `all` = no filter |
| `from`, `to` | Inclusive calendar days on `scheduled_at`. Both omitted → **current month** |
| `da_id`, `visit_type`, `status` | Optional equality filters (`status` → `physical_status`) |
| `groupBy` | Omit → one KPI object. Else `visit_type` \| `woreda` \| `da_id` \| `month` |

Unknown params are ignored. `farmingType` / `recordState` are N/A (not required).

**Scope:** filter-only. Empty filters = all visits visible to the service account. No User Permissions / portal geography for this route.

## Row contract

Ungrouped (including zero matches — keeps BFF tiles stable):

```json
[{
  "planned": 12,
  "completed": 8,
  "missed": 1,
  "rescheduled": 2,
  "in_progress": 1,
  "cancelled": 0,
  "total": 24,
  "farmers_reached": 7
}]
```

Grouped with no matches → `[]`. Grouped rows also include the group key field (e.g. `"woreda": "ET04-W01"`).

Counting rules (same ideas as portal `dashboard_counts`):

- `planned` — statuses in Planned / Confirmed / In progress / Physical Visit Complete / Missed
- `completed` / `missed` / `rescheduled` / `in_progress` / `cancelled` — exclusive buckets
- `total` — all matching visits
- `farmers_reached` — distinct `farmer_id` values

Aggregates only — no PII, GPS, or visit ids.

## BFF handoff

1. Keycloak client for DA charts + role `charts:read`
2. BFF config: add DA Services base URL (today registries + a2c only)
3. Map `charts=visits` → `GET {da_base}/api/v1/da-services/charts/visits`
4. Forward geo / date query params

After wiring, `GET /api/charts?charts=visits` on the BFF should wrap our array in the usual envelope.

## Postman

`postman/Charts/` — client-credentials placeholders + token pre-request. Leave `charts_client_id` empty for local auth-off.
