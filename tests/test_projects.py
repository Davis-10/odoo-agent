import json
import unittest

from tests.fixtures import instance
from tests.support import FakeOdoo, Workspace

ME, OTHER = 7, 90
WRITES = {"create", "write", "unlink"}
GLOBEX = {"id": 500, "name": "Globex Corp"}


def seed(odoo: FakeOdoo, major: int) -> None:
    """Acme's two Globex projects (stage 2 L2, L6) plus projects that trigger every flag."""
    analytic = "account_id" if major >= 18 else "analytic_account_id"
    odoo.records["res.partner"] = [{"id": 500, "name": "Globex Corp"}, {"id": 501, "name": "ACME"}]
    odoo.records["res.users"] = [{"id": ME, "name": "Jane Doe"}, {"id": OTHER, "name": "Ana Manager"}]
    odoo.records["account.analytic.account"] = [{"id": 600, "name": "Globex webshop"},
                                                {"id": 601, "name": "Globex legacy"}, {"id": 602, "name": "Acme"}]
    if major >= 16:
        odoo.records["project.project.stage"] = [
            {"id": 7, "name": "En desarrollo", "fold": False},
            {"id": 15, "name": "En mantenimiento", "fold": True},
            {"id": 31, "name": "FINALIZADO", "fold": True},
        ]
    stage = (lambda i: {"stage_id": i}) if major >= 16 else (lambda i: {})
    project = {"active": True, "allow_timesheets": True}
    odoo.records["project.project"] = [
        project | {"id": 501, "name": "Globex | Webshop", "partner_id": 500, "user_id": OTHER,
                   analytic: 600} | stage(7),
        project | {"id": 502, "name": "Legacy site (Globex)", "partner_id": 500, "user_id": ME, analytic: 601} | stage(31),
        project | {"id": 10, "name": "Internal", "partner_id": False, "user_id": False, analytic: False,
                   "allow_timesheets": False} | stage(False),
        project | {"id": 11, "name": "Acme support", "partner_id": 501, "user_id": OTHER, analytic: 602} | stage(15),
    ]
    odoo.records["project.task.type"] = [{"id": 1, "name": "To do", "fold": False}, {"id": 2, "name": "Done", "fold": True}]
    assign = (lambda uid: {"user_id": uid}) if major <= 14 else (lambda uid: {"user_ids": [uid]})
    base = {"stage_id": 1, "date_deadline": False}
    odoo.records["project.task"] = [
        base | {"id": 9101, "name": "Improvement backlog", "project_id": 501} | assign(ME),
        base | {"id": 9103, "name": "Checkout", "project_id": 501} | assign(ME),
        base | {"id": 9104, "name": "Finished", "project_id": 501, "stage_id": 2} | assign(ME),
        base | {"id": 3000, "name": "Old work", "project_id": 502} | assign(ME),
        base | {"id": 3001, "name": "Housekeeping", "project_id": 10} | assign(ME),
        base | {"id": 3002, "name": "Not mine", "project_id": 11} | assign(OTHER),
        base | {"id": 3003, "name": "Private", "project_id": False, "stage_id": False} | assign(ME),
    ]


def row(id, name, client, manager, stage, accepts, analytic, mine, flags):
    return {"id": id, "name": name, "client": client, "manager": manager, "stage": stage,
            "accepts_timesheets": accepts, "analytic_account": analytic, "my_open_tasks": mine, "flags": flags}


ECOMMERCE = row(501, "Globex | Webshop", GLOBEX, {"id": OTHER, "name": "Ana Manager"},
                {"id": 7, "name": "En desarrollo"}, True, {"id": 600, "name": "Globex webshop"}, 2, [])
KD_SUB = row(502, "Legacy site (Globex)", GLOBEX, {"id": ME, "name": "Jane Doe"},
             {"id": 31, "name": "FINALIZADO"}, True, {"id": 601, "name": "Globex legacy"}, 1, ["closed_stage"])
INTERNAL = row(10, "Internal", None, None, None, False, None, 1, ["no_analytic_account", "no_timesheets"])


