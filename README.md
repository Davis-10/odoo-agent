# AgentOdoo

A personal work assistant for Odoo: lets an AI agent see my assigned and unassigned tasks across Odoo instances of any version, log hours, and track work on projects that are not in Odoo yet. Every write needs my confirmation.

**New here? Follow the step-by-step guide: [INSTALL.md](INSTALL.md).**

Status: AgentOdoo ships a CLI, an MCP server and an agent skill. The only Odoo writes are creating a task and creating a timesheet line, each through a saved plan you confirm; nothing is ever updated or deleted in Odoo. Supported clients today: Claude Desktop (one-click `.mcpb` bundle; confirmation on a plan card), Claude Code (confirmation through MCP elicitation) and gentle-pi / Pi (confirmation extension). Planned: ChatGPT (needs a hosted server).

- Design: [`docs/design.md`](docs/design.md)

## Install

Python 3.11+.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Set up a profile

A profile names one Odoo instance. It stores URL, database and username in `~/.config/agentodoo/profiles.json` (mode `0600`, or under `$XDG_CONFIG_HOME`). It never stores the secret.

```bash
agentodoo profile add acme --url https://odoo.example.com --db acme --username you@example.com
agentodoo profile set-key acme         # prompts for your Odoo API key, stores it in the system keychain
agentodoo profile list
agentodoo profile set-closed-stages acme 41 57   # see "Closing stages" below
agentodoo profile set-closed-project-stages acme 31 35 36   # see "Closing project stages" below
agentodoo profile remove acme          # also deletes its key
```

Create the API key in Odoo under *My Profile → Account Security → New API Key*. Use the key, not your password.

- URLs must be `https://`. `--allow-http` exists only for a local instance.
- The API key is read only from the system keychain (`keyring`). Environment variables are never used, so a credential exported in your shell can never be picked up by mistake.
- `set-key --stdin` reads the key from standard input, for scripted setup.

## Commands

All commands print JSON. `--profile NAME` is optional when only one profile exists.

