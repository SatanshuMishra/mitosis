import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import run


def git_in(tree, *args):
    subprocess.run(["git", *args], cwd=tree, check=True, capture_output=True)


def put(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


class WrittenPaths(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mitosis-written-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.tree = os.path.join(self.root, "repo")
        os.makedirs(self.tree)
        git_in(self.tree, "init", "-q", "-b", "main")
        git_in(self.tree, "config", "user.email", "t@example.invalid")
        git_in(self.tree, "config", "user.name", "t")
        put(os.path.join(self.tree, "kept.txt"), "kept\n")
        put(os.path.join(self.tree, "moved.txt"), "a line long enough to be found as a rename\n")
        put(os.path.join(self.tree, "edited.txt"), "before\n")
        git_in(self.tree, "add", "-A")
        git_in(self.tree, "commit", "-q", "-m", "base")
        git_in(self.tree, "mv", "moved.txt", "renamed.txt")
        put(os.path.join(self.tree, "edited.txt"), "after\n")
        put(os.path.join(self.tree, "deep", "café notes.txt"), "new\n")

    def written_since_lists_both_sides_of_a_rename_and_every_untracked_file(self):
        self.assertEqual(
            run.written_since(self.tree),
            ("edited.txt", "moved.txt", "renamed.txt", "deep/café notes.txt"),
        )

    def dirty_paths_lists_only_the_new_side_of_a_rename(self):
        self.assertEqual(
            run._dirty_paths({"path": self.tree}),
            ("edited.txt", "renamed.txt","deep/café notes.txt"),
        )

    def a_clean_tree_has_no_written_paths(self):
        git_in(self.tree, "add", "-A")
        git_in(self.tree, "commit", "-q", "-m", "all")
        self.assertEqual(run.written_since(self.tree), ())
        self.assertEqual(run._dirty_paths({"path": self.tree}), ())

    def outside_a_repository_written_since_refuses_and_dirty_paths_finds_nothing(self):
        outside = os.path.join(self.root, "plain")
        os.makedirs(outside)
        with self.assertRaises(run.GitError):
            run.written_since(outside)
        self.assertEqual(run._dirty_paths({"path": outside}), ())


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            WrittenPaths,
            (
                "written_since_lists_both_sides_of_a_rename_and_every_untracked_file",
                "dirty_paths_lists_only_the_new_side_of_a_rename",
                "a_clean_tree_has_no_written_paths",
                "outside_a_repository_written_since_refuses_and_dirty_paths_finds_nothing",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
