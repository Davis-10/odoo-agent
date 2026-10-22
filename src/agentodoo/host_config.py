"""Bundle mode (stage 6 S2): one profile from settings the host passes as environment variables.

Only `agentodoo mcp --config-from-host` reads these variables; everywhere else environment
variables are never used (stage 1 S2). The profile `default` and its API key live in memory
only: nothing is written to the profiles file or the keychain. When a setting is missing or
invalid the server still starts and every tool explains what to fill in; the key is never echoed.

Closing stages are not in the install form (stage 6 S6): the agent sets them with `set_closing_stages`
and they are kept in `<state dir>/host-settings.json`, per Odoo instance (URL + database), never with
credentials. A missing file means none; an unreadable one means none plus a `doctor` warning.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from agentodoo.errors import ConfigurationError
from agentodoo.profiles import Profile

PROFILE = "default"
# variable -> label shown to the user; must match packaging/mcpb/manifest.json mcp_config.env
REQUIRED = {
    "AGENTODOO_HOST_URL": "Odoo URL",
    "AGENTODOO_HOST_DATABASE": "database",
    "AGENTODOO_HOST_USERNAME": "username",
    "AGENTODOO_HOST_API_KEY": "API key",
}
MAX_DAILY_HOURS = "AGENTODOO_HOST_MAX_DAILY_HOURS"
OPTIONAL = {
    MAX_DAILY_HOURS: "daily hours limit",
}
SETTINGS_FILE = "host-settings.json"
CLOSING_KEYS = ("closed_stage_ids", "closed_project_stage_ids")
VARIABLES = tuple(REQUIRED) + tuple(OPTIONAL)
SETTINGS_HINT = "Open Claude Desktop → Settings → Extensions → AgentOdoo and fill in the settings."
UNEXPANDED = re.compile(r"^\$\{user_config\.\w+\}$")  # an optional setting the host left empty


class HostSettings:
    """Closing stages per Odoo instance in `<state dir>/host-settings.json` (0600 in a 0700 dir)."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.path = state_dir / SETTINGS_FILE

    @staticmethod
    def instance_key(profile: Profile) -> str:
        return f"{profile.url}|{profile.database}"  # the URL is already normalized by Profile.create

    def load(self) -> dict:
        """All instances' settings; {} when the file is missing. Raises ValueError when it is unreadable."""
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            instances = data["instances"]
            for entry in instances.values():
                for key in CLOSING_KEYS:
                    ids = entry.get(key, [])
                    if not (isinstance(ids, list) and all(type(i) is int and i > 0 for i in ids)):
                        raise ValueError
            return instances
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ValueError(str(exc)) from exc

    def save(self, profile: Profile) -> None:
        try:
            instances = self.load()
        except ValueError:
            instances = {}  # an unreadable file is replaced by the settings known now
        instances[self.instance_key(profile)] = {key: list(getattr(profile, key)) for key in CLOSING_KEYS}
        try:
            self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.state_dir, 0o700)
            tmp = self.path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"version": 1, "instances": instances}, indent=2, sort_keys=True) + "\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError as exc:
            raise ConfigurationError(f"Cannot save host settings {self.path}: {exc}") from exc


class HostProfiles:
    """The ProfileStore interface over the single in-memory profile, or the configuration error.

    Closing stage setters update the in-memory profile and host-settings.json.
    """

    def __init__(self, profile: Profile | None, error: str | None, settings: HostSettings | None = None,
                 warnings: list[str] | None = None):
        self._profile, self._error = profile, error
        self._settings = settings
        self.warnings = list(warnings or [])

    def list(self) -> list[Profile]:
        return [self.get(PROFILE)]

    def get(self, name: str) -> Profile:
        if self._error:
            raise ConfigurationError(self._error)
        if name != PROFILE:
            raise ConfigurationError(f"Unknown profile '{name}'. In Claude Desktop the only profile is '{PROFILE}'")
        return self._profile

    def resolve(self, name: str | None) -> Profile:
        return self.get(PROFILE if name is None else name)

    def set_closed_stages(self, name: str, stage_ids: list[int]) -> Profile:
        return self._update(name, closed_stage_ids=tuple(stage_ids))

    def set_closed_project_stages(self, name: str, stage_ids: list[int]) -> Profile:
        return self._update(name, closed_project_stage_ids=tuple(stage_ids))

    def _update(self, name: str, **changes) -> Profile:
        profile = replace(self.get(name), **changes)
        if self._settings is not None:
            self._settings.save(profile)
        self._profile, self.warnings = profile, []
        return profile


class HostCredentials:
    """The in-memory API key of the profile `default`; never stored anywhere."""

    def __init__(self, secret: str | None):
        self._secret = secret

    def get(self, profile: str) -> str | None:
        return self._secret if profile == PROFILE else None

    def set(self, profile: str, secret: str) -> None:
        raise ConfigurationError("In Claude Desktop the API key is set in the extension settings")

    def delete(self, profile: str) -> None:
        raise ConfigurationError("In Claude Desktop the API key is set in the extension settings")


def _value(environ: Mapping[str, str], name: str) -> str:
    value = (environ.get(name) or "").strip()
    return "" if UNEXPANDED.match(value) else value


def _hours(text: str) -> float:
    hours = float(text) if text else 8.0
    if not (math.isfinite(hours) and 0 < hours <= 24):
        raise ValueError
    return hours


def load(environ: Mapping[str, str], state_dir: Path | None = None) -> tuple[HostProfiles, HostCredentials]:
    values = {name: _value(environ, name) for name in VARIABLES}
    missing = [label for name, label in REQUIRED.items() if not values[name]]
    problems = [f"missing {', '.join(missing)}"] if missing else []
    profile = None
    if not missing:
        try:  # the same validation as `profile add`, without --allow-http
            profile = Profile.create(PROFILE, values["AGENTODOO_HOST_URL"], values["AGENTODOO_HOST_DATABASE"],
                                     values["AGENTODOO_HOST_USERNAME"])
        except ConfigurationError as exc:  # messages name the rule, never the value
            problems.append(f"invalid Odoo URL ({exc})")
    extras = {}
    for name, key, parse, rule in (
            (MAX_DAILY_HOURS, "max_daily_hours", _hours, "a number greater than 0 and at most 24"),):
        try:
            extras[key] = parse(values[name])
        except ValueError:
            problems.append(f"invalid {OPTIONAL[name]} ({rule})")
    if problems:
        return HostProfiles(None, f"AgentOdoo is not configured: {'; '.join(problems)}. {SETTINGS_HINT}"), \
            HostCredentials(None)
    profile = replace(profile, **extras)
    settings, warnings = (HostSettings(state_dir), []) if state_dir is not None else (None, [])
    if settings is not None:
        try:
            stored = settings.load().get(HostSettings.instance_key(profile), {})
            profile = replace(profile, **{key: tuple(stored.get(key, ())) for key in CLOSING_KEYS})
        except ValueError:
            warnings.append(f"Cannot read host settings {settings.path}; no closing stages are set. "
                            "Set them again with set_closing_stages")
    return HostProfiles(profile, None, settings, warnings), \
        HostCredentials(values["AGENTODOO_HOST_API_KEY"])
