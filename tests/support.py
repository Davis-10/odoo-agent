"""Offline test doubles. Nothing here contacts Odoo or the OS keychain."""
from __future__ import annotations

import io
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from agentodoo.cli import main
from agentodoo.errors import OdooError, OdooRejectedError, OdooUnreachableError
from agentodoo.profiles import ProfileStore


UNREACHABLE = object()
CREATABLE = ("project.task", "account.analytic.line")


class FakeOdoo:
    """An in-memory Odoo that, like the real one, rejects unknown fields.

    Records are plain dicts. Many2one values are stored as ids and returned as
    [id, name] pairs; many2many values are lists of ids.

    `create` works only on the two models AgentOdoo may create. Switches:
    `fail_create = "fault"` rejects it (nothing stored), `fail_create = "network"`
    stores the record and then loses the answer, and `drift` overrides stored values
    (e.g. Odoo rounding hours) so the read-back differs from the plan.
    """

    def __init__(self, version: dict, fields: dict[str, dict], uid: int = 7, secret: str = "key"):
        self.version_info = version
        self.fields = fields
        self.uid = uid
        self.secret = secret
        self.records: dict[str, list[dict]] = {model: [] for model in fields}
        self.calls: list[tuple[str, str]] = []
        self.fail_create: str | None = None
        self.drift: dict = {}

    def version(self) -> dict:
        return self.version_info

    def authenticate(self, database: str, login: str, secret: str) -> int | bool:
        return self.uid if secret == self.secret else False

    def execute_kw(self, database, uid, secret, model, method, args, kwargs):
        self.calls.append((model, method))
        if uid != self.uid or secret != self.secret:
            raise OdooError("Odoo rejected the request: Access Denied")
        if method == "create" and model in CREATABLE:
            return self._create(model, args[0])
        if method in ("create", "write", "unlink"):
            raise AssertionError(f"AgentOdoo must never call {model}.{method}")
        if model not in self.fields:
            raise OdooError(f"Odoo rejected {model}.{method}: KeyError: '{model}'")
        if method == "fields_get":
            return self.fields[model]
        if method == "search_read":
            domain = args[0] if args else kwargs.get("domain", [])
            fields = kwargs.get("fields", [])
            self._check_fields(model, fields)
            rows = [r for r in self.records[model] if self._match(model, r, domain)]
            for name in reversed([part.strip() for part in kwargs.get("order", "id").split(",")]):
                rows.sort(key=lambda r: r.get(name) or "" if name != "id" else r["id"])
            limit = kwargs.get("limit")
            return [self._render(model, r, fields) for r in rows[:limit]]
        raise AssertionError(f"unexpected call {model}.{method}")

    def _create(self, model: str, values: dict) -> int:
        for name in values:
            if name not in self.fields[model]:
                raise OdooRejectedError(f"Odoo rejected {model}.create: ValueError: Invalid field '{name}' on model '{model}'")
        if self.fail_create == "fault":
            raise OdooRejectedError(f"Odoo rejected {model}.create: ValidationError: You cannot log hours in a closed period")
        record = {"id": max((r["id"] for r in self.records[model]), default=0) + 1}
        for name, value in values.items():
            many = self.fields[model][name]["type"] == "many2many"
            record[name] = list(value[0][2]) if many else value  # only [[6, 0, ids]] is supported
        record |= self.drift
        self.records[model].append(record)
        if self.fail_create == "network":
            raise OdooUnreachableError("Cannot reach https://odoo.example.com: timed out")
        return record["id"]

    def _check_fields(self, model: str, fields: list[str]) -> None:
        for name in fields:
            if name != "id" and name not in self.fields[model]:
                raise OdooError(f"Odoo rejected {model}.search_read: Invalid field '{name}' on model '{model}'")

    def _value(self, model: str, record: dict, path: str):
        head, _, rest = path.partition(".")
        if head != "id" and head not in self.fields[model]:
            raise OdooError(f"Odoo rejected {model}.search_read: Invalid field '{head}' on model '{model}'")
        value = record.get(head, False)
        if not rest:
            return value
        relation = self.fields[model][head]["relation"]
        target = next((r for r in self.records.get(relation, []) if r["id"] == value), None)
        return self._value(relation, target, rest) if target else UNREACHABLE

    def _match(self, model: str, record: dict, domain: list) -> bool:
        """Evaluate an Odoo domain: implicit AND, plus prefix '|' and '&'."""
        items = list(domain)

        def term() -> bool:
            item = items.pop(0)
            if item == "|":
                left, right = term(), term()
                return left or right
            if item == "&":
                left, right = term(), term()
                return left and right
            return self._term(model, record, *item)

        results = []
        while items:
            results.append(term())
        return all(results)

    def _term(self, model: str, record: dict, path: str, op: str, expected) -> bool:
        value = self._value(model, record, path)
        if value is UNREACHABLE:
            return False  # like Odoo: a dotted path through an empty many2one never matches
        if op == "ilike":
            return isinstance(value, str) and str(expected).casefold() in value.casefold()
        if isinstance(value, list):
            checks = {"=": lambda: value == [] if expected is False else expected in value,
                      "in": lambda: bool(set(value) & set(expected))}
        else:
            checks = {"=": lambda: value == expected, "!=": lambda: value != expected,
                      "in": lambda: value in expected, "not in": lambda: value not in expected}
        if op not in checks:
            raise AssertionError(f"fake does not support operator {op} on {path}")
        return checks[op]()

    def _render(self, model: str, record: dict, fields: list[str]) -> dict:
        row = {"id": record["id"]}
        for name in fields:
            value = record.get(name, False)
            spec = self.fields[model].get(name, {})
            if spec.get("type") == "many2one" and value:
                target = next((r for r in self.records.get(spec["relation"], []) if r["id"] == value), {})
                value = [value, target.get("name", "")]
            row[name] = value
        return row


class MemoryCredentials:
    def __init__(self, secrets: dict[str, str] | None = None):
        self.secrets = dict(secrets or {})

    def get(self, profile: str) -> str | None:
        return self.secrets.get(profile)

    def set(self, profile: str, secret: str) -> None:
        self.secrets[profile] = secret

    def delete(self, profile: str) -> None:
        self.secrets.pop(profile, None)


class Workspace:
    """A temporary config directory plus in-memory credentials."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.profiles = ProfileStore(self.dir / "profiles.json")
        self.credentials = MemoryCredentials()
        self.transport_factory = None

    def cleanup(self) -> None:
        self._tmp.cleanup()

    def run(self, argv: list[str], stdin: str = "") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = main(
                    argv,
                    profiles=self.profiles,
                    credentials=self.credentials,
                    transport_factory=self.transport_factory,
                    stdin=io.StringIO(stdin),
                    state_dir=self.dir / "state",
                )
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()
