import contextlib
import copy
import hashlib
import importlib
import inspect
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MITOSIS = os.path.join(ROOT, "mitosis.py")
CALL_LIMIT = 60
RUN_LIMIT = 400
STRIPPED = ("CLAUDE_CODE_SESSION_ID", "PLUGIN_METRICS_PARENT", "SHIP")
GIT_IDENTITY = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "mitosis test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "mitosis test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}

ORIGINAL_CALC = "def add(a, b):\n    return 0\n"
SUM_CALC = "def add(a, b):\n    return a + b\n"

VALID_RETURN = {
    "item": "add-numbers",
    "status": "ok",
    "files_changed": ["src/calc.py"],
    "notes": "done",
}

MODEL = r'''
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
role = os.path.splitext(os.path.basename(os.path.abspath(__file__)))[0]
with open(os.path.join(here, role + ".log"), "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\n")
with open(os.path.join(here, role + ".json"), encoding="utf-8") as handle:
    job = json.load(handle)
for path, content in sorted(job.get("writes", {}).items()):
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)
if job.get("stderr"):
    sys.stderr.write(job["stderr"] + "\n")
    sys.stderr.flush()
if not job.get("silent"):
    printed = {
        "type": "result",
        "session_id": sys.argv[2] if len(sys.argv) > 2 else "",
        "result": job.get("result", "done"),
    }
    if "reply" in job:
        printed["structured_output"] = job["reply"]
    sys.stdout.write(json.dumps(printed) + "\n")
    sys.stdout.flush()
sys.exit(job.get("exit", 0))
'''

ACCEPT = r'''
import sys

try:
    with open("src/calc.py", encoding="utf-8", newline="") as handle:
        text = handle.read()
except OSError:
    sys.exit(1)
sys.exit(0 if text == %r else 1)
''' % SUM_CALC

RENDER_BODY = (
    "## Problem\nSums were wrong.\n\n## Change\n- Add numbers.\n\n## Impact\nSums are right.\n\n"
    "## Testing\n| Check | Result |\n|---|---|\n| tests/test_calc.py | passed |"
)

RENDER_REPLY = {"title": "feat(calc): add numbers", "body": RENDER_BODY}

RENDER = r'''
import json
import sys

sys.stdout.write(json.dumps(%r) + "\n")
''' % RENDER_REPLY


def module(name):
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    return importlib.import_module(name)


def put(path, content):
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)
    return path


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def json_lines(text):
    found = []
    for line in text.splitlines():
        try:
            json.loads(line)
        except ValueError:
            continue
        found.append(line)
    return found


def tail(completed):
    return "\n--- stdout ---\n%s\n--- stderr ---\n%s" % (
        completed.stdout[-4000:],
        completed.stderr[-4000:],
    )


def executable(path, script):
    put(path, "#!%s\n%s" % (sys.executable, script))
    os.chmod(path, 0o755)
    return path


