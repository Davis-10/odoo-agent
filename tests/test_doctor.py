import json
import unittest

from tests.fixtures import ODOO16_VERSION, instance, version
from tests.support import FakeOdoo, Workspace

ACME_PROFILE = ["profile", "add", "acme", "--url", "https://odoo.example.com",
                "--db", "acme", "--username", "me@example.com"]

ODOO16_FIELDS = {
    "employee.active": "active",
    "employee.name": "name",
    "employee.user": "user_id",
    "project.allow_timesheets": "allow_timesheets",
    "project.analytic": "analytic_account_id",
    "project.manager": "user_id",
    "project.name": "name",
    "project.partner": "partner_id",
    "project.stage": "stage_id",
    "project_stage.folded": "fold",
    "project_stage.name": "name",
    "stage.folded": "fold",
    "stage.name": "name",
    "stage.projects": "project_ids",
    "task.assignees": "user_ids",
    "task.deadline": "date_deadline",
    "task.description": "description",
    "task.name": "name",
    "task.planned_hours": "planned_hours",
    "task.project": "project_id",
    "task.stage": "stage_id",
    "task.status": "kanban_state",
    "timesheet.account": "account_id",
    "timesheet.date": "date",
    "timesheet.employee": "employee_id",
    "timesheet.hours": "unit_amount",
    "timesheet.name": "name",
    "timesheet.project": "project_id",
    "timesheet.task": "task_id",
}


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.ws.run(ACME_PROFILE)
        self.ws.credentials.set("acme", "key")
        self.odoo = FakeOdoo(ODOO16_VERSION, instance(16)[1])
        self.ws.transport_factory = lambda profile: self.odoo

    def tearDown(self):
        self.ws.cleanup()

    def test_doctor_reports_version_user_and_field_map(self):
        code, out, err = self.ws.run(["doctor"])
        self.assertEqual((0, ""), (code, err))
        self.assertEqual({
            "status": "ok",
            "profile": "acme",
            "url": "https://odoo.example.com",
            "database": "acme",
            "server": {"version": "16.0-20240101", "major": 16, "edition": "community", "supported": True},
            "user_id": 7,
            "fields": ODOO16_FIELDS,
            "missing_required": [],
            "missing_for_writes": [],
            "closed_stage_ids": [],
            "closed_project_stage_ids": [],
            "max_daily_hours": 8.0,
            "warnings": [],
        }, json.loads(out))
        self.assertNotIn("key", [v for v in json.loads(out).values() if isinstance(v, str)])
        self.assertEqual({"fields_get"}, {method for _, method in self.odoo.calls})

    def test_missing_required_field_exits_3(self):
        del self.odoo.fields["project.task"]["user_ids"]
        code, out, err = self.ws.run(["doctor", "--profile", "acme"])
        report = json.loads(out)
        self.assertEqual((3, ""), (code, err))
        self.assertEqual(("incomplete", ["task.assignees"]), (report["status"], report["missing_required"]))
        self.assertIsNone(report["fields"]["task.assignees"])

    # S7: write fields are reported but never change status or exit code
    def test_missing_write_fields_are_reported_without_changing_status(self):
        del self.odoo.fields["account.analytic.line"]["unit_amount"]
        del self.odoo.fields["hr.employee"]
        code, out, err = self.ws.run(["doctor"])
        report = json.loads(out)
        self.assertEqual((0, "", "ok", []), (code, err, report["status"], report["missing_required"]))
        self.assertEqual(["employee.active", "employee.name", "employee.user", "timesheet.hours"],
                         report["missing_for_writes"])
        self.assertIsNone(report["fields"]["timesheet.hours"])
        del self.odoo.fields["project.task"]["user_ids"]
        code, out, err = self.ws.run(["doctor"])
        self.assertEqual((3, "incomplete", ["task.assignees"]),
                         (code, json.loads(out)["status"], json.loads(out)["missing_required"]))
        self.assertEqual(4, len(json.loads(out)["missing_for_writes"]))

    # S7: Odoo 14-15 have no project.project.stage model; doctor still answers
    def test_odoo_14_without_project_stage_model_is_ok(self):
        self.odoo = FakeOdoo(version(14), instance(14)[1])
        code, out, err = self.ws.run(["doctor"])
        report = json.loads(out)
        self.assertEqual((0, "", "ok", []), (code, err, report["status"], report["missing_for_writes"]))
        self.assertEqual((None, None, None, "analytic_account_id"),
                         (report["fields"]["project.stage"], report["fields"]["project_stage.name"],
                          report["fields"]["project_stage.folded"], report["fields"]["project.analytic"]))

    def test_unsupported_version_exits_3(self):
        self.odoo.version_info = version(13)
        code, out, err = self.ws.run(["doctor"])
        self.assertEqual((3, ""), (code, err))
        self.assertEqual("unsupported_version", json.loads(out)["status"])

    def test_wrong_key_fails_without_printing_it(self):
        self.ws.credentials.set("acme", "wrong-secret")
        code, out, err = self.ws.run(["doctor"])
        self.assertEqual((1, "", "error: Authentication failed for profile 'acme' (check username and API key)\n"),
                         (code, out, err))

    def test_missing_key_explains_how_to_store_it(self):
        self.ws.credentials.delete("acme")
        code, out, err = self.ws.run(["doctor"])
        self.assertEqual((1, "", "error: No API key stored for profile 'acme'. Run: agentodoo profile set-key acme\n"),
                         (code, out, err))
        self.assertEqual([], self.odoo.calls)

    def test_profile_is_required_when_several_exist(self):
        self.ws.run(["profile", "add", "other", "--url", "https://odoo.example", "--db", "d", "--username", "u"])
        self.assertEqual((1, "", "error: Several profiles configured; choose one with --profile\n"), self.ws.run(["doctor"]))
        self.assertEqual(0, self.ws.run(["doctor", "--profile", "acme"])[0])


if __name__ == "__main__":
    unittest.main()
