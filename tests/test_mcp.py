"""The MCP server, driven in-process through the SDK client with FakeOdoo behind it."""
import hashlib
import hmac
import json
import logging
import shutil
import subprocess
import sys
import unittest
from unittest import mock
from datetime import date
from pathlib import Path

try:
    import anyio
    from mcp import Client
    from mcp.client import advertise
    from mcp_types import ElicitResult
except ImportError:  # the optional `mcp` extra is not installed
    Client = None
else:
    from agentodoo.mcp_server import build_server

from tests.support import Workspace
from tests.test_writes import TASK_ARGV, TIME_ARGV, WriteCase

TODAY = date.today().isoformat()
logging.getLogger("mcp").setLevel(logging.CRITICAL)  # refusals are expected; keep test output readable
READ_ONLY = {"doctor", "projects", "tasks", "stages", "project_stages"}


@unittest.skipIf(Client is None, "the mcp extra is not installed")
class McpCase(WriteCase):
    def server(self, ws):
        return build_server(profiles=ws.profiles, credentials=ws.credentials,
                            transport_factory=ws.transport_factory, state_dir=ws.dir / "state")

    def call(self, ws, tool, arguments, answer=None, extensions=None, mode="auto"):
        """Call one tool; `answer` set means the client supports elicitation and replies with it."""
        asked = []

        async def elicit(context, params):
            asked.append(params)
            return answer

        async def run():
            async with Client(self.server(ws), elicitation_callback=elicit if answer else None,
                              extensions=extensions, mode=mode) as client:
                return await client.call_tool(tool, arguments)

        result = anyio.run(run)
        return result.is_error, result.content[0].text, asked

    def tools(self, ws):
        async def run():
            async with Client(self.server(ws)) as client:
                return (await client.list_tools()).tools
        return {tool.name: tool for tool in anyio.run(run)}

    def sign(self, ws, plan_id):
        key = bytes.fromhex((ws.dir / "state" / "host-gate.key").read_text().strip())
        return hmac.new(key, plan_id.encode("utf-8"), hashlib.sha256).hexdigest()

    def receipts(self, ws):
        return [name for name in self.state_files(ws) if name.startswith("receipts/")]

    def time_plan(self, ws):
        return self.plan(ws, TIME_ARGV)["plan_id"]


class McpReadToolTests(McpCase):
    # S1: every read tool returns exactly the JSON the CLI prints
    def test_read_tools_equal_cli_output(self):
        ws, _ = self.workspace(16)
        cases = [
            ("doctor", {}, ["doctor"]),
            ("projects", {}, ["projects"]),
            ("projects", {"name": "acme", "limit": 1, "profile": "acme"},
             ["projects", "--name", "acme", "--limit", "1", "--profile", "acme"]),
            ("tasks", {"scope": "mine"}, ["tasks", "mine"]),
            ("tasks", {"scope": "unassigned", "project_id": 501, "limit": 5},
             ["tasks", "unassigned", "--project-id", "501", "--limit", "5"]),
            ("stages", {}, ["stages"]),
            ("stages", {"project_id": 501, "name": "do"}, ["stages", "--project-id", "501", "--name", "do"]),
            ("project_stages", {"name": "fin"}, ["project-stages", "--name", "fin"]),
        ]
        for tool, arguments, argv in cases:
            with self.subTest(tool=tool, arguments=arguments):
                code, out, err = ws.run(argv)
                self.assertEqual((0, ""), (code, err))
                self.assertEqual((False, out.rstrip("\n"), []), self.call(ws, tool, arguments))
        self.assertEqual([], self.creates(_))

    # S1: errors come back as tool errors carrying the CLI's message
    def test_errors_are_tool_errors_with_same_message(self):
        ws, _ = self.workspace(16)
        error, text, _ = self.call(ws, "tasks", {"scope": "mine", "profile": "nope"})
        self.assertTrue(error)
        self.assertIn("Unknown profile 'nope'", text)
        error, text, _ = self.call(ws, "tasks", {"scope": "mine", "limit": 0})
        self.assertTrue(error)
        self.assertIn("limit must be between 1 and 1000", text)

    # S1, S2: read tools are read-only; plan tools are not destructive; apply_plan is destructive
    def test_annotations(self):
        ws, _ = self.workspace(16)
        tools = self.tools(ws)
        self.assertEqual(READ_ONLY | {"plan_task", "plan_time", "apply_plan", "confirm_plan", "set_closing_stages"},
                         set(tools))
        for name in READ_ONLY:
            self.assertTrue(tools[name].annotations.read_only_hint, name)
        for name in ("plan_task", "plan_time"):
            self.assertFalse(tools[name].annotations.read_only_hint, name)
            self.assertFalse(tools[name].annotations.destructive_hint, name)
        self.assertFalse(tools["apply_plan"].annotations.read_only_hint)
        self.assertTrue(tools["apply_plan"].annotations.destructive_hint)
        self.assertEqual({"plan_id", "host_confirmation"}, set(tools["apply_plan"].input_schema["properties"]))
        closing = tools["set_closing_stages"]  # stage 6 S6
        self.assertEqual((False, False, None), (closing.annotations.read_only_hint,
                                                closing.annotations.destructive_hint, closing.meta))
        self.assertEqual({"stage_ids", "project_stage_ids", "profile"}, set(closing.input_schema["properties"]))


