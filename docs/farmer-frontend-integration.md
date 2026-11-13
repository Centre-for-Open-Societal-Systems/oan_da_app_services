# Farmer screens: frontend integration

The frontend calls only DA Services. DA Services reads and writes farmers in the Farmer Registry and returns one flat shape per screen.
For the backend side (registry calls, scope, cache) see `farmer-registry-integration.md`.

Postman: `postman/Farmers/OAN-DA-Farmers.postman_collection.json` and `postman/Farmers/OAN-DA-Farmers.postman_environment.json`.
Import both, pick the "OAN DA Farmers local" environment, fill `base_url`, `usr` and `pwd`, then run **0. Sign in › Login**. List farmers stores `farmer_id` for the requests after it.

## Setup

| | |
|---|---|
| Base URL | `http://127.0.0.1:8200` on the backend machine; `http://<backend IP>:8200` from another laptop on the same Wi-Fi |
| Reachability | `GET {base_url}/api/method/ping` returns `{"message":"pong"}` |
| Route prefix | `/api/v1/da-services` |

DA-registry-frontend proxies `/api/v1/*` to `API_BASE_URL`, so the browser needs no CORS. Put this in `.env.local` and restart `npm run dev`:

```
API_BASE_URL=http://<backend IP>:8200
```

Ask the backend dev for the test accounts. Use an Admin account to see every farmer. A DA sees only farmers in the DA's assigned kebeles. A Supervisor sees farmers in the supervisor's woreda.

## Sign in

| Method | Route | Notes |
|---|---|---|
| POST | `/api/v1/auth/login` | Body `{"usr", "pwd"}` (`username` / `password` give 400). Returns `access_token` (15 minutes), `refresh_token`, `user`, `roles` |
| POST | `/api/v1/auth/refresh` | Body `{"refresh_token"}`. Keep the new `refresh_token` it returns |
| GET | `/api/v1/da-services/me` | Role, role label, the menu paths that role may open, scope |

Every other call sends `Authorization: Bearer <token>`. On a 401: refresh once and retry, then go to login.

**SSO on develop.** Since commits `0733869` and `85a55bc`, DA Services forwards the caller's `Authorization` and `X-CSRF-Token` headers to the Farmer Registry. The farmer routes therefore need the Keycloak staff token from the SSO login, plus its CSRF value in `X-CSRF-Token`:

- Without `X-CSRF-Token`, they return `Missing Authorization or X-CSRF-Token header`.
- With the local login token, the registry refuses it with `Unknown Issuer`.

Checked on 2 Nov 2026. How the frontend obtains the SSO token and CSRF value is still to be agreed with the backend team.
In Postman, set `access_token` and `csrf_token` in the environment; the collection adds both headers to every request.

## Response format

```json
{ "status": "success", "message": "Success", "data": { }, "meta": { "api_version": "v1" }, "request_id": "..." }
{ "status": "error", "code": "VALIDATION_ERROR", "message": "Validation failed", "details": { "usr": "Field required" } }
```

| HTTP | Meaning | Show |
|---|---|---|
| 400 | Bad input; `details` names the fields | the `details` texts, else `message` |
| 401 | Token missing or expired | refresh, else login |
| 403 | The farmer is outside the user's scope | "You don't have access to this farmer" |
| 404 | Farmer not found | not-found state |
| 502 / 503 / 504 | Farmer Registry did not answer | "Registry unavailable, try again" |

## Screen to route

Every `{id}` takes the registry id or the Farmer-ID (`FR-…`).

| Screen | Method | Route |
|---|---|---|
| My Farmers table and search box | GET | `/farmers?search_text=FR-&page=1&page_size=50` |
| Profile header and overview | GET | `/farmers/{id}/profile` |
| Holdings tab | GET | `/farmers/{id}/plots` |
| Avatar | GET | `photo_url` from the list, or `/farmers/{id}/photo` |
| Edit button | GET | `/farmers/{id}/permissions` |
| Visit history | GET | `/farmers/{id}/visits` |
| Change requests | GET | `/farmers/{id}/change-requests?status=&tab=` |
| Register wizard, dropdowns | GET | `/registries/farmer/field-options` |
| Register wizard, location cascade | GET | `/registries/farmer/places?level=region\|zone\|woreda\|kebele&parent=` |
| Register wizard, form layout | GET | `/registries/farmer/intake-form` |
| Register wizard, ID document | POST | `/farmers/registrations/documents` (multipart `file`) |
| Register wizard, submit | POST | `/farmers` |
| Registration status | GET | `/farmers/registrations/{submission_id}` |
| Edit farmer | PATCH | `/farmers/{id}` |
| Pending edits | GET | `/farmers/{id}/update-status` |

