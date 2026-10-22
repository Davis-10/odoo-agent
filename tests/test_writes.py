import json
import stat
import unittest
from datetime import date, timedelta

from agentodoo.errors import NotAllowedError
from agentodoo.profiles import Profile
from agentodoo.session import OdooSession
from tests.fixtures import instance
from tests.support import FakeOdoo, Workspace
from tests.test_projects import GLOBEX, ME, seed

TODAY = date.today().isoformat()
ECOMMERCE = {"id": 501, "name": "Globex | Webshop"}
EMPLOYEE = {"id": 70, "name": "Jane Doe", "active": True, "user_id": ME}
OTHER_LINE = {"id": 9000, "name": "Someone else", "date": TODAY, "unit_amount": 1.5, "project_id": 501,
              "task_id": 9101, "employee_id": 99, "account_id": 600, "user_id": 90}


class WriteCase(unittest.TestCase):
    def workspace(self, major: int = 16) -> tuple[Workspace, FakeOdoo]:
        ws = Workspace()
        self.addCleanup(ws.cleanup)
        ws.run(["profile", "add", "acme", "--url", "https://odoo.example.com", "--db", "prod", "--username", "me"])
        ws.credentials.set("acme", "key")
        version, fields = instance(major)
        odoo = FakeOdoo(version, fields, uid=ME)
        seed(odoo, major)
        odoo.records["hr.employee"] = [dict(EMPLOYEE), {"id": 99, "name": "Ana", "active": True, "user_id": 90}]
        odoo.records["account.analytic.line"] = [dict(OTHER_LINE)]
        ws.transport_factory = lambda profile: odoo
        if major >= 16:
            ws.profiles.set_closed_project_stages("acme", [31])
        return ws, odoo

    def state_files(self, ws) -> list[str]:
        root = ws.dir / "state"
        return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()) if root.exists() else []

    def creates(self, odoo) -> list[str]:
        return [model for model, method in odoo.calls if method == "create"]

    def assertRefused(self, ws, odoo, argv, message):
        before = {model: len(rows) for model, rows in odoo.records.items()}
        files = self.state_files(ws)
        self.assertEqual((1, "", f"error: {message}\n"), ws.run(argv))
        self.assertEqual(files, self.state_files(ws))
        self.assertEqual([], self.creates(odoo))
        self.assertEqual(before, {model: len(rows) for model, rows in odoo.records.items()})

    def plan(self, ws, argv) -> dict:
        code, out, err = ws.run(argv)
        self.assertEqual((0, ""), (code, err))
        return json.loads(out)

    def apply(self, ws, plan_id) -> tuple[int, dict, str]:
        code, out, err = ws.run(["apply", plan_id, "--confirm", plan_id])
        receipt = json.loads(out) if out else None
        if receipt and "applied_at" in receipt:
            self.assertRegex(receipt.pop("applied_at"), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        return code, receipt, err


TASK_ARGV = ["task-plan", "--project-id", "501", "--name", "  Stage 2 writes ",
             "--description", "Plan & apply <safe>", "--planned-hours", "3"]
TIME_ARGV = ["time-plan", "--task-id", "9101", "--hours", "1.5", "--date", TODAY, "--description", "Write gate"]


class TaskPlanTests(WriteCase):
    # S2, S4: the plan names exactly what will be created, assigned to me, and is saved privately
    def test_task_plan_on_odoo_16_shows_exact_values_and_saves_plan(self):
        ws, odoo = self.workspace(16)
        plan = self.plan(ws, TASK_ARGV)
        plan_id = plan.pop("plan_id")
        self.assertRegex(plan_id, r"^[0-9a-f]{32}$")
        self.assertEqual({
            "kind": "task", "profile": "acme",
            "values": {"name": "Stage 2 writes", "project_id": 501, "user_ids": [[6, 0, [ME]]],
                       "description": "<p>Plan &amp; apply &lt;safe&gt;</p>", "planned_hours": 3.0},
            "display": {"project": ECOMMERCE, "client": GLOBEX, "name": "Stage 2 writes",
                        "description": "Plan & apply <safe>", "planned_hours": 3.0, "assignee_user_id": ME},
            "confirm": f"agentodoo apply {plan_id} --confirm {plan_id}",
        }, plan)
        self.assertEqual([f"plans/{plan_id}.json"], self.state_files(ws))
        path = ws.dir / "state" / "plans" / f"{plan_id}.json"
        self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE(path.parent.stat().st_mode))
        saved = json.loads(path.read_text())
        self.assertEqual({"plan_id", "kind", "profile", "created_at", "inputs", "values", "display", "fingerprint"},
                         set(saved))
        self.assertEqual({"project_id": 501, "name": "  Stage 2 writes ", "description": "Plan & apply <safe>",
                          "planned_hours": 3.0}, saved["inputs"])
        self.assertEqual([], self.creates(odoo))

    # S2: optional fields are left out; a closed task with the same name is not a duplicate
    def test_minimal_plan_and_closed_namesake_is_not_duplicate(self):
        ws, _ = self.workspace(16)
        plan = self.plan(ws, ["task-plan", "--project-id", "501", "--name", "finished", "--profile", "acme"])
        self.assertEqual({"name": "finished", "project_id": 501, "user_ids": [[6, 0, [ME]]]}, plan["values"])
        self.assertEqual((None, None), (plan["display"]["description"], plan["display"]["planned_hours"]))

    # S2: every refusal writes no plan and creates nothing
    def test_task_plan_refusals(self):
        ws, odoo = self.workspace(16)
        cases = [
            (["--project-id", "999", "--name", "X"], "Project 999 not found"),
            (["--project-id", "502", "--name", "X"], "Project 502 is flagged closed_stage; choose another project"),
            (["--project-id", "10", "--name", "X"],
             "Project 10 is flagged no_analytic_account, no_timesheets; choose another project"),
            (["--project-id", "501", "--name", " checkout "],
             "Project 501 already has an open task named 'Checkout' (task 9103)"),
            (["--project-id", "501", "--name", "   "], "Task name must not be empty"),
            (["--project-id", "501", "--name", "X", "--planned-hours", "-1"],
             "Planned hours must be a finite number of at least 0"),
            (["--project-id", "501", "--name", "X", "--planned-hours", "nan"],
             "Planned hours must be a finite number of at least 0"),
            (["--project-id", "501", "--name", "X", "--planned-hours", "inf"],
             "Planned hours must be a finite number of at least 0"),
        ]
        for argv, message in cases:
            with self.subTest(argv=argv):
                self.assertRefused(ws, odoo, ["task-plan"] + argv, message)

    # S2, S4, S7: plan and apply on Odoo 14 (one assignee) and 16 (many assignees)
    def test_task_plan_and_apply_on_14_and_16(self):
        for major, assignees, read in ((14, {"user_id": ME}, {"user_id": [ME, "Jane Doe"]}),
                                       (16, {"user_ids": [[6, 0, [ME]]]}, {"user_ids": [ME]})):
            with self.subTest(major=major):
                ws, odoo = self.workspace(major)
                plan = self.plan(ws, TASK_ARGV)
                values = {"name": "Stage 2 writes", "project_id": 501, "description":
                          "<p>Plan &amp; apply &lt;safe&gt;</p>", "planned_hours": 3.0} | assignees
                self.assertEqual(values, plan["values"])
                code, receipt, err = self.apply(ws, plan["plan_id"])
                self.assertEqual((0, ""), (code, err))
                self.assertEqual({
                    "plan_id": plan["plan_id"], "kind": "task", "profile": "acme", "status": "applied",
                    "record_id": 9105, "values": values, "mismatches": [], "error": None,
                    "read_back": {"id": 9105, "name": "Stage 2 writes", "project_id": [501, ECOMMERCE["name"]],
                                  "description": "<p>Plan &amp; apply &lt;safe&gt;</p>", "planned_hours": 3.0} | read,
                }, receipt)
                stored = odoo.records["project.task"][-1]
                self.assertEqual((9105, "Stage 2 writes", [ME] if major > 14 else ME),
                                 (stored["id"], stored["name"], stored["user_ids" if major > 14 else "user_id"]))
                self.assertEqual(["project.task"], self.creates(odoo))
                self.assertEqual(sorted([f"plans/{plan['plan_id']}.json", f"receipts/{plan['plan_id']}.json"]),
                                 self.state_files(ws))
                saved = json.loads((ws.dir / "state" / "receipts" / f"{plan['plan_id']}.json").read_text())
                self.assertEqual("applied", saved["status"])

    # S4: apply re-checks; a duplicate created between plan and apply stops the write
    def test_apply_refuses_when_duplicate_appears_after_plan(self):
        ws, odoo = self.workspace(16)
        plan_id = self.plan(ws, TASK_ARGV)["plan_id"]
        odoo.records["project.task"].append({"id": 9110, "name": "stage 2 writes", "project_id": 501,
                                             "stage_id": 1, "user_ids": [90]})
        self.assertRefused(ws, odoo, ["apply", plan_id, "--confirm", plan_id],
                           "Project 501 already has an open task named 'stage 2 writes' (task 9110)")


