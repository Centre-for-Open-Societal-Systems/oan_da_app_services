# Master Data API

`GET /api/v1/da-services/get_master_data` returns lists owned by the platform's Master Data
service: the geography (country, region, zone, woreda, kebele) and code lists such as
`DOCUMENT_PURPOSE`. One endpoint, one set of parameters and one row shape serve both, so a
frontend builds every dropdown from the same call.

The endpoint reads Master Data live with the **caller's own token**. It keeps no copy of the data.
(The local `DA Administrative Area` table behind `/master/areas` is a different, synced copy:
see `area-master-sync.md`.)

## Request

```
GET /api/v1/da-services/get_master_data
    ?data_type=<geo|attribute>
    &field_name=<level or attribute id>
    &parent=<optional>
    &search=<optional>
    &current_page=<optional>
    &page_size=<optional>
Authorization: Bearer <access_token>
X-CSRF-Token: <csrf_token>
```

| Parameter | Required | Description |
|---|---|---|
| `data_type` | yes | `geo` for the geography, `attribute` for a code list |
| `field_name` | yes | `geo`: `country`, `region`, `zone`, `woreda` or `kebele`. `attribute`: an attribute id such as `DOCUMENT_PURPOSE` (letters, digits, `_`, `.`, `-`; at most 64 characters) |
| `parent` | no | Keep only the children of this value: a region code for `zone`, a zone code for `woreda`, and so on. Not allowed for `country`. For `attribute`, matches the row's parent value |
| `search` | no | Search text, truncated to 80 characters |
| `current_page` | no | Page number, a whole number of at least 1. Default `1` |
| `page_size` | no | Rows per page, 1 to 1000. Default `100` |

Both headers are required. They are forwarded to Master Data unchanged: the bearer token as
`Authorization`, and the CSRF value as `X-CSRF-Token` header and cookie (the double-submit form a
server-to-server call needs). Master Data therefore applies the caller's own access, and no service
identity is involved.

The caller needs a DA Services role (Development Agent, Supervisor, Executive, OAN Administrator,
Communications Officer) or an unrestricted role, the same rule as `/master/areas`.

### Examples

```bash
# Regions of a country
curl 'http://localhost:8000/api/v1/da-services/get_master_data?data_type=geo&field_name=region&parent=ET' \
  -H 'Accept: application/json' \
  -H 'Authorization: Bearer <access_token>' \
  -H 'X-CSRF-Token: <csrf_token>'

# Woredas of a zone, searched and paged
curl 'http://localhost:8000/api/v1/da-services/get_master_data?data_type=geo&field_name=woreda&parent=ET04-Z01&search=Bako&current_page=1&page_size=50' \
  -H 'Authorization: Bearer <access_token>' \
  -H 'X-CSRF-Token: <csrf_token>'

# A code list
curl 'http://localhost:8000/api/v1/da-services/get_master_data?data_type=attribute&field_name=DOCUMENT_PURPOSE' \
  -H 'Authorization: Bearer <access_token>' \
  -H 'X-CSRF-Token: <csrf_token>'
```

The tokens come from the SSO login (`POST /api/v1/da-services/sso/login`), which returns
`access_token` and `csrf_token`.

## Response

The row keys are the same for `geo` and `attribute`.

```json
{
  "status": "success",
  "data": [
    {
      "id": "DOCPURP_IDENTITY_VERIFICATION",
      "code": "IDENTITY_VERIFICATION",
      "name": "Identity verification",
      "field_name": "DOCUMENT_PURPOSE",
      "parent": null,
      "sort_order": 1
    }
  ],
  "meta": {
    "data_type": "attribute",
    "field_name": "DOCUMENT_PURPOSE",
    "parent": null,
    "search": "",
    "current_page": 1,
    "page_size": 100,
    "total_items": 4,
    "total_pages": 1,
    "total": 4
  }
}
```

### Row

| Key | `geo` (Master Data field) | `attribute` (Master Data field) |
|---|---|---|
| `id` | `level_value_id` | `value_id` |
| `code` | `level_value_id` (geography has no separate code) | `value_code` |
| `name` | `level_value_mnemonic` | `value_display` |
| `field_name` | the level asked for | `attribute_id` |
| `parent` | `parent_level_value_id` | `parent_value_id` |
| `sort_order` | always `0` | `sort_order`; `null` is returned as `0` |

