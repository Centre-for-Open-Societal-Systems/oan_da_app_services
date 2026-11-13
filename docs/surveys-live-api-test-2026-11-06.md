# Surveys live API test, 2026-11-06

Status: **Passed — 38/38 collection and negative live checks, 0 failed.**

> Historical run. The survey contract changed on 2026-11-09 (direct submission only,
> Woreda-wide or selected-DA assignment, multilingual prompts, area filters). See
> [surveys-live-api-test-2026-11-09.md](surveys-live-api-test-2026-11-09.md) for the
> current evidence. Statements below about accepting `Draft` responses are no longer true.

Additional live auth checks (login, refresh, wrong-password) and workbook-content
validation also passed; those are outside the 38-check JSON.

- Collection: `postman/Surveys/OAN-DA-Surveys.postman_collection.json`
- Environment: `postman/Surveys/OAN-DA-Surveys.postman_environment.json`
- Site: `http://da-services.localhost:8000` (health endpoint returned `200`)
- Scope: DA-352 / FR-09d / FSD 3.9 / data-model D28 v1
- Raw results: `/tmp/da_survey_test_results.json`
- Export evidence: `/tmp/da_survey_export.xlsx`

All paths below are relative to `/api/v1/da-services` unless the path starts with
`/api/v1/auth`. Tokens and passwords are not recorded in this report.

## Method

The collection was executed over live HTTP against the local Frappe site. Every
reported live result is from an actual request; no live result is inferred from
unit tests.

The 38 checks in `/tmp/da_survey_test_results.json` cover the endpoint, negative,
idempotency, results and export matrix. Login, token refresh and wrong-password
checks were executed separately over live HTTP and are recorded in the
“Additional live auth checks” section below.

The checked-in Postman environment had an empty `usr`, `pwd`, `other_da_usr`,
`da_usr`, `exec_usr` and all password/token fields. Active RBAC accounts on the
site also had no password hashes. With approval, short-lived JWTs were minted
server-side for the existing active accounts. The documented demo Supervisor
credentials were separately exercised through the real login and refresh
endpoints.

## Accounts and roles used

| Purpose | Account | Role / scope |
|---|---|---|
| Template creation, survey reads, capture, results, export | `zone-sup@da.local` | Supervisor, `ET04` / `ET04-W01` |
| DA reads, DA negative checks | `zone-da1@da.local` | Development Agent, `DA-000001`, `ET04-W01` |
| Cross-DA duplicate | `da2@da.local` | Development Agent, `DA-000002`, `ET04-W01` |
| Executive reads/results/export | `zone-exec@da.local` | Executive, `ET04` / `ET04-Z01` |
| Unassigned-account denial | `sup.demo@da.local` | Supervisor role but no active RBAC assignment |

No OAN Administrator account was used in the live run.

## Prerequisites and test data

The site had no survey templates, responses, DA Kebele Assignments or DA Farmer
References at the start. The following minimum prerequisites were created with
approval:

- Administrative area `ET04-W01-K01` (`Bako 01`) under `ET04-W01`.
- Effective `DA Kebele Assignment` for `DA-000001` to `ET04-W01-K01`.
- Effective `DA Kebele Assignment` for `DA-000002` to `ET04-W01-K01`.
- Active `DA RBAC Assignment` for `da2@da.local` as Development Agent bound to
  `DA-000002`, scope `ET04` / `ET04-W01`.

The configured environment farmer `FR-427495614351` does not exist on this site
and is not one of the farmer mock fixtures. With approval, the run used
`FR-000101` (`farmer-uuid-001`, Kebele `Bako 01`, Woreda `ET04-W01`). The API
cached it as a `DA Farmer Reference` during capture.

## Results summary

| HTTP status | Count |
|---|---:|
| `200` | 15 |
| `201` | 4 |
| `400` | 5 |
| `401` | 2 |
| `403` | 6 |
| `404` | 2 |
| `409` | 4 |
| **Total** | **38** |

All 38 checks matched their expected status. The three additional auth checks are
not included in this 38-check count.

## Additional live auth checks

These were executed live against `/api/v1/auth/*` but are not in the 38-check JSON.

| Check | Request | Observed | Evidence |
|---|---|---|---|
| Login with documented demo Supervisor credentials | `POST /api/v1/auth/login` | `200`; `token_type=Bearer`; `expires_in=900`; roles `["Supervisor"]` | separate live run |
| Refresh token | `POST /api/v1/auth/refresh` | `200`; new access and refresh tokens returned | separate live run |
| Wrong password | `POST /api/v1/auth/login` | `401`; `Authentication failed` | separate live run |

