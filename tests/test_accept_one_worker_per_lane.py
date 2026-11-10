import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_accept_any_input as any_input
import test_every_finding as fixtures
import test_finishes_the_work as finishes
from test_accept_execution import tail

SPEC = "spec.md"

TWO_TESTS = (
    "import unittest\n\nimport a\n\n\nclass A(unittest.TestCase):\n"
    "    def works(self):\n        self.assertEqual(a.value(), 2)\n\n"
    "    def also(self):\n        self.assertEqual(a.value() + 1, 3)\n"
)

MISNAMED = (
    "import unittest\n\nimport a\n\n\nclass Alpha(unittest.TestCase):\n"
    "    def works(self):\n        self.assertEqual(a.value(), 2)\n"
)

NAMED_ACCEPT = r'''
import os
import subprocess
import sys

mode, file, test = sys.argv[1], sys.argv[2], sys.argv[3]
folder, name = os.path.split(file)
module = os.path.splitext(name)[0]
env = dict(os.environ, PYTHONPATH=os.pathsep.join([os.path.abspath(folder), os.getcwd()]))
completed = subprocess.run(
    [sys.executable, "-m", "unittest", "-v", module + "." + test],
    env=env,
    capture_output=True,
    text=True,
    timeout=120,
)
sys.stdout.write(completed.stdout + completed.stderr)
output = completed.stdout + completed.stderr
if "_FailedTest" in output:
    sys.exit(1 if "ModuleNotFoundError" in output or "ImportError: cannot import name" in output else 2)
sys.exit(completed.returncode if completed.returncode in (0, 1) else 2)
'''

STRUCTURE = r'''
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.stdin.read()
with open(os.path.join(here, "structure-calls.jsonl"), "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": sys.argv[1:]}) + "\n")
with open(os.path.join(here, "structure-reply.json"), encoding="utf-8") as handle:
    reply = json.load(handle)
print(json.dumps({"type": "result", "session_id": sys.argv[1], "result": "done", "structured_output": {"reply": reply}}))
'''


BRIEF = r'''
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.stdin.read()
with open(os.path.join(here, "brief-calls.jsonl"), "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": sys.argv[1:]}) + "\n")
sys.exit(1)
'''

def lines(path):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class OwnTests(any_input.AsksCase):
    def an_asks_plan_gives_no_step_a_check_author_and_each_step_writes_its_test(self):
        built = any_input.item(
            "reader", ["reader.py"], "reader prints the count", tests=[{"file": "tests/test_reader.py", "test": "R.reads"}]
        )
        completed = self._asks_run("add a reader\n", {"add a reader": any_input.reply(built)})
        self.exits(completed, 0)
        self.assertFalse(self.plan(completed).get("check_authors"), tail(completed))
        steps = self._items(completed)
        self.assertEqual(any_input.named(steps, "check-author"), [], tail(completed))
        self.assertEqual(
            (any_input.by_name(steps, "reader") or {}).get("files"),
            ["reader.py", "tests/test_reader.py"],
            tail(completed),
        )


