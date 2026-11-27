# Assignments chart feeder (central dashboard)

DA Services exposes a Kebele assignment aggregate feeder on the same contract as the Visits feeder
([charts-visits-integration.md](charts-visits-integration.md)). It covers the "Kebele assignments and
reassignments" report: assignments, reassignments, out-of-bounds and superseded, per month.

## Endpoint

| Method | Path | Body |
|--------|------|------|
| `GET` | `/api/v1/da-services/charts/assignments` | **Raw JSON array** (no `{status,data}` envelope) |

Chart id for BFF mapping: **`assignments`**.

Portal `GET /api/v1/da-services/assignments` and `/assignments/coverage` are unchanged (Supervisor
session JWT).

## Auth

Same as the Visits feeder: staff Keycloak client-credentials token with the client role
**`charts:read`**, checked by `charts_auth`. Configured on **DA Services Settings → Charts Auth**. With
Charts Auth Enabled unchecked (local) no token is needed. When enabled, a missing or invalid token
gives 401 and a token without the role gives 403.

## Source

`DA Kebele Assignment`, the decision record DA Services keeps for every assignment and reassignment.
A decision that is replaced is not deleted: it stays as `Superseded` with a link to the row that
replaced it, so any past month can be reported.

## Filters

| Param | Notes |
|-------|--------|
| `region`, `zone`, `woreda`, `kebele` | Geo master codes. Empty / `all` = no filter |
| `from`, `to` | Inclusive calendar days on `effective_date`. Both omitted → **current month** |
| `da_id` | Optional equality filter |
| `groupBy` | Omit → one KPI object. Else `month` \| `woreda` |

Filters combine with AND. Unknown params and an unknown `groupBy` are ignored.

**Scope:** filter-only. Empty filters = all assignments. No User Permissions / portal geography for
this route, because the caller is the dashboard service account.

## Row contract

Ungrouped (including zero matches, which keeps BFF tiles stable):

```json
[{
  "assignments": 14,
  "reassignments": 5,
  "out_of_bounds": 2,
  "superseded": 5
}]
```

Grouped rows also carry the group key, sorted by it (`month` is `YYYY-MM`, oldest first):

```json
[{"assignments": 8, "reassignments": 2, "out_of_bounds": 1, "superseded": 4, "month": "2026-10"},
 {"assignments": 6, "reassignments": 3, "out_of_bounds": 1, "superseded": 1, "month": "2026-11"}]
```

Grouped with no matches → `[]`.

Counting rules, over the assignment decisions whose `effective_date` is in the range:

| Key | Counts |
|-----|--------|
| `assignments` | every decision (first postings, reassignments and flagged ones) |
| `reassignments` | decisions that replaced an earlier decision of the same DA (`supersedes` is set) |
| `out_of_bounds` | decisions whose geofence result is `Out-of-bounds` |
| `superseded` | decisions that have since been replaced (state `Superseded`) |

The buckets overlap: a reassignment that was later replaced counts in `assignments`,
`reassignments` and `superseded`.

A flagged out-of-bounds decision does not replace the posting in force, so it is not a reassignment.
The decision that later moves the DA is.

The key names are a contract with the dashboard: adding a key is fine, renaming or removing one is a
breaking change. `test_charts_assignments.py` pins them.

Aggregates only. No names, positions or assignment ids.

## If assignments move to the DA Registry only

This feeder reads our own records. If the posting history is later kept only in the DA Registry, the
sync has to bring the full history (dates, old and new Kebele, superseded rows), not just the current
Kebele. With that in place the same report works from the synced rows.

## BFF handoff

1. Same Keycloak client and `charts:read` role as the Visits feeder
2. Map `charts=assignments` → `GET {da_base}/api/v1/da-services/charts/assignments`
3. Forward geo / date query params

## Postman

`postman/Charts/` → folder **2. Assignments chart**.
