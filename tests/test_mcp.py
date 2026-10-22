"""The MCP server, driven in-process through the SDK client with FakeOdoo behind it."""
import hashlib
import hmac
import json
import logging
import sys
import unittest
from unittest import mock
from datetime import date

try:
    import anyio
    from mcp import Client
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

    def call(self, ws, tool, arguments, answer=None):
        """Call one tool; `answer` set means the client supports elicitation and replies with it."""
        asked = []

        async def elicit(context, params):
            asked.append(params)
            return answer

        async def run():
            async with Client(self.server(ws), elicitation_callback=elicit if answer else None) as client:
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
        self.assertEqual(READ_ONLY | {"plan_task", "plan_time", "apply_plan"}, set(tools))
        for name in READ_ONLY:
            self.assertTrue(tools[name].annotations.read_only_hint, name)
        for name in ("plan_task", "plan_time"):
            self.assertFalse(tools[name].annotations.read_only_hint, name)
            self.assertFalse(tools[name].annotations.destructive_hint, name)
        self.assertFalse(tools["apply_plan"].annotations.read_only_hint)
        self.assertTrue(tools["apply_plan"].annotations.destructive_hint)
        self.assertEqual({"plan_id", "host_confirmation"}, set(tools["apply_plan"].input_schema["properties"]))


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
