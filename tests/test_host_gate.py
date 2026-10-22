import hashlib
import hmac
import json
import stat
import unittest

from agentodoo.host_gate import host_confirmation_valid
from tests.support import Workspace


class HostGateInitTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)
        self.key_path = self.ws.dir / "state" / "host-gate.key"

    def init(self, *extra):
        code, out, err = self.ws.run(["host-gate", "init", *extra])
        self.assertEqual((0, ""), (code, err))
        return json.loads(out), out

    # S8: init creates a private 32-byte hex key and never prints it
    def test_init_creates_private_key_without_printing_it(self):
        result, out = self.init()
        self.assertEqual({"key_path": str(self.key_path), "status": "created"}, result)
        key = self.key_path.read_text().strip()
        self.assertRegex(key, r"^[0-9a-f]{64}$")
        self.assertNotIn(key, out)
        self.assertEqual(0o600, stat.S_IMODE(self.key_path.stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE(self.key_path.parent.stat().st_mode))

    # S8: init is idempotent and never overwrites
    def test_init_again_keeps_existing_key(self):
        self.init()
        key = self.key_path.read_text()
        result, out = self.init()
        self.assertEqual({"key_path": str(self.key_path), "status": "exists"}, result)
        self.assertEqual(key, self.key_path.read_text())
        self.assertNotIn(key.strip(), out)

    # S8: --rotate replaces the key, still private and unprinted
    def test_rotate_replaces_key(self):
        self.init()
        old = self.key_path.read_text()
        result, out = self.init("--rotate")
        self.assertEqual({"key_path": str(self.key_path), "status": "rotated"}, result)
        new = self.key_path.read_text().strip()
        self.assertRegex(new, r"^[0-9a-f]{64}$")
        self.assertNotEqual(old.strip(), new)
        self.assertNotIn(new, out)
        self.assertEqual(0o600, stat.S_IMODE(self.key_path.stat().st_mode))

    # S8: rotate without an existing key creates one
    def test_rotate_without_key_creates_it(self):
        result, _ = self.init("--rotate")
        self.assertEqual("rotated", result["status"])
        self.assertTrue(self.key_path.exists())


class HostConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)
        self.state = self.ws.dir / "state"
        self.plan_id = "a" * 32

    def sign(self, plan_id):
        key = bytes.fromhex((self.state / "host-gate.key").read_text().strip())
        return hmac.new(key, plan_id.encode("utf-8"), hashlib.sha256).hexdigest()

    # S8: only the HMAC of this plan ID with the local key is valid; missing key means no confirmation
    def test_validity(self):
        self.assertFalse(host_confirmation_valid(self.state, self.plan_id, "0" * 64))
        self.ws.run(["host-gate", "init"])
        self.assertTrue(host_confirmation_valid(self.state, self.plan_id, self.sign(self.plan_id)))
        self.assertFalse(host_confirmation_valid(self.state, self.plan_id, self.sign("b" * 32)))
        self.assertFalse(host_confirmation_valid(self.state, self.plan_id, self.sign(self.plan_id).upper()))
        self.assertFalse(host_confirmation_valid(self.state, self.plan_id, None))
        self.assertFalse(host_confirmation_valid(self.state, self.plan_id, ""))
        (self.state / "host-gate.key").write_text("not hex\n")
        self.assertFalse(host_confirmation_valid(self.state, self.plan_id, "0" * 64))


if __name__ == "__main__":
    unittest.main()
