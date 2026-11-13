# DA Services API contract from the UI prototype

Source: `DA-registry-frontend` (Next.js 16, commit `7bd6a14`, local clone `C:\Users\shail\DA-registry-frontend`,
served on the staging URL `da-registry-development.oanstaging.com`). Read in full on 6 Oct 2026: `src/lib/rbac.ts`
(roles, navigation), every `src/features/*` types and mock data file, and the route components under
`src/app/(dashboard)/`. The prototype makes **zero network calls**: all data is mock constants, form drafts live
in `sessionStorage`, sign-in matches five demo accounts in the browser.

This document maps every screen a Development Agent can reach to the API it needs, the field names the
frontend already uses, the doctype behind it (names from `data-model.md`) and the owning system. Field names
below are the frontend's TypeScript names; the API returns `snake_case` versions of the same names unless a
column says otherwise.

Legend for **Source**: `ours` = DA Services doctype; `Registry` = DA Registry (OpenG2P, via adapter);
`Farmer Registry`, `Grievance`, `ODK`, `Agrilearn`, `Credit`, `Land Registry`, `SMS` = external systems;
`derived` = computed at read time; `device` = stays on the device.

## 1. Cross-cutting contracts

### 1.1 Roles, session and navigation

Frontend role enum (`lib/rbac.ts`): `DA | Supervisor | Executive | Admin | CommsOfficer`, with labels
"Development Agent", "Supervisor (Woreda)", "Executive (Regional)", "OAN / ATI Administrator",
"Communications Officer". Our Frappe roles map 1:1 (Development Agent, Supervisor, Executive, OAN
Administrator, Communications Officer). The auth store keeps `user {id, name, email}` plus one `role`.

`GET /api/v1/da-services/me` therefore needs, in addition to what it returns today:

| Field | Type | Why |
|---|---|---|
| `role` | one of the five codes above | the frontend switches dashboards and nav on a single role |
| `scope_label` | string, e.g. "Own record · Bako Tibe", "Woreda · Bako Tibe", "Region · Oromia (read)" | sidebar footer (`ROLE_SCOPES`) |
| `agent.da_id`, `agent.full_name`, `agent.kebele`, `agent.woreda`, `agent.region`, `agent.phone`, `agent.specialisation`, `agent.education_tier`, `agent.joined_at`, `agent.farmer_count`, `agent.active_status` | from Registry projection | header, footer, My Teams, Assignments |
| `menu` | list of nav hrefs | must use the frontend's hrefs (next table), not our current section names |

Frontend nav (`NAV_GROUPS`), DA items in bold:

| Group | href | Roles |
|---|---|---|
| | **/dashboard** | all |
| Farmer Management | **/farmers**, **/beneficiaries**, **/grievances** | DA, Supervisor (+Executive on /farmers, +Admin on the other two) |
| Agent Registry | **/my-teams** | DA only |
| | /agents, /visits, /assignments, /approvals, /registry-sync | officers (Supervisor sees /visits and /assignments) |
| | **/feedback** | DA, Supervisor, Admin |
| Communication | **/knowledge**, **/broadcast** | all / DA, Comms, Admin |
| | /alerts | Comms, Admin, Supervisor |
| Performance | **/performance**, **/surveys** | DA, Supervisor, Executive (+ Admin on /surveys) |
| Administration | /admin/users, /admin/reports, /admin/settings | Admin (reports: officers) |

Routes without a nav item that the DA still uses: `/farmers/new`, `/farmers/{id}`, `/farmers/{id}/services`,
`/visits` and `/visits/{id}` (planner, reached from the dashboard), `/visits/{id}/outcome`, `/assignments`
(tab on My Farmers), `/sync`, `/profile`, `/knowledge/{id}`, `/surveys/{id}`.

**Finding:** every nav item is tagged `part: 1` in the prototype and nothing is tagged `part: 2`, although
visits, performance, leave, surveys, feedback and sync are Part 2 (DA Services) functions. The tag only
disables items in the UI, but the frontend team should know these screens call DA Services, not the Registry.

### 1.2 Login

The prototype matches `DEMO_ACCOUNTS` in the browser. Real flow: `POST /api/v1/auth/login` (oan_auth_service,
exists) → JWT → `GET /api/v1/auth/me` (profile with our `profiles.da_services`) or `GET /da-services/me`.
Logout clears drafts client-side; server side `POST /api/v1/auth/logout` (exists). Password reset and Fayda
sign-in are not in the prototype.

### 1.3 Lists, filters, search, export

Every table (`DataTable`) filters and searches client-side over the whole mock array, and `FilterDropdown`
shows a **count per option** (e.g. "Teff (482)"). For real data the list endpoints take
`?q=&limit=&start=&sort=` plus one query param per filter column, and return:

```json
{"status": "success", "data": {"items": [...], "total": 1284,
  "facets": {"kebele": [{"value": "Bako 01", "count": 482}], "status": [...]}}}
```

Facets are computed over the user's scope, not the filtered page. CSV/XLSX export is done in the browser
(`lib/export.ts`) from the loaded rows; only the activity report needs a server-rendered PDF.

### 1.4 Dates, ids, offline

- API dates are ISO 8601 (`2026-09-14`, `2026-09-14T09:12:00+03:00`). The mock shows pre-formatted strings
  ("14 Sep 2026, 09:12"); the client formats.
- Ids the frontend expects to be opaque strings: `da_id` (mock `DA-OR-000341`), farmer id (mock slug
  `lelise-gudeta`, registry id `FR-88466` elsewhere), visit id (`v-1001`), ticket id, `ISS-2041`, `q-3301`.
- Every write a device can capture offline carries `client_op_id`; replays go through `POST /sync/operations`
  (§2.16). Drafts (farmer registration, issue, KPI entry, visit outcome) stay in `sessionStorage`; there is no
  server-side draft API and none is proposed.

## 2. Screens

### 2.1 Dashboard (`/dashboard`, DA view)

