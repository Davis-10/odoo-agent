"""The MCP server (stage 4): the CLI's commands as tools, over the same service functions.

Needs the optional `mcp` extra. Tools return exactly the JSON the CLI prints; errors
become MCP tool errors with the CLI's message. `apply_plan` writes only after the
user confirmed, checked in this order: (a) the client supports elicitation and the
user accepts the server's question, (b) a valid host confirmation (see host_gate),
(c) otherwise it refuses with the CLI command to run.
"""

import json
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

from mcp.server.mcpserver import AcceptedElicitation, Context, Elicit, ElicitationResult, MCPServer, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from agentodoo import service
from agentodoo.credentials import CredentialStore, KeyringCredentials
from agentodoo.errors import AgentOdooError
from agentodoo.host_gate import host_confirmation_valid
from agentodoo.plans import PlanStore, default_state_dir
from agentodoo.profiles import Profile, ProfileStore, default_config_path
from agentodoo.transport import Transport, XmlRpcTransport

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
PLAN = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
APPLY = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True)

ProfileArg = Annotated[str | None, Field(description="profile name (optional when only one profile exists)")]
LimitArg = Annotated[int, Field(description="maximum rows to return (1-1000)")]


class Confirmation(BaseModel):
    confirm: bool = Field(description="Write this to Odoo")


def as_cli_json(result: Any) -> str:
    return json.dumps(result, indent=2, sort_keys=True, default=str)


def confirmation_message(plan: dict) -> str:
    """What the user is asked to confirm: what, where, when, how much."""
    display, values = plan["display"], plan["values"]
    lines = [f"Write this to Odoo (profile {plan['profile']}, plan {plan['plan_id']})?"]
    if plan["kind"] == "timesheet":
        lines += [f"What: timesheet line \"{display['description']}\"",
                  f"Project: {display['project']['name']} ({display['project']['id']})",
                  f"Task: {display['task']['name']} ({display['task']['id']})",
                  f"Date: {display['date']}", f"Hours: {display['hours']:g}"]
        if "day_hours_after" in display:
            lines.append(f"Your day total after this line: {display['day_hours_after']:g} h")
    else:
        lines += [f"What: new task \"{display['name']}\" assigned to you",
                  f"Project: {display['project']['name']} ({display['project']['id']})"]
        if display.get("stage"):
            lines.append(f"Stage: {display['stage']['name']} ({display['stage']['id']})")
        if display.get("planned_hours") is not None:
            lines.append(f"Planned hours: {display['planned_hours']:g}")
        if display.get("description"):
            lines.append(f"Description: {display['description']}")
    lines.append(f"Values: {json.dumps(values, sort_keys=True, ensure_ascii=False)}")
    return "\n".join(lines)


def client_can_elicit(ctx: Context) -> bool:
    capabilities = ctx.client_capabilities
    return capabilities is not None and capabilities.elicitation is not None


