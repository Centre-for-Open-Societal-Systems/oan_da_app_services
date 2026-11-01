# Farmer Registry integration (DA-272)

What branch `DA-272-farmer-registry-read-integration` adds on top of `DA-272-registry-integration-into-da-services`:
DA Services reads farmers live from the OpenG2P Farmer Registry and registers new farmers through its intake form.
The frontend talks only to DA Services; DA Services talks to the registry.

```
Frontend ──JWT──► DA Services ──staff token + CSRF──► Farmer Registry (Staff API)
                      │                               Master Data (code lists, places)
                      └── DA Farmer Reference (cache), DA Integration Event (audit)
```

Code map:

| Layer | File |
|---|---|
| API routes | `da_services/api/v1/farmers.py`, `farmers_update.py`, `registries.py` |
| Business rules (scope, cache, registration) | `da_services/services/farmers.py` |
| Registry client | `da_services/integrations/farmer_registry/http.py` (live), `mock.py` (tests / local) |
| Intake form registration | `da_services/integrations/farmer_registry/intake.py` |
| Registry ⇄ DA field mapping | `da_services/integrations/farmer_registry/mapper.py`, `schemas.py` |
| Registry login, CSRF, envelope (shared with Crop Sown, Livestock) | `da_services/integrations/staff_api/auth.py`, `transport.py` |

---

## 1. DA Services APIs

All under `/api/v1/da-services`, with `Authorization: Bearer <JWT from POST /api/v1/auth/login>`.
Every `{id}` accepts the registry internal id or the Farmer-ID (`FR-…`).

### Read

| Method | Endpoint | Returns |
|---|---|---|
| GET | `/farmers?search_text=&page=&page_size=` | Farmer list. With `search_text` it searches the live registry (`FR-` = every approved farmer; also name or phone). Each row: `farmer_id`, `reg_id`, `farmer_name`, `phone`, `kebele`, `woreda`, `region`, `total_land_hectares`, `total_plots`, `photo_url`, `registration_state`, `registered_at`. Optional `woreda`, `kebele`, `state` filters. Without `search_text` it reads the DA cache. |
| GET | `/farmers/{id}/profile` | Profile header and overview (name, phone, email, gender, education, location, total land, land ownership, household size, last visit, `can_edit`) |
| GET | `/farmers/{id}/plots` | Land records (area, ownership, soil fertility, land use) |
| GET | `/farmers/{id}/photo` | `photo_url` (signed link, about 1 hour) and `document_id` |
| GET | `/farmers/{id}/visits` | Visit history |
| GET | `/farmers/{id}/permissions` | `can_edit`, `reason`, `is_linked` |
| GET | `/farmers/{id}/change-requests?status=&tab=` | Change requests raised on the farmer in the registry |

### Register a farmer

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/farmers/registrations/documents` | Upload the identity document (multipart `file`); returns `document_id` |
| POST | `/farmers` | Register the farmer (body validated by `FarmerRegistrationForm`); returns `submission_id`, `status: PENDING_APPROVAL`. A repeated `client_op_id` returns the first result (`DUPLICATE`) instead of a second record |
| GET | `/farmers/registrations/{submission_id}` | Registration status in the registry (`DRAFT` / `PENDING_APPROVAL` / `APPROVED` / `REJECTED`), application reference |

### Update (Farmer-ID now accepted)

| Method | Endpoint | Purpose |
|---|---|---|
| PATCH | `/farmers/{id}` | Queue an update; sent to the registry as a change request |
| GET | `/farmers/{id}/update-status` | Updates not yet in the registry |

### Registration form data (for the frontend wizard)

| Method | Endpoint | Returns |
|---|---|---|
| GET | `/registries/farmer/registers` | Registers of the Farmer Registry (Farmer, Land, Crop, Livestock …) |
| GET | `/registries/farmer/intake-form` | The intake form layout |
| GET | `/registries/farmer/field-options` | Every dropdown's options `{register: {field: [{value, label}]}}`, from the registry's own code lists (cached 1 h) |
| GET | `/registries/farmer/places?level=region\|zone\|woreda\|kebele&parent=` | Master Data places `[{id, name}]` for the location cascade |

---

## 2. Farmer Registry APIs used

Staff API base: `farmer_registry_url` (dev: `https://staff-farmer.development.oanstaging.com`). All POST with the OpenG2P `request_header` / `request_body` envelope.

