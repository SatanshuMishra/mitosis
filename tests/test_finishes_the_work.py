import json
import os
import stat
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import run
import test_every_finding as fixtures
import test_gate_teeth as teeth
from test_check_author_lane import saved_plan

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
log = os.path.join(here, "attempts.jsonl")
found = []
if os.path.exists(log):
    with open(log, encoding="utf-8") as handle:
        found = [json.loads(line) for line in handle]
name = next(
    (step for step in sorted(jobs, key=len, reverse=True) if "build " + step in task),
    next((entry["step"] for entry in found if entry["session"] == SESSION), None),
)
earlier = sum(1 for entry in found if entry["step"] == name)
with open(log, "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"step": name, "task": task, "session": SESSION}) + "\n")
attempts = jobs[name]
attempt = attempts[min(earlier, len(attempts) - 1)]
if attempt.get("limit"):
    print("You've hit your usage limit")
    sys.exit(1)
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

    def sessions(self, step):
        path = os.path.join(self.stubs, "attempts.jsonl")
        with open(path, encoding="utf-8") as handle:
            found = [json.loads(line) for line in handle]
        return [entry["session"] for entry in found if entry["step"] == step]

    def continuing_worker(self):
        return self.stub("worker.py", "{prompt}", "{session_id}", "{schema}")

    def hook(self, body, name="pre-commit"):
        executable(os.path.join(self.repo, ".git", "hooks", name), "#!/bin/sh\n" + body)

    def built(self, steps, jobs, push=False, acceptance="unittest", flags=(), reask=None, saved=False):
        self.jobs(jobs)
        items = self.items(steps)
        planned = ("--plan-file", saved_plan(items, self.repo)) if saved else ()
        shipping = (
            (*fixtures.PR_FLAGS, "--pr-command", self.stub("pr.py", "{branch}"), "--render-command", self.render)
            if push
            else ("--no-push",)
        )
        completed = self.mitosis(
            "--items",
            items,
            "--run-dir",
            self.run_dir,
            "--feature-branch",
            "feature",
            "--dispatch-command",
            self.stub("worker.py", "{task}", "{session_id}", "{schema}"),
            "--reask-command",
            reask or fixtures.REASK_COMMAND,
            "--acceptance-command",
            self.stub("accept.py", acceptance, "{file}", "{test}"),
            *shipping,
            *planned,
            *flags,
        )
        return completed, fixtures.summary_of(completed)

    def on_branch(self, branch, path):
        return self.git("cat-file", "-e", "refs/heads/%s:%s" % (branch, path)).returncode == 0

    def worktree_of(self, branch):
        blocks = self.checked("worktree", "list", "--porcelain").split("\n\n")
        found = [block for block in blocks if "branch refs/heads/%s" % branch in block]
        return found[0].splitlines()[0].split(" ", 1)[1]

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


def foreign_paths(summary):
    return sorted(entry["detail"] for entry in summary["findings"] if entry["kind"] == "foreign")


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

    def an_acceptance_command_that_cannot_judge_gets_one_repair_and_never_holds_the_msp(self):
        scratch = self._scratch()
        completed, summary = scratch.built(
            [built_step()],
            {"first": [{"writes": {"a.py": RIGHT, TEST_FILE: PROPERTY_TEXT}}]},
            push=True,
            acceptance="exit-2",
        )
        self._exit(completed, 0)
        tasks = scratch.attempts("first")
        self.assertEqual(len(tasks), 2, fixtures.tail(completed))
        self.assertIn("could not run tests/test_a.py::A.works", tasks[1])
        self.assertEqual(msp_of(summary, "first")["state"], "shipped")
        self.assertEqual(msp_of(summary, "first")["gate"]["outcome"], "inconclusive")

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
            reask=scratch.continuing_worker(),
            saved=True,
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
            reask=scratch.continuing_worker(),
            saved=True,
        )
        self._exit(completed, 0)
        record = msp_of(summary, "first")
        self.assertEqual(record["lock"]["state"], "ok")
        self.assertEqual(record["gate"]["outcome"], "pass")
        self.assertTrue(scratch.on_branch("mitosis/first", TEST_FILE))
        retry = scratch.attempts("check-author")[1]
        self.assertIn("mitosis refused the checks you wrote", retry)
        self.assertNotIn("build check-author", retry)
        sessions = scratch.sessions("check-author")
        self.assertEqual(sessions[0], sessions[1])

    def a_lock_worker_reporting_failure_starts_again_told_why(self):
        scratch = self._scratch()
        completed, summary = scratch.built(
            [lock_author(), builder()],
            {
                "check-author": [
                    {"status": "failed", "notes": "the fixture was missing"},
                    {"writes": {TEST_FILE: PROPERTY_TEXT, "a.py": RIGHT}},
                ],
                "first": [{"writes": {"a.py": RIGHT}}],
            },
            reask=scratch.continuing_worker(),
            saved=True,
        )
        self._exit(completed, 0)
        self.assertEqual(msp_of(summary, "first")["lock"]["state"], "ok")
        tasks = scratch.attempts("check-author")
        self.assertIn("An earlier attempt at this work failed", tasks[1])
        sessions = scratch.sessions("check-author")
        self.assertNotEqual(sessions[0], sessions[1])

    def a_lock_whose_session_cannot_continue_starts_again_told_why(self):
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
            reask="%s -c 'raise SystemExit(1)' {prompt} {session_id} {schema}" % sys.executable,
            saved=True,
        )
        self._exit(completed, 0)
        self.assertEqual(msp_of(summary, "first")["lock"]["state"], "ok")
        tasks = scratch.attempts("check-author")
        self.assertEqual(len(tasks), 2)
        self.assertIn("An earlier attempt at this work failed", tasks[1])
        self.assertNotIn("mitosis keeps the write", tasks[1])
        sessions = scratch.sessions("check-author")
        self.assertNotEqual(sessions[0], sessions[1])

    def a_repair_writing_a_sibling_lanes_file_is_reported(self):
        scratch = self._scratch()
        completed, summary = scratch.built(
            [built_step(), fixtures.step("second", ["b.py"], "first")],
            {
                "first": [
                    {"writes": {"a.py": WRONG, TEST_FILE: PROPERTY_TEXT}},
                    {"writes": {"a.py": RIGHT, TEST_FILE: PROPERTY_TEXT, "b.py": "taken over\n"}},
                ],
                "second": [{"writes": {"b.py": "b = 1\n"}}],
            },
        )
        self._exit(completed, 8)
        self.assertEqual(msp_of(summary, "first")["gate"]["outcome"], "pass")
        self.assertEqual(foreign_paths(summary), ["b.py"])
        self.assertEqual(scratch.checked("show", "refs/heads/mitosis/first:b.py"), "taken over\n")

    def a_repair_keeps_the_foreign_writes_of_the_lanes_first_attempt(self):
        scratch = self._scratch()
        completed, summary = scratch.built(
            [
                {**built_step(), "after": ["second"]},
                fixtures.step("second", ["b.py"], "first"),
                fixtures.step("third", ["c.py"], "first", [], ["second"]),
            ],
            {
                "first": [
                    {"writes": {"a.py": WRONG, TEST_FILE: PROPERTY_TEXT, "b.py": "taken over\n"}},
                    {"writes": {"a.py": RIGHT, TEST_FILE: PROPERTY_TEXT}},
                ],
                "second": [{"writes": {"b.py": "b = 1\n"}}],
                "third": [{"writes": {"c.py": "c = 1\n"}}],
            },
        )
        self._exit(completed, 8)
        self.assertEqual(len(scratch.attempts("first")), 2)
        self.assertEqual(foreign_paths(summary), ["b.py"])

    def a_repair_reaches_the_gate_when_a_sibling_wrote_that_file_earlier(self):
        scratch = self._scratch()
        second_test = "tests/test_b.py"
        checks = (
            "import unittest\n\nimport b\n\n\nclass B(unittest.TestCase):\n"
            "    def works(self):\n        self.assertEqual(b.value(), 2)\n"
        )
        completed, summary = scratch.built(
            [
                fixtures.step("first", ["a.py"], "first", [], ["second"]),
                fixtures.step(
                    "second",
                    ["b.py", second_test],
                    "first",
                    [{"file": second_test, "test": "B.works"}],
                ),
                fixtures.step("third", ["c.py"], "first", [], ["second"]),
            ],
            {
                "first": [{"writes": {"a.py": "a = 1\n", "b.py": WRONG + "# first\n"}}],
                "third": [{"writes": {"c.py": "c = 1\n"}}],
                "second": [
                    {"writes": {"b.py": WRONG, second_test: checks}},
                    {"writes": {"b.py": RIGHT, second_test: checks}},
                ],
            },
        )
        self._exit(completed, 8)
        record = msp_of(summary, "first")
        self.assertEqual(record["state"], "committed")
        self.assertEqual(record["gate"]["outcome"], "pass")
        self.assertEqual(scratch.checked("show", "refs/heads/mitosis/first:b.py"), RIGHT)

    def a_producers_repair_reaches_the_consumer_built_on_it(self):
        scratch = self._scratch()
        completed, summary = scratch.built(
            [built_step(), fixtures.step("second", ["b.py"], "second", [], ["first"])],
            {
                "first": [
                    {"writes": {"a.py": WRONG, TEST_FILE: PROPERTY_TEXT}},
                    {"writes": {"a.py": RIGHT, TEST_FILE: PROPERTY_TEXT}},
                ],
                "second": [{"writes": {"b.py": "b = 1\n"}}],
            },
        )
        self._exit(completed, 0)
        self.assertEqual(msp_of(summary, "second")["state"], "committed")
        self.assertEqual(scratch.checked("show", "refs/heads/mitosis/second:a.py"), RIGHT)

    def a_usage_limit_during_a_repair_parks_and_the_resume_finishes_the_repair(self):
        scratch = self._scratch()
        jobs = {
            "first": [
                {"writes": {"a.py": WRONG, TEST_FILE: PROPERTY_TEXT}},
                {"limit": True},
                {"writes": {"a.py": RIGHT, TEST_FILE: PROPERTY_TEXT}},
            ]
        }
        completed, summary = scratch.built([built_step()], jobs)
        self._exit(completed, 13)
        self.assertEqual(msp_of(summary, "first")["state"], "parked")
        resumed, summary = scratch.built([built_step()], jobs, flags=("--resume",))
        self._exit(resumed, 0)
        tasks = scratch.attempts("first")
        self.assertEqual(len(tasks), 3)
        self.assertIn("The finished build fails tests/test_a.py::A.works", tasks[2])
        self.assertEqual(msp_of(summary, "first")["state"], "committed")

    def a_retried_lanes_outside_write_reaches_the_gate_and_is_reported(self):
        scratch = self._scratch()
        helped = "import helper\n\n\ndef value():\n    return helper.TWO\n"
        completed, summary = scratch.built(
            [built_step()],
            {
                "first": [
                    {"status": "failed", "notes": "helper.py is outside my write-set"},
                    {"writes": {"a.py": helped, "helper.py": "TWO = 2\n", TEST_FILE: PROPERTY_TEXT}},
                ]
            },
        )
        self._exit(completed, 0)
        self.assertIn("mitosis keeps the write", scratch.attempts("first")[1])
        record = msp_of(summary, "first")
        self.assertEqual(record["gate"]["outcome"], "pass")
        self.assertEqual(
            [entry["detail"] for entry in summary["findings"] if entry["kind"] == "undeclared"],
            ["helper.py"],
        )
        self.assertTrue(scratch.on_branch("mitosis/first", "helper.py"))

    def a_tracked_file_under_an_ignored_folder_lands(self):
        scratch = self._scratch({"gen/x.py": "x = 1\n"})
        scratch.checked("checkout", "-q", "feature")
        fixtures.put(os.path.join(scratch.repo, ".gitignore"), "__pycache__/\ngen/\n")
        scratch.checked("add", ".gitignore")
        scratch.checked("commit", "-q", "-m", "ignore the generated folder")
        scratch.checked("checkout", "-q", "main")
        completed, summary = scratch.built(
            [fixtures.step("first", ["gen/x.py"], "first")],
            {"first": [{"writes": {"gen/x.py": "x = 2\n"}}]},
        )
        self._exit(completed, 0)
        self.assertEqual(scratch.checked("show", "refs/heads/mitosis/first:gen/x.py"), "x = 2\n")

    def a_hook_never_moves_the_branch_and_its_edits_never_touch_the_work(self):
        scratch = self._scratch()
        seen = os.path.join(scratch.tmp, "branch-during-hook")
        scratch.hook('git rev-parse refs/heads/mitosis/first > "%s"\necho tampered >> a.py\n' % seen)
        completed, summary = scratch.built(
            [built_step()],
            {"first": [{"writes": {"a.py": RIGHT, TEST_FILE: PROPERTY_TEXT}}]},
        )
        self._exit(completed, 0)
        tip = scratch.checked("rev-parse", "refs/heads/mitosis/first").strip()
        with open(seen, encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), tip)
        self.assertEqual(scratch.checked("show", "refs/heads/mitosis/first:a.py"), RIGHT)
        self.assertEqual(scratch.checked("-C", scratch.worktree_of("mitosis/first"), "status", "--porcelain"), "")

    def a_hook_exiting_129_holds_the_msp(self):
        scratch = self._scratch()
        scratch.hook("echo refused with a usage code >&2\nexit 129\n")
        completed, summary = scratch.built(
            [built_step()],
            {"first": [{"writes": {"a.py": RIGHT, TEST_FILE: PROPERTY_TEXT}}]},
        )
        self._exit(completed, 21)
        self.assertIn("refused with a usage code", msp_of(summary, "first")["reason"])

    def a_repair_the_worker_reports_failed_is_named_in_the_held_reason(self):
        scratch = self._scratch()
        completed, summary = scratch.built(
            [built_step()],
            {
                "first": [
                    {"writes": {"a.py": WRONG, TEST_FILE: PROPERTY_TEXT}},
                    {"status": "failed", "notes": "the service is not reachable here"},
                ]
            },
        )
        self._exit(completed, 21)
        reason = msp_of(summary, "first")["reason"]
        self.assertIn("failed against the finished work", reason)
        self.assertIn("the repair of Lane 0 failed", reason)

    def a_producers_repair_that_conflicts_blocks_the_consumer_and_says_why(self):
        scratch = self._scratch()
        completed, summary = scratch.built(
            [built_step(), fixtures.step("second", ["b.py"], "second", [], ["first"])],
            {
                "first": [
                    {"writes": {"a.py": WRONG, TEST_FILE: PROPERTY_TEXT}},
                    {"writes": {"a.py": RIGHT, TEST_FILE: PROPERTY_TEXT, "b.py": "from the repair\n"}},
                ],
                "second": [{"writes": {"b.py": "b = 1\n"}}],
            },
        )
        self._exit(completed, 7)
        record = msp_of(summary, "second")
        self.assertEqual(record["state"], "blocked")
        self.assertIn("would not merge", record["reason"])

    def _pre_push_marked(self, flags):
        scratch = self._scratch()
        marker = os.path.join(scratch.tmp, "pre-push-ran")
        scratch.hook('touch "%s"\n' % marker, "pre-push")
        completed, summary = scratch.built(
            [built_step()],
            {"first": [{"writes": {"a.py": RIGHT, TEST_FILE: PROPERTY_TEXT}}]},
            push=True,
            flags=flags(scratch),
        )
        self._exit(completed, 0)
        self.assertTrue(scratch.pushed("mitosis/first"))
        return os.path.lexists(marker)

    def a_pre_push_hook_runs_when_workers_are_not_sandboxed(self):
        self.assertTrue(self._pre_push_marked(lambda scratch: ()))

    def a_sandboxed_run_never_runs_a_pre_push_hook(self):
        if sys.platform != "darwin":
            self.skipTest("sandbox-exec exists only on macOS")
        self.assertFalse(
            self._pre_push_marked(lambda scratch: ("--worker-sandbox", "--worker-write", scratch.stubs))
        )

    def a_sandboxed_run_fires_no_repository_hook_outside_the_sandbox(self):
        if sys.platform != "darwin":
            self.skipTest("sandbox-exec exists only on macOS")
        scratch = self._scratch()
        marker = os.path.join(scratch.tmp, "hook-fired")
        for name in ("reference-transaction", "post-index-change", "post-checkout"):
            scratch.hook('echo %s >> "%s"\n' % (name, marker), name)
        completed, summary = scratch.built(
            [built_step()],
            {"first": [{"writes": {"a.py": RIGHT, TEST_FILE: PROPERTY_TEXT}}]},
            push=True,
            flags=("--worker-sandbox", "--worker-write", scratch.stubs),
        )
        self._exit(completed, 0)
        self.assertTrue(scratch.pushed("mitosis/first"))
        self.assertFalse(os.path.lexists(marker), "a repository hook ran outside the sandbox")

    def a_repair_note_stays_bounded_however_many_tests_fail(self):
        scratch = self._scratch()
        noisy = (
            "import unittest\n\n\nclass A(unittest.TestCase):\n"
            "    def one(self):\n        self.fail('x' * 6000)\n\n"
            "    def two(self):\n        self.fail('y' * 6000)\n"
        )
        failing = [{"file": TEST_FILE, "test": "A.one"}, {"file": TEST_FILE, "test": "A.two"}]
        calm = (
            "import unittest\n\n\nclass A(unittest.TestCase):\n"
            "    def one(self):\n        pass\n\n    def two(self):\n        pass\n"
        )
        completed, summary = scratch.built(
            [built_step(acceptance=failing)],
            {
                "first": [
                    {"writes": {"a.py": RIGHT, TEST_FILE: noisy}},
                    {"writes": {"a.py": RIGHT, TEST_FILE: calm}},
                ]
            },
        )
        tasks = scratch.attempts("first")
        self.assertEqual(len(tasks), 2, fixtures.tail(completed))
        self.assertLess(len(tasks[1]) - len(tasks[0]), run.MAX_OUTPUT + len(run.REPAIR_NOTE) + 200)


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    suite.addTests(
        FinishesTheWork(name)
        for name in (
            "a_lane_reporting_failure_is_retried_with_its_reason_and_ships",
            "a_lock_whose_session_cannot_continue_starts_again_told_why",
            "a_lock_worker_reporting_failure_starts_again_told_why",
            "a_repair_writing_a_sibling_lanes_file_is_reported",
            "a_repair_keeps_the_foreign_writes_of_the_lanes_first_attempt",
            "a_repair_reaches_the_gate_when_a_sibling_wrote_that_file_earlier",
            "a_producers_repair_reaches_the_consumer_built_on_it",
            "a_usage_limit_during_a_repair_parks_and_the_resume_finishes_the_repair",
            "a_retried_lanes_outside_write_reaches_the_gate_and_is_reported",
            "a_tracked_file_under_an_ignored_folder_lands",
            "a_hook_never_moves_the_branch_and_its_edits_never_touch_the_work",
            "a_hook_exiting_129_holds_the_msp",
            "a_pre_push_hook_runs_when_workers_are_not_sandboxed",
            "a_sandboxed_run_never_runs_a_pre_push_hook",
            "a_repair_note_stays_bounded_however_many_tests_fail",
            "a_sandboxed_run_fires_no_repository_hook_outside_the_sandbox",
            "a_repair_the_worker_reports_failed_is_named_in_the_held_reason",
            "a_producers_repair_that_conflicts_blocks_the_consumer_and_says_why",
            "a_lane_failing_twice_ships_nothing_for_its_msp_and_its_sibling_still_ships",
            "a_test_failing_against_the_work_is_repaired_once_and_ships",
            "a_test_still_failing_after_the_repair_holds_its_msp_and_names_the_test",
            "an_acceptance_command_that_cannot_judge_gets_one_repair_and_never_holds_the_msp",
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