def build_server(*, profiles: ProfileStore | None = None, credentials: CredentialStore | None = None,
                 transport_factory: Callable[[Profile], Transport] | None = None,
                 state_dir: Path | None = None) -> MCPServer:
    profiles = profiles or ProfileStore(default_config_path())
    credentials = credentials or KeyringCredentials()
    transport_factory = transport_factory or (lambda profile: XmlRpcTransport(profile.url))
    state_dir = state_dir or default_state_dir()
    store = PlanStore(state_dir)
    server = MCPServer("agentodoo", instructions=(
        "Odoo tasks and timesheets. Read tools never write. Writes are two steps: plan_task or plan_time "
        "saves a plan, apply_plan writes it only after the user confirms it outside the model."))

    def run(action: Callable[[], Any]) -> str:
        try:
            return as_cli_json(action())
        except AgentOdooError as exc:
            raise ToolError(str(exc)) from exc

    def session(profile: str | None):
        return service.open_session(profiles.resolve(profile), credentials, transport_factory)

    @server.tool(annotations=READ, structured_output=False)
    def doctor(profile: ProfileArg = None) -> str:
        """Check server version, login and required fields (read-only)."""
        return run(lambda: service.doctor(session(profile)))

    @server.tool(annotations=READ, structured_output=False)
    def projects(name: str | None = None, limit: LimitArg = 100, profile: ProfileArg = None) -> str:
        """Projects where I have open tasks; with `name`, search any active project or client (read-only)."""
        return run(lambda: service.projects(session(profile), name, limit))

    @server.tool(annotations=READ, structured_output=False)
    def tasks(scope: Literal["mine", "unassigned"], project_id: int | None = None, limit: LimitArg = 100,
              profile: ProfileArg = None) -> str:
        """Open tasks assigned to me (`mine`) or with no assignee (`unassigned`) (read-only)."""
        return run(lambda: service.tasks(session(profile), scope, project_id, limit))

    @server.tool(annotations=READ, structured_output=False)
    def stages(project_id: int | None = None, name: str | None = None, profile: ProfileArg = None) -> str:
        """Task stages with IDs, optionally of one project or matching a name (read-only)."""
        return run(lambda: service.stages(session(profile), project_id, name))

    @server.tool(annotations=READ, structured_output=False)
    def project_stages(name: str | None = None, profile: ProfileArg = None) -> str:
        """Project stages with IDs (Odoo 16+) (read-only)."""
        return run(lambda: service.project_stages(session(profile), name))

    @server.tool(annotations=PLAN, structured_output=False)
    def plan_task(project_id: int, name: str, description: str | None = None, planned_hours: float | None = None,
                  stage_id: int | None = None, profile: ProfileArg = None) -> str:
        """Plan one new task assigned to me. Saves a plan only; nothing is written to Odoo."""
        return run(lambda: service.plan_task(store, session(profile), project_id, name, description,
                                             planned_hours, stage_id))

    @server.tool(annotations=PLAN, structured_output=False)
    def plan_time(task_id: int, hours: float, date: str, description: str, profile: ProfileArg = None) -> str:
        """Plan one timesheet line of mine (date YYYY-MM-DD). Saves a plan only; nothing is written to Odoo."""
        return run(lambda: service.plan_time(store, session(profile), task_id, hours, date, description))

    def ask_user(plan_id: str, ctx: Context) -> Elicit[Confirmation] | None:
        # Refusals (unknown, modified, already applied) come before any question.
        try:
            plan = service.load_applicable_plan(store, plan_id)
        except AgentOdooError as exc:
            raise ToolError(str(exc)) from exc
        if not client_can_elicit(ctx):
            return None
        return Elicit(confirmation_message(plan), Confirmation)

    @server.tool(annotations=APPLY, structured_output=False)
    def apply_plan(plan_id: str, ctx: Context,
                   answer: Annotated[ElicitationResult[Confirmation], Resolve(ask_user)],
                   host_confirmation: str | None = None) -> str:
        """Write a saved plan to Odoo after the user confirms it; returns the receipt.

        The user confirms in the client's own dialog. Never invent `host_confirmation`: only the host sets it.
        Without a confirmation this refuses and gives the command the user can run instead.
        """
        if client_can_elicit(ctx):
            confirmed = (isinstance(answer, AcceptedElicitation) and isinstance(answer.data, Confirmation)
                         and answer.data.confirm is True)
            if not confirmed:
                raise ToolError(f"Plan {plan_id} was not confirmed; nothing was written")
        elif not host_confirmation_valid(state_dir, plan_id, host_confirmation):
            raise ToolError(f"Plan {plan_id} needs your confirmation and this client cannot ask for it; nothing "
                            f"was written. To write it, run: {service.confirm_command(plan_id)}")
        try:
            outcome = service.apply(store, profiles, credentials, transport_factory, plan_id)
        except AgentOdooError as exc:
            raise ToolError(str(exc)) from exc
        text = as_cli_json(outcome.output)
        if outcome.code != service.EXIT_OK:
            raise ToolError(f"{outcome.message}\n{text}")
        return text

    return server
