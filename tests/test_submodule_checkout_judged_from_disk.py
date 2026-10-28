import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import run
from test_submodule_checkout import git_text, read_bytes
from test_uncommitted_work import BRANCH, CONFTEST, BaseSubmoduleWork, write_bytes

DEPENDENCY = {"dep.txt": "1\n", "helper.py": "X = 1\n"}
EDITED = b"X = 9\n"
MODULE = b"import builtins\n"
VENV = b"home = /usr\n"


def git_path(dep, name):
    found = git_text(["rev-parse", "--git-path", name], dep).strip()
    return found if os.path.isabs(found) else os.path.join(dep, found)


def put_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_bytes(path, data)


class SubmoduleCheckoutJudgedFromDisk(BaseSubmoduleWork):
    def _held_back(self, route):
        tree = self._committed_tree(DEPENDENCY)
        dep = os.path.join(tree, "dep")
        planted, payload = route(dep)
        held = self._referee_stage()
        self.assertEqual((held or {}).get("state"), "gate-failed")
        self.assertIsNone(held["gate"])
        self.assertEqual(read_bytes(os.path.join(dep, *planted.split("/"))), payload)
        with self.assertRaises(run.UncommittedWorkError) as raised:
            run.referee_writes({"locked": []}, tree, BRANCH, "feature")
        self.assertEqual(raised.exception.paths, ("dep/",))
        head = git_text(["rev-parse", BRANCH], tree).strip()
        with self.assertRaises(run.RestoreError) as restored:
            run.restore_gate_commit(tree, BRANCH, head, (), "feature", discard=True)
        self.assertEqual(restored.exception.left, ("dep/",))
        self.assertEqual(read_bytes(os.path.join(dep, *planted.split("/"))), payload)

    def _untracked_hidden_by_status_config(self, dep):
        write_bytes(os.path.join(dep, "conftest.py"), CONFTEST)
        git_text(["config", "status.showUntrackedFiles", "no"], dep)
        return "conftest.py", CONFTEST

    def _edit_hidden_by_assume_unchanged(self, dep):
        write_bytes(os.path.join(dep, "helper.py"), EDITED)
        git_text(["update-index", "--assume-unchanged", "helper.py"], dep)
        return "helper.py", EDITED

    def _edit_hidden_by_skip_worktree(self, dep):
        write_bytes(os.path.join(dep, "helper.py"), EDITED)
        git_text(["update-index", "--skip-worktree", "helper.py"], dep)
        return "helper.py", EDITED

    def _edit_hidden_by_clean_filter(self, dep):
        write_bytes(os.path.join(dep, "helper.py"), EDITED)
        put_bytes(git_path(dep, "info/attributes"), b"helper.py filter=f\n")
        git_text(["config", "filter.f.clean", "sed s/9/1/"], dep)
        return "helper.py", EDITED

    def _module_trusted_through_info_exclude(self, dep):
        write_bytes(os.path.join(dep, "sitecustomize.py"), MODULE)
        put_bytes(git_path(dep, "info/exclude"), b"sitecustomize.py\n")
        return "sitecustomize.py", MODULE

    def _module_trusted_through_worker_gitignore(self, dep):
        write_bytes(os.path.join(dep, "sitecustomize.py"), MODULE)
        write_bytes(os.path.join(dep, ".gitignore"), b"*\n")
        return "sitecustomize.py", MODULE

    def _referee_file_in_worker_venv_folder(self, dep):
        put_bytes(os.path.join(dep, "evil", "pyvenv.cfg"), VENV)
        put_bytes(os.path.join(dep, "evil", "conftest.py"), CONFTEST)
        put_bytes(git_path(dep, "info/exclude"), b"evil/\n")
        return "evil/conftest.py", CONFTEST

    def _status_redirected_by_core_worktree(self, dep):
        clean = os.path.join(self.tmp, "clean")
        git_text(["clone", "-q", os.path.join(self.tmp, "dependency"), clean], self.tmp)
        write_bytes(os.path.join(dep, "conftest.py"), CONFTEST)
        git_text(["config", "core.worktree", clean], dep)
        return "conftest.py", CONFTEST

    def an_untracked_file_hidden_by_the_status_config_holds_the_msp_back(self):
        self._held_back(self._untracked_hidden_by_status_config)

    def an_edit_hidden_by_assume_unchanged_holds_the_msp_back(self):
        self._held_back(self._edit_hidden_by_assume_unchanged)

    def an_edit_hidden_by_skip_worktree_holds_the_msp_back(self):
        self._held_back(self._edit_hidden_by_skip_worktree)

    def an_edit_hidden_by_a_clean_filter_holds_the_msp_back(self):
        self._held_back(self._edit_hidden_by_clean_filter)

    def a_module_hidden_by_info_exclude_holds_the_msp_back(self):
        self._held_back(self._module_trusted_through_info_exclude)

    def a_module_hidden_by_an_untracked_gitignore_holds_the_msp_back(self):
        self._held_back(self._module_trusted_through_worker_gitignore)

    def a_referee_file_in_a_worker_venv_folder_holds_the_msp_back(self):
        self._held_back(self._referee_file_in_worker_venv_folder)

    def a_file_hidden_by_a_redirected_core_worktree_holds_the_msp_back(self):
        self._held_back(self._status_redirected_by_core_worktree)


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            SubmoduleCheckoutJudgedFromDisk,
            (
                "an_untracked_file_hidden_by_the_status_config_holds_the_msp_back",
                "an_edit_hidden_by_assume_unchanged_holds_the_msp_back",
                "an_edit_hidden_by_skip_worktree_holds_the_msp_back",
                "an_edit_hidden_by_a_clean_filter_holds_the_msp_back",
                "a_module_hidden_by_info_exclude_holds_the_msp_back",
                "a_module_hidden_by_an_untracked_gitignore_holds_the_msp_back",
                "a_referee_file_in_a_worker_venv_folder_holds_the_msp_back",
                "a_file_hidden_by_a_redirected_core_worktree_holds_the_msp_back",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
