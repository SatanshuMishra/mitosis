import hashlib
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import mutants
import test_gate_teeth as gate_fixtures
import test_referee_lock as lock_fixtures

NON_UTF8_LOCKED = b"# \xff\nimport unittest\r# x\n"
NON_UTF8_CALC = b"NAME = '\xff'\n"


def commit_bytes(scratch, path, content):
    scratch.checked("-C", scratch.repo, "checkout", "-q", "feature")
    target = os.path.join(scratch.repo, path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as handle:
        handle.write(content)
    scratch.checked("-C", scratch.repo, "add", "-A")
    scratch.checked("-C", scratch.repo, "commit", "-q", "-m", "seed %s as raw bytes" % path)
    scratch.checked("-C", scratch.repo, "checkout", "-q", "main")


class ExactBytes(unittest.TestCase):
    def setUp(self):
        self.scratch = None

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()

    def a_non_utf8_locked_file_is_hashed_by_its_exact_bytes(self):
        self.scratch = lock_fixtures.Scratch()
        commit_bytes(self.scratch, lock_fixtures.LOCKED, NON_UTF8_LOCKED)
        charter = self.scratch.file("charter.md", lock_fixtures.LOCKED_CHARTER)
        items = self.scratch.items([lock_fixtures.step("build", ["calc.py"], "demo")])
        completed = self.scratch.mitosis(
            "--items",
            items,
            "--charter",
            charter,
            "--plan-only",
            "--feature-branch",
            "feature",
            "--run-dir",
            self.scratch.run_dir,
        )
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + lock_fixtures.tail(completed))
        referee = lock_fixtures.summary_of(completed).get("referee") or {}
        self.assertEqual(
            referee.get("receipt_lock"),
            hashlib.sha256(
                b"file:" + lock_fixtures.LOCKED.encode("utf-8") + b"\n" + NON_UTF8_LOCKED + b"\n"
            ).hexdigest(),
        )

    def a_non_utf8_line_in_the_diff_does_not_stop_the_gate(self):
        self.scratch = gate_fixtures.Scratch()
        commit_bytes(self.scratch, "calc.py", NON_UTF8_CALC)
        items = self.scratch.items([gate_fixtures.add_step()])
        self.scratch.jobs(
            {
                "demo": {
                    "step": "add",
                    "writes": {
                        "calc.py": gate_fixtures.CORRECT_ADD,
                        "tests/test_calc.py": gate_fixtures.EXACT_TEST,
                    },
                }
            }
        )
        completed = self.scratch.build(items, push=True)
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + gate_fixtures.tail(completed))
        summary = gate_fixtures.summary_of(completed)
        self.assertTrue(summary.get("msps"), "the JSON summary lists no MSP" + gate_fixtures.tail(completed))
        self.assertEqual(summary["msps"][0]["state"], "shipped")
        self.assertEqual(
            len(self.scratch.checked("ls-remote", "origin", "mitosis/demo").splitlines()), 1
        )

    def drop_added_lines_splits_only_at_line_feeds(self):
        found = mutants.fallback({"limits.txt": [2]}, lambda path: "a\x0cb\nc\n")
        self.assertEqual(
            found,
            [{"file": "limits.txt", "line": 2, "op": mutants.DROP_ADDED, "text": "a\x0cb\n"}],
        )


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            ExactBytes,
            (
                "a_non_utf8_locked_file_is_hashed_by_its_exact_bytes",
                "a_non_utf8_line_in_the_diff_does_not_stop_the_gate",
                "drop_added_lines_splits_only_at_line_feeds",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
