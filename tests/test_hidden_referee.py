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

import core
import run
import test_referee_lock as lock_fixtures

LOCKED = "tests/test_calc.py"
ORIGINAL = "import unittest\n"
TAMPERED = "raise SystemExit(0)\n"
CONFTEST = "tests/conftest.py"
PLAN = {"locked": [LOCKED]}


def git_text(args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, timeout=60
    ).stdout.decode("utf-8")


def common_dir(tree):
    found = git_text(["rev-parse", "--git-common-dir"], tree).strip()
    return found if os.path.isabs(found) else os.path.join(tree, found)


def via_gitignore(tree):
    lock_fixtures.put(os.path.join(tree, ".gitignore"), "__pycache__/\n%s\n" % CONFTEST)
    lock_fixtures.put(os.path.join(tree, CONFTEST), "x = 1\n")


def via_info_exclude(tree):
    lock_fixtures.put(os.path.join(common_dir(tree), "info", "exclude"), "conftest.py\n")
    lock_fixtures.put(os.path.join(tree, CONFTEST), "x = 1\n")


def via_excludes_file(tree):
    excludes = lock_fixtures.put(os.path.join(os.path.dirname(tree), "excludes"), "conftest.py\n")
    git_text(["config", "core.excludesFile", excludes], tree)
    lock_fixtures.put(os.path.join(tree, CONFTEST), "x = 1\n")


def via_skip_worktree(tree):
    git_text(["update-index", "--skip-worktree", LOCKED], tree)
    lock_fixtures.put(os.path.join(tree, LOCKED), TAMPERED)


def via_assume_unchanged(tree):
    git_text(["update-index", "--assume-unchanged", LOCKED], tree)
    lock_fixtures.put(os.path.join(tree, LOCKED), TAMPERED)


def via_nested_repository(tree):
    nested = os.path.join(tree, "tests", "sub")
    lock_fixtures.put(os.path.join(nested, "conftest.py"), "x = 1\n")
    git_text(["init", "-q"], nested)
    git_text(["add", "-A"], nested)
    git_text(["commit", "-q", "-m", "nested"], nested)


def via_bytecode_cache(tree):
    lock_fixtures.put(os.path.join(tree, "tests", "__pycache__", "test_calc.cpython-39.pyc"), "poisoned\n")


def referee_residue(tree):
    found = ()
    for folder, dirs, files in os.walk(tree):
        dirs[:] = sorted(name for name in dirs if name != ".git")
        for name in sorted(files):
            full = os.path.join(folder, name)
            relative = os.path.relpath(full, tree).replace(os.sep, "/")
            if relative == LOCKED:
                with open(full, encoding="utf-8") as handle:
                    if handle.read() != ORIGINAL:
                        found = found + (relative,)
            elif name == "conftest.py" or os.path.basename(folder) == "__pycache__":
                found = found + (relative,)
    return found


class HiddenReferee(unittest.TestCase):
    def setUp(self):
        self.scratch = None
        self.tmp = tempfile.mkdtemp(prefix="mitosis-hidden-")
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        patcher = mock.patch.dict(os.environ, {**lock_fixtures.GIT_IDENTITY, "HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tree(self, name):
        repo = os.path.join(self.tmp, name, "repo")
        os.makedirs(repo)
        git_text(["init", "-q", "-b", "feature"], repo)
        lock_fixtures.put(os.path.join(repo, LOCKED), ORIGINAL)
        lock_fixtures.put(os.path.join(repo, ".gitignore"), "__pycache__/\n")
        git_text(["add", "-A"], repo)
        git_text(["commit", "-q", "-m", "seed"], repo)
        tree = os.path.join(self.tmp, name, "tree")
        git_text(["worktree", "add", "-q", "-b", "mitosis/demo", tree, "feature"], repo)
        return tree

    def _route(self, name, hide, expected):
        tree = self._tree(name)
        hide(tree)
        run.commit_all(tree, "pre-gate")
        reported = run.referee_writes(PLAN, tree, "mitosis/demo", "feature")
        self.assertEqual(reported, expected)
        run.revert_implementation(tree, "feature", reported)
        self.assertEqual(referee_residue(tree), ())
        with open(os.path.join(tree, LOCKED), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), ORIGINAL)
        self.assertFalse(os.path.exists(os.path.join(tree, "tests", "__pycache__")))
        run.commit_all(tree, "restore")
        self.assertEqual(run.referee_writes(PLAN, tree, "mitosis/demo", "feature"), [])

    def a_gitignored_conftest_is_reported_and_removed(self):
        self._route("gitignore", via_gitignore, [CONFTEST])

    def an_info_excluded_conftest_is_reported_and_removed(self):
        self._route("info-exclude", via_info_exclude, [CONFTEST])

    def a_conftest_hidden_by_core_excludes_file_is_reported_and_removed(self):
        self._route("excludes-file", via_excludes_file, [CONFTEST])

    def a_skip_worktree_locked_edit_is_reported_and_restored(self):
        self._route("skip-worktree", via_skip_worktree, [LOCKED])

    def an_assume_unchanged_locked_edit_is_reported_and_restored(self):
        self._route("assume-unchanged", via_assume_unchanged, [LOCKED])

    def a_conftest_in_a_nested_repository_holds_the_msp_back(self):
        tree = self._tree("nested")
        via_nested_repository(tree)
        run.commit_all(tree, "pre-gate")
        with self.assertRaises(run.UncommittedWorkError):
            run.referee_writes(PLAN, tree, "mitosis/demo", "feature")
        with open(os.path.join(tree, "tests", "sub", "conftest.py"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "x = 1\n")
        self.assertTrue(os.path.isdir(os.path.join(tree, "tests", "sub", ".git")))

    def a_bytecode_cache_beside_a_locked_test_is_removed_silently(self):
        self._route("bytecode", via_bytecode_cache, [])

    def a_failed_referee_restore_is_a_named_gate_error(self):
        tree = self._tree("restore-fails")
        lock_fixtures.put(os.path.join(tree, CONFTEST), "x = 1\n")
        run.commit_all(tree, "pre-gate")
        plan = {
            **core.plan([lock_fixtures.step("build", ["calc.py"], "demo")]),
            "locked": [LOCKED],
        }
        failure = run.GitError(["checkout", "-q", "feature", "--", CONFTEST], 1, "refused\n")
        with mock.patch.object(run, "revert_implementation", side_effect=failure):
            held = run._referee_stage(
                plan,
                0,
                {"path": tree, "branch": "mitosis/demo"},
                (),
                [{"path": tree, "branch": "mitosis/demo"}],
                {"feature_branch": "feature"},
            )
        self.assertEqual(held["state"], "gate-failed")
        self.assertIsNone(held["gate"])
        self.assertEqual(
            held["gate_error"],
            "the referee restore on mitosis/demo failed, so the gate did not run: %s" % failure,
        )
        self.assertEqual(held["reconcile"]["referee"], [CONFTEST])
        self.assertIs(held["reconcile"]["fatal"], True)

    def a_worker_hiding_a_conftest_with_gitignore_is_reverted_and_fatal(self):
        self.scratch = lock_fixtures.Scratch()
        charter = self.scratch.file("charter.md", lock_fixtures.PLAIN_CHARTER)
        items = self.scratch.items([lock_fixtures.step("build", ["calc.py"], "demo")])
        self.scratch.jobs(
            {
                "demo": {
                    "step": "build",
                    "writes": {
                        "calc.py": "def add(a, b):\n    return a + b\n",
                        CONFTEST: 'collect_ignore = ["test_calc.py"]\n',
                        ".gitignore": "__pycache__/\n%s\n" % CONFTEST,
                    },
                    "declared": ["calc.py"],
                }
            }
        )
        completed = self.scratch.build(items, "--charter", charter)
        self.assertEqual(completed.returncode, 9, "mitosis exit code" + lock_fixtures.tail(completed))
        msp = lock_fixtures.summary_of(completed)["msps"][0]
        self.assertEqual(msp["state"], "referee-reverted")
        self.assertEqual(msp["reconcile"]["referee"], [CONFTEST])
        self.assertIs(msp["reconcile"]["fatal"], True)
        self.assertFalse(
            os.path.lexists(os.path.join(self.scratch.run_dir, "trees", "demo", CONFTEST))
        )


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            HiddenReferee,
            (
                "a_gitignored_conftest_is_reported_and_removed",
                "an_info_excluded_conftest_is_reported_and_removed",
                "a_conftest_hidden_by_core_excludes_file_is_reported_and_removed",
                "a_skip_worktree_locked_edit_is_reported_and_restored",
                "an_assume_unchanged_locked_edit_is_reported_and_restored",
                "a_conftest_in_a_nested_repository_holds_the_msp_back",
                "a_bytecode_cache_beside_a_locked_test_is_removed_silently",
                "a_failed_referee_restore_is_a_named_gate_error",
                "a_worker_hiding_a_conftest_with_gitignore_is_reverted_and_fatal",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
