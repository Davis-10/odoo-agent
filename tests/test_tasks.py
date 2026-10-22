import json
import unittest

from tests.fixtures import instance
from tests.support import FakeOdoo, Workspace

ME, OTHER = 7, 8


def seed(odoo: FakeOdoo, major: int) -> None:
    odoo.records["project.project"] = [{"id": 1, "name": "Acme"}, {"id": 2, "name": "Internal"}]
    odoo.records["project.task.type"] = [{"id": 10, "name": "To do", "fold": False}, {"id": 11, "name": "Done", "fold": True}]
    assign = (lambda uid: {"user_id": uid or False}) if major <= 14 else (lambda uid: {"user_ids": [uid] if uid else []})
    hours = "planned_hours" if major <= 16 else "allocated_hours"
    status = ("kanban_state", "normal") if major <= 16 else ("state", "01_in_progress")
    base = {"stage_id": 10, "date_deadline": False, hours: 0.0, status[0]: status[1]}
    odoo.records["project.task"] = [
        base | {"id": 100, "name": "Mine open", "project_id": 1, "date_deadline": "2026-11-01", hours: 4.0} | assign(ME),
        base | {"id": 101, "name": "Mine folded", "project_id": 1, "stage_id": 11} | assign(ME),
        base | {"id": 102, "name": "Someone else", "project_id": 1} | assign(OTHER),
        base | {"id": 103, "name": "Unassigned Acme", "project_id": 1} | assign(None),
        base | {"id": 104, "name": "Unassigned internal", "project_id": 2} | assign(None),
    ]


class TaskListTests(unittest.TestCase):
    def workspace(self, major: int) -> tuple[Workspace, FakeOdoo]:
        ws = Workspace()
        self.addCleanup(ws.cleanup)
        ws.run(["profile", "add", "work", "--url", "https://odoo.example", "--db", "prod", "--username", "me"])
        ws.credentials.set("work", "key")
        version, fields = instance(major)
        odoo = FakeOdoo(version, fields, uid=ME)
        seed(odoo, major)
        ws.transport_factory = lambda profile: odoo
        return ws, odoo

    def test_mine_on_odoo_16_uses_16_field_names(self):
        ws, odoo = self.workspace(16)
        code, out, err = ws.run(["tasks", "mine"])
        self.assertEqual((0, ""), (code, err))
        self.assertEqual({"profile": "work", "scope": "mine", "tasks": [{
            "id": 100, "name": "Mine open", "project": {"id": 1, "name": "Acme"},
            "stage": {"id": 10, "name": "To do"}, "assignee_ids": [ME],
            "deadline": "2026-11-01", "planned_hours": 4.0, "status": "normal",
        }]}, json.loads(out))
        self.assertNotIn(("project.task", "write"), odoo.calls)

    def test_private_task_without_project_or_stage_is_open(self):
        ws, odoo = self.workspace(16)
        odoo.records["project.task"].append({"id": 105, "name": "Private", "project_id": False, "stage_id": False,
                                             "date_deadline": False, "planned_hours": 0.0,
                                             "kanban_state": "normal", "user_ids": [ME]})
        code, out, err = ws.run(["tasks", "mine"])
        self.assertEqual((0, ""), (code, err))
        tasks = json.loads(out)["tasks"]
        self.assertEqual([100, 105], [t["id"] for t in tasks])
        self.assertEqual({"id": 105, "name": "Private", "project": None, "stage": None, "assignee_ids": [ME],
                          "deadline": None, "planned_hours": 0.0, "status": "normal"}, tasks[1])

    def test_mine_works_on_every_supported_shape(self):
        for major in (14, 16, 17, 18):
            with self.subTest(major=major):
                ws, _ = self.workspace(major)
                code, out, err = ws.run(["tasks", "mine"])
                self.assertEqual((0, ""), (code, err))
                self.assertEqual([100], [t["id"] for t in json.loads(out)["tasks"]])
                self.assertEqual([ME], json.loads(out)["tasks"][0]["assignee_ids"])

    def test_closed_state_is_excluded_on_17_and_later(self):
        ws, odoo = self.workspace(17)
        odoo.records["project.task"][0]["state"] = "1_done"
        code, out, err = ws.run(["tasks", "mine"])
        self.assertEqual((0, "", []), (code, err, json.loads(out)["tasks"]))

    def test_unassigned_with_and_without_project_filter(self):
        ws, _ = self.workspace(16)
        code, out, err = ws.run(["tasks", "unassigned"])
        self.assertEqual((0, ""), (code, err))
        self.assertEqual(([103, 104], "unassigned"), ([t["id"] for t in json.loads(out)["tasks"]], json.loads(out)["scope"]))
        self.assertEqual([], json.loads(out)["tasks"][0]["assignee_ids"])
        code, out, err = ws.run(["tasks", "unassigned", "--project-id", "2", "--limit", "5"])
        self.assertEqual((0, "", [104]), (code, err, [t["id"] for t in json.loads(out)["tasks"]]))

    def test_limit_is_applied_and_validated(self):
        ws, _ = self.workspace(16)
        code, out, err = ws.run(["tasks", "unassigned", "--limit", "1"])
        self.assertEqual((0, [103]), (code, [t["id"] for t in json.loads(out)["tasks"]]))
        code, out, err = ws.run(["tasks", "mine", "--limit", "0"])
        self.assertEqual((2, ""), (code, out))
        self.assertIn("--limit must be between 1 and 1000", err)

    def test_optional_fields_absent_are_null_and_never_requested(self):
        ws, odoo = self.workspace(16)
        del odoo.fields["project.task"]["planned_hours"]
        del odoo.fields["project.task"]["date_deadline"]
        code, out, err = ws.run(["tasks", "mine"])
        task = json.loads(out)["tasks"][0]
        self.assertEqual((0, "", None, None), (code, err, task["planned_hours"], task["deadline"]))

    def test_missing_required_field_points_to_doctor(self):
        ws, odoo = self.workspace(16)
        del odoo.fields["project.task"]["user_ids"]
        self.assertEqual((1, "", "error: This Odoo instance has no field for 'task.assignees'; run agentodoo doctor\n"),
                         ws.run(["tasks", "mine"]))


if __name__ == "__main__":
    unittest.main()
