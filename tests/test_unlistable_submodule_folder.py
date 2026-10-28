import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from test_submodule_checkout import git_text, read_bytes
from test_uncommitted_work import CACHE, CONFTEST, BaseSubmoduleWork, write_bytes

LISTABLE = 0o755
UNLISTABLE = 0o000


class UnlistableSubmoduleFolder(BaseSubmoduleWork):
    def setUp(self):
        if os.geteuid() == 0:
            self.skipTest("root lists a folder whatever its mode")
        BaseSubmoduleWork.setUp(self)

    def _held_with_unlistable(self, folder, planted, beside=None, repository=None):
        tree = self._committed_tree({"dep.txt": "1\n", ".gitignore": "build/\n"})
        hidden = os.path.join(tree, "dep", *folder)
        conftest = os.path.join(hidden, planted)
        os.makedirs(hidden)
        write_bytes(conftest, CONFTEST)
        if beside is not None:
            write_bytes(os.path.join(tree, "dep", *beside), CACHE)
        if repository is not None:
            git_text(["init", "-q"], os.path.join(tree, "dep", *repository))
        os.chmod(hidden, UNLISTABLE)
        try:
            held = self._referee_stage()
        finally:
            os.chmod(hidden, LISTABLE)
        return held, conftest

    def an_unlistable_ignored_folder_in_a_base_submodule_holds_the_msp_back(self):
        held, conftest = self._held_with_unlistable(("build",), "conftest.py")
        self.assertEqual((held or {}).get("state"), "gate-failed")
        self.assertIsNone(held["gate"])
        self.assertEqual(read_bytes(conftest), CONFTEST)

    def an_unlistable_folder_inside_an_ignored_one_holds_the_msp_back(self):
        held, conftest = self._held_with_unlistable(
            ("build", "sub"), "conftest.py", beside=("build", "out.o")
        )
        self.assertEqual((held or {}).get("state"), "gate-failed")
        self.assertIsNone(held["gate"])
        self.assertEqual(read_bytes(conftest), CONFTEST)

    def an_unlistable_folder_in_an_ignored_repository_holds_the_msp_back(self):
        held, conftest = self._held_with_unlistable(
            ("build", "sub"), "conftest.py", repository=("build",)
        )
        self.assertEqual((held or {}).get("state"), "gate-failed")
        self.assertIsNone(held["gate"])
        self.assertEqual(read_bytes(conftest), CONFTEST)


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            UnlistableSubmoduleFolder,
            (
                "an_unlistable_ignored_folder_in_a_base_submodule_holds_the_msp_back",
                "an_unlistable_folder_inside_an_ignored_one_holds_the_msp_back",
                "an_unlistable_folder_in_an_ignored_repository_holds_the_msp_back",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
