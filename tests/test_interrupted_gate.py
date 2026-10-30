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
site, route = %r, %r
during = (site == "mutant" and present and text != %r) or (site == "probe" and not present)
if during:
    os.kill(os.getppid(), getattr(signal, route))
    sys.exit(1)
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "accept_real.py")).read())
'''

FIRST_RUN_EXIT = {"SIGTERM": -signal.SIGTERM, "SIGKILL": -signal.SIGKILL}

PAIRS = tuple((site, route) for site in ("mutant", "probe") for route in ("SIGTERM", "SIGKILL"))


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

    def _interrupted(self, scratch, site, route):
        items = scratch.items([teeth.add_step()])
        scratch.jobs(
            {"demo": {"step": "add", "writes": {"calc.py": WORK, "tests/test_calc.py": teeth.EXACT_TEST}}}
        )
        teeth.put(os.path.join(scratch.stubs, "accept_real.py"), teeth.ACCEPT)
        teeth.put(os.path.join(scratch.stubs, "accept.py"), INTERRUPT % (site, route, WORK))
        first = scratch.build(items, push=True)
        self.assertEqual(first.returncode, FIRST_RUN_EXIT[route], "first run exit" + teeth.tail(first))
        left = os.path.join(self._left_checkout(scratch), "calc.py")
        if site == "mutant":
            with open(left, encoding="utf-8", newline="") as handle:
                self.assertNotEqual(handle.read(), WORK, "the first run left no mutant in its checkout")
        else:
            self.assertFalse(os.path.lexists(left), "the first run left the work in its checkout")
        with open(os.path.join(self._tree(scratch), "calc.py"), encoding="utf-8", newline="") as handle:
            self.assertEqual(handle.read(), WORK)
        self.assertEqual(self._in_tree(scratch, "status", "--porcelain"), "")
        teeth.put(os.path.join(scratch.stubs, "accept.py"), teeth.ACCEPT)
        return items

    def _pushed_calc(self, scratch):
        shown = scratch.git("--git-dir", scratch.remote, "show", "mitosis/demo:calc.py")
        return shown.stdout if shown.returncode == 0 else None

    def an_interrupted_gate_never_ships_its_mutation(self):
        for site, route in PAIRS:
            with self.subTest(site=site, route=route):
                scratch = teeth.Scratch()
                try:
                    items = self._interrupted(scratch, site, route)
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
            ("an_interrupted_gate_never_ships_its_mutation",),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