class Builds(unittest.TestCase):
    def setUp(self):
        self.scratch = finishes.Scratch({SPEC: fixtures.SPEC_DOCUMENT})

    def tearDown(self):
        self.scratch.close()

    def _exit(self, completed, code):
        self.assertEqual(completed.returncode, code, "mitosis exit code" + fixtures.tail(completed))

    def _attempts(self):
        return lines(os.path.join(self.scratch.stubs, "attempts.jsonl"))

    def a_spec_build_hands_the_splits_task_to_its_worker_with_no_brief_or_lock_session(self):
        scratch = self.scratch
        scratch.checked("checkout", "-q", "feature")
        fixtures.put(os.path.join(scratch.stubs, "structure.py"), STRUCTURE)
        step = {
            "name": "first",
            "task": "build first: make a.value return 2",
            "files": ["a.py"],
            "spec_ref": ["1.1"],
            "acceptance": [finishes.PROPERTY],
        }
        fixtures.put(
            os.path.join(scratch.stubs, "structure-reply.json"),
            json.dumps({"items": [step], "assumptions": [], "constraints": []}),
        )
        scratch.jobs({"first": [{"writes": {"a.py": finishes.RIGHT, finishes.TEST_FILE: finishes.PROPERTY_TEXT}}]})
        completed = scratch.mitosis(
            "--spec",
            SPEC,
            "--run-dir",
            scratch.run_dir,
            "--feature-branch",
            "feature",
            "--no-push",
            "--decompose-command",
            scratch.stub("structure.py", "{session_id}", "{schema}"),
            "--reask-command",
            fixtures.REASK_COMMAND,
            "--dispatch-command",
            scratch.stub("worker.py", "{task}", "{session_id}", "{schema}"),
            "--acceptance-command",
            scratch.stub("accept.py", "unittest", "{file}", "{test}"),
        )
        attempts = self._attempts()
        self.assertIn("build first: make a.value return 2", attempts[0]["task"])
        self.assertEqual(len(attempts), 1, fixtures.tail(completed))
        self._exit(completed, 0)
        self.assertEqual(len(lines(os.path.join(scratch.stubs, "structure-calls.jsonl"))), 1)
        with open(os.path.join(scratch.run_dir, "plan.json"), encoding="utf-8") as handle:
            plan = json.load(handle)
        self.assertFalse(plan.get("check_authors"), fixtures.tail(completed))
        self.assertEqual(
            [entry.get("files") for entry in plan["items"]], [["a.py", finishes.TEST_FILE]], fixtures.tail(completed)
        )
        msps = fixtures.summary_of(completed)["msps"]
        self.assertEqual([entry["gate"]["outcome"] for entry in msps], ["pass"], fixtures.tail(completed))

    def an_older_callers_brief_command_is_ignored_with_a_notice(self):
        scratch = self.scratch
        scratch.checked("checkout", "-q", "feature")
        fixtures.put(os.path.join(scratch.stubs, "structure.py"), STRUCTURE)
        fixtures.put(os.path.join(scratch.stubs, "brief.py"), BRIEF)
        step = {
            "name": "first",
            "task": "build first",
            "files": ["a.py"],
            "spec_ref": ["1.1"],
            "acceptance": [finishes.PROPERTY],
        }
        fixtures.put(
            os.path.join(scratch.stubs, "structure-reply.json"),
            json.dumps({"items": [step], "assumptions": [], "constraints": []}),
        )
        completed = scratch.mitosis(
            "--spec",
            SPEC,
            "--plan-only",
            "--run-dir",
            scratch.run_dir,
            "--feature-branch",
            "feature",
            "--decompose-command",
            scratch.stub("structure.py", "{session_id}", "{schema}"),
            "--reask-command",
            fixtures.REASK_COMMAND,
            "--brief-command",
            scratch.stub("brief.py", "{step}", "{session_id}", "{schema}"),
        )
        self._exit(completed, 0)
        self.assertIn(
            "mitosis: --brief-command is deprecated and ignored; the split writes each Step's task",
            completed.stderr,
        )
        self.assertEqual(lines(os.path.join(scratch.stubs, "brief-calls.jsonl")), [])

    def steps_sharing_a_file_build_in_one_worker_session(self):
        scratch = self.scratch
        alpha = fixtures.step("alpha", ["a.py", finishes.TEST_FILE], "alpha", [{"file": finishes.TEST_FILE, "test": "A.works"}])
        beta = fixtures.step(
            "beta", ["a.py", finishes.TEST_FILE], "alpha", [{"file": finishes.TEST_FILE, "test": "A.also"}], ["alpha"]
        )
        completed, summary = scratch.built(
            [alpha, beta], {"alpha": [{"writes": {"a.py": finishes.RIGHT, finishes.TEST_FILE: TWO_TESTS}}]}
        )
        attempts = self._attempts()
        self.assertEqual(len(attempts), 1, fixtures.tail(completed))
        self._exit(completed, 0)
        task = attempts[0]["task"]
        self.assertLess(task.index("build alpha"), task.index("build beta"))
        record = finishes.msp_of(summary, "alpha")
        self.assertEqual(record["gate"]["counts"]["pass"], 2, fixtures.tail(completed))
        commits = scratch.checked("rev-list", "--count", "feature..mitosis/alpha").strip()
        self.assertEqual(commits, "1")

    def a_named_test_the_acceptance_command_cannot_run_goes_back_to_its_worker(self):
        scratch = self.scratch
        fixtures.put(os.path.join(scratch.stubs, "accept.py"), NAMED_ACCEPT)
        completed, summary = scratch.built(
            [finishes.built_step()],
            {
                "first": [
                    {"writes": {"a.py": finishes.RIGHT, finishes.TEST_FILE: MISNAMED}},
                    {"writes": {"a.py": finishes.RIGHT, finishes.TEST_FILE: finishes.PROPERTY_TEXT}},
                ]
            },
            acceptance="named",
        )
        self._exit(completed, 0)
        tasks = scratch.attempts("first")
        self.assertEqual(len(tasks), 2, fixtures.tail(completed))
        self.assertIn("could not run tests/test_a.py::A.works", tasks[1])
        self.assertIn("_FailedTest", tasks[1])
        self.assertEqual(finishes.msp_of(summary, "first")["gate"]["outcome"], "pass", fixtures.tail(completed))


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTest(OwnTests("an_asks_plan_gives_no_step_a_check_author_and_each_step_writes_its_test"))
    for name in (
        "a_spec_build_hands_the_splits_task_to_its_worker_with_no_brief_or_lock_session",
        "an_older_callers_brief_command_is_ignored_with_a_notice",
        "steps_sharing_a_file_build_in_one_worker_session",
        "a_named_test_the_acceptance_command_cannot_run_goes_back_to_its_worker",
    ):
        suite.addTest(Builds(name))
    return suite
