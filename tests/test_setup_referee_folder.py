import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import referee
import test_setup_referee_write as setup_referee

FOLDER_SETUP = r'''
import os
import sys

full = os.path.join(sys.argv[1], *PLANTED.split("/"))
os.makedirs(os.path.dirname(full), exist_ok=True)
with open(full, "w", encoding="utf-8", newline="") as handle:
    handle.write("planted\n")
sys.exit(0)
'''

PLANTED_FOLDERS = (
    ("__snapshots__/calc.test.js.snap", "__snapshots__"),
    (".receipts/gates.md", ".receipts"),
)


class SetupRefereeFolder(setup_referee.SetupRefereeWrite):
    def _planted_run(self, planted):
        if self.scratch is not None:
            self.scratch.close()
            self.scratch = None
        return self._run("PLANTED = %r\n%s" % (planted, FOLDER_SETUP))

    def a_referee_named_folder_gate_setup_makes_is_fatal(self):
        for planted, folder in PLANTED_FOLDERS:
            with self.subTest(planted=planted):
                completed = self._planted_run(planted)
                self.assertEqual(
                    completed.returncode, 9, "mitosis exit code" + setup_referee.lock_fixtures.tail(completed)
                )
                msp = self._msp(completed)
                self.assertEqual(msp["state"], "referee-reverted")
                self.assertIs(msp["reconcile"]["fatal"], True)
                self.assertEqual(msp["reconcile"]["referee"], [folder])
                self.assertEqual(self._accept_log(), [])
                listed = self.scratch.git(
                    "ls-remote", "--heads", self.scratch.remote, "refs/heads/mitosis/demo"
                )
                self.assertEqual(listed.returncode, 0, listed.stderr)
                self.assertEqual(listed.stdout, "")


class TopLevelReason(unittest.TestCase):
    def a_folder_holding_a_locked_path_is_locked(self):
        self.assertEqual(referee.top_level_reason("golden", ("golden/a.txt",)), referee.LOCKED_REASON)
        self.assertEqual(referee.LOCKED_REASON, "locked by the charter")

    def a_referee_folder_name_matches_its_folder_marker(self):
        self.assertEqual(
            referee.top_level_reason("__snapshots__", ()),
            "matches the referee marker *__snapshots__/*",
        )
        self.assertEqual(
            referee.top_level_reason(".receipts", ()), "matches the referee marker *.receipts/*"
        )

    def installed_and_ordinary_names_are_not_referee_paths(self):
        for name in ("node_modules", "built.txt"):
            with self.subTest(name=name):
                self.assertIsNone(referee.top_level_reason(name, ("golden/a.txt",)))


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (SetupRefereeFolder, ("a_referee_named_folder_gate_setup_makes_is_fatal",)),
        (
            TopLevelReason,
            (
                "a_folder_holding_a_locked_path_is_locked",
                "a_referee_folder_name_matches_its_folder_marker",
                "installed_and_ordinary_names_are_not_referee_paths",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
