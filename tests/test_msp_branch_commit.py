import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import test_pinned_referee_state as pinned
import test_referee_lock as lock_fixtures

CORRECT_CALC = "def add(a, b):\n    return a + b\n"


class MspBranchCommit(pinned.PinnedRefereeRun):
    def _pushing_calc_run(self):
        scratch = self.scratch
        items = scratch.items(
            [lock_fixtures.step("build", ("calc.py",), "demo", acceptance=pinned.CALC_ACCEPTANCE)]
        )
        return scratch.mitosis(
            "--items",
            items,
            "--run-dir",
            scratch.run_dir,
            "--feature-branch",
            "feature",
            "--dispatch-command",
            scratch.stub("pinned_worker.py", "{msp}"),
            "--acceptance-command",
            scratch.stub("accept.py", "unittest", "{file}", "{test}"),
            "--timeout",
            "120",
            *lock_fixtures.PR_FLAGS,
            "--pr-command",
            scratch.stub("pr.py", "{branch}"),
            "--charter",
            scratch.file("charter.md", pinned.LOCKED_CALC_CHARTER),
        )

    def an_msp_branch_tamper_under_a_detached_head_never_reaches_the_branch(self):
        self._calc_scratch()
        self._worker(self._tamper_then(["git", "checkout", "-q", "--detach", "HEAD~1"]))
        completed = self._calc_run()
        self.assertEqual(completed.returncode, 21, "mitosis exit code" + lock_fixtures.tail(completed))
        msp = self._summary(completed)["msps"][0]
        self.assertEqual(msp["reconcile"]["referee"], [])
        self.assertEqual(msp["gate"]["properties"][0]["reason"], "exit 1 with the work present")
        self.assertEqual(
            self.scratch.checked("show", "refs/heads/%s:tests/test_calc.py" % pinned.BRANCH),
            pinned.LOCKED_CALC_TEST,
        )
        self.assertEqual(
            self.scratch.checked("show", "refs/heads/%s:calc.py" % pinned.BRANCH), pinned.BROKEN_CALC
        )
        self.assertEqual(
            self.scratch.checked("rev-parse", "refs/heads/feature").strip(), self.feature_before
        )

    def a_detached_head_in_the_clone_ships_as_without_it(self):
        scratch = self._calc_scratch()
        self._worker([["git", "checkout", "-q", "--detach"], ["write", "calc.py", CORRECT_CALC]])
        completed = self._pushing_calc_run()
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + lock_fixtures.tail(completed))
        msp = self._summary(completed)["msps"][0]
        self.assertEqual(msp["state"], "shipped")
        self.assertIs(msp["reconcile"]["fatal"], False)
        self.assertEqual(msp["gate"]["outcome"], "pass")
        self.assertEqual(
            scratch.checked("ls-remote", "origin", "refs/heads/" + pinned.BRANCH).split()[0],
            msp["gate"]["commit"],
        )


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    suite.addTests(
        MspBranchCommit(name)
        for name in (
            "an_msp_branch_tamper_under_a_detached_head_never_reaches_the_branch",
            "a_detached_head_in_the_clone_ships_as_without_it",
        )
    )
    return suite


if __name__ == "__main__":
    unittest.main()