class Scratch:
    def __init__(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="mitosis-handoff-"))
        self.repo = os.path.join(self.tmp, "repo")
        self.remote = os.path.join(self.tmp, "remote.git")
        self.stubs = os.path.join(self.tmp, "stubs")
        self.run_dir = os.path.join(self.tmp, "runs", "run")
        kept = {key: value for key, value in os.environ.items() if key not in STRIPPED}
        self.env = {
            **kept,
            **GIT_IDENTITY,
            "HOME": os.path.join(self.tmp, "home"),
            "PLUGIN_METRICS_DIR": os.path.join(self.tmp, "metrics"),
        }
        os.makedirs(self.env["HOME"])
        os.makedirs(self.repo)
        self.checked("init", "-q", "-b", "main", self.repo)
        self.checked("init", "-q", "--bare", self.remote)
        put(os.path.join(self.repo, "src", "calc.py"), ORIGINAL_CALC)
        put(os.path.join(self.repo, ".gitignore"), "__pycache__/\n")
        put(os.path.join(self.repo, "tests", "test_calc.py"), "import unittest\n")
        self.checked("-C", self.repo, "add", "-A")
        self.checked("-C", self.repo, "commit", "-q", "-m", "seed")
        self.checked("-C", self.repo, "remote", "add", "origin", self.remote)
        self.spec = put(os.path.join(self.tmp, "spec.md"), "# Calc\n\n## 5.1 Add\n\nAdd numbers.\n")
        self.items = [
            {
                "name": "add-numbers",
                "task": "Make add return the sum of its arguments.",
                "files": ["src/calc.py"],
                "source": {
                    "path": self.spec,
                    "sha256": hashlib.sha256(read_bytes(self.spec)).hexdigest(),
                },
                "acceptance": [{"file": "tests/test_calc.py", "test": "Calc.adds"}],
                "spec_ref": ["5.1"],
            }
        ]
        self.items_path = put(os.path.join(self.tmp, "items.json"), json.dumps(self.items))
        for role in ("dispatch", "reask"):
            put(os.path.join(self.stubs, role + ".py"), MODEL)
        put(os.path.join(self.stubs, "accept.py"), ACCEPT)
        self.render = executable(os.path.join(self.stubs, "render"), RENDER)
        self.job("dispatch", writes={"src/calc.py": SUM_CALC}, reply=VALID_RETURN)
        self.job("reask", reply=VALID_RETURN)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def checked(self, *args):
        completed = subprocess.run(
            ["git", *args],
            cwd=self.tmp,
            env=self.env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=CALL_LIMIT,
        )
        if completed.returncode != 0:
            raise RuntimeError("scaffold git %s failed: %s" % (" ".join(args), completed.stderr))
        return completed.stdout

    def job(self, role, **job):
        put(os.path.join(self.stubs, role + ".json"), json.dumps(job))

    def log(self, role):
        return os.path.join(self.stubs, role + ".log")

    def calls(self, role):
        if not os.path.isfile(self.log(role)):
            return []
        with open(self.log(role), encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def stub(self, script, *words):
        return " ".join(
            [shlex.quote(sys.executable), shlex.quote(os.path.join(self.stubs, script))]
            + list(words)
        )

    def flags(self, *extra):
        return [
            "--items",
            self.items_path,
            "--run-dir",
            self.run_dir,
            "--feature-branch",
            "main",
            "--dispatch-command",
            self.stub("dispatch.py", "{task}", "{session_id}", "{schema}"),
            "--reask-command",
            self.stub("reask.py", "{prompt}", "{session_id}", "{schema}"),
            "--acceptance-command",
            self.stub("accept.py", "{file}", "{test}"),
            "--timeout",
            "120",
            *extra,
        ]

    def mitosis(self, *flags):
        return subprocess.run(
            [sys.executable, MITOSIS, *flags],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=RUN_LIMIT,
        )

    def state_path(self):
        return os.path.join(self.run_dir, "state.json")


def valid_state():
    return {
        "version": "0.12.0",
        "plan_id": "plan-one",
        "feature_branch": "main",
        "base_commit": "0" * 40,
        "started": "2026-11-03T00:00:00+00:00",
        "drift": None,
        "worktrees": [],
        "lanes": {
            "0": {
                "lane": 0,
                "msp": 0,
                "state": "ok",
                "reason": None,
                "exit": 0,
                "timed_out": False,
                "returned": dict(VALID_RETURN),
                "stdout": None,
                "stderr": None,
                "merged": [],
                "commit": None,
                "started": None,
                "finished": None,
                "session_id": "session-one",
                "seeded": None,
                "seed_skipped": None,
                "seed_seconds": None,
            }
        },
        "msps": {},
        "stacking_exceptions": [],
        "merge_danger": None,
        "sandbox": "off",
    }


def calc_plan(root):
    core = module("core")
    spec = put(os.path.join(root, "spec.md"), "# Calc\n\n## 5.1 Add\n\nAdd numbers.\n")
    put(os.path.join(root, "src", "calc.py"), ORIGINAL_CALC)
    items = [
        {
            "name": "add-numbers",
            "task": "Make add return the sum of its arguments.",
            "files": ["src/calc.py"],
            "source": {"path": spec, "sha256": hashlib.sha256(read_bytes(spec)).hexdigest()},
            "acceptance": [{"file": "tests/test_calc.py", "test": "Calc.adds"}],
            "spec_ref": ["5.1"],
        }
    ]
    return core.plan(items, root=root)


def captured(call):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = call()
        except SystemExit as stop:
            code = stop.code
    return code, out.getvalue(), err.getvalue()


@contextlib.contextmanager
def working_directory(path):
    before = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(before)


class StateFile(unittest.TestCase):
    def setUp(self):
        self.scratch = None
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()

    def an_invalid_state_is_never_written(self):
        handoff = module("handoff")
        run = module("run")
        run_dir = os.path.join(os.path.realpath(self.folder.name), "run")
        os.makedirs(run_dir)
        state = valid_state()
        self.assertEqual(handoff.errors("h9-mitosis-state", state), ())
        run.write_state(run_dir, state)
        path = os.path.join(run_dir, "state.json")
        before = read_bytes(path)
        broken = copy.deepcopy(state)
        del broken["lanes"]["0"]["session_id"]
        with self.assertRaises(handoff.HandoffError) as caught:
            run.write_state(run_dir, broken)
        message = str(caught.exception)
        for part in (path, "mitosis", "h9-mitosis-state", "#/lanes/0 required", "session_id"):
            self.assertIn(part, message)
        self.assertEqual(read_bytes(path), before)
        self.assertFalse(os.path.exists(path + ".partial"))

    def a_state_file_that_fails_h9_is_refused_not_read_as_absent(self):
        handoff = module("handoff")
        run = module("run")
        base = os.path.realpath(self.folder.name)
        listed, partial, missing = (os.path.join(base, name) for name in ("listed", "partial", "missing"))
        put(os.path.join(listed, "state.json"), "[]")
        put(os.path.join(partial, "state.json"), '{"version": "0.12.0"}')
        os.makedirs(missing)
        for run_dir in (listed, partial):
            path = os.path.join(run_dir, "state.json")
            with self.assertRaises(handoff.HandoffError) as caught:
                run.load_state(run_dir)
            self.assertIn(path, str(caught.exception))
            self.assertIn("h9-mitosis-state", str(caught.exception))
            if run_dir == partial:
                self.assertIn("plan_id", str(caught.exception))
        self.assertIsNone(run.load_state(missing))

    def a_resume_over_an_invalid_state_file_is_refused_with_exit_3(self):
        handoff = module("handoff")
        self.scratch = Scratch()
        self.assertEqual(handoff.errors("h12-interphase-items", self.scratch.items), ())
        first = self.scratch.mitosis(*self.scratch.flags("--no-push"))
        self.assertEqual(first.returncode, 0, "first run exit code" + tail(first))
        path = self.scratch.state_path()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("[]")
        resumed = self.scratch.mitosis(*self.scratch.flags("--no-push", "--resume"))
        self.assertEqual(resumed.returncode, 3, "resumed run exit code" + tail(resumed))
        self.assertIn(path, resumed.stderr)
        self.assertIn("h9-mitosis-state", resumed.stderr)
        self.assertNotIn("Traceback", resumed.stderr)
        self.assertEqual(json_lines(resumed.stdout), [])
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "[]")

    def a_state_write_that_fails_h9_stops_the_run_with_exit_15(self):
        handoff = module("handoff")
        mitosis = module("mitosis")
        self.scratch = Scratch()
        self.assertEqual(handoff.errors("h12-interphase-items", self.scratch.items), ())
        real = handoff.errors

        def refusing(schema, value, *rest, **named):
            if schema == "h9-mitosis-state":
                return ("#/plan_id required: test",)
            return real(schema, value, *rest, **named)

        with unittest.mock.patch.dict(os.environ, self.scratch.env, clear=True), working_directory(
            self.scratch.repo
        ), unittest.mock.patch.object(handoff, "errors", side_effect=refusing):
            code, out, err = captured(lambda: mitosis.main(self.scratch.flags("--no-push")))
        self.assertEqual(code, 15, "mitosis.main exit code\n" + out[-4000:] + "\n" + err[-4000:])
        self.assertEqual(json_lines(out), [])
        for part in ("state.json", "mitosis", "h9-mitosis-state", "#/plan_id required"):
            self.assertIn(part, err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(self.scratch.state_path()))


