# DA Services to Grievance Service

The frontend calls DA Services. DA Services checks the caller's role, then calls the Grievance Service. DA Services does not store grievance cases.

`grievance_backend` defaults to `mock`. Set it to `http` to use the live service. The mock treats a repeated `client_submission_uuid` as the original ticket.

## Authentication

Later grievance calls send only `Authorization: Bearer {token}`. There is no CSRF header. The token is cached until 30 seconds before its JWT `exp`. `GET {base_url}/v1/auth/me` must include `Grievance Assisted Submitter`.

`grievance_iam.login_key` selects the login:

- `local`: `POST {base_url}/v1/auth/login` with `{"usr","pwd"}`. The token is `data.access_token`.
- Any other value, including the default `agent`: one session carries cookies through the IAM login.
  1. `POST {base_url}/v1/auth/iam/login` with `key`, `provider`, and `return_url`. `return_url` is `grievance_iam.return_url`, or the grievance base URL when that is unset. The response supplies `redirect_url` and `cookie_prefix`.
  2. `GET` the redirect URL and follow redirects. Read the `kc-form-login` form action.
  3. `POST` that action as `application/x-www-form-urlencoded` with `username`, `password`, and an empty `credentialId`. Do not follow redirects. The `Location` header is the callback.
  4. `GET` the callback without following redirects. The access token is the `{cookie_prefix}X-Access-Token` cookie, or `X-Access-Token` when that cookie is absent.

`grievance_api_token` is used only when `grievance_iam` has no username and password.

Officer actions (resolve, reopen, close, reassign) are not exposed on the DA routes.

## Calls

| DA route | Upstream |
|---|---|
| `POST /api/v1/da-services/grievances` | `POST /v1/grievances` |
| `GET /api/v1/da-services/grievances/{ticket}` | `GET /v1/grievances/{ticket_number}`, then `GET /v1/grievances/{ticket_number}/timeline` on 404 |
| `GET /api/v1/da-services/grievances/{ticket}/timeline` | `GET /v1/grievances/{ticket_number}/timeline` |
| `GET /api/v1/da-services/grievances` | `GET /v1/grievances` |
| `GET /api/v1/da-services/grievances/options` | `GET /v1/submitters/options` (no bearer token) |
| `POST /api/v1/da-services/grievances/sync/retry` | Retries a failed DA Integration Event. DA Services Admin only. |

A successful submit returns `data.ticket_number` as both `ticket_id` and `ticket_number`. The timeline read is the case when the bare ticket GET is missing: summary, submitter, SLA, and timeline. Timeline rows map `created_on`, `entry_type`, `author_name`, and `body` to `timestamp`, `action`, `actor`, and `detail`.

List requests send `page`, `page_size`, and `sort_order=desc`. Items are `data.items`. The total is `data.pagination.total_count`. Optional filters are `status`, `service_category`, `grievance_type`, `search`, `administrative_area`, `from_date`, and `to_date`. `complainant_id` is not sent: a Development Agent's list is limited by the agent token. Scoped callers who are not Development Agents forward `region` or `woreda`.

Options map `service_categories` and `grievance_types` into `categories`, and also return `priorities` and `statuses`.

### Submit body

| Upstream field | Source |
|---|---|
| `submission_channel` | Always `Development Agent Assisted` |
| `client_submission_uuid` | `client_op_id`, or a UUID generated once and stored on the queued payload |
| `submitter_type` | `submitter_type`, otherwise `Individual Farmer` |
| `submitter_name` | `submitter_name`, otherwise the caller's name |
| `contact_mobile` | `contact_mobile` |
| `administrative_area` | `administrative_area` |
| `administrative_unit` | `administrative_unit` |
| `service_category` | `service_category`, otherwise `category` |
| `grievance_type` | `grievance_type`, otherwise `subcategory` |
| `description` | `description`. `subject` is prepended when it is not already in the text. The upstream service requires at least 20 characters. |
| `desired_outcome` | `desired_outcome` |
| `is_anonymous` | `0` unless the request sets it |
| `consent_given` | `1` unless the request sets it to false |

`priority`, `raised_for`, `farmer_id`, `complainant_id`, and attachments are not sent. `subject` is required on the DA route. `farmer_id` is required locally when `raised_for` is `Farmer`.

A repeated `client_submission_uuid` does not create a second case. The service answers `This submission has already been filed as {ticket_number}`, and the wrapper returns that ticket with `sync_state: synced`.

On submit, a timeout, connection error, HTTP 5xx, or unreachable login is queued (`sync_state: pending`). An upstream 4xx is returned to the caller and is not queued. Other upstream faults return HTTP 502 and are not queued.

## Site config

```json
{
  "grievance_backend": "http",
  "grievance_base_url": "https://grievance.example.com/api",
  "grievance_timeout_seconds": 20,
  "grievance_iam": {
    "login_key": "agent",
    "return_url": "https://grievance.example.com/api",
    "username": "<service account>",
    "password": "<secret>"
  }
}
```

Use `login_key` `local` when the grievance site only offers password login. Keep the password in `site_config.json`. Do not commit it.
