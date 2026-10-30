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

TEST_FILE = "tests/test_calc.py"
AREAS = {"file": TEST_FILE, "test": "Calc.areas"}
AREA_BEFORE = (
    "def area(width, height):\n"
    "    size = width * height\n"
    "    label = 1 / 0\n"
    "    return size\n"
)
AREA_AFTER = AREA_BEFORE.replace("    label = 1 / 0\n", "    label = 4\n")
AREA_TEST = (
    "import unittest\n"
    "\n"
    "import calc\n"
    "\n"
    "\n"
    "class Calc(unittest.TestCase):\n"
    "    def areas(self):\n"
    "        self.assertEqual(calc.area(2, 3), 6)\n"
)
CORRECT_ADD = "def add(a, b):\n    return a + b\n"
SHAPING = (
    "GIT_DIFF_OPTS",
    "GIT_EXTERNAL_DIFF",
    "GIT_DIFF_PATH_COUNTER",
    "GIT_DIFF_PATH_TOTAL",
    "GIT_LITERAL_PATHSPECS",
    "GIT_GLOB_PATHSPECS",
    "GIT_NOGLOB_PATHSPECS",
    "GIT_ICASE_PATHSPECS",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_REPLACE_REF_BASE",
    "GIT_ATTR_SOURCE",
)
GIT_LOGGER = r'''#!/bin/sh
env | sed -n 's/^\(GIT_[A-Za-z0-9_]*\)=.*/\1/p' | tr '\n' ' ' >> %(log)s
echo >> %(log)s
exec %(git)s "$@"
'''


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


class PinnedDiffEnv(unittest.TestCase):
    def setUp(self):
        self.scratch = None

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()

    def _scratch(self, seeded=None):
        self.scratch = Scratch(seeded)
        return self.scratch

    def _first_property(self, summary, label="demo"):
        found = [msp for msp in summary.get("msps") or [] if msp.get("msp") == label]
        self.assertEqual(len(found), 1, "the JSON summary lists no MSP %s" % label)
        gate = found[0].get("gate") or {}
        self.assertTrue(gate.get("properties"), "the MSP's gate object carries no properties")
        return gate["properties"][0]

    def _harmless_shaping(self, scratch):
        git_dir = os.path.join(scratch.repo, ".git")
        return {
            "GIT_DIFF_OPTS": "-U0",
            "GIT_EXTERNAL_DIFF": "true",
            "GIT_DIFF_PATH_COUNTER": "1",
            "GIT_DIFF_PATH_TOTAL": "1",
            "GIT_LITERAL_PATHSPECS": "0",
            "GIT_GLOB_PATHSPECS": "0",
            "GIT_NOGLOB_PATHSPECS": "0",
            "GIT_ICASE_PATHSPECS": "0",
            "GIT_DIR": git_dir,
            "GIT_WORK_TREE": scratch.repo,
            "GIT_INDEX_FILE": os.path.join(git_dir, "index"),
            "GIT_OBJECT_DIRECTORY": os.path.join(git_dir, "objects"),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": os.path.join(git_dir, "objects"),
            "GIT_COMMON_DIR": git_dir,
            "GIT_NAMESPACE": "mitosis-test",
            "GIT_REPLACE_REF_BASE": "refs/replace/",
            "GIT_ATTR_SOURCE": "HEAD",
        }

    def an_inherited_unified_context_still_finds_the_weak_check(self):
        scratch = self._scratch({"calc.py": AREA_BEFORE})
        items = scratch.items([step("label", ["calc.py", TEST_FILE], "demo", acceptance=[AREAS])])
        scratch.jobs({"label": {"writes": {"calc.py": AREA_AFTER, TEST_FILE: AREA_TEST}}})
        completed = scratch.build(items, env={"GIT_DIFF_OPTS": "--unified=5"})
        self.assertEqual(completed.returncode, 24, "mitosis exit code" + tail(completed))
        first = self._first_property(summary_of(completed))
        self.assertEqual(first["outcome"], "weak")
        self.assertRegex(first["reason"] or "", r"calc\.py:3\b")

    def every_git_call_runs_without_the_shaping_variables(self):
        scratch = self._scratch()
        real_git = shutil.which("git", path=os.environ.get("PATH"))
        wrapper = os.path.join(scratch.tmp, "git-wrapper")
        log = os.path.join(scratch.tmp, "git-environment.log")
        put(
            os.path.join(wrapper, "git"),
            GIT_LOGGER % {"log": shlex.quote(log), "git": shlex.quote(real_git)},
        )
        os.chmod(os.path.join(wrapper, "git"), 0o755)
        items = scratch.items([step("add", ["calc.py"], "demo")])
        scratch.jobs({"add": {"writes": {"calc.py": CORRECT_ADD}}})
        shaping = self._harmless_shaping(scratch)
        self.assertEqual(sorted(shaping), sorted(SHAPING))
        completed = scratch.build(
            items,
            env={**shaping, "PATH": wrapper + os.pathsep + scratch.env.get("PATH", "")},
        )
        lines = []
        if os.path.exists(log):
            with open(log, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        self.assertGreater(len(lines), 0, "no git call reached the wrapper" + tail(completed))
        leaked = sorted({name for line in lines for name in line.split() if name in SHAPING})
        self.assertEqual(leaked, [], "git calls received shaping variables" + tail(completed))
        self.assertTrue(
            any("GIT_AUTHOR_NAME" in line.split() for line in lines),
            "no git call received GIT_AUTHOR_NAME",
        )
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names_ in (
        (
            PinnedDiffEnv,
            (
                "an_inherited_unified_context_still_finds_the_weak_check",
                "every_git_call_runs_without_the_shaping_variables",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names_)
    return suite


if __name__ == "__main__":
    unittest.main()
