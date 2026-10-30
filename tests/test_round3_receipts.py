import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import run
from test_mutant_coverage import ADDS, CORRECT_ADD, TEST_FILE, Scratch, step, summary_of, tail
from test_round1_receipts import EXACT_TEST

EXIT_REFUSED = 3
REFUSED_LINE = "exit 3: refused to start"
SPELLINGS = ("absolute", "symlink", "relative", "trailing slash", "case-folded")


def refusal(given, run_dir):
    return (
        "--worker-write %s overlaps the run folder %s; sandboxed code may never write "
        "mitosis's own state, logs or gate folders, so no Worker was started" % (given, run_dir)
    )


def case_insensitive(folder):
    return os.path.exists(folder.swapcase())


class Receipts(unittest.TestCase):
    def setUp(self):
        self.scratch = Scratch({TEST_FILE: EXACT_TEST})
        self.addCleanup(self.scratch.close)
        self.items = self.scratch.items([step("add", ["calc.py"], "demo", acceptance=[ADDS])])
        self.scratch.jobs({"add": {"writes": {"calc.py": CORRECT_ADD}}})

    def _overlapping(self):
        run_dir = self.scratch.run_dir
        return (
            ("equal", run_dir, ""),
            ("ancestor", os.path.dirname(run_dir), ""),
            ("home ancestor", self.scratch.tmp, ""),
            ("inside", run_dir, "gate"),
        )

    def _spelled(self, spelling, target, below, cwd, label):
        if spelling == "symlink":
            link = os.path.join(self.scratch.tmp, "link-%s" % label.replace(" ", "-"))
            if not os.path.lexists(link):
                os.symlink(target, link)
            return os.path.join(link, below) if below else link
        joined = os.path.join(target, below) if below else target
        if spelling == "relative":
            return os.path.relpath(joined, cwd)
        if spelling == "trailing slash":
            return joined + os.sep
        if spelling == "case-folded":
            return joined.swapcase()
        return joined

    def _cases(self, cwd):
        for label, target, below in self._overlapping():
            for spelling in SPELLINGS:
                if spelling == "case-folded" and not case_insensitive(self.scratch.tmp):
                    continue
                yield label, spelling, self._spelled(spelling, target, below, cwd, label)

    def _assert_refused(self, completed, given):
        self.assertEqual(completed.returncode, EXIT_REFUSED, "mitosis exit code" + tail(completed))
        self.assertEqual(
            completed.stderr.splitlines(),
            ["mitosis: " + refusal(given, self.scratch.run_dir), REFUSED_LINE],
            tail(completed),
        )
        self.assertEqual(self.scratch.dispatched(), [], "a Worker started" + tail(completed))
        self.assertFalse(os.path.exists(os.path.join(self.scratch.run_dir, run.STATE_FILE)))

    def _build(self, given, *extra):
        return self.scratch.build(self.items, "--worker-sandbox", "--worker-write", given, *extra)

    def _plan_only(self):
        completed = self.scratch.build(self.items, "--plan-only")
        self.assertEqual(completed.returncode, 0, "plan-only exit code" + tail(completed))

    def a_fresh_run_refuses_a_worker_write_that_overlaps_the_run_folder(self):
        for label, spelling, given in self._cases(self.scratch.repo):
            with self.subTest(relation=label, spelling=spelling):
                self._assert_refused(self._build(given), given)

    def a_resumed_run_refuses_a_worker_write_that_overlaps_the_run_folder(self):
        self._plan_only()
        for label, spelling, given in self._cases(self.scratch.repo):
            with self.subTest(relation=label, spelling=spelling):
                self._assert_refused(self._build(given, "--resume"), given)

    def the_resumed_plan_refuses_a_stored_worker_write_that_overlaps_the_run_folder(self):
        self._plan_only()
        for label, spelling, given in self._cases(os.getcwd()):
            with self.subTest(relation=label, spelling=spelling):
                with self.assertRaises(run.OverlappingWrite) as raised:
                    run.execute(
                        {"lanes": [], "msps": []},
                        self.scratch.repo,
                        "feature",
                        self.scratch.run_dir,
                        self.scratch.stub("worker.py", "{msp}", "{task}"),
                        self.scratch.stub("accept.py", "unittest", "{file}", "{test}"),
                        None,
                        120,
                        1,
                        resume=True,
                        no_push=True,
                        env=self.scratch.env,
                        worker_sandbox=True,
                        worker_writes=(given,),
                    )
                self.assertIsInstance(raised.exception, run.ConfigError)
                self.assertEqual(str(raised.exception), refusal(given, self.scratch.run_dir))
                self.assertEqual(self.scratch.dispatched(), [])

    def _disjoint(self):
        sibling = self.scratch.run_dir + "-sibling"
        os.makedirs(sibling)
        return ("--worker-write", sibling, "--worker-write", self.scratch.stubs + os.sep)

    def _sandbox_flags(self):
        return ("--worker-sandbox",) if sys.platform == "darwin" else ()

    def _assert_built(self, completed):
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        self.assertEqual(self.scratch.dispatched(), ["add"], tail(completed))
        expected = "sandbox-exec" if sys.platform == "darwin" else "off"
        self.assertEqual(summary_of(completed).get("sandbox"), expected, tail(completed))

    def a_fresh_run_accepts_a_disjoint_worker_write(self):
        completed = self.scratch.build(self.items, *(self._sandbox_flags() + self._disjoint()))
        self._assert_built(completed)

    def a_resumed_run_accepts_a_disjoint_worker_write(self):
        self._plan_only()
        completed = self.scratch.build(
            self.items, "--resume", *(self._sandbox_flags() + self._disjoint())
        )
        self._assert_built(completed)


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            Receipts,
            (
                "a_fresh_run_refuses_a_worker_write_that_overlaps_the_run_folder",
                "a_resumed_run_refuses_a_worker_write_that_overlaps_the_run_folder",
                "the_resumed_plan_refuses_a_stored_worker_write_that_overlaps_the_run_folder",
                "a_fresh_run_accepts_a_disjoint_worker_write",
                "a_resumed_run_accepts_a_disjoint_worker_write",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
