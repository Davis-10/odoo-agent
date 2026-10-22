"""The two allowed writes: plan a task or a timesheet line, then apply the plan.

Planning checks every refusal rule and derives the exact create values. Applying
runs the same planner again against live Odoo, creates through the session's
write gate, reads the record back and returns a receipt for whatever happened.
"""
from __future__ import annotations

import html
import json
import math
import re
from datetime import date
from typing import Any, Callable

from agentodoo.errors import OdooError, OdooRejectedError, OdooUnreachableError, RefusedError
from agentodoo.plans import utc_now
from agentodoo.projects import read_projects
from agentodoo.session import CREATE_FIELDS, OdooSession
from agentodoo.stages import STAGE_MODEL
from agentodoo.tasks import open_task_domain

TASK_MODEL = "project.task"
TIMESHEET_MODEL = "account.analytic.line"
EMPLOYEE_MODEL = "hr.employee"
FLOAT_TOLERANCE = 1e-6


def plan_task(session: OdooSession, project_id: int, name: str, description: str | None = None,
              planned_hours: float | None = None, stage_id: int | None = None) -> tuple[dict, dict]:
    """Values and display for one new task assigned to me, or RefusedError."""
    caps = session.capabilities
    title = name.strip()
    if not title:
        raise RefusedError("Task name must not be empty")
    if planned_hours is not None and not (math.isfinite(planned_hours) and planned_hours >= 0):
        raise RefusedError("Planned hours must be a finite number of at least 0")
    project = usable_project(session, project_id)
    stage = usable_stage(session, stage_id, project_id) if stage_id is not None else None

    task_name = caps.field("task.name")
    rows = session.read(TASK_MODEL, "search_read", open_task_domain(session, "any", project_id),
                        fields=[task_name], order="id")
    same = next((row for row in rows if (row[task_name] or "").strip().casefold() == title.casefold()), None)
    if same:
        raise RefusedError(f"Project {project_id} already has an open task named '{same[task_name]}' "
                           f"(task {same['id']})")

    uid = session.uid
    many = caps.kind("task.assignees") == "many2many"
    values: dict[str, Any] = {task_name: title, caps.field("task.project"): project_id,
                              caps.field("task.assignees"): [[6, 0, [uid]]] if many else uid}
    text = (description or "").strip()
    if text:
        values[caps.field("task.description")] = f"<p>{html.escape(text)}</p>"
    if planned_hours is not None:
        values[caps.field("task.planned_hours")] = float(planned_hours)
    if stage:
        values[caps.field("task.stage")] = stage["id"]
    display = {"project": {"id": project["id"], "name": project["name"]}, "client": project["client"],
               "name": title, "description": text or None,
               "planned_hours": float(planned_hours) if planned_hours is not None else None,
               "assignee_user_id": uid}
    if stage:
        display["stage"] = stage
    return values, display


