# OAN Ethiopia – DA Services Backend

Part 2 of the OpenAgriNet Ethiopia Development Agent (DA) specification: **supervision
and lifecycle management** for DAs, built as a standard Frappe application (`da_services`).

## What this app is, and is not

DA Services covers KPI and performance management, goals and reviews, SLA monitoring,
training/certification tracking, incentive eligibility, the Advisor directory, and the
DA lifecycle (transfer, promotion, specialisation change, leave, retirement,
reactivation, separation).

**The DA Registry is not part of this application.** The authoritative DA master
(identity, DA-ID, Kebele assignment, registry state) lives in the DA Registry, built on
OpenG2P Registry Gen 2, and is exposed to us through APIs and events. This app keeps only
its own operational records plus DA-ID / external references. It must never become a
second DA master, and it never reads another system's database directly.

```
DA Registry (Part 1, OpenG2P Gen 2 + FastAPI)   <-- APIs / events -->   DA Services (Part 2, Frappe)
  DA master, identity, DA-ID, assignment                                 supervision, KPI, lifecycle,
                                                                           leave, training, advisors
```

## Status

Phase 1 complete (project setup, roles, permissions, DA reference integration):

- Five roles seeded on install/migrate: Development Agent, Supervisor, Executive,
  OAN Administrator, Communications Officer (`setup/install.py`).
- `DA RBAC Assignment` doctype: grants a role to a user within a Region / Woreda scope
  and date window; a Development Agent grant is bound to one DA-ID.
- Deny-by-default scope rule in `utils/permissions.py` (`require_roles`,
  `require_da_access`) plus Frappe permission hooks for the app's own doctypes.
- DA Registry client boundary in `integrations/da_registry/`: interface, in-memory mock
  (default backend), and an HTTP adapter that stays blocked until the API contract is
  confirmed (OpenG2P Gen 2 directly vs Part 1 service APIs).
- First v1 endpoint: `GET /api/method/da_services.api.v1.da.get_da?da_id=…`.
- 25 integration tests (`bench run-tests --app da_services`).

Next: Phase 2, Lifecycle Transition doctype and the transfer / promotion /
specialisation workflows.

## Layout

```
da_services/
├── api/            thin whitelisted endpoints, versioned under api/v1
├── services/       business logic, one module per capability
├── integrations/   da_registry, moa, iam clients (API only)
├── utils/          permissions, validators, dates, audit
├── workflows/      approval workflow definitions
├── tests/
├── da_services/    Frappe module (doctypes go here)
├── hooks.py
├── modules.txt
└── patches.txt
```

API functions stay thin; business rules live in `services/`. Authorization is
deny-by-default and enforced in the backend for every call, combining role, Region and
Woreda scope, active assignment, record relationship and workflow state.

## Development

Frappe runs inside the `frappe_docker` dev container; nothing runs natively on the host.
See [SETUP.md](SETUP.md) for the full walkthrough. Every `bench` command runs inside the
container from `/workspace/development/frappe-bench`:

```bash
bench --site da-services.localhost install-app da_services
bench --site da-services.localhost migrate        # after doctype JSON or patches.txt changes
bench --site da-services.localhost clear-cache    # after hooks.py or fixture changes
bench run-tests --app da_services
```

Lint from the app root:

```bash
ruff check da_services/
ruff format da_services/
```

## Reference documents

- EOAN_SPEC_FRS_DA_Registry_D3 (Functional Specification, Part 2 sections)
- EOAN_SPEC_Design_DA_Registry_Part1_D1 (High Level Design; defines the Part 1 / Part 2 boundary)

## License

MIT. See [license.txt](license.txt).