class McpClosingStagesTests(McpCase):
    """stage 6 S6: set_closing_stages in profile mode updates the profile exactly like the CLI setters."""

    def writes(self, odoo):
        return [(model, method) for model, method in odoo.calls if method in ("create", "write", "unlink")]

    def test_updates_profile_like_cli(self):
        ws, odoo = self.workspace(16)
        cli, _ = self.workspace(16)
        error, text, _ = self.call(ws, "set_closing_stages", {"stage_ids": [2, 1], "project_stage_ids": []})
        self.assertFalse(error, text)
        self.assertEqual({"profile": "acme", "closed_stage_ids": [1, 2], "closed_project_stage_ids": []},
                         json.loads(text))
        cli.run(["profile", "set-closed-stages", "acme", "2", "1"])
        cli.run(["profile", "set-closed-project-stages", "acme", "--none"])
        self.assertEqual(cli.profiles.get("acme"), ws.profiles.get("acme"))
        error, text, _ = self.call(ws, "set_closing_stages", {"project_stage_ids": [31], "profile": "acme"})
        self.assertEqual({"profile": "acme", "closed_stage_ids": [1, 2], "closed_project_stage_ids": [31]},
                         json.loads(text))
        self.assertEqual([], self.writes(odoo))
        self.assertFalse((ws.dir / "state" / "host-settings.json").exists())

    def test_refusals_store_nothing(self):
        ws, odoo = self.workspace(16)
        before = ws.profiles.get("acme")
        for arguments, message in (
                ({"stage_ids": [1, 999]}, "Unknown stage IDs in Odoo: 999"),
                ({"stage_ids": [1], "project_stage_ids": [998]}, "Unknown project stage IDs in Odoo: 998"),
                ({}, "Give stage_ids, project_stage_ids or both"),
                ({"stage_ids": [1], "profile": "nope"}, "Unknown profile 'nope'")):
            with self.subTest(arguments=arguments):
                error, text, _ = self.call(ws, "set_closing_stages", arguments)
                self.assertTrue(error)
                self.assertIn(message, text)
                self.assertEqual(before, ws.profiles.get("acme"))
        self.assertEqual([], self.writes(odoo))


class McpPlanToolTests(McpCase):
    # S1, S3: plan tools save a plan like the CLI and write nothing to Odoo
    def test_plan_tools_save_plans_and_write_nothing(self):
        ws, odoo = self.workspace(16)
        error, text, _ = self.call(ws, "plan_task", {"project_id": 501, "name": "  Stage 2 writes ",
                                                     "description": "Plan & apply <safe>", "planned_hours": 3})
        self.assertFalse(error)
        task = json.loads(text)
        cli = self.plan(ws, TASK_ARGV)
        self.assertEqual({k: v for k, v in cli.items() if k not in ("plan_id", "confirm")},
                         {k: v for k, v in task.items() if k not in ("plan_id", "confirm")})
        self.assertEqual(f"agentodoo apply {task['plan_id']} --confirm {task['plan_id']}", task["confirm"])
        error, text, _ = self.call(ws, "plan_time", {"task_id": 9101, "hours": 1.5, "date": TODAY,
                                                     "description": "Write gate"})
        self.assertFalse(error)
        self.assertEqual("timesheet", json.loads(text)["kind"])
        self.assertEqual([], self.creates(odoo))
        self.assertEqual([], self.receipts(ws))

    # S3: stage 2 refusals apply to plan tools with the same message
    def test_plan_refusal_is_tool_error(self):
        ws, odoo = self.workspace(16)
        error, text, _ = self.call(ws, "plan_task", {"project_id": 502, "name": "X"})
        self.assertTrue(error)
        self.assertIn("Project 502 is flagged closed_stage; choose another project", text)
        error, text, _ = self.call(ws, "plan_time", {"task_id": 9101, "hours": 9, "date": TODAY,
                                                     "description": "Too much"})
        self.assertTrue(error)
        self.assertIn(f"Day {TODAY} would have", text)
        self.assertEqual([], self.state_files(ws))
        self.assertEqual([], self.creates(odoo))