| Element | API | Source |
|---|---|---|
| Date range (Last 7/30/90 days, This quarter, This year, Custom) | `from`, `to` on the summary call | – |
| Tile Farm visits "4 of 6 planned" + bar; links to `/visits` | `GET /dashboard/summary?from&to` → `visits {completed, planned}` | ours: DA Visit |
| Tile Tasks "4 open · 3 done", chips `1 URGENT`, `2 DUE TODAY` | same → `tasks {open, done, urgent, due_today}` | ours: DA Task |
| Tile Sync status "All data synced · 5 min ago" | same → `sync {server_pending, failed, conflicts, last_received_at}`; the device merges its own queue count | ours + device |
| Today's Plan rows `{time, title, desc, status UPCOMING/DUE TODAY, href}` | `GET /dashboard/today?date=` → `items[] {kind visit/task, id, time, title, subtitle, status, href}` | ours: DA Visit + DA Task |
| "View calendar" → `/visits/{id}` | link | – |
| Quick actions: Register Farmer, Submit Crop Report, Start Farm Visit, Sync Queue | links | – |
| Notifications bell | §2.19 | ours |
| "Search portal..." | `GET /search?q=` (farmers, visits, grievances, articles); phase 2 | ours |

### 2.1b Supervisor Programme Health (`/dashboard`, Supervisor view) — DA-373

Frontend `WoredaHealth` / `KebeleRow` (`features/dashboard/supervisor.ts`): heading `{woreda} - Programme Health`,
KPI tiles (farmers, active agents, avg data quality /100, open grievances), kebele table with Visit %, Last sync,
status (`On track` | `Watch` | `At risk`), search + advanced filters (Kebele / Status / Agents) and date range.

**Same route as the DA home:** `GET /dashboard/summary`. Role/scope come from the access token.

| Caller | Behaviour |
|---|---|
| Development Agent | `view: "agent"` — visits / tasks / sync (unchanged) |
| Supervisor / Admin, no `da_id` | `view: "woreda"` — woreda KPIs + kebele breakdown |
| Officer with `da_id` | `view: "agent"` for that DA (scope-checked) |

| Element | API | Source |
|---|---|---|
| Woreda heading + KPI tiles + kebele table | `GET /dashboard/summary?from&to[&woreda&kebele&status&agents&q&limit&start]` | ours: scope + Administrative Area + Farmer Reference + agents + DA Visit + Grievance client |
| Status thresholds | server: `visit_pct >= 80` On track · `60–79` Watch · `< 60` At risk (same as FE `statusFor`) | derived from visit completion in range |
| Avg data quality (interim) | `stats.data_quality` = round(mean of kebele `visit_pct`) | no dedicated quality DocType yet |
| Filters | `kebele`, `status`, `agents` (exact count), `q`; apply to **kebeles only**; stats stay full-woreda | – |
| Scope | Caller's `woreda_scopes` only; foreign woreda/kebele → 403/400. Multi-woreda supervisors must pass `woreda=` | DA RBAC Assignment |

`GET /dashboard/today` stays DA/agent day plan only. Executive / Admin / Comms roll-up dashboards
(`WOREDA_ROLLUPS`, `REGION_SUMMARY`, `ADMIN_STATS`, …) remain out of scope here.

### 2.2 My Farmers (`/farmers`, tabs My Farmers · Visits · Assignments)

Frontend `Farmer`: `id, name, email, phone, avatar?, kebele, woreda, region, crop, landHa, registeredAt,
registeredTime, status (Verified | Pending | Flagged), statusReason?, totalPlots, visits, lastVisit`.
Filters: kebele, crop, status (`Verified, Pending, Enrolling, Inactive, Flagged`), education
(`None, Primary, Secondary, Tertiary`). Header shows `TOTAL_FARMERS`.

| Element | API | Source |
|---|---|---|
| Table + filters + facets + total | `GET /farmers?q&woreda&kebele&crop&status&education&limit&start` → `Farmer` fields above + `farmer_id` (registry id). A DA's `woreda` / `kebele` must be inside their assigned scope; an out-of-scope selection is 403, not an empty list | ours: DA Farmer Reference (projection) + DA Farmer Link; `visits`, `lastVisit` derived from DA Visit |
| Status hover reason | `status_reason` | Farmer Registry (verification state) |
| Row → profile | `/farmers/{id}` | – |
| Bulk "Add to segment" (shared with Beneficiaries) | §2.9 | ours |
| Export CSV | client | – |

**Finding:** two different farmer status vocabularies exist (`Farmer.status` has 3 values, the filter has 5).
The API returns the Farmer Registry's own state and the UI maps it.

### 2.3 Farmer profile (`/farmers/{id}`, tabs Overview · Holdings · Crop history · Visit history · Documents · Advisory)

| Element | Frontend type | API | Source |
|---|---|---|---|
| Header + stats | `Farmer` | `GET /farmers/{id}` | Farmer Registry via projection |
| Holdings | `Plot {code, crop, areaHa, soil, zone, lat, lng, tenure}` | `GET /farmers/{id}/plots` | Farmer Registry (Land Registry later) |
| Crop history | `CropRecord {season, crop, yield, status Successful/Average/Below average}` | `GET /farmers/{id}/crops` | Farmer Registry / ODK results |
| Visit history | `Visit {title, summary, date, time}` | `GET /visits?farmer_id=&status=Completed` | ours: DA Visit |
| Documents | `FarmerDocument {name, uploadedAt, size}` + download | `GET /farmers/{id}/documents`, file URL | Farmer Registry (files) |
| Advisory notes | `AdvisoryNote {date, author, text}`; Add note modal | `GET/POST /farmers/{id}/advisory-notes` | ours: new child of DA Farmer Link (`DA Advisory Note`, see §3) |
| Action strip: Plan visit | §2.6 | | |
| Action strip: Services | `/farmers/{id}/services` (§2.5) | | |
| Action strip: Raise grievance | §2.10 | | |
| Action strip: Request update `{field (phone/email/kebele/woreda/crop), current, new_value, reason, supporting[]}` | `POST /farmers/{id}/change-requests` → routed to Registry team | ours: `DA Farmer Change Request` (new, §3) → Farmer Registry |
| Action strip: Consent view/withdraw `{consent_id, reason (4 options)}`; consents `{counterparty, categories[], validTo, openTx}` | `GET /farmers/{id}/consents`, `POST /farmers/{id}/consents/{id}/withdraw` | Farmer Registry (consent master); we record actor DA-ID, time, reason |

### 2.4 Register farmer (`/farmers/new`, 5 steps, Save draft, Submit registration)

Exact form field names (`name=` attributes):

| Step | Fields | Options |
|---|---|---|
| 1 Personal details | `photo`, `firstName`*, `lastName`*, `mobile`, `email`, `dob`*, `gender`*, `idType`, `idNumber`, `language` | gender `male/female/other`; idType `national_id/kebele_id/passport/driving_license`; language `en/am/om/ti` |
| 2 Location and kebele | `region`*, `zone`*, `woreda`*, `kebele`*, `landmark`, `gps` ("lat, lng", device geolocation or typed) | cascading from `locationData.ts` |
| 3 ID and documents | `documents[] {type, fileName, description, file}`; PDF/JPG/PNG ≤ 5 MB | sample types "Fayda ID", "Household ID", land certificate |
| 4 Specialization & qualification | `educationTier`*, `specialization`*, `certificates[]` | tiers Certificate / Diploma / Bachelor's degree / Master's degree; 7 specialisations |
| 5 Review and submit | `confirmed` checkbox (consent text) | – |

