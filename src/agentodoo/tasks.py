"""Read-only task listings, expressed in logical fields so every Odoo version answers the same shape."""
from __future__ import annotations

from typing import Any

from agentodoo.session import OdooSession

# Closed values of `project.task.state` (Odoo 17+). Older versions close a task by folding its stage,
# and the profile's closing stages cover instances whose finished stages are not folded.
CLOSED_STATES = ["1_done", "1_canceled"]
OPTIONAL = (("deadline", "task.deadline"), ("planned_hours", "task.planned_hours"), ("status", "task.status"))


def open_task_domain(session: OdooSession, scope: str, project_id: int | None = None) -> list[Any]:
    """Domain for open tasks of one scope ("mine", "unassigned" or "any"), shared by every listing and plan."""
    caps = session.capabilities
    assignees = caps.field("task.assignees")
    stage = caps.field("task.stage")

    # A task without a stage (e.g. an Odoo 16+ private task) is open, not closed.
    domain: list[Any] = ["|", [stage, "=", False], [f"{stage}.{caps.field('stage.folded')}", "=", False]]
    if caps.has("task.status") and caps.field("task.status") == "state":
        domain.append(["state", "not in", CLOSED_STATES])
    if session.profile.closed_stage_ids:
        domain.append([stage, "not in", list(session.profile.closed_stage_ids)])
    if scope == "mine":
        many = caps.kind("task.assignees") == "many2many"
        domain.append([assignees, "in", [session.uid]] if many else [assignees, "=", session.uid])
    elif scope == "unassigned":
        domain.append([assignees, "=", False])
    if project_id is not None:
        domain.append([caps.field("task.project"), "=", project_id])
    return domain


def list_tasks(session: OdooSession, scope: str, project_id: int | None = None, limit: int = 100) -> dict:
    caps = session.capabilities
    assignees = caps.field("task.assignees")
    stage = caps.field("task.stage")
    project = caps.field("task.project")
    domain = open_task_domain(session, scope, project_id)

    optional = [(key, caps.field(logical)) for key, logical in OPTIONAL if caps.has(logical)]
    fields = [caps.field("task.name"), project, stage, assignees] + [name for _, name in optional]
    rows = session.read("project.task", "search_read", domain, fields=fields, order="id", limit=limit)

    tasks = []
    for row in rows:
        task = {
            "id": row["id"],
            "name": row[caps.field("task.name")],
            "project": _relation(row[project]),
            "stage": _relation(row[stage]),
            "assignee_ids": _ids(row[assignees], caps.kind("task.assignees")),
        }
        task |= {key: None for key, _ in OPTIONAL}
        task |= {key: row[name] if row[name] is not False else None for key, name in optional}
        tasks.append(task)
    return {"profile": session.profile.name, "scope": scope, "tasks": tasks}


def _relation(value: Any) -> dict | None:
    return {"id": value[0], "name": value[1]} if value else None


def _ids(value: Any, kind: str) -> list[int]:
    if not value:
        return []
    return [value[0]] if kind == "many2one" else list(value)  # many2one reads as [id, display_name]
