import datetime
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_check_author_lane import saved_plan

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MITOSIS = os.path.join(ROOT, "mitosis.py")
CALL_LIMIT = 60
RUN_LIMIT = 900
STRIPPED = (
    "CLAUDE_CODE_SESSION_ID",
    "PLUGIN_METRICS_PARENT",
    "SHIP",
    "PAGER",
    "GH_PROMPT_DISABLED",
    "MITOSIS_TRAJECTORY_STORE",
)
WORKER_VARIABLES = (
    "MITOSIS_BRANCH",
    "MITOSIS_CLUSTER",
    "MITOSIS_ITEM",
    "MITOSIS_ITEMS",
    "MITOSIS_PLAN_ID",
    "MITOSIS_TIER",
    "MITOSIS_FILES",
)
GIT_IDENTITY = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "mitosis test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "mitosis test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}
SPEC_SECTION = "1.1"
SPEC_DOCUMENT = "# Spec\n\n## 1.1 Build\n"

WORKER = r'''
import json
import os
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
tmp = os.path.dirname(here)
prompt = sys.argv[1]
session = sys.argv[2]
sys.stderr.write(prompt + "\n")
for key in sorted(os.environ):
    if key.startswith("MITOSIS_") and key != "MITOSIS_TRAJECTORY_STORE":
        sys.stderr.write("%s=%s\n" % (key, os.environ[key]))
sys.stderr.flush()


def load(name):
    with open(os.path.join(here, name), encoding="utf-8") as handle:
        return json.load(handle)


def replied(reply):
    sys.stdout.write(json.dumps({"type": "result", "session_id": session, "result": "done", "structured_output": reply}) + "\n")


def put(path, content):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)


def listing():
    found = []
    for folder, folders, files in os.walk("."):
        folders[:] = sorted(name for name in folders if name != ".git")
        found.extend(os.path.relpath(os.path.join(folder, name), ".") for name in files)
    return sorted(found)


jobs = load("worker.json")
control = load("control.json")
named = sorted((name for name in jobs if ("task-" + name) in prompt), key=len)
if not named:
    replied({"item": "unknown", "status": "failed", "files_changed": [], "notes": "no job"})
    sys.exit(1)
name = named[-1]
put(os.path.join(tmp, "started-" + name), "\n".join(listing()) + "\n")
job = control.get(name) or {}
time.sleep(job.get("sleep", 0))
if "stdout" in job:
    sys.stdout.write(job["stdout"])
    sys.exit(0)
writes = jobs[name]
for path, content in sorted(writes.items()):
    put(path, content)
status = job.get("status", "ok")
replied({"item": name, "status": status, "files_changed": sorted(writes), "notes": status})
sys.exit(0)
'''

REASK = r'''
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "control.json"), encoding="utf-8") as handle:
    control = json.load(handle)
if "reask_stdout" not in control:
    sys.stderr.write("no re-ask was expected\n")
    sys.exit(1)
sys.stdout.write(control["reask_stdout"])
sys.exit(0)
'''

ACCEPT = r'''
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "control.json"), encoding="utf-8") as handle:
    control = json.load(handle)
path = control.get("accept_path")
if path is None:
    sys.exit(2)
if not os.path.isfile(path):
    sys.exit(1)
with open(path, encoding="utf-8") as handle:
    sys.exit(0 if handle.read().strip() else 1)
'''

STRUCTURER = r'''
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
prompt = sys.stdin.read()
with open(os.path.join(here, "structure-prompt.txt"), "w", encoding="utf-8") as handle:
    handle.write(prompt)
with open(os.path.join(here, "structure-reply.json"), encoding="utf-8") as handle:
    reply = json.load(handle)
print(json.dumps({"type": "result", "session_id": sys.argv[1], "result": "done", "structured_output": {"reply": reply}}))
'''


def S(name, files, **extra):
    return {
        "name": name,
        "task": "task-" + name,
        "files": list(files),
        "acceptance": [],
        "spec_ref": [SPEC_SECTION],
        **extra,
    }


def check_author(name, files, msp):
    return {
        "name": name,
        "task": "task-" + name,
        "role": "check-author",
        "msp": msp,
        "files": list(files),
        "acceptance": [],
        "spec_ref": [SPEC_SECTION],
    }


def node_link(edges):
    files = sorted({path for edge in edges for path in edge})
    return {
        "nodes": [{"id": path, "source_file": path} for path in files],
        "links": [{"source": source, "target": target} for source, target in edges],
    }


def put(path, content):
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content.encode("utf-8"))
    return path


def put_json(path, value):
    return put(path, json.dumps(value))


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def tail(completed):
    return "\n--- exit %s ---\n--- stdout ---\n%s\n--- stderr ---\n%s" % (
        completed.returncode,
        completed.stdout[-3000:],
        completed.stderr[-3000:],
    )


