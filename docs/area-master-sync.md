# Administrative areas from Master Data

DA Services keeps a copy of Ethiopia's Region / Zone / Woreda / Kebele tree in
`DA Administrative Area`. Scope checks, the Kebele picker and the assignment screens
(`GET /api/v1/da-services/master/areas`, `GET /master/kebeles`) read it. The tree itself is
owned by the platform's Master Data service, so DA Services syncs it instead of seeding it
by hand.

## What the sync does

`da_services.services.area_sync.sync_areas` walks Master Data level by level
(`POST /geo/get_all_geo_levels`, then `POST /geo/get_geo_level_values` per parent) and
upserts one row per area:

| Master Data | DA Administrative Area |
|---|---|
| `level_value_id` (P-code, e.g. `ET010101`) | `code` (the row name) |
| `level_mnemonic` region / zone / woreda / kebele | `level` Region / Zone / Woreda / Kebele |
| `parent_level_value_id` | `parent_area` (a Region has none; the country level is not kept) |
| `level_value_mnemonic` | `name_en` |

Rows carry `source = master_data`, `source_version` (the sync time) and `last_synced_at`.
An area Master Data no longer lists is set `active = 0`, never deleted: assignments and
visits link to it. If Master Data answers with no areas at all, nothing is changed.

The same P-codes come back from the DA Registry on every DA record
(`region_code`, `woreda_code`, `geo_lowest_level_value_id`), so after the sync the
registry's codes, the RBAC scopes and the area master agree.

## Running it

Master Data takes the same staff token as the DA Registry. A scheduled job or a bench
command has no signed-in user, so the sync uses the **service identity** in
`site_config.json` (`da_registry_keycloak`: the `da-services-backend` client credentials,
or a staff user's portal login on dev), whatever `da_registry_auth` is set to for the
user-facing calls. With `da_registry_backend: "mock"` it loads the demo geography.

First load after a deploy, and whenever the tree changes:

```
bench --site <site> execute da_services.services.area_sync.sync_areas
```

It prints the counts: `{"inserted": …, "updated": …, "unchanged": …, "deactivated": …}`.
The scheduler then runs it daily (`sync_areas_scheduled`); a run that cannot reach Master
Data or has no service identity writes one Error Log entry and skips.

Master Data answers one level per parent, so a full tree is roughly one call per Region,
Zone and Woreda (about a thousand calls on production data, a few minutes).
