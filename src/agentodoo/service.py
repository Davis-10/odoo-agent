"""What every front end does: one function per command, shared by the CLI and the MCP server.

Functions take plain values plus the stores they need and return the JSON-able
result a front end prints, or raise the usual AgentOdooError subclasses. Every
safety rule lives here or below, never in a front end.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable

from agentodoo.credentials import CredentialStore, require_secret
from agentodoo.doctor import doctor as doctor_report
from agentodoo.errors import ConfigurationError, RefusedError
from agentodoo.plans import PlanStore, new_plan
from agentodoo.profiles import Profile, ProfileStore
from agentodoo.projects import list_project_stages, list_projects, missing_project_stage_ids
from agentodoo.session import OdooSession
from agentodoo.stages import list_stages, missing_stage_ids
from agentodoo.tasks import list_tasks
from agentodoo.transport import Transport
from agentodoo.writes import PLANNERS, apply_plan

TransportFactory = Callable[[Profile], Transport]

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CHECK_FAILED = 3
EXIT_ATTENTION = 4  # something was or may have been written to Odoo and needs a look

# Receipt status -> exit code and what to tell the user.
OUTCOMES = {
    "applied": (EXIT_OK, None),
    "rejected": (EXIT_ERROR, "{error}; nothing was written"),
    "unknown_outcome": (EXIT_ATTENTION, "{error}; the record may have been created. Check Odoo before retrying"),
    "read_back_mismatch": (EXIT_ATTENTION, "{error}; record {record_id} was created, check it in Odoo"),
}
LIMIT_RANGE = (1, 1000)


def open_session(profile: Profile, credentials: CredentialStore, transport_factory: TransportFactory) -> OdooSession:
    return OdooSession(profile, transport_factory(profile), require_secret(credentials, profile.name))


def check_limit(limit: int) -> int:
    if not LIMIT_RANGE[0] <= limit <= LIMIT_RANGE[1]:
        raise ConfigurationError("limit must be between 1 and 1000")
    return limit


# Read commands -----------------------------------------------------------------

def doctor(session: OdooSession) -> dict:
    return doctor_report(session)


def doctor_exit_code(result: dict) -> int:
    return EXIT_OK if result["status"] == "ok" else EXIT_CHECK_FAILED


def stages(session: OdooSession, project_id: int | None = None, name: str | None = None) -> list[dict]:
    return list_stages(session, project_id, name)


def project_stages(session: OdooSession, name: str | None = None) -> list[dict]:
    return list_project_stages(session, name)


def projects(session: OdooSession, name: str | None = None, limit: int = 100) -> list[dict]:
    return list_projects(session, name, check_limit(limit))


def tasks(session: OdooSession, scope: str, project_id: int | None = None, limit: int = 100) -> list[dict]:
    if scope not in ("mine", "unassigned"):
        raise ConfigurationError("scope must be 'mine' or 'unassigned'")
    return list_tasks(session, scope, project_id, check_limit(limit))


# Plans -------------------------------------------------------------------------

def confirm_command(plan_id: str) -> str:
    return f"agentodoo apply {plan_id} --confirm {plan_id}"


def save_plan(store: PlanStore, session: OdooSession, kind: str, inputs: dict) -> dict:
    _, planner = PLANNERS[kind]
    values, display = planner(session, **inputs)
    plan = new_plan(kind, session.profile.name, inputs, values, display)
    store.save_plan(plan)
    return {key: plan[key] for key in ("plan_id", "kind", "profile", "display", "values")} | {
        "confirm": confirm_command(plan["plan_id"])}


def plan_task(store: PlanStore, session: OdooSession, project_id: int, name: str, description: str | None = None,
              planned_hours: float | None = None, stage_id: int | None = None) -> dict:
    inputs = {"project_id": project_id, "name": name, "description": description, "planned_hours": planned_hours}
    if stage_id is not None:  # absent from older plans, so apply keeps working for them
        inputs["stage_id"] = stage_id
    return save_plan(store, session, "task", inputs)


def plan_time(store: PlanStore, session: OdooSession, task_id: int, hours: float, date: str,
              description: str) -> dict:
    return save_plan(store, session, "timesheet",
                     {"task_id": task_id, "hours": hours, "date": date, "description": description})


# Apply -------------------------------------------------------------------------

def check_cli_confirmation(plan_id: str, confirm: str) -> None:
    if confirm != plan_id:
        raise RefusedError("--confirm must repeat the plan ID exactly; nothing was written")


def load_applicable_plan(store: PlanStore, plan_id: str) -> dict:
    """The saved plan, refused when it was modified, applied, or may already have written something."""
    plan = store.load_plan(plan_id)
    previous = store.load_receipt(plan_id)
    if previous and previous.get("status") == "applied":
        raise RefusedError(f"Plan {plan_id} was already applied (record {previous.get('record_id')}); "
                           "nothing was written")
    if previous and previous.get("status") != "rejected":
        # Only a rejected create is known to have written nothing; anything else must never be retried blindly.
        raise RefusedError(f"Plan {plan_id} already has a {previous.get('status')} receipt: a record was or may "
                           "have been created. Check Odoo; nothing was written")
    return plan


@dataclass
class ApplyOutcome:
    """What apply did: `output` is the JSON to show, `code` the CLI exit code, `message` the error to show."""
    output: dict
    code: int
    message: str | None


def apply(store: PlanStore, profiles: ProfileStore, credentials: CredentialStore,
          transport_factory: TransportFactory, plan_id: str) -> ApplyOutcome:
    """Write a confirmed plan. Callers must have obtained the user's confirmation first."""
    plan = load_applicable_plan(store, plan_id)
    session = open_session(profiles.get(plan["profile"]), credentials, transport_factory)
    receipt = apply_plan(session, plan)
    try:
        store.save_receipt(receipt)  # before showing it: the receipt must exist even if output is lost
    except ConfigurationError as exc:
        return ApplyOutcome({"receipt": receipt, "receipt_not_saved": str(exc)}, EXIT_ATTENTION,
                            "The receipt could not be saved; it is printed on stdout. Keep it")
    code, message = OUTCOMES[receipt["status"]]
    if receipt["status"] == "read_back_mismatch" and receipt["mismatches"]:
        message = (f"Odoo stored different values than planned for record {receipt['record_id']}: "
                   f"{', '.join(m['field'] for m in receipt['mismatches'])}; check it in Odoo")
    return ApplyOutcome(receipt, code, message.format(**receipt) if message else None)


