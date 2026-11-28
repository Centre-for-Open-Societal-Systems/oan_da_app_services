# DA Services backend engineering standards

| | |
| --- | --- |
| **Purpose** | Approved engineering policy for the DA Services Frappe/Python backend: architecture ownership, security, registry integrations, API contracts, tests, and verification. |
| **Audience** | Backend engineers, reviewers, and architects working in this repository. |
| **Owner** | DA Services backend |
| **Source of truth** | This directory and root [`AGENTS.md`](../AGENTS.md), committed in git. |

## Skills

| Skill | Path | When to use |
| --- | --- | --- |
| Team coding standard | [`da-backend-team/SKILL.md`](da-backend-team/SKILL.md) | Any API, service, DocType, test, permission, or integration change |
| Registry + Visits | [`da-registry-integration/SKILL.md`](da-registry-integration/SKILL.md) | Crop / Farmer / Livestock / Grievance, Settings cleanup, Visits permission contract |

## Mandatory gates

Defined in detail in `da-backend-team`; summarized in [`AGENTS.md`](../AGENTS.md):

- **Postman:** upsert collection (and env if needed) when creating or modifying an HTTP API contract
- **Docs:** update integration and screen docs when the contract changes
- **Tests:** scenario-first, only necessary cases; cleanup **only** test-owned rows (never wipe DocTypes)
- **Verify:** Ruff on touched Python + **affected** bench test modules before done/push

## Related product docs

- `docs/rbac.md`, `docs/visits-integration.md`, `docs/api-da-screens.md`
- Feature guides under `docs/*-integration.md`
