import json
import unittest

from tests.fixtures import instance
from tests.support import FakeOdoo, Workspace

ME = 7
WRITES = {"create", "write", "unlink"}


class ClosingStageTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)
        self.ws.run(["profile", "add", "acme", "--url", "https://odoo.example.com",
                     "--db", "prod", "--username", "me"])
        self.ws.credentials.set("acme", "key")
        version, fields = instance(16)
        self.odoo = FakeOdoo(version, fields, uid=ME)
        self.odoo.records["project.project"] = [{"id": 1, "name": "Acme"}, {"id": 2, "name": "Internal"}]
        self.odoo.records["project.task.type"] = [
            {"id": 10, "name": "To do", "fold": False, "project_ids": [1, 2]},
            {"id": 11, "name": "FINALIZADO", "fold": False, "project_ids": [1]},
            {"id": 12, "name": "Finalizado", "fold": False, "project_ids": [2]},
            {"id": 13, "name": "Archived", "fold": True, "project_ids": []},
        ]
        base = {"project_id": 1, "date_deadline": False, "planned_hours": 0.0, "kanban_state": "normal"}
        self.odoo.records["project.task"] = [
            base | {"id": 100, "name": "Open", "stage_id": 10, "user_ids": [ME]},
            base | {"id": 101, "name": "Finished", "stage_id": 11, "user_ids": [ME]},
            base | {"id": 102, "name": "Folded", "stage_id": 13, "user_ids": [ME]},
            base | {"id": 103, "name": "Open unassigned", "stage_id": 10, "user_ids": []},
            base | {"id": 104, "name": "Finished unassigned", "project_id": 2, "stage_id": 12, "user_ids": []},
        ]
        self.ws.transport_factory = lambda profile: self.odoo

    def stored(self):
        return [p.closed_stage_ids for p in self.ws.profiles.list()]

    def task_ids(self, out):
        return [t["id"] for t in json.loads(out)["tasks"]]

    def assertNoWrites(self):
        self.assertEqual(set(), {m for _, m in self.odoo.calls} & WRITES)

    # S7.1
    def test_stages_lists_ids_names_folded_closed_and_projects(self):
        self.ws.profiles.set_closed_stages("acme", [11])
        code, out, err = self.ws.run(["stages"])
        self.assertEqual((0, ""), (code, err))
        self.assertEqual({"profile": "acme", "stages": [
            {"id": 10, "name": "To do", "folded": False, "closed": False, "project_ids": [1, 2]},
            {"id": 11, "name": "FINALIZADO", "folded": False, "closed": True, "project_ids": [1]},
            {"id": 12, "name": "Finalizado", "folded": False, "closed": False, "project_ids": [2]},
            {"id": 13, "name": "Archived", "folded": True, "closed": False, "project_ids": []},
        ]}, json.loads(out))
        self.assertNoWrites()

    # S7.1
    def test_stages_name_and_project_filters(self):
        code, out, err = self.ws.run(["stages", "--profile", "acme", "--name", "finaliz"])
        self.assertEqual((0, "", [11, 12]), (code, err, [s["id"] for s in json.loads(out)["stages"]]))
        code, out, err = self.ws.run(["stages", "--project-id", "2", "--name", "FINAL"])
        self.assertEqual((0, "", [12]), (code, err, [s["id"] for s in json.loads(out)["stages"]]))

    # S7.1
    def test_stages_without_project_field_shows_null_projects(self):
        del self.odoo.fields["project.task.type"]["project_ids"]
        code, out, err = self.ws.run(["stages", "--name", "to do"])
        self.assertEqual((0, ""), (code, err))
        self.assertEqual({"profile": "acme", "stages": [
            {"id": 10, "name": "To do", "folded": False, "closed": False, "project_ids": None}]}, json.loads(out))
        self.assertEqual((1, "", "error: This Odoo instance has no field for 'stage.projects'; run agentodoo doctor\n"),
                         self.ws.run(["stages", "--project-id", "1"]))

    # S7.2
    def test_set_closed_stages_replaces_list(self):
        code, out, err = self.ws.run(["profile", "set-closed-stages", "acme", "12", "11", "12"])
        self.assertEqual((0, "", {"profile": "acme", "closed_stage_ids": [11, 12]}), (code, err, json.loads(out)))
        self.assertEqual([(11, 12)], self.stored())
        code, out, err = self.ws.run(["profile", "set-closed-stages", "acme", "10"])
        self.assertEqual((0, "", [(10,)]), (code, err, self.stored()))
        self.assertNoWrites()

    # S7.2
    def test_set_closed_stages_rejects_unknown_ids_and_keeps_list(self):
        self.ws.profiles.set_closed_stages("acme", [11])
        before = self.ws.profiles.list()
        code, out, err = self.ws.run(["profile", "set-closed-stages", "acme", "11", "99", "98"])
        self.assertEqual((1, "", "error: Unknown stage IDs in Odoo: 98, 99\n"), (code, out, err))
        self.assertEqual(before, self.ws.profiles.list())

    # S7.2
    def test_set_closed_stages_none_clears_and_usage_errors(self):
        self.ws.profiles.set_closed_stages("acme", [11])
        code, out, err = self.ws.run(["profile", "set-closed-stages", "acme", "--none"])
        self.assertEqual((0, "", {"profile": "acme", "closed_stage_ids": []}), (code, err, json.loads(out)))
        self.assertEqual([()], self.stored())
        self.ws.profiles.set_closed_stages("acme", [11])
        for argv in (["acme"], ["acme", "11", "--none"]):
            with self.subTest(argv=argv):
                code, out, err = self.ws.run(["profile", "set-closed-stages"] + argv)
                self.assertEqual((2, ""), (code, out))
                self.assertIn("give stage IDs or --none", err)
                self.assertEqual([(11,)], self.stored())
        self.assertEqual((1, "", "error: Unknown profile 'nope'\n"),
                         self.ws.run(["profile", "set-closed-stages", "nope", "11"]))

    # S7.3
    def test_tasks_exclude_closing_stages(self):
        self.ws.run(["profile", "set-closed-stages", "acme", "11", "12"])
        code, out, err = self.ws.run(["tasks", "mine"])
        self.assertEqual((0, "", [100]), (code, err, self.task_ids(out)))
        code, out, err = self.ws.run(["tasks", "unassigned"])
        self.assertEqual((0, "", [103]), (code, err, self.task_ids(out)))
        self.assertNoWrites()

    # S7.3
    def test_profile_saved_before_closing_stages_loads_and_filters_as_before(self):
        legacy = {"version": 1, "profiles": [{"name": "acme", "url": "https://odoo.example.com",
                                               "database": "prod", "username": "me", "allow_http": False}]}
        (self.ws.dir / "profiles.json").write_text(json.dumps(legacy), encoding="utf-8")
        self.assertEqual([()], self.stored())
        code, out, err = self.ws.run(["tasks", "mine"])
        self.assertEqual((0, "", [100, 101]), (code, err, self.task_ids(out)))
        code, out, err = self.ws.run(["tasks", "unassigned"])
        self.assertEqual((0, "", [103, 104]), (code, err, self.task_ids(out)))
        code, out, err = self.ws.run(["profile", "list"])
        self.assertEqual((0, "", []), (code, err, json.loads(out)[0]["closed_stage_ids"]))

    # S7.4
    def test_list_and_doctor_report_closing_stages_and_stale_ids(self):
        self.ws.profiles.set_closed_stages("acme", [11, 99])
        code, out, err = self.ws.run(["profile", "list"])
        self.assertEqual((0, "", [11, 99]), (code, err, json.loads(out)[0]["closed_stage_ids"]))
        code, out, err = self.ws.run(["doctor"])
        report = json.loads(out)
        self.assertEqual((0, "", "ok"), (code, err, report["status"]))
        self.assertEqual(([11, 99], ["closing stage ID 99 no longer exists in Odoo"]),
                         (report["closed_stage_ids"], report["warnings"]))
        del self.odoo.fields["project.task"]["user_ids"]
        code, out, err = self.ws.run(["doctor"])
        self.assertEqual((3, "", ["closing stage ID 99 no longer exists in Odoo"]),
                         (code, err, json.loads(out)["warnings"]))
        self.assertNoWrites()

    # S7.5
    def test_closing_stages_never_write_to_odoo(self):
        for argv in (["stages"], ["profile", "set-closed-stages", "acme", "11"], ["tasks", "mine"],
                     ["tasks", "unassigned"], ["doctor"], ["profile", "set-closed-stages", "acme", "--none"]):
            self.assertEqual(0, self.ws.run(argv)[0])
        self.assertEqual({"fields_get", "search_read"}, {m for _, m in self.odoo.calls})
        self.assertEqual(json.loads((self.ws.dir / "profiles.json").read_text())["profiles"][0]["closed_stage_ids"], [])


if __name__ == "__main__":
    unittest.main()
