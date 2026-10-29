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

import run

GIT_IDENTITY = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "mitosis test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "mitosis test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}

BEFORE = {
    "a.py": "def a(x):\n    return x + 1\n",
    "b.py": "def b(x):\n    return x > 1\n",
    "c.py": "z = 1\n",
    "e.py": "same = 1\n",
}

AFTER = {
    "a.py": "def a(x):\n    y = x + 1\n    return y - 2\n",
    "b.py": "def b(x):\n    return x > 1\n\n\ndef c(x):\n    return x < 5\n",
    "c.py": "z = 2\n",
    "d.py": "w = 4 * 5\n",
    "e.py": "same = 1\n",
}

PLAN = {
    "items": [
        {
            "name": "build",
            "task": "build it",
            "files": ["a.py", "b.py", "c.py", "d.py", "e.py"],
            "acceptance": [],
            "msp": 0,
        }
    ],
    "msps": {0: {"steps": ["build"], "files": ["a.py", "b.py", "c.py", "d.py", "e.py"]}},
}

PROPERTIES = (
    {"step": "build", "test": "one", "outcome": "pass"},
    {"step": "build", "test": "two", "outcome": "pass"},
    {"step": "build", "test": "three", "outcome": "pass"},
)

EXPECTED = [("a.py", 2), ("a.py", 3), ("b.py", 6), ("c.py", 1), ("d.py", 1)]


class AddedLinesOnce(unittest.TestCase):
    def setUp(self):
        self.tree = tempfile.mkdtemp(prefix="mitosis-once-")
        self.addCleanup(shutil.rmtree, self.tree, True)
        self.env = {**os.environ, **GIT_IDENTITY}
        self.git("init", "-q", "-b", "main")
        self.base = self.commit(BEFORE, "base")
        self.head = self.commit(AFTER, "work")

    def git(self, *args):
        completed = subprocess.run(
            ["git", *args],
            cwd=self.tree,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        return completed.stdout.decode().strip()

    def commit(self, files, message):
        for path in os.listdir(self.tree):
            if path != ".git":
                os.remove(os.path.join(self.tree, path))
        for path, content in files.items():
            with open(os.path.join(self.tree, path), "w", encoding="utf-8") as handle:
                handle.write(content)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def counted(self):
        real = run.git_run
        diffs = []

        def counting(args, *rest, **named):
            if "--no-index" in args:
                diffs.append(tuple(args))
            return real(args, *rest, **named)

        return diffs, mock.patch.object(run, "git_run", counting)

    def places(self, mutants):
        return sorted({(mutant["file"], mutant["line"]) for mutant in mutants})

    def every_property_of_a_step_shares_one_diff_process(self):
        diffs, patched = self.counted()
        with patched:
            found = [
                run.property_mutants(PLAN, 0, self.tree, self.base, self.head, entry)
                for entry in PROPERTIES
            ]
        self.assertEqual(len(diffs), 1, "git diff --no-index processes: %r" % (diffs,))
        for mutants in found:
            self.assertEqual(self.places(mutants), EXPECTED)
        self.assertEqual(found[0], found[1])
        self.assertEqual(found[1], found[2])

    def one_diff_process_covers_every_changed_file_of_the_step(self):
        diffs, patched = self.counted()
        with patched:
            found = run.property_mutants(PLAN, 0, self.tree, self.base, self.head, PROPERTIES[0])
        self.assertEqual(len(diffs), 1, "git diff --no-index processes: %r" % (diffs,))
        self.assertEqual(self.places(found), EXPECTED)


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            AddedLinesOnce,
            (
                "every_property_of_a_step_shares_one_diff_process",
                "one_diff_process_covers_every_changed_file_of_the_step",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
