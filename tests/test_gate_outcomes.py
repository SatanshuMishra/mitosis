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

K6_SCHEMA = os.path.join(HERE, "fixtures", "contracts", "k6-outcome.schema.json")
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

PR_FLAGS = (
    "--pr-type",
    "feat",
    "--pr-subject",
    "add the calculator",
    "--pr-why",
    "Callers need it.",
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
        shipping = (*PR_FLAGS, "--pr-command", self.stub("pr.py", "{branch}")) if push else ("--no-push",)
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


CORRECT_ADD = "def add(a, b):\n    return a + b\n"
SWAPPED_ADD = "def add(a, b):\n    return b + a\n"

EXACT_TEST = (
    "import unittest\n"
    "\n"
    "import calc\n"
    "\n"
    "\n"
    "class Calc(unittest.TestCase):\n"
    "    def adds(self):\n"
    "        self.assertTrue(calc.add(2, 3) == 5)\n"
)

BOTH_TESTS = (
    "import unittest\n"
    "\n"
    "import calc\n"
    "\n"
    "\n"
    "class Calc(unittest.TestCase):\n"
    "    def adds(self):\n"
    "        self.assertEqual(calc.add(2, 3), 5)\n"
    "\n"
    "    def adds_negatives(self):\n"
    "        self.assertEqual(calc.add(-2, -3), -5)\n"
)

ADDS = {"file": "tests/test_calc.py", "test": "Calc.adds"}
ADDS_NEGATIVES = {"file": "tests/test_calc.py", "test": "Calc.adds_negatives"}


class GateOutcomes(unittest.TestCase):
    def setUp(self):
        self.scratch = None

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()

    def _store(self):
        return os.path.join(self.scratch.tmp, "store", "trajectories.jsonl")

    def _entries(self, store):
        if not os.path.exists(store):
            return []
        with open(store, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines() if line.strip()]

    def _valid(self, entry):
        self.assertIn("id", entry, "the trajectory line carries no id")
        self.assertIn("ts", entry, "the trajectory line carries no ts")
        bare = {key: value for key, value in entry.items() if key not in ("id", "ts")}
        self.assertEqual(contract_schema.errors(bare, contract_schema.load(K6_SCHEMA)), [])
        return bare

    def _gate(self, completed):
        summary = summary_of(completed)
        self.assertTrue(summary.get("msps"), "the JSON summary lists no MSP" + tail(completed))
        return summary["msps"][0].get("gate") or {}

    def _passing_run(self, acceptance):
        self.scratch = Scratch(run_name="run-one")
        items = self.scratch.items([step("add", ["calc.py", "tests/test_calc.py"], "demo", acceptance)])
        self.scratch.jobs(
            {"demo": {"step": "add", "writes": {"calc.py": CORRECT_ADD, "tests/test_calc.py": BOTH_TESTS}}}
        )
        return self.scratch.build(items, "--trajectory", self._store())

    def each_gated_acceptance_test_appends_one_k6_entry(self):
        completed = self._passing_run([ADDS, ADDS_NEGATIVES])
        entries = self._entries(self._store())
        self.assertEqual(len(entries), 2, "trajectory lines" + tail(completed))
        commit = self._gate(completed).get("commit")
        self.assertRegex(commit or "", "^[0-9a-f]{40}$", "the summary's gate carries no commit")
        bare = [self._valid(entry) for entry in entries]
        for entry in bare:
            self.assertEqual(entry["surface_key"], "run-one/demo")
            self.assertEqual(entry["outcome"], "fixed")
            for tag in ("kind:gate", "run:run-one", "msp:demo", "usd:0", "commit:" + commit):
                self.assertIn(tag, entry["tags"])
        self.assertEqual(
            sorted(entry["surface"] for entry in bare),
            ["tests/test_calc.py::Calc.adds", "tests/test_calc.py::Calc.adds_negatives"],
        )

    def a_property_that_does_not_pass_appends_unverified_reasoned(self):
        self.scratch = Scratch({"calc.py": CORRECT_ADD})
        items = self.scratch.items([step("add", ["calc.py", "tests/test_calc.py"], "demo", [ADDS])])
        self.scratch.jobs(
            {"demo": {"step": "add", "writes": {"calc.py": SWAPPED_ADD, "tests/test_calc.py": EXACT_TEST}}}
        )
        completed = self.scratch.build(items, "--trajectory", self._store(), push=True)
        entries = self._entries(self._store())
        self.assertEqual(len(entries), 1, "trajectory lines" + tail(completed))
        entry = self._valid(entries[0])
        self.assertEqual(entry["outcome"], "unverified-reasoned")
        gate = self._gate(completed)
        self.assertTrue(gate.get("properties"), "the MSP's gate object carries no properties")
        self.assertEqual(entry.get("symptom"), gate["properties"][0]["reason"])

    def an_acceptance_entry_with_a_check_id_carries_its_check_tag(self):
        completed = self._passing_run([{**ADDS, "check": "C1.1"}])
        entries = self._entries(self._store())
        self.assertEqual(len(entries), 1, "trajectory lines" + tail(completed))
        entry = self._valid(entries[0])
        self.assertIn("check:C1.1=pass", entry["tags"])


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            GateOutcomes,
            (
                "each_gated_acceptance_test_appends_one_k6_entry",
                "a_property_that_does_not_pass_appends_unverified_reasoned",
                "an_acceptance_entry_with_a_check_id_carries_its_check_tag",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