# Profiles ----------------------------------------------------------------------

def profile_add(profiles: ProfileStore, name: str, url: str, database: str, username: str,
                allow_http: bool = False) -> dict:
    profiles.add(Profile.create(name, url, database, username, allow_http))
    return {"added": name}


def profile_list(profiles: ProfileStore, credentials: CredentialStore) -> list[dict]:
    return [asdict(p) | {"has_key": bool(credentials.get(p.name))} for p in profiles.list()]


def profile_remove(profiles: ProfileStore, credentials: CredentialStore, name: str) -> dict:
    profiles.get(name)
    credentials.delete(name)
    profiles.remove(name)
    return {"removed": name}


def profile_set_key(profiles: ProfileStore, credentials: CredentialStore, name: str,
                    read_secret: Callable[[], str]) -> dict:
    profiles.get(name)
    secret = read_secret().strip()
    if not secret:
        raise ConfigurationError("API key must not be empty")
    credentials.set(name, secret)
    return {"key_stored": name}


def profile_set_closed_stages(profiles: ProfileStore, credentials: CredentialStore,
                              transport_factory: TransportFactory, name: str, stage_ids: list[int]) -> dict:
    profile = profiles.get(name)
    if stage_ids:
        unknown = missing_stage_ids(open_session(profile, credentials, transport_factory), stage_ids)
        if unknown:
            raise ConfigurationError(f"Unknown stage IDs in Odoo: {', '.join(map(str, unknown))}")
    profile = profiles.set_closed_stages(name, stage_ids)
    return {"profile": profile.name, "closed_stage_ids": list(profile.closed_stage_ids)}


def profile_set_closed_project_stages(profiles: ProfileStore, credentials: CredentialStore,
                                      transport_factory: TransportFactory, name: str, stage_ids: list[int]) -> dict:
    profile = profiles.get(name)
    if stage_ids:
        unknown = missing_project_stage_ids(open_session(profile, credentials, transport_factory), stage_ids)
        if unknown:
            raise ConfigurationError(f"Unknown project stage IDs in Odoo: {', '.join(map(str, unknown))}")
    profile = profiles.set_closed_project_stages(name, stage_ids)
    return {"profile": profile.name, "closed_project_stage_ids": list(profile.closed_project_stage_ids)}


def profile_set_max_daily_hours(profiles: ProfileStore, name: str, hours: float) -> dict:
    profile = profiles.set_max_daily_hours(name, hours)
    return {"profile": profile.name, "max_daily_hours": profile.max_daily_hours}

