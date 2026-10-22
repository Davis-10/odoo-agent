import json
import os
import stat
import unittest
from unittest.mock import patch

from agentodoo.credentials import require_secret
from agentodoo.errors import CredentialError
from tests.support import MemoryCredentials, Workspace

ACME_PROFILE = ["profile", "add", "acme", "--url", "https://odoo.example.com/",
                "--db", "acme", "--username", "me@example.com"]


class ProfileCommandTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()

    def tearDown(self):
        self.ws.cleanup()

    def test_add_then_list_shows_profile_without_secret(self):
        code, out, err = self.ws.run(ACME_PROFILE)
        self.assertEqual((0, ""), (code, err))
        self.assertEqual({"added": "acme"}, json.loads(out))

        code, out, err = self.ws.run(["profile", "list"])
        self.assertEqual((0, ""), (code, err))
        self.assertEqual([{
            "name": "acme", "url": "https://odoo.example.com",
            "database": "acme", "username": "me@example.com",
            "allow_http": False, "closed_stage_ids": [], "closed_project_stage_ids": [], "max_daily_hours": 8.0,
            "has_key": False,
        }], json.loads(out))

    def test_profiles_file_is_private(self):
        self.ws.run(ACME_PROFILE)
        mode = stat.S_IMODE(os.stat(self.ws.dir / "profiles.json").st_mode)
        self.assertEqual(0o600, mode)

    def test_duplicate_name_is_rejected_and_keeps_original(self):
        self.ws.run(ACME_PROFILE)
        code, out, err = self.ws.run(["profile", "add", "acme", "--url", "https://other.example",
                                      "--db", "x", "--username", "y"])
        self.assertEqual((1, "", "error: Profile 'acme' already exists\n"), (code, out, err))
        self.assertEqual("https://odoo.example.com", self.ws.profiles.get("acme").url)

    def test_http_requires_explicit_opt_in(self):
        args = ["profile", "add", "local", "--url", "http://localhost:8069", "--db", "dev", "--username", "admin"]
        code, out, err = self.ws.run(args)
        self.assertEqual((1, ""), (code, out))
        self.assertEqual("error: URL must use https:// (use --allow-http only for a local instance)\n", err)
        self.assertEqual([], self.ws.profiles.list())
        code, out, err = self.ws.run(args + ["--allow-http"])
        self.assertEqual((0, ""), (code, err))
        self.assertTrue(self.ws.profiles.get("local").allow_http)

    def test_invalid_url_and_name_are_rejected(self):
        cases = [
            (["--url", "https://user:pw@odoo.example", "--db", "d", "--username", "u"], "bad",
             "error: URL must not contain credentials, a query or a fragment\n"),
            (["--url", "ftp://odoo.example", "--db", "d", "--username", "u"], "bad",
             "error: URL must use https:// (use --allow-http only for a local instance)\n"),
            (["--url", "https://odoo.example", "--db", "d", "--username", "u"], "Bad Name",
             "error: Profile name must be 1-32 lowercase letters, digits, '-' or '_'\n"),
        ]
        for extra, name, message in cases:
            with self.subTest(message=message):
                code, out, err = self.ws.run(["profile", "add", name] + extra)
                self.assertEqual((1, "", message), (code, out, err))
        self.assertEqual([], self.ws.profiles.list())

    def test_set_key_from_stdin_stores_secret_and_never_prints_it(self):
        self.ws.run(ACME_PROFILE)
        code, out, err = self.ws.run(["profile", "set-key", "acme", "--stdin"], stdin="s3cret-key\n")
        self.assertEqual((0, ""), (code, err))
        self.assertEqual({"key_stored": "acme"}, json.loads(out))
        self.assertNotIn("s3cret-key", out + err)
        self.assertEqual("s3cret-key", self.ws.credentials.get("acme"))
        self.assertTrue(json.loads(self.ws.run(["profile", "list"])[1])[0]["has_key"])

    def test_set_key_rejects_empty_secret_and_unknown_profile(self):
        self.ws.run(ACME_PROFILE)
        self.assertEqual((1, "", "error: API key must not be empty\n"),
                         self.ws.run(["profile", "set-key", "acme", "--stdin"], stdin="\n"))
        self.assertEqual((1, "", "error: Unknown profile 'nope'\n"),
                         self.ws.run(["profile", "set-key", "nope", "--stdin"], stdin="k\n"))
        self.assertEqual({}, self.ws.credentials.secrets)

    def test_remove_deletes_profile_and_its_key(self):
        self.ws.run(ACME_PROFILE)
        self.ws.credentials.set("acme", "k")
        code, out, err = self.ws.run(["profile", "remove", "acme"])
        self.assertEqual((0, ""), (code, err))
        self.assertEqual({"removed": "acme"}, json.loads(out))
        self.assertEqual([], self.ws.profiles.list())
        self.assertEqual({}, self.ws.credentials.secrets)


class CredentialTests(unittest.TestCase):
    def test_ambient_environment_never_supplies_or_overrides_the_secret(self):
        creds = MemoryCredentials()
        env = {"AGENTODOO_API_KEY": "ambient", "ODOO_PASSWORD": "ambient", "LEGACY_TOOL_PASSWORD": "ambient"}
        with patch.dict(os.environ, env):
            with self.assertRaises(CredentialError) as ctx:
                require_secret(creds, "acme")
            self.assertEqual("No API key stored for profile 'acme'. Run: agentodoo profile set-key acme",
                             str(ctx.exception))
            creds.set("acme", "stored")
            self.assertEqual("stored", require_secret(creds, "acme"))


if __name__ == "__main__":
    unittest.main()
