"""The Claude Desktop bundle (stage 6 S1, S2): manifest and launcher agree with bundle mode."""
import json
import runpy
import unittest
from pathlib import Path
from unittest import mock

from agentodoo import host_config

try:
    import anyio
    from mcp import Client
except ImportError:  # the optional `mcp` extra is not installed
    Client = None
else:
    from agentodoo.mcp_server import build_server

MCPB = Path(__file__).resolve().parent.parent / "packaging" / "mcpb"
ENV = {  # variable read by --config-from-host -> user_config key
    "AGENTODOO_HOST_URL": "odoo_url",
    "AGENTODOO_HOST_DATABASE": "database",
    "AGENTODOO_HOST_USERNAME": "username",
    "AGENTODOO_HOST_API_KEY": "api_key",
    "AGENTODOO_HOST_MAX_DAILY_HOURS": "max_daily_hours",
}


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((MCPB / "manifest.json").read_text(encoding="utf-8"))

    # S1: a uv bundle, manifest 0.4, launched in bundle mode with the mcp extra
    def test_required_fields(self):
        m = self.manifest
        self.assertEqual(("0.4", "agentodoo", "AgentOdoo", {"name": "AgentOdoo contributors"}),
                         (m["manifest_version"], m["name"], m["display_name"], m["author"]))
        self.assertTrue(m["description"])
        self.assertNotIn("license", m)
        self.assertEqual("uv", m["server"]["type"])
        self.assertEqual("server/main.py", m["server"]["entry_point"])
        config = m["server"]["mcp_config"]
        self.assertEqual("uv", config["command"])
        self.assertEqual(["run", "--directory", "${__dirname}", "--extra", "mcp", "server/main.py"], config["args"])
        self.assertEqual(["darwin", "win32", "linux"], m["compatibility"]["platforms"])
        self.assertEqual({"python": ">=3.11"}, m["compatibility"]["runtimes"])
        self.assertTrue(m["keywords"])

    # S2: env names match exactly what --config-from-host reads, each from its own user_config key
    def test_env_matches_host_mode(self):
        env = self.manifest["server"]["mcp_config"]["env"]
        self.assertEqual(set(host_config.VARIABLES), set(env))
        self.assertEqual({name: f"${{user_config.{key}}}" for name, key in ENV.items()}, env)
        self.assertEqual(set(ENV.values()), set(self.manifest["user_config"]))

    # S2: the install form: required connection fields, a sensitive key, optional limits
    def test_user_config(self):
        config = self.manifest["user_config"]
        for key, field in config.items():
            with self.subTest(key=key):
                self.assertTrue(field["title"] and field["description"])
        for key in ("odoo_url", "database", "username", "api_key"):
            self.assertEqual(("string", True), (config[key]["type"], config[key]["required"]), key)
        self.assertEqual("Odoo URL", config["odoo_url"]["title"])
        self.assertIn("usually your email", config["username"]["description"])
        self.assertTrue(config["api_key"]["sensitive"])
        self.assertIn("Account Security", config["api_key"]["description"])
        self.assertEqual({k: config["max_daily_hours"].get(k) for k in ("type", "default", "min", "max", "required")},
                         {"type": "number", "default": 8, "min": 0.5, "max": 24, "required": False})
        for key in ("closed_stage_ids", "closed_project_stage_ids"):  # stage 6 S6: the agent asks instead
            self.assertNotIn(key, config)
        self.assertEqual([], [k for k, f in config.items() if f.get("sensitive") and k != "api_key"])

    # S1: the advertised tools are exactly the model-visible ones (app-only confirm_plan is not advertised)
    @unittest.skipIf(Client is None, "the mcp extra is not installed")
    def test_tools_match_visible_server_tools(self):
        async def run():
            async with Client(build_server(profiles=mock.Mock(), credentials=mock.Mock())) as client:
                return (await client.list_tools()).tools
        visible = {t.name for t in anyio.run(run) if "app" != ((t.meta or {}).get("ui") or {}).get("visibility", [None])[0]}
        self.assertEqual(visible, {tool["name"] for tool in self.manifest["tools"]})
        self.assertNotIn("confirm_plan", visible)
        self.assertTrue(all(tool["description"] for tool in self.manifest["tools"]))

    # S1: the launcher runs exactly `agentodoo mcp --config-from-host`
    def test_launcher(self):
        with mock.patch("agentodoo.cli.main", return_value=0) as main:
            with self.assertRaises(SystemExit) as exit_:
                runpy.run_path(str(MCPB / "server" / "main.py"), run_name="__main__")
        main.assert_called_once_with(["mcp", "--config-from-host"])
        self.assertEqual(0, exit_.exception.code)


if __name__ == "__main__":
    unittest.main()