| Element | API | Source |
|---|---|---|
| Cascading dropdowns | `GET /master/regions`, `/master/zones?region=`, `/master/woredas?zone=`, `/master/kebeles?woreda=` | ours: DA Administrative Area |
| Reference lists (gender, id type, language, education tier, specialization, certificate type) | `GET /master/reference-values?type=` | ours: DA Reference Value |
| File uploads | `POST /files` (returns `file_url`, `content_hash`) | Frappe File |
| Submit | `POST /farmers` with the steps as nested objects + `client_op_id` → Farmer Registry create → `DA Farmer Link` (Automatic) → returns `{farmer_id, ticket_number, status}` | Farmer Registry + ours |
| Ticket number shown after submit (mock `AMHA-SD-OCT-12345`) | from the Farmer Registry response | Farmer Registry |
| Duplicate check | `GET /farmers/check-duplicate?phone=&id_number=` | Farmer Registry |

**Findings for the UI/Registry teams:** (a) step 4 (education tier, specialisation, certificates) is a DA
attribute set reused from the DA profile, not a farmer attribute; confirm whether the Farmer Registry has it.
(b) First/Last name + Gregorian DOB vs Shikha's Name/Father/Grandfather + Ethiopian-calendar birth
month/year: the Farmer Registry schema wins. (c) The consent text says "recorded in the master DA Registry";
for a farmer it should say Farmer Registry.

### 2.5 Farmer services (`/farmers/{id}/services`, tabs Credit · Marketplace)

| Element | Fields | API | Source |
|---|---|---|---|
| Active credit application | `ref, lender, amount, tenure, product, currentStage (0–4), stages[] {at, actor}`; stages Submitted / Document review / Credit assessment / Final approval / Disbursement | `GET /farmers/{id}/credit-applications` | Credit service (partner bank), mirrored read-only; **no contract yet** |
| New credit application | `product (Seasonal input loan / Livestock working capital / Irrigation equipment)`, `amount`, `tenure (6/12/18/24)`, `purpose`, `docs[]`; requires farmer Fayda `Verified` and active consent | `POST /farmers/{id}/credit-applications` (+ `client_op_id`, offline queue) | ours: `DA Credit Application Link` (new, §3) → Credit service |
| Documents / Add note / Statement buttons | bank-mirrored record | `GET .../credit-applications/{ref}/documents` | Credit service |
| Marketplace: record a service payment `{service (6-item catalogue), category, provider, date, payee, amount, method (Mobile money / Bank transfer / Cash (receipted)), notes}` | `POST /farmers/{id}/service-payments` | ours: `DA Service Payment` (new, §3); gateway WIP, no funds move |

### 2.6 Plan a visit (modal; from profile, planner, priority list)

