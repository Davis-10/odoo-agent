import unittest

from agentodoo.capabilities import CapabilityMap, ServerVersion
from agentodoo.errors import NotAllowedError
from tests.fixtures import ODOO16_VERSION, instance, version


class ServerVersionTests(unittest.TestCase):
    def test_odoo_16_community_is_supported(self):
        v = ServerVersion.parse(ODOO16_VERSION)
        self.assertEqual((16, "community", "16.0-20240101", True), (v.major, v.edition, v.label, v.supported))

    def test_enterprise_saas_and_old_versions(self):
        self.assertEqual((17, "enterprise", True), (lambda v: (v.major, v.edition, v.supported))(ServerVersion.parse(version(17, "e"))))
        saas = {"server_version": "saas~17.2+e", "server_version_info": ["saas~17", 2, 0, "final", 0, "e"]}
        self.assertEqual(17, ServerVersion.parse(saas).major)
        self.assertFalse(ServerVersion.parse(version(13)).supported)


class CapabilityMapTests(unittest.TestCase):
    EXPECTED = {
        14: {"task.assignees": ("user_id", "many2one"), "task.planned_hours": ("planned_hours", "float"), "task.status": ("kanban_state", "selection")},
        16: {"task.assignees": ("user_ids", "many2many"), "task.planned_hours": ("planned_hours", "float"), "task.status": ("kanban_state", "selection")},
        17: {"task.assignees": ("user_ids", "many2many"), "task.planned_hours": ("allocated_hours", "float"), "task.status": ("state", "selection")},
        18: {"task.assignees": ("user_ids", "many2many"), "task.planned_hours": ("allocated_hours", "float"), "task.status": ("state", "selection")},
    }

    def test_each_version_maps_to_its_own_field_names(self):
        for major, expected in self.EXPECTED.items():
            with self.subTest(major=major):
                caps = CapabilityMap.build(instance(major)[1])
                for logical, (field, kind) in expected.items():
                    self.assertEqual(field, caps.field(logical))
                    self.assertEqual(kind, caps.kind(logical))
                self.assertEqual([], caps.missing_required())
                self.assertEqual("stage_id", caps.field("task.stage"))

    # S7: project fields, absent project stages on 14, renamed analytic account on 18
    def test_project_and_write_fields_per_version(self):
        for major, stage, analytic in ((14, None, "analytic_account_id"), (16, "stage_id", "analytic_account_id"),
                                       (17, "stage_id", "analytic_account_id"), (18, "stage_id", "account_id")):
            with self.subTest(major=major):
                fields = instance(major)[1]
                if major < 16:
                    self.assertNotIn("project.project.stage", fields)
                report = CapabilityMap.build(fields).report()
                self.assertEqual((stage, analytic, "unit_amount", "user_id"),
                                 (report["project.stage"], report["project.analytic"], report["timesheet.hours"],
                                  report["employee.user"]))
                self.assertEqual([], CapabilityMap.build(fields).missing_for_writes())

    def test_project_stage_must_point_to_project_stages(self):
        fields = instance(16)[1]
        fields["project.project"]["stage_id"]["relation"] = "project.task.type"
        self.assertFalse(CapabilityMap.build(fields).has("project.stage"))

    def test_missing_required_and_optional_fields_are_reported(self):
        fields = instance(16)[1]
        del fields["project.task"]["user_ids"]
        del fields["project.task"]["planned_hours"]
        caps = CapabilityMap.build(fields)
        self.assertEqual(["task.assignees"], caps.missing_required())
        self.assertIsNone(caps.report()["task.planned_hours"])
        self.assertFalse(caps.has("task.planned_hours"))
        with self.assertRaises(NotAllowedError):
            caps.field("task.assignees")

    def test_candidate_with_wrong_type_is_not_used(self):
        fields = instance(16)[1]
        fields["project.task"]["user_ids"] = {"type": "char", "string": "Not users"}
        self.assertEqual(["task.assignees"], CapabilityMap.build(fields).missing_required())


if __name__ == "__main__":
    unittest.main()
