# AgentOdoo — Design

Status: draft, stage 1 not started. Language: Python 3.11+ (proposed, see Open questions).

## Goal

A personal work assistant that lets an AI agent see the tasks assigned to me (or still unassigned) across one or more Odoo instances, log hours, and run a small set of task operations, always with my confirmation. It must work with any supported Odoo version, and it must also track work on projects that do not exist in Odoo yet.

Personal first. If it proves useful, it is presented to the team, so nothing may assume a single user or a single instance.

## Three layers, three sources of truth

| Layer | Holds | Source of truth for |
|---|---|---|
| **Odoo** (one or more instances) | Projects, tasks, stages, official timesheets | Everything that already exists in Odoo. Always queried live, never mirrored. |
| **Local ledger** (SQLite on my machine) | Projects not yet in Odoo, hours pending upload, links between local and Odoo projects | Work Odoo does not know about yet. |
| **Engram** (agent memory) | My context: repo → project mapping, my employee ID per instance, habits, decisions | Nothing transactional. Never stores tasks or hours. |

Why not keep tasks in Engram: a copy goes stale the moment someone reassigns a task in Odoo. Why not keep pending hours in Engram: it is free text, so it cannot sum hours or prevent duplicates.

## Version independence

An earlier internal prototype hardcoded Odoo 17 field names: it read `state` and `allocated_hours`, which do not exist on Odoo 16. AgentOdoo does not hardcode them.

1. **Detect.** On connect, call `common.version()` (no login needed) and `fields_get` on every model the tool uses.
2. **Map.** Build a **capability map** per instance: logical name → real field, or "unavailable". Business code only uses logical names.
3. **Fail early.** `doctor` reports every logical field the tool needs and whether this instance provides it. A missing required field fails in `doctor`, not halfway through a write.

Known differences the map must absorb (to confirm against real instances, not assumed):

| Logical field | Odoo ≤ 14 | 15–16 | 17 | 18+ |
|---|---|---|---|---|
| task assignees | `user_id` (one) | `user_ids` | `user_ids` | `user_ids` |
| task planned hours | `planned_hours` | `planned_hours` | `allocated_hours` | `allocated_hours` |
| task status | `kanban_state` | `kanban_state` | `state` | `state` |
| project analytic account | `analytic_account_id` | `analytic_account_id` | `analytic_account_id` | `account_id` |

**Transport is swappable.** Stage 1 uses XML-RPC, available through Odoo 18 and confirmed enabled on the first Odoo 16 instance tested. Odoo 19 introduced a JSON-2 API and deprecated XML-RPC, so the transport sits behind its own interface from day one.

## Safety model for writes

Kept from that earlier prototype because it is the part that earns trust:

- A closed allowlist of write operations. No generic `execute_kw`.
- Every write is **plan → explicit confirmation → write → read-back → receipt**.
- Before writing, re-check that the record did not change since the plan.
- A receipt is persisted **even when something fails after the write** (read-back mismatch, network error). A write without a trace must not be possible.

Fixed relative to that prototype:

- Credentials come from the OS keychain per profile. Ambient environment variables never silently override them.
- No instance is hardcoded. Every instance is one profile among others.
- HTTPS only, unless a profile explicitly opts in for a local instance.

## Hours for projects not yet in Odoo

Two modes per profile:

- **Outbox (default).** Hours on a local project stay pending with their original date. When the project appears in Odoo, I link the local project to it and the agent offers to upload the pending hours as timesheets, with confirmation, duplicate detection, and original dates. Odoo may reject dates in a closed period; those stay pending and are reported.
- **Strict.** Hours can only be logged on projects that exist in Odoo. For teams whose process creates projects in Odoo before work starts.

The agent also reports local projects with pending hours and no Odoo project yet.

## Delivery form

- **Core library and CLI.** All logic, fully testable offline.
- **MCP server.** Exposes tools such as `my_tasks`, `log_time`, `move_stage` to any agent (Pi, Claude Code, others).
- **Skill.** Teaches the agent how to work: query before writing, always confirm, and use Engram for context.

## Roadmap

Each stage is useful on its own.

1. **Foundation.** Profiles, keychain credentials, version detection, capability map, `doctor`, read-only "my tasks" and "unassigned tasks". First real check: an Odoo 16 Community instance.
2. **Safe writes.** Find projects, create one task per piece of work and log hours on it, with the full safety model. Past timesheets are never edited.
3. **Local ledger.** Local projects, pending hours, link to Odoo, outbox upload, strict mode.
4. **Agent.** MCP server, skill, Engram integration; writes confirmed by the user through MCP elicitation, never by the model. (done before stage 3)
5. **Team.** Install docs, per-person onboarding, presentation.

## Open questions

- **Language.** Python is proposed: Odoo is Python, the reference repo is Python, and an official MCP SDK exists. Not yet confirmed.
- **Keychain dependency.** `keyring` adds a third-party dependency. The alternative is a `0600` credentials file. To decide in stage 1.
- **Oldest supported version.** "Any version" needs a floor. Proposed: Odoo 14+.
