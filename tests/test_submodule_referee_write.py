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
CALC = b"LIMIT = 3\n"
CONFTEST = b"import os\nos.environ['PLANTED'] = '1'\n"
PLAN = {
    "msps": [{"files": ["calc.py"], "steps": ["build"], "label": "demo"}],
    "items": [{"name": "build", "files": ["calc.py"], "acceptance": [], "msp": "demo"}],
    "locked": [],
}
PYTEST_INI = b"[pytest]\n"
BUMPED = b"2\n"


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


def write_bytes(path, data):
    with open(path, "wb") as handle:
        handle.write(data)


def put_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_bytes(path, data)


def committed_repository(folder, files):
    os.makedirs(folder)
    git_text(["init", "-q", "-b", "main"], folder)
    for name, data in sorted(files.items()):
        put_bytes(os.path.join(folder, name), data)
    git_text(["add", "-A"], folder)
    git_text(["commit", "-q", "-m", "seed"], folder)
    return folder


def commit_inside(folder, message):
    git_text(["add", "-A"], folder)
    git_text(["commit", "-q", "-m", message], folder)
    return git_text(["rev-parse", "HEAD"], folder).strip()


class SubmoduleRefereeWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mitosis-uncommitted-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        patcher = mock.patch.dict(os.environ, {**lock_fixtures.GIT_IDENTITY, "HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.handle = None

    def _referee_stage(self):
        return run._referee_stage(PLAN, 0, self.handle, (), [self.handle], {"feature_branch": "feature"})

    def _tree(self, nested=False):
        dependency = committed_repository(
            os.path.join(self.tmp, "dependency"), {"dep.txt": b"1\n", "pytest.ini": PYTEST_INI}
        )
        if nested:
            inner = committed_repository(os.path.join(self.tmp, "inner"), {"i.txt": b"1\n"})
            git_text(["submodule", "add", "-q", inner, "lib"], dependency)
            git_text(["commit", "-q", "-m", "nest"], dependency)
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
        git_text(["submodule", "update", "--init", "--recursive", "-q"], tree)
        write_bytes(os.path.join(tree, "calc.py"), CALC)
        git_text(["commit", "-q", "-am", "work"], tree)
        self.tree = tree
        self.handle = {"path": tree, "branch": BRANCH}
        self.dep = os.path.join(tree, "dep")
        return tree

    def _gated(self):
        run.commit_all(self.tree, "pre-gate")
        return self._referee_stage()

    def _shipped_gitlink(self):
        return git_text(["ls-tree", "HEAD", "dep"], self.tree).split()[2]

    def _assert_reverted(self, held, written):
        self.assertEqual((held or {}).get("state"), "referee-reverted")
        self.assertEqual(held["reconcile"]["referee"], written)
        self.assertTrue(held["reconcile"]["fatal"])
        self.assertEqual(
            self._shipped_gitlink(), git_text(["rev-parse", "feature:dep"], self.tree).strip()
        )

    def a_conftest_committed_inside_a_base_submodule_is_reverted(self):
        self._tree()
        write_bytes(os.path.join(self.dep, "conftest.py"), CONFTEST)
        commit_inside(self.dep, "worker")
        self._assert_reverted(self._gated(), ["dep/conftest.py"])
        self.assertFalse(os.path.lexists(os.path.join(self.dep, "conftest.py")))

    def a_conftest_committed_in_a_nested_submodule_is_reverted(self):
        self._tree(nested=True)
        lib = os.path.join(self.dep, "lib")
        write_bytes(os.path.join(lib, "conftest.py"), CONFTEST)
        commit_inside(lib, "worker")
        commit_inside(self.dep, "bump lib")
        self._assert_reverted(self._gated(), ["dep/lib/conftest.py"])
        self.assertFalse(os.path.lexists(os.path.join(lib, "conftest.py")))

    def a_clean_base_submodule_passes_the_referee_stage(self):
        self._tree()
        self.assertIsNone(self._gated())
        self.assertEqual(
            self._shipped_gitlink(), git_text(["rev-parse", "feature:dep"], self.tree).strip()
        )

    def a_submodule_bump_that_touches_no_referee_path_passes(self):
        self._tree()
        write_bytes(os.path.join(self.dep, "dep.txt"), BUMPED)
        bumped = commit_inside(self.dep, "bump")
        self.assertIsNone(self._gated())
        self.assertEqual(self._shipped_gitlink(), bumped)
        self.assertEqual(read_bytes(os.path.join(self.dep, "dep.txt")), BUMPED)


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            SubmoduleRefereeWrite,
            (
                "a_conftest_committed_inside_a_base_submodule_is_reverted",
                "a_conftest_committed_in_a_nested_submodule_is_reverted",
                "a_clean_base_submodule_passes_the_referee_stage",
                "a_submodule_bump_that_touches_no_referee_path_passes",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
