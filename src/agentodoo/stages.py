"""Read-only stage listing, used to choose a profile's closing stages."""
from __future__ import annotations

from typing import Any, Iterable

from agentodoo.session import OdooSession

STAGE_MODEL = "project.task.type"


def list_stages(session: OdooSession, project_id: int | None = None, name: str | None = None) -> dict:
    caps = session.capabilities
    domain: list[list[Any]] = []
    if project_id is not None:
        domain.append([caps.field("stage.projects"), "in", [project_id]])
    projects = caps.field("stage.projects") if caps.has("stage.projects") else None
    fields = [caps.field("stage.name"), caps.field("stage.folded")] + ([projects] if projects else [])
    rows = session.read(STAGE_MODEL, "search_read", domain, fields=fields, order="id")

    closed = set(session.profile.closed_stage_ids)
    needle = (name or "").casefold()
    stages = []
    for row in rows:
        stage_name = row[caps.field("stage.name")] or ""
        if needle not in stage_name.casefold():
            continue
        stages.append({
            "id": row["id"],
            "name": stage_name,
            "folded": bool(row[caps.field("stage.folded")]),
            "closed": row["id"] in closed,
            "project_ids": list(row[projects] or []) if projects else None,
        })
    return {"profile": session.profile.name, "stages": stages}


def missing_stage_ids(session: OdooSession, stage_ids: Iterable[int], model: str = STAGE_MODEL) -> list[int]:
    """Return the given stage IDs that do not exist in Odoo, sorted."""
    wanted = sorted(set(stage_ids))
    if not wanted:
        return []
    rows = session.read(model, "search_read", [["id", "in", wanted]], fields=["id"])
    found = {row["id"] for row in rows}
    return [i for i in wanted if i not in found]
