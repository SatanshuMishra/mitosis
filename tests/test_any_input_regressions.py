import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_accept_execution as accept
import test_accept_any_input as any_input
import test_accept_structure_parity as parity
from test_accept_execution import tail

STEP = {"name": "a", "files": ["a.py"], "spec_ref": ["1.1"], "acceptance": []}
HEADER = b"# Spec\n\n## 1.1 Build\n"
TOO_LARGE_FOR_ONE_ENVIRONMENT_VALUE = 2000000
TEMPLATE = ("--recon-prompt-template", "ASK<{ask}>")


class UnusualSpecs(parity.ParityCase):
    def _planned_from(self, content, *flags):
        with open(os.path.join(self.scratch.repo, "spec.md"), "wb") as handle:
            handle.write(content)
        return self._spec_plan_only([dict(STEP)], *flags)

    def a_spec_that_is_not_utf8_still_plans(self):
        self.exits(self._planned_from(HEADER + b"\xff\xfe\n"), 0)

    def a_spec_that_is_not_utf8_plans_under_a_prompt_template(self):
        self.exits(self._planned_from(HEADER + b"\xff\xfe\n", *TEMPLATE), 0)

    def a_spec_holding_a_nul_byte_still_plans(self):
        self.exits(self._planned_from(HEADER + b"\x00\n"), 0)

    def a_spec_too_large_for_one_environment_value_still_plans(self):
        self.exits(self._planned_from(HEADER + b"x" * TOO_LARGE_FOR_ONE_ENVIRONMENT_VALUE + b"\n"), 0)


class UnusualAsksFiles(any_input.AsksCase):
    def _planned_from(self, content):
        path = self.scratch.path("asks.bin")
        with open(path, "wb") as handle:
            handle.write(content)
        self._control({})
        return self.scratch.mitosis(
            "--asks",
            path,
            "--decompose-command",
            self._recon_command(),
            "--reask-command",
            self._reask_command(),
            "--tier-model",
            "top=test-top",
            "--plan-only",
            "--run-dir",
            self.scratch.run_dir,
        )

    def an_asks_file_that_is_not_utf8_is_refused(self):
        completed = self._planned_from(b"add a reader\n\xff\xfe\n")
        self.exits(completed, 3)
        self.assertNotIn("Traceback", completed.stderr)

    def an_asks_file_holding_a_nul_byte_is_refused(self):
        completed = self._planned_from(b"add a\x00 reader\n")
        self.exits(completed, 3)
        self.assertNotIn("Traceback", completed.stderr)


class ResumedAsks(any_input.AsksCase):
    def _landed_on_the_feature_branch(self, path):
        scratch = self.scratch
        for arguments in (("switch", "-q", "feature"),):
            self.assertEqual(scratch.git(*arguments).returncode, 0)
        accept.put(os.path.join(scratch.repo, path), "import unittest\n")
        for arguments in (("add", "-A"), ("commit", "-q", "-m", "land a test"), ("switch", "-q", "main")):
            self.assertEqual(scratch.git(*arguments).returncode, 0)

    def a_resume_keeps_the_plan_after_a_named_test_lands_on_the_branch(self):
        built = any_input.item(
            "reader", ["reader.py"], "reader prints the count", tests=[{"file": "tests/test_reader.py", "test": "R.reads"}]
        )
        replies = {"add a reader": any_input.reply(built)}
        first = self._asks_run("add a reader\n", replies, "--feature-branch", "feature")
        self.exits(first, 0)
        planned = self.plan(first).get("plan_id")
        self._landed_on_the_feature_branch("tests/test_reader.py")
        second = self._asks_run("add a reader\n", replies, "--feature-branch", "feature", "--resume")
        self.exits(second, 0)
        self.assertEqual(self.plan(second).get("plan_id"), planned, tail(second))

    def a_resume_still_reports_what_the_first_run_dropped(self):
        replies = {"one": any_input.reply(any_input.item("a", ["a.py"], "a works")), "three": any_input.reply()}
        first = self._asks_run("one\nthree\n", replies)
        self.exits(first, 0)
        second = self._asks_run("one\nthree\n", replies, "--resume")
        self.exits(second, 0)
        attention = accept.summary_of(second).get("attention") or []
        self.assertTrue(any('ask "three"' in line for line in attention if isinstance(line, str)), tail(second))


class RunnerRecord(accept.Case):
    def a_run_records_the_process_running_it(self):
        scratch = self.scratch
        completed = scratch.full_run([accept.S("a", ["a.py"], msp="m")])
        recorded = accept.read_text(os.path.join(scratch.run_dir, "runner.pid"))
        self.assertTrue(recorded.strip().isdigit(), repr(recorded) + tail(completed))
        self.assertNotEqual(int(recorded), os.getpid(), tail(completed))


def load_tests(loader, standard, pattern):
    helpers = ("make_scratch",)

    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            return sorted(
                name
                for name in vars(case)
                if not name.startswith("_") and callable(getattr(case, name)) and name not in helpers
            )

    suite = unittest.TestSuite()
    for case in (UnusualSpecs, UnusualAsksFiles, ResumedAsks, RunnerRecord):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
