"""Wire protocols to Odoo. Business code depends on `Transport`, never on XML-RPC."""
from __future__ import annotations

import http.client
import xmlrpc.client
from typing import Any, Protocol

from agentodoo.errors import OdooRejectedError, OdooUnreachableError


class Transport(Protocol):
    def version(self) -> dict: ...
    def authenticate(self, database: str, login: str, secret: str) -> int | bool: ...
    def execute_kw(self, database: str, uid: int, secret: str, model: str, method: str,
                   args: list, kwargs: dict) -> Any: ...


class _TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout: float):
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = self.timeout
        return connection


class _TimeoutSafeTransport(xmlrpc.client.SafeTransport):
    def __init__(self, timeout: float):
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = self.timeout
        return connection


class XmlRpcTransport:
    """Odoo's /xmlrpc/2 endpoints (Odoo 8 to 18; deprecated from Odoo 19)."""

    def __init__(self, url: str, timeout: float = 30):
        self.url = url
        wire = _TimeoutSafeTransport(timeout) if url.startswith("https://") else _TimeoutTransport(timeout)
        self._common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", transport=wire, allow_none=True)
        self._object = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", transport=wire, allow_none=True)

    def version(self) -> dict:
        return self._call("version", lambda: self._common.version())

    def authenticate(self, database: str, login: str, secret: str) -> int | bool:
        return self._call("authenticate", lambda: self._common.authenticate(database, login, secret, {}))

    def execute_kw(self, database, uid, secret, model, method, args, kwargs):
        return self._call(f"{model}.{method}",
                          lambda: self._object.execute_kw(database, uid, secret, model, method, args, kwargs))

    def _call(self, label: str, request):
        try:
            return request()
        except xmlrpc.client.Fault as fault:
            lines = [line for line in str(fault.faultString).strip().splitlines() if line.strip()]
            raise OdooRejectedError(f"Odoo rejected {label}: {lines[-1].strip() if lines else fault.faultCode}") from None
        except xmlrpc.client.ProtocolError as exc:
            raise OdooUnreachableError(f"Odoo answered HTTP {exc.errcode} {exc.errmsg} for {label}") from None
        except (OSError, http.client.HTTPException) as exc:
            raise OdooUnreachableError(f"Cannot reach {self.url}: {exc}") from None