# Project 501's real stages (stage 2 L10), plus a folded one and one linked only to another project.
WEBSHOP_STAGES = [
    {"id": 42, "name": "POR HACER", "fold": False, "project_ids": [501]},
    {"id": 44, "name": "EN PROCESO", "fold": False, "project_ids": [501]},
    {"id": 47, "name": "EN REVISIÓN", "fold": False, "project_ids": [501]},
    {"id": 48, "name": "FINALIZADO", "fold": False, "project_ids": [501]},
    {"id": 49, "name": "ARCHIVADO", "fold": True, "project_ids": [501]},
    {"id": 50, "name": "SOPORTE", "fold": False, "project_ids": [11]},
]
STAGE_ARGV = ["task-plan", "--project-id", "501", "--name", "Checkout redesign"]


class TaskStageTests(WriteCase):
    # S2.1: --stage-id puts the task in that stage of the project
    def stage_workspace(self) -> tuple[Workspace, FakeOdoo]:
        ws, odoo = self.workspace(16)
        odoo.records["project.task.type"] += [dict(stage) for stage in WEBSHOP_STAGES]
        ws.profiles.set_closed_stages("acme", [48])
        return ws, odoo

    def test_stage_id_plans_and_applies(self):
        ws, odoo = self.stage_workspace()
        plan = self.plan(ws, STAGE_ARGV + ["--stage-id", "44"])
        plan_id = plan.pop("plan_id")
        values = {"name": "Checkout redesign", "project_id": 501, "user_ids": [[6, 0, [ME]]],
                  "stage_id": 44}
        self.assertEqual({
            "kind": "task", "profile": "acme", "values": values,
            "display": {"project": ECOMMERCE, "client": GLOBEX, "name": "Checkout redesign",
                        "description": None, "planned_hours": None, "assignee_user_id": ME,
                        "stage": {"id": 44, "name": "EN PROCESO"}},
            "confirm": f"agentodoo apply {plan_id} --confirm {plan_id}",
        }, plan)
        saved = json.loads((ws.dir / "state" / "plans" / f"{plan_id}.json").read_text())
        self.assertEqual(44, saved["inputs"]["stage_id"])
        code, receipt, err = self.apply(ws, plan_id)
        self.assertEqual((0, ""), (code, err))
        self.assertEqual({
            "plan_id": plan_id, "kind": "task", "profile": "acme", "status": "applied",
            "record_id": 9105, "values": values, "mismatches": [], "error": None,
            "read_back": {"id": 9105, "name": "Checkout redesign",
                          "project_id": [501, ECOMMERCE["name"]], "user_ids": [ME],
                          "stage_id": [44, "EN PROCESO"]},
        }, receipt)
        self.assertEqual(44, odoo.records["project.task"][-1]["stage_id"])

    # S2.1: read-back compares the stage like any other many2one
    def test_stage_read_back_mismatch(self):
        ws, odoo = self.stage_workspace()
        plan_id = self.plan(ws, STAGE_ARGV + ["--stage-id", "44"])["plan_id"]
        odoo.drift = {"stage_id": 42}
        code, receipt, _ = self.apply(ws, plan_id)
        self.assertEqual(4, code)
        self.assertEqual("read_back_mismatch", receipt["status"])
        self.assertEqual([{"field": "stage_id", "planned": 44, "read_back": [42, "POR HACER"]}],
                         receipt["mismatches"])

    # S2.1: missing, unlinked, folded and closing stages are refused with no plan
    def test_stage_refusals(self):
        ws, odoo = self.stage_workspace()
        cases = [
            ("99", "Stage 99 not found (project 501)"),
            ("50", "Stage 50 is not linked to project 501"),
            ("49", "Stage 49 is folded; choose an open stage of project 501"),
            ("48", "Stage 48 is a closing stage of profile 'acme'; choose an open stage of project 501"),
        ]
        for stage_id, message in cases:
            with self.subTest(stage_id=stage_id):
                self.assertRefused(ws, odoo, STAGE_ARGV + ["--stage-id", stage_id], message)

    # S2.1: without a stage-to-project link the stage cannot be checked
    def test_stage_refused_when_instance_has_no_stage_projects(self):
        ws, odoo = self.stage_workspace()
        del odoo.fields["project.task.type"]["project_ids"]
        self.assertRefused(ws, odoo, STAGE_ARGV + ["--stage-id", "44"],
                           "This Odoo instance has no field linking stages to projects, so stage 44 cannot be "
                           "checked against project 501; run agentodoo doctor")

    # S2.1, S4: apply re-checks the stage
    def test_apply_refuses_when_stage_becomes_folded_or_closed(self):
        for change, message in (
                ("fold", "Stage 44 is folded; choose an open stage of project 501"),
                ("close", "Stage 44 is a closing stage of profile 'acme'; choose an open stage of project 501")):
            with self.subTest(change=change):
                ws, odoo = self.stage_workspace()
                plan_id = self.plan(ws, STAGE_ARGV + ["--stage-id", "44"])["plan_id"]
                if change == "fold":
                    next(s for s in odoo.records["project.task.type"] if s["id"] == 44)["fold"] = True
                else:
                    ws.profiles.set_closed_stages("acme", [44, 48])
                self.assertRefused(ws, odoo, ["apply", plan_id, "--confirm", plan_id], message)
                self.assertEqual([f"plans/{plan_id}.json"], self.state_files(ws))