def summary_of(completed):
    lines = completed.stdout.splitlines()
    if not lines:
        return {}
    try:
        parsed = json.loads(lines[-1])
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def kept_environment():
    return {
        key: value
        for key, value in os.environ.items()
        if key not in STRIPPED and not key.startswith("GIT_") and key not in WORKER_VARIABLES
    }


def contract_errors(name, value):
    folder = os.path.join(ROOT, "contracts")
    spec = importlib.util.spec_from_file_location("accept_contract_validator", os.path.join(folder, "validator.py"))
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    return list(validator.errors_against(folder, name, value))


def moment(text):
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))


class Scratch:
    def __init__(self, seeded=None):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="mitosis-accept-"))
        self.repo = os.path.join(self.tmp, "repo")
        self.remote = os.path.join(self.tmp, "remote.git")
        self.stubs = os.path.join(self.tmp, "stubs")
        self.run_dir = os.path.join(self.tmp, "run")
        self.no_history = put(os.path.join(self.tmp, "no-history.jsonl"), "")
        self.env = {
            **kept_environment(),
            **GIT_IDENTITY,
            "HOME": os.path.join(self.tmp, "home"),
            "PLUGIN_METRICS_DIR": os.path.join(self.tmp, "metrics"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "MITOSIS_TRAJECTORY_STORE": self.no_history,
        }
        self.labels = 0
        os.makedirs(self.env["HOME"])
        os.makedirs(self.repo)
        os.makedirs(self.stubs)
        self.checked("init", "-q", "-b", "main", self.repo)
        self.checked("init", "-q", "--bare", self.remote)
        put(os.path.join(self.repo, "README.md"), "seed\n")
        put(os.path.join(self.repo, ".gitignore"), "__pycache__/\n")
        for path, content in sorted((seeded or {}).items()):
            put(os.path.join(self.repo, path), content)
        self.checked("-C", self.repo, "add", "-A")
        self.checked("-C", self.repo, "commit", "-q", "-m", "seed")
        self.checked("-C", self.repo, "branch", "feature")
        self.checked("-C", self.repo, "remote", "add", "origin", self.remote)
        self.spec = put(os.path.join(self.tmp, "spec.md"), SPEC_DOCUMENT)
        put(os.path.join(self.stubs, "worker.py"), WORKER)
        put(os.path.join(self.stubs, "reask.py"), REASK)
        put(os.path.join(self.stubs, "accept.py"), ACCEPT)
        put(os.path.join(self.stubs, "structurer.py"), STRUCTURER)
        put_json(os.path.join(self.stubs, "control.json"), {})
        put_json(os.path.join(self.stubs, "worker.json"), {})

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.repo,
            env=self.env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=CALL_LIMIT,
        )

    def checked(self, *args, cwd=None):
        completed = self.git(*args, cwd=cwd)
        if completed.returncode != 0:
            raise RuntimeError("scaffold git %s failed: %s" % (" ".join(args), completed.stderr))
        return completed.stdout

    def path(self, *parts):
        return os.path.join(self.tmp, *parts)

    def stub(self, script, *words):
        return " ".join(
            [shlex.quote(sys.executable), shlex.quote(os.path.join(self.stubs, script))] + list(words)
        )

    def items(self, steps):
        self.labels += 1
        source = {
            "path": self.spec,
            "sha256": hashlib.sha256(SPEC_DOCUMENT.encode("utf-8")).hexdigest(),
        }
        owned = [{**step, "source": step.get("source") or source} for step in steps]
        return put_json(self.path("items-%d.json" % self.labels), owned)

    def mitosis(self, *flags, cwd=None, env=None):
        return subprocess.run(
            [sys.executable, MITOSIS, *flags],
            cwd=cwd or self.repo,
            env={**self.env, **(env or {})},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=RUN_LIMIT,
        )

    def saved_flags(self, items, flags, saved):
        return ("--plan-file", saved_plan(items, self.repo, *flags)) if saved else ()

    def plan_only(self, steps, *flags, cwd=None, run_dir=None, env=None, saved=False):
        items = self.items(steps)
        return self.mitosis(
            "--items",
            items,
            "--plan-only",
            "--feature-branch",
            "feature",
            "--run-dir",
            run_dir or self.run_dir,
            *self.saved_flags(items, flags, saved),
            *flags,
            cwd=cwd,
            env=env,
        )

    def full_run(self, steps, *flags, control=None, writes=None, run_dir=None, env=None, saved=False):
        jobs = {
            step["name"]: {path: "work of %s\n" % step["name"] for path in step["files"]}
            for step in steps
        }
        put_json(os.path.join(self.stubs, "worker.json"), {**jobs, **(writes or {})})
        put_json(os.path.join(self.stubs, "control.json"), control or {})
        items = self.items(steps)
        return self.mitosis(
            "--items",
            items,
            "--run-dir",
            run_dir or self.run_dir,
            "--feature-branch",
            "feature",
            "--dispatch-command",
            self.stub("worker.py", "{task}", "{session_id}", "{schema}"),
            "--reask-command",
            self.stub("reask.py", "{prompt}", "{session_id}", "{schema}"),
            "--acceptance-command",
            self.stub("accept.py", "{file}", "{test}"),
            "--no-push",
            *self.saved_flags(items, flags, saved),
            *flags,
            env=env,
        )