Frontend state: farmer search (`name, phone, kebele`, quick filters "Registered", "Input voucher redeemed this
season"), selected farmer card (`registryCode FR-xxxxx, kebele, crop + landHa, lastVisit`), `visitType`
(`Crop Survey | Livestock Survey | Services | Follow-up | Advisory`), Advisory Q&A rows `{question, answer}`,
`purpose`*, `date`*, `time`*, `duration (30/45/60/90 min)`, `location` (read-only, from parcel), `priority`
(`High — overdue by 4 days | Medium — due this week | Low — routine`), Save as draft / Plan visit.
Schedule mode (from the priority list) presets `reason`, `visitType`, `level`.

| Element | API | Source |
|---|---|---|
| Farmer search + chips | `GET /farmers?q=&limit=10` → items + `flags[]` (`registered`, `voucher_redeemed_season`) | ours projection; voucher flag external (**contract pending**) |
| Plan | `POST /visits` body: `client_op_id, farmer_id, visit_type, purpose, scheduled_at, duration_min, priority (High/Medium/Low), priority_reason, location {lat,lng} (optional, server fills from parcel), notify_farmer_sms, advisory_qa[] {question, answer}` → `{id, physical_status: Planned}` | ours: DA Visit (+ `DA Visit Advisory QA` child, §3) |
| Save as draft | device | device |
| "Planned offline" banner | idempotent on `client_op_id` | ours |

### 2.7 Visits list (`/visits`, Supervisor nav item; DA reaches it from the dashboard tile)

Frontend `VisitRecord`: `id, date, time, agent, farmerId, farmerName, farmerEmail, farmerAvatar?, kebele,
purpose, status (Confirmed | Planned | Completed | Missed), plotRef, durationMin, assignedBy?`.
`VISIT_STATS`: `plannedThisWeek, completed, missed, onTimeRate`. Filters: agent, kebele, status.
Purposes seen: Crop inspection, Input advisory, Pest follow-up, Registration, Harvest survey, Soil sampling,
Follow-up. Details modal shows `VisitTimelineEntry {label, by, when, done}`. Reschedule modal:
`new_date, new_time, reason` (Farmer unavailable / Weather / road access / Agent schedule conflict / Inputs not
yet delivered / Other), farmer notified by SMS.

| Element | API | Source |
|---|---|---|
| Table + stats + facets | `GET /visits?q&agent&region&zone&woreda&kebele&status&from&to&limit&start` → `VisitRecord` fields (`agent` = DA name, `assigned_by`) + `stats`. Explicit area selections are validated against the caller's scope; unauthorized areas are 403 | ours: DA Visit |
| View details | `GET /visits/{id}` → record + `timeline[]` (from DA Visit status history) | ours |
| Reschedule | `POST /visits/{id}/reschedule {scheduled_at, reason}` → new DA Visit with `rescheduled_from`, old one `Rescheduled`; SMS job | ours + SMS |
| Supervisor assigns a visit to a DA (`assignedBy`) | `POST /visits` with `da_id` by a Supervisor (scope: own Woreda) | ours |

### 2.8 Visit planner (`/visits/{id}`)

Frontend: `PLANNER_DAYS[] {label, dayOfMonth, isToday, visits[] {time, farmerName, kebele, status
scheduled/completed/overdue}}`, views Week / Day / Month, `PRIORITY_LIST[] {farmerId, name, issue, level
high/medium/low, visitType}`, `TODAYS_VISITS[] {farmerName, plannedArrival, state done/confirmed/tentative,
note, kebele, parcel, lat, lng}`, Start visit modal `{arrivedAt, gps {lat,lng,accuracy} | unavailable → parcel
location, farmerPresent}` → opens outcome form with `?started=&farmer=&gps=1`.

| Element | API | Source |
|---|---|---|
| Calendar | `GET /visits?from&to&da_id=me` → items with `scheduled_at`, `farmer_name`, `kebele`, `planner_status` (derived: scheduled / completed / overdue) | ours |
| Priority list | `GET /farmers/priority?limit=10` → `{farmer_id, name, issue, level, visit_type, source}` | **derived** from open DA Tasks, pest/weather signals, grievances; **open:** alert source |
| Schedule from priority row | `POST /visits` with preset | ours |
| Today's visits with confirmation | `GET /visits?date=today` → `farmer_confirmation (Unknown/Confirmed/Tentative/Declined)`, `confirmation_note`, `parcel`, `lat`, `lng` | ours: DA Visit (**fields to add**) |
| Start visit (check-in) | `POST /visits/{id}/start {arrived_at, gps {lat, lng, accuracy_m} | null, farmer_present: true, client_op_id}` → `physical_status = In progress` | ours |
| View locations | same list, coordinates | ours |

### 2.9 Visit outcome and activity report (`/visits/{id}/outcome`)

Frontend `OutcomeValues`: `purpose, farmerResponse, observed, advice, inputs, nextAction` (all free text;
`nextAction` is "Follow-up visit · 12 Aug 2026"). Evidence rows `{title, badge VERIFIED / AGRILEARN · AUTO /
PENDING VERIFICATION, meta, file, url, isImage}` with upload/replace/view. `ACTIVITY_REPORT {weekLabel,
visitsCompleted "14 / 16", farmersReached, advisoriesIssued, issuesRaised, followUps}` and
`SUBMITTED_OUTCOMES[] {farmerName, kebele, purpose, summary, date}`; Activity Log link; Export Report.

| Element | API | Source |
|---|---|---|
| Header | `GET /visits/{id}` | ours |
| Save draft | device (`visit-outcome:{id}` draft key) | device |
| Submit visit report | `POST /visits/{id}/submit {purpose, farmer_response, observed, advice_given, inputs_actions, next_action {type: Follow-up visit / Task / None, note, date}, evidence[] file ids, client_op_id}` → `physical_status = Physical Visit Complete`; server creates the follow-up `DA Task` and, for a follow-up visit, a Planned `DA Visit` | ours |
| Evidence upload (geo-tagged) | `POST /visits/{id}/attachments` (multipart + `lat, lng, captured_at, content_hash, kind`) | ours: DA Visit Attachment |
| Weekly activity report | `GET /reports/activity?week=2026-W26` → `{visits_completed, visits_planned, farmers_reached, advisories_issued, issues_raised, follow_ups, outcomes[]}` | derived from DA Visit, DA Task, DA Grievance Link, DA Internal Issue |
| Export | `GET /reports/activity/export?week=&format=pdf|xlsx` | ours |

**Findings:** (a) `nextAction` is free text in the UI; the API splits it into type + date so the follow-up task
can be created. (b) The evidence list on this screen shows the DA's own certificates (mock reuse of
`EVIDENCE_DOCUMENTS`); visit evidence should be photos of the visit. (c) Mock dates "Jun 24, 2016" in the
submitted list are a mock bug (the data file says 2026).

### 2.10 Grievances (`/grievances`)

Frontend `Grievance`: `ticketId, title, submitter, region, woreda, type, category (Inputs | Schemes | Payments |
Markets | Extension | Land), status (Pending Submit | Submitted | More Info Needed | Assigned | In Progress |
Under Review | Resolved | Rejected | Pending Submitter | Closed), priority (Low | Medium | High | Critical),
submittedAt, attachments, responses, raisedBy (DA | Farmer)`. Stat buckets: All / Pending / In Progress /
Under Review / Resolved / Rejected (`bucketOf`). Tabs by `raisedBy`: All / DA / Farmer. Filters: category,
status bucket, priority, region. Case detail `GrievanceCase {thread[] (submission | dept-response |
status-change | internal-note), submitter {name, kind, faydaId, kebele, channel, submittedOn}, sla
{consumedPct, submittedOn, dueOn}}`. Raise modal: `forWhom (farmer | me), farmer, category, subject (≥3),
description (≥10), attachments[]`; offline → "QUEUED — case ID assigned on sync".

| Element | API | Source |
|---|---|---|
| List + stats + facets | `GET /grievances?raised_by&category&status&priority&q` → records + `stats` | Grievance Service via adapter; DA Grievance Link for raised_by / farmer link |
| Detail + thread + SLA | `GET /grievances/{ticket_id}` | Grievance Service |
| Raise | `POST /grievances {raised_for: Self/Farmer, farmer_id, category, subject, description, attachments[], client_op_id}` → `{link_id, external_case_id | null, api_sync_state}` | ours: DA Grievance Link → Grievance Service |
| Service status banner | `GET /grievances/sync-status` | ours |
| Retry failed push | `POST /grievances/sync/retry` | ours |

**Finding:** the prototype also contains `GrievanceResponsePanel`, `DeferSlaModal` (nodal officers, 30-day
max) and `GrievanceCaseSidebar` with officer/department pickers. Those are case-handling actions that belong
to the Grievance Service UI, not to DA Services (FSD: triage, response, closure happen there). The DA role
must not see them; confirm with the UI team which roles do.

### 2.11 Beneficiaries (`/beneficiaries`)

Frontend `Segment {id, name, rule, scheme, members, tone, icon}` (rules edited by Supervisor/Admin only),
`Beneficiary {id, name, avatar?, kebele, crop, segmentId, status (Enrolled | Pending verification | Removed),
linkedAt}`; actions `link(farmerIds[], segmentId)` → `{added[], skipped[]}`, `remove(id)`.

| Element | API | Source |
|---|---|---|
| Segment cards | `GET /beneficiaries/segments` → `{code, title, rule, scheme, members}` | ours: DA Beneficiary Segment |
| Table + filters (farmer, kebele, crop, segment, status) | `GET /beneficiaries?segment&status&kebele&crop&q` | ours: DA Beneficiary Classification + farmer projection |
| Link farmers | `POST /beneficiaries/link {farmer_ids[], segment, reason?, evidence[]}` → `{added[], skipped[]}`; rows start `Pending verification` (`review_state = Pending`) | ours |
| Remove | `POST /beneficiaries/{id}/remove {removal_note}` (required; sets `previously_removed`) | ours |
| Rule editing | `PATCH /beneficiaries/segments/{code}` (Supervisor/Admin) | ours |

