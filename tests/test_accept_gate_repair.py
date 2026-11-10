import os
import re
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
KEY = "tests/test_a.py::A.works"


def test_text(assertion):
    return (
        "import unittest\n\nimport a\n\n\nclass A(unittest.TestCase):\n"
        "    def works(self):\n        %s\n" % assertion
    )


WEAK = test_text("self.assertNotEqual(a.value(), 1)")
INERT = test_text("self.assertTrue(callable(a.value))")
EXACT = test_text("self.assertEqual(a.value(), 5)")

WEAK_NOTE = re.compile(
    r"The gate changed a\.py:2 by (number \+1|number -1|return -> None) in this Lane's work and "
    + re.escape(KEY)
    + r" still passed, so the test cannot tell working code from broken code\."
)

INERT_NOTE = (
    KEY + " passes with this Lane's work reverted and no change to the work makes it fail, so it "
    "cannot tell the work from its absence. Change it so it fails without the work and passes with it."
)


def attempt(test):
    return {"writes": {"a.py": WORK, finishes.TEST_FILE: test}}


class GateRepair(unittest.TestCase):
    def setUp(self):
        self.scratch = None

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()

    def _repaired(self, first):
        self.scratch = finishes.Scratch({"a.py": BASE})
        completed, summary = self.scratch.built(
            [finishes.built_step()], {"first": [attempt(first), attempt(EXACT)]}
        )
        tasks = self.scratch.attempts("first")
        self.assertEqual(len(tasks), 2, fixtures.tail(completed))
        gate = finishes.msp_of(summary, "first")["gate"]
        self.assertEqual(gate["outcome"], "pass", fixtures.tail(completed))
        self.assertEqual(completed.returncode, 0, fixtures.tail(completed))
        self.assertTrue(self.scratch.on_branch("mitosis/first", finishes.TEST_FILE))
        return tasks

    def a_weak_test_goes_back_to_its_worker_and_the_repair_gates_pass(self):
        tasks = self._repaired(WEAK)
        self.assertRegex(tasks[1], WEAK_NOTE)
        self.assertIn("Output of the run:", tasks[1])
        self.assertNotRegex(tasks[0], WEAK_NOTE)

    def an_inert_test_goes_back_to_its_worker_and_the_repair_gates_pass(self):
        tasks = self._repaired(INERT)
        self.assertIn(INERT_NOTE, tasks[1])
        self.assertNotIn(INERT_NOTE, tasks[0])

    def a_weak_locked_test_is_not_sent_back(self):
        self.scratch = finishes.Scratch({"a.py": BASE, finishes.TEST_FILE: WEAK})
        charter = self.scratch.file("charter.md", "# Charter\n\n- Locked: %s\n" % finishes.TEST_FILE)
        completed, summary = self.scratch.built(
            [finishes.built_step(files=("a.py",))],
            {"first": [{"writes": {"a.py": WORK}}, {"writes": {"a.py": WORK}}]},
            flags=("--charter", charter),
        )
        self.assertEqual(len(self.scratch.attempts("first")), 1, fixtures.tail(completed))
        gate = finishes.msp_of(summary, "first")["gate"]
        self.assertEqual(gate["outcome"], "weak", fixtures.tail(completed))
        self.assertEqual(gate["properties"][0]["outcome"], "weak", fixtures.tail(completed))


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        GateRepair(name)
        for name in (
            "a_weak_test_goes_back_to_its_worker_and_the_repair_gates_pass",
            "an_inert_test_goes_back_to_its_worker_and_the_repair_gates_pass",
            "a_weak_locked_test_is_not_sent_back",
        )
    )