class Case(unittest.TestCase):
    def setUp(self):
        self.scratch = self.make_scratch()
        self.addCleanup(self.scratch.close)

    def make_scratch(self):
        return Scratch()

    def exits(self, completed, code):
        self.assertEqual(completed.returncode, code, tail(completed))

    def loaded(self, path, completed):
        self.assertTrue(os.path.isfile(path), "%s was not written" % path + tail(completed))
        return read_json(path)

    def plan(self, completed, run_dir=None):
        return self.loaded(os.path.join(run_dir or self.scratch.run_dir, "plan.json"), completed)

    def state(self, completed, run_dir=None):
        return self.loaded(os.path.join(run_dir or self.scratch.run_dir, "state.json"), completed)

    def planned(self, steps, *flags, **options):
        completed = self.scratch.plan_only(steps, *flags, **options)
        self.exits(completed, 0)
        return completed, self.plan(completed, options.get("run_dir"))

    def lane_index(self, plan, name, completed):
        found = [index for index, lane in enumerate(plan.get("lanes") or []) if name in lane.get("steps", [])]
        self.assertEqual(len(found), 1, "no single Lane holds %s" % name + tail(completed))
        return found[0]

    def lane(self, name, completed, run_dir=None):
        plan = self.plan(completed, run_dir)
        state = self.state(completed, run_dir)
        index = self.lane_index(plan, name, completed)
        record = (state.get("lanes") or {}).get(str(index))
        self.assertIsInstance(record, dict, "state.json has no record of Lane %d" % index + tail(completed))
        return record

    def stderr_of(self, record, completed):
        path = record.get("stderr")
        self.assertIsInstance(path, str, "the Lane record names no stderr file" + tail(completed))
        self.assertTrue(os.path.isfile(path), "%s does not exist" % path + tail(completed))
        return read_text(path)


class EntryDirectory(Case):
    def make_scratch(self):
        return Scratch(seeded={"app/a.py": "value = 1\n"})

    def a_plan_run_from_a_subdirectory_reads_paths_from_the_repository_root(self):
        completed = self.scratch.plan_only(
            [S("a", ["app/a.py"])], cwd=os.path.join(self.scratch.repo, "app")
        )
        self.exits(completed, 0)
        plan = self.plan(completed)
        self.assertEqual((plan.get("counts") or {}).get("missing_paths"), 0, tail(completed))

    def a_relative_run_dir_is_read_against_the_repository_root(self):
        completed = self.scratch.plan_only(
            [S("a", ["app/a.py"])],
            cwd=os.path.join(self.scratch.repo, "app"),
            run_dir=os.path.join("runs", "r1"),
        )
        self.exits(completed, 0)
        self.assertTrue(
            os.path.isfile(os.path.join(self.scratch.repo, "runs", "r1", "plan.json")), tail(completed)
        )
        self.assertFalse(os.path.exists(os.path.join(self.scratch.repo, "app", "runs")), tail(completed))


class BriefFormat(Case):
    def a_lane_brief_carries_a_task_line_and_json_spec(self):
        completed, plan = self.planned([S("a", ["a.py"], task="Build a")])
        briefs = plan.get("briefs") or []
        self.assertTrue(briefs, "plan.json holds no briefs" + tail(completed))
        text = briefs[0].get("text") or ""
        leaves = json.dumps(
            {"leaves": [{"name": "a", "edit": ["a.py"], "read": []}]}, indent=2
        )
        self.assertTrue(text.startswith("Lane 0 of MSP a\n"), text)
        self.assertIn("Task: Build a", text.splitlines(), text)
        self.assertIn(leaves, text)
        self.assertIn("Edit ONLY the files listed under `edit`.", text)
        self.assertNotIn("Write-set for this Lane", text)


