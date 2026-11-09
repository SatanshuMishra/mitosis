import json
import os
import stat
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import test_every_finding as fixtures
import test_gate_teeth as teeth

WORKER = r'''
import json
import os
import subprocess
import sys

SESSION, SCHEMA = sys.argv[-2], sys.argv[-1]
task = sys.argv[-3]

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "worker.json"), encoding="utf-8") as handle:
    jobs = json.load(handle)
name = next(step for step in sorted(jobs, key=len, reverse=True) if "build " + step in task)
log = os.path.join(here, "attempts.jsonl")
earlier = 0
if os.path.exists(log):
    with open(log, encoding="utf-8") as handle:
        earlier = sum(1 for line in handle if json.loads(line)["step"] == name)
with open(log, "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"step": name, "task": task}) + "\n")
attempts = jobs[name]
attempt = attempts[min(earlier, len(attempts) - 1)]
for path, content in sorted(attempt.get("writes", {}).items()):
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)
if attempt.get("self_commit"):
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", "the Worker committed for itself"], check=True)
reply = {
    "item": name,
    "status": attempt.get("status", "ok"),
    "files_changed": sorted(attempt.get("writes", {})),
    "notes": attempt.get("notes", "done"),
}
print(json.dumps({"type": "result", "session_id": SESSION, "result": "done", "structured_output": reply}))
'''

TEST_FILE = "tests/test_a.py"
PROPERTY = {"file": TEST_FILE, "test": "A.works"}
PROPERTY_TEXT = (
    "import unittest\n\nimport a\n\n\nclass A(unittest.TestCase):\n"
    "    def works(self):\n        self.assertEqual(a.value(), 2)\n"
)
RIGHT = "def value():\n    return 2\n"
WRONG = "def value():\n    return 3\n"
REFUSED_LOCK = (
    "import unittest\n\n\nclass A(unittest.TestCase):\n"
    "    def works(self):\n        self.assertTrue(True)\n"
)


