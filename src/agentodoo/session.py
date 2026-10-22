"""An authenticated view of one Odoo instance: allowlisted reads plus the only two allowed creates."""
from __future__ import annotations

from typing import Any

from agentodoo.capabilities import MODELS, REQUIRED_MODELS, CapabilityMap, ServerVersion
from agentodoo.errors import CredentialError, NotAllowedError, OdooError
from agentodoo.profiles import Profile
from agentodoo.transport import Transport

READ_METHODS = frozenset({"search_read", "fields_get"})
READ_MODELS = frozenset(MODELS)
# The write gate: the only Odoo writes are these creates, limited to these logical fields.
# There is deliberately no update, write, unlink or generic execute.
CREATE_FIELDS = {
    "project.task": ("task.name", "task.project", "task.assignees", "task.description", "task.planned_hours",
                     "task.stage"),
    "account.analytic.line": ("timesheet.name", "timesheet.date", "timesheet.hours", "timesheet.project",
                              "timesheet.task", "timesheet.employee", "timesheet.account"),
}


class OdooSession:
    def __init__(self, profile: Profile, transport: Transport, secret: str):
        self.profile = profile
        self._transport = transport
        self._secret = secret
        self._uid: int | None = None
        self._version: ServerVersion | None = None
        self._capabilities: CapabilityMap | None = None

    @property
    def version(self) -> ServerVersion:
        if self._version is None:
            self._version = ServerVersion.parse(self._transport.version())
        return self._version

    @property
    def uid(self) -> int:
        if self._uid is None:
            uid = self._transport.authenticate(self.profile.database, self.profile.username, self._secret)
            if not uid:
                raise CredentialError(
                    f"Authentication failed for profile '{self.profile.name}' (check username and API key)")
            self._uid = int(uid)
        return self._uid

    @property
    def capabilities(self) -> CapabilityMap:
        if self._capabilities is None:
            fields = {model: self._fields_get(model) for model in REQUIRED_MODELS}
            for model in MODELS:
                if model not in fields:
                    try:
                        fields[model] = self._fields_get(model)
                    except OdooError:
                        fields[model] = {}  # model not installed on this instance: its fields are unavailable
            self._capabilities = CapabilityMap.build(fields)
        return self._capabilities

    def _fields_get(self, model: str) -> dict:
        return self.read(model, "fields_get", attributes=["type", "string", "relation"])

    def read(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        if model not in READ_MODELS or method not in READ_METHODS:
            raise NotAllowedError(f"{model}.{method} is not an allowed read operation")
        return self._transport.execute_kw(self.profile.database, self.uid, self._secret,
                                          model, method, list(args), kwargs)

    def create(self, model: str, values: dict) -> int:
        """Create one record through the write gate. Anything outside it is refused before sending."""
        if model not in CREATE_FIELDS:
            raise NotAllowedError(f"{model}.create is not an allowed write operation")
        caps = self.capabilities
        allowed = {caps.field(logical) for logical in CREATE_FIELDS[model] if caps.has(logical)}
        extra = sorted(set(values) - allowed)
        if extra:
            raise NotAllowedError(f"Fields not allowed on {model}.create: {', '.join(extra)}")
        return self._transport.execute_kw(self.profile.database, self.uid, self._secret,
                                          model, "create", [values], {})