These checks used `sup.demo@da.local`, which has a valid documented demo password
but no active RBAC assignment. The credentials and tokens are not recorded.

## Tested endpoints

### Auth and profile

| Check | Method and path | Role | Expected | Actual | Result |
|---|---|---|---|---:|---|
| Supervisor profile | `GET /me` | Supervisor | 200 | 200 | PASS |
| Executive profile | `GET /me` | Executive | 200 | 200 | PASS |
| Unassigned Supervisor denied | `GET /me` | Supervisor without assignment | 401 | 401 | PASS |
| DA profile | `GET /me` | DA-000001 | 200 | 200 | PASS |

The Executive profile menu included `/surveys`. The unassigned Supervisor token
was rejected by the JWT middleware with `Authentication failed`.

### Templates — Supervisor only (no Admin live cases)

| Check | Method and path | Role | Expected | Actual | Result |
|---|---|---|---|---:|---|
| Create template | `POST /surveys/templates` | Supervisor | 201 | 201 | PASS |
| List templates | `GET /surveys/templates?type=Satisfaction&limit=50&start=0` | Supervisor | 200 | 200 | PASS |
| Get template | `GET /surveys/templates/{id}` | Supervisor | 200 | 200 | PASS |
| Patch template | `PATCH /surveys/templates/{id}` | Supervisor | 200 | 200 | PASS |
| Invalid template type | `POST /surveys/templates` | Supervisor | 400 | 400 | PASS |
| DA cannot create | `POST /surveys/templates` | DA-000001 | 403 | 403 | PASS |
| Executive cannot create | `POST /surveys/templates` | Executive | 403 | 403 | PASS |
| DA cannot list templates | `GET /surveys/templates` | DA-000001 | 403 | 403 | PASS |

Create returned `201` with status `Open`, five parameters, first parameter type
`consent`, and version `FSS-T1-v1.0`. The patch changed `targetFarmers` from 20 to
25. The invalid type returned `VALIDATION_ERROR`.

Admin template operations were not live-tested in this run. They are covered by
the automated suite only.

### Assigned surveys

| Check | Method and path | Role | Expected | Actual | Result |
|---|---|---|---|---:|---|
| List surveys | `GET /surveys?limit=50&start=0` | Supervisor | 200 | 200 | PASS |
| Get survey | `GET /surveys/{id}` | Supervisor | 200 | 200 | PASS |
| Get survey | `GET /surveys/{id}` | DA-000001 | 200 | 200 | PASS |
| Unknown survey | `GET /surveys/DA-SRV-DOES-NOT-EXIST` | Supervisor | 404 | 404 | PASS |

The survey detail included `id`, `name`, `templateVersion`, `parameters` and
`answered`. The unknown survey returned `NOT_FOUND`.

DA assigned-survey listing was not separately live-tested; the live list call
used a Supervisor token. DA list scope is covered by the automated suite.

### Responses — positive path

| Check | Method and path | Role | Expected | Actual | Result |
|---|---|---|---|---:|---|
| Submit Synced response | `POST /surveys/{id}/responses` | Supervisor collecting for DA-000001 | 201 | 201 | PASS |
| Save draft response | `POST /surveys/{id}/responses` | Supervisor collecting for DA-000001 | 201 | 201 | PASS |
| List responses | `GET /surveys/{id}/responses?limit=50&start=0` | Supervisor | 200 | 200 | PASS |
| Idempotent replay of Synced payload | `POST /surveys/{id}/responses` | Supervisor collecting for DA-000001 | 201 | 201 | PASS |

The Synced response returned `DA-SRR-2026-0002116` with canonical farmer
`farmer-uuid-001`. The draft returned `DA-SRR-2026-0002117` with `sync_state =
Draft`. The response list returned both rows.

### Responses — duplicates, idempotency and validation

| Check | Method and path | Role | Expected | Actual | Result |
|---|---|---|---|---:|---|
| Replay same `client_op_id` with different payload | `POST /surveys/{id}/responses` | Supervisor / DA-000001 | 409 | 409 | PASS |
| Duplicate same DA | `POST /surveys/{id}/responses` | Supervisor / DA-000001 | 409 | 409 | PASS |
| Duplicate from another DA | `POST /surveys/{id}/responses` | DA-000002 | 409 | 409 | PASS |
| `client_op_id` reused by another collector | `POST /surveys/{id}/responses` | DA-000002 | 409 | 409 | PASS |
| Invalid farmer | `POST /surveys/{id}/responses` | Supervisor / DA-000001 | 404 | 404 | PASS |
| Farmer outside assigned Kebele | `POST /surveys/{id}/responses` | Supervisor / DA-000001 | 403 | 403 | PASS |
| Invalid rating answer | `POST /surveys/{id}/responses` | Supervisor / DA-000001 | 400 | 400 | PASS |
| Server-only `sync_state` | `POST /surveys/{id}/responses` | Supervisor / DA-000001 | 400 | 400 | PASS |
| Missing consent for Synced | `POST /surveys/{id}/responses` | Supervisor / DA-000001 | 400 | 400 | PASS |

