import glob
import os
import signal
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_gate_teeth as teeth

WORK = "LIMIT = 3\n\n\ndef add(a, b):\n    return a + b\n"

INTERRUPT = r'''
import os, signal, sys
calc = os.path.join(os.getcwd(), "calc.py")
present = os.path.exists(calc)
text = open(calc).read() if present else None
route = %r
if present and text == %r:
    os.kill(os.getppid(), getattr(signal, route))
    sys.exit(1)
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "accept_real.py")).read())
'''

FIRST_RUN_EXIT = {"SIGTERM": -signal.SIGTERM, "SIGKILL": -signal.SIGKILL}

ROUTES = ("SIGTERM", "SIGKILL")


class InterruptedGate(unittest.TestCase):
    def _tree(self, scratch):
        return os.path.join(scratch.run_dir, "trees", "demo")

    def _in_tree(self, scratch, *args):
        return subprocess.run(
            ["git", *args],
            cwd=self._tree(scratch),
            env=scratch.env,
            capture_output=True,
            text=True,
            timeout=teeth.CALL_LIMIT,
        ).stdout.strip()

    def _left_checkout(self, scratch):
        left = glob.glob(os.path.join(scratch.run_dir, "gate", "msp-0", "checkout-*"))
        self.assertEqual(len(left), 1, "the interrupted gate left %r" % left)
        return left[0]

    def _interrupted(self, scratch, route):
        items = scratch.items([teeth.add_step()])
        scratch.jobs(
            {"demo": {"step": "add", "writes": {"calc.py": WORK, "tests/test_calc.py": teeth.EXACT_TEST}}}
        )
        teeth.put(os.path.join(scratch.stubs, "accept_real.py"), teeth.ACCEPT)
        teeth.put(os.path.join(scratch.stubs, "accept.py"), INTERRUPT % (route, WORK))
        first = scratch.build(items, push=True)
        self.assertEqual(first.returncode, FIRST_RUN_EXIT[route], "first run exit" + teeth.tail(first))
        self._left_checkout(scratch)
        self.assertEqual(self._pushed_calc(scratch), None, "the interrupted run pushed its branch")
        with open(os.path.join(self._tree(scratch), "calc.py"), encoding="utf-8", newline="") as handle:
            self.assertEqual(handle.read(), WORK)
        self.assertEqual(self._in_tree(scratch, "status", "--porcelain"), "")
        teeth.put(os.path.join(scratch.stubs, "accept.py"), teeth.ACCEPT)
        return items

    def _pushed_calc(self, scratch):
        shown = scratch.git("--git-dir", scratch.remote, "show", "mitosis/demo:calc.py")
        return shown.stdout if shown.returncode == 0 else None

    def an_interrupted_gate_ships_the_work_on_resume_and_leaves_no_checkout(self):
        for route in ROUTES:
            with self.subTest(route=route):
                scratch = teeth.Scratch()
                try:
                    items = self._interrupted(scratch, route)
                    second = scratch.build(items, "--resume", push=True)
                    self.assertEqual(second.returncode, 0, "resume exit" + teeth.tail(second))
                    self.assertEqual(teeth.summary_of(second)["msps"][0]["state"], "shipped")
                    self.assertEqual(self._pushed_calc(scratch), WORK)
                    with open(scratch.pr_log, encoding="utf-8") as handle:
                        self.assertEqual(handle.read(), "mitosis/demo\n")
                    self.assertEqual(
                        glob.glob(os.path.join(scratch.run_dir, "gate", "msp-0", "checkout-*")), []
                    )
                finally:
                    scratch.close()


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            InterruptedGate,
            ("an_interrupted_gate_ships_the_work_on_resume_and_leaves_no_checkout",),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
