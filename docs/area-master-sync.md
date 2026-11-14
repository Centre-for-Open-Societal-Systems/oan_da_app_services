# Geography masters from Master Data

DA Services keeps Ethiopia's Country / Region / Zone / Woreda / Kebele tree as Frappe
DocTypes (`DA Country`, `DA Region`, `DA Zone`, `DA Woreda`, `DA Kebele`). Desk Links,
RBAC / Kebele Assignment scopes, Visit / Task stamps, and User Permissions read these
masters. The tree is owned by the platform's Master Data service; DA Services syncs it
instead of seeding codes by hand.

There is **no** `DA Administrative Area` DocType and no `area_sync` module. Use
`geo_master_sync` only.

## What the sync does

`da_services.services.geo_master_sync.sync_geo_masters` walks Master Data level by level
(`POST /geo/get_all_geo_levels`, then `POST /geo/get_geo_level_values` per parent) and
upserts one row per value into the matching DocType.

| Master Data | Frappe DocType |
|---|---|
| country | `DA Country` |
| region | `DA Region` |
| zone | `DA Zone` (Link parent: Region) |
| woreda | `DA Woreda` (Link parent: Zone) |
| kebele | `DA Kebele` (Link parent: Woreda) |

Rows that Master Data no longer lists are set `is_active = 0`, never deleted: other
records still Link to them.

The run refuses to change anything when the answer cannot be trusted (login or call
failure, empty level, or more than a fifth of active rows would deactivate).

## Running it

Credentials come from **DA Services Settings** (`master_sync_username` / password /
login_key) plus `master_data_domain` in site config. See `geo_master_sync` module docs.

First load after a deploy, and whenever the tree changes:

```
bench --site <site> execute da_services.services.geo_master_sync.sync_geo_masters
```

The scheduler runs it daily on the long queue
(`da_services.services.geo_master_sync.sync_geo_masters_scheduled`).

## Desk and API usage

- Geography fields are **Link** fields to these DocTypes (not free-text Data).
- Cascading Desk pickers use `set_query` / `da_services.geo_cascade` (Visit pattern).
- Assignment master lists (`GET …/master/areas`, `GET …/master/kebeles`) read via
  `da_services.services.geo.list_masters` / `kebeles_for_woreda`.
- Thin parent-chain helpers live in `da_services.services.geo` (`ancestors_of_kebele`,
  etc.). Prefer Link + `fetch_from` over custom re-validation.

## Tests

Integration tests call `require_geo_masters` / `resolve_test_geo` (and
`require_named_masters` when a test is coupled to mock-registry fixture codes). They
must not invent Admin Area trees.