### 2.12 My Teams (`/my-teams`, DA only)

| Element | API | Source |
|---|---|---|
| Supervisor card `{name, title, woreda, phone, email}` | `GET /my-team` → `supervisor` | Registry (Woreda supervisor) + our RBAC assignment |
| Team table: `fullName, daId, kebele, specialisation, educationTier, farmerCount, activeStatus, phone, joinedAt` (same Woreda, not Separated, me first) | same → `members[]` | Registry projection (DA Agent Reference) |

Read-only; no row links. Requires the Registry projection to carry `phone`, `specialisation`, `joined_at`,
`farmer_count` (added to `DAReference` in DA-197; mock values until the registry contract lands).

### 2.13 Internal Feedback (`/feedback`, tabs Report an issue · Issue queue)

DA side (`Issue`): `id (ISS-2041), subject, description, category (Operational | Equipment / supplies |
Payment / incentive | Safety | Data / system | HR), severity (Low | Medium | High), relatedTo?, status (Queued
(offline) | Submitted | New | In Review | Assigned | In Progress | Resolved | Closed), submittedLabel, note`.
`NewIssueInput {category, severity, subject, description, relatedTo?}` + attachments; `RELATED_OPTIONS` mix
kebele / farmer / visit / none. Supervisor queue (`QueueIssue`): `ref, subject, category, severity, reporter,
woreda, assignee, slaDue, slaBreached, status (+ Needs more info, Reopened, Rejected), history[] {at, text}`;
actions Assign (`assignee`, note optional), Request info (note), Resolve (note), Reopen (note); stats Open /
Unassigned / Breaching SLA / Resolved (30d).

| Element | API | Source |
|---|---|---|
| My issues | `GET /issues?mine=1&status=` | ours: DA Internal Issue |
| Submit | `POST /issues {category, severity, subject, description, related {type: Kebele/Farmer/Visit/None, id}, attachments[], client_op_id}` | ours |
| Supervisor queue + stats + facets | `GET /issues?woreda=&status&severity&category&assignee&q` | ours (scope: own Woreda) |
| Actions | `POST /issues/{id}/assign {assignee, note}`, `/request-info {note}`, `/resolve {note}`, `/reopen {note}` | ours; reporter notified (SMS/app) |
| Assignee list | `GET /issues/assignees` (Supervisor, desks) | ours: DA Reference Value |

### 2.14 Knowledge Base (`/knowledge`, `/knowledge/{id}`) and Broadcast (`/broadcast`)

`Article {id, title, category (Crop production | Livestock | Soil & water | Pest & disease | Markets & credit |
Advisory), summary, body[], snippet (SMS ≤160), languages (en | am), updatedAt, author, sends}`;
`KnowledgeVideo {id, title, category, summary, url, image, imageCredit}`; Create snippet (Supervisor, Admin,
Comms): `{title, category, snippet}`. Send to farmers → `Dispatch`.
Broadcast: `Signal {id, source (Knowledge | Emergency | Weather | Informational | Grievance), title, detail,
receivedAt, severity (Info | Warning | Critical), status (New | Dismissed | Dispatched)}`, `Dispatch {id, message,
source, channels (SMS | Telegram)[], audience, recipients, dispatchedBy, dispatchedAt, delivery {delivered,
failed, pending}, evidence?}`; audiences include "My linked farmers" and kebele groups.

| Element | API | Source |
|---|---|---|
| Articles + categories + search | `GET /knowledge/articles?category&q&lang`, `GET /knowledge/articles/{id}` | ours: DA Knowledge Article (§7 of data-model) |
| Videos | `GET /knowledge/videos` | ours (curated links) |
| Create snippet | `POST /knowledge/articles` (Supervisor/Admin/Comms) | ours |
| Send to farmers (DA: own linked farmers only) | `POST /broadcasts {article_id | message, channels[], audience {type: my_farmers / kebele / segment, id}, evidence[]}` → dispatch id | ours: DA Broadcast Dispatch → SMS/Telegram gateway |
| Dispatch history + delivery | `GET /broadcasts?source&audience&channel`, `GET /broadcasts/{id}` | ours + gateway callbacks |
| Signals inbox (Comms/Admin/Supervisor) | `GET /signals`, `POST /signals/{id}/dismiss`, `POST /signals/{id}/dispatch` | ours + external feeds (NMA weather, plant health, Grievance) |

**Finding:** FSD gives broadcast to the Communications Officer. The prototype lets a DA dispatch to "My linked
farmers" and kebele groups. Allow it only for the DA's own linked farmers, with the article's approved snippet;
free-text broadcast stays with Comms/Admin.

### 2.15 Performance (`/performance`, `/performance/kpi-entry`)

DA view: `PERFORMANCE_STATS` keys `visits ("12", "Target: 10 visits"), farmers, registrations, tasks ("27 / 31"),
grievances, response ("1.8 d"), satisfaction ("4.6 / 5", "From 62 surveys"), fieldDays`; `QUARTERS`; `Goal {id,
title, target, due, progress 0–100, status (On track | Needs attention | At risk)}`; `FARMER_SATISFACTION
{score, outOf, note}`; `UPCOMING_REVIEWS[] {id, title, detail}`; `SUPERVISOR_FEEDBACK` text.
Supervisor view: `SUPERVISOR_PERFORMANCE_STATS` (agents, active today, visits week, pending reviews,
training %), `PERFORMANCE_TIERS` T1–T4 with editable `TierThresholds`, `AgentPerformanceRow {daId, name,
kebele, visits, quality %, training %, status (On track | Needs support | Check in), tier}`, KPI entry
`ManualForm {period, woreda, agent, kebele, visits, farmerCases, needs, supportActions, reviews, training,
quality, notes}` (required: period, woreda, agent, kebele, visits, farmerCases, training, quality) or bulk
xlsx/csv with `TEMPLATE_HEADERS`.

