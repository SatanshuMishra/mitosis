import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

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

WORKER = r'''
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "worker.json"), encoding="utf-8") as handle:
    job = json.load(handle)[sys.argv[1]]
with open(os.path.join(here, "dispatch.log"), "a", encoding="utf-8") as handle:
    handle.write(job["step"] + "\n")
for path, content in sorted(job.get("writes", {}).items()):
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)
limit = job.get("limit")
if limit:
    stream = sys.stdout if limit["stream"] == "stdout" else sys.stderr
    stream.buffer.write((limit["line"] + "\n").encode("utf-8"))
    stream.flush()
    sys.exit(1)
declared = job.get("declared", sorted(job.get("writes", {})))
print(json.dumps({"item": job["step"], "status": "ok", "files_changed": declared, "notes": "done"}))
'''

ACCEPT = r'''
import os
import subprocess
import sys

mode, file, test = sys.argv[1], sys.argv[2], sys.argv[3]
if mode != "unittest":
    sys.exit(2)
folder, name = os.path.split(file)
module = os.path.splitext(name)[0]
env = dict(os.environ, PYTHONPATH=os.pathsep.join([os.path.abspath(folder), os.getcwd()]))
try:
    code = subprocess.run(
        [sys.executable, "-m", "unittest", module + "." + test], env=env, timeout=120
    ).returncode
except subprocess.TimeoutExpired:
    sys.exit(2)
sys.exit(code if code in (0, 1) else 2)
'''

PULL_REQUEST = r'''
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(os.path.dirname(here), "pr.log"), "a", encoding="utf-8") as handle:
    handle.write(sys.argv[1] + "\n")
print("https://example.invalid/pull/1")
'''

RENDER = r'''
import json
print(json.dumps({"title": "feat: add the calculator", "body": "## Problem\nCallers need it."}))
'''

PR_FLAGS = (
    "--pr-type",
    "feat",
    "--pr-subject",
    "add the calculator",
    "--pr-why",
    "Callers need it.",
    "--pr-impact",
    "Callers can add.",
)


def put(path, content):
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content.encode("utf-8"))
    return path


def step(name, files, msp, acceptance=(), after=()):
    base = {
        "name": name,
        "task": "build " + name,
        "files": list(files),
        "source": None,
        "acceptance": list(acceptance),
        "msp": msp,
    }
    return {**base, "after": list(after)} if after else base


def tail(completed):
    return "\n--- stdout ---\n%s\n--- stderr ---\n%s" % (
        completed.stdout[-4000:],
        completed.stderr[-4000:],
    )


