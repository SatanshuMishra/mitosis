import os
import shlex
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import test_mutant_coverage as coverage
from test_mutant_coverage import (
    ADDS,
    CALL_ONLY_TEST,
    CORRECT_ADD,
    OTHER_TEST,
    TEST_FILE,
    Scratch,
    put,
    step,
    summary_of,
    tail,
)

EXACT_TEST = (
    "import unittest\n"
    "\n"
    "import calc\n"
    "\n"
    "\n"
    "class Calc(unittest.TestCase):\n"
    "    def adds(self):\n"
    "        self.assertEqual(calc.add(2, 3), 5)\n"
)


RED_TEST = (
    "import unittest\n"
    "\n"
    "\n"
    "class Broken(unittest.TestCase):\n"
    "    def test_unrelated(self):\n"
    "        self.fail(\"red before any mutant\")\n"
)
UNIMPORTABLE_TEST = "import module_that_is_not_installed\n"
RED_SUITE_REASON = (
    "no mutant of the work fails it: it still passes when calc.py:2 changes by return -> None, "
    "and the suite fails before any mutant is applied (exit 1)"
)


def escaping(targets):
    return (
        "for _path in %r:\n"
        "    try:\n"
        "        with open(_path, 'a') as _handle:\n"
        "            _handle.write('escaped\\n')\n"
        "    except OSError:\n"
        "        pass\n" % (list(targets),)
    )


def check_author(files=(TEST_FILE,)):
    return {**step("check-author", files, "check-author"), "role": "check-author"}