| Element | API | Source |
|---|---|---|
| My stats for a quarter | `GET /performance/me?period=2026-Q3` → `stats {visits {value, target}, farmers, registrations, tasks {done, total}, grievances {resolved, open}, response_days, satisfaction {score, out_of, surveys}, field_days}` | derived: DA Visit, DA Task, DA Grievance Link, survey responses, KPI records |
| Goals | `GET /performance/me/goals`, Supervisor `POST /performance/goals` | ours: DA Goal (data-model 6.4) |
| Satisfaction, upcoming reviews, supervisor feedback | same `/performance/me` → `satisfaction`, `reviews[]`, `feedback` | ours: DA Performance Review |
| Woreda registry + tiers | `GET /performance/agents?period&kebele&status&tier&q`, `GET /performance/tiers`, `PATCH /performance/tiers` (Admin) | ours: DA KPI Record + tier engine |
| KPI entry manual | `POST /performance/kpi-records {period, da_id, kebele, visits, farmer_cases, needs, support_actions, reviews, training_pct, quality_pct, notes}` | ours |
| KPI bulk import | `POST /performance/kpi-records/import` (xlsx/csv ≤ 5 MB) → per-row result; template `GET /performance/kpi-records/template` | ours |

### 2.16 Surveys (`/surveys`, `/surveys/{id}`)

Backend contract: [surveys-integration.md](surveys-integration.md). Frontend wire-up: [surveys-frontend.md](surveys-frontend.md). Postman: `postman/Surveys/`.

`SurveyTask {id, name, templateVersion, type (Satisfaction | Service quality | Needs assessment), window {open,
close}, languages[], targetFarmers, collected, queued, status (Open | Closing soon | Closed), estMinutes,
assignmentMode (Woreda | Selected), assignedDaIds[], parameters[], answered[] farmer ids}`; `Parameter {id, order,
prompt, type (consent | auto | lookup | rating | single | multi | text), required, options?, helper?, prefill?
(Farmer Registry | DA assignment), translations? {Amharic?, Afaan Oromoo?}, condition? {parameterId, equals}}`.
Capture: pick farmer (one response per farmer per survey), consent first (declined ends the response), then submit
directly → `Synced | Queued`. Only `Queued` and `Synced` are client-settable; `Draft` is rejected with 400.
`Declined` is forced when consent is declined, and `Rejected` / `Conflict` are server-only. Supervisor/Admin create
template `TemplateInput {name, type, open, close, languages[], targetFarmers, questions[] {prompt, type, options,
required, translations?}, assignmentMode?, assignedDaIds?, woreda?, region?, zone?, client_op_id?}`.
The Woreda is resolved from `DA Administrative Area`, its Zone/Region are derived, and the target is checked
against the creator's live RBAC assignment; supplied Region/Zone are consistency checks only. Deployments are
Woreda-wide by default; `Selected` restricts visibility/capture to the listed DAs in that Woreda.

| Element | API | Source |
|---|---|---|
| Assigned surveys | `GET /surveys?status&type&language&woreda&q` → tasks with my `collected`, `queued`, `answered[]`; `Selected` surveys hidden from unassigned DAs | ours: DA Survey Template + DA Survey Response (data-model §7) |
| Capture | `GET /surveys/{id}` (parameters; unassigned DA → 403 in `Selected` mode); `POST /surveys/{id}/responses {farmer_id, da_id?, consent: granted/declined, answers {param_id: value}, started_at, completed_at, sync_state?, client_op_id}`. `da_id` is required when a Supervisor or Admin collects on behalf of a DA. Farmer must be in the collector DA's assigned Kebele and the survey Woreda: unknown → 404, out of scope → 403, duplicate farmer/template → 409 (across all DAs). Before `window_open` / after close, invalid timestamps, or an invalid client `sync_state` (including `Draft`) → 400. Response stores the farmer's location, not the DA's | ours |
| Create template | `POST /surveys/templates` (Supervisor/Admin) → publishes an Open survey into the authoritative Woreda. The Woreda → Zone → Region hierarchy is resolved from master data; forged, unknown, inactive or mismatched areas are rejected and the target must be inside the caller's active assignment. `assignmentMode=Selected` requires non-empty `assignedDaIds` from the same Woreda | ours |
| Template list / detail | `GET /surveys/templates?status&type&woreda&q`, `GET /surveys/templates/{id}` (Supervisor/Admin/Executive). Search and derived status filters apply before pagination; `meta.total` is the filtered count and `Closed` includes expired Open templates | ours |
| Patch template | `PATCH /surveys/templates/{id}` — window, targetFarmers, languages, status Closed, assignmentMode/assignedDaIds; question edits with no collected responses update the template and bump its version, while edits after responses exist create a new template version with the submitted questions and zero responses (original template/questions/responses unchanged); invalid questions → 400 | ours |
| Responses list | `GET /surveys/{id}/responses?farmer_id&da_id&region&zone&woreda&kebele` — DA own rows; a DA + Supervisor/Executive with a **live** officer assignment uses the officer scope. An expired officer grant falls back to DA rows. Explicit area/DA selections are validated; unauthorized values are 403, not silently ignored | ours |
| Excel export | `GET /surveys/{id}/responses/export` → complete XLSX download (all base columns + every parameter; Supervisor/Executive/Admin with a live assignment, survey in scope) | ours |
| Results | `GET /surveys/{id}/results` — counts, rating averages, choice histograms (basic; Part 2 analytics later) | ours |

### 2.17 Sync queue (`/sync`)

`QueueItem {id, entity (Farmer update | Visit outcome | Survey response | Grievance | Internal issue |
Certificate upload | Plot boundary | Credit application), ref, capturedAt, size, status (Awaiting sync | Syncing
| Synced | Failed | Conflict | Resolved — kept mine | Resolved — kept server | Resolved — merged), detail?,
conflicts[] {field, device, server}, version {base, server}}`. Actions: Sync now, Retry (Failed), Needs you —
resolve (Conflict) with per-field Merge choices; merged records flagged for supervisor spot-check.

| Element | API | Source |
|---|---|---|
| Sync now | `POST /sync/operations` batch of `{client_op_id, entity_type, operation, payload, base_version, captured_at, device_id, payload_hash}` → per op `{state Accepted/Rejected/Conflict, entity_name, conflict_id, message}` | ours: DA Device Sync Operation |
| Large attachments | `POST /sync/attachments` (chunked, `content_hash`) | ours |
| Conflict detail | `GET /sync/conflicts/{id}` → `field_diffs[] {field, device, server}`, `base_version`, `server_version` | ours: DA Sync Conflict |
| Resolve | `POST /sync/conflicts/{id}/resolve {resolution: Keep mine / Keep server / Merge, merged_fields {field: device/server}}` → applies to owner system (e.g. Farmer Registry) and sets `flag_for_supervisor` on Merge | ours → owner |
| Retry failed external push | `POST /sync/operations/{client_op_id}/retry` | ours → Grievance / Credit |
| Server view, tiles | `GET /sync/status`, `GET /sync/operations?state&entity_type` | ours |
| Awaiting count, device storage | device | device |

