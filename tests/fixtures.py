"""Minimal `version()` and `fields_get` shapes for the Odoo versions we target.

They only contain the fields AgentOdoo cares about, with the names each version
uses. Real instances are checked by `agentodoo doctor`, not by these fixtures.
"""
from __future__ import annotations

import copy

COMMON_TASK = {
    "name": {"type": "char", "string": "Title"},
    "project_id": {"type": "many2one", "string": "Project", "relation": "project.project"},
    "stage_id": {"type": "many2one", "string": "Stage", "relation": "project.task.type"},
    "date_deadline": {"type": "date", "string": "Deadline"},
    "description": {"type": "html", "string": "Description"},
    "active": {"type": "boolean", "string": "Active"},
}
ONE_ASSIGNEE = {"user_id": {"type": "many2one", "string": "Assigned to", "relation": "res.users"}}
MANY_ASSIGNEES = {"user_ids": {"type": "many2many", "string": "Assignees", "relation": "res.users"}}
LEGACY_TASK = {
    "planned_hours": {"type": "float", "string": "Initially Planned Hours"},
    "kanban_state": {"type": "selection", "string": "Kanban State"},
}
MODERN_TASK = {
    "allocated_hours": {"type": "float", "string": "Allocated Time"},
    "state": {"type": "selection", "string": "State"},
}
PROJECT = {
    "name": {"type": "char", "string": "Name"},
    "active": {"type": "boolean", "string": "Active"},
    "partner_id": {"type": "many2one", "string": "Customer", "relation": "res.partner"},
    "user_id": {"type": "many2one", "string": "Project Manager", "relation": "res.users"},
    "allow_timesheets": {"type": "boolean", "string": "Timesheets"},
}
PROJECT_STAGE_FIELD = {"stage_id": {"type": "many2one", "string": "Stage", "relation": "project.project.stage"}}
LEGACY_ANALYTIC = {"analytic_account_id": {"type": "many2one", "string": "Analytic Account",
                                           "relation": "account.analytic.account"}}
MODERN_ANALYTIC = {"account_id": {"type": "many2one", "string": "Analytic Account",
                                  "relation": "account.analytic.account"}}
PROJECT_STAGE = {"name": {"type": "char", "string": "Name"}, "fold": {"type": "boolean", "string": "Folded"}}
TIMESHEET = {
    "name": {"type": "char", "string": "Description"},
    "date": {"type": "date", "string": "Date"},
    "unit_amount": {"type": "float", "string": "Quantity"},
    "project_id": {"type": "many2one", "string": "Project", "relation": "project.project"},
    "task_id": {"type": "many2one", "string": "Task", "relation": "project.task"},
    "employee_id": {"type": "many2one", "string": "Employee", "relation": "hr.employee"},
    "account_id": {"type": "many2one", "string": "Analytic Account", "relation": "account.analytic.account"},
    "user_id": {"type": "many2one", "string": "User", "relation": "res.users"},
}
EMPLOYEE = {
    "name": {"type": "char", "string": "Name"},
    "active": {"type": "boolean", "string": "Active"},
    "user_id": {"type": "many2one", "string": "User", "relation": "res.users"},
}
# Related models AgentOdoo never asks fields_get for; the fake needs them to follow dotted paths.
NAMED = {"name": {"type": "char", "string": "Name"}}
STAGE = {
    "name": {"type": "char", "string": "Name"},
    "fold": {"type": "boolean", "string": "Folded in Kanban"},
    "project_ids": {"type": "many2many", "string": "Projects", "relation": "project.project"},
}


def version(major: int, edition: str = "") -> dict:
    return {
        "server_version": f"{major}.0" + ("+e" if edition else ""),
        "server_version_info": [major, 0, 0, "final", 0, edition],
        "server_serie": f"{major}.0",
        "protocol_version": 1,
    }


def instance(major: int) -> tuple[dict, dict]:
    task = dict(COMMON_TASK)
    task |= ONE_ASSIGNEE if major <= 14 else MANY_ASSIGNEES
    task |= LEGACY_TASK if major <= 16 else MODERN_TASK
    project = dict(PROJECT) | (MODERN_ANALYTIC if major >= 18 else LEGACY_ANALYTIC)
    fields = {"project.task": task, "project.project": project, "project.task.type": dict(STAGE),
              "account.analytic.line": dict(TIMESHEET), "hr.employee": dict(EMPLOYEE)}
    if major >= 16:  # project stages arrived in Odoo 16
        project |= PROJECT_STAGE_FIELD
        fields["project.project.stage"] = dict(PROJECT_STAGE)
    fields |= {model: dict(NAMED) for model in ("res.partner", "res.users", "account.analytic.account")}
    return version(major), copy.deepcopy(fields)  # tests mutate field specs


# Shape of a real Odoo 16 Community answer.
ODOO16_VERSION = {
    "server_version": "16.0-20240101",
    "server_version_info": [16, 0, 0, "final", 0, ""],
    "server_serie": "16.0",
    "protocol_version": 1,
}