Error codes observed:

- Duplicate and idempotency conflicts: `DUPLICATE_ENTRY`.
- Invalid farmer: `NOT_FOUND`.
- Out-of-Kebele capture: `PERMISSION_DENIED`.
- Invalid rating, server-only state and missing consent: `VALIDATION_ERROR`.

The exact idempotent replay returned the original response ID
`DA-SRR-2026-0002116`. After all duplicate attempts, the response list still
contained exactly two rows; no duplicate rows were written.

The live run did not test the Woreda-mismatch capture negative; that case is
covered by the automated suite.

### Results

| Check | Method and path | Role | Expected | Actual | Result |
|---|---|---|---|---:|---|
| Basic aggregates | `GET /surveys/{id}/results` | Supervisor | 200 | 200 | PASS |
| Basic aggregates | `GET /surveys/{id}/results` | Executive | 200 | 200 | PASS |
| DA denied | `GET /surveys/{id}/results` | DA-000001 | 403 | 403 | PASS |

The aggregate response contained `collected`, `by_status` and `parameters`.
Observed values: `collected=1`, `by_status={"Draft": 1, "Synced": 1}`,
`parameters=2`.

### XLSX export

| Check | Method and path | Role | Expected | Actual | Result |
|---|---|---|---|---:|---|
| Export as XLSX | `GET /surveys/{id}/responses/export` | Executive | 200 | 200 | PASS |
| Export as XLSX | `GET /surveys/{id}/responses/export` | Supervisor | 200 | 200 | PASS |
| DA denied | `GET /surveys/{id}/responses/export` | DA-000001 | 403 | 403 | PASS |
| Unassigned Supervisor denied | `GET /surveys/{id}/responses/export` | Supervisor without assignment | 401 | 401 | PASS |

### Close and closed-window case

| Check | Method and path | Role | Expected | Actual | Result |
|---|---|---|---|---:|---|
| Patch template to `Closed` | `PATCH /surveys/templates/{id}` | Supervisor | 200 | 200 | PASS |
| Submit after close | `POST /surveys/{id}/responses` | Supervisor / DA-000001 | 400 | 400 | PASS |

The closed-window submission returned `VALIDATION_ERROR` with the message
`This survey window is closed.`

The before-window and opening-boundary timing cases were not live-tested; they
are covered by the automated suite.

## XLSX export verification

The export was written to `/tmp/da_survey_export.xlsx`.

- Size: 5,565 bytes.
- SHA-256: `1f1cd54b3acecf7619955f1b79a0efc3436f919db0d880ac70377ce046e2ff27`.
- Valid ZIP: yes.
- ZIP parts: 9.
- Workbook part present: yes.
- Worksheet part present: yes.
- Sheet: `Survey Responses`.
- Rows including header: 3.
- Columns: 19.

Response headers:

`Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`

`Content-Disposition: attachment; filename=survey-DA-SRV-2026-0002112-responses.xlsx`

Workbook columns:

```text
Response ID, Survey ID, Template Version, Farmer ID, Farmer Registry ID,
Collector DA ID, Consent, Sync State, Started At, Completed At,
Region, Zone, Woreda, Kebele, p1: consent, p2: Farmer ID,
p3: Kebele, p4: advice usefulness, p5: advice applied
```

Exported rows:

| Response ID | Sync State | Farmer ID | Registry ID | Collector DA | Consent | p4 | p5 |
|---|---|---|---|---|---|---|---|
| `DA-SRR-2026-0002116` | Synced | `farmer-uuid-001` | `FR-000101` | `DA-000001` | granted | 4 | Yes, fully |
| `DA-SRR-2026-0002117` | Draft | `farmer-uuid-001` | `FR-000101` | `DA-000001` | blank | 3 | blank |

The Draft row has blank consent and blank `p5` because the collection's Draft
payload sends only `p4` and no consent. This is the stored payload, not an export
defect.

## Test records created

Records were intentionally left in place; no cleanup was run.