class ProjectsTests(unittest.TestCase):
    def workspace(self, major: int = 16, closed: list[int] | None = (31,)) -> tuple[Workspace, FakeOdoo]:
        ws = Workspace()
        self.addCleanup(ws.cleanup)
        ws.run(["profile", "add", "acme", "--url", "https://odoo.example.com", "--db", "prod", "--username", "me"])
        ws.credentials.set("acme", "key")
        version, fields = instance(major)
        odoo = FakeOdoo(version, fields, uid=ME)
        seed(odoo, major)
        ws.transport_factory = lambda profile: odoo
        if closed:
            ws.profiles.set_closed_project_stages("acme", list(closed))
        return ws, odoo

    def assertNoWrites(self, odoo):
        self.assertEqual(set(), {m for _, m in odoo.calls} & WRITES)

    # S1: projects without --name are those with my open tasks, same open rule as `tasks mine`
    def test_projects_without_name_lists_projects_with_my_open_tasks(self):
        ws, odoo = self.workspace()
        code, out, err = ws.run(["projects"])
        self.assertEqual((0, ""), (code, err))
        self.assertEqual({"profile": "acme", "projects": [ECOMMERCE, INTERNAL, KD_SUB]}, json.loads(out))
        self.assertNoWrites(odoo)

    # S1: the open-task rule is shared with `tasks mine`, including task closing stages
    def test_projects_respects_task_closing_stages(self):
        ws, _ = self.workspace()
        ws.profiles.set_closed_stages("acme", [1])
        code, out, err = ws.run(["projects"])
        self.assertEqual((0, "", {"profile": "acme", "projects": []}), (code, err, json.loads(out)))

    # S1: Globex example from L2; --name matches the project name, case-insensitive
    def test_name_search_flags_closed_project_stage_only(self):
        ws, odoo = self.workspace()
        code, out, err = ws.run(["projects", "--name", "globex"])
        self.assertEqual((0, ""), (code, err))
        self.assertEqual({"profile": "acme", "projects": [ECOMMERCE, KD_SUB]}, json.loads(out))
        self.assertNoWrites(odoo)

    # S1: --name matches the client name too, and lists projects where I have no tasks
    def test_name_search_matches_client_name_and_projects_without_my_tasks(self):
        ws, _ = self.workspace()
        code, out, err = ws.run(["projects", "--name", "globex corp"])
        self.assertEqual((0, "", [501, 502]), (code, err, [p["id"] for p in json.loads(out)["projects"]]))
        code, out, err = ws.run(["projects", "--profile", "acme", "--name", "ACME"])
        self.assertEqual((0, ""), (code, err))
        self.assertEqual([row(11, "Acme support", {"id": 501, "name": "ACME"}, {"id": OTHER, "name": "Ana Manager"},
                              {"id": 15, "name": "En mantenimiento"}, True, {"id": 602, "name": "Acme"}, 0, [])],
                         json.loads(out)["projects"])

    # S1: folding is not closing (L6); without closing project stages nothing is flagged closed_stage
    def test_folded_project_stage_is_not_closed(self):
        ws, _ = self.workspace(closed=None)
        code, out, err = ws.run(["projects", "--name", "globex"])
        self.assertEqual((0, "", [[], []]), (code, err, [p["flags"] for p in json.loads(out)["projects"]]))

    # S1: --limit like tasks
    def test_limit_is_applied_and_validated(self):
        ws, _ = self.workspace()
        code, out, err = ws.run(["projects", "--limit", "1"])
        self.assertEqual((0, "", [501]), (code, err, [p["id"] for p in json.loads(out)["projects"]]))
        code, out, err = ws.run(["projects", "--name", "e", "--limit", "1001"])
        self.assertEqual((2, ""), (code, out))
        self.assertIn("--limit must be between 1 and 1000", err)

    # S1, S7: Odoo 14 has no project stages; Odoo 18 renamed the analytic account field
    def test_every_version_answers_the_same_shape(self):
        for major in (14, 16, 17, 18):
            with self.subTest(major=major):
                ws, _ = self.workspace(major)
                code, out, err = ws.run(["projects", "--name", "globex"])
                self.assertEqual((0, ""), (code, err))
                projects = json.loads(out)["projects"]
                if major == 14:
                    self.assertEqual([ECOMMERCE | {"stage": None}, KD_SUB | {"stage": None, "flags": []}], projects)
                else:
                    self.assertEqual([ECOMMERCE, KD_SUB], projects)

    # S7: fields an instance lacks are null and raise no flag
    def test_missing_optional_fields_are_null_without_flags(self):
        ws, odoo = self.workspace(14)
        for name in ("partner_id", "user_id", "allow_timesheets", "analytic_account_id"):
            del odoo.fields["project.project"][name]
        code, out, err = ws.run(["projects"])
        self.assertEqual((0, ""), (code, err))
        self.assertEqual([row(501, "Globex | Webshop", None, None, None, None, None, 2, []),
                          row(10, "Internal", None, None, None, None, None, 1, []),
                          row(502, "Legacy site (Globex)", None, None, None, None, None, 1, [])],
                         json.loads(out)["projects"])
        code, out, err = ws.run(["projects", "--name", "globex corp"])
        self.assertEqual((0, "", []), (code, err, json.loads(out)["projects"]))


