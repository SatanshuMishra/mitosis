import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import mitosis
import test_referee_lock as lock_fixtures
from test_submodule_checkout import git_text, read_bytes
from test_submodule_referee_write import SubmoduleRefereeWrite, commit_inside
from test_uncommitted_work import CONFTEST as PLANTED
from test_uncommitted_work import PLAN, write_bytes

CALC = b"LIMIT = 3\n"
EDITED = b"LIMIT = 4\n"
CONFTEST = "collect_ignore = ['x'] \n"
REFUSING_HOOK = "#!/bin/sh\nexec git diff --cached --check\n"
HANGING_HOOK = (
    "#!/bin/sh\nif git diff --cached --name-only | grep -q conftest; then exec tail -f /dev/null; fi\n"
)
LAUNCHER = """import os, runpy, sys
ROOT = %r
sys.path.insert(0, ROOT)
sys.argv = [os.path.join(ROOT, "mitosis.py")] + sys.argv[1:]
import run
original = run.git
def bounded(args, cwd, check=True, timeout=run.GIT_TIMEOUT, env=None):
    before_gate = args[:1] == ["commit"] and any("before the gate" in str(a) for a in args)
    return original(args, cwd, check=check, timeout=2 if before_gate else timeout, env=env)
run.git = bounded
runpy.run_path(os.path.join(ROOT, "mitosis.py"), run_name="__main__")
"""


class PreGateCommitFailure(unittest.TestCase):
    def setUp(self):
        self.scratch = lock_fixtures.Scratch({"calc.py": "LIMIT = 1\n"})
        self.addCleanup(self.scratch.close)

    def _build_with_hook(self, hook_text):
        hook = os.path.join(self.scratch.repo, ".git", "hooks", "pre-commit")
        lock_fixtures.put(hook, hook_text)
        os.chmod(hook, 0o755)
        items = self.scratch.items([lock_fixtures.step("build", ["calc.py"], "demo")])
        self.scratch.jobs(
            {
                "demo": {
                    "step": "build",
                    "writes": {"calc.py": CALC.decode("utf-8"), "tests/conftest.py": CONFTEST},
                    "declared": ["calc.py"],
                }
            }
        )
        return self.scratch.build(items)

    def _assert_referee_reverted(self, done):
        msp = lock_fixtures.summary_of(done)["msps"][0]
        tree = os.path.join(self.scratch.run_dir, "trees", "demo")
        self.assertEqual(done.returncode, 9, lock_fixtures.tail(done))
        self.assertEqual(msp["state"], "referee-reverted")
        self.assertEqual(msp["reconcile"]["referee"], ["tests/conftest.py"])
        self.assertIs(msp["reconcile"]["fatal"], True)
        self.assertFalse(os.path.lexists(os.path.join(tree, "tests", "conftest.py")))
        self.assertEqual(read_bytes(os.path.join(tree, "calc.py")), CALC)
        self.assertEqual(self.scratch.git("cat-file", "-e", "mitosis/demo:tests/conftest.py").returncode, 128)

    def a_refused_pre_gate_commit_still_restores_the_referee_write(self):
        self._assert_referee_reverted(self._build_with_hook(REFUSING_HOOK))

    def a_timed_out_pre_gate_commit_still_restores_the_referee_write(self):
        launcher = self.scratch.file("launcher.py", LAUNCHER % ROOT)
        with mock.patch.object(lock_fixtures, "MITOSIS", launcher):
            done = self._build_with_hook(HANGING_HOOK)
        self._assert_referee_reverted(done)


class UnjudgedSubmoduleWork(SubmoduleRefereeWrite):
    def _head(self):
        return git_text(["rev-parse", "HEAD"], self.tree).strip()

    def every_referee_write_is_listed_beside_main_tree_work_nobody_judged(self):
        self._tree()
        gated = self._head()
        write_bytes(os.path.join(self.dep, "conftest.py"), PLANTED)
        lock_fixtures.put(os.path.join(self.tree, "tests", "conftest.py"), CONFTEST)
        write_bytes(os.path.join(self.tree, "calc.py"), EDITED)
        held = self._referee_stage()
        self.assertEqual((held or {}).get("state"), "gate-failed")
        self.assertEqual(held["reconcile"]["referee"], ["dep/conftest.py", "tests/conftest.py"])
        self.assertIs(held["reconcile"]["fatal"], True)
        self.assertEqual(
            mitosis.exit_code({"worktrees": [self.handle], "msps": {"0": held}, "lanes": {}}, PLAN), 9
        )
        self.assertEqual(read_bytes(os.path.join(self.tree, "calc.py")), EDITED)
        self.assertEqual(self._head(), gated)

    def a_submodule_whose_commits_cannot_be_read_is_held_back(self):
        self._tree()
        write_bytes(os.path.join(self.dep, "conftest.py"), PLANTED)
        written = commit_inside(self.dep, "worker")
        os.remove(os.path.join(self.dep, "conftest.py"))
        root = git_text(["rev-parse", written + "^{tree}"], self.dep).strip()
        objects = git_text(["rev-parse", "--git-path", "objects"], self.dep).strip()
        loose = os.path.join(self.dep, objects, root[:2], root[2:])
        os.chmod(loose, 0o644)
        os.remove(loose)
        held = self._referee_stage()
        self.assertEqual((held or {}).get("state"), "gate-failed")
        self.assertIsNone(held["reconcile"])
        self.assertIn("dep/ cannot be judged", held["reason"])


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            PreGateCommitFailure,
            (
                "a_refused_pre_gate_commit_still_restores_the_referee_write",
                "a_timed_out_pre_gate_commit_still_restores_the_referee_write",
            ),
        ),
        (
            UnjudgedSubmoduleWork,
            (
                "every_referee_write_is_listed_beside_main_tree_work_nobody_judged",
                "a_submodule_whose_commits_cannot_be_read_is_held_back",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
