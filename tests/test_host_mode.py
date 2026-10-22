"""Bundle mode (stage 6 S2, S4): `agentodoo mcp --config-from-host` reads its one profile from host env vars."""
import json
import os
import unittest
from datetime import date
from unittest import mock

try:
    import anyio
    from mcp import Client
    from mcp.client import advertise
    from mcp.server.mcpserver import MCPServer
except ImportError:  # the optional `mcp` extra is not installed
    Client = None

from tests.fixtures import instance
from tests.support import FakeOdoo, Workspace
from tests.test_projects import ME, seed
from tests.test_writes import EMPLOYEE, OTHER_LINE

TODAY = date.today().isoformat()
SECRET = "s3cr3t-api-key-value"
HOST = {
    "AGENTODOO_HOST_URL": "https://odoo.example.com",
    "AGENTODOO_HOST_DATABASE": "prod",
    "AGENTODOO_HOST_USERNAME": "me@example.com",
    "AGENTODOO_HOST_API_KEY": SECRET,
}
SETTINGS = "Open Claude Desktop → Settings → Extensions → AgentOdoo and fill in the settings."


@unittest.skipIf(Client is None, "the mcp extra is not installed")
class HostModeTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)
        version, fields = instance(16)
        self.odoo = FakeOdoo(version, fields, uid=ME, secret=SECRET)
        seed(self.odoo, 16)
        self.odoo.records["hr.employee"] = [dict(EMPLOYEE)]
        self.odoo.records["account.analytic.line"] = [dict(OTHER_LINE)]
        self.connected = []

        def factory(profile):
            self.connected.append(profile)
            return self.odoo
        self.ws.transport_factory = factory

    def server(self, env, argv=("mcp", "--config-from-host")):
        """Run the CLI like the bundle does and return the server it would serve over stdio."""
        clean = {k: v for k, v in os.environ.items() if not k.startswith("AGENTODOO_HOST_")}
        with mock.patch.dict(os.environ, clean | env, clear=True), \
                mock.patch.object(MCPServer, "run", autospec=True) as run:
            code, out, err = self.ws.run(list(argv))
        self.assertEqual((0, "", ""), (code, out, err))
        return run.call_args.args[0]

    def call(self, server, tool, arguments, extensions=None):
        async def run():
            async with Client(server, extensions=extensions) as client:
                return await client.call_tool(tool, arguments)
        result = anyio.run(run)
        return result.is_error, result.content[0].text

    def assertNoSecretAnywhere(self, *texts):
        for text in texts:
            self.assertNotIn(SECRET, text)
        self.assertFalse((self.ws.dir / "profiles.json").exists())
        self.assertEqual({}, self.ws.credentials.secrets)

    def creates(self):
        return [model for model, method in self.odoo.calls if method == "create"]

    # S2: configured host mode serves the in-memory profile `default`; nothing is saved
    def test_configured_lists_tasks_with_default_profile(self):
        server = self.server(HOST)
        for arguments in ({"scope": "mine"}, {"scope": "mine", "profile": "default"}):
            with self.subTest(arguments=arguments):
                error, text = self.call(server, "tasks", arguments)
                self.assertFalse(error, text)
                listing = json.loads(text)
                self.assertEqual("default", listing["profile"])
                self.assertEqual([3000, 3001, 3003, 9101, 9103], [t["id"] for t in listing["tasks"]])
        profile = self.connected[0]
        self.assertEqual(("default", "https://odoo.example.com", "prod", "me@example.com", 8.0),
                         (profile.name, profile.url, profile.database, profile.username, profile.max_daily_hours))
        self.assertNoSecretAnywhere(text)

    # S2: each missing required setting is named; the server still starts; the key is never echoed
    def test_missing_required_setting_is_named(self):
        labels = {"AGENTODOO_HOST_URL": "Odoo URL", "AGENTODOO_HOST_DATABASE": "database",
                  "AGENTODOO_HOST_USERNAME": "username", "AGENTODOO_HOST_API_KEY": "API key"}
        for name, label in labels.items():
            for value in (None, "  ", "${user_config.x}"):
                with self.subTest(name=name, value=value):
                    env = {k: v for k, v in HOST.items() if k != name}
                    if value is not None:
                        env[name] = value
                    server = self.server(env)
                    for tool, arguments in (("doctor", {}), ("tasks", {"scope": "mine"}),
                                            ("plan_time", {"task_id": 9101, "hours": 1, "date": TODAY,
                                                           "description": "x"})):
                        error, text = self.call(server, tool, arguments)
                        self.assertTrue(error)
                        self.assertIn(f"AgentOdoo is not configured: missing {label}. {SETTINGS}", text)
                        self.assertNoSecretAnywhere(text)
        error, text = self.call(self.server({}), "doctor", {})
        self.assertIn("AgentOdoo is not configured: missing Odoo URL, database, username, API key. "
                      f"{SETTINGS}", text)
        self.assertEqual([], self.connected)

    # S2: the URL follows Profile.create's rules (https only, no credentials in it)
    def test_invalid_url_refused(self):
        for url in ("http://odoo.example.com", f"https://me:{SECRET}@odoo.example.com"):
            with self.subTest(url=url.split(":")[0]):
                error, text = self.call(self.server(HOST | {"AGENTODOO_HOST_URL": url}), "tasks", {"scope": "mine"})
                self.assertTrue(error)
                self.assertIn("AgentOdoo is not configured: invalid Odoo URL", text)
                self.assertIn(SETTINGS, text)
                self.assertNoSecretAnywhere(text)
        self.assertEqual([], self.connected)

    # S2, S6: closing stage IDs are no longer read from the host environment
    def test_closed_stage_env_ignored(self):
        server = self.server(HOST | {"AGENTODOO_HOST_CLOSED_STAGE_IDS": "1,2",
                                     "AGENTODOO_HOST_CLOSED_PROJECT_STAGE_IDS": "x"})
        error, text = self.call(server, "tasks", {"scope": "mine"})
        self.assertEqual((False, [3000, 3001, 3003, 9101, 9103]), (error, [t["id"] for t in json.loads(text)["tasks"]]))
        error, text = self.call(server, "doctor", {})
        self.assertEqual(([], [], []), tuple(json.loads(text)[k] for k in
                                             ("closed_stage_ids", "closed_project_stage_ids", "warnings")))

    def settings_path(self):
        return self.ws.dir / "state" / "host-settings.json"

    def writes(self):
        return [(model, method) for model, method in self.odoo.calls if method in ("create", "write", "unlink")]

    # S6: set_closing_stages stores the IDs in host-settings.json (0600 in a 0700 dir); a new server loads them
    def test_set_closing_stages_persists(self):
        error, text = self.call(self.server(HOST), "set_closing_stages",
                                {"stage_ids": [2, 1], "project_stage_ids": [31]})
        self.assertFalse(error, text)
        self.assertEqual({"profile": "default", "closed_stage_ids": [1, 2], "closed_project_stage_ids": [31]},
                         json.loads(text))
        path = self.settings_path()
        self.assertEqual((0o600, 0o700), (path.stat().st_mode & 0o777, path.parent.stat().st_mode & 0o777))
        stored = path.read_text()
        self.assertIn("https://odoo.example.com", stored)
        for value in (SECRET, "me@example.com"):
            self.assertNotIn(value, stored)
        self.assertEqual([p.name for p in path.parent.iterdir() if p.suffix == ".tmp"], [])
        server = self.server(HOST)
        error, text = self.call(server, "tasks", {"scope": "mine"})
        self.assertEqual([3003], [t["id"] for t in json.loads(text)["tasks"]])
        error, text = self.call(server, "projects", {"name": "legacy"})
        self.assertEqual([(502, ["closed_stage"])], [(p["id"], p["flags"]) for p in json.loads(text)["projects"]])
        # another Odoo instance (other database) does not see them
        error, text = self.call(self.server(HOST | {"AGENTODOO_HOST_DATABASE": "test"}), "tasks", {"scope": "mine"})
        self.assertEqual([3000, 3001, 3003, 9101, 9103], [t["id"] for t in json.loads(text)["tasks"]])
        self.assertEqual([], self.writes())
        self.assertNoSecretAnywhere(text)

    # S6: in the same server the change applies at once; one kind can be changed alone; [] clears it
    def test_set_closing_stages_partial_and_clear(self):
        server = self.server(HOST)
        self.call(server, "set_closing_stages", {"stage_ids": [1, 2], "project_stage_ids": [31]})
        error, text = self.call(server, "tasks", {"scope": "mine"})
        self.assertEqual([3003], [t["id"] for t in json.loads(text)["tasks"]])
        error, text = self.call(server, "set_closing_stages", {"stage_ids": []})
        self.assertEqual({"profile": "default", "closed_stage_ids": [], "closed_project_stage_ids": [31]},
                         json.loads(text))
        error, text = self.call(self.server(HOST), "doctor", {})
        self.assertEqual(([], [31]), (json.loads(text)["closed_stage_ids"], json.loads(text)["closed_project_stage_ids"]))

    # S6: unknown IDs are refused by name and nothing is stored; at least one argument is required
    def test_set_closing_stages_refusals(self):
        server = self.server(HOST)
        self.call(server, "set_closing_stages", {"stage_ids": [1]})
        before = self.settings_path().read_text()
        for arguments, message in (
                ({"stage_ids": [2, 998, 999]}, "Unknown stage IDs in Odoo: 998, 999"),
                ({"stage_ids": [2], "project_stage_ids": [31, 997]}, "Unknown project stage IDs in Odoo: 997"),
                ({}, "Give stage_ids, project_stage_ids or both"),
                ({"stage_ids": [2], "profile": "acme"}, "Unknown profile 'acme'")):
            with self.subTest(arguments=arguments):
                error, text = self.call(server, "set_closing_stages", arguments)
                self.assertTrue(error)
                self.assertIn(message, text)
                self.assertEqual(before, self.settings_path().read_text())
        error, text = self.call(server, "doctor", {})
        self.assertEqual([1], json.loads(text)["closed_stage_ids"])
        self.assertEqual([], self.writes())

    # S6: a corrupt settings file means no closing stages and a doctor warning, never a crash
    def test_corrupt_settings_file(self):
        self.settings_path().parent.mkdir(parents=True)
        for content in ("{not json", '{"version": 1, "instances": {"https://odoo.example.com|prod": 5}}'):
            with self.subTest(content=content):
                self.settings_path().write_text(content)
                server = self.server(HOST)
                error, text = self.call(server, "tasks", {"scope": "mine"})
                self.assertEqual((False, 5), (error, len(json.loads(text)["tasks"])))
                error, text = self.call(server, "doctor", {})
                self.assertFalse(error, text)
                self.assertEqual([f"Cannot read host settings {self.settings_path()}; no closing stages are set. "
                                  "Set them again with set_closing_stages"], json.loads(text)["warnings"])
        error, text = self.call(server, "set_closing_stages", {"stage_ids": [1]})
        self.assertFalse(error, text)
        error, text = self.call(server, "doctor", {})
        self.assertEqual(([1], []), (json.loads(text)["closed_stage_ids"], json.loads(text)["warnings"]))
        # a missing file is the normal first start: no warning
        self.settings_path().unlink()
        error, text = self.call(self.server(HOST), "doctor", {})
        self.assertEqual([], json.loads(text)["warnings"])

    # S2, S4: the daily hours limit is applied to plan_time; invalid values are refused
    def test_max_daily_hours(self):
        server = self.server(HOST | {"AGENTODOO_HOST_MAX_DAILY_HOURS": "2"})
        error, text = self.call(server, "plan_time", {"task_id": 9101, "hours": 2.5, "date": TODAY,
                                                      "description": "Too much"})
        self.assertTrue(error)
        self.assertIn(f"Day {TODAY} would have 2.5 h", text)
        self.assertIn("the limit for profile 'default' is 2 h", text)
        for value in ("0", "25", "abc", "nan"):
            with self.subTest(value=value):
                error, text = self.call(self.server(HOST | {"AGENTODOO_HOST_MAX_DAILY_HOURS": value}),
                                        "doctor", {})
                self.assertTrue(error)
                self.assertIn("AgentOdoo is not configured: invalid daily hours limit (a number greater than 0 "
                              f"and at most 24). {SETTINGS}", text)
        error, text = self.call(self.server(HOST | {"AGENTODOO_HOST_MAX_DAILY_HOURS": ""}), "doctor", {})
        self.assertEqual(8.0, json.loads(text)["max_daily_hours"])

    # S2: only the profile `default` exists in host mode
    def test_other_profile_refused(self):
        error, text = self.call(self.server(HOST), "tasks", {"scope": "mine", "profile": "acme"})
        self.assertTrue(error)
        self.assertIn("Unknown profile 'acme'. In Claude Desktop the only profile is 'default'", text)
        self.assertEqual([], self.connected)

    # S4: plan then confirm on the card writes through the same service, with a receipt in the state dir
    def test_plan_and_confirm(self):
        server = self.server(HOST)
        error, text = self.call(server, "plan_time", {"task_id": 9101, "hours": 1.5, "date": TODAY,
                                                      "description": "Write gate"})
        self.assertFalse(error, text)
        plan = json.loads(text)
        self.assertEqual("default", plan["profile"])
        apps = [advertise("io.modelcontextprotocol/ui", {"mimeTypes": ["text/html;profile=mcp-app"]})]
        error, text = self.call(server, "confirm_plan", {"plan_id": plan["plan_id"]}, extensions=apps)
        self.assertFalse(error, text)
        self.assertEqual("applied", json.loads(text)["status"])
        self.assertEqual(["account.analytic.line"], self.creates())
        self.assertTrue((self.ws.dir / "state" / "receipts" / f"{plan['plan_id']}.json").exists())
        self.assertNoSecretAnywhere(text)

    # stage 1 S2: without the flag the host variables are ignored entirely
    def test_env_ignored_without_flag(self):
        self.ws.run(["profile", "add", "acme", "--url", "https://acme.example.com", "--db", "acme",
                     "--username", "me"])
        self.ws.credentials.set("acme", SECRET)
        server = self.server(HOST | {"AGENTODOO_HOST_MAX_DAILY_HOURS": "2"}, argv=("mcp",))
        error, text = self.call(server, "tasks", {"scope": "mine"})
        self.assertFalse(error, text)
        self.assertEqual("acme", self.connected[-1].name)
        self.assertEqual(8.0, self.connected[-1].max_daily_hours)
        error, text = self.call(server, "tasks", {"scope": "mine", "profile": "default"})
        self.assertTrue(error)
        self.assertIn("Unknown profile 'default'", text)
        self.assertNotIn("Claude Desktop", text)
        self.ws.credentials.delete("acme")
        error, text = self.call(server, "doctor", {})
        self.assertIn("No API key stored for profile 'acme'", text)


if __name__ == "__main__":
    unittest.main()
