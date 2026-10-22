"""Local plans and receipts: what will be written, and what happened when it was.

Files live under the state directory, private to the user (dirs 0700, files 0600),
and are replaced atomically so a crash never leaves half a plan or receipt.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agentodoo.errors import ConfigurationError, RefusedError

PLAN_ID = re.compile(r"^[0-9a-f]{32}$")


def default_state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "agentodoo"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fingerprint(kind: str, profile: str, values: dict) -> str:
    canonical = json.dumps({"kind": kind, "profile": profile, "values": values}, sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def new_plan(kind: str, profile: str, inputs: dict, values: dict, display: dict) -> dict:
    values = json.loads(json.dumps(values))  # exactly what a saved plan reads back as
    return {"plan_id": uuid.uuid4().hex, "kind": kind, "profile": profile, "created_at": utc_now(),
            "inputs": inputs, "values": values, "display": display,
            "fingerprint": fingerprint(kind, profile, values)}


class PlanStore:
    def __init__(self, root: Path):
        self.root = root

    def receipt_path(self, plan_id: str) -> Path:
        return self.root / "receipts" / f"{plan_id}.json"

    def save_plan(self, plan: dict) -> None:
        try:
            self._write(self.root / "plans" / f"{plan['plan_id']}.json", plan)
        except OSError as exc:
            raise ConfigurationError(f"Cannot save plan: {exc}") from exc

    def load_plan(self, plan_id: str) -> dict:
        path = self.root / "plans" / f"{plan_id}.json"
        if not PLAN_ID.match(plan_id) or not path.exists():
            raise RefusedError(f"Unknown plan {plan_id}")
        try:
            plan = json.loads(path.read_text(encoding="utf-8"))
            intact = plan["plan_id"] == plan_id and plan["fingerprint"] == fingerprint(
                plan["kind"], plan["profile"], plan["values"])
        except (OSError, ValueError, KeyError, TypeError):
            intact = False
        if not intact:
            raise RefusedError(f"Plan {plan_id} was modified; create a new plan")
        return plan

    def load_receipt(self, plan_id: str) -> dict | None:
        path = self.receipt_path(plan_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigurationError(f"Cannot read receipt {path}: {exc}") from exc

    def save_receipt(self, receipt: dict) -> Path:
        path = self.receipt_path(receipt["plan_id"])
        try:
            self._write(path, receipt)
        except OSError as exc:
            raise ConfigurationError(f"Cannot save receipt {path}: {exc}") from exc
        return path

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        for directory in (path.parent.parent, path.parent):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
