"""Unit tests for the reply guardrails (no harness needed)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import guards  # noqa: E402

JOBS = [
    {"jid": "J1", "minute": 10, "zone": None, "status": "unassigned"},
    {"jid": "J2", "minute": 20, "zone": "Kista", "status": "queued"},
    {"jid": "J3", "minute": 30, "zone": "Solna", "status": "in_progress"},
    {"jid": "J4", "minute": 40, "zone": None, "status": "travelling"},
    {"jid": "J5", "minute": 50, "zone": None, "status": "unassigned"},
]
TECHS = [{"tid": "T1", "off_shift": False}, {"tid": "T2", "off_shift": True}]
ZONES = ["Depot", "Kista", "Solna"]


def legal(*cmds):
    return guards.legal(list(cmds), JOBS, TECHS, ZONES)


class Guards(unittest.TestCase):
    def test_always_ends_in_wait_with_at_most_four(self):
        out = legal("[ask J1 location]", "[ask J5 location]", "[ask J1 urgency]", "[ask J5 urgency]",
                    "[ask J1 problem]")
        self.assertEqual(out[-1], "[wait]")
        self.assertEqual(len(out), 5)
        self.assertEqual(legal(), ["[wait]"])

    def test_closed_or_unknown_jobs_are_dropped(self):
        self.assertEqual(legal("[assign T1 J9 Kista]", "[ignore J9]", "[ask J9 location]"), ["[wait]"])

    def test_off_shift_or_unknown_technician_is_dropped(self):
        self.assertEqual(legal("[assign T2 J1 Kista]", "[assign T7 J1 Kista]"), ["[wait]"])

    def test_zone_must_be_on_the_map_or_known(self):
        self.assertEqual(legal("[assign T1 J1 Atlantis]"), ["[wait]"])
        self.assertEqual(legal("[assign T1 J1]"), ["[wait]"])            # no zone and none known
        self.assertEqual(legal("[assign T1 J2]"), ["[assign T1 J2]", "[wait]"])

    def test_same_job_is_assigned_once(self):
        self.assertEqual(legal("[assign T1 J1 Kista]", "[assign T1 J1 Solna]"), ["[assign T1 J1 Kista]", "[wait]"])

    def test_job_in_progress_cannot_be_assigned_unassigned_or_ignored(self):
        self.assertEqual(legal("[assign T1 J3 Solna]", "[unassign J3]", "[ignore J3]"), ["[wait]"])

    def test_unassign_only_queued_or_travelling(self):
        self.assertEqual(legal("[unassign J1]", "[unassign J2]", "[unassign J4]"),
                         ["[unassign J2]", "[unassign J4]", "[wait]"])

    def test_merge_never_into_itself_or_a_later_job(self):
        self.assertEqual(legal("[merge J1 J1]", "[merge J1 J5]"), ["[wait]"])
        self.assertEqual(legal("[merge J5 J1]"), ["[merge J5 J1]", "[wait]"])

    def test_unknown_question_and_duplicates_are_dropped(self):
        self.assertEqual(legal("[ask J1 colour]", "[ask J1 location]", "[ask J1 location]"),
                         ["[ask J1 location]", "[wait]"])


if __name__ == "__main__":
    unittest.main()
