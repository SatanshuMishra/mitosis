import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import run


class MakeParentsCost(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)

    def kind_of(self, name):
        return run._kind(os.lstat(os.path.join(self.folder, name)).st_mode)

    def behind_link_probe_is_never_called_when_making_parents(self):
        calls = []
        original = run._behind_link

        def counting(tree, path):
            calls.append(path)
            return original(tree, path)

        paths = ["a%d/b%d/c/d/e/f%d.py" % (index % 10, index % 7, index) for index in range(200)]
        with mock.patch.object(run, "_behind_link", counting):
            for path in paths:
                run._make_parents(self.folder, path)
        self.assertEqual(len(calls), 0)
        self.assertEqual(self.kind_of("a3/b3/c/d/e"), "dir")

    def symlinked_parent_is_replaced_by_a_real_folder(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        os.symlink(outside, os.path.join(self.folder, "a"))
        run._make_parents(self.folder, "a/b/c/file.py")
        self.assertEqual(self.kind_of("a"), "dir")
        self.assertEqual(self.kind_of("a/b/c"), "dir")
        self.assertEqual(os.listdir(outside), [])

    def file_in_the_way_of_a_parent_is_replaced_by_a_real_folder(self):
        with open(os.path.join(self.folder, "a"), "wb") as handle:
            handle.write(b"obstruction")
        run._make_parents(self.folder, "a/b/file.py")
        self.assertEqual(self.kind_of("a"), "dir")
        self.assertEqual(self.kind_of("a/b"), "dir")


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    suite.addTests(
        MakeParentsCost(name)
        for name in (
            "behind_link_probe_is_never_called_when_making_parents",
            "symlinked_parent_is_replaced_by_a_real_folder",
            "file_in_the_way_of_a_parent_is_replaced_by_a_real_folder",
        )
    )
    return suite


if __name__ == "__main__":
    unittest.main()