def executable(path, text):
    fixtures.put(path, text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


class Scratch(fixtures.Scratch):
    def __init__(self, seeded=None):
        super().__init__(seeded)
        fixtures.put(os.path.join(self.stubs, "worker.py"), WORKER)
        fixtures.put(self.render, "#!%s\n%s" % (sys.executable, teeth.RENDER))

    def jobs(self, jobs):
        return fixtures.put(os.path.join(self.stubs, "worker.json"), json.dumps(jobs))

    def attempts(self, step):
        path = os.path.join(self.stubs, "attempts.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            found = [json.loads(line) for line in handle]
        return [entry["task"] for entry in found if entry["step"] == step]

    def hook(self, body):
        executable(os.path.join(self.repo, ".git", "hooks", "pre-commit"), "#!/bin/sh\n" + body)

    def built(self, steps, jobs, push=False, acceptance="unittest"):
        self.jobs(jobs)
        shipping = (
            (*fixtures.PR_FLAGS, "--pr-command", self.stub("pr.py", "{branch}"), "--render-command", self.render)
            if push
            else ("--no-push",)
        )
        completed = self.mitosis(
            "--items",
            self.items(steps),
            "--run-dir",
            self.run_dir,
            "--feature-branch",
            "feature",
            "--dispatch-command",
            self.stub("worker.py", "{task}", "{session_id}", "{schema}"),
            "--reask-command",
            fixtures.REASK_COMMAND,
            "--acceptance-command",
            self.stub("accept.py", acceptance, "{file}", "{test}"),
            *shipping,
        )
        return completed, fixtures.summary_of(completed)

    def on_branch(self, branch, path):
        return self.git("cat-file", "-e", "refs/heads/%s:%s" % (branch, path)).returncode == 0

    def pushed(self, branch):
        return bool(self.checked("ls-remote", "origin", branch).strip())


def built_step(name="first", files=("a.py", TEST_FILE), msp="first", acceptance=(PROPERTY,)):
    return fixtures.step(name, list(files), msp, list(acceptance))


def lock_author():
    labelled = {**fixtures.step("check-author", [TEST_FILE], None, []), "role": "check-author"}
    return {key: value for key, value in labelled.items() if key != "msp"}


def builder():
    return fixtures.step("first", ["a.py"], "first", [PROPERTY], ["check-author"])


def msp_of(summary, label):
    return next(entry for entry in summary["msps"] if entry["msp"] == label)


class FinishesTheWork(unittest.TestCase):
    def setUp(self):
        self.scratch = None

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()

    def _scratch(self, seeded=None):
        self.scratch = Scratch(seeded)
        return self.scratch

    def _exit(self, completed, code):
        self.assertEqual(completed.returncode, code, "mitosis exit code" + fixtures.tail(completed))

    def a_lane_reporting_failure_is_retried_with_its_reason_and_ships(self):
        scratch = self._scratch()
        completed, summary = scratch.built(
            [built_step()],
            {
                "first": [
                    {"status": "failed", "notes": "the helper module is outside my write-set"},
                    {"writes": {"a.py": RIGHT, TEST_FILE: PROPERTY_TEXT}},
                ]
            },
            push=True,
        )
        self._exit(completed, 0)
        tasks = scratch.attempts("first")
        self.assertEqual(len(tasks), 2)
        self.assertIn("An earlier attempt at this work failed", tasks[1])
        self.assertIn("the helper module is outside my write-set", tasks[1])
        self.assertNotIn("An earlier attempt", tasks[0])
        self.assertEqual(msp_of(summary, "first")["state"], "shipped")
        self.assertTrue(scratch.pushed("mitosis/first"))

    def a_lane_failing_twice_ships_nothing_for_its_msp_and_its_sibling_still_ships(self):
        scratch = self._scratch()
        completed, summary = scratch.built(
            [
                built_step(),
                fixtures.step("second", ["b.py"], "second"),
            ],
            {
                "first": [{"status": "failed", "notes": "cannot do it"}],
                "second": [{"writes": {"b.py": "b = 1\n"}}],
            },
            push=True,
        )
        self._exit(completed, 10)
        self.assertEqual(len(scratch.attempts("first")), 2)
        self.assertEqual(msp_of(summary, "first")["state"], "blocked")
        self.assertFalse(scratch.pushed("mitosis/first"))
        self.assertEqual(msp_of(summary, "second")["state"], "shipped")
        self.assertTrue(scratch.pushed("mitosis/second"))

    def a_test_failing_against_the_work_is_repaired_once_and_ships(self):
        scratch = self._scratch()
        completed, summary = scratch.built(
            [built_step()],
            {
                "first": [
                    {"writes": {"a.py": WRONG, TEST_FILE: PROPERTY_TEXT}},
                    {"writes": {"a.py": RIGHT, TEST_FILE: PROPERTY_TEXT}},
                ]
            },
            push=True,
        )
        self._exit(completed, 0)
        tasks = scratch.attempts("first")
        self.assertEqual(len(tasks), 2)
        self.assertIn("The finished build fails tests/test_a.py::A.works", tasks[1])
        self.assertIn("AssertionError", tasks[1])
        record = msp_of(summary, "first")
        self.assertEqual(record["state"], "shipped")
        self.assertEqual(record["gate"]["outcome"], "pass")

    def a_test_still_failing_after_the_repair_holds_its_msp_and_names_the_test(self):
        scratch = self._scratch()
        completed, summary = scratch.built(
            [built_step()],
            {"first": [{"writes": {"a.py": WRONG, TEST_FILE: PROPERTY_TEXT}}]},
            push=True,
        )
        self._exit(completed, 21)
        self.assertEqual(len(scratch.attempts("first")), 2)
        record = msp_of(summary, "first")
        self.assertEqual(record["state"], "gate-failed")
        self.assertIn("tests/test_a.py::A.works failed against the finished work", record["reason"])
        self.assertFalse(scratch.pushed("mitosis/first"))
        self.assertEqual(
            [entry["kind"] for entry in summary["findings"]], ["gate-inconclusive"]
        )

    def an_acceptance_command_that_cannot_judge_never_holds_the_msp(self):
        scratch = self._scratch()
        completed, summary = scratch.built(
            [built_step()],
            {"first": [{"writes": {"a.py": RIGHT, TEST_FILE: PROPERTY_TEXT}}]},
            push=True,
            acceptance="exit-2",
        )
        self._exit(completed, 0)
        self.assertEqual(len(scratch.attempts("first")), 1)
        self.assertEqual(msp_of(summary, "first")["state"], "shipped")
        self.assertEqual(msp_of(summary, "first")["gate"]["outcome"], "inconclusive")

    def a_mutant_is_never_taken_from_a_test_file(self):
        scratch = self._scratch()
        other = "tests/test_docs.py"
        files = ("z.py", other, "tests/test_z.py")
        check = {"file": "tests/test_z.py", "test": "Z.works"}
        text = (
            "import unittest\n\nimport z\n\n\nclass Z(unittest.TestCase):\n"
            "    def works(self):\n        self.assertEqual(z.value(), 2)\n"
        )
        completed, summary = scratch.built(
            [built_step(files=files, acceptance=(check,))],
            {
                "first": [
                    {
                        "writes": {
                            "z.py": RIGHT,
                            "tests/test_z.py": text,
                            other: "VERSION = 1 + 1\n",
                        }
                    }
                ]
            },
        )
        self._exit(completed, 0)
        judged = msp_of(summary, "first")["gate"]["properties"][0]
        self.assertEqual(judged["mutants"]["killed"]["file"], "z.py")
        self.assertEqual(judged["mutants"]["tried"], 1)

    def a_gitignored_path_in_a_write_set_never_fails_its_lane(self):
        scratch = self._scratch({".gitignore": "__pycache__/\nbuild/\n"})
        files = ("a.py", TEST_FILE, "build/out.txt")
        completed, summary = scratch.built(
            [built_step(files=files)],
            {"first": [{"writes": {"a.py": RIGHT, TEST_FILE: PROPERTY_TEXT, "build/out.txt": "made\n"}}]},
        )
        self._exit(completed, 0)
        self.assertEqual(msp_of(summary, "first")["state"], "committed")
        self.assertTrue(scratch.on_branch("mitosis/first", "a.py"))
        self.assertFalse(scratch.on_branch("mitosis/first", "build/out.txt"))

    def a_hook_sees_the_whole_msp_once_and_never_a_partial_lane(self):
        scratch = self._scratch()
        calls = os.path.join(scratch.tmp, "hook-calls")
        scratch.hook(
            'echo run >> "%s"\n' % calls
            + "staged=$(git diff --cached --name-only)\n"
            + 'case "$staged" in *a.py*) ;; *) echo missing a.py >&2; exit 1;; esac\n'
            + 'case "$staged" in *b.py*) ;; *) echo missing b.py >&2; exit 1;; esac\n'
        )
        completed, summary = scratch.built(
            [
                built_step(),
                fixtures.step("second", ["b.py"], "first"),
            ],
            {
                "first": [{"writes": {"a.py": RIGHT, TEST_FILE: PROPERTY_TEXT}}],
                "second": [{"writes": {"b.py": "b = 1\n"}}],
            },
        )
        self._exit(completed, 0)
        self.assertEqual(msp_of(summary, "first")["state"], "committed")
        with open(calls, encoding="utf-8") as handle:
            self.assertEqual(handle.read().split(), ["run"])

    def a_hook_refusing_the_whole_change_holds_the_msp_and_says_why(self):
        scratch = self._scratch()
        scratch.hook("echo a secret was found >&2\nexit 1\n")
        completed, summary = scratch.built(
            [built_step()],
            {"first": [{"writes": {"a.py": RIGHT, TEST_FILE: PROPERTY_TEXT}}]},
            push=True,
        )
        self._exit(completed, 21)
        record = msp_of(summary, "first")
        self.assertEqual(record["state"], "gate-failed")
        self.assertIn("a secret was found", record["reason"])
        self.assertFalse(scratch.pushed("mitosis/first"))

    def a_declared_path_never_written_is_reported_and_exits_zero(self):
        scratch = self._scratch()
        files = ("a.py", TEST_FILE, "notes.md")
        completed, summary = scratch.built(
            [built_step(files=files)],
            {"first": [{"writes": {"a.py": RIGHT, TEST_FILE: PROPERTY_TEXT}}]},
        )
        self._exit(completed, 0)
        self.assertEqual(msp_of(summary, "first")["reconcile"]["unwritten"], ["notes.md"])

    def a_worker_that_changed_nothing_is_never_called_clean(self):
        scratch = self._scratch()
        completed, summary = scratch.built(
            [fixtures.step("first", ["a.py"], "first")],
            {"first": [{"notes": "nothing to do"}]},
        )
        self._exit(completed, 6)
        self.assertEqual(msp_of(summary, "first")["state"], "unchanged")

    def a_worker_that_commits_for_itself_still_lands_its_work(self):
        scratch = self._scratch()
        completed, summary = scratch.built(
            [built_step()],
            {"first": [{"writes": {"a.py": RIGHT, TEST_FILE: PROPERTY_TEXT}, "self_commit": True}]},
        )
        self._exit(completed, 0)
        self.assertEqual(msp_of(summary, "first")["state"], "committed")
        self.assertTrue(scratch.on_branch("mitosis/first", "a.py"))

    def a_lock_refused_twice_falls_back_and_the_build_still_ships(self):
        scratch = self._scratch()
        completed, summary = scratch.built(
            [lock_author(), builder()],
            {
                "check-author": [{"writes": {TEST_FILE: REFUSED_LOCK}}],
                "first": [{"writes": {"a.py": RIGHT}}],
            },
            push=True,
        )
        self._exit(completed, 0)
        tasks = scratch.attempts("check-author")
        self.assertEqual(len(tasks), 2)
        self.assertIn("passes on the feature base without the build", tasks[1])
        record = msp_of(summary, "first")
        self.assertEqual(record["state"], "shipped")
        self.assertEqual(record["lock"]["state"], "failed")
        judged = record["gate"]["properties"][0]
        self.assertEqual(judged["outcome"], "not-applicable")
        self.assertIn("could not lock it", judged["reason"])
        self.assertTrue(scratch.pushed("mitosis/first"))

    def a_lock_refused_once_is_accepted_on_its_retry(self):
        scratch = self._scratch()
        completed, summary = scratch.built(
            [lock_author(), builder()],
            {
                "check-author": [
                    {"writes": {TEST_FILE: REFUSED_LOCK}},
                    {"writes": {TEST_FILE: PROPERTY_TEXT, "a.py": RIGHT}},
                ],
                "first": [{"writes": {"a.py": RIGHT}}],
            },
            push=True,
        )
        self._exit(completed, 0)
        record = msp_of(summary, "first")
        self.assertEqual(record["lock"]["state"], "ok")
        self.assertEqual(record["gate"]["outcome"], "pass")
        self.assertTrue(scratch.on_branch("mitosis/first", TEST_FILE))


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    suite.addTests(
        FinishesTheWork(name)
        for name in (
            "a_lane_reporting_failure_is_retried_with_its_reason_and_ships",
            "a_lane_failing_twice_ships_nothing_for_its_msp_and_its_sibling_still_ships",
            "a_test_failing_against_the_work_is_repaired_once_and_ships",
            "a_test_still_failing_after_the_repair_holds_its_msp_and_names_the_test",
            "an_acceptance_command_that_cannot_judge_never_holds_the_msp",
            "a_mutant_is_never_taken_from_a_test_file",
            "a_gitignored_path_in_a_write_set_never_fails_its_lane",
            "a_hook_sees_the_whole_msp_once_and_never_a_partial_lane",
            "a_hook_refusing_the_whole_change_holds_the_msp_and_says_why",
            "a_declared_path_never_written_is_reported_and_exits_zero",
            "a_worker_that_changed_nothing_is_never_called_clean",
            "a_worker_that_commits_for_itself_still_lands_its_work",
            "a_lock_refused_twice_falls_back_and_the_build_still_ships",
            "a_lock_refused_once_is_accepted_on_its_retry",
        )
    )
    return suite


if __name__ == "__main__":
    unittest.main()
