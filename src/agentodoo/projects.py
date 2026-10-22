"""Read-only project listings and project stages, used to find where to log hours."""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from agentodoo.session import OdooSession
from agentodoo.stages import missing_stage_ids
from agentodoo.tasks import _relation, open_task_domain

PROJECT_MODEL = "project.project"
PROJECT_STAGE_MODEL = "project.project.stage"
OPTIONAL = (("client", "project.partner"), ("manager", "project.manager"), ("stage", "project.stage"),
            ("accepts_timesheets", "project.allow_timesheets"), ("analytic_account", "project.analytic"))


def list_projects(session: OdooSession, name: str | None = None, limit: int = 100) -> dict:
    """Projects with my open tasks, or, with `name`, active projects whose name or client name contains it."""
    caps = session.capabilities
    task_project = caps.field("task.project")
    project_name = caps.field("project.name")

    def my_open_tasks(domain: list[Any]) -> Counter:
        rows = session.read("project.task", "search_read", open_task_domain(session, "mine") + domain,
                            fields=[task_project])
        return Counter(row[task_project][0] for row in rows if row[task_project])

    if name is None:
        counts = my_open_tasks([[task_project, "!=", False]])
        domain: list[Any] = [["id", "in", sorted(counts)]]
    else:
        domain = [[project_name, "ilike", name]]
        if caps.has("project.partner"):
            domain = ["|", *domain, [f"{caps.field('project.partner')}.name", "ilike", name]]
    projects = read_projects(session, domain, limit)
    if name is not None:
        counts = my_open_tasks([[task_project, "in", [p["id"] for p in projects]]]) if projects else Counter()
    for project in projects:
        project["my_open_tasks"] = counts.get(project["id"], 0)
    return {"profile": session.profile.name, "projects": projects}


def read_projects(session: OdooSession, domain: list[Any], limit: int | None = None) -> list[dict]:
    """Projects matching `domain`, ordered by name then ID, with their flags. Shared by listings and plans."""
    caps = session.capabilities
    project_name = caps.field("project.name")
    optional = [(key, caps.field(logical)) for key, logical in OPTIONAL if caps.has(logical)]
    options = {"limit": limit} if limit is not None else {}
    rows = session.read(PROJECT_MODEL, "search_read", domain, fields=[project_name] + [f for _, f in optional],
                        order=f"{project_name}, id", **options)

    closed = set(session.profile.closed_project_stage_ids)
    projects = []
    for row in rows:
        project = {key: None for key, _ in OPTIONAL}
        for key, field in optional:
            value = row[field]
            project[key] = bool(value) if key == "accepts_timesheets" else _relation(value)
        flags = []
        if project["stage"] and project["stage"]["id"] in closed:
            flags.append("closed_stage")
        if project["accepts_timesheets"] is False:
            flags.append("no_timesheets")
        if caps.has("project.analytic") and project["analytic_account"] is None:
            flags.append("no_analytic_account")
        projects.append({"id": row["id"], "name": row[project_name], **project, "flags": sorted(flags)})
    return projects


def list_project_stages(session: OdooSession, name: str | None = None) -> dict:
    caps = session.capabilities
    stage_name, folded = caps.field("project_stage.name"), caps.field("project_stage.folded")
    rows = session.read(PROJECT_STAGE_MODEL, "search_read", [], fields=[stage_name, folded], order="id")
    closed = set(session.profile.closed_project_stage_ids)
    needle = (name or "").casefold()
    stages = [{"id": row["id"], "name": row[stage_name] or "", "folded": bool(row[folded]), "closed": row["id"] in closed}
              for row in rows if needle in (row[stage_name] or "").casefold()]
    return {"profile": session.profile.name, "project_stages": stages}


def missing_project_stage_ids(session: OdooSession, stage_ids: Iterable[int]) -> list[int]:
    """Return the given project stage IDs that do not exist in Odoo, sorted. Fails where project stages do not exist."""
    session.capabilities.field("project_stage.name")
    return missing_stage_ids(session, stage_ids, PROJECT_STAGE_MODEL)