def plan_time(session: OdooSession, task_id: int, hours: float, date: str, description: str) -> tuple[dict, dict]:
    """Values and display for one timesheet line of mine on an open task, or RefusedError."""
    caps = session.capabilities
    day = date
    if not (math.isfinite(hours) and 0 < hours <= 24):
        raise RefusedError("Hours must be a finite number greater than 0 and at most 24")
    if _parse_day(day) > _today():
        raise RefusedError(f"Date {day} is in the future")
    text = description.strip()
    if not text:
        raise RefusedError("Description must not be empty")

    task_name, task_project = caps.field("task.name"), caps.field("task.project")
    rows = session.read(TASK_MODEL, "search_read", [["id", "=", task_id]], fields=[task_name, task_project])
    if not rows:
        raise RefusedError(f"Task {task_id} not found")
    task = rows[0]
    if not task[task_project]:
        raise RefusedError(f"Task {task_id} has no project (private task); Odoo needs a project to log hours")
    project = usable_project(session, task[task_project][0])
    if not session.read(TASK_MODEL, "search_read", open_task_domain(session, "any") + [["id", "=", task_id]],
                        fields=["id"]):
        raise RefusedError(f"Task {task_id} is not open (closed or folded stage, or done); log hours on an open task")
    employee = my_employee(session)

    line = {logical: caps.field(f"timesheet.{logical}")
            for logical in ("name", "date", "hours", "project", "task", "employee", "account")}
    existing = session.read(TIMESHEET_MODEL, "search_read",
                            [[line["task"], "=", task_id], [line["date"], "=", day],
                             [line["employee"], "=", employee["id"]], [line["name"], "=", text]],
                            fields=[line["hours"]], order="id")
    same = next((row for row in existing if abs((row[line["hours"]] or 0.0) - hours) <= FLOAT_TOLERANCE), None)
    if same:
        raise RefusedError(f"An identical timesheet line already exists (line {same['id']})")

    day_lines = session.read(TIMESHEET_MODEL, "search_read",
                             [[line["employee"], "=", employee["id"]], [line["date"], "=", day]],
                             fields=[line["hours"]], order="id")
    before = sum((row[line["hours"]] or 0.0 for row in day_lines), 0.0)
    after = before + hours
    limit = session.profile.max_daily_hours
    if after > limit + FLOAT_TOLERANCE:
        raise RefusedError(f"Day {day} would have {_hours(after)} h ({_hours(before)} h already logged + "
                           f"{_hours(hours)} h); the limit for profile '{session.profile.name}' is {_hours(limit)} h")

    values = {line["name"]: text, line["date"]: day, line["hours"]: float(hours), line["project"]: project["id"],
              line["task"]: task_id, line["employee"]: employee["id"],
              line["account"]: project["analytic_account"]["id"]}
    display = {"task": {"id": task_id, "name": task[task_name]},
               "project": {"id": project["id"], "name": project["name"]}, "employee": employee,
               "analytic_account": project["analytic_account"], "date": day, "hours": float(hours),
               "description": text, "day_hours_before": round(before, 6), "day_hours_after": round(after, 6)}
    return values, display


def usable_project(session: OdooSession, project_id: int) -> dict:
    """The project, refused when missing or flagged by the same rules as `projects`."""
    found = read_projects(session, [["id", "=", project_id]])
    if not found:
        raise RefusedError(f"Project {project_id} not found")
    project = found[0]
    if project["flags"]:
        raise RefusedError(f"Project {project_id} is flagged {', '.join(project['flags'])}; choose another project")
    return project


def usable_stage(session: OdooSession, stage_id: int, project_id: int) -> dict:
    """The task stage, refused when missing, not linked to the project, folded or closing."""
    caps = session.capabilities
    if not caps.has("stage.projects"):
        raise RefusedError(f"This Odoo instance has no field linking stages to projects, so stage {stage_id} "
                           f"cannot be checked against project {project_id}; run agentodoo doctor")
    name, folded, projects = (caps.field(f"stage.{logical}") for logical in ("name", "folded", "projects"))
    rows = session.read(STAGE_MODEL, "search_read", [["id", "=", stage_id]], fields=[name, folded, projects])
    if not rows:
        raise RefusedError(f"Stage {stage_id} not found (project {project_id})")
    stage = rows[0]
    if project_id not in (stage[projects] or []):
        raise RefusedError(f"Stage {stage_id} is not linked to project {project_id}")
    if stage[folded]:
        raise RefusedError(f"Stage {stage_id} is folded; choose an open stage of project {project_id}")
    if stage_id in session.profile.closed_stage_ids:
        raise RefusedError(f"Stage {stage_id} is a closing stage of profile '{session.profile.name}'; "
                           f"choose an open stage of project {project_id}")
    return {"id": stage_id, "name": stage[name]}