class TimePlanTests(WriteCase):
    # S3, S4: the plan names task, project, employee and analytic account
    def test_time_plan_on_odoo_16_and_apply(self):
        ws, odoo = self.workspace(16)
        plan = self.plan(ws, TIME_ARGV)
        plan_id = plan.pop("plan_id")
        values = {"name": "Write gate", "date": TODAY, "unit_amount": 1.5, "project_id": 501, "task_id": 9101,
                  "employee_id": 70, "account_id": 600}
        self.assertEqual({
            "kind": "timesheet", "profile": "acme", "values": values,
            "display": {"task": {"id": 9101, "name": "Improvement backlog"}, "project": ECOMMERCE,
                        "employee": {"id": 70, "name": "Jane Doe"},
                        "analytic_account": {"id": 600, "name": "Globex webshop"},
                        "date": TODAY, "hours": 1.5, "description": "Write gate",
                        "day_hours_before": 0.0, "day_hours_after": 1.5},
            "confirm": f"agentodoo apply {plan_id} --confirm {plan_id}",
        }, plan)
        code, receipt, err = self.apply(ws, plan_id)
        self.assertEqual((0, ""), (code, err))
        self.assertEqual({
            "plan_id": plan_id, "kind": "timesheet", "profile": "acme", "status": "applied", "record_id": 9001,
            "values": values, "mismatches": [], "error": None,
            "read_back": {"id": 9001, "name": "Write gate", "date": TODAY, "unit_amount": 1.5,
                          "project_id": [501, ECOMMERCE["name"]], "task_id": [9101, "Improvement backlog"],
                          "employee_id": [70, "Jane Doe"], "account_id": [600, "Globex webshop"]},
        }, receipt)
        self.assertEqual(["account.analytic.line"], self.creates(odoo))

    # S3, S7: Odoo 18 reads the analytic account from project.account_id; state closes tasks
    def test_time_plan_on_odoo_18(self):
        ws, odoo = self.workspace(18)
        plan = self.plan(ws, TIME_ARGV)
        self.assertEqual(600, plan["values"]["account_id"])
        code, receipt, err = self.apply(ws, plan["plan_id"])
        self.assertEqual((0, "", "applied"), (code, err, receipt["status"]))
        odoo.records["project.task"][0]["state"] = "1_done"
        odoo.calls.clear()
        self.assertRefused(ws, odoo, TIME_ARGV,
                           "Task 9101 is not open (closed or folded stage, or done); log hours on an open task")

    # S3: Globex (L2): project 502 is in a closing project stage; 501 is accepted; 24 h is the upper bound
    def test_time_plan_refusals(self):
        ws, odoo = self.workspace(16)
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        base = ["time-plan", "--task-id", "9101", "--date", TODAY, "--description", "Work"]
        cases = [
            (["--task-id", "999", "--hours", "1", "--date", TODAY, "--description", "W"], "Task 999 not found"),
            (["--task-id", "3003", "--hours", "1", "--date", TODAY, "--description", "W"],
             "Task 3003 has no project (private task); Odoo needs a project to log hours"),
            (["--task-id", "3000", "--hours", "1", "--date", TODAY, "--description", "W"],
             "Project 502 is flagged closed_stage; choose another project"),
            (["--task-id", "3001", "--hours", "1", "--date", TODAY, "--description", "W"],
             "Project 10 is flagged no_analytic_account, no_timesheets; choose another project"),
            (["--task-id", "9104", "--hours", "1", "--date", TODAY, "--description", "W"],
             "Task 9104 is not open (closed or folded stage, or done); log hours on an open task"),
        ]
        cases += [(base[1:] + ["--hours", h], "Hours must be a finite number greater than 0 and at most 24")
                  for h in ("0", "-1", "24.01", "nan", "inf")]
        cases += [(["--task-id", "9101", "--hours", "1", "--date", d, "--description", "W"], "Date must be YYYY-MM-DD")
                  for d in ("2026-1-05", "20261022", "yesterday")]
        cases += [
            (["--task-id", "9101", "--hours", "1", "--date", tomorrow, "--description", "W"],
             f"Date {tomorrow} is in the future"),
            (["--task-id", "9101", "--hours", "1", "--date", TODAY, "--description", "  "],
             "Description must not be empty"),
        ]
        for argv, message in cases:
            with self.subTest(argv=argv):
                self.assertRefused(ws, odoo, ["time-plan"] + argv, message)
        ws.profiles.set_max_daily_hours("acme", 24)  # S8: the daily limit is checked separately
        self.assertEqual(24.0, self.plan(ws, base + ["--hours", "24"])["values"]["unit_amount"])

    # S3: tasks in the profile's closing stages are not open
    def test_task_in_closing_stage_is_refused(self):
        ws, odoo = self.workspace(16)
        ws.profiles.set_closed_stages("acme", [1])
        self.assertRefused(ws, odoo, TIME_ARGV,
                           "Task 9101 is not open (closed or folded stage, or done); log hours on an open task")

    # S3: exactly one active employee must be linked to me
    def test_employee_must_be_exactly_one(self):
        ws, odoo = self.workspace(16)
        odoo.records["hr.employee"][0]["active"] = False
        self.assertRefused(ws, odoo, TIME_ARGV,
                           f"No active employee is linked to your Odoo user (ID {ME}); Odoo needs one to log hours")
        odoo.records["hr.employee"][0]["active"] = True
        odoo.records["hr.employee"].append({"id": 72, "name": "Jane Doe 2", "active": True, "user_id": ME})
        self.assertRefused(ws, odoo, TIME_ARGV,
                           f"Several active employees are linked to your Odoo user (ID {ME}): 70, 72")

    # S3: an identical line of mine is a duplicate; a different hour count is not
    def test_identical_line_is_refused(self):
        ws, odoo = self.workspace(16)
        odoo.records["account.analytic.line"].append(
            OTHER_LINE | {"id": 9005, "name": "Write gate", "employee_id": 70, "user_id": ME})
        self.assertRefused(ws, odoo, TIME_ARGV, "An identical timesheet line already exists (line 9005)")
        self.assertEqual(2.0, self.plan(ws, TIME_ARGV[:4] + ["2"] + TIME_ARGV[5:])["values"]["unit_amount"])


