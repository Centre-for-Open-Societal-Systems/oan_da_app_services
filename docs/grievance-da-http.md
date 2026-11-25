# DA Services to Grievance Service

The frontend calls DA Services. DA Services is a thin proxy: it does not hard-code roles, permissions, or geography scope. Authz is owned by the Grievance Service (via the forwarded FE bearer) and by Frappe session login. DA Services does not store grievance cases.

## Authentication

Calls forward the frontend's `Authorization: Bearer {token}` to the Grievance Service. There is no CSRF header and no site-config service-account login. A missing bearer is an error. Upstream refusals (4xx/5xx) are wrapped and returned to the FE.

Officer actions (resolve, reopen, close, reassign) are not exposed on the DA routes.

## Calls

| DA route | Upstream |
|---|---|
| `POST /api/v1/da-services/grievances` | `POST /v1/grievances` |
| `GET /api/v1/da-services/grievances/{ticket}` | `GET /v1/grievances/{ticket_number}/timeline` |
| `GET /api/v1/da-services/grievances/{ticket}/timeline` | `GET /v1/grievances/{ticket_number}/timeline` |
| `GET /api/v1/da-services/grievances` | `GET /v1/grievances` |
| `GET /api/v1/da-services/grievances/summary` | `GET /v1/grievances/summary` |
| `GET /api/v1/da-services/grievances/options` | `GET /v1/submitters/options` (no bearer), then `GET /v1/grievances/options` |
| `POST /api/v1/da-services/grievances/{ticket}/attachments` | `POST /v1/grievances/{ticket_number}/attachments` (multipart) |
| `GET /api/v1/da-services/grievances/{ticket}/attachments` | `GET /v1/grievances/{ticket_number}/attachments` |
| `GET /api/v1/da-services/grievances/{ticket}/attachments/{attachment_id}/view` | `GET /v1/grievances/{ticket_number}/attachments`, then `GET /v1/attachments/{attachment_id}/view` |
| `POST /api/v1/da-services/grievances/sync/retry` | Offline retry (not finalized yet). |

A successful submit returns `data.ticket_number` as both `ticket_id` and `ticket_number`. There is no `GET /v1/grievances/{ticket_number}`. The case is `GET /v1/grievances/{ticket_number}/timeline`: description, category, type, submitter, SLA, and timeline. Timeline rows map `created_on`, `entry_type`, `author_name`, and `body` to `timestamp`, `action`, `actor`, and `detail`. `sla_consumed_percent` is not on that SLA object.

List requests send `page`, `page_size`, and `sort_order=desc`. Rows are `data.grievances`. `creation` and `modified` become `created_at` and `updated_at`. A row has no `description`, so `subject` is `grievance_type_name` or `grievance_type`. `location` is the display string or hierarchy when the service sends one, otherwise `administrative_unit`, otherwise the `administrative_area` id. Optional filters are `status`, `category`, `location`, `from_date`, `to_date`, and `search`. Row visibility is decided upstream from the FE bearer; DA Services does not narrow by role or geography.

Options call public `GET /v1/submitters/options` for categories, types, submitter types, and channels, then `GET /v1/grievances/options` for queue `statuses`, `departments`, and `submission_channels`. Category and type rows are `{value, label, code}`. A type is listed under a category only when that row names `service_category`. The DA route forwards `service_category`, `category`, `department`, `administrative_area`, `search_country`, `country`, and `include_phone_extensions`.

Summary returns the upstream `data` object unchanged: `cards` of `status`, `label`, `order`, `is_open`, `is_terminal`, and `count`. An HTTP error, or a `200` body whose `status` is `error`, is not returned as a summary.

### Evidence

Evidence is the documents attached to a grievance after it is filed. The files are sent on to the Grievance Service and are not stored in DA Services.

Upload is `multipart/form-data`: one or more files under `files` (the key `file` is accepted too) and an optional `document_type` label. DA Services requires at least one file and checks nothing else. The Grievance Service owns the rules: JPG, PNG, PDF or MP3 (read from the file's content, so a renamed file is refused), 10 MB a file, and 10 files a grievance in total. Its refusal comes back as `400` with its message. One request is all or nothing: if any file is refused, none is stored. The whole request must also fit the site's upload limit (Frappe's `max_file_size`, 25 MB unless raised), so the portal sends one file a request.

A successful upload returns `201` with `data` as the list of new rows: `attachment_id`, `file_name`, `mime_type`, `size_bytes`, `document_type`, `scan_status`, `scanned_at`, `uploaded_at`, and `timeline_entry`. The service names the row `attachment` on upload and `name` in the list; both are `attachment_id` here, and `creation` is `uploaded_at`. A new file is `scan_status: "Pending"` until the service's virus scan finishes, then `Clean` or `Infected`.

The list returns every file, Pending and Infected ones too, oldest first.

View returns the file itself, not JSON: inline by default, a download with `?download=1`. The service reads an attachment by its id alone, so DA Services first checks the attachment is on the ticket in the path (`404` otherwise). The service refuses a file that is not Clean; that comes back as `400` with its message.

When the Grievance Service is down the caller gets `503 SERVICE_UNAVAILABLE` and sends the files again.

Like the other grievance routes, evidence endpoints forward the FE bearer and pass upstream errors through; they do not apply DA Services role or scope checks.

### Submit body

| Upstream field | Source |
|---|---|
| `submission_channel` | Always `Development Agent Assisted` |
| `client_submission_uuid` | `client_op_id`, or a generated UUID |
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

On submit, a timeout, connection error, or HTTP 5xx returns HTTP 503 (`SERVICE_UNAVAILABLE`). An upstream 4xx is returned to the caller. Other upstream faults return HTTP 502. Offline queue / retry is not finalized yet.

## Settings

Set **Grievance Base URL** (`grievance_base_url`) on **DA Services Settings** — same pattern as Master Data Domain. If it is blank, the client raises a configuration error (same idea as an unset master domain).

Example value: `https://grievance-service.development.oanstaging.com` or bare host `grievance-service.development.oanstaging.com`.

`grievance_base_url` is the Grievance **APISIX gateway** host. Public paths are `/v1/...` (no `/api`). The gateway rewrites to Frappe as `/api/v1/...`. Do not put `/api` on the base URL: `{base}/api/v1/...` through the gateway returns 404.
