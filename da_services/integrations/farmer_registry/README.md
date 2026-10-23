# Farmer Registry Integration

Boundary to the **Farmer Registry** (OpenG2P Gen 2) for the DA app: read farmers,
plots and holdings, send farmer edits as change requests, and register new
farmers through the ingest pipeline. Services never call the registry directly;
they ask `get_farmer_registry_client()` for a client and call its methods.

```
client.py    interface + factory (mock | http)
mock.py      in-memory fixtures — the default, used by tests and local dev
http.py      live client over the OpenG2P Staff API
schemas.py   Pydantic models: Farmer, FarmerListItem, Plot, FarmerRegistrationForm
mapper.py    registry rows <-> our schemas, DA form -> ingest payload, DA-41 updates -> change requests
```

## Backends

| `farmer_registry_backend` | Behaviour |
|---|---|
| `"mock"` (default) | Fixture data, no network. Tests always run on this. |
| `"http"` | Live registry over the Staff API (contract below). |

## Staff API contract (http backend)

Every call is a `POST` with the OpenG2P staff envelope:

```json
{
  "request_header": {"sender_app_mnemonic": "DA_SERVICES", "request_id": "<uuid4>", "request_timestamp": "...", ...},
  "request_body": {
    "request_payload": {...},
    "pagination_request": {"current_page": 1, "page_size": 50, "sort_by": "", "filter_by": {...}, "search_text": ""}
  }
}
```

Responses come back as `{"response_header": {"response_status": "SUCCESS|ERROR", ...}, "response_body": {"response_payload": ..., "pagination_response": {...}}}`.

| Client method | Staff API endpoint | Notes |
|---|---|---|
| `get_farmer(id)` | `register-data/get_subject_record` + `get_tab_records` (farmer, location, household tabs) | Identity row is flat; birth / socio-economic / phones / location / household come from tab sections and are merged in. Returns `None` when the registry says not found. |
| `list_farmers(woreda, kebele, state, limit, offset)` | `register-data/search_in_a_register` | `filter_by` is a JSON dict of the register's whitelisted fields (`woreda_name`, `region_name`, `state`, `first_name`, ...). DA codes are translated to names via `DA Administrative Area`. `kebele_name` is not yet whitelisted on the registry, so Kebele is also narrowed client-side from `display_fields`. |
| `get_farmer_plots(id)` | `register-data/get_tab_records` (land tab) | Land Details rows -> `Plot`. |
| `update_farmer(id, updates)` | `change-requests/create_change_request` | One change request per register section; approved by a Woreda officer. Only socio-economic fields are editable this way today (`income_source`, `education_level`, `marital_status`, `is_psnp_user`, `disabled`); other DA-41 fields are rejected as unsupported so the sync queue records a clear reason. The change request id is returned as `version`. |
| `register_farmer(payload)` | `input-mechanism-data/ingest-data?data_model&register_id&intake_form_id` | Async: returns a `correlation_id`; the farmer lands in the Intake Form and gets an FR-id after Woreda approval. |
| `get_ingest_status(correlation_id)` | `ingestion-data/search_in_ingestion_data` | Pipeline rows for a submission. |

Auth: a Keycloak **client-credentials** token (service account) cached per worker
behind a lock, plus the double-submit CSRF pair (`X-CSRF-Token` header == cookie)
the staff middleware requires on POST. HTTP 4xx raises
`FarmerRegistryClientError` (not retryable), 5xx / network raises
`FarmerRegistryServerError` (retryable).

The service account needs these `farmer-registry-staff-portal` client roles:
`Intake Officer` (intakeSubmission:edit — register), `Data Editor`
(register:view, changeRequest:create — read/update), `Integration Manager`
(incomingMessage:view — ingest status).

## Configuration

`sites/<site>/site_config.json` (see `config.example.json`):

```json
{
  "farmer_registry_backend": "http",
  "farmer_registry_url": "https://staff-farmer-development.oanstaging.com",
  "farmer_registry_timeout": 30,
  "farmer_registry_keycloak": {
    "token_url": "https://keycloak-development.oanstaging.com/realms/staff/protocol/openid-connect/token",
    "client_id": "da-services",
    "client_secret": "<from DevOps>"
  },
  "farmer_registry_ingest": {
    "data_model": "FARMER_ODK_MODEL",
    "register_id": "a1a4d25a-1cd4-4356-abac-985a0b3c6bcd",
    "intake_form_id": "a1a4d25a-1cd4-4356-abac-8782382649",
    "tabs": {"farmer": "farmer_farmer_tab", "location": "farmer_location_tab",
             "household": "farmer_household_link_tab", "land": "farmer_land_tab"}
  }
}
```

| Option | Required | Description |
|---|---|---|
| `farmer_registry_url` | yes | Staff API base URL (`http(s)://...`) |
| `farmer_registry_keycloak` | yes* | `token_url`, `client_id`, `client_secret` of the DA Services service account. *Or `farmer_registry_api_key` for a static bearer token (testing only). |
| `farmer_registry_ingest.register_id` | yes | Farmer register id |
| `farmer_registry_ingest.intake_form_id` | for register | Farmer intake form id |
| `farmer_registry_ingest.data_model` | for register | Ingest data model (default `FARMER_ODK_MODEL`) |
| `farmer_registry_ingest.tabs` | no | Override tab ids (defaults match the dev Farmer register) |
| `farmer_registry_timeout` | no | Seconds, default 30 |

The http client validates this at construction and raises
`FarmerRegistryError` on a missing URL, partial Keycloak config, no auth at all
or a missing register id — misconfiguration surfaces at startup, not on the
first request from the field.

## Cache

Farmer reads are cached in `DA Farmer Reference` (`services/farmers.py`).
Opening a profile fetches and caches that farmer; listing a DA's farmers on the
http backend triggers `sync_farmer_references(kebeles=...)` for Kebeles with no
cached rows, which pages through `list_farmers` and upserts one row per farmer.
Registry failures during that sync are logged and the list falls back to the
cache.

## Usage

```python
from da_services.integrations.farmer_registry import get_farmer_registry_client

client = get_farmer_registry_client()
farmer = client.get_farmer("frm-internal-id")
farmers, total = client.list_farmers(woreda="ET04-W01", limit=50)
plots = client.get_farmer_plots("frm-internal-id")
result = client.update_farmer("frm-internal-id", {"income_source": "LIVESTOCK"})
```

## Testing

- `tests/test_farmer_registration.py` — mapper, registration form validation, idempotency (mock backend).
- `tests/test_farmer_registry_http.py` — the http client against stubbed Staff API envelopes: envelope/auth/CSRF, read merging, search filters and pagination, plots, change requests, 4xx/5xx mapping, config validation.

Run: `bench run-tests --app da_services`.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `FarmerRegistryError: ... misconfigured` at startup | A `farmer_registry_*` key is missing or partial (see table above). |
| `403 Insufficient resource_access roles` | The Keycloak service account lacks the staff-portal client roles listed above. |
| `403 CSRF token missing or invalid` | Header and cookie `X-CSRF-Token` differ — only possible if a proxy strips cookies. |
| `list_farmers` ignores the Kebele | `kebele_name` is not in the registry's filter whitelist yet; rows are narrowed client-side only when they carry a kebele display field. |
| Farmer edit rejected as "not editable through the Farmer Registry change request" | The field is outside the socio-economic section mapping in `mapper.CHANGE_REQUEST_SECTIONS` (phones and household fields live in other registers). |
