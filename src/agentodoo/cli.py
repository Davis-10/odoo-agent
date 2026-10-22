from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import Any, Callable, TextIO

from agentodoo import __version__, service
from agentodoo.credentials import CredentialStore, KeyringCredentials
from agentodoo.errors import AgentOdooError, ConfigurationError
from agentodoo.host_gate import init_key
from agentodoo.plans import PlanStore, default_state_dir
from agentodoo.profiles import Profile, ProfileStore, default_config_path
from agentodoo.service import EXIT_ERROR, EXIT_OK
from agentodoo.transport import Transport, XmlRpcTransport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentodoo",
        description="Personal Odoo work assistant: tasks and timesheets, any Odoo version.",
        epilog="Exit codes: 0 ok, 1 error (nothing was written), 2 invalid usage, 3 doctor found a problem, "
               "4 something was or may have been written and needs your attention.",
    )
    parser.add_argument("--version", action="version", version=f"agentodoo {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    profile = commands.add_parser("profile", help="manage Odoo connection profiles")
    actions = profile.add_subparsers(dest="action", required=True, metavar="ACTION")
    add = actions.add_parser("add", help="add a profile (no secret is stored here)")
    add.add_argument("name")
    add.add_argument("--url", required=True, help="Odoo base URL, https:// only")
    add.add_argument("--db", required=True, help="Odoo database name")
    add.add_argument("--username", required=True, help="Odoo login")
    add.add_argument("--allow-http", action="store_true", help="allow http:// for a local instance")
    actions.add_parser("list", help="list profiles and whether a key is stored")
    remove = actions.add_parser("remove", help="remove a profile and its stored key")
    remove.add_argument("name")
    set_key = actions.add_parser("set-key", help="store the profile's Odoo API key in the system keychain")
    set_key.add_argument("name")
    set_key.add_argument("--stdin", action="store_true", help="read the key from standard input instead of prompting")
    closing = actions.add_parser(
        "set-closed-stages", help="replace the stage IDs that close a task for this profile (stored locally)",
        description="Tasks in these stages are left out of `tasks`. Find IDs with `agentodoo stages`. "
                    "Every ID is checked against Odoo; nothing is written to Odoo.")
    closing.add_argument("name")
    closing.add_argument("stage_ids", nargs="*", type=int, metavar="ID", help="Odoo stage ID")
    closing.add_argument("--none", action="store_true", help="clear the closing stage list")
    project_closing = actions.add_parser(
        "set-closed-project-stages",
        help="replace the project stage IDs that close a project for this profile (stored locally)",
        description="Projects in these project stages are flagged closed_stage by `projects`. Find IDs with "
                    "`agentodoo project-stages`. Every ID is checked against Odoo; nothing is written to Odoo.")
    project_closing.add_argument("name")
    project_closing.add_argument("stage_ids", nargs="*", type=int, metavar="ID", help="Odoo project stage ID")
    project_closing.add_argument("--none", action="store_true", help="clear the closing project stage list")

    daily = actions.add_parser(
        "set-max-daily-hours", help="set the most hours time-plan allows on one day for this profile (stored locally)",
        description="time-plan refuses a line when your hours on that day, in every project, would exceed this "
                    "limit. Default 8. Nothing is written to Odoo.")
    daily.add_argument("name")
    daily.add_argument("hours", type=float, metavar="H", help="hours, more than 0 and at most 24")

    check = commands.add_parser("doctor", help="check version, login and required fields (read-only)")
    add_profile_option(check)

    stages = commands.add_parser("stages", help="list Odoo stages with IDs, to choose closing stages (read-only)")
    add_profile_option(stages)
    stages.add_argument("--project-id", type=int, help="only stages linked to this Odoo project ID")
    stages.add_argument("--name", help="only stages whose name contains this text (case-insensitive)")

    project_stages = commands.add_parser(
        "project-stages", help="list project stages with IDs, to choose closing project stages (read-only)")
    add_profile_option(project_stages)
    project_stages.add_argument("--name", help="only project stages whose name contains this text (case-insensitive)")

    projects = commands.add_parser(
        "projects", help="list projects where I have open tasks, or search any project (read-only)",
        description="Without --name: projects with at least one of my open tasks. With --name: active projects "
                    "whose name or client name contains TEXT. Flags: closed_stage, no_timesheets, no_analytic_account.")
    add_profile_option(projects)
    projects.add_argument("--name", metavar="TEXT", help="search project or client name (case-insensitive)")
    projects.add_argument("--limit", type=limit_value, default=100, help="maximum projects to return (1-1000, default 100)")

    tasks = commands.add_parser("tasks", help="list open tasks (read-only)")
    scopes = tasks.add_subparsers(dest="scope", required=True, metavar="SCOPE")
    for scope, text in (("mine", "open tasks assigned to me"), ("unassigned", "open tasks with no assignee")):
        listing = scopes.add_parser(scope, help=text)
        add_profile_option(listing)
        listing.add_argument("--project-id", type=int, help="only tasks of this Odoo project ID")
        listing.add_argument("--limit", type=limit_value, default=100, help="maximum tasks to return (1-1000, default 100)")

    task_plan = commands.add_parser(
        "task-plan", help="plan one new task assigned to me (writes nothing to Odoo)",
        description="Saves a plan for one new task in the project, assigned to you. Refused when the project is "
                    "missing or flagged (see `projects`) or already has an open task with the same name, or when "
                    "--stage-id is not an open, non-closing stage linked to the project. "
                    "Write it with the `apply` command the plan prints.")
    add_profile_option(task_plan)
    task_plan.add_argument("--project-id", type=int, required=True, help="Odoo project ID")
    task_plan.add_argument("--name", required=True, metavar="TEXT", help="task title")
    task_plan.add_argument("--description", metavar="TEXT", help="plain text description")
    task_plan.add_argument("--planned-hours", type=float, metavar="H", help="planned hours, 0 or more")
    task_plan.add_argument("--stage-id", type=int, metavar="N",
                           help="task stage ID (see `stages --project-id`); without it Odoo picks the project's default stage")

    time_plan = commands.add_parser(
        "time-plan", help="plan one timesheet line on a task (writes nothing to Odoo)",
        description="Saves a plan for one timesheet line of yours on an open task of an unflagged project. "
                    "Refused for future dates, hours outside (0, 24], an identical existing line, or when your "
                    "hours that day in every project would exceed the profile's max_daily_hours "
                    "(see `profile set-max-daily-hours`). "
                    "Write it with the `apply` command the plan prints.")
    add_profile_option(time_plan)
    time_plan.add_argument("--task-id", type=int, required=True, help="Odoo task ID")
    time_plan.add_argument("--hours", type=float, required=True, metavar="H", help="hours, more than 0 and at most 24")
    time_plan.add_argument("--date", required=True, metavar="YYYY-MM-DD", help="day worked, today or earlier")
    time_plan.add_argument("--description", required=True, metavar="TEXT", help="what you did")

    apply = commands.add_parser(
        "apply", help="write a saved plan to Odoo, then read it back and save a receipt",
        description="Re-checks the plan against Odoo, creates the record, reads it back and saves a receipt. "
                    "Uses the plan's profile. Exit 4 means something was or may have been written and needs a look.")
    apply.add_argument("plan_id", metavar="PLAN_ID")
    apply.add_argument("--confirm", required=True, metavar="PLAN_ID", help="repeat the plan ID to confirm the write")

    gate = commands.add_parser("host-gate", help="manage the key a host (gentle-pi) uses to confirm MCP writes")
    gate_actions = gate.add_subparsers(dest="action", required=True, metavar="ACTION")
    gate_init = gate_actions.add_parser(
        "init", help="create the host confirmation key (never printed)",
        description="Creates <state dir>/host-gate.key (mode 0600). Keeps an existing key unless --rotate.")
    gate_init.add_argument("--rotate", action="store_true", help="replace the existing key")

    commands.add_parser(
        "mcp", help="run the MCP server over stdio (needs the mcp extra: pip install 'agentodoo[mcp]')",
        description="Serves read tools, plan tools and apply_plan to an MCP client over stdio. apply_plan writes "
                    "only after you confirm through the client (elicitation) or a host confirmation; "
                    "otherwise it refuses and gives the `agentodoo apply` command to run yourself.")
    return parser


def limit_value(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if not 1 <= value <= 1000:
        raise argparse.ArgumentTypeError("--limit must be between 1 and 1000")
    return value


def add_profile_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", help="profile name (optional when only one profile exists)")


def main(
    argv: list[str] | None = None,
    *,
    profiles: ProfileStore | None = None,
    credentials: CredentialStore | None = None,
    transport_factory: Callable[[Profile], Transport] | None = None,
    stdin: TextIO | None = None,
    state_dir: Path | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "profile" and args.action == "set-closed-stages" and bool(args.stage_ids) == args.none:
        parser.error("set-closed-stages: give stage IDs or --none")
    if args.command == "profile" and args.action == "set-closed-project-stages" and bool(args.stage_ids) == args.none:
        parser.error("set-closed-project-stages: give project stage IDs or --none")
    profiles = profiles or ProfileStore(default_config_path())
    credentials = credentials or KeyringCredentials()
    transport_factory = transport_factory or (lambda profile: XmlRpcTransport(profile.url))
    state_dir = state_dir or default_state_dir()
    store = PlanStore(state_dir)
    code = EXIT_OK
    try:
        if args.command == "mcp":
            return run_mcp(profiles, credentials, transport_factory, state_dir)
        if args.command == "apply":
            service.check_cli_confirmation(args.plan_id, args.confirm)
            outcome = service.apply(store, profiles, credentials, transport_factory, args.plan_id)
            print(json.dumps(outcome.output, indent=2, sort_keys=True))
            if outcome.message:
                print(f"error: {outcome.message}", file=sys.stderr)
            return outcome.code
        if args.command == "host-gate":
            result = init_key(state_dir, args.rotate)
        elif args.command == "profile":
            result = run_profile(args, profiles, credentials, stdin or sys.stdin, transport_factory)
        else:
            session = service.open_session(profiles.resolve(args.profile), credentials, transport_factory)
            if args.command == "doctor":
                result = service.doctor(session)
                code = service.doctor_exit_code(result)
            elif args.command == "stages":
                result = service.stages(session, args.project_id, args.name)
            elif args.command == "project-stages":
                result = service.project_stages(session, args.name)
            elif args.command == "projects":
                result = service.projects(session, args.name, args.limit)
            elif args.command == "task-plan":
                result = service.plan_task(store, session, args.project_id, args.name, args.description,
                                           args.planned_hours, args.stage_id)
            elif args.command == "time-plan":
                result = service.plan_time(store, session, args.task_id, args.hours, args.date, args.description)
            else:
                result = service.tasks(session, args.scope, args.project_id, args.limit)
    except AgentOdooError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return code


def run_mcp(profiles: ProfileStore, credentials: CredentialStore,
            transport_factory: Callable[[Profile], Transport], state_dir: Path) -> int:
    try:
        from agentodoo.mcp_server import build_server
    except ImportError as exc:
        if exc.name is None or not exc.name.startswith("mcp"):
            raise
        raise ConfigurationError("The MCP server needs the mcp extra: pip install 'agentodoo[mcp]'") from exc
    build_server(profiles=profiles, credentials=credentials, transport_factory=transport_factory,
                 state_dir=state_dir).run("stdio")
    return EXIT_OK


def run_profile(args: argparse.Namespace, profiles: ProfileStore, credentials: CredentialStore, stdin: TextIO,
                transport_factory: Callable[[Profile], Transport]) -> Any:
    if args.action == "add":
        return service.profile_add(profiles, args.name, args.url, args.db, args.username, args.allow_http)
    if args.action == "list":
        return service.profile_list(profiles, credentials)
    if args.action == "remove":
        return service.profile_remove(profiles, credentials, args.name)
    if args.action == "set-closed-stages":
        return service.profile_set_closed_stages(profiles, credentials, transport_factory, args.name, args.stage_ids)
    if args.action == "set-closed-project-stages":
        return service.profile_set_closed_project_stages(profiles, credentials, transport_factory, args.name,
                                                         args.stage_ids)
    if args.action == "set-max-daily-hours":
        return service.profile_set_max_daily_hours(profiles, args.name, args.hours)
    read = (lambda: stdin.readline()) if args.stdin else (lambda: getpass.getpass(f"Odoo API key for '{args.name}': "))
    return service.profile_set_key(profiles, credentials, args.name, read)
