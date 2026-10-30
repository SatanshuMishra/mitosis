import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import contract_schema

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
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "worker.json"), encoding="utf-8") as handle:
    jobs = json.load(handle)
brief = " ".join(sys.argv[2:])
named = [
    name
    for name in sorted(jobs)
    if re.search(r"(?<![\w-])task-%s(?![\w-])" % re.escape(name), brief)
]
if len(named) > 1:
    named = [name for name in named if jobs[name].get("msp") in (None, sys.argv[1])]
if len(named) != 1:
    print(json.dumps({"item": "unknown", "status": "failed", "files_changed": [], "notes": "no job"}))
    sys.exit(1)
name = named[0]
job = jobs[name]
with open(os.path.join(here, "dispatch.log"), "a", encoding="utf-8") as handle:
    handle.write(name + "\n")
record = {"cwd": os.path.realpath(os.getcwd())}


def git(*args):
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, timeout=60, check=True
    ).stdout.strip()


def put(path, content):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)


def attempt(path, content, mode="a"):
    try:
        with open(path, mode, encoding="utf-8") as handle:
            handle.write(content)
    except OSError as refused:
        return refused.errno
    return 0


def digest(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


for path, content in sorted(job.get("writes", {}).items()):
    put(path, content)
for path in job.get("executable", []):
    os.chmod(path, 0o755)
for path, target in sorted(job.get("links", {}).items()):
    os.symlink(target, path)
for path in job.get("deletes", []):
    os.remove(path)
if job.get("script"):
    exec(job["script"])
os.makedirs(os.path.join(here, "records"), exist_ok=True)
with open(os.path.join(here, "records", name + ".json"), "w", encoding="utf-8") as handle:
    json.dump(record, handle)
touched = sorted(set(job.get("writes", {})) | set(job.get("links", {})) | set(job.get("deletes", [])))
declared = job.get("declared", touched)
print(json.dumps({"item": name, "status": "ok", "files_changed": declared, "notes": "done"}))
'''

ACCEPT = r'''
import os
import subprocess
import sys

mode, file, test = sys.argv[1], sys.argv[2], sys.argv[3]
if mode not in ("unittest", "keep"):
    sys.exit(2)
folder, name = os.path.split(file)
module = os.path.splitext(name)[0]
inherited = [os.environ["PYTHONPATH"]] if mode == "keep" and os.environ.get("PYTHONPATH") else []
env = dict(os.environ, PYTHONPATH=os.pathsep.join(inherited + [os.path.abspath(folder), os.getcwd()]))
try:
    code = subprocess.run(
        [sys.executable, "-m", "unittest", module + "." + test], env=env, timeout=120
    ).returncode
except subprocess.TimeoutExpired:
    sys.exit(2)
sys.exit(code if code in (0, 1) else 2)
'''

K2_SCHEMA = os.path.join(HERE, "fixtures", "contracts", "k2-locked.schema.json")
TEST_FILE = "tests/test_calc.py"
ADDS = {"file": TEST_FILE, "test": "Calc.adds"}
CORRECT_ADD = "def add(a, b):\n    return a + b\n"
EXACT_TEST = (
    "import unittest\n"
    "\n"
    "import calc\n"
    "\n"
    "\n"
    "class Calc(unittest.TestCase):\n"
    "    def adds(self):\n"
    "        self.assertEqual(calc.add(2, 3), 5)\n"
)
PASSING_TEST = (
    "import unittest\n"
    "\n"
    "\n"
    "class Calc(unittest.TestCase):\n"
    "    def adds(self):\n"
    "        self.assertTrue(True)\n"
)
LOCK_MESSAGE = "test: lock the acceptance checks"


def check_author(name="check-author", files=(TEST_FILE,), acceptance=()):
    return {**step(name, files, "check-author", acceptance), "role": "check-author"}


def builder(files=("calc.py",), after=("check-author",)):
    return step("add", files, "calc", [ADDS], after)


def names_step(text, name):
    return re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(name), text or "") is not None


def put(path, content):
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content.encode("utf-8"))
    return path


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def step(name, files, msp, acceptance=(), after=()):
    base = {
        "name": name,
        "task": "task-" + name,
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


def summary_of(completed):
    lines = completed.stdout.splitlines()
    if not lines:
        return {}
    try:
        parsed = json.loads(lines[-1])
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def names(detail, path):
    return re.search(r"(?<![\w/.-])%s(?![\w.-])" % re.escape(path), detail or "") is not None


def inside(path, folder):
    return path.startswith(os.path.join(folder, ""))


class Scratch:
    def __init__(self, seeded=None, run_name="run"):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="mitosis-isolation-"))
        self.repo = os.path.join(self.tmp, "repo")
        self.remote = os.path.join(self.tmp, "remote.git")
        self.stubs = os.path.join(self.tmp, "stubs")
        self.markers = os.path.join(self.stubs, "markers")
        self.run_dir = os.path.join(self.tmp, "runs", run_name)
        kept = {key: value for key, value in os.environ.items() if key not in STRIPPED}
        self.env = {
            **kept,
            **GIT_IDENTITY,
            "HOME": os.path.join(self.tmp, "home"),
            "PLUGIN_METRICS_DIR": os.path.join(self.tmp, "metrics"),
        }
        os.makedirs(self.env["HOME"])
        os.makedirs(self.repo)
        os.makedirs(self.markers)
        self.checked("init", "-q", "-b", "main", self.repo)
        self.checked("init", "-q", "--bare", self.remote)
        put(os.path.join(self.repo, "README.md"), "seed\n")
        put(os.path.join(self.repo, ".gitignore"), "__pycache__/\n")
        self.checked("-C", self.repo, "add", "-A")
        self.checked("-C", self.repo, "commit", "-q", "-m", "seed")
        self.checked("-C", self.repo, "checkout", "-q", "-b", "feature")
        put(os.path.join(self.repo, "NOTES.md"), "feature\n")
        for path, content in (seeded or {}).items():
            put(os.path.join(self.repo, path), content)
        self.checked("-C", self.repo, "add", "-A")
        self.checked("-C", self.repo, "commit", "-q", "-m", "seed the feature branch")
        self.checked("-C", self.repo, "checkout", "-q", "main")
        self.checked("-C", self.repo, "remote", "add", "origin", self.remote)
        put(os.path.join(self.stubs, "worker.py"), WORKER)
        put(os.path.join(self.stubs, "accept.py"), ACCEPT)

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

    def mitosis(self, *flags, env=None):
        return subprocess.run(
            [sys.executable, MITOSIS, *flags],
            cwd=self.repo,
            env={**self.env, **(env or {})},
            capture_output=True,
            text=True,
            timeout=RUN_LIMIT,
        )

    def build(self, items, *extra, acceptance="unittest", env=None):
        return self.mitosis(
            "--items",
            items,
            "--run-dir",
            self.run_dir,
            "--feature-branch",
            "feature",
            "--dispatch-command",
            self.stub("worker.py", "{msp}", "{task}"),
            "--acceptance-command",
            self.stub("accept.py", acceptance, "{file}", "{test}"),
            "--timeout",
            "120",
            "--no-push",
            *extra,
            env=env,
        )

    def record(self, name):
        path = os.path.join(self.stubs, "records", name + ".json")
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def dispatched(self):
        path = os.path.join(self.stubs, "dispatch.log")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return handle.read().splitlines()


class CheckAuthorLane(unittest.TestCase):
    def setUp(self):
        self.scratch = None

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()

    def _scratch(self, seeded=None):
        self.scratch = Scratch(seeded)
        return self.scratch

    def _msp(self, summary, label):
        found = [msp for msp in summary.get("msps") or [] if msp.get("msp") == label]
        self.assertEqual(len(found), 1, "the JSON summary lists no MSP %s" % label)
        return found[0]

    def _lane(self, summary, name):
        found = [lane for lane in summary.get("lanes") or [] if name in (lane.get("steps") or [])]
        self.assertEqual(len(found), 1, "the JSON summary lists no Lane for %s" % name)
        return found[0]

    def _lock(self, summary, completed):
        lock = summary.get("lock")
        self.assertIsInstance(lock, dict, "the JSON summary carries no lock object" + tail(completed))
        return lock

    def _referee(self, summary, completed):
        referee = summary.get("referee")
        self.assertIsInstance(referee, dict, "the JSON summary carries no referee object" + tail(completed))
        self.assertEqual(contract_schema.errors(referee, contract_schema.load(K2_SCHEMA)), [])
        return referee

    def _feature(self):
        return self.scratch.checked("rev-parse", "feature").strip()

    def _gained(self, before):
        return self.scratch.checked("rev-list", before + "..feature").split()

    def _plan_only(self, steps, label):
        scratch = self.scratch
        return scratch.mitosis(
            "--items",
            scratch.items(steps, label + ".json"),
            "--plan-only",
            "--feature-branch",
            "feature",
            "--run-dir",
            os.path.join(scratch.tmp, "runs", label),
        )

    def an_items_file_with_a_check_author_plans(self):
        scratch = self._scratch()
        completed = self._plan_only([check_author(), builder()], "plan")
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        summary = summary_of(completed)
        self.assertEqual([msp.get("msp") for msp in summary.get("msps") or []], ["calc"])
        self.assertEqual(
            [lane.get("steps") for lane in summary.get("lanes") or []], [["add"]]
        )
        with open(os.path.join(scratch.tmp, "runs", "plan", "plan.json"), encoding="utf-8") as handle:
            plan = json.load(handle)
        self.assertEqual(plan.get("locked"), [TEST_FILE])
        for lane in plan.get("lanes") or []:
            self.assertNotIn("check-author", lane.get("steps") or [])
        report = completed.stdout.splitlines()[:-1]
        self.assertTrue(
            any("lock stage" in line.lower() and TEST_FILE in line for line in report),
            "no report line names the lock stage and its locked paths" + tail(completed),
        )

    def a_malformed_check_author_is_refused(self):
        self._scratch()
        cases = (
            (
                "two-check-authors",
                [
                    check_author(name="lock-one"),
                    check_author(name="lock-two", files=("tests/test_two.py",)),
                    builder(after=("lock-one", "lock-two")),
                ],
                ("lock-one", "lock-two"),
            ),
            (
                "acceptance-entries",
                [check_author(name="write-checks", acceptance=[ADDS]), builder(after=("write-checks",))],
                ("write-checks",),
            ),
            (
                "unknown-role",
                [{**step("add", ["calc.py"], "calc"), "role": "builder"}],
                ("add",),
            ),
            (
                "shared-file",
                [
                    check_author(name="write-checks"),
                    builder(files=("calc.py", TEST_FILE), after=("write-checks",)),
                ],
                ("add",),
            ),
        )
        for label, steps, offenders in cases:
            with self.subTest(case=label):
                completed = self._plan_only(steps, label)
                self.assertEqual(completed.returncode, 3, "mitosis exit code" + tail(completed))
                self.assertTrue(
                    any(names_step(completed.stderr, name) for name in offenders),
                    "stderr names none of %s" % (offenders,) + tail(completed),
                )

    def a_check_author_lane_locks_then_the_build_runs(self):
        scratch = self._scratch()
        before = self._feature()
        items = scratch.items([check_author(), builder()])
        scratch.jobs(
            {
                "check-author": {"writes": {TEST_FILE: EXACT_TEST}, "msp": "lock"},
                "add": {"writes": {"calc.py": CORRECT_ADD}, "msp": "calc"},
            }
        )
        completed = scratch.build(items)
        gained = self._gained(before)
        self.assertEqual(len(gained), 1, "commits the feature branch gained" + tail(completed))
        lock_commit = gained[0]
        self.assertEqual(scratch.checked("log", "-1", "--format=%s", lock_commit).strip(), LOCK_MESSAGE)
        self.assertEqual(
            scratch.checked("diff", "--name-status", before, lock_commit).strip(),
            "A\t" + TEST_FILE,
        )
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        summary = summary_of(completed)
        referee = self._referee(summary, completed)
        self.assertEqual(referee["commit"], lock_commit)
        self.assertEqual(referee["locked"], [TEST_FILE])
        self.assertIn(
            lock_commit, scratch.checked("rev-list", "--first-parent", "mitosis/calc").split()
        )
        self.assertEqual(scratch.dispatched(), ["check-author", "add"])
        self.assertEqual((self._msp(summary, "calc").get("gate") or {}).get("outcome"), "pass")

    def a_locked_test_that_passes_before_the_build_stops_it(self):
        scratch = self._scratch()
        before = self._feature()
        items = scratch.items([check_author(), builder()])
        scratch.jobs(
            {
                "check-author": {"writes": {TEST_FILE: PASSING_TEST}, "msp": "lock"},
                "add": {"writes": {"calc.py": CORRECT_ADD}, "msp": "calc"},
            }
        )
        completed = scratch.build(items)
        summary = summary_of(completed)
        lock = self._lock(summary, completed)
        self.assertEqual(lock.get("state"), "failed")
        self.assertIn(TEST_FILE, lock.get("reason") or "")
        self.assertIn("Calc.adds", lock.get("reason") or "")
        self.assertEqual(self._lane(summary, "add")["state"], "blocked")
        self.assertEqual(self._feature(), before)
        self.assertEqual(scratch.dispatched(), ["check-author"])
        self.assertNotEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))

    def a_check_author_write_outside_the_locked_paths_is_not_landed(self):
        scratch = self._scratch()
        before = self._feature()
        items = scratch.items([check_author(), builder()])
        scratch.jobs(
            {
                "check-author": {
                    "writes": {TEST_FILE: EXACT_TEST, "calc.py": CORRECT_ADD},
                    "declared": [TEST_FILE],
                    "msp": "lock",
                },
                "add": {"writes": {"calc.py": CORRECT_ADD}, "msp": "calc"},
            }
        )
        completed = scratch.build(items)
        gained = self._gained(before)
        self.assertEqual(len(gained), 1, "commits the feature branch gained" + tail(completed))
        self.assertEqual(
            scratch.checked("diff", "--name-status", before, gained[0]).strip(),
            "A\t" + TEST_FILE,
        )
        found = [
            finding
            for finding in summary_of(completed).get("findings") or []
            if finding.get("msp") == "lock"
            and finding.get("kind") == "undeclared"
            and names(finding.get("detail"), "calc.py")
        ]
        self.assertTrue(found, "no undeclared finding under lock names calc.py" + tail(completed))

    def locked_files_already_on_the_feature_branch_skip_the_lane(self):
        scratch = self._scratch({TEST_FILE: EXACT_TEST})
        before = self._feature()
        items = scratch.items([check_author(), builder()])
        scratch.jobs(
            {
                "check-author": {"writes": {TEST_FILE: EXACT_TEST}, "msp": "lock"},
                "add": {"writes": {"calc.py": CORRECT_ADD}, "msp": "calc"},
            }
        )
        completed = scratch.build(items)
        self.assertEqual(scratch.dispatched(), ["add"], "dispatch log" + tail(completed))
        summary = summary_of(completed)
        lock = self._lock(summary, completed)
        self.assertEqual(lock.get("state"), "ok")
        self.assertIn("already on the feature branch", lock.get("reason") or "")
        referee = self._referee(summary, completed)
        self.assertEqual(referee["commit"], before)
        self.assertEqual(self._feature(), before)
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names_ in (
        (
            CheckAuthorLane,
            (
                "an_items_file_with_a_check_author_plans",
                "a_malformed_check_author_is_refused",
                "a_check_author_lane_locks_then_the_build_runs",
                "a_locked_test_that_passes_before_the_build_stops_it",
                "a_check_author_write_outside_the_locked_paths_is_not_landed",
                "locked_files_already_on_the_feature_branch_skip_the_lane",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names_)
    return suite


if __name__ == "__main__":
    unittest.main()
