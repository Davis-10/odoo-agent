"""Per-profile API keys in the OS keychain.

Secrets are only ever read from the credential store. Environment variables are
deliberately not consulted, so ambient credentials can never be used by mistake.
"""
from __future__ import annotations

from typing import Protocol

from agentodoo.errors import CredentialError

SERVICE = "agentodoo"


class CredentialStore(Protocol):
    def get(self, profile: str) -> str | None: ...
    def set(self, profile: str, secret: str) -> None: ...
    def delete(self, profile: str) -> None: ...


class KeyringCredentials:
    """Stores one secret per profile name in the system keychain via `keyring`."""

    def _keyring(self):
        try:
            import keyring
            from keyring.errors import KeyringError
        except ImportError as exc:
            raise CredentialError("The 'keyring' package is required: pip install keyring") from exc
        return keyring, KeyringError

    def get(self, profile: str) -> str | None:
        keyring, error = self._keyring()
        try:
            return keyring.get_password(SERVICE, profile)
        except error as exc:
            raise CredentialError(f"Cannot read the system keychain: {exc}") from exc

    def set(self, profile: str, secret: str) -> None:
        keyring, error = self._keyring()
        try:
            keyring.set_password(SERVICE, profile, secret)
        except error as exc:
            raise CredentialError(f"Cannot write to the system keychain: {exc}") from exc

    def delete(self, profile: str) -> None:
        keyring, error = self._keyring()
        try:
            if keyring.get_password(SERVICE, profile) is not None:
                keyring.delete_password(SERVICE, profile)
        except error as exc:
            raise CredentialError(f"Cannot update the system keychain: {exc}") from exc


def require_secret(store: CredentialStore, profile: str) -> str:
    secret = store.get(profile)
    if not secret:
        raise CredentialError(f"No API key stored for profile '{profile}'. Run: agentodoo profile set-key {profile}")
    return secret