def my_employee(session: OdooSession) -> dict:
    caps = session.capabilities
    name = caps.field("employee.name")
    rows = session.read(EMPLOYEE_MODEL, "search_read",
                        [[caps.field("employee.user"), "=", session.uid], [caps.field("employee.active"), "=", True]],
                        fields=[name], order="id")
    if not rows:
        raise RefusedError(f"No active employee is linked to your Odoo user (ID {session.uid}); "
                           "Odoo needs one to log hours")
    if len(rows) > 1:
        raise RefusedError(f"Several active employees are linked to your Odoo user (ID {session.uid}): "
                           f"{', '.join(str(row['id']) for row in rows)}")
    return {"id": rows[0]["id"], "name": rows[0][name]}


PLANNERS: dict[str, tuple[str, Callable[..., tuple[dict, dict]]]] = {
    "task": (TASK_MODEL, plan_task),
    "timesheet": (TIMESHEET_MODEL, plan_time),
}


def apply_plan(session: OdooSession, plan: dict) -> dict:
    """Re-check, create, read back. Refusals raise before the create; every later outcome is a receipt."""
    model, planner = PLANNERS[plan["kind"]]
    values, _ = planner(session, **plan["inputs"])
    if json.loads(json.dumps(values)) != plan["values"]:
        raise RefusedError("Odoo changed since the plan; create a new plan")
    values = plan["values"]
    receipt = {"plan_id": plan["plan_id"], "kind": plan["kind"], "profile": plan["profile"], "status": None,
               "record_id": None, "values": values, "read_back": None, "mismatches": [], "error": None,
               "applied_at": utc_now()}
    try:
        record_id = session.create(model, values)
    except OdooRejectedError as exc:
        return receipt | {"status": "rejected", "error": str(exc)}
    except OdooUnreachableError as exc:
        return receipt | {"status": "unknown_outcome", "error": str(exc)}
    receipt["record_id"] = record_id
    try:
        return _read_back(session, model, values, record_id, receipt)
    except Exception as exc:  # the record exists: any failure from here on must still produce a receipt
        return receipt | {"status": "read_back_mismatch",
                          "error": f"Unexpected error after creating record {record_id}: {type(exc).__name__}: {exc}"}


def _read_back(session: OdooSession, model: str, values: dict, record_id: int, receipt: dict) -> dict:
    try:
        rows = session.read(model, "search_read", [["id", "=", record_id]], fields=list(values))
    except OdooError as exc:
        return receipt | {"status": "read_back_mismatch", "error": f"Cannot read record {record_id} back: {exc}"}
    if not rows:
        return receipt | {"status": "read_back_mismatch", "error": f"Record {record_id} not found when reading it back"}
    mismatches = compare(session, model, values, rows[0])
    if mismatches:
        fields = ", ".join(m["field"] for m in mismatches)
        return receipt | {"status": "read_back_mismatch", "read_back": rows[0], "mismatches": mismatches,
                          "error": f"Odoo stored different values than planned: {fields}"}
    return receipt | {"status": "applied", "read_back": rows[0]}


def compare(session: OdooSession, model: str, values: dict, read_back: dict) -> list[dict]:
    """Planned fields that Odoo stored differently. Html is skipped: Odoo sanitizes it."""
    caps = session.capabilities
    kinds = {caps.field(logical): caps.kind(logical) for logical in CREATE_FIELDS[model] if caps.has(logical)}
    mismatches = []
    for name, planned in values.items():
        kind, actual = kinds.get(name), read_back.get(name)
        if kind == "html":
            continue
        if kind == "many2one":
            same = (actual[0] if actual else False) == planned
        elif kind == "many2many":
            same = set(actual or []) == set(planned[0][2])
        elif kind == "float":
            same = isinstance(actual, (int, float)) and abs(actual - planned) <= FLOAT_TOLERANCE
        else:
            same = actual == planned
        if not same:
            mismatches.append({"field": name, "planned": planned, "read_back": actual})
    return mismatches


def _hours(value: float) -> str:
    """Hours without float noise or trailing zeros: 9, 1.5."""
    return f"{round(value, 6):g}"


def _parse_day(text: str) -> date:
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return date.fromisoformat(text)
    except ValueError:
        pass
    raise RefusedError("Date must be YYYY-MM-DD")


def _today() -> date:
    return date.today()