Attribute rows are sorted by `sort_order`; rows with the same value keep Master Data's order.

### Meta

`meta` repeats the request (`data_type`, `field_name`, `parent`, `search`) and adds the page:

| Key | Meaning |
|---|---|
| `current_page`, `page_size` | The page asked for |
| `total_items` | Master Data's `number_of_items`. When it does not report it, the number of rows returned |
| `total_pages` | Master Data's `number_of_pages`. When it does not report it, `current_page` |
| `total` | Rows in this page |

Attribute lists report their totals. The geography calls answer without a `pagination_response`, so
for `geo` the two fallbacks apply: `total_pages` then only echoes the page asked for. To read a whole
geo level, request the next `current_page` until a page returns fewer than `page_size` rows.

## Flow

```
client ──GET──▶ da_services  get_master_data
                   │  role check; read Authorization + X-CSRF-Token from the request
                   │  base URL = DA Services Settings.master_data_domain
                   ▼
  data_type=geo        POST {domain}/geo/get_all_geo_levels      region → level_id l1
                       POST {domain}/geo/get_geo_level_values    level_id (+ parent)
  data_type=attribute  POST {domain}/attributes/get_attribute_values    attribute_id
                   ▼
              rows mapped to one shape, parent filter applied, page numbers added
```

Every call to Master Data uses the staff envelope (`request_header` with a fresh `request_id`,
timestamp and the sender app; `request_body` with `request_payload` and `pagination_request`) and
asks for exactly the page requested. The geo levels lookup is one call and reads no further pages.

`parent` is sent to Master Data and also applied to the returned page, so the answer is the parent's
children even if Master Data ignores the filter. If it does ignore it, a page can hold fewer than
`page_size` rows.

## Configuration

| Setting | Where | Default | Purpose |
|---|---|---|---|
| `master_data_domain` | DA Services Settings (single doctype) | none, required | Master Data host (`master-data.example.org`) or full URL. A bare host gets `https://` |
| `da_registry_sender_app` | `site_config.json` | `da-services` | `sender_app_mnemonic` sent to Master Data |
| `master_data_timeout_seconds` | `site_config.json` | `20` | Timeout of each call to Master Data |

## Errors

Errors use the standard envelope (`status`, `message`, `code`, `request_id`).

| HTTP | `code` | Cause |
|---|---|---|
| 400 | `VALIDATION_ERROR` | `data_type` missing or unknown; `field_name` not a level for `geo` or not a valid attribute id; `parent` given for `country`; `current_page` or `page_size` out of range; no `X-CSRF-Token` header |
| 401 | `AUTHENTICATION_ERROR` | No Bearer `Authorization` header, or Master Data refused the token (401 or 403) |
| 403 | `PERMISSION_DENIED` | The caller holds no DA Services role |
| 500 | `MASTER_DATA_NOT_CONFIGURED` | `master_data_domain` is empty |
| 502 | `BAD_GATEWAY` | Master Data answered with an error or a body that is not usable |
| 503 | `SERVICE_UNAVAILABLE` | Master Data is unreachable or answered 5xx |

For 5xx and configuration errors the cause is written to the Error Log under **Master Data request
failed**; the caller sees a fixed message that does not name internal hosts.

## Code

| Path | Contents |
|---|---|
| `da_services/api/v1/master.py` | The endpoint: parameter checks, header forwarding, error mapping |
| `da_services/integrations/master/client.py` | `MasterDataClient`: the three Master Data calls, row mapping, paging |
| `da_services/integrations/master/errors.py` | `MasterDataError` with a `kind` (`config`, `unauthorized`, `unavailable`, `rejected`, `invalid`) |
| `da_services/tests/test_master_data.py` | Unit tests; no live service |

The package is separate from the other integrations and shares no code with them.

## Testing

```
cd apps/da_services
python -m unittest da_services.tests.test_master_data
ruff check . && ruff format --check .
```

The unit tests mock Master Data. A live check needs a staff token and `master_data_domain` set on
the site; the server must be restarted after a code change when it runs with `--noreload`.