class IntraMspScheduling(Case):
    def a_finished_lane_lands_and_the_next_starts_while_a_sibling_runs(self):
        steps = [S("a", ["a.py"], msp="m"), S("b", ["b.py"], msp="m"), S("c", ["c.py"], msp="m")]
        completed = self.scratch.full_run(steps, "--concurrency", "2", control={"a": {"sleep": 4}})
        self.exits(completed, 0)
        records = {name: self.lane(name, completed) for name in ("a", "b", "c")}
        self.assertEqual({name: record.get("state") for name, record in records.items()}, dict.fromkeys("abc", "ok"))
        self.assertLess(moment(records["c"]["started"]), moment(records["a"]["finished"]), tail(completed))
        branch = ((self.state(completed).get("worktrees") or [{}])[0]).get("branch")
        self.assertIsInstance(branch, str, tail(completed))
        subjects = self.scratch.checked("log", "--reverse", "--format=%s", "feature.." + branch).splitlines()
        named = [
            found[0]
            for found in (
                [name for name in ("a", "b", "c") if re.search(r"(?<![\w-])%s(?![\w-])" % name, subject)]
                for subject in subjects
            )
            if len(found) == 1
        ]
        self.assertEqual(named, ["b", "c", "a"], "\n".join(subjects))


class CrossMspStart(Case):
    def steps(self):
        return [
            S("p", ["p.py"], msp="P"),
            S("q", ["q.py"], msp="P", after=["p"]),
            S("c", ["c.py"], msp="C", after=["p"]),
        ]

    def a_consumer_starts_only_after_its_producers_whole_msp_landed(self):
        completed = self.scratch.full_run(self.steps(), "--concurrency", "3", control={"q": {"sleep": 4}})
        consumer = self.lane("c", completed)
        producer = self.lane("q", completed)
        self.assertEqual(consumer.get("state"), "ok", tail(completed))
        self.assertGreaterEqual(moment(consumer["started"]), moment(producer["finished"]), tail(completed))
        started = self.scratch.path("started-c")
        self.assertTrue(os.path.isfile(started), tail(completed))
        self.assertIn("q.py", read_text(started).splitlines())

    def a_consumer_of_an_msp_with_a_failed_lane_is_blocked_unrun(self):
        completed = self.scratch.full_run(self.steps(), control={"q": {"status": "failed"}})
        producer = self.lane("q", completed)
        consumer = self.lane("c", completed)
        self.assertEqual(producer.get("state"), "failed", tail(completed))
        self.assertEqual(consumer.get("state"), "blocked", tail(completed))
        self.assertEqual(consumer.get("reason"), "dependencies can no longer be satisfied", tail(completed))
        self.assertIsNone(consumer.get("stdout"), tail(completed))
        self.assertFalse(os.path.exists(self.scratch.path("started-c")), tail(completed))


class SerialVerifyMode(Case):
    def make_scratch(self):
        return Scratch(seeded={"tests/test_x.py": "import unittest\n"})

    def a_serial_step_is_gated_like_any_other(self):
        steps = [
            S(
                "x",
                ["auth/x.py"],
                complexity="simple",
                acceptance=[{"file": "tests/test_x.py", "test": "x_works"}],
            )
        ]
        completed = self.scratch.full_run(
            steps, "--serial-markers", "auth", control={"accept_path": "auth/x.py"}
        )
        self.exits(completed, 0)
        gate = (((self.state(completed).get("msps") or {}).get("0") or {}).get("gate")) or {}
        self.assertEqual((gate.get("counts") or {}).get("not-applicable"), 0, json.dumps(gate))
        outcomes = [
            prop.get("outcome")
            for prop in gate.get("properties") or []
            if prop.get("step") == "x" or "x_works" in json.dumps(prop)
        ]
        self.assertEqual(outcomes, ["pass"], json.dumps(gate))


class RunLog(Case):
    def a_run_log_holds_one_line_per_lane_with_the_plan_id(self):
        log = self.scratch.path("log.jsonl")
        completed = self.scratch.full_run([S("a", ["a.py"]), S("b", ["b.py"])], "--run-log", log)
        self.exits(completed, 0)
        plan_id = self.state(completed).get("plan_id")
        self.assertTrue(os.path.isfile(log), tail(completed))
        lines = read_text(log).splitlines()
        self.assertEqual(len(lines), 2, "\n".join(lines))
        entries = [json.loads(line) for line in lines]
        self.assertTrue(all(isinstance(entry, dict) for entry in entries), lines)
        self.assertEqual([entry.get("state") for entry in entries], ["ok", "ok"], lines)
        self.assertEqual([entry.get("plan_id") for entry in entries], [plan_id, plan_id], lines)
        self.assertEqual({entry.get("lane") for entry in entries}, {0, 1}, lines)


def load_tests(loader, standard, pattern):
    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            return sorted(
                name
                for name in vars(case)
                if not name.startswith("_") and callable(getattr(case, name)) and name not in ("steps", "make_scratch")
            )

    suite = unittest.TestSuite()
    for case in (EntryDirectory, BriefFormat, IntraMspScheduling, CrossMspStart, SerialVerifyMode, RunLog):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite
