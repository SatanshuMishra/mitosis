import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import test_clean_gate as clean
import test_referee_lock as lock_fixtures

REFEREE_SETUP = r'''
import os
import sys

folder = sys.argv[1]
for name, content in (("conftest.py", "import pytest\n"), ("built.txt", "ok\n")):
    with open(os.path.join(folder, name), "w", encoding="utf-8", newline="") as handle:
        handle.write(content)
sys.exit(0)
'''

ORDINARY_SETUP = r'''
import os
import sys

folder = sys.argv[1]
with open(os.path.join(folder, "built.txt"), "w", encoding="utf-8", newline="") as handle:
    handle.write("ok\n")
sys.exit(0)
'''


class SetupRefereeWrite(unittest.TestCase):
    def setUp(self):
        self.scratch = None

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()

    def _run(self, setup_script):
        self.scratch = lock_fixtures.Scratch({"tests/test_calc.py": clean.CALC_TEST})
        scratch = self.scratch
        lock_fixtures.put(os.path.join(scratch.stubs, "clean_worker.py"), clean.WORKER)
        lock_fixtures.put(os.path.join(scratch.stubs, "accept_log.py"), clean.LOGGING_ACCEPT)
        lock_fixtures.put(os.path.join(scratch.stubs, "gate_setup.py"), setup_script)
        lock_fixtures.put(
            os.path.join(scratch.stubs, "accept.json"),
            json.dumps({"read": [], "writes": {}, "check": clean.CALC_CHECK}),
        )
        lock_fixtures.put(
            os.path.join(scratch.stubs, "clean-worker.json"),
            json.dumps(
                {
                    "demo": {
                        "step": "build",
                        "actions": [["write", "calc.py", clean.CALC]],
                        "declared": ["calc.py"],
                    }
                }
            ),
        )
        items = scratch.items(
            [lock_fixtures.step("build", ("calc.py",), "demo", acceptance=clean.ACCEPTANCE)]
        )
        return scratch.mitosis(
            "--items",
            items,
            "--run-dir",
            scratch.run_dir,
            "--feature-branch",
            "feature",
            "--charter",
            scratch.file("charter.md", lock_fixtures.PLAIN_CHARTER),
            "--dispatch-command",
            scratch.stub("clean_worker.py", "{msp}"),
            "--acceptance-command",
            scratch.stub("accept_log.py", "{worktree}"),
            "--gate-setup",
            scratch.stub("gate_setup.py", "{worktree}"),
            "--timeout",
            "120",
            "--no-push",
        )

    def _msp(self, completed):
        summary = lock_fixtures.summary_of(completed)
        self.assertTrue(summary.get("msps"), "the JSON result lists no MSP" + lock_fixtures.tail(completed))
        return summary["msps"][0]

    def _accept_log(self):
        path = os.path.join(self.scratch.tmp, "accept.log")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def a_referee_named_top_level_file_gate_setup_makes_is_fatal(self):
        completed = self._run(REFEREE_SETUP)
        self.assertEqual(completed.returncode, 9, "mitosis exit code" + lock_fixtures.tail(completed))
        msp = self._msp(completed)
        self.assertEqual(msp["state"], "referee-reverted")
        self.assertIs(msp["reconcile"]["fatal"], True)
        self.assertIn("conftest.py", msp["reconcile"]["referee"])
        for entry in self._accept_log():
            if "conftest.py" in entry["listing"]:
                self.assertIs(
                    msp["reconcile"]["fatal"],
                    True,
                    "the acceptance run in %s saw conftest.py and the run is not fatal"
                    % entry["worktree"],
                )
        listed = self.scratch.git("ls-remote", "--heads", self.scratch.remote, "refs/heads/mitosis/demo")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(listed.stdout, "")

    def an_ordinary_top_level_file_gate_setup_makes_survives_every_run(self):
        completed = self._run(ORDINARY_SETUP)
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + lock_fixtures.tail(completed))
        msp = self._msp(completed)
        self.assertEqual(msp["reconcile"]["referee"], [])
        entries = self._accept_log()
        self.assertTrue(entries, "accept.log records no acceptance run")
        for entry in entries:
            self.assertIn("built.txt", entry["listing"], "the run in %s" % entry["worktree"])


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            SetupRefereeWrite,
            (
                "a_referee_named_top_level_file_gate_setup_makes_is_fatal",
                "an_ordinary_top_level_file_gate_setup_makes_survives_every_run",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