class Receipts(unittest.TestCase):
    def setUp(self):
        self.scratch = None

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()

    def _scratch(self, seeded=None):
        self.scratch = Scratch(seeded)
        put(os.path.join(self.scratch.stubs, "suite.py"), coverage.SUITE)
        return self.scratch

    def _first_property(self, completed, label="demo"):
        found = [
            msp for msp in summary_of(completed).get("msps") or [] if msp.get("msp") == label
        ]
        self.assertEqual(len(found), 1, "the JSON summary lists no MSP %s" % label + tail(completed))
        properties = (found[0].get("gate") or {}).get("properties") or []
        self.assertTrue(properties, "the MSP's gate carries no properties" + tail(completed))
        return properties[0]

    def _escape_targets(self):
        scratch = self.scratch
        return (
            os.path.join(scratch.repo, ".git", "refs", "heads", "escaped"),
            os.path.join(scratch.run_dir, "escaped.txt"),
            os.path.join(scratch.repo, "escaped.txt"),
        )

    def _assert_confined(self, completed):
        self.assertEqual(summary_of(completed).get("sandbox"), "sandbox-exec", tail(completed))
        for target in self._escape_targets():
            with self.subTest(target=target):
                self.assertFalse(
                    os.path.lexists(target),
                    "Worker-written code wrote %s outside the sandbox" % target + tail(completed),
                )
        self.assertEqual(self.scratch.git("rev-parse", "--verify", "-q", "refs/heads/escaped").returncode, 1)

    def _sandboxed(self, seeded, jobs, flags=lambda scratch: (), acceptance="unittest", steps=None):
        scratch = self._scratch(seeded)
        items = scratch.items(steps or [step("add", ["calc.py"], "demo", acceptance=[ADDS])])
        scratch.jobs(jobs(self._escape_targets()))
        return scratch.build(
            items,
            "--worker-sandbox",
            "--worker-write",
            scratch.stubs,
            *flags(scratch),
            acceptance=acceptance,
        )

    def _skip_without_sandbox(self):
        if sys.platform != "darwin":
            self.skipTest("sandbox-exec exists only on macOS")

    def sandboxed_worker_code_cannot_write_outside_at_the_acceptance_runs(self):
        self._skip_without_sandbox()
        completed = self._sandboxed(
            {TEST_FILE: EXACT_TEST},
            lambda targets: {"add": {"writes": {"calc.py": escaping(targets) + CORRECT_ADD}}},
        )
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        self.assertEqual(self._first_property(completed)["outcome"], "pass")
        self._assert_confined(completed)

    def sandboxed_worker_code_cannot_write_outside_at_the_suite_runs(self):
        self._skip_without_sandbox()
        completed = self._sandboxed(
            {TEST_FILE: CALL_ONLY_TEST, "tests/test_other.py": OTHER_TEST},
            lambda targets: {"add": {"writes": {"calc.py": escaping(targets) + CORRECT_ADD}}},
            lambda scratch: ("--suite-command", scratch.stub("suite.py", "{worktree}")),
            acceptance="keep",
        )
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        first = self._first_property(completed)
        self.assertEqual(first["outcome"], "pass")
        self.assertEqual(first.get("mutation"), "covered-elsewhere")
        self._assert_confined(completed)

    def sandboxed_worker_code_cannot_write_outside_at_the_gate_setup(self):
        self._skip_without_sandbox()
        completed = self._sandboxed(
            {TEST_FILE: EXACT_TEST},
            lambda targets: {"add": {"writes": {"calc.py": escaping(targets) + CORRECT_ADD}}},
            lambda scratch: ("--gate-setup", "%s -c 'import calc'" % shlex.quote(sys.executable)),
        )
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        self.assertEqual(self._first_property(completed)["outcome"], "pass")
        self._assert_confined(completed)

    def sandboxed_check_author_code_cannot_write_outside_at_the_lock_stage(self):
        self._skip_without_sandbox()
        completed = self._sandboxed(
            None,
            lambda targets: {
                "check-author": {"writes": {TEST_FILE: escaping(targets) + EXACT_TEST}, "msp": "lock"},
                "add": {"writes": {"calc.py": CORRECT_ADD}, "msp": "demo"},
            },
            steps=[
                check_author(),
                step("add", ["calc.py"], "demo", [ADDS], ("check-author",)),
            ],
        )
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        self.assertEqual((summary_of(completed).get("lock") or {}).get("state"), "ok")
        self._assert_confined(completed)

    def _red_suite_run(self, red):
        scratch = self._scratch(
            {TEST_FILE: CALL_ONLY_TEST, "tests/test_other.py": OTHER_TEST, "tests/test_red.py": red}
        )
        items = scratch.items([step("add", ["calc.py"], "demo", acceptance=[ADDS])])
        scratch.jobs({"add": {"writes": {"calc.py": CORRECT_ADD}}})
        return scratch.build(
            items, "--suite-command", scratch.stub("suite.py", "{worktree}"), acceptance="keep"
        )

    def _assert_weak_on_a_red_suite(self, completed):
        self.assertEqual(completed.returncode, 24, "mitosis exit code" + tail(completed))
        first = self._first_property(completed)
        self.assertEqual(first["outcome"], "weak")
        self.assertEqual(first.get("mutation"), "weak")
        self.assertEqual(first["reason"], RED_SUITE_REASON)
        self.assertNotIn("killers", first)

    def a_suite_red_before_any_mutant_keeps_the_property_weak(self):
        self._assert_weak_on_a_red_suite(self._red_suite_run(RED_TEST))

    def a_suite_that_cannot_import_keeps_the_property_weak(self):
        self._assert_weak_on_a_red_suite(self._red_suite_run(UNIMPORTABLE_TEST))


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            Receipts,
            (
                "sandboxed_worker_code_cannot_write_outside_at_the_acceptance_runs",
                "sandboxed_worker_code_cannot_write_outside_at_the_suite_runs",
                "sandboxed_worker_code_cannot_write_outside_at_the_gate_setup",
                "sandboxed_check_author_code_cannot_write_outside_at_the_lock_stage",
                "a_suite_red_before_any_mutant_keeps_the_property_weak",
                "a_suite_that_cannot_import_keeps_the_property_weak",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
