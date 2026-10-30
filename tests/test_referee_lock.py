import hashlib
import importlib
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

import contract_schema

MITOSIS = os.path.join(ROOT, "mitosis.py")
K2_SCHEMA = os.path.join(HERE, "fixtures", "contracts", "k2-locked.schema.json")
LOCK_CASES = os.path.join(HERE, "fixtures", "receipt-lock-cases.json")
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
LOCKED = "tests/test_locked.py"
LOCKED_TEXT = "import unittest\n"
LOCKED_CHARTER = "# Charter\n\n- Python 3.9 or later.\n- Locked: tests/test_locked.py\n"
PLAIN_CHARTER = "# Charter\n\n- Python 3.9 or later.\n"

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


class RefereeLock(unittest.TestCase):
    def setUp(self):
        self.scratch = None

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()

    def _scratch(self, seeded=None):
        self.scratch = Scratch(seeded)
        return self.scratch

    def _module(self, name):
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as missing:
            if missing.name != name:
                raise
            self.fail("mitosis has no %s module: %s" % (name, missing))

    def _msp(self, summary):
        self.assertTrue(summary.get("msps"), "the JSON summary lists no MSP")
        return summary["msps"][0]

    def _reconcile(self, msp):
        reconcile = msp.get("reconcile") or {}
        self.assertIn("referee", reconcile, "the MSP's reconcile object carries no referee list")
        self.assertIn("fatal", reconcile, "the MSP's reconcile object carries no fatal flag")
        return reconcile

    def plan_only_refuses_a_step_writing_a_locked_path(self):
        scratch = self._scratch()
        charter = scratch.file("charter.md", LOCKED_CHARTER)
        items = scratch.items([step("build", ["calc.py", LOCKED], "demo")])
        completed = scratch.mitosis(
            "--items", items, "--charter", charter, "--plan-only", "--run-dir", scratch.run_dir
        )
        self.assertEqual(completed.returncode, 3, "mitosis exit code" + tail(completed))
        self.assertIn("build", completed.stderr)
        self.assertIn(LOCKED, completed.stderr)
        self.assertEqual(scratch.git("branch", "--list", "mitosis/demo").stdout, "")

    def plan_only_refuses_each_referee_marker_path(self):
        scratch = self._scratch()
        charter = scratch.file("charter.md", PLAIN_CHARTER)
        for index, path in enumerate(
            (
                "tests/conftest.py",
                "tests/__snapshots__/calc.snap",
                "tests/perf_baseline.json",
                "tests/allowlist.txt",
                "tests/skiplist.txt",
            )
        ):
            with self.subTest(path=path):
                items = scratch.items(
                    [step("build", ["calc.py", path], "demo")], "items-%d.json" % index
                )
                completed = scratch.mitosis(
                    "--items",
                    items,
                    "--charter",
                    charter,
                    "--plan-only",
                    "--run-dir",
                    os.path.join(scratch.tmp, "runs", "marker-%d" % index),
                )
                self.assertEqual(completed.returncode, 3, "mitosis exit code" + tail(completed))
                self.assertIn(path, completed.stderr)

    def plan_only_reports_the_locked_list_as_k2(self):
        scratch = self._scratch({LOCKED: LOCKED_TEXT})
        charter = scratch.file("charter.md", LOCKED_CHARTER)
        items = scratch.items([step("build", ["calc.py", "src/auth/ip_allowlist.py"], "demo")])
        completed = scratch.mitosis(
            "--items",
            items,
            "--charter",
            charter,
            "--plan-only",
            "--feature-branch",
            "feature",
            "--run-dir",
            scratch.run_dir,
        )
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        summary = summary_of(completed)
        self.assertIn("referee", summary, "the JSON summary carries no referee object")
        referee = summary["referee"]
        self.assertIsNotNone(referee, "the JSON summary's referee object is null")
        self.assertEqual(contract_schema.errors(referee, contract_schema.load(K2_SCHEMA)), [])
        self.assertEqual(referee["locked"], [LOCKED])
        self.assertEqual(referee["commit"], scratch.checked("rev-parse", "feature").strip())
        self.assertEqual(
            referee["receipt_lock"],
            hashlib.sha256(
                "file:tests/test_locked.py\nimport unittest\n\n".encode("utf-8")
            ).hexdigest(),
        )

    def a_locked_acceptance_file_is_read_only_not_refused(self):
        scratch = self._scratch({LOCKED: LOCKED_TEXT})
        charter = scratch.file("charter.md", LOCKED_CHARTER)
        items = scratch.items(
            [
                step(
                    "build",
                    ["calc.py", LOCKED],
                    "demo",
                    acceptance=[{"file": LOCKED, "test": "Locked.works"}],
                )
            ]
        )
        completed = scratch.mitosis(
            "--items",
            items,
            "--charter",
            charter,
            "--plan-only",
            "--feature-branch",
            "feature",
            "--run-dir",
            scratch.run_dir,
        )
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        summary = summary_of(completed)
        self.assertIn("referee", summary, "the JSON summary carries no referee object")
        self.assertIsNotNone(summary["referee"], "the JSON summary's referee object is null")
        with open(os.path.join(scratch.run_dir, "plan.json"), encoding="utf-8") as handle:
            plan = json.load(handle)
        for kind in ("lanes", "msps"):
            for entry in plan.get(kind) or []:
                self.assertNotIn(LOCKED, entry.get("files") or [], "%s entry %r" % (kind, entry))
        for brief in plan.get("briefs") or []:
            self.assertNotIn(LOCKED, brief.get("write_set") or [], "a Lane's write-set")

    def receipt_lock_matches_receipts_lock(self):
        referee = self._module("referee")
        with open(LOCK_CASES, encoding="utf-8") as handle:
            cases = json.load(handle)
        self.assertGreater(len(cases), 0)
        for case in cases:
            with self.subTest(paths=[f["path"] for f in case["files"]]):
                self.assertEqual(
                    referee.receipt_lock([(f["path"], f["content"]) for f in case["files"]]),
                    case["lock"],
                )

    def a_worker_write_to_a_locked_path_is_reverted_and_fatal(self):
        scratch = self._scratch({LOCKED: LOCKED_TEXT})
        charter = scratch.file("charter.md", LOCKED_CHARTER)
        items = scratch.items([step("build", ["calc.py"], "demo")])
        scratch.jobs(
            {
                "demo": {
                    "step": "build",
                    "writes": {
                        "calc.py": "def add(a, b):\n    return a + b\n",
                        LOCKED: "import unittest\nraise SystemExit(0)\n",
                    },
                    "declared": ["calc.py"],
                }
            }
        )
        completed = scratch.build(items, "--charter", charter)
        self.assertEqual(completed.returncode, 9, "mitosis exit code" + tail(completed))
        msp = self._msp(summary_of(completed))
        self.assertEqual(msp["state"], "referee-reverted")
        reconcile = self._reconcile(msp)
        self.assertEqual(reconcile["referee"], [LOCKED])
        self.assertIs(reconcile["fatal"], True)
        self.assertEqual(scratch.checked("show", "mitosis/demo:" + LOCKED), LOCKED_TEXT)

    def a_worker_write_to_a_referee_marker_path_is_reverted_and_fatal(self):
        scratch = self._scratch()
        charter = scratch.file("charter.md", PLAIN_CHARTER)
        items = scratch.items([step("build", ["calc.py"], "demo")])
        scratch.jobs(
            {
                "demo": {
                    "step": "build",
                    "writes": {
                        "calc.py": "def add(a, b):\n    return a + b\n",
                        "tests/conftest.py": 'collect_ignore = ["test_calc.py"]\n',
                    },
                    "declared": ["calc.py"],
                }
            }
        )
        completed = scratch.build(items, "--charter", charter)
        self.assertEqual(completed.returncode, 9, "mitosis exit code" + tail(completed))
        msp = self._msp(summary_of(completed))
        self.assertEqual(msp["state"], "referee-reverted")
        reconcile = self._reconcile(msp)
        self.assertEqual(reconcile["referee"], ["tests/conftest.py"])
        self.assertIs(reconcile["fatal"], True)
        self.assertNotEqual(
            scratch.git("cat-file", "-e", "mitosis/demo:tests/conftest.py").returncode, 0
        )

    def an_ordinary_undeclared_write_is_kept_and_not_fatal(self):
        scratch = self._scratch()
        charter = scratch.file("charter.md", PLAIN_CHARTER)
        items = scratch.items([step("build", ["calc.py"], "demo")])
        scratch.jobs(
            {
                "demo": {
                    "step": "build",
                    "writes": {
                        "calc.py": "def add(a, b):\n    return a + b\n",
                        "notes.txt": "notes\n",
                    },
                    "declared": ["calc.py"],
                }
            }
        )
        completed = scratch.build(items, "--charter", charter)
        self.assertEqual(completed.returncode, 6, "mitosis exit code" + tail(completed))
        msp = self._msp(summary_of(completed))
        reconcile = self._reconcile(msp)
        self.assertEqual(reconcile["referee"], [])
        self.assertIs(reconcile["fatal"], False)
        self.assertEqual(reconcile.get("undeclared"), ["notes.txt"])
        self.assertEqual(scratch.git("cat-file", "-e", "mitosis/demo:notes.txt").returncode, 0)


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            RefereeLock,
            (
                "plan_only_refuses_a_step_writing_a_locked_path",
                "plan_only_refuses_each_referee_marker_path",
                "plan_only_reports_the_locked_list_as_k2",
                "a_locked_acceptance_file_is_read_only_not_refused",
                "receipt_lock_matches_receipts_lock",
                "a_worker_write_to_a_locked_path_is_reverted_and_fatal",
                "a_worker_write_to_a_referee_marker_path_is_reverted_and_fatal",
                "an_ordinary_undeclared_write_is_kept_and_not_fatal",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
