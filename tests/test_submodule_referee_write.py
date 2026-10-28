import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import run
import test_referee_lock as lock_fixtures
from test_submodule_checkout import git_text, read_bytes
from test_uncommitted_work import BRANCH, CALC, CONFTEST, BaseSubmoduleWork, write_bytes

PYTEST_INI = b"[pytest]\n"
HIDDEN_INI = b"[pytest]\naddopts = -k nothing\n"
VENV = b"home = /usr\n"
BUMPED = b"2\n"


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


def git_path(tree, name):
    found = git_text(["rev-parse", "--git-path", name], tree).strip()
    return found if os.path.isabs(found) else os.path.join(tree, found)


def commit_inside(folder, message):
    git_text(["add", "-A"], folder)
    git_text(["commit", "-q", "-m", message], folder)
    return git_text(["rev-parse", "HEAD"], folder).strip()


class SubmoduleRefereeWrite(BaseSubmoduleWork):
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

    def a_conftest_hidden_by_the_submodule_status_config_is_reverted(self):
        self._tree()
        git_text(["config", "status.showUntrackedFiles", "no"], self.dep)
        write_bytes(os.path.join(self.dep, "conftest.py"), CONFTEST)
        self._assert_reverted(self._gated(), ["dep/conftest.py"])
        self.assertFalse(os.path.lexists(os.path.join(self.dep, "conftest.py")))

    def a_skip_worktree_edit_of_a_base_referee_file_is_restored(self):
        self._tree()
        write_bytes(os.path.join(self.dep, "pytest.ini"), HIDDEN_INI)
        git_text(["update-index", "--skip-worktree", "pytest.ini"], self.dep)
        self._assert_reverted(self._gated(), ["dep/pytest.ini"])
        self.assertEqual(read_bytes(os.path.join(self.dep, "pytest.ini")), PYTEST_INI)

    def a_conftest_committed_in_a_nested_submodule_is_reverted(self):
        self._tree(nested=True)
        lib = os.path.join(self.dep, "lib")
        write_bytes(os.path.join(lib, "conftest.py"), CONFTEST)
        commit_inside(lib, "worker")
        commit_inside(self.dep, "bump lib")
        self._assert_reverted(self._gated(), ["dep/lib/conftest.py"])
        self.assertFalse(os.path.lexists(os.path.join(lib, "conftest.py")))

    def a_worker_committed_ignore_never_trusts_a_referee_file(self):
        self._tree()
        write_bytes(os.path.join(self.dep, ".gitignore"), b"evil/\n")
        put_bytes(os.path.join(self.dep, "evil", "pyvenv.cfg"), VENV)
        put_bytes(os.path.join(self.dep, "evil", "conftest.py"), CONFTEST)
        commit_inside(self.dep, "worker ignore")
        self._assert_reverted(self._gated(), ["dep/evil/conftest.py"])
        self.assertFalse(os.path.lexists(os.path.join(self.dep, "evil", "conftest.py")))

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

    def _named_module(self, name):
        tree = self.tree
        dependency = os.path.join(self.tmp, "dependency")
        lock_fixtures.put(
            os.path.join(tree, ".gitmodules"),
            '[submodule "%s"]\n\tpath = dep\n\turl = %s\n' % (name, dependency),
        )
        git_text(["commit", "-q", "--allow-empty", "-am", "crafted"], tree)
        return git_text(["rev-parse", "HEAD:dep"], tree).strip()

    def a_module_name_that_climbs_out_of_the_modules_folder_is_not_judged(self):
        self._tree()
        modules = git_path(self.tree, "modules")
        climbing = os.path.relpath(os.path.join(self.tmp, "dependency", ".git"), modules)
        gitlink = self._named_module(climbing)
        self.assertEqual(run.submodule_work(self.tree, "dep", gitlink, "HEAD"), ("dep/",))

    def a_module_folder_linked_outside_the_modules_folder_is_not_judged(self):
        self._tree()
        gitlink = self._named_module("dep")
        module = git_path(self.tree, "modules/dep")
        moved = os.path.join(self.tmp, "moved")
        os.rename(module, moved)
        os.symlink(moved, module)
        git_text(["config", "--file", os.path.join(moved, "config"), "core.worktree", self.dep], self.tmp)
        self.assertEqual(run.submodule_work(self.tree, "dep", gitlink, "HEAD"), ("dep/",))


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            SubmoduleRefereeWrite,
            (
                "a_conftest_committed_inside_a_base_submodule_is_reverted",
                "a_conftest_hidden_by_the_submodule_status_config_is_reverted",
                "a_skip_worktree_edit_of_a_base_referee_file_is_restored",
                "a_conftest_committed_in_a_nested_submodule_is_reverted",
                "a_worker_committed_ignore_never_trusts_a_referee_file",
                "a_clean_base_submodule_passes_the_referee_stage",
                "a_submodule_bump_that_touches_no_referee_path_passes",
                "a_module_name_that_climbs_out_of_the_modules_folder_is_not_judged",
                "a_module_folder_linked_outside_the_modules_folder_is_not_judged",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
