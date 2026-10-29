import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_gate_teeth as teeth

CALC_TEST = "tests/test_calc.py"
CONFTEST = "tests/conftest.py"
LOCKED_CHARTER = "# Charter\n\n- Locked: %s\n" % CALC_TEST

TAMPERING_ADD = (
    "import os\n"
    "_locked = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tests', 'test_calc.py')\n"
    "with open(_locked, 'a') as _handle:\n"
    "    _handle.write('# tampered after the referee check\\n')\n"
    "def add(a, b):\n"
    "    return a + b\n"
)

LITTERING_ADD = (
    "import os\n"
    "with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stray.txt'), 'w') as _handle:\n"
    "    _handle.write('left by a test run\\n')\n"
    "def add(a, b):\n"
    "    return a + b\n"
)

HIDING_ADD = (
    "import os\n"
    "with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tests', 'conftest.py'), 'w') as _handle:\n"
    "    _handle.write('collect_ignore = [\"test_calc.py\"]\\n')\n"
    "def add(a, b):\n"
    "    return a + b\n"
)

FIRST_RUN_TEST = (
    "import os\n"
    "import unittest\n"
    "\n"
    "import calc\n"
    "\n"
    "FLAG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ran.flag')\n"
    "\n"
    "\n"
    "class Calc(unittest.TestCase):\n"
    "    def adds(self):\n"
    "        first = not os.path.exists(FLAG)\n"
    "        open(FLAG, 'w').close()\n"
    "        self.assertTrue(first and callable(calc.add))\n"
)


class PostGateWrite(unittest.TestCase):
    def setUp(self):
        self.scratch = None

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()

    def _tree(self, path):
        return os.path.join(self.scratch.run_dir, "trees", "demo", path)

    def _read(self, path):
        with open(self._tree(path), encoding="utf-8", newline="") as handle:
            return handle.read()

    def _held_as_referee(self, completed, written):
        self.assertEqual(completed.returncode, 9, "mitosis exit code" + teeth.tail(completed))
        msp = teeth.summary_of(completed)["msps"][0]
        self.assertEqual(msp["state"], "referee-reverted")
        self.assertEqual(msp["reconcile"]["referee"], written)
        self.assertIs(msp["reconcile"]["fatal"], True)
        self.assertEqual(self.scratch.checked("ls-remote", "origin", "mitosis/demo"), "")
        self.assertFalse(os.path.exists(self.scratch.pr_log), "pr.log exists")
        self.assertEqual(
            self.scratch.checked("rev-parse", "mitosis/demo").strip(), msp["gate"]["commit"]
        )
        return msp

    def a_locked_path_written_during_the_gate_is_restored_and_fatal(self):
        self.scratch = teeth.Scratch({CALC_TEST: teeth.EXACT_TEST})
        items = self.scratch.items([teeth.add_step()])
        self.scratch.jobs(
            {"demo": {"step": "add", "writes": {"calc.py": TAMPERING_ADD}, "declared": ["calc.py"]}}
        )
        charter = self.scratch.file("charter.md", LOCKED_CHARTER)
        completed = self.scratch.build(items, "--charter", charter, push=True)
        self._held_as_referee(completed, [CALC_TEST])
        self.assertEqual(self.scratch.checked("show", "mitosis/demo:" + CALC_TEST), teeth.EXACT_TEST)
        self.assertEqual(self._read(CALC_TEST), teeth.EXACT_TEST)

    def an_ignored_referee_file_a_test_run_writes_is_reported_and_removed(self):
        self.scratch = teeth.Scratch(
            {CALC_TEST: teeth.EXACT_TEST, ".gitignore": "__pycache__/\n%s\n" % CONFTEST}
        )
        items = self.scratch.items([teeth.add_step()])
        self.scratch.jobs(
            {"demo": {"step": "add", "writes": {"calc.py": HIDING_ADD}, "declared": ["calc.py"]}}
        )
        completed = self.scratch.build(items, push=True)
        self._held_as_referee(completed, [CONFTEST])
        self.assertFalse(os.path.lexists(self._tree(CONFTEST)), "the ignored conftest is still on disk")

    def a_file_a_test_run_leaves_never_ships(self):
        self.scratch = teeth.Scratch()
        items = self.scratch.items([teeth.add_step()])
        self.scratch.jobs(
            {"demo": {"step": "add", "writes": {"calc.py": LITTERING_ADD, CALC_TEST: teeth.EXACT_TEST}}}
        )
        completed = self.scratch.build(items, push=True)
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + teeth.tail(completed))
        msp = teeth.summary_of(completed)["msps"][0]
        self.assertEqual(msp["state"], "shipped")
        self.assertEqual(msp["reconcile"]["referee"], [])
        pushed = self.scratch.checked("ls-remote", "origin", "mitosis/demo").split()[0]
        self.assertEqual(pushed, msp["gate"]["commit"])
        self.assertEqual(self.scratch.checked("rev-parse", "mitosis/demo").strip(), pushed)
        self.assertNotEqual(self.scratch.git("cat-file", "-e", pushed + ":stray.txt").returncode, 0)
        self.assertFalse(os.path.lexists(self._tree("stray.txt")), "stray.txt is still on disk")

    def a_file_one_gate_run_leaves_never_judges_a_later_run(self):
        self.scratch = teeth.Scratch()
        items = self.scratch.items([teeth.add_step()])
        self.scratch.jobs(
            {"demo": {"step": "add", "writes": {"calc.py": teeth.BROKEN_ADD, CALC_TEST: FIRST_RUN_TEST}}}
        )
        completed = self.scratch.build(items, push=True)
        self.assertEqual(completed.returncode, 24, "mitosis exit code" + teeth.tail(completed))
        msp = teeth.summary_of(completed)["msps"][0]
        self.assertEqual(msp["state"], "gate-failed")
        self.assertEqual(msp["gate"]["properties"][0]["outcome"], "weak")
        self.assertEqual(self.scratch.checked("ls-remote", "origin", "mitosis/demo"), "")
        self.assertFalse(os.path.lexists(self._tree("ran.flag")), "ran.flag is still on disk")


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            PostGateWrite,
            (
                "a_locked_path_written_during_the_gate_is_restored_and_fatal",
                "an_ignored_referee_file_a_test_run_writes_is_reported_and_removed",
                "a_file_a_test_run_leaves_never_ships",
                "a_file_one_gate_run_leaves_never_judges_a_later_run",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