class Scratch:
    def __init__(self, seeded=None, run_name="run"):
        self.tmp = tempfile.mkdtemp(prefix="mitosis-referee-")
        self.repo = os.path.join(self.tmp, "repo")
        self.remote = os.path.join(self.tmp, "remote.git")
        self.stubs = os.path.join(self.tmp, "stubs")
        self.run_dir = os.path.join(self.tmp, "runs", run_name)
        self.pr_log = os.path.join(self.tmp, "pr.log")
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
        put(os.path.join(self.repo, "README.md"), "seed\n")
        put(os.path.join(self.repo, ".gitignore"), "__pycache__/\n")
        self.checked("-C", self.repo, "add", "-A")
        self.checked("-C", self.repo, "commit", "-q", "-m", "seed")
        self.checked("-C", self.repo, "checkout", "-q", "-b", "feature")
        if seeded:
            for path, content in seeded.items():
                put(os.path.join(self.repo, path), content)
            self.checked("-C", self.repo, "add", "-A")
            self.checked("-C", self.repo, "commit", "-q", "-m", "seed the feature branch")
        self.checked("-C", self.repo, "checkout", "-q", "main")
        self.checked("-C", self.repo, "remote", "add", "origin", self.remote)
        put(os.path.join(self.stubs, "worker.py"), WORKER)
        put(os.path.join(self.stubs, "accept.py"), ACCEPT)
        put(os.path.join(self.stubs, "pr.py"), PULL_REQUEST)
        self.render = put(os.path.join(self.stubs, "render.py"), "#!%s\n%s" % (sys.executable, RENDER))
        os.chmod(self.render, 0o755)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args):
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=CALL_LIMIT,
        )

    def checked(self, *args):
        completed = self.git(*args)
        if completed.returncode != 0:
            raise RuntimeError("scaffold git %s failed: %s" % (" ".join(args), completed.stderr))
        return completed.stdout

    def file(self, name, content):
        return put(os.path.join(self.tmp, name), content)

    def items(self, steps, name="items.json"):
        return self.file(name, json.dumps(steps))

    def jobs(self, jobs):
        return put(os.path.join(self.stubs, "worker.json"), json.dumps(jobs))

    def stub(self, script, *words):
        return " ".join(
            [shlex.quote(sys.executable), shlex.quote(os.path.join(self.stubs, script))]
            + list(words)
        )

    def mitosis(self, *flags):
        return subprocess.run(
            [sys.executable, MITOSIS, *flags],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=RUN_LIMIT,
        )

    def build(self, items, *extra, acceptance="unittest", push=False):
        shipping = (
            (*PR_FLAGS, "--pr-command", self.stub("pr.py", "{branch}"), "--render-command", self.render)
            if push
            else ("--no-push",)
        )
        return self.mitosis(
            "--items",
            items,
            "--run-dir",
            self.run_dir,
            "--feature-branch",
            "feature",
            "--dispatch-command",
            self.stub("worker.py", "{msp}"),
            "--acceptance-command",
            self.stub("accept.py", acceptance, "{file}", "{test}"),
            "--timeout",
            "120",
            *shipping,
            *extra,
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


HIT_LIMIT = "You've hit your limit · resets 3pm (America/Vancouver)"
LIMIT_REACHED = "Claude usage limit reached. Your limit will reset at 3pm"


def limited(name, writes, stream="stdout", line=HIT_LIMIT):
    return {"step": name, "writes": writes, "limit": {"stream": stream, "line": line}}


def finished(name, writes):
    return {"step": name, "writes": writes}


class UsageLimit(unittest.TestCase):
    def setUp(self):
        self.scratch = None

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()

    def _summary(self, completed):
        summary = summary_of(completed)
        self.assertTrue(summary.get("lanes"), "the JSON summary lists no Lane" + tail(completed))
        return summary

    def _dispatched(self):
        path = os.path.join(self.scratch.stubs, "dispatch.log")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return handle.read().splitlines()

    def _one_step(self, job):
        self.scratch = Scratch()
        items = self.scratch.items([step("build", ["calc.py"], "demo")])
        self.scratch.jobs({"demo": job})
        return self.scratch.build(items)

    def a_worker_printing_a_usage_limit_line_is_parked(self):
        completed = self._one_step(limited("build", {}))
        self.assertEqual(completed.returncode, 13, "mitosis exit code" + tail(completed))
        summary = self._summary(completed)
        self.assertEqual(summary["lanes"][0]["state"], "parked-limit")
        self.assertIn(HIT_LIMIT, summary["lanes"][0]["reason"] or "")
        self.assertEqual(summary["msps"][0]["state"], "parked")

    def a_usage_limit_line_on_stderr_also_parks(self):
        completed = self._one_step(limited("build", {}, stream="stderr", line=LIMIT_REACHED))
        self.assertEqual(completed.returncode, 13, "mitosis exit code" + tail(completed))
        self.assertEqual(self._summary(completed)["lanes"][0]["state"], "parked-limit")

    def no_lane_is_dispatched_after_a_usage_limit(self):
        self.scratch = Scratch()
        names = ("one", "two", "three")
        items = self.scratch.items([step(name, [name + ".py"], name) for name in names])
        self.scratch.jobs({name: limited(name, {}) for name in names})
        completed = self.scratch.build(items, "--concurrency", "1")
        self.assertEqual(completed.returncode, 13, "mitosis exit code" + tail(completed))
        self.assertEqual(len(self._dispatched()), 1, "dispatch.log lines")
        self.assertEqual(
            [lane["state"] for lane in self._summary(completed)["lanes"]],
            ["parked-limit", "parked-limit", "parked-limit"],
        )

    def a_resume_skips_every_ok_lane(self):
        self.scratch = Scratch()
        items = self.scratch.items(
            [step("a", ["a.py"], "one"), step("b", ["b.py"], "two", after=["a"])]
        )
        self.scratch.jobs(
            {
                "one": finished("a", {"a.py": "a = 1\n"}),
                "two": limited("b", {}),
            }
        )
        first = self.scratch.build(items)
        self.assertEqual(first.returncode, 13, "first run exit code" + tail(first))
        lanes = {tuple(lane["steps"]): lane["state"] for lane in self._summary(first)["lanes"]}
        self.assertEqual(lanes, {("a",): "ok", ("b",): "parked-limit"})
        self.scratch.jobs(
            {
                "one": finished("a", {"a.py": "a = 1\n"}),
                "two": finished("b", {"b.py": "b = 1\n"}),
            }
        )
        resumed = self.scratch.build(items, "--resume")
        self.assertEqual(resumed.returncode, 0, "resumed run exit code" + tail(resumed))
        self.assertEqual(self._dispatched(), ["a", "b", "b"])
        self.assertEqual(
            [lane["state"] for lane in self._summary(resumed)["lanes"]], ["ok", "ok"]
        )


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            UsageLimit,
            (
                "a_worker_printing_a_usage_limit_line_is_parked",
                "a_usage_limit_line_on_stderr_also_parks",
                "no_lane_is_dispatched_after_a_usage_limit",
                "a_resume_skips_every_ok_lane",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
