import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import core
import test_every_finding as fixtures
import test_finishes_the_work as finishes

BASE = "def cut(text):\n    return text.lower().replace(\" \", \"-\")\n"

STRIPPED_FIRST = (
    "def cut(text):\n"
    "    if text.strip() == text:\n"
    "        return text.lower().replace(\" \", \"-\")\n"
    "    return text.strip().lower()\n"
)

BESIDE = BASE + "\n\ndef shout(text):\n    return text.upper()\n"

CLEAN_SLUG = (
    "import unittest\n\nimport a\n\n\nclass A(unittest.TestCase):\n"
    "    def works(self):\n        self.assertEqual(a.cut(\"A Clean Slug\"), \"a-clean-slug\")\n"
)


class Guards(unittest.TestCase):
    def setUp(self):
        self.scratch = finishes.Scratch({"a.py": BASE})

    def tearDown(self):
        self.scratch.close()

    def _judged(self, work):
        completed, summary = self.scratch.built(
            [finishes.built_step()],
            {"first": [{"writes": {"a.py": work, finishes.TEST_FILE: CLEAN_SLUG}}]},
        )
        gate = finishes.msp_of(summary, "first")["gate"]
        self.assertEqual(len(gate["properties"]), 1, fixtures.tail(completed))
        return completed, gate, gate["properties"][0]

    def a_test_that_passes_without_the_work_and_kills_a_mutant_gates_pass(self):
        completed, gate, judged = self._judged(STRIPPED_FIRST)
        self.assertEqual(judged["outcome"], "pass", fixtures.tail(completed))
        self.assertEqual(gate["outcome"], "pass", fixtures.tail(completed))
        self.assertEqual(judged["mutation"], "killed")
        place = judged["mutants"]["killed"]
        self.assertEqual(place["file"], "a.py")
        self.assertEqual(
            judged["reason"],
            "passes with and without the work and fails when a.py:%d changes by %s, so it guards "
            "behaviour the work must keep" % (place["line"], place["op"]),
        )
        self.assertEqual(judged["reason"], core.GUARD_KILLED_REASON % ("a.py", place["line"], place["op"]))
        self.assertEqual(completed.returncode, 0, fixtures.tail(completed))

    def a_test_beside_a_mutation_proven_test_of_its_step_is_a_guard_and_never_sent_back(self):
        both = CLEAN_SLUG + "\n    def exists(self):\n        self.assertTrue(callable(a.cut))\n"
        acceptance = ({"file": finishes.TEST_FILE, "test": "A.works"}, {"file": finishes.TEST_FILE, "test": "A.exists"})
        work = {"writes": {"a.py": STRIPPED_FIRST, finishes.TEST_FILE: both}}
        completed, summary = self.scratch.built([finishes.built_step(acceptance=acceptance)], {"first": [work, work]})
        gate = finishes.msp_of(summary, "first")["gate"]
        outcomes = {entry["test"]: entry["outcome"] for entry in gate["properties"]}
        self.assertEqual(outcomes, {"A.works": "pass", "A.exists": "not-applicable"}, fixtures.tail(completed))
        self.assertEqual(len(self.scratch.attempts("first")), 1, fixtures.tail(completed))

    def a_test_that_passes_without_the_work_and_no_mutant_fails_stays_inert(self):
        completed, gate, judged = self._judged(BESIDE)
        self.assertEqual(judged["outcome"], "inert", fixtures.tail(completed))
        self.assertEqual(gate["outcome"], "inert", fixtures.tail(completed))
        self.assertEqual(judged["reason"], "passes with the implementation reverted")
        self.assertIsNone(judged["mutants"]["killed"], fixtures.tail(completed))
        self.assertNotEqual(judged.get("mutation"), "weak")


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        Guards(name)
        for name in (
            "a_test_that_passes_without_the_work_and_kills_a_mutant_gates_pass",
            "a_test_beside_a_mutation_proven_test_of_its_step_is_a_guard_and_never_sent_back",
            "a_test_that_passes_without_the_work_and_no_mutant_fails_stays_inert",
        )
    )
