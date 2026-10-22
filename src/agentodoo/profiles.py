"""Named Odoo connection profiles. Profiles never contain secrets."""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

from agentodoo.errors import ConfigurationError

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


@dataclass(frozen=True)
class Profile:
    name: str
    url: str
    database: str
    username: str
    allow_http: bool = False
    closed_stage_ids: tuple[int, ...] = ()  # absent in files saved before closing stages existed
    closed_project_stage_ids: tuple[int, ...] = ()  # absent in files saved before project closing stages
    max_daily_hours: float = 8.0  # absent in files saved before the daily hours limit

    def __post_init__(self):
        for key in ("closed_stage_ids", "closed_project_stage_ids"):
            object.__setattr__(self, key, tuple(sorted({int(i) for i in getattr(self, key)})))

    @classmethod
    def create(cls, name: str, url: str, database: str, username: str, allow_http: bool = False) -> "Profile":
        if not NAME_PATTERN.match(name):
            raise ConfigurationError("Profile name must be 1-32 lowercase letters, digits, '-' or '_'")
        if not database.strip():
            raise ConfigurationError("Database must not be empty")
        if not username.strip():
            raise ConfigurationError("Username must not be empty")
        return cls(name, normalize_url(url, allow_http), database.strip(), username.strip(), allow_http)


def normalize_url(url: str, allow_http: bool) -> str:
    parts = urlsplit(url.strip())
    allowed = ("https", "http") if allow_http else ("https",)
    if parts.scheme not in allowed:
        raise ConfigurationError("URL must use https:// (use --allow-http only for a local instance)")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ConfigurationError("URL must not contain credentials, a query or a fragment")
    if not parts.hostname:
        raise ConfigurationError("URL must include a host")
    return f"{parts.scheme}://{parts.netloc}{parts.path}".rstrip("/")


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "agentodoo" / "profiles.json"


class ProfileStore:
    def __init__(self, path: Path):
        self.path = path

    def list(self) -> list[Profile]:
        return sorted(self._load().values(), key=lambda profile: profile.name)

    def get(self, name: str) -> Profile:
        profiles = self._load()
        if name not in profiles:
            raise ConfigurationError(f"Unknown profile '{name}'")
        return profiles[name]

    def resolve(self, name: str | None) -> Profile:
        """Return the named profile, or the only profile when no name is given."""
        if name is not None:
            return self.get(name)
        profiles = self.list()
        if len(profiles) == 1:
            return profiles[0]
        if not profiles:
            raise ConfigurationError("No profiles configured. Run: agentodoo profile add")
        raise ConfigurationError("Several profiles configured; choose one with --profile")

    def add(self, profile: Profile) -> None:
        profiles = self._load()
        if profile.name in profiles:
            raise ConfigurationError(f"Profile '{profile.name}' already exists")
        profiles[profile.name] = profile
        self._save(profiles)

    def remove(self, name: str) -> None:
        profiles = self._load()
        if name not in profiles:
            raise ConfigurationError(f"Unknown profile '{name}'")
        del profiles[name]
        self._save(profiles)

    def set_closed_stages(self, name: str, stage_ids: list[int]) -> Profile:
        """Replace the profile's closing stage list. Stored locally only."""
        return self._update(name, closed_stage_ids=tuple(stage_ids))

    def set_closed_project_stages(self, name: str, stage_ids: list[int]) -> Profile:
        """Replace the profile's closing project stage list. Stored locally only."""
        return self._update(name, closed_project_stage_ids=tuple(stage_ids))

    def set_max_daily_hours(self, name: str, hours: float) -> Profile:
        """Replace the profile's daily hours limit. Stored locally only."""
        if not (math.isfinite(hours) and 0 < hours <= 24):
            raise ConfigurationError("Max daily hours must be a finite number greater than 0 and at most 24")
        return self._update(name, max_daily_hours=float(hours))

    def _update(self, name: str, **changes) -> Profile:
        profiles = self._load()
        if name not in profiles:
            raise ConfigurationError(f"Unknown profile '{name}'")
        profiles[name] = replace(profiles[name], **changes)
        self._save(profiles)
        return profiles[name]

    def _load(self) -> dict[str, Profile]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return {item["name"]: Profile(**item) for item in data["profiles"]}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ConfigurationError(f"Cannot read profiles file {self.path}: {exc}") from exc

    def _save(self, profiles: dict[str, Profile]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "profiles": [asdict(p) for p in sorted(profiles.values(), key=lambda p: p.name)]}
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
