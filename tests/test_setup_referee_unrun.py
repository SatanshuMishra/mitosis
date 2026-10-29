import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import test_setup_referee_write as setup_referee

lock_fixtures = setup_referee.lock_fixtures

UNRUN_REASON = (
    "the --gate-setup command wrote conftest.py, which the referee owns, "
    "so no acceptance run was judged"
)


class SetupRefereeUnrun(setup_referee.SetupRefereeWrite):
    def _trajectory_run(self, setup_script):
        real = lock_fixtures.Scratch.mitosis
        paths = []

        def with_trajectory(scratch, *args):
            path = os.path.join(scratch.tmp, "trajectory.jsonl")
            paths.append(path)
            return real(scratch, *args, "--trajectory", path)

        with mock.patch.object(lock_fixtures.Scratch, "mitosis", with_trajectory):
            completed = self._run(setup_script)
        return completed, paths[0]

    def a_setup_referee_write_names_why_every_property_went_unjudged(self):
        completed, _ = self._trajectory_run(setup_referee.REFEREE_SETUP)
        self.assertEqual(completed.returncode, 9, "mitosis exit code" + lock_fixtures.tail(completed))
        msp = self._msp(completed)
        self.assertEqual(msp["state"], "referee-reverted")
        self.assertIs(msp["reconcile"]["fatal"], True)
        self.assertEqual(msp["reconcile"]["referee"], ["conftest.py"])
        properties = msp["gate"]["properties"]
        self.assertTrue(properties, "the gate lists no property")
        for entry in properties:
            self.assertEqual(entry["reason"], UNRUN_REASON, "the reason of %s" % entry["test"])

    def a_setup_referee_write_leaves_no_trajectory_entry(self):
        completed, path = self._trajectory_run(setup_referee.REFEREE_SETUP)
        self.assertEqual(completed.returncode, 9, "mitosis exit code" + lock_fixtures.tail(completed))
        lines = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                lines = [line for line in handle if line.strip()]
        self.assertEqual(lines, [], "trajectory lines" + lock_fixtures.tail(completed))


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            SetupRefereeUnrun,
            (
                "a_setup_referee_write_names_why_every_property_went_unjudged",
                "a_setup_referee_write_leaves_no_trajectory_entry",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