class McpApplyTests(McpCase):
    ACCEPT = ElicitResult(action="accept", content={"confirm": True}) if Client else None

    def assertNothingWritten(self, ws, odoo, lines_before):
        self.assertEqual([], self.creates(odoo))
        self.assertEqual([], self.receipts(ws))
        self.assertEqual(lines_before, len(odoo.records["account.analytic.line"]))

    # S2 (a): accepted elicitation writes through the same apply as the CLI
    def test_elicitation_accept_applies(self):
        ws, odoo = self.workspace(16)
        plan_id = self.time_plan(ws)
        error, text, asked = self.call(ws, "apply_plan", {"plan_id": plan_id}, self.ACCEPT)
        self.assertFalse(error, text)
        receipt = json.loads(text)
        self.assertEqual(("applied", plan_id), (receipt["status"], receipt["plan_id"]))
        self.assertEqual(["account.analytic.line"], self.creates(odoo))
        self.assertEqual([f"receipts/{plan_id}.json"], self.receipts(ws))
        self.assertEqual(1, len(asked))
        message = asked[0].message
        for text in ("Write gate", "Globex | Webshop", "Improvement backlog", TODAY, "1.5",
                     "day total after"):
            self.assertIn(text, message)
        self.assertEqual({"confirm"}, set(asked[0].requested_schema["properties"]))

    # S2 (a): decline, cancel or confirm=false write nothing, even with a valid host confirmation
    def test_elicitation_refusals_write_nothing(self):
        for answer in (ElicitResult(action="decline"), ElicitResult(action="cancel"),
                       ElicitResult(action="accept", content={"confirm": False})):
            with self.subTest(action=answer.action):
                ws, odoo = self.workspace(16)
                ws.run(["host-gate", "init"])
                plan_id = self.time_plan(ws)
                before = len(odoo.records["account.analytic.line"])
                error, text, asked = self.call(
                    ws, "apply_plan", {"plan_id": plan_id, "host_confirmation": self.sign(ws, plan_id)}, answer)
                self.assertTrue(error)
                self.assertIn(f"Plan {plan_id} was not confirmed; nothing was written", text)
                self.assertEqual(1, len(asked))
                self.assertNothingWritten(ws, odoo, before)

    # S2 (b): a valid host confirmation writes when the client has no elicitation
    def test_host_confirmation_applies(self):
        ws, odoo = self.workspace(16)
        ws.run(["host-gate", "init"])
        plan_id = self.time_plan(ws)
        error, text, _ = self.call(ws, "apply_plan", {"plan_id": plan_id,
                                                      "host_confirmation": self.sign(ws, plan_id)})
        self.assertFalse(error, text)
        self.assertEqual("applied", json.loads(text)["status"])
        self.assertEqual(["account.analytic.line"], self.creates(odoo))

    # S2 (c), S8: no, wrong, other-plan or keyless confirmation refuses with the exact CLI command
    def test_without_valid_confirmation_refuses_with_cli_command(self):
        ws, odoo = self.workspace(16)
        plan_id = self.time_plan(ws)
        other = self.time_plan_other(ws)
        before = len(odoo.records["account.analytic.line"])
        command = f"agentodoo apply {plan_id} --confirm {plan_id}"
        attempts = [("missing key", {"host_confirmation": "0" * 64}), ("none", {})]
        for label, extra in attempts:
            with self.subTest(label):
                error, text, _ = self.call(ws, "apply_plan", {"plan_id": plan_id} | extra)
                self.assertTrue(error)
                self.assertIn(command, text)
        ws.run(["host-gate", "init"])
        for label, value in (("wrong", "0" * 64), ("other plan", self.sign(ws, other)),
                             ("plan id", plan_id), ("uppercase", self.sign(ws, plan_id).upper())):
            with self.subTest(label):
                error, text, _ = self.call(ws, "apply_plan", {"plan_id": plan_id, "host_confirmation": value})
                self.assertTrue(error)
                self.assertIn(command, text)
        self.assertNothingWritten(ws, odoo, before)

    def time_plan_other(self, ws):
        return self.plan(ws, ["time-plan", "--task-id", "9103", "--hours", "1", "--date", TODAY,
                              "--description", "Other"])["plan_id"]

    # S3: already-applied and stage 2 re-checks still refuse through MCP, before asking
    def test_stage_2_rules_still_apply(self):
        ws, odoo = self.workspace(16)
        plan_id = self.time_plan(ws)
        self.assertFalse(self.call(ws, "apply_plan", {"plan_id": plan_id}, self.ACCEPT)[0])
        error, text, asked = self.call(ws, "apply_plan", {"plan_id": plan_id}, self.ACCEPT)
        self.assertTrue(error)
        self.assertIn(f"Plan {plan_id} was already applied (record 9001); nothing was written", text)
        self.assertEqual([], asked)
        error, text, asked = self.call(ws, "apply_plan", {"plan_id": "f" * 32}, self.ACCEPT)
        self.assertTrue(error)
        self.assertIn(f"Unknown plan {'f' * 32}", text)
        self.assertEqual([], asked)

        second = self.plan(ws, ["time-plan", "--task-id", "9103", "--hours", "6", "--date", TODAY,
                                "--description", "Big"])["plan_id"]
        odoo.records["account.analytic.line"].append(
            {"id": 9500, "name": "Meanwhile", "date": TODAY, "unit_amount": 1.0, "project_id": 501,
             "task_id": 9101, "employee_id": 70, "account_id": 600, "user_id": 7})
        error, text, _ = self.call(ws, "apply_plan", {"plan_id": second}, self.ACCEPT)
        self.assertTrue(error)
        self.assertIn(f"Day {TODAY} would have 8.5 h", text)
        self.assertEqual(["account.analytic.line"], self.creates(odoo))

    # S3: an Odoo rejection is reported as a tool error with the receipt, like CLI exit 1
    def test_rejected_create_reports_receipt(self):
        ws, odoo = self.workspace(16)
        plan_id = self.time_plan(ws)
        odoo.fail_create = "fault"
        error, text, _ = self.call(ws, "apply_plan", {"plan_id": plan_id}, self.ACCEPT)
        self.assertTrue(error)
        self.assertIn("You cannot log hours in a closed period; nothing was written", text)
        self.assertIn('"status": "rejected"', text)
        self.assertEqual([f"receipts/{plan_id}.json"], self.receipts(ws))