SHIPPED_RECORD = {
    "branch": "mitosis/add-numbers",
    "base": "main",
    "remote": "origin",
    "pushed": "1" * 40,
    "pull_request": "",
    "stacking_exception": False,
    "unchanged": False,
    "title": "feat(calc): add numbers",
    "body": RENDER_BODY,
}


class ResultLine(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = os.path.realpath(self.folder.name)
        self.run_dir = os.path.join(self.root, "run")

    def an_invalid_result_line_is_never_printed(self):
        mitosis = module("mitosis")
        plan = calc_plan(self.root)
        self.assertEqual(len(plan["msps"]), 1)
        state = {"msps": {"0": {"state": "shipped", "reason": None, "ship": dict(SHIPPED_RECORD)}}}
        code, out, err = captured(lambda: mitosis._summarised(plan, state, self.run_dir, 0))
        self.assertEqual(code, 15)
        self.assertEqual(json_lines(out), [])
        for part in ("result line", "mitosis", "h8-mitosis-result", "#/msps/0/pull_request"):
            self.assertIn(part, err)

    def the_result_line_keeps_every_line_of_a_reason(self):
        mitosis = module("mitosis")
        plan = calc_plan(self.root)
        state = {
            "lanes": {"0": {"state": "failed", "reason": "first line\nsecond line"}},
            "msps": {"0": {"state": "blocked", "reason": "held\nby lane 0"}},
        }
        summary = mitosis.summary(plan, state, self.run_dir, 10)
        self.assertEqual(summary["lanes"][0]["reason"], "first line\nsecond line")
        self.assertEqual(summary["msps"][0]["reason"], "held\nby lane 0")
        failed = [
            finding
            for finding in summary["findings"]
            if finding["kind"] == "failed" and finding["lane"] == 0
        ]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["detail"], "first line\nsecond line")