class ApplyTests(WriteCase):
    def planned(self, major: int = 16, argv=TIME_ARGV):
        ws, odoo = self.workspace(major)
        return ws, odoo, self.plan(ws, argv)["plan_id"]

    # S4: the confirmation must repeat the plan ID
    def test_confirm_mismatch_sends_nothing(self):
        ws, odoo, plan_id = self.planned()
        calls = len(odoo.calls)
        self.assertRefused(ws, odoo, ["apply", plan_id, "--confirm", plan_id[:-1]],
                           "--confirm must repeat the plan ID exactly; nothing was written")
        self.assertEqual(calls, len(odoo.calls))

    def test_unknown_plan(self):
        ws, odoo = self.workspace()
        self.assertRefused(ws, odoo, ["apply", "f" * 32, "--confirm", "f" * 32], f"Unknown plan {'f' * 32}")
        self.assertRefused(ws, odoo, ["apply", "../x", "--confirm", "../x"], "Unknown plan ../x")

    # S4: a modified plan file is refused
    def test_tampered_plan_is_refused(self):
        ws, odoo, plan_id = self.planned()
        path = ws.dir / "state" / "plans" / f"{plan_id}.json"
        saved = json.loads(path.read_text())
        saved["values"]["unit_amount"] = 8.0
        path.write_text(json.dumps(saved))
        self.assertRefused(ws, odoo, ["apply", plan_id, "--confirm", plan_id],
                           f"Plan {plan_id} was modified; create a new plan")

    # S4: apply uses the plan's profile, even when several profiles exist
    def test_apply_uses_plan_profile(self):
        ws, odoo, plan_id = self.planned()
        ws.run(["profile", "add", "other", "--url", "https://odoo.example", "--db", "d", "--username", "u"])
        self.assertEqual((1, "", "error: Several profiles configured; choose one with --profile\n"),
                         ws.run(TIME_ARGV))
        code, receipt, err = self.apply(ws, plan_id)
        self.assertEqual((0, "", "acme", "applied"), (code, err, receipt["profile"], receipt["status"]))

    # S4: a plan is applied once
    def test_double_apply_is_refused(self):
        ws, odoo, plan_id = self.planned()
        self.assertEqual(0, self.apply(ws, plan_id)[0])
        odoo.calls.clear()
        before = len(odoo.records["account.analytic.line"])
        self.assertEqual((1, "", f"error: Plan {plan_id} was already applied (record 9001); nothing was written\n"),
                         ws.run(["apply", plan_id, "--confirm", plan_id]))
        self.assertEqual(([], before), (self.creates(odoo), len(odoo.records["account.analytic.line"])))

    # S4: values re-derived from live Odoo must equal the plan's values
    def test_apply_refuses_when_odoo_changed(self):
        ws, odoo, plan_id = self.planned()
        odoo.records["project.project"][0]["analytic_account_id"] = 602
        self.assertRefused(ws, odoo, ["apply", plan_id, "--confirm", plan_id],
                           "Odoo changed since the plan; create a new plan")

    # S5: Odoo raised and rolled back: receipt says rejected
    def test_rejected_create_writes_receipt(self):
        ws, odoo, plan_id = self.planned()
        odoo.fail_create = "fault"
        code, receipt, err = self.apply(ws, plan_id)
        message = "Odoo rejected account.analytic.line.create: ValidationError: You cannot log hours in a closed period"
        self.assertEqual((1, f"error: {message}; nothing was written\n"), (code, err))
        self.assertEqual(("rejected", None, None, [], message),
                         (receipt["status"], receipt["record_id"], receipt["read_back"], receipt["mismatches"],
                          receipt["error"]))
        self.assertEqual(1, len(odoo.records["account.analytic.line"]))
        self.assertIn(f"receipts/{plan_id}.json", self.state_files(ws))

    # S5: network error after the request: the outcome is unknown and the user must check Odoo
    def test_unreachable_create_writes_unknown_outcome_receipt(self):
        ws, odoo, plan_id = self.planned()
        odoo.fail_create = "network"
        code, receipt, err = self.apply(ws, plan_id)
        message = "Cannot reach https://odoo.example.com: timed out"
        self.assertEqual((4, f"error: {message}; the record may have been created. Check Odoo before retrying\n"),
                         (code, err))
        self.assertEqual(("unknown_outcome", None, message), (receipt["status"], receipt["record_id"], receipt["error"]))
        self.assertEqual(2, len(odoo.records["account.analytic.line"]))
        saved = json.loads((ws.dir / "state" / "receipts" / f"{plan_id}.json").read_text())
        self.assertEqual("unknown_outcome", saved["status"])

    # S4, S5: Odoo stored something else: receipt lists the mismatch and exit 4
    def test_read_back_mismatch(self):
        ws, odoo = self.workspace()
        plan_id = self.plan(ws, TIME_ARGV[:4] + ["1.3"] + TIME_ARGV[5:])["plan_id"]
        odoo.drift = {"unit_amount": 1.25}
        code, receipt, err = self.apply(ws, plan_id)
        self.assertEqual((4, "error: Odoo stored different values than planned for record 9001: unit_amount; "
                             "check it in Odoo\n"), (code, err))
        self.assertEqual(("read_back_mismatch", 9001, [{"field": "unit_amount", "planned": 1.3, "read_back": 1.25}]),
                         (receipt["status"], receipt["record_id"], receipt["mismatches"]))
        self.assertEqual("Odoo stored different values than planned: unit_amount", receipt["error"])

    # S5: once something was or may have been written, the same plan never writes again
    def test_plan_with_uncertain_or_drifted_outcome_cannot_be_reapplied(self):
        for status, setup in (("unknown_outcome", {"fail_create": "network"}), ("read_back_mismatch", {"drift": {"unit_amount": 1.25}})):
            with self.subTest(status=status):
                ws, odoo, plan_id = self.planned()
                for name, value in setup.items():
                    setattr(odoo, name, value)
                self.assertEqual(status, self.apply(ws, plan_id)[1]["status"])
                odoo.fail_create, odoo.drift = None, {}
                odoo.calls.clear()
                before = len(odoo.records["account.analytic.line"])
                self.assertEqual((1, "", f"error: Plan {plan_id} already has a {status} receipt: a record was or may have been "
                                          "created. Check Odoo; nothing was written\n"),
                                 ws.run(["apply", plan_id, "--confirm", plan_id]))
                self.assertEqual(([], before), (self.creates(odoo), len(odoo.records["account.analytic.line"])))

    # S5: even a bug after the create must leave a receipt naming the record
    def test_unexpected_error_after_create_still_writes_receipt(self):
        from unittest.mock import patch
        ws, odoo, plan_id = self.planned()
        with patch("agentodoo.writes.compare", side_effect=KeyError("boom")):
            code, receipt, err = self.apply(ws, plan_id)
        self.assertEqual(4, code)
        self.assertEqual(("read_back_mismatch", 9001, "Unexpected error after creating record 9001: KeyError: 'boom'"),
                         (receipt["status"], receipt["record_id"], receipt["error"]))
        saved = json.loads((ws.dir / "state" / "receipts" / f"{plan_id}.json").read_text())
        self.assertEqual(("read_back_mismatch", 9001), (saved["status"], saved["record_id"]))

    # S5: Odoo rejected the create, so nothing was written and the same plan may be retried
    def test_rejected_plan_can_be_retried(self):
        ws, odoo, plan_id = self.planned()
        odoo.fail_create = "fault"
        self.assertEqual("rejected", self.apply(ws, plan_id)[1]["status"])
        odoo.fail_create = None
        code, receipt, err = self.apply(ws, plan_id)
        self.assertEqual((0, "applied"), (code, receipt["status"]))

    # S5: the receipt cannot be saved: print it in full and exit 4
    def test_receipt_save_failure_prints_receipt(self):
        ws, odoo, plan_id = self.planned()
        (ws.dir / "state" / "receipts").write_text("not a directory")
        code, out, err = ws.run(["apply", plan_id, "--confirm", plan_id])
        self.assertEqual(4, code)
        printed = json.loads(out)
        self.assertEqual({"receipt", "receipt_not_saved"}, set(printed))
        self.assertEqual(("applied", 9001), (printed["receipt"]["status"], printed["receipt"]["record_id"]))
        self.assertTrue(printed["receipt_not_saved"].startswith("Cannot save receipt"), printed["receipt_not_saved"])
        self.assertEqual("error: The receipt could not be saved; it is printed on stdout. Keep it\n", err)
        self.assertEqual(2, len(odoo.records["account.analytic.line"]))