from agentodoo import __version__

# Versioned so Claude Desktop, which may cache UI resources by URI, fetches the new card after an update.
UI_URI = f"ui://agentodoo/plan-{__version__}.html"
UI_MIME = "text/html;profile=mcp-app"


def apps_client(mime_types=(UI_MIME,)):
    """Client extensions of a host that renders MCP Apps (ext-apps spec 2026-01-26)."""
    return [advertise("io.modelcontextprotocol/ui", {"mimeTypes": list(mime_types)})]


class McpAppsTests(McpCase):
    ACCEPT = McpApplyTests.ACCEPT
    assertNothingWritten = McpApplyTests.assertNothingWritten

    def card(self, ws):
        async def run():
            async with Client(self.server(ws)) as client:
                listed = (await client.list_resources()).resources
                read = await client.read_resource(UI_URI)
                return listed, read
        return anyio.run(run)

    # stage 6 S3: plan tools link the card; confirm_plan is app-only and destructive; nothing else has UI
    def test_tools_declare_ui_meta(self):
        ws, _ = self.workspace(16)
        tools = self.tools(ws)
        for name in ("plan_task", "plan_time"):
            self.assertEqual({"ui": {"resourceUri": UI_URI}}, tools[name].meta, name)
        confirm = tools["confirm_plan"]
        self.assertEqual({"ui": {"visibility": ["app"]}}, confirm.meta)
        self.assertTrue(confirm.annotations.destructive_hint)
        self.assertFalse(confirm.annotations.read_only_hint)
        self.assertEqual({"plan_id"}, set(confirm.input_schema["properties"]))
        for name in READ_ONLY | {"apply_plan"}:
            self.assertIsNone(tools[name].meta, name)

    # stage 6 S3: the card is listed and readable as an MCP App, self-contained, with Confirm and Cancel only
    def test_card_resource(self):
        ws, _ = self.workspace(16)
        listed, read = self.card(ws)
        self.assertEqual([(UI_URI, UI_MIME)], [(str(r.uri), r.mime_type) for r in listed])
        self.assertEqual(1, len(read.contents))
        html = read.contents[0]
        self.assertEqual((UI_URI, UI_MIME), (str(html.uri), html.mime_type))
        text = html.text
        self.assertTrue(text.startswith("<!doctype html>"))
        for control in ('<button id="confirm"', ">Confirm</button>", '<button id="cancel"', ">Cancel</button>",
                        '"confirm_plan"', "Nothing was written"):
            self.assertIn(control, text)
        self.assertEqual(2, text.count("<button"))
        for external in ("http:", "https:", "//cdn", " src=", "@import", "url("):
            self.assertNotIn(external, text)

    # stage 6 S3, S4: confirm_plan writes through the same apply and returns the receipt
    def test_confirm_plan_applies(self):
        ws, odoo = self.workspace(16)
        plan_id = self.time_plan(ws)
        error, text, _ = self.call(ws, "confirm_plan", {"plan_id": plan_id}, extensions=apps_client())
        self.assertFalse(error, text)
        receipt = json.loads(text)
        self.assertEqual(("applied", plan_id, 9001), (receipt["status"], receipt["plan_id"], receipt["record_id"]))
        self.assertEqual(["account.analytic.line"], self.creates(odoo))
        self.assertEqual([f"receipts/{plan_id}.json"], self.receipts(ws))

    # stage 6 S3 (L11): on a card client the plan result tells the model the user confirms on the card
    def test_plan_result_tells_model_to_defer_to_card(self):
        ws, _ = self.workspace(16)
        arguments = {"task_id": 9101, "hours": 1.5, "date": TODAY, "description": "Write gate"}
        error, text, _ = self.call(ws, "plan_time", arguments, extensions=apps_client())
        self.assertFalse(error, text)
        plan = json.loads(text)
        self.assertEqual("card", plan["confirm_on"])
        self.assertEqual("The user confirms or cancels this plan on the AgentOdoo plan card shown with this "
                         "result. Do not repeat the plan as a table, do not ask whether to apply it, and do not "
                         "call apply_plan. Tell the user in one line to press Confirm to write it or Cancel to "
                         "discard it.", plan["next_step"])
        error, text, _ = self.call(ws, "plan_time", arguments)
        self.assertFalse(error, text)
        self.assertNotIn("confirm_on", json.loads(text))
        self.assertNotIn("next_step", json.loads(text))

    # stage 6 S4: unknown and already-applied plans are refused, writing nothing more
    def test_confirm_plan_refusals(self):
        ws, odoo = self.workspace(16)
        apps = apps_client()
        error, text, _ = self.call(ws, "confirm_plan", {"plan_id": "f" * 32}, extensions=apps)
        self.assertTrue(error)
        self.assertIn(f"Unknown plan {'f' * 32}", text)
        self.assertEqual([], self.creates(odoo))
        plan_id = self.time_plan(ws)
        self.assertFalse(self.call(ws, "confirm_plan", {"plan_id": plan_id}, extensions=apps)[0])
        error, text, _ = self.call(ws, "confirm_plan", {"plan_id": plan_id}, extensions=apps)
        self.assertTrue(error)
        self.assertIn(f"Plan {plan_id} was already applied (record 9001); nothing was written", text)
        self.assertEqual(["account.analytic.line"], self.creates(odoo))
        self.assertEqual([f"receipts/{plan_id}.json"], self.receipts(ws))

    # stage 6 S4: an Odoo rejection is a tool error carrying the receipt, like apply_plan
    def test_confirm_plan_rejected_reports_receipt(self):
        ws, odoo = self.workspace(16)
        plan_id = self.time_plan(ws)
        odoo.fail_create = "fault"
        error, text, _ = self.call(ws, "confirm_plan", {"plan_id": plan_id}, extensions=apps_client())
        self.assertTrue(error)
        self.assertIn("You cannot log hours in a closed period; nothing was written", text)
        self.assertIn('"status": "rejected"', text)

    # stage 6 S3: clients without MCP Apps may not hide app-only tools, so confirm_plan refuses there
    def test_confirm_plan_refuses_without_apps_client(self):
        ws, odoo = self.workspace(16)
        ws.run(["host-gate", "init"])
        plan_id = self.time_plan(ws)
        before = len(odoo.records["account.analytic.line"])
        for label, answer, extensions in (("plain", None, None), ("elicitation", self.ACCEPT, None),
                                          ("no html apps", None, apps_client(()))):
            with self.subTest(label):
                error, text, asked = self.call(ws, "confirm_plan", {"plan_id": plan_id}, answer,
                                               extensions=extensions)
                self.assertTrue(error)
                self.assertIn(f"Plan {plan_id} can only be confirmed on the plan card, in a client that shows MCP "
                              "Apps; nothing was written. Use apply_plan instead.", text)
                self.assertEqual([], asked)
        self.assertNothingWritten(ws, odoo, before)

    # stage 6 S3: on an MCP Apps client apply_plan refuses, even with elicitation or a valid host confirmation
    def test_apply_plan_refuses_on_apps_client(self):
        ws, odoo = self.workspace(16)
        ws.run(["host-gate", "init"])
        plan_id = self.time_plan(ws)
        before = len(odoo.records["account.analytic.line"])
        for label, answer, extra, mode in (
                ("plain", None, {}, "auto"), ("legacy handshake", None, {}, "legacy"),
                ("elicitation", self.ACCEPT, {}, "auto"),
                ("host confirmation", None, {"host_confirmation": self.sign(ws, plan_id)}, "auto")):
            with self.subTest(label):
                error, text, asked = self.call(ws, "apply_plan", {"plan_id": plan_id} | extra, answer,
                                               extensions=apps_client(), mode=mode)
                self.assertTrue(error)
                self.assertIn(f"Plan {plan_id} must be confirmed by the user with the Confirm button on the plan "
                              "card; nothing was written", text)
                self.assertEqual([], asked)
        self.assertNothingWritten(ws, odoo, before)

    # stage 6 S3: a client without the HTML app MIME type keeps the stage 4 paths
    def test_apply_plan_unchanged_without_html_apps(self):
        ws, odoo = self.workspace(16)
        plan_id = self.time_plan(ws)
        error, text, _ = self.call(ws, "apply_plan", {"plan_id": plan_id}, extensions=apps_client(()))
        self.assertTrue(error)
        self.assertIn(f"agentodoo apply {plan_id} --confirm {plan_id}", text)
        error, text, asked = self.call(ws, "apply_plan", {"plan_id": plan_id}, self.ACCEPT,
                                       extensions=[advertise("com.example/other")])
        self.assertFalse(error, text)
        self.assertEqual(1, len(asked))


class McpCardScriptTests(unittest.TestCase):
    # stage 6 S3: the card's script, driven with a fake host bridge (node --test)
    @unittest.skipIf(shutil.which("node") is None, "node is not installed")
    def test_card_script(self):
        script = Path(__file__).parent / "ui" / "plan_card.test.mjs"
        result = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, timeout=120)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


class McpCliTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)

    # S1: help names the new commands
    def test_help_lists_mcp_and_host_gate(self):
        code, out, _ = self.ws.run(["--help"])
        self.assertEqual(0, code)
        self.assertIn("mcp", out)
        self.assertIn("host-gate", out)

    # Q2: without the mcp extra, `agentodoo mcp` explains how to install it
    def test_mcp_without_extra_explains_install(self):
        modules = {name: None for name in list(sys.modules) if name == "mcp" or name.startswith("mcp.")}
        with mock.patch.dict(sys.modules, modules | {"mcp": None}):
            sys.modules.pop("agentodoo.mcp_server", None)
            result = self.ws.run(["mcp"])
        self.assertEqual((1, "", "error: The MCP server needs the mcp extra: pip install 'agentodoo[mcp]'\n"), result)


if __name__ == "__main__":
    unittest.main()
