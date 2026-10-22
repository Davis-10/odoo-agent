"""Map AgentOdoo's logical fields to the real field names of one Odoo instance.

Field names change between Odoo versions (`planned_hours` became
`allocated_hours` in 17, `user_id` became `user_ids` in 15). Business code asks
for logical names only; this module resolves them from the server's `fields_get`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from agentodoo.errors import NotAllowedError

MIN_SUPPORTED_MAJOR = 14


@dataclass(frozen=True)
class FieldSpec:
    logical: str
    model: str
    candidates: tuple[str, ...]
    types: tuple[str, ...]
    required: bool
    write: bool = False  # needed by the coming writes; reported by doctor, never required for reading
    relation: str | None = None  # when set, the field must point to this model


FIELD_SPECS = (
    FieldSpec("project.name", "project.project", ("name",), ("char",), True),
    FieldSpec("project.partner", "project.project", ("partner_id",), ("many2one",), False),
    FieldSpec("project.manager", "project.project", ("user_id",), ("many2one",), False),
    FieldSpec("project.stage", "project.project", ("stage_id",), ("many2one",), False,
              relation="project.project.stage"),  # Odoo 16+
    FieldSpec("project.allow_timesheets", "project.project", ("allow_timesheets",), ("boolean",), False),
    FieldSpec("project.analytic", "project.project", ("analytic_account_id", "account_id"), ("many2one",), False),
    FieldSpec("project_stage.name", "project.project.stage", ("name",), ("char",), False),
    FieldSpec("project_stage.folded", "project.project.stage", ("fold",), ("boolean",), False),
    FieldSpec("stage.folded", "project.task.type", ("fold",), ("boolean",), True),
    FieldSpec("stage.name", "project.task.type", ("name",), ("char",), True),
    FieldSpec("stage.projects", "project.task.type", ("project_ids",), ("many2many",), False),
    FieldSpec("task.name", "project.task", ("name",), ("char",), True),
    FieldSpec("task.project", "project.task", ("project_id",), ("many2one",), True),
    FieldSpec("task.stage", "project.task", ("stage_id",), ("many2one",), True),
    FieldSpec("task.assignees", "project.task", ("user_ids", "user_id"), ("many2many", "many2one"), True),
    FieldSpec("task.deadline", "project.task", ("date_deadline",), ("date", "datetime"), False),
    FieldSpec("task.description", "project.task", ("description",), ("html",), False, True),
    FieldSpec("task.planned_hours", "project.task", ("allocated_hours", "planned_hours"), ("float",), False),
    FieldSpec("task.status", "project.task", ("state", "kanban_state"), ("selection",), False),
    FieldSpec("timesheet.name", "account.analytic.line", ("name",), ("char",), False, True),
    FieldSpec("timesheet.date", "account.analytic.line", ("date",), ("date",), False, True),
    FieldSpec("timesheet.hours", "account.analytic.line", ("unit_amount",), ("float",), False, True),
    FieldSpec("timesheet.project", "account.analytic.line", ("project_id",), ("many2one",), False, True),
    FieldSpec("timesheet.task", "account.analytic.line", ("task_id",), ("many2one",), False, True),
    FieldSpec("timesheet.employee", "account.analytic.line", ("employee_id",), ("many2one",), False, True),
    FieldSpec("timesheet.account", "account.analytic.line", ("account_id",), ("many2one",), False, True),
    FieldSpec("employee.name", "hr.employee", ("name",), ("char",), False, True),
    FieldSpec("employee.active", "hr.employee", ("active",), ("boolean",), False, True),
    FieldSpec("employee.user", "hr.employee", ("user_id",), ("many2one",), False, True),
)
MODELS = tuple(sorted({spec.model for spec in FIELD_SPECS}))
# Models every supported version has. The others (e.g. project.project.stage before Odoo 16)
# may be absent; their fields then resolve to unavailable.
REQUIRED_MODELS = tuple(sorted({spec.model for spec in FIELD_SPECS if spec.required}))


@dataclass(frozen=True)
class ServerVersion:
    major: int
    edition: str
    label: str

    @property
    def supported(self) -> bool:
        return self.major >= MIN_SUPPORTED_MAJOR

    @classmethod
    def parse(cls, info: dict) -> "ServerVersion":
        parts = info.get("server_version_info") or []
        match = re.search(r"\d+", str(parts[0] if parts else info.get("server_version", "")))
        major = int(match.group()) if match else 0
        edition = "enterprise" if parts and parts[-1] == "e" else "community"
        return cls(major, edition, str(info.get("server_version", "unknown")))

    def missing_for_writes(self) -> list[str]:
        return sorted(spec.logical for spec in FIELD_SPECS if spec.write and not self.has(spec.logical))

    def report(self) -> dict:
        return {"version": self.label, "major": self.major, "edition": self.edition, "supported": self.supported}


@dataclass(frozen=True)
class Resolved:
    name: str
    type: str


class CapabilityMap:
    def __init__(self, resolved: dict[str, Resolved | None]):
        self._resolved = resolved

    @classmethod
    def build(cls, fields_by_model: dict[str, dict]) -> "CapabilityMap":
        resolved: dict[str, Resolved | None] = {}
        for spec in FIELD_SPECS:
            available = fields_by_model.get(spec.model, {})
            resolved[spec.logical] = next(
                (Resolved(name, available[name]["type"]) for name in spec.candidates
                 if name in available and available[name].get("type") in spec.types
                 and (spec.relation is None or available[name].get("relation") == spec.relation)),
                None,
            )
        return cls(resolved)

    def has(self, logical: str) -> bool:
        return self._resolved.get(logical) is not None

    def field(self, logical: str) -> str:
        return self._require(logical).name

    def kind(self, logical: str) -> str:
        return self._require(logical).type

    def missing_required(self) -> list[str]:
        return [spec.logical for spec in FIELD_SPECS if spec.required and not self.has(spec.logical)]

    def missing_for_writes(self) -> list[str]:
        return sorted(spec.logical for spec in FIELD_SPECS if spec.write and not self.has(spec.logical))

    def report(self) -> dict[str, str | None]:
        return {logical: (r.name if r else None) for logical, r in sorted(self._resolved.items())}

    def _require(self, logical: str) -> Resolved:
        resolved = self._resolved.get(logical)
        if resolved is None:
            raise NotAllowedError(f"This Odoo instance has no field for '{logical}'; run agentodoo doctor")
        return resolved
