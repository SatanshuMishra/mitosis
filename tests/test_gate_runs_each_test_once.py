import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import test_every_finding as fixtures
import test_finishes_the_work as finishes

BASE = "def value():\n    return 1\n"
WORK = "def value():\n    return 5\n"


def test_text(assertion):
    return (
        "import unittest\n\nimport a\n\n\nclass A(unittest.TestCase):\n"
        "    def works(self):\n        %s\n" % assertion
    )


PASSES_EITHER_WAY = test_text("self.assertTrue(callable(a.value))")
LOOSE = test_text("self.assertNotEqual(a.value(), 1)")
EXACT = test_text("self.assertEqual(a.value(), 5)")


class GateRunsEachTestOnce(unittest.TestCase):
    def setUp(self):
        self.scratch = None

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()

    def _built(self, test):
        self.scratch = finishes.Scratch({"a.py": BASE})
        attempt = {"writes": {"a.py": WORK, finishes.TEST_FILE: test}}
        completed, summary = self.scratch.built(
            [finishes.built_step()], {"first": [attempt, attempt]}
        )
        self.assertEqual(completed.returncode, 0, fixtures.tail(completed))
        return completed, finishes.msp_of(summary, "first")["gate"]

    def _passes_on_one_attempt(self, test):
        completed, gate = self._built(test)
        self.assertEqual(len(gate["properties"]), 1, fixtures.tail(completed))
        judged = gate["properties"][0]
        self.assertEqual(judged["outcome"], "pass", fixtures.tail(completed))
        self.assertIsNone(judged["reason"], fixtures.tail(completed))
        self.assertEqual(gate["outcome"], "pass", fixtures.tail(completed))
        self.assertEqual(gate["unproven"], [], fixtures.tail(completed))
        self.assertEqual(len(self.scratch.attempts("first")), 1, fixtures.tail(completed))

    def a_test_that_passes_with_and_without_the_work_passes_and_is_not_sent_back(self):
        self._passes_on_one_attempt(PASSES_EITHER_WAY)

    def a_test_no_change_to_the_work_fails_passes_and_is_not_sent_back(self):
        self._passes_on_one_attempt(LOOSE)

    def the_gate_runs_each_acceptance_test_once_and_only_against_the_work(self):
        completed, gate = self._built(EXACT)
        folder = os.path.join(self.scratch.run_dir, "gate", "msp-0")
        self.assertEqual(
            sorted(os.listdir(folder)), ["0-with-work.log", "0-with-work.log.err"], fixtures.tail(completed)
        )
        judged = gate["properties"][0]
        self.assertEqual(judged["outcome"], "pass", fixtures.tail(completed))
        self.assertIsNone(judged["mutants"], fixtures.tail(completed))
        self.assertNotIn("mutation", judged)
        self.assertNotIn("coverage", judged)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        GateRunsEachTestOnce(name)
        for name in (
            "a_test_that_passes_with_and_without_the_work_passes_and_is_not_sent_back",
            "a_test_no_change_to_the_work_fails_passes_and_is_not_sent_back",
            "the_gate_runs_each_acceptance_test_once_and_only_against_the_work",
        )
    )