| Command | What it does |
|---|---|
| `agentodoo doctor` | Shows server version and edition, your user ID, and which real field backs each logical field. Fails if the version is older than 14 or a required field is missing. `missing_for_writes` lists the task description, timesheet and employee fields the writes need and this instance lacks; it does not change the status or exit code. |
| `agentodoo stages` | Lists stages with `id`, `name`, `folded`, `closed` (in this profile's closing list) and `project_ids` (`null` if the instance has no such field). Options: `--project-id N`, `--name TEXT` (case-insensitive substring). |
| `agentodoo profile set-closed-stages NAME ID [ID ...]` | Replaces the profile's closing stage list after checking every ID exists in Odoo. `--none` clears it. |
| `agentodoo project-stages` | Lists project stages with `id`, `name`, `folded` and `closed` (in this profile's closing project stage list). Option: `--name TEXT`. Fails on Odoo 14-15, which have no project stages. |
| `agentodoo profile set-closed-project-stages NAME ID [ID ...]` | Replaces the profile's closing project stage list after checking every ID exists in Odoo. `--none` clears it. |
| `agentodoo profile set-max-daily-hours NAME H` | Sets the most hours `time-plan` allows on one day for this profile (more than 0, at most 24; default 8). Stored locally only. |
| `agentodoo projects` | Projects where you have at least one open task. `--name TEXT` instead searches active projects whose name or client name contains TEXT (case-insensitive), whether or not you have tasks there. `--limit N` (1-1000, default 100). |
| `agentodoo tasks mine` | Open tasks assigned to you. |
| `agentodoo tasks unassigned` | Open tasks with no assignee. |
| `agentodoo task-plan --project-id N --name TEXT` | Plans one new task assigned to you. Options: `--description TEXT`, `--planned-hours H`, `--stage-id N`. Writes nothing to Odoo. |
| `agentodoo time-plan --task-id N --hours H --date YYYY-MM-DD --description TEXT` | Plans one timesheet line of yours on a task. Writes nothing to Odoo. |
| `agentodoo apply PLAN_ID --confirm PLAN_ID` | Writes a saved plan to Odoo, reads it back and saves a receipt. |

`tasks` options: `--project-id N` (only one Odoo project), `--limit N` (1-1000, default 100).

A task is open when it has no stage (e.g. a private task) or its stage is not folded, its stage is not in the profile's closing list, and on Odoo 17+ its state is not done or cancelled. Every Odoo version returns the same JSON shape: `id`, `name`, `project`, `stage`, `assignee_ids`, `deadline`, `planned_hours`, `status`. Fields an instance does not have are `null`.

`projects` rows, ordered by name then ID: `id`, `name`, `client`, `manager`, `stage` (`{id, name}` or `null`), `accepts_timesheets` (`null` when the instance has no such field), `analytic_account`, `my_open_tasks` (same open rule as `tasks mine`) and `flags`, a sorted list of:

- `closed_stage`: the project stage is in the profile's closing project stage list.
- `no_timesheets`: the project does not accept timesheets.
- `no_analytic_account`: the project has no analytic account.

`profile list` and `doctor` show `closed_stage_ids`, `closed_project_stage_ids` and `max_daily_hours`. `doctor` also returns `warnings` for configured closing stages or closing project stages that no longer exist in Odoo; warnings do not change its exit code.

## Closing stages

Some instances never mark finished work as closed. On some Odoo 16 instances, stages such as `FINALIZADO` are not folded and `is_closed` is false everywhere, so without help `tasks` would list finished tasks as open. Each profile can keep its own list of stage IDs that close a task. The list lives only in your local profile file; nothing is written to Odoo and nobody else is affected.

Stage names repeat across projects, because each project has its own stages, so you pick them by ID:

```bash
agentodoo stages --name finaliz                      # every stage whose name contains "finaliz", with IDs and project IDs
agentodoo profile set-closed-stages acme 41 57    # replaces the list; unknown IDs are rejected
agentodoo profile set-closed-stages acme --none   # clears it
```

## Closing project stages

Projects have their own stages (`project.project.stage`, Odoo 16+). Folding does not mean closed: a folded stage such as "In maintenance" can still hold projects that take hours. So each profile keeps an explicit list of project stage IDs that close a project; `projects` flags those projects `closed_stage`. Like closing stages, the list lives only in your local profile file.

```bash
agentodoo project-stages                                   # IDs, names, folded, closed
agentodoo profile set-closed-project-stages acme 31 35 36   # replaces the list; unknown IDs are rejected
agentodoo profile set-closed-project-stages acme --none     # clears it
```

## Writing: plan, confirm, apply

Every write is a saved plan first. A plan command checks the rules below, prints exactly what will be created and the command to write it:

```bash
agentodoo task-plan --project-id 501 --name "Checkout redesign" --planned-hours 6
agentodoo time-plan --task-id 9101 --hours 1.5 --date 2026-10-22 --description "Checkout tests"
# prints {"plan_id", "kind", "profile", "display", "values", "confirm": "agentodoo apply <id> --confirm <id>"}
agentodoo apply <id> --confirm <id>
```

`task-plan` is refused when the project does not exist or is flagged (see `projects`), the name is empty, planned hours are not a finite number of at least 0, or an open task in that project has the same name (ignoring case and surrounding spaces). With `--stage-id N` the task is created in that task stage (see `stages --project-id`) and the plan's `display` gains `stage` (`{id, name}`); it is refused when the stage does not exist, is not linked to the project, is folded, or is in the profile's closing stages, or when the instance has no stage-to-project field (run `doctor`). Without it Odoo picks the project's default stage. The task is assigned to you; the description is stored as one HTML paragraph.

`time-plan` is refused when the task does not exist, has no project (a private task), is not open, or its project is flagged; when hours are not a finite number in (0, 24]; when the date is not `YYYY-MM-DD` or is in the future; when the description is empty; when your Odoo user does not have exactly one active employee; or when an identical line (task, date, hours, description, your employee) exists. The line goes to the project's analytic account.

`time-plan` also adds up every timesheet line of your employee on that date, in every project, and refuses when those hours plus the new ones would exceed the profile's `max_daily_hours` (default 8; exactly the limit is allowed), e.g. `Day 2026-08-21 would have 8.5 h (8 h already logged + 0.5 h); the limit for profile 'acme' is 8 h`. The plan's `display` shows `day_hours_before` and `day_hours_after`. `apply` re-checks the limit, so a line logged in between makes it refuse. Change the limit with `agentodoo profile set-max-daily-hours acme 7.5`.

`apply`:

- `--confirm` must repeat the plan ID exactly. The plan's own profile is used.
- Refuses a plan whose file was modified, a plan that was already applied, and a plan whose earlier apply may have written something (`read_back_mismatch`, `unknown_outcome`): check Odoo instead of retrying. Only a `rejected` plan, where Odoo wrote nothing, can be applied again.
- Re-checks every rule of the plan against Odoo; if Odoo changed so that the values would differ, it refuses ("create a new plan").
- Creates the record, reads it back and compares every planned field (the task description is not compared, Odoo reformats HTML).
- Saves a receipt and prints it. `status` is `applied`, `read_back_mismatch` (written, but Odoo stored something else), `rejected` (Odoo refused it; nothing was written) or `unknown_outcome` (the connection failed; the record may exist, check Odoo before retrying). If the receipt cannot be saved, apply prints it on stdout under `receipt` with `receipt_not_saved`, and exits 4.

Plans and receipts live in `~/.local/state/agentodoo/` (or `$XDG_STATE_HOME/agentodoo/`): `plans/<plan_id>.json` and `receipts/<plan_id>.json`, directories `0700`, files `0600`.

Exit codes: `0` ok, `1` error, nothing was written (message on stderr), `2` invalid usage, `3` `doctor` found a problem, `4` something was or may have been written and needs your attention (read-back mismatch, unknown outcome, or receipt not saved).

## MCP server

An AI agent can use AgentOdoo as an MCP server: it runs locally over stdio with your own profiles and keychain key. It needs the optional `mcp` extra; the CLI works without it.

```bash
.venv/bin/pip install -e '.[mcp]'
agentodoo mcp            # stdio MCP server named "agentodoo"
```

The tools call the same code as the CLI, so every rule above applies identically, and each returns exactly the JSON its CLI command prints. Errors come back as MCP tool errors with the CLI's message. Every tool takes an optional `profile`.

| Tool | Arguments | Annotation |
|---|---|---|
| `doctor` | | read-only |
| `projects` | `name`, `limit` | read-only |
| `tasks` | `scope` (`mine` or `unassigned`), `project_id`, `limit` | read-only |
| `stages` | `project_id`, `name` | read-only |
| `project_stages` | `name` | read-only |
| `set_closing_stages` | `stage_ids`, `project_stage_ids` | stored locally, never writes to Odoo; not read-only, not destructive |
| `plan_task` | `project_id`, `name`, `description`, `planned_hours`, `stage_id` | saves a plan only; shows the plan card |
| `plan_time` | `task_id`, `hours`, `date`, `description` | saves a plan only; shows the plan card |
| `apply_plan` | `plan_id`, `host_confirmation` | destructive |
| `confirm_plan` | `plan_id` | destructive; app-only (called by the plan card) |

`set_closing_stages` replaces the closing stage list, the closing project stage list, or both, after checking every ID exists in Odoo like `profile set-closed-stages` / `set-closed-project-stages`; an omitted list is kept, `[]` clears one, unknown IDs are refused and nothing is stored. It returns `profile`, `closed_stage_ids` and `closed_project_stage_ids`.

### Plan card (MCP Apps)

Clients that show MCP Apps, such as Claude Desktop, display each plan from `plan_task` or `plan_time` as a card in the chat: what (task or timesheet line), project, task, stage, date, hours, description, your day total before → after, and the exact values, with **Confirm** and **Cancel** buttons. The card is the resource `ui://agentodoo/plan-<version>.html` (`text/html;profile=mcp-app`; versioned so hosts that cache UI resources show the new card after an update), a single self-contained HTML file that loads nothing from the network; its labels are in English.

- **Confirm** disables both buttons, calls `confirm_plan` with the plan ID and shows the receipt (status and record ID) or the error.
- **Cancel** disables both buttons and shows "Nothing was written". It calls nothing.

`confirm_plan` is declared `_meta.ui.visibility: ["app"]`, so the client hides it from the model and only the card can call it. It writes through the same `apply` as `apply_plan` and the CLI, with every rule below. In these clients `apply_plan` refuses and tells the agent to let you press Confirm on the card. A client counts as showing MCP Apps when it declares the extension `io.modelcontextprotocol/ui` with that MIME type; in every other client `confirm_plan` refuses (they may not hide it from the model) and `apply_plan` works as described below.

Known limit: the server cannot tell who called `confirm_plan`. The guarantee that a person pressed Confirm relies on the client enforcing app-only visibility, as the MCP Apps specification requires.

`apply_plan` never writes on the model's word. In clients without MCP Apps it checks, in this order:

1. **The client can ask you (MCP elicitation).** The server shows the plan (what, project, task, date, hours, your day total after the line, and the exact values) and writes only if you accept and tick confirm. Decline, cancel or an unticked confirm write nothing. Claude clients also show their own approval prompt because the tool is marked destructive.
2. **A host confirmation.** Hosts without elicitation, such as gentle-pi, confirm through their own dialog and then pass `host_confirmation`: the lowercase hex HMAC-SHA256 of the plan ID under the key in `host-gate.key`. Any other value counts as no confirmation.
3. **Otherwise it refuses**, writes nothing, and gives you the command to run yourself: `agentodoo apply <id> --confirm <id>`.

A plan that is unknown, modified or already applied is refused before anything is asked. The write is the same `apply` as the CLI: re-checks, read-back and receipt. A receipt whose status is not `applied` comes back as a tool error with the receipt.

Create the host key once (gentle-pi needs it):

```bash
agentodoo host-gate init            # {"key_path": ".../host-gate.key", "status": "created"} or "exists"
agentodoo host-gate init --rotate   # replace it; status "rotated"
```

The key is 32 random bytes stored as hex in `~/.local/state/agentodoo/host-gate.key` (mode `0600`), and is never printed. Without it, path 2 is unavailable.

Known limit: where the agent also has a shell, these gates stop accidental or unconfirmed writes, not a deliberately malicious agent, which could run the CLI or read the key itself.

## Set up in gentle-pi

Once per person, after [Install](#install) and [Set up a profile](#set-up-a-profile). Use absolute paths.

```bash
cd /path/to/AgentOdoo
.venv/bin/pip install -e '.[mcp]'
.venv/bin/agentodoo host-gate init
pi mcp add agentodoo -- /path/to/AgentOdoo/.venv/bin/agentodoo mcp   # the server name must be "agentodoo"
pi install /path/to/AgentOdoo                                        # confirmation extension + agentodoo skill
pi mcp list                                                          # agentodoo: connected, 9 tools
```

The repository root is a Pi package (`package.json`): it loads the confirmation extension from `integrations/pi/` and the skill from `skills/agentodoo/`. Start a new Pi session afterwards. Ask in plain language, for example "¿qué tareas tengo abiertas en Globex?" or "cargá 2 horas de la reunión con Globex de ayer". Before any write, Pi shows a dialog with the plan; nothing is written unless you approve it.

Claude Code and Claude Desktop: register the same stdio command (`/path/to/AgentOdoo/.venv/bin/agentodoo mcp`) as an MCP server named `agentodoo` and add `skills/agentodoo` as a skill. Claude Desktop confirms on the plan card; Claude Code through MCP elicitation and its own approval prompt. For Claude Desktop, the one-click `.mcpb` bundle from the releases page is the easiest path (see INSTALL.md). ChatGPT needs a remote server and is not supported yet.

## Development

```bash
make test   # offline: fakes and a localhost XML-RPC server, never a real Odoo; MCP tests skip without the extra
make lint
```

### Claude Desktop bundle

```bash
scripts/build-mcpb.sh        # dist/agentodoo-<version>.mcpb (needs npx; runs a pinned @anthropic-ai/mcpb validate + pack)
scripts/build-skill-zip.sh   # dist/agentodoo-skill.zip, the skill upload (agentodoo/SKILL.md)
```

The bundle (`packaging/mcpb/`) is a `uv` server: Claude Desktop installs Python and the `mcp` extra itself and runs `agentodoo mcp --config-from-host`. Only in that mode the server reads its one profile, `default`, from the `AGENTODOO_HOST_*` variables the install form fills in (URL, database, username, API key, and optionally `MAX_DAILY_HOURS`). The profile and key stay in memory; the profiles file and keychain are not used, plans and receipts still go to the state directory. Closing stages are not in the form: `set_closing_stages` keeps them in `<state dir>/host-settings.json` (mode 0600, no credentials), per Odoo URL and database, and the server loads them at start. If that file cannot be read, no closing stages apply and `doctor` warns. When a setting is missing or invalid, every tool says which one to fill in. Without the flag these variables are ignored.

## License

No license has been chosen yet, so all rights are reserved by the author. You may install and use it as described in INSTALL.md; ask before reusing the code elsewhere.
