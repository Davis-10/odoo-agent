"""The host confirmation key (stage 4 S8).

A host such as the gentle-pi extension asks the user itself and, only after a yes,
passes HMAC-SHA256(key, plan_id) to `apply_plan`. The key never leaves this machine
and is never printed. A missing or unreadable key means no host confirmation.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

from agentodoo.errors import ConfigurationError

KEY_FILE = "host-gate.key"


def key_path(state_dir: Path) -> Path:
    return state_dir / KEY_FILE


def init_key(state_dir: Path, rotate: bool = False) -> dict:
    """Create the key (0600, dir 0700). Keeps an existing key unless `rotate`."""
    path = key_path(state_dir)
    try:
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(state_dir, 0o700)
        if not rotate and path.exists():
            return {"key_path": str(path), "status": "exists"}
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(secrets.token_hex(32) + "\n")
        os.chmod(tmp, 0o600)
        if rotate:
            os.replace(tmp, path)
        else:
            os.link(tmp, path)  # fails if another process created the key meanwhile: never overwrite
            os.unlink(tmp)
    except FileExistsError:
        os.unlink(tmp)
        return {"key_path": str(path), "status": "exists"}
    except OSError as exc:
        raise ConfigurationError(f"Cannot write host gate key {path}: {exc}") from exc
    return {"key_path": str(path), "status": "rotated" if rotate else "created"}


def host_confirmation_valid(state_dir: Path, plan_id: str, value: str | None) -> bool:
    """True only for the lowercase hex HMAC-SHA256 of this plan ID under the local key."""
    if not value:
        return False
    try:
        key = bytes.fromhex(key_path(state_dir).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return False
    if not key:
        return False
    expected = hmac.new(key, plan_id.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode("ascii"), value.encode("utf-8"))