| Registry endpoint | Used by | For |
|---|---|---|
| `/register-data/search_in_a_register` | `GET /farmers?search_text=` | List and search rows (name, FR-id, location, photo link) |
| `/register-data/get_subject_record` | list (per farmer), profile, photo | Phone, total land, email, photo; identity row |
| `/register-data/get_tab_records` | list (per farmer), profile, plots | Lands tab (plots), farmer / location / household tabs |
| `/change-requests/get_change_requests` | `GET /farmers/{id}/change-requests` | Change requests per tab |
| `/change-requests/create_change_request` | `PATCH /farmers/{id}` (sync) | Send an update |
| `/register-metadata/get_all_registers` | registers, id discovery | Register ids (Farmer register found by name) |
| `/intake-form-metadata/get_all_intake_forms` | id discovery | Intake form id (form name containing "Farmer Ingestion") |
| `/intake-form-metadata/render_intake_form` | intake form, field options, validation | Form layout, dropdown code lists, conditional "required" rules |
| `/intake-form-data/save_intake_form_submission` | `POST /farmers` | Save each section into one draft |
| `/intake-form-data/finalize_intake_form_submission` | `POST /farmers` | Send the draft for approval |
| `/intake-form-data/get_intake_form_submission` | `GET /farmers/registrations/{id}` | Registration status |
| `/intake-form-data/delete_intake_form_submission` | `POST /farmers` on failure | Remove the draft of a failed registration |
| `/documents/upload_documents` | `POST /farmers/registrations/documents` | Store the identity document |
| `/input-mechanism-data/ingest-data` | `POST /farmers` in `ingest` mode | Older path; returns 401 on dev since 26 Oct (ODK signature), use `intake` |

Master Data base: `farmer_registry_master_data_url` (dev: `https://master-data.development.oanstaging.com`).

| Master Data endpoint | For |
|---|---|
| `/attributes/get_attribute_values` | Dropdown labels (GENDER, CROP_SEASON …) |
| `/geo/get_all_geo_levels`, `/geo/get_geo_level_values` | Region › Zone › Woreda › Kebele |

Registry login (`staff_api/auth.py`), cached per worker until shortly before expiry:
staff user via IAM (`/auth/start_authentication_transaction` → Keycloak login form → IAM callback cookies `X-Access-Token` + `X-CSRF-Token`), else Keycloak `client_credentials`.
Each call sends `Authorization: Bearer` and the matching `X-CSRF-Token` header and cookie.

---

## 3. Behaviour worth knowing

**Who sees which farmers** (`services/farmers.py` `search_farmers`):

| Role | Sees |
|---|---|
| Admin | every farmer; `woreda` / `kebele` filters as asked |
| DA | farmers in the DA's assigned kebeles (DA Kebele Assignment); none without an assignment |
| Supervisor | farmers in the supervisor's woreda |

**The dev registry ignores `filter_by`.** `search_in_a_register` returns the same rows for any filter (checked 29 Oct and 1 Nov 2026: `woreda_name=Nowhere` still returns all 25). DA Services sends the filter anyway and then keeps only the rows whose woreda / kebele name matches; `filtered_client_side` reports it, and `total` counts what is left.

**Search rows are incomplete.** They carry only the registry's list-view columns (names, gender, birth, education, location, photo). Phone and land area come from `get_subject_record`, the plot count from the Lands tab. These are cached per farmer for 10 minutes, so the first full list takes about 20 s for 25 farmers and later calls under 1 s.

