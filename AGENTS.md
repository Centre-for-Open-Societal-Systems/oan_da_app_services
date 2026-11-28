# Agent instructions — DA Services backend

Before implementing, modifying, debugging, testing, or reviewing work in this
repository, **read and follow** these committed skills:

1. **[`agent_skill/da-backend-team/SKILL.md`](agent_skill/da-backend-team/SKILL.md)**  
   Always — coding standard, clean code, permissions, ORM, Git safety.

2. **[`agent_skill/da-registry-integration/SKILL.md`](agent_skill/da-registry-integration/SKILL.md)**  
   When the task touches Crop Sown, Farmer, Livestock, Grievance, registry
   Settings cleanup, staff CSRF forward, or Visits / attendance / gathering
   permission contracts.

See also [`agent_skill/README.md`](agent_skill/README.md).

## Hard gates (do not skip)

| Gate | Rule |
| --- | --- |
| Postman | Creating or modifying an HTTP API contract → **upsert** `postman/<Feature>/OAN-DA-<Feature>.postman_collection.json` (+ env if needed) in the **same** change set. Empty secrets; `{{variables}}` only. |
| Docs | When the API or screen contract changes → update `docs/<feature>-integration.md` and `docs/api-da-screens.md` in the same change set. |
| Tests | Think through scenarios first; add **only necessary** tests. Cleanup **only test-created rows** — never truncate or wipe DocType tables. |
| Verify before done/push | Ruff check + format on **touched** Python; run **affected** `bench … run-tests --module …` modules. Report honestly if not run. |

## Git writes

Do not commit, push, merge, rebase, reset, or stage for the purpose of committing
unless the **current** user message explicitly authorizes that action. Feature
branch pushes go to the configured `fork` remote only. Never attribute commits
or PRs to AI tools.
