import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import run
from test_submodule_referee_write import SubmoduleRefereeWrite, commit_inside

PLANTED = b"import os\nos.environ['PLANTED'] = '1'\n"


def git_text(args, cwd):
    return subprocess.run(
        ["git", "-c", "protocol.file.allow=always", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout.decode("utf-8")


def write_bytes(path, data):
    with open(path, "wb") as handle:
        handle.write(data)


class UnjudgedSubmoduleWork(SubmoduleRefereeWrite):
    def a_submodule_whose_commits_cannot_be_read_is_held_back(self):
        self._tree()
        write_bytes(os.path.join(self.dep, "conftest.py"), PLANTED)
        written = commit_inside(self.dep, "worker")
        run.commit_all(self.tree, "pre-gate")
        os.remove(os.path.join(self.dep, "conftest.py"))
        root = git_text(["rev-parse", written + "^{tree}"], self.dep).strip()
        objects = git_text(["rev-parse", "--git-path", "objects"], self.dep).strip()
        loose = os.path.join(self.dep, objects, root[:2], root[2:])
        os.chmod(loose, 0o644)
        os.remove(loose)
        held = self._referee_stage()
        self.assertEqual((held or {}).get("state"), "gate-failed")
        self.assertIsNone(held["reconcile"])
        self.assertIn("dep/ cannot be judged", held["reason"])


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (UnjudgedSubmoduleWork, ("a_submodule_whose_commits_cannot_be_read_is_held_back",)),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
