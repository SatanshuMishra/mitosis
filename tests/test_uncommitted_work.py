import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import run
import test_referee_lock as lock_fixtures
from test_submodule_checkout import git_text, read_bytes

BRANCH = "mitosis/demo"
HOOK = "#!/bin/sh\nexec git diff --cached --check\n"
CALC = b"LIMIT = 3\n"
SPACED = b"NEW = True \n"
NEW = b"NEW = True\n"
CONFTEST = b"import os\nos.environ['PLANTED'] = '1'\n"
PLAN = {
    "msps": [{"files": ["calc.py"], "steps": ["build"], "label": "demo"}],
    "items": [{"name": "build", "files": ["calc.py"], "acceptance": [], "msp": "demo"}],
    "locked": [],
}


def write_bytes(path, data):
    with open(path, "wb") as handle:
        handle.write(data)


class CommitFailure(unittest.TestCase):
    def setUp(self):
        self.scratch = lock_fixtures.Scratch({"calc.py": "LIMIT = 1\n"})
        self.addCleanup(self.scratch.close)

    def a_refused_pre_gate_commit_holds_the_msp_back_and_keeps_the_work(self):
        hook = os.path.join(self.scratch.repo, ".git", "hooks", "pre-commit")
        lock_fixtures.put(hook, HOOK)
        os.chmod(hook, 0o755)
        items = self.scratch.items([lock_fixtures.step("build", ["calc.py"], "demo")])
        self.scratch.jobs(
            {
                "demo": {
                    "step": "build",
                    "writes": {"calc.py": CALC.decode("utf-8"), "feature_new.py": SPACED.decode("utf-8")},
                    "declared": ["calc.py"],
                }
            }
        )
        done = self.scratch.build(items)
        msp = lock_fixtures.summary_of(done)["msps"][0]
        self.assertEqual(done.returncode, 21, lock_fixtures.tail(done))
        self.assertEqual(msp["state"], "gate-failed")
        self.assertIn("feature_new.py:1: trailing whitespace.", msp["gate"]["error"])
        kept = os.path.join(self.scratch.run_dir, "trees", "demo", "feature_new.py")
        self.assertEqual(read_bytes(kept), SPACED)


class ReconcileGuard(unittest.TestCase):
    def setUp(self):
        self.scratch = lock_fixtures.Scratch({"calc.py": "LIMIT = 1\n"})
        self.addCleanup(self.scratch.close)
        patcher = mock.patch.dict(os.environ, self.scratch.env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def reconcile_refuses_a_branch_head_that_lacks_the_work(self):
        repo = self.scratch.repo
        self.scratch.checked("-C", repo, "checkout", "-q", "-b", BRANCH, "feature")
        self.scratch.checked("-C", repo, "commit", "-q", "--allow-empty", "-m", "work")
        write_bytes(os.path.join(repo, "calc.py"), CALC)
        write_bytes(os.path.join(repo, "feature_new.py"), NEW)
        with self.assertRaises(run.GateTreeError):
            run.reconcile(PLAN, 0, repo, BRANCH, "feature")
        self.assertEqual(read_bytes(os.path.join(repo, "calc.py")), CALC)
        self.assertEqual(read_bytes(os.path.join(repo, "feature_new.py")), NEW)


class BaseSubmoduleWork(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mitosis-uncommitted-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        patcher = mock.patch.dict(os.environ, {**lock_fixtures.GIT_IDENTITY, "HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tree = self._committed_tree()
        self.handle = {"path": self.tree, "branch": BRANCH}

    def _committed_tree(self):
        dependency = os.path.join(self.tmp, "dependency")
        os.makedirs(dependency)
        git_text(["init", "-q", "-b", "main"], dependency)
        lock_fixtures.put(os.path.join(dependency, "dep.txt"), "1\n")
        git_text(["add", "-A"], dependency)
        git_text(["commit", "-q", "-m", "dependency"], dependency)
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        git_text(["init", "-q", "-b", "feature"], repo)
        lock_fixtures.put(os.path.join(repo, "calc.py"), "LIMIT = 1\n")
        lock_fixtures.put(os.path.join(repo, ".gitignore"), "__pycache__/\n")
        git_text(["add", "-A"], repo)
        git_text(["submodule", "add", "-q", dependency, "dep"], repo)
        git_text(["commit", "-q", "-m", "seed"], repo)
        tree = os.path.join(self.tmp, "tree")
        git_text(["worktree", "add", "-q", "-b", BRANCH, tree, "feature"], repo)
        git_text(["submodule", "update", "--init", "-q"], tree)
        write_bytes(os.path.join(tree, "calc.py"), CALC)
        git_text(["commit", "-q", "-am", "work"], tree)
        return tree

    def _referee_stage(self):
        return run._referee_stage(PLAN, 0, self.handle, (), [self.handle], {"feature_branch": "feature"})

    def a_worker_file_in_a_base_submodule_holds_the_msp_back(self):
        planted = os.path.join(self.tree, "dep", "conftest.py")
        write_bytes(planted, CONFTEST)
        held = self._referee_stage()
        self.assertEqual((held or {}).get("state"), "gate-failed")
        self.assertIsNone(held["gate"])
        self.assertEqual(read_bytes(planted), CONFTEST)

    def a_clean_base_submodule_checkout_is_not_held_back(self):
        self.assertIsNone(self._referee_stage())
        self.assertEqual(sorted(os.listdir(os.path.join(self.tree, "dep"))), [".git", "dep.txt"])


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (CommitFailure, ("a_refused_pre_gate_commit_holds_the_msp_back_and_keeps_the_work",)),
        (ReconcileGuard, ("reconcile_refuses_a_branch_head_that_lacks_the_work",)),
        (
            BaseSubmoduleWork,
            (
                "a_worker_file_in_a_base_submodule_holds_the_msp_back",
                "a_clean_base_submodule_checkout_is_not_held_back",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