| Record | Value |
|---|---|
| Survey template | `DA-SRV-2026-0002112` |
| Template name | `Meher season farmer satisfaction` |
| Template type | `Satisfaction` |
| Window | `2026-11-03` to `2026-11-30` |
| Final target | 25 after patch |
| Template version | `FSS-T1-v1.0` |
| Final status | `Closed` |
| Synced response | `DA-SRR-2026-0002116` |
| Draft response | `DA-SRR-2026-0002117` |
| Collector DA | `DA-000001` |
| Canonical farmer | `farmer-uuid-001` / `FR-000101` |

The API also created the cached `DA Farmer Reference` for `farmer-uuid-001`.

## Live-tested v1 functionality

The following were exercised by actual HTTP requests in this run:

- Additional live auth checks: login `200`, refresh `200`, wrong password `401`.
- JWT middleware and active-RBAC-assignment enforcement: active
  Supervisor/DA/Executive `200`; unassigned Supervisor `401`.
- Supervisor template create, list, get, patch and close.
- Template creation with `woreda=ET04-W01`; the resulting survey/export used
  Woreda `ET04-W01`.
- Supervisor survey list/detail and DA survey detail.
- Response capture in `Synced` and `Draft` states.
- Farmer ID resolution and canonicalisation (`FR-000101` ->
  `farmer-uuid-001`).
- Outside-Kebele capture rejection; successful capture stored the farmer's
  `ET04-W01` location.
- Same-DA and cross-DA duplicate protection.
- Response `client_op_id` idempotency: identical replay returns the same row;
  changed payload or different collector conflicts.
- Post-close response rejection.
- Basic results aggregates for Supervisor and Executive; DA denied.
- XLSX export for Executive and Supervisor; DA `403`; unassigned `401`;
  workbook content validated.
- DA restrictions on template create/list, results and export; Executive
  restriction on template create.

## Automated-test coverage only (not live-tested in this run)

The following are covered by the automated suite but were not exercised as live
HTTP requests in this run:

- Admin template create and explicit target selection
  (`test_admin_can_select_explicit_target_but_still_needs_a_valid_hierarchy`).
- Forged/mismatched client Region/Zone rejection and out-of-scope Woreda
  rejection (`test_forged_woreda_is_rejected`,
  `test_forged_or_mismatched_region_and_zone_are_rejected`).
- Before-window and opening-boundary response timing
  (`test_response_before_window_open_is_rejected`,
  `test_response_on_opening_boundary_is_allowed`).
- Farmer outside the survey Woreda
  (`test_farmer_outside_survey_woreda_is_denied_and_not_stored`).
- DA assigned-survey listing (`test_list_scope_per_role`).
- Template `client_op_id` idempotency/conflict and concurrent replay
  (`test_client_op_id_idempotent_on_create` and related tests).
- Dual-role live-officer fallback and expired-grant edge cases.
- Question-lock and template-version edge cases.

These are covered by `da_services/tests/test_surveys.py`; they were not re-run in
this live session.

## Remaining D28 scope gaps

The following are not implemented in v1 and are not failures of this run:

- ODK Central sync.
- Separate Template vs Deployment with Woreda approval.
- Per-DA `Survey due` task creation on publish.
- Part 2 analytics dashboards beyond `/results`.
- Authoring skip-logic, lookup questions or conditional parameters in the create
  modal.
- Full questionnaire authoring controls beyond the current `required`, type,
  rating range, text length and option membership checks.

## Pending business decisions

These are documented as deliberate v1 limitations:

- Draft lifecycle: update/submit behaviour and whether a draft reserves a farmer.
- Publish workflow: create currently equals publish.
- Advanced field validation: min/max, regex, dates and skip-logic authoring.
- Declined-response rules: reservation and `collected` counting.
- Response correction: submitted responses remain immutable.
- Excel filtering, PII redaction, column selection, async export and large-survey
  memory behaviour.
- Clock-skew tolerance for client timestamps.

## Limitations of this run

- The imported environment's `base_url` is `http://127.0.0.1:8200`; the actual
  local bench listened on port **8000**. The run used port 8000.
- The imported environment contained no credentials. Active accounts were
  authenticated with short-lived server-minted JWTs, not password login.
- The configured farmer `FR-427495614351` was absent; `FR-000101` was used with
  approval.
- No OAN Administrator account was exercised live; Admin template operations are
  automated-test coverage only.
- The site is a local development/test site (`developer_mode=1`,
  `allow_tests=True`). Minimum prerequisite rows were created for the run.
- One template and two responses were exercised. This is not a load, concurrency
  race, volume, PII-redaction or async-export test.
- The XLSX check is structural and content-based for this small workbook; it is
  not a performance or security review.
- Evidence files remain under `/tmp` and were not committed.

## Repository state

No application source code, migrations, Git branch, commit or push changes were
made by this test run. The repository already had uncommitted DA-352 changes
before testing; this run did not alter them.
