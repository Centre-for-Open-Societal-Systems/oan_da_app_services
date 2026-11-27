# A2C Credit integration

DA Services does not store Credit applications. The Credit page calls DA
Services, which validates the caller, forwards the caller's own Bearer token to
A2C, and returns the A2C JSON body unchanged. This is a read-only wrapper: there
is no lead creation, consent, CIC, submission or status write in this change.

A2C has supplied confirmed response fields for the summary and list endpoints.
The successful documents response is still unverified. The wrapper does not
reshape the body, invent field names, or translate statuses. A2C remains the
only owner of the data shape.

## Upstream calls

| DA Services route | A2C call | Forwarded |
| --- | --- | --- |
| `GET /api/v1/da-services/credit/applications/summary` | `GET /v1/loan-applications/summary` | nothing |
| `GET /api/v1/da-services/credit/applications?status=&page=` | `GET /v1/loan-applications?status={status}&page={page}` | `status`, `page` only |
| `GET /api/v1/da-services/credit/applications/{application_id}/documents` | `GET /v1/loan-applications/{application_id}/documents` | the path id only |

`page` is optional and defaults to `1`. It must be a positive integer. `status`
is optional and is forwarded as sent, but it must be one safe query value:
letters, digits, spaces, `_` and `-` only. No undocumented query parameter is
forwarded, even when the caller sends it.

`application_id` must be one URL path segment: letters, digits, `.`, `_` and
`-`, starting and ending with an alphanumeric character, at most 128 characters.
Values containing `/`, `\`, `?`, `#`, `%2F`, whitespace or `..` are rejected
before any upstream request.

Successful responses use the normal DA Services envelope with the upstream body
under `data`:

```json
{
  "status": "success",
  "message": "Success",
  "data": { "...": "the A2C JSON body, unchanged" },
  "meta": { "api_version": "v1" }
}
```

The complete upstream body is nested unchanged under `data`. The wrapper does
not rename, remove, flatten, or reconstruct upstream fields, and unknown
additional upstream fields are preserved.

### Confirmed summary fields

The summary response contains `data.stages`, `data.tab_counts`, and `data.total`,
plus top-level `message`, `meta`, `request_id`, and `status`. Because the whole
upstream body is nested under the DA `data` field, clients read these at
`data.data.stages`, `data.data.tab_counts`, and so on.

### Confirmed list fields

The list response contains:

- `data`: an array of application records.
- Record fields: `application_id`, `bank`, `creation`, `first_name`,
  `is_successful`, `is_terminal`, `kebele`, `last_name`, `lead_id`,
  `loan_amount`, `loan_product`, `loan_product_name`, `loan_type`,
  `phone_number`, `region`, `sequence`, `stage_id`, `stage_label`, `status`,
  `step`, `submitted_at`, and `woreda`.
- `pagination`: `has_next`, `limit`, `page`, `total`, and `total_pages`.
- Top-level `message`, `meta`, `request_id`, and `status`.

The wrapper preserves this structure under its `data` field. Pagination remains
inside the preserved upstream body; it is not copied into a separate DA
envelope field.

### Documents

A successful documents response has not been verified. The only captured
documents response is an upstream `NOT_FOUND` error. The wrapper maps that to
DA `404 NOT_FOUND` and does not echo the upstream error body. Do not assume a
successful documents schema or a `file_attachment` field.

## Authentication and configuration

The caller's current `Authorization` header is read from the incoming DA
Services request for each upstream call and sent to A2C. No separate A2C login,
token cache, cookie jar or CSRF value is used or stored. The client holds only
the base URL and timeout, so a token from one request cannot leak into the next.
Only `Accept: application/json` and the caller's `Authorization` are sent.

Site configuration (`site_config.json`):

| Key | Meaning |
| --- | --- |
| `a2c_backend` | `http` for the Credit reads. The local default is `mock`. |
| `a2c_api_base_url` | A2C base URL, for example `https://a2c-service.development.oanstaging.com`. Must be `http` or `https`. |
| `a2c_timeout_seconds` | Optional positive timeout, default `30`. |

With `a2c_backend: "mock"` the Credit reads answer `503`. The mock backend does
not fabricate loan applications or documents: fake national loan data is worse
than an explicit unavailability.

## Errors

| Situation | DA Services status | Code |
| --- | --- | --- |
| Invalid `page`, `status` or `application_id` | `400` | `VALIDATION_ERROR` |
| Caller lacks the required role | `403` | `PERMISSION_DENIED` |
| A2C answers `404` | `404` | `NOT_FOUND` |
| A2C answers `401`/`403` or another `4xx` | `502` | `BAD_GATEWAY` |
| A2C answers non-JSON or an unusable shape | `502` | `BAD_GATEWAY` |
| A2C is unreachable, times out, or answers `5xx` | `503` | `SERVICE_UNAVAILABLE` |
| `a2c_backend` is `mock` or the base URL is missing | `503` | `SERVICE_UNAVAILABLE` |

An A2C `401`/`403` is not treated as a DA Services session failure. The caller's
token may be valid here and refused there, so the wrapper reports an upstream
rejection rather than signing the user out.

Only technical context is logged on failure: the action, the upstream path and
the HTTP status (or exception class). Tokens, request headers, response bodies
and farmer data are never logged.

## Access scope limitation

A2C does not document a verified way to limit `GET /v1/loan-applications` or the
summary to the caller's Region / Zone / Woreda, and the API takes no Farmer-ID
filter. DA Services therefore does not invent a farmer mapping or pretend the
upstream list is scoped.

Until A2C provides an enforced scope, all three routes require one of the roles
that are already unrestricted in DA Services (`OAN Administrator`,
`System Manager`, `Administrator`). A Development Agent, Supervisor or Executive
is refused. This is intentionally narrower than other DA reads and must be
revisited when A2C exposes scope enforcement. The single gate lives in
`da_services/services/credit.py` as `READ_ROLES`.

## Out of scope

This change does not implement:

- application creation, lead creation, consent or CIC checks;
- document upload, notes or statements;
- submission or status updates;
- `POST /api/v1/a2c/applications/check-active`;
- Farmer-ID filtering or a local Credit mirror;
- changes to `has_active_application(s)` used by pending actions (DA-345).

## Tests

`da_services/tests/test_a2c_credit.py` stubs `requests.get` and the incoming
Authorization header. It covers the three paths, query forwarding, page/status
validation, path-injection rejection, per-request Bearer forwarding and absence
of stored tokens, upstream `401`/`403`/`404`/`5xx`, timeout and connection
errors, malformed JSON, missing configuration, mock-backend refusal, role gate,
error envelopes, confirmed summary/list pass-through, and preservation of
unknown upstream fields. The documents success response is not tested because it
is unverified; documents `NOT_FOUND` mapping is covered.

From the bench directory:

```bash
bench --site da-services.localhost run-tests \
  --app da_services --module da_services.tests.test_a2c_credit
```