## My Farmers list

One route serves the table and the search box. Send `search_text=FR-` when the page opens; every Farmer-ID starts with it. While the user types, send the typed name, Farmer-ID or phone, debounced by about 400 ms.

```json
{
  "farmers": [
    {
      "farmer_id": "38ef1bcb-a359-45b4-b813-dd0d2b05c864",
      "reg_id": "FR-675618129549",
      "farmer_name": "Aster Mulugeta Assefa",
      "phone": "+251964815374",
      "kebele": "Lekatit",
      "woreda": "Shire Endaslasie town",
      "region": "Tigray",
      "total_land_hectares": 4.25,
      "total_plots": 2,
      "photo_url": "https://minio-farmer.development.oanstaging.com/documents/...",
      "registration_state": "APPROVED",
      "registered_at": "2026-10-30 14:16:40.292811"
    }
  ],
  "total": 25,
  "page": { "page": 1, "page_size": 50, "number_of_pages": 1, "filtered_client_side": false },
  "source": "registry"
}
```

- `phone` and `photo_url` are `null` when the registry has none. Show a dash for the phone, and initials for the photo.
- Only approved farmers appear. A pending registration has no Farmer-ID yet.
- The first call after a while takes about 20 s, while DA Services reads each farmer's record. Repeat calls within 10 minutes take under a second. Show a loading state.

## Profile, plots and photo

- **Profile** returns `name`, `phone` (without `+251`), `email`, `gender`, `education_level`, location, `total_land_area`, `land_ownership`, `household_size`, `last_visit_date`, `can_edit` and `incomplete_sections`. Show "partial data" for each section listed in `incomplete_sections`.
- **Plots** returns `plots[]` with `area_in_hectare`, `land_ownership_type`, `soil_fertility` and `current_land_use`. These are registry codes (`OWNER`, `SF_HIGH`, `GRAZING`); show labels for them.
- **Photo**: `photo_url` goes straight into `<img src>` and needs no token. The link expires after about an hour; when the image fails, call the list or `/photo` again.

Saved responses for each of these are in the Postman collection.

## Register a farmer

1. Load the dropdowns from `field-options` (`{register: {field: [{value, label}]}}`). Send `value` and show `label`.
2. Fill the location one level at a time from `places`, passing the chosen `id` as `parent` for the next level. The kebele `id` (for example `ET04080101`) is the `kebele` in the body.
3. Upload the ID document. The response's `document_id` goes into the body as `identity_document_id`.
4. `POST /farmers`. The body is in the collection. Use a new `client_op_id` per registration: a repeat returns the first result (`DUPLICATE`) and does not create a second record.

The response has `submission_id` and `status: PENDING_APPROVAL`. A `400` names the missing fields in `details`. The farmer gets a Farmer-ID and appears in the list only after a supervisor approves in the registry.

## Known gaps

| Item | Effect on the UI |
|---|---|
| SSO token and CSRF for the frontend not yet agreed | Farmer routes cannot be called with the local login token on develop |
| `permissions`, `visits` and `update-status` returned 500 on the local develop stack, 2 Nov 2026 | Cause not checked yet; may be the same SSO header issue |
| The dev registry ignores its own filters | For a Woreda / Kebele-scoped caller DA Services reads the whole result (up to 20 registry pages of 500), filters it, and counts `total` and `number_of_pages` itself, with `filtered_client_side: true`. Pages are full; only the last can be short. An unscoped Admin search gets the registry's page and figures |
| Area scope matches names exactly | A demo DA whose kebele has no registry farmers sees an empty list |