Entity → owner: Farmer update → Farmer Registry; Visit outcome, Internal issue, Certificate upload → ours;
Survey response → ours (+ ODK when the survey is an ODK form); Grievance → Grievance Service; Credit
application → Credit service (**no contract**); Plot boundary → Land Registry (**no contract**, held as evidence).

### 2.18 Profile (`/profile`)

`AGENT_PROFILE {name, role, agentId, location, email, phone, joinedOn, status}`; `AGENT_STATS {yearsOfService,
serviceSince, kebelesAssigned, kebeles, certifications, certificationsPending}`; `LEARNING_SUMMARY {completed,
assigned, certificationsCurrent, note, agrilearnUrl}` (read-only from Agrilearn). Demographics: locked
(`firstName, lastName, dob, gender, idType, idNumber`, "verified via Fayda"), editable (`mobile`, `email`,
`language`, `photo`) → "sent to your Woreda supervisor for approval". Dependents `{name, relationship (Spouse |
Son | Daughter | Parent | Sibling | Other), dateOfBirth, status (Verified | Pending approval)}`. Certificates
`{title, status (verified | auto | pending), issuer, fileMeta, url}`; upload `{source external/agrilearn, file,
name, organisation, type (Professional certification | Academic qualification | Training completion | Licence /
permit), issueDate, expiryDate, reference, notes}`. Leave: `LeaveBalance {type (Annual leave | Sick leave |
Other / statutory), entitlement, used}`, `LeaveRecord {id, type, period, days, status (Pending | Approved |
Taken), message}`, request `{type, from, to, reason, coverage}` (working days computed).

| Element | API | Source |
|---|---|---|
| Header, stats, learning summary | `GET /profile/me` | Registry projection + ours (certificates, kebeles) + Agrilearn mirror |
| Edit demographics | `POST /profile/change-requests {changes {mobile, email, language, photo}}` → Supervisor approval | ours: DA Profile Change Request (data-model 4.4) |
| Dependents | `GET /profile/dependents`, `POST /profile/dependents {name, relationship, date_of_birth}` → Pending approval | ours: `DA Dependent` (new, §3) |
| Certificates | `GET /profile/certificates`, `POST /profile/certificates` (multipart) → `pending`; Agrilearn ones arrive by sync | ours: DA Certificate (data-model 6.5) + Agrilearn |
| Leave | `GET /profile/leave` → balances + history; `POST /profile/leave-requests {leave_type, from_date, to_date, reason, coverage}` | ours: DA Leave Balance, DA Leave Request (6.3) |

Supervisor-side approvals for all of the above are the `ApprovalRequest` queue (`/approvals`): `kind
(Onboarding | Profile change | Assignment | Specialization)`, `data[] {label, value, verified}`, `trail[]`,
decisions approve / changes / reject with a note (min 5 chars). Onboarding is Registry; the other three are
ours → `GET /approvals?kind&status&kebele`, `POST /approvals/{id}/decide {decision, note}` using the approval
workflow pattern (data-model 6.1).

### 2.19 Notifications

Two different shapes exist in the prototype: `communication.Notification {id, title, body, at, read, kind
(Onboarding | Assignment | Sync | Grievance | Broadcast | Issue)}` and `NotificationsPanel.Notification {id,
type (user | money | warning | plant), title, description, time, isUnread, group (TODAY | YESTERDAY)}`. The API
follows the first and adds `href`:

`GET /notifications?unread=1&limit=` → `{id, kind, title, body, at, read, href}`; `POST /notifications/{id}/read`;
`POST /notifications/read-all`. Source: ours (DA Notification, data-model 5.10). The UI team should merge the
two types.

## 3. Changes this implies for `data-model.md`

- **DA Visit**: add `farmer_response`, `observed`, `inputs_actions`, `next_action_type` (None / Follow-up visit /
  Task), `next_action_note`, `next_action_date`, `farmer_confirmation` (Unknown / Confirmed / Tentative /
  Declined), `confirmation_note`, `arrived_at`, `farmer_present`, `priority_reason`, `plot_ref`,
  `assigned_by`, `reschedule_reason`; `priority` becomes Low / Medium / High (frontend) instead of
  Normal / High / Urgent; `visit_type` already matches (Crop Survey, Livestock Survey, Services, Advisory,
  Follow-up). Add child **DA Visit Advisory QA** `{question, answer}`.
- **DA Farmer Reference**: add `email`, `avatar`, `region`, `land_ha`, `plots_count` (exists), `visits_count`,
  `last_visit_at`, `status_reason`, `flags` (JSON).
- **DA Agent Reference** (and `DAReference` in `integrations/da_registry/schemas.py`): add `phone`,
  `specialisation`, `joined_at`, `farmer_count`, `supervisor {name, title, phone, email}`.
- **New**: `DA Advisory Note` (farmer, author, text, visit?), `DA Farmer Change Request` (farmer, field,
  current_value, new_value, reason, attachments, state), `DA Credit Application Link` (farmer, product,
  amount, tenure_months, purpose, external_ref, stage, lender, api_sync_state), `DA Service Payment`
  (farmer, service, category, provider, service_date, payee, amount, method, notes, gateway_state),
  `DA Dependent` (name, relationship, date_of_birth, approval_state), `DA Knowledge Article`,
  `DA Broadcast Dispatch`, `DA Signal` (if not already under §7 Communication).
- **DA Internal Issue**: statuses from the frontend (`Submitted, New, In Review, Assigned, In Progress, Needs
  more info, Reopened, Resolved, Closed, Rejected`), `related_type/related_id`, `assignee`, `sla_due_at`,
  `history` child.
- **DA Device Sync Operation**: add `base_version`, `size_bytes`, `external_push_state`; **DA Sync Conflict**
  `resolution` values match the UI (Keep mine / Keep server / Merge).
- **DA Grievance Link**: `category` enum (Inputs, Schemes, Payments, Markets, Extension, Land), `priority`
  enum (Low, Medium, High, Critical); status values mirror the Grievance Service list above.
- **DA Beneficiary Classification**: statuses Enrolled / Pending verification / Removed map to
  `active` + `review_state` + `previously_removed`; no change, document the mapping.
