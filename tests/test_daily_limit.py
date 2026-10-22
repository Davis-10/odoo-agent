import json
import os
import stat
import unittest
from datetime import date, timedelta

from tests.test_writes import OTHER_LINE, WriteCase

FULL_DAY = (date.today() - timedelta(days=2)).isoformat()
SIX_HOURS_DAY = (date.today() - timedelta(days=1)).isoformat()
MINE = OTHER_LINE | {"employee_id": 70, "user_id": 0}


def time_argv(day: str, hours: str) -> list[str]:
    return ["time-plan", "--task-id", "9101", "--hours", hours, "--date", day, "--description", "Daily limit"]


class DailyLimitTests(WriteCase):
    def workspace(self, major: int = 16):
        ws, odoo = super().workspace(major)
        lines = odoo.records["account.analytic.line"]
        # L11: 2026-08-21 had 2 + 1.5 + 3 + 1.5 = 8 h; lines on other projects count too
        for offset, (hours, project) in enumerate(((2, 501), (1.5, 501), (3, 10), (1.5, 502))):
            lines.append(MINE | {"id": 9100 + offset, "date": FULL_DAY, "unit_amount": hours, "project_id": project,
                                 "name": f"Work {offset}"})
        lines.append(MINE | {"id": 9200, "date": SIX_HOURS_DAY, "unit_amount": 6.0, "name": "Six"})
        # another employee's lines on the same day never count
        lines.append(OTHER_LINE | {"id": 9201, "date": SIX_HOURS_DAY, "unit_amount": 5.0})
        return ws, odoo

    # S8: a full day refuses even half an hour more
    def test_full_day_refuses_more_hours(self):
        ws, odoo = self.workspace()
        self.assertRefused(ws, odoo, time_argv(FULL_DAY, "0.5"),
                           f"Day {FULL_DAY} would have 8.5 h (8 h already logged + 0.5 h); "
                           "the limit for profile 'acme' is 8 h")

    # S8: exactly the limit is allowed; display shows the day before and after
    def test_exactly_the_limit_is_allowed(self):
        ws, odoo = self.workspace()
        plan = self.plan(ws, time_argv(SIX_HOURS_DAY, "2"))
        self.assertEqual((6.0, 8.0), (plan["display"]["day_hours_before"], plan["display"]["day_hours_after"]))
        self.assertRefused(ws, odoo, time_argv(SIX_HOURS_DAY, "2.5"),
                           f"Day {SIX_HOURS_DAY} would have 8.5 h (6 h already logged + 2.5 h); "
                           "the limit for profile 'acme' is 8 h")

    # S8: the profile's own limit applies
    def test_profile_limit_applies(self):
        ws, odoo = self.workspace()
        ws.profiles.set_max_daily_hours("acme", 7.5)
        self.assertRefused(ws, odoo, time_argv(SIX_HOURS_DAY, "2"),
                           f"Day {SIX_HOURS_DAY} would have 8 h (6 h already logged + 2 h); "
                           "the limit for profile 'acme' is 7.5 h")
        self.assertEqual(7.5, self.plan(ws, time_argv(SIX_HOURS_DAY, "1.5"))["display"]["day_hours_after"])

    # S8, S4: apply re-checks the limit against lines logged after the plan
    def test_apply_rechecks_limit(self):
        ws, odoo = self.workspace()
        plan_id = self.plan(ws, time_argv(SIX_HOURS_DAY, "2"))["plan_id"]
        odoo.records["account.analytic.line"].append(MINE | {"id": 9300, "date": SIX_HOURS_DAY, "unit_amount": 1.0,
                                                             "name": "Meanwhile"})
        odoo.calls.clear()
        self.assertRefused(ws, odoo, ["apply", plan_id, "--confirm", plan_id],
                           f"Day {SIX_HOURS_DAY} would have 9 h (7 h already logged + 2 h); "
                           "the limit for profile 'acme' is 8 h")
        self.assertEqual([f"plans/{plan_id}.json"], self.state_files(ws))


class MaxDailyHoursProfileTests(WriteCase):
    # S8: set-max-daily-hours stores the limit privately and profile list / doctor show it
    def test_set_max_daily_hours(self):
        ws, _ = self.workspace()
        self.assertEqual(8.0, json.loads(ws.run(["profile", "list"])[1])[0]["max_daily_hours"])
        code, out, err = ws.run(["profile", "set-max-daily-hours", "acme", "7.5"])
        self.assertEqual((0, "", {"profile": "acme", "max_daily_hours": 7.5}), (code, err, json.loads(out)))
        self.assertEqual(0o600, stat.S_IMODE(os.stat(ws.dir / "profiles.json").st_mode))
        self.assertEqual(7.5, json.loads(ws.run(["profile", "list"])[1])[0]["max_daily_hours"])
        self.assertEqual(7.5, json.loads(ws.run(["doctor"])[1])["max_daily_hours"])
        self.assertEqual(24.0, json.loads(ws.run(["profile", "set-max-daily-hours", "acme", "24"])[1])["max_daily_hours"])

    # S8: invalid limits and unknown profiles exit 1 and keep the stored value
    def test_invalid_limit_is_rejected(self):
        ws, _ = self.workspace()
        ws.profiles.set_max_daily_hours("acme", 7.5)
        for value in ("0", "-1", "24.01", "nan", "inf"):
            with self.subTest(value=value):
                self.assertEqual((1, "", "error: Max daily hours must be a finite number greater than 0 "
                                         "and at most 24\n"),
                                 ws.run(["profile", "set-max-daily-hours", "acme", value]))
                self.assertEqual(7.5, ws.profiles.get("acme").max_daily_hours)
        self.assertEqual((1, "", "error: Unknown profile 'nope'\n"),
                         ws.run(["profile", "set-max-daily-hours", "nope", "6"]))
        self.assertEqual(7.5, ws.profiles.get("acme").max_daily_hours)

    # S8: profile files saved before the limit existed load with 8
    def test_old_profile_file_loads_with_8(self):
        ws, _ = self.workspace()
        legacy = {"version": 1, "profiles": [{"name": "acme", "url": "https://odoo.example.com",
                                               "database": "prod", "username": "me", "allow_http": False}]}
        (ws.dir / "profiles.json").write_text(json.dumps(legacy), encoding="utf-8")
        self.assertEqual(8.0, json.loads(ws.run(["profile", "list"])[1])[0]["max_daily_hours"])

    def test_help_documents_limit(self):
        ws, _ = self.workspace()
        self.assertIn("set-max-daily-hours", ws.run(["profile", "--help"])[1])
        self.assertIn("max_daily_hours", ws.run(["time-plan", "--help"])[1])


if __name__ == "__main__":
    unittest.main()
