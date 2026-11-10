import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import test_every_finding as fixtures
import test_finishes_the_work as finishes

STRAY = "notes/stray.txt"
WORK = {"a.py": finishes.RIGHT, finishes.TEST_FILE: finishes.PROPERTY_TEXT}
SECOND = fixtures.step("second", ["b.py"], "second")


def of_kind(summary, kind):
    return [entry for entry in summary.get("findings") or () if entry.get("kind") == kind]


class UndeclaredNoise(unittest.TestCase):
    def setUp(self):
        self.scratch = finishes.Scratch()

    def tearDown(self):
        self.scratch.close()

    def a_write_no_msp_owns_is_reported_and_the_run_ships(self):
        completed, summary = self.scratch.built(
            [finishes.built_step()], {"first": [{"writes": {**WORK, STRAY: "stray\n"}}]}
        )
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + fixtures.tail(completed))
        self.assertEqual(summary["exit"], 0, fixtures.tail(completed))
        self.assertEqual([entry["detail"] for entry in of_kind(summary, "undeclared")], [STRAY])
        self.assertEqual(finishes.msp_of(summary, "first")["state"], "committed", fixtures.tail(completed))
        self.assertEqual(finishes.msp_of(summary, "first")["reconcile"]["undeclared"], [STRAY])

    def a_write_another_msp_owns_still_ends_crossing(self):
        completed, summary = self.scratch.built(
            [finishes.built_step(), SECOND],
            {
                "first": [{"writes": {**WORK, "b.py": "b = 2\n"}}],
                "second": [{"writes": {"b.py": "b = 1\n"}}],
            },
        )
        self.assertEqual(completed.returncode, 7, "mitosis exit code" + fixtures.tail(completed))
        self.assertEqual(summary["exit"], 7, fixtures.tail(completed))
        self.assertTrue(of_kind(summary, "crossing"), fixtures.tail(completed))


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        UndeclaredNoise(name)
        for name in (
            "a_write_no_msp_owns_is_reported_and_the_run_ships",
            "a_write_another_msp_owns_still_ends_crossing",
        )
    )