- **Surveys**: parameter `type` enum, `translations`, `prefill`; one-response-per-farmer is enforced by a unique canonical `duplicate_guard_key` (cross-DA, 409). Declined responses do not reserve the farmer; new drafts are rejected (historical Draft rows remain readable/exported). Template scope comes from the `DA Administrative Area` hierarchy and a live RBAC assignment; client `region` / `zone` values are only consistency checks. Woreda-wide is the default; `Selected` restricts the survey to the listed DA-IDs.
- **KPI**: `DA KPI Record` fields per `ManualForm`; tier thresholds as a single settings doc.

## 4. Consolidated endpoint list (DA screens)

| # | Endpoints | Screen | Owner |
|---|---|---|---|
| 1 | `GET /me` (+ role, scope_label, menu hrefs, agent fields) | shell | Nikky (small change) |
| 2 | `GET /notifications`, `POST /notifications/{id}/read`, `/read-all` | shell | Nikky |
| 3 | `GET /master/regions|zones|woredas|kebeles`, `GET /master/reference-values` | forms, filters | Pushkar |
| 4 | `GET /dashboard/summary` (role-aware), `GET /dashboard/today` | DA + Supervisor dashboard | Nikky / DA-373 |
| 5 | `GET /farmers`, `GET /farmers/{id}`, `/plots`, `/crops`, `/documents`, `/check-duplicate`, `GET /farmers/priority` | farmers, planner | Nikky (Farmer Registry adapter) |
| 6 | `POST /farmers` | register farmer | Nikky |
| 7 | `GET/POST /farmers/{id}/advisory-notes`, `POST /farmers/{id}/change-requests`, `GET /farmers/{id}/consents`, `POST .../withdraw` | farmer profile | Nikky |
| 8 | `GET/POST /farmers/{id}/credit-applications`, `POST /farmers/{id}/service-payments` | services | later (no contracts) |
| 9 | `GET /visits`, `GET /visits/{id}`, `POST /visits`, `/reschedule`, `/start`, `/submit`, `/attachments` | visits | Nikky |
| 10 | `GET /tasks`, `POST /tasks/{id}/done` | dashboard tasks | Nikky |
| 11 | `GET /reports/activity`, `/export` | outcome page | Nikky |
| 12 | `GET /assignments/me`, `GET /assignments`, `GET /assignments/coverage` | assignments | Nikky |
| 13 | `GET /beneficiaries/segments`, `GET /beneficiaries`, `POST /beneficiaries/link`, `POST /beneficiaries/{id}/remove` | beneficiaries | Nikky |
| 14 | Grievances (list, detail, raise, sync-status, retry) | grievances | Pushkar (ticket written) |
| 15 | `GET /my-team` | my teams | Nikky |
| 16 | `GET /issues`, `POST /issues`, `/assign`, `/request-info`, `/resolve`, `/reopen`, `GET /issues/assignees` | feedback | Pushkar (candidate) |
| 17 | Knowledge articles/videos, `POST /broadcasts`, `GET /broadcasts`, signals | knowledge, broadcast | Pushkar (candidate, after grievance) |
| 18 | `GET /performance/me`, goals, `GET /performance/agents`, tiers, KPI records + import | performance | Nikky |
| 19 | `GET /surveys`, `GET /surveys/{id}`, `POST /surveys/{id}/responses`, `GET /surveys/{id}/responses`, `GET /surveys/{id}/results`, `POST/GET/PATCH /surveys/templates` | surveys | Nikky |
| 20 | `POST /sync/operations`, `/attachments`, `GET /sync/status`, `/operations`, `/conflicts/{id}`, `POST /conflicts/{id}/resolve`, `POST /operations/{id}/retry` | sync | Pushkar |
| 21 | `GET /profile/me`, change-requests, dependents, certificates, leave, leave-requests | profile | Nikky |
| 22 | `GET /approvals`, `POST /approvals/{id}/decide` | supervisor approvals | Nikky |
| 23 | `POST /files` | uploads | Frappe built-in |

About 75 endpoints for the DA role's screens plus the Supervisor actions they trigger. Supervisor Programme
Health is `GET /dashboard/summary` with a Supervisor token (DA-373). Executive / Admin / Comms dashboards, Agents registry,
Fayda and DA-ID pipelines, Registry sync, Alerts inbox, Users & Roles, Reports and Settings are not fully
mapped here; Agents/Fayda/DA-ID/Registry sync are Part 1 (Registry) screens, Users & Roles maps to our
existing RBAC admin API.

## 5. Findings for the UI team

1. No API layer exists yet: no fetch, no env, no proxy module (the Grievance data file mentions
   `src/proxy.ts`, which does not exist). A thin `src/lib/api.ts` with the envelope, auth header and
   `client_op_id` helper is the first frontend task once contracts are agreed.
2. Farmer ids are name slugs in the mock; the API uses the Farmer Registry id.
3. Register farmer step 4 reuses DA qualification fields; the consent text names the DA Registry.
4. Visit `priority` is a label with an embedded reason ("High — overdue by 4 days"); the API separates
   `priority` and `priority_reason`.
5. Outcome `nextAction` is free text; the API needs type + date to create the follow-up.
6. Visit evidence list shows DA certificates (mock reuse).
7. Two `Notification` types; two farmer status vocabularies.
8. Grievance case-handling components (respond, defer SLA, assign officer) must be hidden for DA; they
   belong to the Grievance Service.
9. DA broadcast should be limited to own linked farmers with approved snippets.
10. DA-ID format: UI `DA-OR-000341`, profile `DA-00012351`, approvals `DA-4402`, Registry `DA-` + 10 digits.
    The UI should treat it as opaque and the Registry format wins.
11. Mock dates "Jun 24, 2016" on the outcome page are a typo for 2026.
12. Every nav item is tagged Part 1; the DA Services screens should be tagged Part 2 so the UI knows which
    backend serves them.

## 6. Open questions (team)

1. Farmer schema and name/calendar conventions: Farmer Registry contract (Shikha's list vs prototype).
2. Priority list / pest alert source (Agrilearn, ODK, plant-health signals, Supervisor flag).
3. "Input voucher redeemed this season" source.
4. Credit service, Land Registry, payment gateway: no contracts; show the screens as "coming soon" or accept
   and hold?
5. Farmer confirmation of visits: DA-entered or inbound SMS reply (needs a webhook)?
6. Weekly crop report task: ODK crop survey or a DA report form?
7. Which roles may edit beneficiary segment rules and KPI tier thresholds (prototype: Supervisor/Admin for
   segments, Admin for tiers)?
8. Supervisor-assigned visits (`assignedBy`): in scope for phase 1?
