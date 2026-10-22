import socket
import threading
import unittest
import xmlrpc.client
from xmlrpc.server import SimpleXMLRPCRequestHandler, SimpleXMLRPCServer

from agentodoo.errors import OdooError, OdooRejectedError, OdooUnreachableError
from agentodoo.transport import XmlRpcTransport
from tests.fixtures import ODOO16_VERSION


class OdooPaths(SimpleXMLRPCRequestHandler):
    rpc_paths = ("/xmlrpc/2/common", "/xmlrpc/2/object")


class LocalOdoo:
    """A real XML-RPC server on localhost exposing Odoo's /xmlrpc/2 shape."""

    def __init__(self):
        self.server = SimpleXMLRPCServer(("127.0.0.1", 0), requestHandler=OdooPaths, logRequests=False, allow_none=True)
        self.server.register_function(lambda: ODOO16_VERSION, "version")
        self.server.register_function(lambda db, login, secret, env: 7 if secret == "key" else False, "authenticate")
        self.server.register_function(self.execute_kw, "execute_kw")
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @staticmethod
    def execute_kw(db, uid, secret, model, method, args, kwargs):
        if model == "project.task" and method == "search_read":
            raise xmlrpc.client.Fault(2, "Traceback (most recent call last):\n  ...\nValueError: Invalid field 'state' on model 'project.task'")
        return {"model": model, "method": method, "args": args, "kwargs": kwargs}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class XmlRpcTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.odoo = LocalOdoo()
        cls.transport = XmlRpcTransport(cls.odoo.url, timeout=5)

    @classmethod
    def tearDownClass(cls):
        cls.odoo.close()

    def test_version_needs_no_login(self):
        self.assertEqual(ODOO16_VERSION, self.transport.version())

    def test_authenticate_returns_uid_or_false(self):
        self.assertEqual(7, self.transport.authenticate("db", "me", "key"))
        self.assertFalse(self.transport.authenticate("db", "me", "wrong"))

    def test_execute_kw_passes_arguments_through(self):
        result = self.transport.execute_kw("db", 7, "key", "project.project", "fields_get", [], {"attributes": ["type"]})
        self.assertEqual({"model": "project.project", "method": "fields_get", "args": [], "kwargs": {"attributes": ["type"]}}, result)

    def test_server_fault_becomes_odoo_error_with_last_line_only(self):
        with self.assertRaises(OdooError) as ctx:
            self.transport.execute_kw("db", 7, "key", "project.task", "search_read", [[]], {"fields": ["state"]})
        self.assertEqual("Odoo rejected project.task.search_read: ValueError: Invalid field 'state' on model 'project.task'",
                         str(ctx.exception))

    def test_unreachable_server_becomes_odoo_error(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with self.assertRaises(OdooError) as ctx:
            XmlRpcTransport(f"http://127.0.0.1:{port}", timeout=2).version()
        self.assertTrue(str(ctx.exception).startswith(f"Cannot reach http://127.0.0.1:{port}: "), str(ctx.exception))

    # S5: a fault means Odoo raised and rolled back; a network error leaves the outcome unknown
    def test_fault_is_rejected_and_network_error_is_unreachable(self):
        with self.assertRaises(OdooRejectedError):
            self.transport.execute_kw("db", 7, "key", "project.task", "search_read", [[]], {})
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with self.assertRaises(OdooUnreachableError):
            XmlRpcTransport(f"http://127.0.0.1:{port}", timeout=2).execute_kw("db", 7, "key", "m", "create", [{}], {})


if __name__ == "__main__":
    unittest.main()
