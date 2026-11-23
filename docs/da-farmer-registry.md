# Farmer list: Farmer Registry search

`GET /api/v1/da-services/farmers` returns one page of the Farmer Registry register. It calls the
registry's `search_in_a_register` live with the **caller's own token** and keeps no copy of the
data. Request keys are the registry's own; each row's `display_fields` are returned as plain keys.

## Request

```
GET /api/v1/da-services/farmers
    ?current_page=<optional>
    &page_size=<optional>
    &filter_by=<optional JSON object>
    &search_text=<optional>
Authorization: Bearer <access_token>
X-CSRF-Token: <csrf_token>
```

| Parameter | Required | Description |
|---|---|---|
| `current_page` | no | Page number, a whole number of at least 1. Default `1` |
| `page_size` | no | Rows per page, 1 to 1000. Default `100` |
| `filter_by` | no | Registry filter object, e.g. `{"first_name": {"eq": "test"}}`. On a GET, send it URL-encoded JSON; in a JSON body, as an object. Default `{}` |
| `search_text` | no | Free-text search. Default `""` |

The four values are sent to the registry as its `pagination_request`, with the geo codes of
`filter_by` filled in or checked and sent as names (see [Scope](#scope)).

Both headers are required. They are forwarded unchanged: the bearer token as `Authorization`, and
the CSRF value as the `X-CSRF-Token` header and cookie (the double-submit form a server-to-server
call needs). The registry applies the caller's own access.

### Example

```bash
curl -G 'http://localhost:8000/api/v1/da-services/farmers' \
  --data-urlencode 'current_page=1' \
  --data-urlencode 'page_size=100' \
  --data-urlencode 'filter_by={"first_name":{"eq":"test"}}' \
  --data-urlencode 'search_text=' \
  -H 'Authorization: Bearer <access_token>' \
  -H 'X-CSRF-Token: <csrf_token>'
```

The tokens come from the SSO login (`POST /api/v1/da-services/sso/login`), which returns
`access_token` and `csrf_token`.

## Call to the registry

```
POST {farmer_registry_domain}/register-data/search_in_a_register
{
  "request_header": {
    "sender_app_mnemonic": "OAN_REGISTRY_API_TEST",
    "sender_app_url": "",
    "request_id": "<uuid>",
    "request_timestamp": "<UTC, ISO 8601>"
  },
  "request_body": {
    "request_payload": {"register_id": "<farmer_registry_form_register_id>"},
    "pagination_request": {
      "current_page": 1,
      "page_size": 100,
      "filter_by": {"first_name": {"eq": "test"}},
      "search_text": ""
    }
  }
}
```

## Response

`data` is the registry's `response_payload` and `meta` is its `pagination_response`. Each row's
`display_fields` list is turned into plain keys, `field_name` → `value`, and `display_fields` is
removed. The row's other keys are kept as they are. `meta` also carries `api_version`, which every
v1 response adds.

Registry row:

```json
{
  "display_fields": [
    {"field_name": "functional_record_id", "order": 1, "value": "FR-821910965037"},
    {"field_name": "first_name", "order": 2, "value": "Signoff"},
    {"field_name": "woreda_name", "order": 12, "value": "Tahtay Adiyabo"}
  ]
}
```

Returned as:

```json
{
  "status": "success",
  "message": "Success",
  "data": [
    {
      "functional_record_id": "FR-821910965037",
      "first_name": "Signoff",
      "woreda_name": "Tahtay Adiyabo"
    }
  ],
  "meta": {
    "api_version": "v1",
    "current_page": 1,
    "page_size": 100,
    "number_of_items": 1,
    "number_of_pages": 1
  }
}
```

## Scope

The geo keys of `filter_by` are filled in or checked from the caller's User Permissions, then sent
to the registry as names, by `apply_geo_user_permissions` (`da_services/services/utils.py`):

| Caller sends (a code) | User Permission on | Registry gets (a name) | Name from |
|---|---|---|---|
| `region_code` | DA Region | `region_name` | `DA Region.region_name` |
| `zone_code` | DA Zone | `zone_name` | `DA Zone.zone_name` |
| `woreda_code` | DA Woreda | `woreda_name` | `DA Woreda.woreda_name` |
| `kebele_code` | DA Kebele (the `Not Assigned` placeholder is ignored) | `kebele_name` | `DA Kebele.kebele_name` |

For each level:

- **Empty or missing**, and the caller has User Permissions on that level: their codes are used.
- **Given** (`"X"`, `{"eq": "X"}` or `{"in": [...]}`), and the caller has User Permissions on that
  level: every code must be one of them, else `403 PERMISSION_DENIED` and the registry is not called.
- **No User Permissions on that level**: the codes asked for are used as they are.

The codes are then replaced by their names, `{"eq": "<name>"}` for one and
`{"in": ["<name>", ...]}` for several. A code with no geo record is `400 VALIDATION_ERROR`.
The `*_code` keys are not sent. Administrator is not restricted, but codes are still sent as names.
Other `filter_by` keys pass through unchanged, and the registry's rows are returned as they are.

Example: a DA with a User Permission on DA Woreda `ET04-W01` sends
`{"first_name": {"eq": "test"}}`; the registry gets
`{"first_name": {"eq": "test"}, "woreda_name": {"eq": "Bako Tibe"}}`.

## Configuration

| Setting | Where | Default | Purpose |
|---|---|---|---|
| `farmer_registry_domain` | DA Services Settings | none, required | Registry host (`staff-farmer.example.org`) or full URL. A bare host gets `https://` |
| `farmer_registry_form_register_id` | DA Services Settings | none, required | `request_payload.register_id` |
| `farmer_registry_sender_app` | `site_config.json` | `OAN_REGISTRY_API_TEST` | `sender_app_mnemonic` |
| `farmer_registry_timeout_seconds` | `site_config.json` | `20` | Timeout of the call |

## Errors

Errors use the standard envelope (`status`, `message`, `code`).

| HTTP | `code` | Cause |
|---|---|---|
| 400 | `VALIDATION_ERROR` | `current_page` or `page_size` out of range; `filter_by` not a JSON object; a geo code with no record; no `X-CSRF-Token` header |
| 401 | `AUTHENTICATION_ERROR` | No Bearer `Authorization` header, or the registry refused the token (401 or 403) |
| 403 | `PERMISSION_DENIED` | A geo code in `filter_by` is outside the caller's User Permissions |
| 500 | `FARMER_REGISTRY_NOT_CONFIGURED` | `farmer_registry_domain` or `farmer_registry_form_register_id` is empty |
| 502 | `BAD_GATEWAY` | The registry answered with an error or a body that is not usable |
| 503 | `SERVICE_UNAVAILABLE` | The registry is unreachable or answered 5xx |

The cause is written to the Error Log under **Farmer Registry search failed**; the caller sees a
fixed message that does not name internal hosts.

## Code

| Path | Contents |
|---|---|
| `da_services/api/v1/farmers.py` (`list_farmers`) | The endpoint: parameter checks, header forwarding, error mapping |
| `da_services/integrations/da_farmer_registry/client.py` | `FarmerRegistryClient.search_in_a_register`, `client_from_settings` |
| `da_services/integrations/da_farmer_registry/errors.py` | `FarmerRegistryError` with a `kind` (`config`, `unauthorized`, `unavailable`, `rejected`) |
| `da_services/services/utils.py` (`apply_geo_user_permissions`) | Geo codes of `filter_by` from User Permissions, sent as names |
| `da_services/tests/test_da_farmer_registry.py` | Tests; the registry is mocked |
| `da_services/tests/test_geo_user_permission_filter.py` | Tests of `apply_geo_user_permissions` |

The package is separate from `integrations/farmer_registry`, which still serves the farmer
profile, plots, photo, change requests and registration.
