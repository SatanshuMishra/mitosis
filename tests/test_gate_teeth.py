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

G14_CASES = os.path.join(HERE, "fixtures", "g14-cases.json")
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


CORRECT_ADD = "def add(a, b):\n    return a + b\n"
BROKEN_ADD = "def add(a, b):\n    return a - b\n"
SWAPPED_ADD = "def add(a, b):\n    return b + a\n"

EXISTENCE_TEST = (
    "import unittest\n"
    "\n"
    "import calc\n"
    "\n"
    "\n"
    "class Calc(unittest.TestCase):\n"
    "    def adds(self):\n"
    "        self.assertTrue(callable(calc.add))\n"
)

SKIPPED_TEST = (
    "import unittest\n"
    "\n"
    "import calc\n"
    "\n"
    "\n"
    "class Calc(unittest.TestCase):\n"
    "    @unittest.skip(\"later\")\n"
    "    def adds(self):\n"
    "        self.assertTrue(calc.add(2, 3) == 5)\n"
)

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

LIMITS_TEST = (
    "import json\n"
    "import os\n"
    "import unittest\n"
    "\n"
    "ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
    "\n"
    "\n"
    "class Calc(unittest.TestCase):\n"
    "    def adds(self):\n"
    "        with open(os.path.join(ROOT, \"limits.json\"), encoding=\"utf-8\") as handle:\n"
    "            self.assertEqual(json.load(handle)[\"cap\"], 3)\n"
)

ADDS = {"file": "tests/test_calc.py", "test": "Calc.adds"}


def add_step(files=("calc.py", "tests/test_calc.py")):
    return step("add", files, "demo", acceptance=[ADDS])


class GateTeeth(unittest.TestCase):
    def setUp(self):
        self.scratch = None

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()

    def _module(self, name):
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as missing:
            if missing.name != name:
                raise
            self.fail("mitosis has no %s module: %s" % (name, missing))

    def _gated(self, writes, seeded=None, acceptance="unittest", files=("calc.py", "tests/test_calc.py")):
        self.scratch = Scratch(seeded)
        items = self.scratch.items([add_step(files)])
        self.scratch.jobs({"demo": {"step": "add", "writes": writes}})
        return self.scratch.build(items, acceptance=acceptance, push=True)

    def _first_property(self, msp):
        gate = msp.get("gate") or {}
        self.assertTrue(gate.get("properties"), "the MSP's gate object carries no properties")
        return gate["properties"][0]

    def _msp(self, completed):
        summary = summary_of(completed)
        self.assertTrue(summary.get("msps"), "the JSON summary lists no MSP" + tail(completed))
        return summary["msps"][0]

    def _held_back(self, completed, code, outcome):
        self.assertEqual(completed.returncode, code, "mitosis exit code" + tail(completed))
        msp = self._msp(completed)
        self.assertEqual(self._first_property(msp)["outcome"], outcome)
        self.assertEqual(msp["state"], "gate-failed")
        self.assertEqual(self.scratch.checked("ls-remote", "origin", "mitosis/demo"), "")
        self.assertFalse(os.path.exists(self.scratch.pr_log), "pr.log exists")

    def mutants_match_receipts_g14(self):
        mutants = self._module("mutants")
        with open(G14_CASES, encoding="utf-8") as handle:
            cases = json.load(handle)
        self.assertGreater(len(cases), 0)
        for case in cases:
            with self.subTest(line=case["line"], ext=case["ext"]):
                self.assertEqual(
                    [(m["op"], m["after"]) for m in mutants.mutants_for_line(case["line"], case["ext"])],
                    [(m["op"], m["after"]) for m in case["mutants"]],
                )

    def an_existence_only_test_of_a_broken_add_is_weak(self):
        completed = self._gated({"calc.py": BROKEN_ADD, "tests/test_calc.py": EXISTENCE_TEST})
        self._held_back(completed, 24, "weak")

    def a_skipped_test_fails_the_gate(self):
        completed = self._gated({"calc.py": CORRECT_ADD, "tests/test_calc.py": SKIPPED_TEST})
        self._held_back(completed, 24, "weak")

    def an_inert_test_holds_the_msp_back(self):
        completed = self._gated(
            {"calc.py": SWAPPED_ADD, "tests/test_calc.py": EXACT_TEST},
            seeded={"calc.py": CORRECT_ADD},
        )
        self._held_back(completed, 20, "inert")

    def an_inconclusive_property_holds_the_msp_back(self):
        completed = self._gated(
            {"calc.py": CORRECT_ADD, "tests/test_calc.py": EXACT_TEST}, acceptance="exit-2"
        )
        self._held_back(completed, 21, "inconclusive")

    def a_test_that_kills_a_mutant_passes_and_ships(self):
        completed = self._gated({"calc.py": CORRECT_ADD, "tests/test_calc.py": EXACT_TEST})
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        msp = self._msp(completed)
        first = self._first_property(msp)
        self.assertEqual(first["outcome"], "pass")
        self.assertIn("mutants", first, "the gate's property carries no mutants record")
        self.assertEqual(
            (first["mutants"] or {}).get("killed"),
            {"file": "calc.py", "line": 2, "op": "return -> None"},
        )
        self.assertEqual(msp["state"], "shipped")
        self.assertEqual(
            len(self.scratch.checked("ls-remote", "origin", "mitosis/demo").splitlines()), 1
        )
        with open(self.scratch.pr_log, encoding="utf-8") as handle:
            self.assertEqual(len(handle.read().splitlines()), 1)

    def a_data_file_is_judged_by_the_drop_added_lines_mutant(self):
        completed = self._gated(
            {"limits.json": "{\"cap\": 3}\n", "tests/test_calc.py": LIMITS_TEST},
            files=("limits.json", "tests/test_calc.py"),
        )
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        first = self._first_property(self._msp(completed))
        self.assertEqual(first["outcome"], "pass")
        self.assertIn("mutants", first, "the gate's property carries no mutants record")
        self.assertEqual(
            (first["mutants"] or {}).get("killed"),
            {"file": "limits.json", "line": 1, "op": "drop added lines"},
        )


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            GateTeeth,
            (
                "mutants_match_receipts_g14",
                "an_existence_only_test_of_a_broken_add_is_weak",
                "a_skipped_test_fails_the_gate",
                "an_inert_test_holds_the_msp_back",
                "an_inconclusive_property_holds_the_msp_back",
                "a_test_that_kills_a_mutant_passes_and_ships",
                "a_data_file_is_judged_by_the_drop_added_lines_mutant",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
