---
name: agentodoo
description: "Trigger: Odoo hours, timesheet, parte de horas, imputar horas, cargar horas, Odoo task, AgentOdoo, mcp__agentodoo. Read Odoo projects/tasks and log hours or create tasks through AgentOdoo MCP tools, only after explicit user confirmation."
license: All rights reserved (no open-source license yet)
metadata:
  author: agentodoo
  version: "1.0"
---

## Activation Contract

Use when the user wants to see Odoo projects, tasks or stages, log hours, or create a task, and the AgentOdoo MCP server (`agentodoo`) is available. In Pi the tools are `mcp__agentodoo__<tool>`: `doctor`, `projects`, `tasks`, `stages`, `project_stages`, `set_closing_stages`, `plan_task`, `plan_time`, `apply_plan`.

Reply to the user in their language. Tool arguments (descriptions, task names) follow what the user wrote or approved.

## Hard Rules

- **Read before write.** Resolve the profile with `doctor` when unsure. Find the project with `projects` and the task with `tasks` before planning. Never guess or invent IDs.
- **Odoo is the source of truth.** Always query it live. Engram holds only context: mappings and preferences. Never store tasks, timesheets, hours or API keys in Engram.
- **Never require git or Engram.** They are optional evidence for developers; plain-language requests must work without them.
- **Hours honesty.** Hours you derive are estimates: label them "estimated" with their basis (Engram entries, commits, PRs). Never present them as recorded facts. With no basis, ask the user for the hours.
- **Times.** Engram timestamps are UTC; convert to the user's local time before assigning a date. Commit times can show when work was pushed, not when it was done; never treat them as effort.
- **Daily limit.** Respect the profile's `max_daily_hours` (default 8 h, shown by `doctor`). Redistribute only across days where the work really happened; never invent dates.
- **Every write = plan → table → explicit yes → `apply_plan(plan_id)` once.** Silence, "ok?" or approval of a different plan is not a yes.
- **Plan card clients (Claude Desktop).** When a `plan_task`/`plan_time` result has `"confirm_on": "card"`, the person confirms on the AgentOdoo plan card, not in the chat; follow its `next_step`: do not repeat the plan as a table, do not ask "shall I apply it?", and do not call `apply_plan` (it refuses there). Say in one line that nothing is written yet and that they should press **Confirm** on the card to write it or **Cancel** to discard it; offer to redo the plan if a detail (description, hours, date) should change. After the click, the card tells you the outcome in your context (written with its record id, cancelled, or failed): report that, and never apply or re-plan a plan the card says was written.
- **Never pass `host_confirmation`.** The host fills it after the user approves its dialog.
- **Never run `agentodoo apply` through a shell yourself.** If `apply_plan` refuses with a CLI command, show the command to the user and stop.
- **Past hours stay untouched.** Never offer to edit, move or delete existing timesheets; AgentOdoo cannot, and changes to past hours are the project manager's decision.
- **Listings by stage.** Show tasks and projects grouped by their `stage`.
- **Closing stages are the user's call.** When a listing shows stages whose names suggest finished or cancelled work (e.g. FINALIZADO, Hecho, Done, Cancelada) that are not closing yet (`doctor` shows `closed_stage_ids` / `closed_project_stage_ids`), ask once which of them mean finished, with their IDs. Then call `set_closing_stages` with exactly the confirmed IDs (`stage_ids` for tasks, `project_stage_ids` for projects). Never decide alone. Say it only changes the local view, not Odoo.
- **Never retry a plan** after `read_back_mismatch` or `unknown_outcome`.

## Decision Gates

| Situation | Action |
| --- | --- |
| Inside a git repo, Engram available | Developer mode |
| No repo/Engram, or the user just describes the work | Plain mode |
| Engram has `agentodoo/mapping/<repo-name>` | Use its profile, project id, default task id; still verify with `projects`/`tasks` |
| No mapping | Search with `projects --name`, propose a mapping, save it only after the user confirms |
| Relative date ("ayer", "el lunes") | Resolve to an absolute `YYYY-MM-DD` and confirm it in the table |
| Several matching projects or tasks | Show the candidates with IDs and ask which one |
| Work has no task yet | Propose `plan_task` (name, project, optional stage) before `plan_time` |
| Plan refused | See Troubleshooting; never work around a refusal |

## Execution Steps

### 1. Context

1. Unsure which Odoo instance: call `doctor`; report its status and the daily limit.
2. Developer mode: take the repo name from the current working directory (git top-level folder name). Search Engram for topic `agentodoo/mapping/<repo-name>`.
3. When the user confirms a new or changed mapping, save it with `mem_save`, topic key `agentodoo/mapping/<repo-name>`, content only: repo, profile, project id and name, default task id and name. Update the same topic key when it changes.

