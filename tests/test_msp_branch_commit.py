import json
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
            "--render-command",
            scratch.render,
            "--charter",
            scratch.file("charter.md", pinned.LOCKED_CALC_CHARTER),
        )

    def an_msp_branch_tamper_under_a_detached_head_is_reverted_and_fatal(self):
        self._calc_scratch()
        self._worker(self._tamper_then(["git", "checkout", "-q", "--detach", "feature"]))
        self._assert_locked_test_reverted(self._calc_run())

    def a_worktree_head_off_the_msp_commit_is_fatal(self):
        scratch = self._calc_scratch()
        self._worker([["git", "checkout", "-q", "--detach"], ["write", "calc.py", CORRECT_CALC]])
        completed = self._pushing_calc_run()
        self.assertEqual(completed.returncode, 9, "mitosis exit code" + lock_fixtures.tail(completed))
        msp = self._summary(completed)["msps"][0]
        self.assertEqual(msp["state"], "referee-reverted")
        self.assertIs(msp["reconcile"]["fatal"], True)
        self.assertEqual(
            (msp["gate"]["outcome"], msp["gate"]["commit"], msp["gate"]["properties"]), (None, None, [])
        )
        with open(os.path.join(scratch.run_dir, "state.json"), encoding="utf-8") as handle:
            self.assertIsNone(json.load(handle)["msps"]["0"].get("gate"))
        self.assertEqual(scratch.checked("ls-remote", "origin", "refs/heads/" + pinned.BRANCH), "")
        self.assertFalse(os.path.exists(scratch.pr_log), "a pull-request command ran")


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    suite.addTests(
        MspBranchCommit(name)
        for name in (
            "an_msp_branch_tamper_under_a_detached_head_is_reverted_and_fatal",
            "a_worktree_head_off_the_msp_commit_is_fatal",
        )
    )
    return suite


if __name__ == "__main__":
    unittest.main()