class ProjectStageTests(unittest.TestCase):
    def setUp(self):
        self.ws, self.odoo = ProjectsTests.workspace(self, closed=None)

    def stored(self):
        return [p.closed_project_stage_ids for p in self.ws.profiles.list()]

    # S1: read-only project stage listing
    def test_project_stages_lists_id_name_folded_closed(self):
        self.ws.profiles.set_closed_project_stages("acme", [31])
        code, out, err = self.ws.run(["project-stages"])
        self.assertEqual((0, ""), (code, err))
        self.assertEqual({"profile": "acme", "project_stages": [
            {"id": 7, "name": "En desarrollo", "folded": False, "closed": False},
            {"id": 15, "name": "En mantenimiento", "folded": True, "closed": False},
            {"id": 31, "name": "FINALIZADO", "folded": True, "closed": True},
        ]}, json.loads(out))
        code, out, err = self.ws.run(["project-stages", "--profile", "acme", "--name", "en "])
        self.assertEqual((0, "", [7, 15]), (code, err, [s["id"] for s in json.loads(out)["project_stages"]]))
        self.assertEqual(set(), {m for _, m in self.odoo.calls} & WRITES)

    # S1, S7: Odoo 14-15 have no project stages
    def test_project_stages_on_odoo_14_points_to_doctor(self):
        ws, _ = ProjectsTests.workspace(self, 14, closed=None)
        self.assertEqual((1, "", "error: This Odoo instance has no field for 'project_stage.name'; run agentodoo doctor\n"),
                         ws.run(["project-stages"]))
        self.assertEqual((1, "", "error: This Odoo instance has no field for 'project_stage.name'; run agentodoo doctor\n"),
                         ws.run(["profile", "set-closed-project-stages", "acme", "31"]))
        self.assertEqual([()], [p.closed_project_stage_ids for p in ws.profiles.list()])

    # S1: set-closed-project-stages replaces the list after validating IDs
    def test_set_closed_project_stages_replaces_list(self):
        code, out, err = self.ws.run(["profile", "set-closed-project-stages", "acme", "31", "15", "31"])
        self.assertEqual((0, "", {"profile": "acme", "closed_project_stage_ids": [15, 31]}),
                         (code, err, json.loads(out)))
        self.assertEqual([(15, 31)], self.stored())
        self.assertEqual([()], [p.closed_stage_ids for p in self.ws.profiles.list()])

    # S1: unknown IDs are named and leave the stored list unchanged
    def test_set_closed_project_stages_rejects_unknown_ids(self):
        self.ws.profiles.set_closed_project_stages("acme", [31])
        before = self.ws.profiles.list()
        code, out, err = self.ws.run(["profile", "set-closed-project-stages", "acme", "31", "99", "98"])
        self.assertEqual((1, "", "error: Unknown project stage IDs in Odoo: 98, 99\n"), (code, out, err))
        self.assertEqual(before, self.ws.profiles.list())

    # S1: --none clears; usage errors keep the list
    def test_set_closed_project_stages_none_and_usage_errors(self):
        self.ws.profiles.set_closed_project_stages("acme", [31])
        code, out, err = self.ws.run(["profile", "set-closed-project-stages", "acme", "--none"])
        self.assertEqual((0, "", {"profile": "acme", "closed_project_stage_ids": []}), (code, err, json.loads(out)))
        self.assertEqual([()], self.stored())
        self.ws.profiles.set_closed_project_stages("acme", [31])
        for argv in (["acme"], ["acme", "31", "--none"]):
            with self.subTest(argv=argv):
                code, out, err = self.ws.run(["profile", "set-closed-project-stages"] + argv)
                self.assertEqual((2, ""), (code, out))
                self.assertIn("set-closed-project-stages: give project stage IDs or --none", err)
                self.assertEqual([(31,)], self.stored())
        self.assertEqual((1, "", "error: Unknown profile 'nope'\n"),
                         self.ws.run(["profile", "set-closed-project-stages", "nope", "31"]))

    # S1: profile list shows the list; a profile file without the key still loads
    def test_profile_list_and_old_profile_file(self):
        legacy = {"version": 1, "profiles": [{"name": "acme", "url": "https://odoo.example.com",
                                               "database": "prod", "username": "me", "allow_http": False,
                                               "closed_stage_ids": [2]}]}
        (self.ws.dir / "profiles.json").write_text(json.dumps(legacy), encoding="utf-8")
        code, out, err = self.ws.run(["profile", "list"])
        self.assertEqual((0, ""), (code, err))
        self.assertEqual(([2], []), (json.loads(out)[0]["closed_stage_ids"], json.loads(out)[0]["closed_project_stage_ids"]))
        self.ws.profiles.set_closed_project_stages("acme", [31])
        self.assertEqual([31], json.loads(self.ws.run(["profile", "list"])[1])[0]["closed_project_stage_ids"])

    # S1: doctor reports configured and stale closing project stages as warnings only
    def test_doctor_warns_about_stale_project_stages(self):
        self.ws.profiles.set_closed_project_stages("acme", [31, 99])
        code, out, err = self.ws.run(["doctor"])
        report = json.loads(out)
        self.assertEqual((0, "", "ok"), (code, err, report["status"]))
        self.assertEqual(([31, 99], ["closing project stage ID 99 no longer exists in Odoo"]),
                         (report["closed_project_stage_ids"], report["warnings"]))

    # S6: no new command calls an Odoo write method
    def test_new_commands_never_write(self):
        for argv in (["projects"], ["projects", "--name", "globex"], ["project-stages"],
                     ["profile", "set-closed-project-stages", "acme", "31"], ["doctor"],
                     ["profile", "set-closed-project-stages", "acme", "--none"]):
            self.assertEqual(0, self.ws.run(argv)[0], argv)
        self.assertEqual({"fields_get", "search_read"}, {m for _, m in self.odoo.calls})


if __name__ == "__main__":
    unittest.main()
