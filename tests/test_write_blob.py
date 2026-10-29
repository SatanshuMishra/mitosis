import os
import shutil
import stat
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import run


class WriteBlob(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.target = os.path.join(self.folder, "entry")

    def permissions(self):
        return stat.S_IMODE(os.lstat(self.target).st_mode)

    def regular_file_is_written_at_0o644(self):
        run._write_blob(self.target, run.REGULAR_MODE, b"plain bytes\n")
        with open(self.target, "rb") as handle:
            written = handle.read()
        self.assertEqual(written, b"plain bytes\n")
        self.assertEqual(self.permissions(), 0o644)
        self.assertTrue(stat.S_ISREG(os.lstat(self.target).st_mode))

    def executable_is_written_at_0o755(self):
        run._write_blob(self.target, run.EXECUTABLE_MODE, b"#!/bin/sh\n")
        with open(self.target, "rb") as handle:
            written = handle.read()
        self.assertEqual(written, b"#!/bin/sh\n")
        self.assertEqual(self.permissions(), 0o755)

    def symlink_points_at_the_given_target(self):
        run._write_blob(self.target, run.SYMLINK_MODE, b"elsewhere/file.txt")
        self.assertTrue(os.path.islink(self.target))
        self.assertEqual(os.readlink(self.target), "elsewhere/file.txt")

    def existing_file_is_refused(self):
        with open(self.target, "wb") as handle:
            handle.write(b"keep")
        with self.assertRaises(FileExistsError):
            run._write_blob(self.target, run.REGULAR_MODE, b"replace")
        with open(self.target, "rb") as handle:
            kept = handle.read()
        self.assertEqual(kept, b"keep")

    def existing_symlink_is_refused_for_a_file_and_a_link(self):
        os.symlink("original", self.target)
        for mode, data in ((run.REGULAR_MODE, b"replace"), (run.SYMLINK_MODE, b"other")):
            with self.assertRaises(FileExistsError):
                run._write_blob(self.target, mode, data)
        self.assertEqual(os.readlink(self.target), "original")


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    suite.addTests(
        WriteBlob(name)
        for name in (
            "regular_file_is_written_at_0o644",
            "executable_is_written_at_0o755",
            "symlink_points_at_the_given_target",
            "existing_file_is_refused",
            "existing_symlink_is_refused_for_a_file_and_a_link",
        )
    )
    return suite


if __name__ == "__main__":
    unittest.main()
