# Surveys live API test, 2026-11-09

Status: **Passed — 47/47 live checks, 0 failed.**

This run revalidates the DA-352 changes: direct submission, Woreda-wide / selected-DA
assignment, multilingual prompts, administrative-area filters, complete XLSX export and
selected-assignment switching. It supersedes the 2026-11-06 run for the current contract.

- Collection: `postman/Surveys/OAN-DA-Surveys.postman_collection.json`
- Environment: `postman/Surveys/OAN-DA-Surveys.postman_environment.json`
- Site: `http://da-services.localhost:8000` (`Host: da-services.localhost`)
- Raw results: `/tmp/da352_live_results.json`, `/tmp/da352_live_assignment_switch.json`
- Export evidence: `/tmp/da352_live_export.xlsx`
- Automated evidence: full `bench --site da-services.localhost run-tests --app da_services`
  run — **686 integration tests + 114 unit tests passed** (after updating the Kebele-scope
  regression to expect the new 403 behaviour). Targeted modules:
  `test_surveys` 81, `test_visits` 15, `test_farmers_api` 27,
  `test_cross_da_visibility` 10, `test_surveys_export` 6,
  `test_kebele_farmer_scoping` 15.

No site migration was executed for this run. The `DA Survey Parameter.prompts_json` column
was already present (`frappe.db.has_column(...) == true`).

## Method

Requests were sent over live HTTP. Short-lived JWTs were minted server-side for existing
active accounts; no password or token is recorded here. The local disposable test site had
no survey rows, so temporary local prerequisites were created with approval-equivalent test
setup: Kebele `ET04-W01-K01` (`Bako 01`), Effective `DA Kebele Assignment` rows for
`DA-000001` / `DA-000002`, and an active DA RBAC assignment for `da2@da.local` bound to
`DA-000002` in `ET04-W01`.

| Purpose | Account | Scope |
|---|---|---|
| Create templates, capture, area checks | `zone-sup@da.local` | Supervisor, `ET04-W01` |
| Selected-DA visibility and farmer filters | `zone-da1@da.local` | DA-000001 |
| Unassigned-DA negative check | `da2@da.local` | DA-000002 |
| Export | `zone-exec@da.local` | Executive, `ET04-Z01` |

## Positive checks

| Check | Request | Observed |
|---|---|---|
| Create Woreda-wide template with translations | `POST /surveys/templates` | `201`; `assignmentMode=Woreda`; `assignedDaIds=[]`; `p4.translations` has `Amharic` + `Afaan Oromoo` |
| Create selected-DA template | `POST /surveys/templates` with `assignmentMode=Selected`, `assignedDaIds=["DA-000001"]` | `201`; mode and DA list echoed |
| Get selected template | `GET /surveys/templates/{id}` | `200`; assignment mode, DA list and translations round-trip |
| Assigned DA lists surveys | `GET /surveys` | `200`; sees both Woreda-wide and selected templates |
| Unassigned DA lists surveys | `GET /surveys` | `200`; sees Woreda-wide, not selected |
| Assigned DA survey detail | `GET /surveys/{id}` | `200` |
| Submit completed response | `POST /surveys/{id}/responses` (`Synced`) | `201`; canonical farmer and `sync_state=Synced` |
| Response Woreda filter | `GET /surveys/{id}/responses?woreda=ET04-W01` | `200`; `meta.total=1` |
| Response Kebele filter | `GET /surveys/{id}/responses?kebele=ET04-W01-K01` | `200`; `meta.total=1` |
| Complete XLSX export | `GET /surveys/{id}/responses/export` | `200`; XLSX contains every parameter column and stored answers |

Live IDs: main template `DA-SRV-2026-0014117`, selected template
`DA-SRV-2026-0014118` (plus earlier disposable runs).

## Negative and security checks

| Check | Request | Observed |
|---|---|---|
| Draft submission rejected | `POST /surveys/{id}/responses`, `sync_state=Draft` | `400` |
| Unassigned DA survey detail | `GET /surveys/{selected_id}` as DA-000002 | `403` |
| Out-of-scope response Woreda filter | `GET /surveys/{id}/responses?woreda=ET04-W09` as Supervisor `ET04-W01` | `403` |
| Unknown Kebele filter | `GET /surveys/{id}/responses?kebele=ET04-W01-K99` | `400` |
| Out-of-scope visit Woreda filter | `GET /visits?woreda=ET04-W09` | `403` |
| Out-of-scope farmer Woreda filter | `GET /farmers?woreda=ET04-W09` as DA-000001 | `403` |
| Out-of-scope farmer Kebele filter | `GET /farmers?kebele=ET04-W01-K02` as DA-000001 | `403` |

## Assignment switching

`PATCH /surveys/templates/{id}` was exercised live on the selected template:

1. `assignmentMode=Woreda` → `200`; mode `Woreda`, DA list empty; previously unassigned
   DA-000002 can open the survey.
2. `assignmentMode=Selected`, `assignedDaIds=["DA-000001"]` → `200`; mode and DA list
   restored; DA-000002 is blocked again with `403`.

Marker evidence for `survey.assignment:DA-SRV-2026-0014118`:

| Marker task | DA | Status |
|---|---|---|
| `DA-TSK-2026-0014119` | `DA-000001` | `Cancelled` |
| `DA-TSK-2026-0014121` | `DA-000001` | `Open` |

## Notes and residual risk

- The live run created local disposable templates, responses and marker tasks. They are not
  production data.
- Historical `Draft` rows can no longer be created through the API; they remain readable
  and are included in export, as covered by automated tests.
- No response-correction or declined-response workflow was added.