class WriteGateTests(unittest.TestCase):
    # S6: the only writes are task and timesheet creation with known fields
    def setUp(self):
        version, fields = instance(16)
        self.odoo = FakeOdoo(version, fields, uid=ME)
        profile = Profile("acme", "https://odoo.example.com", "prod", "me")
        self.session = OdooSession(profile, self.odoo, "key")

    def test_gate_rejects_other_methods_models_and_fields(self):
        cases = [
            (lambda: self.session.read("project.task", "write", [1], {"name": "x"}),
             "project.task.write is not an allowed read operation"),
            (lambda: self.session.read("account.analytic.line", "unlink", [1]),
             "account.analytic.line.unlink is not an allowed read operation"),
            (lambda: self.session.read("project.task", "create", {"name": "x"}),
             "project.task.create is not an allowed read operation"),
            (lambda: self.session.create("project.project", {"name": "x"}),
             "project.project.create is not an allowed write operation"),
            (lambda: self.session.create("hr.employee", {"name": "x"}),
             "hr.employee.create is not an allowed write operation"),
            (lambda: self.session.create("project.task", {"name": "x", "stage_id": 2, "active": False}),
             "Fields not allowed on project.task.create: active"),
            (lambda: self.session.create("account.analytic.line", {"name": "x", "user_id": 90}),
             "Fields not allowed on account.analytic.line.create: user_id"),
        ]
        for call, message in cases:
            with self.subTest(message=message):
                with self.assertRaises(NotAllowedError) as ctx:
                    call()
                self.assertEqual(message, str(ctx.exception))
        self.assertEqual(set(), {m for _, m in self.odoo.calls} - {"fields_get"})
        for name in ("write", "unlink", "update", "execute", "execute_kw"):
            self.assertFalse(hasattr(self.session, name), name)

    def test_gate_allows_planned_fields(self):
        task_id = self.session.create("project.task", {"name": "x", "project_id": 1, "user_ids": [[6, 0, [ME]]],
                                                       "description": "<p>x</p>", "planned_hours": 1.0,
                                                       "stage_id": 1})
        line_id = self.session.create("account.analytic.line", {
            "name": "x", "date": TODAY, "unit_amount": 1.0, "project_id": 1, "task_id": task_id,
            "employee_id": 70, "account_id": 600})
        self.assertEqual((1, 1), (task_id, line_id))
        self.assertEqual([ME], self.odoo.records["project.task"][0]["user_ids"])


class HelpTests(WriteCase):
    def test_help_documents_write_commands_and_exit_code_4(self):
        ws, _ = self.workspace()
        code, out, _ = ws.run(["--help"])
        self.assertEqual(0, code)
        for text in ("task-plan", "time-plan", "apply", "4 something was or may have been written"):
            self.assertIn(text, out)
        self.assertIn("--confirm", ws.run(["apply", "--help"])[1])
        self.assertIn("--stage-id", ws.run(["task-plan", "--help"])[1])


if __name__ == "__main__":
    unittest.main()