### 2. Find the target

1. `projects` lists projects where the user has open tasks; `projects --name TEXT` searches by name or client. Skip flagged projects (no timesheets, no analytic account, closing stage).
2. `tasks` (mine or unassigned) for the task; `stages`/`project_stages` only when choosing a stage.

### 3. Propose the work

- **Developer mode:** gather evidence from Engram observations and git history (`git log` dates, PR scope). For each proposed line show: date (local), estimated hours, description, evidence. Group by real work day.
- **Plain mode:** use only what the user said. Ask for anything missing (hours, date, task). Do not mine git or Engram.

### 4. Plan and confirm

1. Call `plan_task` / `plan_time` for each line. Each returns a `plan_id` and a `display` with the day total.
2. Show one table:

| # | Project / task | Date | Hours | Description | Day total after |
|---|---|---|---|---|---|

   Mark estimated hours as "(est.)" and list their basis below the table.
3. Ask: "Apply these N plans?" and STOP until the user answers.

### 5. Apply and report

1. After an explicit yes, call `apply_plan(plan_id)` once per plan. Do not pass `host_confirmation`.
2. Report per plan: record id and receipt status.
   - `applied`: done.
   - `read_back_mismatch` or `unknown_outcome`: tell the user to check the record in Odoo; do not retry the plan.
3. If `apply_plan` refuses and returns `agentodoo apply ID --confirm ID`, show that command and stop.

## Examples

### Developer mode (repo `globex-webshop`)

User: "Log my hours for the checkout redesign."

1. Engram mapping `agentodoo/mapping/globex-webshop` → profile `acme`, project 501 (Globex | Webshop). `tasks` shows no task "Checkout redesign"; propose `plan_task(project_id=501, name="Checkout redesign", stage_id=44)` (In progress).
2. Engram entries 2026-08-21 (UTC, shown in local UTC+2) and all 7 PRs committed 2026-08-22 00:20–01:46. Explain: the commit times show upload, so hours are estimated from Engram work entries.
3. Table: 2026-08-21 8 h (est.) across several lines, day total 8; 2026-08-22 2 h (est.), commits 01:12–01:45, after midnight; and so on.
4. A 0.5 h extra on 2026-08-21 is refused for the 8 h limit: offer to move it only to another day with evidence of that work, or drop it.
5. User: "yes" → `apply_plan` per plan → report task id, line ids, `applied`.

### Plain mode

User: "Log 2 hours for yesterday's meeting with Globex"

1. Today is 2026-10-22, so "yesterday" = 2026-10-21. `projects --name Globex` → 501; `tasks` → ask which task if several (for example a meetings task).
2. `plan_time(task_id=…, date="2026-10-21", hours=2, description="Meeting with Globex")`.
3. Table with date 2026-10-21, 2 h, day total; ask "Apply this plan?" and wait.
4. "yes" → `apply_plan` → "Done, line 9xxx logged (applied)."

## Troubleshooting

| Message contains | Say / do |
| --- | --- |
| `is a closing stage` / `is not open (closed or folded stage, or done)` | The task or stage is closed; suggest an open task or stage of the same project. |
| `is flagged` | The project cannot take hours (no timesheets, no analytic account or closing stage); ask for another project. |
| `Day … would have … the limit for profile … is … h` | Over the daily limit; show hours already logged; propose another real work day or fewer hours. Never invent a date. |
| `An identical timesheet line already exists (line N)` | That line is already in Odoo; do not create it again. |
| `already has an open task named` | Reuse that task instead of creating one. |
| `has no project (private task)` | Private tasks cannot take hours; ask for a task inside a project. |
| `No API key stored for profile` | Tell the user to run `agentodoo profile set-key <profile>` themselves; never ask them to paste the key in chat. |
| `Date … is in the future` | Confirm the date; hours are logged only for past or current days. |
| `Odoo changed since the plan` | Create a new plan, show it, ask again. |
| `No active employee is linked` / `Several active employees` | The Odoo user needs one employee; tell the user to ask the Odoo admin. |
| `apply_plan` refused with a CLI command | Show the command; stop. |

## Output Contract

Every write turn ends with either the plan table plus a yes/no question, or the apply report (record ids and receipt statuses). Estimated hours always carry "(est.)" and their basis.

## References

- `docs/design.md` in the AgentOdoo repository — three layers, safety model and confirmation gates.
- The AgentOdoo README — write rules, receipts and the daily limit.