**Registration** (`farmer_registry_register_mode: "intake"`): every section is saved into one draft, then finalized. Rules read from the form: the membership section is always sent; "Others" income needs its text; Disabled = Yes needs type and severity; a co-op / union member needs its name. A failed save (4xx, 5xx or timeout) deletes the draft so no half record stays in the registry. Every attempt is a `DA Integration Event`. The Farmer-ID (`FR-…`) exists only after a supervisor approves, so pending farmers do not appear in `FR-` search.

**Other details**
- The list sends phone as `+251…`, the profile as `9…` (both from the registry).
- `photo_url` is a signed MinIO link that expires after about an hour; call the list or `/photo` again for a new one.
- Document file names may contain only one dot; DA Services rewrites them.

---

## 4. Configuration (`site_config.json`)

```json
{
  "farmer_registry_backend": "http",
  "farmer_registry_url": "https://staff-farmer.development.oanstaging.com",
  "farmer_registry_master_data_url": "https://master-data.development.oanstaging.com",
  "farmer_registry_register_mode": "intake",
  "farmer_registry_keycloak": {
    "iam_base_url": "https://staff-iam.development.oanstaging.com",
    "kc_realm_url": "<staff realm URL>",
    "login_provider_id": "1",
    "username": "<staff user>",
    "password": "<staff password>"
  }
}
```

`register_id` and `intake_form_id` are discovered and cached for an hour; set them under `farmer_registry_ingest` only to pin them.
`farmer_registry_backend: "mock"` serves fixtures for tests and offline work.
Full list with comments: `da_services/integrations/farmer_registry/config.example.json` and the farmer_registry `README.md`.

---

## 5. Testing

Automated (all pass on this branch):

```bash
bench --site <site> run-tests --app da_services --module da_services.tests.test_farmer_registry_http        # 32
bench --site <site> run-tests --app da_services --module da_services.tests.test_farmers_api                 # 26
bench --site <site> run-tests --app da_services --module da_services.tests.test_farmer_intake_registration  # 37
bench --site <site> run-tests --app da_services --module da_services.tests.test_farmer_registration         # 18
```

By hand against the dev registry (Admin sees every farmer):

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8200/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"usr":"Administrator","pwd":"admin"}' | python -c "import sys,json;print(json.load(sys.stdin)['data']['access_token'])")

curl -s "http://127.0.0.1:8200/api/v1/da-services/farmers?search_text=FR-&page=1&page_size=50" -H "Authorization: Bearer $TOKEN"
curl -s "http://127.0.0.1:8200/api/v1/da-services/farmers/FR-675618129549/profile" -H "Authorization: Bearer $TOKEN"
curl -s "http://127.0.0.1:8200/api/v1/da-services/farmers/FR-675618129549/plots" -H "Authorization: Bearer $TOKEN"
```

Verified on dev (31 Oct – 1 Nov 2026): 25 approved farmers listed with phone, land, plot count and photo; registration end to end (`PENDING_APPROVAL`, a repeated `client_op_id` returns `DUPLICATE`); 14 regions › zones › woredas › kebeles; about 50 dropdowns across 9 registers.

---

## 6. Known limitations and open items

| Item | Impact | Owner / next step |
|---|---|---|
| Registry ignores `filter_by` | Filtering after the page: a page can come back short and `total` is the registry's figure beyond one page | Registry team (Asmit) |
| Search rows lack phone / land / plots | Two extra calls per farmer, ~20 s first list | Registry list-view config (Asmit) |
| DA profile scope uses the manual farmer link, list uses kebeles | An Automatic-mode DA sees a farmer in the list but gets 403 on the profile | DA Services fix |
| DA edit check compares a kebele name with a kebele code | `can_edit` false for DAs | DA Services fix |
| A registry error is cached for 10 min in the list summary | Phone / land / plots read null for that farmer for 10 min | DA Services fix |
| Scope matches area names exactly | Spelling differences hide farmers; demo DA (Bako Tibe) sees 0 | Use Master Data ids end to end |
| Registry login uses a staff user's password | Not suitable for production | Service account (`client_credentials`) |
| Approval is done in the registry | DA Services shows `PENDING_APPROVAL` until a supervisor approves there | Registry approval flow |
