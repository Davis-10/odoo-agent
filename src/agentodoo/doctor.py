from __future__ import annotations

from agentodoo.session import OdooSession
from agentodoo.projects import missing_project_stage_ids
from agentodoo.stages import missing_stage_ids


def doctor(session: OdooSession) -> dict:
    """Check that this instance offers every field AgentOdoo needs. Read-only."""
    version = session.version
    user_id = session.uid
    caps = session.capabilities
    missing = caps.missing_required()
    project_stages = session.profile.closed_project_stage_ids
    # Without project stages (Odoo 14-15) every configured project stage ID is stale.
    stale_project_stages = (missing_project_stage_ids(session, project_stages) if caps.has("project_stage.name")
                            else list(project_stages))
    if not version.supported:
        status = "unsupported_version"
    elif missing:
        status = "incomplete"
    else:
        status = "ok"
    return {
        "status": status,
        "profile": session.profile.name,
        "url": session.profile.url,
        "database": session.profile.database,
        "server": version.report(),
        "user_id": user_id,
        "fields": caps.report(),
        "missing_required": missing,
        "missing_for_writes": caps.missing_for_writes(),  # informative: does not change status
        "closed_stage_ids": list(session.profile.closed_stage_ids),
        "closed_project_stage_ids": list(project_stages),
        "max_daily_hours": session.profile.max_daily_hours,
        "warnings": [f"closing stage ID {i} no longer exists in Odoo"
                     for i in missing_stage_ids(session, session.profile.closed_stage_ids)]
                    + [f"closing project stage ID {i} no longer exists in Odoo" for i in stale_project_stages],
    }
