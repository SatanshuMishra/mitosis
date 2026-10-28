import os
import shutil
import subprocess
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

BRANCH = "mitosis/demo"
LOCKED = "tests/test_calc.py"
PLAN = {"locked": [LOCKED]}
CHECKOUT = [".git", "dep.txt", "new.txt"]
EDITED = b"edited\n"
UNTRACKED = b"untracked\n"


def git_text(args, cwd):
    return subprocess.run(
        ["git", "-c", "protocol.file.allow=always", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout.decode("utf-8")


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


class SubmoduleCheckout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mitosis-submodule-")
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        patcher = mock.patch.dict(os.environ, {**lock_fixtures.GIT_IDENTITY, "HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tree = self._dirty_submodule_tree()
        self.dep = os.path.join(self.tree, "dep")

    def _dirty_submodule_tree(self):
        dependency = os.path.join(self.tmp, "dependency")
        os.makedirs(dependency)
        git_text(["init", "-q", "-b", "main"], dependency)
        lock_fixtures.put(os.path.join(dependency, "dep.txt"), "1\n")
        git_text(["add", "-A"], dependency)
        git_text(["commit", "-q", "-m", "dependency"], dependency)
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        git_text(["init", "-q", "-b", "feature"], repo)
        lock_fixtures.put(os.path.join(repo, LOCKED), "import unittest\n")
        lock_fixtures.put(os.path.join(repo, ".gitignore"), "__pycache__/\n")
        git_text(["add", "-A"], repo)
        git_text(["submodule", "add", "-q", dependency, "dep"], repo)
        git_text(["commit", "-q", "-m", "seed"], repo)
        tree = os.path.join(self.tmp, "tree")
        git_text(["worktree", "add", "-q", "-b", BRANCH, tree, "feature"], repo)
        git_text(["submodule", "update", "--init", "-q"], tree)
        lock_fixtures.put(os.path.join(tree, "dep", "dep.txt"), EDITED.decode("utf-8"))
        lock_fixtures.put(os.path.join(tree, "dep", "new.txt"), UNTRACKED.decode("utf-8"))
        return tree

    def _assert_checkout_kept(self):
        self.assertEqual(sorted(os.listdir(self.dep)), CHECKOUT)
        self.assertEqual(read_bytes(os.path.join(self.dep, "dep.txt")), EDITED)
        self.assertEqual(read_bytes(os.path.join(self.dep, "new.txt")), UNTRACKED)

    def the_referee_check_keeps_a_base_submodule_checkout(self):
        self.assertEqual(run.referee_writes(PLAN, self.tree, BRANCH, "feature"), [])
        self._assert_checkout_kept()

    def a_restore_over_a_dirty_base_submodule_refuses_and_keeps_it(self):
        head = git_text(["rev-parse", BRANCH], self.tree).strip()
        with self.assertRaises(run.RestoreError):
            run.restore_gate_commit(self.tree, BRANCH, head, [LOCKED], "feature")
        self._assert_checkout_kept()


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            SubmoduleCheckout,
            (
                "the_referee_check_keeps_a_base_submodule_checkout",
                "a_restore_over_a_dirty_base_submodule_refuses_and_keeps_it",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