class OutcomeAndLock(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = os.path.realpath(self.folder.name)

    def an_invalid_outcome_entry_is_never_appended(self):
        handoff = module("handoff")
        outcomes = module("outcomes")
        path = os.path.join(self.root, "store", "trajectory.jsonl")
        valid = outcomes.entry(
            "mitosis",
            "run-one",
            "demo",
            "0" * 40,
            {
                "file": "tests/test_calc.py",
                "test": "Calc.adds",
                "outcome": "pass",
                "reason": None,
                "check": "C1.1",
            },
            0.25,
        )
        self.assertEqual(handoff.errors("k6-outcome", valid), ())
        invalid = {
            **copy.deepcopy(valid),
            "tags": [tag for tag in valid["tags"] if not tag.startswith("minutes:")],
        }
        with self.assertRaises(handoff.HandoffError) as caught:
            outcomes.append(path, [valid, invalid])
        for part in (path, "mitosis", "k6-outcome", "#/tags minContains"):
            self.assertIn(part, str(caught.exception))
        self.assertFalse(os.path.exists(path))

    def an_invalid_lock_record_is_never_returned(self):
        handoff = module("handoff")
        run = module("run")
        repo = os.path.join(self.root, "repo")
        env = {**os.environ, **GIT_IDENTITY, "HOME": self.root}
        for args in (
            ["git", "init", "-q", "-b", "main", repo],
            ["git", "-C", repo, "commit", "-q", "--allow-empty", "-m", "root"],
        ):
            subprocess.run(
                args, check=True, capture_output=True, env=env, stdin=subprocess.DEVNULL, timeout=CALL_LIMIT
            )
        commit = subprocess.run(
            ["git", "-C", repo, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=CALL_LIMIT,
        ).stdout.strip()
        with self.assertRaises(handoff.HandoffError) as caught:
            run.locked_record(repo, "main", commit, [])
        for part in ("mitosis", "k2-locked", "#/locked minItems"):
            self.assertIn(part, str(caught.exception))


VALID_FACTS = {
    "title": "feat(calc): add numbers",
    "problem": ["Sums were wrong."],
    "change": ["Add numbers."],
    "impact": ["Sums are right."],
    "testing": [{"check": "tests/test_calc.py", "result": "passed"}],
}

PRINTING = r'''
import sys

sys.stdout.write(%r)
'''

MARKING = r'''
import sys

with open(%r, "w", encoding="utf-8") as handle:
    handle.write("ran\n")
sys.stdout.write(%r)
'''


class RenderFields(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = os.path.realpath(self.folder.name)

    def _render(self, command, facts, path):
        pr_facts = module("pr_facts")
        offered = {
            "command": command,
            "facts": facts,
            "path": path,
            "timeout": CALL_LIMIT,
            "env": dict(os.environ),
        }
        taken = inspect.signature(pr_facts.render).parameters
        return pr_facts.render(**{name: value for name, value in offered.items() if name in taken})

    def _printing(self, name, text):
        return executable(os.path.join(self.root, "stubs", name), PRINTING % text)

    def invalid_facts_are_never_written_and_render_never_runs(self):
        pr_facts = module("pr_facts")
        marker = os.path.join(self.root, "rendered")
        command = executable(
            os.path.join(self.root, "stubs", "render"),
            MARKING % (marker, json.dumps(RENDER_REPLY) + "\n"),
        )
        facts = {key: value for key, value in VALID_FACTS.items() if key != "testing"}
        path = os.path.join(self.root, "facts", "fields.json")
        with self.assertRaises(pr_facts.RenderError) as caught:
            self._render(command, facts, path)
        for part in (path, "h15-pr-fields", "testing"):
            self.assertIn(part, str(caught.exception))
        self.assertFalse(os.path.exists(path))
        self.assertFalse(os.path.exists(marker))

    def a_render_reply_meeting_h16_gives_its_title_and_body(self):
        handoff = module("handoff")
        self.assertEqual(handoff.errors("h16-pr-render", RENDER_REPLY), ())
        self.assertEqual(handoff.errors("h15-pr-fields", VALID_FACTS), ())
        command = self._printing("render", json.dumps(RENDER_REPLY) + "\n")
        rendered = self._render(command, VALID_FACTS, os.path.join(self.root, "fields.json"))
        self.assertEqual(rendered, ("feat(calc): add numbers", RENDER_BODY))

    def a_render_reply_outside_h16_is_refused_naming_render(self):
        handoff = module("handoff")
        pr_facts = module("pr_facts")
        self.assertEqual(handoff.errors("h15-pr-fields", VALID_FACTS), ())
        valid_line = json.dumps(RENDER_REPLY)
        cases = (
            ("extra", json.dumps({**RENDER_REPLY, "extra": 1}) + "\n", "additionalProperties"),
            ("title", json.dumps({**RENDER_REPLY, "title": "Add numbers"}) + "\n", "#/title"),
            ("trailing", valid_line + "\ndone\n", None),
            ("prose", "rendered\n", None),
            ("empty", "", None),
        )
        for name, printed, expected in cases:
            command = self._printing(name, printed)
            with self.assertRaises(pr_facts.RenderError, msg=name) as caught:
                self._render(command, VALID_FACTS, os.path.join(self.root, name + ".json"))
            message = str(caught.exception)
            self.assertIn("pull-request render", message, name)
            self.assertIn("h16-pr-render", message, name)
            if expected is not None:
                self.assertIn(expected, message, name)


class GateResult(unittest.TestCase):
    def an_unjudged_property_is_inconclusive_with_a_reason(self):
        run = module("run")
        result = run._gate_result(
            [
                {
                    "step": "add-numbers",
                    "file": "tests/test_calc.py",
                    "test": "Calc.adds",
                    "outcome": None,
                    "reason": None,
                }
            ],
            [],
            "0" * 40,
            [],
        )
        judged = result["properties"][0]
        self.assertEqual(judged["outcome"], "inconclusive")
        self.assertIsInstance(judged["reason"], str)
        self.assertTrue(judged["reason"].strip())
        self.assertIn("never", judged["reason"])
        self.assertEqual(judged["seconds"], 0)
        self.assertEqual(result["counts"]["inconclusive"], 1)


class Loader(unittest.TestLoader):
    def getTestCaseNames(self, case):
        return sorted(
            name
            for name in vars(case)
            if not name.startswith("_")
            and callable(getattr(case, name))
            and name not in ("setUp", "tearDown")
        )


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case in (StateFile, ResultLine, OutcomeAndLock, RenderFields, GateResult):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
