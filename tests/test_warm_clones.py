import contextlib
import errno
import importlib
import io
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from test_check_author_lane import saved_plan

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
APFS_ONLY = "only APFS gives this test a real copy-on-write filesystem"

WORKER = r'''
import errno
import json
import os
import re
import stat
import subprocess
import sys

SESSION, SCHEMA = sys.argv[-2], sys.argv[-1]
del sys.argv[-2:]

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
    print(json.dumps({"type": "result", "session_id": SESSION, "result": "done", "structured_output": {"item": "unknown", "status": "failed", "files_changed": [], "notes": "no job"}}))
    sys.exit(1)
name = named[0]
job = jobs[name]
with open(os.path.join(here, "dispatch.log"), "a", encoding="utf-8") as handle:
    handle.write(name + "\n")
record = {"cwd": os.path.realpath(os.getcwd())}


def put(path, content):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)


def attempt(path, content, mode="a"):
    try:
        with open(path, mode, encoding="utf-8", newline="") as handle:
            handle.write(content)
    except OSError as refused:
        return refused.errno
    return 0


def seeded_state(path):
    if not os.path.isfile(path):
        return {"present": False, "bytes": None}
    with open(path, "rb") as handle:
        return {"present": True, "bytes": handle.read().decode("latin-1")}


for path, content in sorted(job.get("writes", {}).items()):
    put(path, content)
if job.get("script"):
    exec(job["script"])
os.makedirs(os.path.join(here, "records"), exist_ok=True)
with open(os.path.join(here, "records", name + ".json"), "w", encoding="utf-8") as handle:
    json.dump(record, handle)
declared = job.get("declared", sorted(job.get("writes", {})))
print(json.dumps({"type": "result", "session_id": SESSION, "result": "done", "structured_output": {"item": name, "status": "ok", "files_changed": declared, "notes": "done"}}))
'''

REASK_COMMAND = " ".join(
    (
        shlex.quote(sys.executable),
        "-c",
        shlex.quote("import sys; sys.stderr.write('no re-ask was expected\\n'); sys.exit(1)"),
        "{prompt}",
        "{session_id}",
        "{schema}",
    )
)

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

GATE_ACCEPT = r'''
import json
import os
import re
import sys

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "gate.log"), "a", encoding="utf-8") as handle:
    handle.write(
        json.dumps({"cwd": os.path.realpath(os.getcwd()), "cache": os.path.lexists("cache")}) + "\n"
    )
try:
    with open("left.py", encoding="utf-8") as handle:
        text = handle.read()
except OSError:
    sys.exit(1)
sys.exit(0 if re.search(r"^LEFT\b", text, re.MULTILINE) else 1)
'''

SEEDED_FILE = "cache/sub/a.bin"
WARM = "warm\n"
LEFT = 'LEFT = "left"\n'
RIGHT = 'RIGHT = "right"\n'
IGNORES = "__pycache__/\ncache/\n"
LISTED = "cache/\n"
COMMENTS_ONLY = "# nothing yet\n\n"
LEFT_SUCCESS = {"item": "left", "status": "ok", "files_changed": ["left.py"], "notes": "done"}

RECORD_SEEDED = r'''
record = {
    **record,
    **seeded_state(os.path.join("cache", "sub", "a.bin")),
    "cache": os.path.lexists("cache"),
}
'''

LSTAT_THEN_DIRTY = r'''
seeded_path = os.path.join("cache", "sub", "a.bin")
try:
    seen = os.lstat(seeded_path)
    record = {
        **record,
        "regular": stat.S_ISREG(seen.st_mode),
        "link": stat.S_ISLNK(seen.st_mode),
        "ino": seen.st_ino,
    }
except OSError:
    record = {**record, "regular": None, "link": None, "ino": None}
record = {**record, "appended": attempt(seeded_path, "dirty\n")}
put(os.path.join("cache", "new.bin"), "new\n")
'''

UNIGNORE_THEN_DIRTY = r'''
put(".gitignore", "__pycache__/\n")
attempt(os.path.join("cache", "sub", "a.bin"), "dirty\n")
put(os.path.join("cache", "new.bin"), "new\n")
'''

DIRTY_FIRST_THEN_RECORD = r'''
with open(os.path.join(here, "dispatch.log"), encoding="utf-8") as handle:
    dispatches = handle.read().splitlines().count(name)
if dispatches <= 2:
    attempt(os.path.join("cache", "sub", "a.bin"), "dirty\n")
    sys.exit(1)
record = {**record, **seeded_state(os.path.join("cache", "sub", "a.bin"))}
put("left.py", job["left"])
'''

WRITE_DIRTY_THEN_FAIL = r'''
put("left.py", job["left"])
attempt(os.path.join("cache", "sub", "a.bin"), "dirty\n")
put(os.path.join("cache", "new.bin"), "new\n")
sys.exit(1)
'''

FAIL_WITHOUT_WRITING = "sys.exit(1)\n"

LEFT_TEST = (
    "import unittest\n"
    "\n"
    "import left\n"
    "\n"
    "\n"
    "class Left(unittest.TestCase):\n"
    "    def defines(self):\n"
    "        self.assertEqual(left.LEFT, 'left')\n"
)

CHECK_AUTHOR_TEST_FILE = "tests/test_calc.py"
ADDS = {"file": CHECK_AUTHOR_TEST_FILE, "test": "Calc.adds"}
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

SECRET_NAMES = (
    ".env",
    ".env.local",
    "server.pem",
    "server.key",
    "cert.p12",
    "cert.pfx",
    "id_rsa",
    "id_ed25519",
    ".npmrc",
    ".netrc",
    ".pgpass",
    ".envrc",
    ".git-credentials",
    "release.jks",
    "upload.keystore",
    "key.properties",
)

PLAIN_COPIERS = (
    "shutil.copy",
    "shutil.copy2",
    "shutil.copyfile",
    "shutil.copytree",
    "os.link",
    "os.symlink",
)


def load(name):
    return importlib.import_module(name)


def put(path, content):
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content.encode("utf-8"))
    return path


def read_text(path):
    with open(path, "rb") as handle:
        return handle.read().decode("utf-8")


def opened_for_cleanup(top):
    for folder, _, _ in os.walk(top):
        if not os.path.islink(folder):
            os.chmod(folder, stat.S_IRWXU)


def remove_tree(top):
    if os.path.isdir(top):
        opened_for_cleanup(top)
    shutil.rmtree(top, ignore_errors=True)


SPEC_SECTION = "1.1"
SPEC_DOCUMENT = "# Spec\n\n## 1.1 Build\n"


def step(name, files, msp, acceptance=(), after=()):
    base = {
        "name": name,
        "task": "task-" + name,
        "files": list(files),
        "acceptance": list(acceptance),
        "spec_ref": [SPEC_SECTION],
        "msp": msp,
    }
    return {**base, "after": list(after)} if after else base


def check_author():
    labelled = {**step("check-author", [CHECK_AUTHOR_TEST_FILE], "calc"), "role": "check-author"}
    return {key: value for key, value in labelled.items() if key != "msp"}


def builder():
    return step("add", ["calc.py"], "calc", [ADDS], ["check-author"])


def tail(completed):
    return "\n--- stdout ---\n%s\n--- stderr ---\n%s" % (
        completed.stdout[-4000:],
        completed.stderr[-4000:],
    )


def summary_of(stdout):
    lines = stdout.splitlines()
    if not lines:
        return {}
    try:
        parsed = json.loads(lines[-1])
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def under_cache(path):
    return path == "cache" or path.startswith("cache/")


def names_cache(finding):
    return re.search(r"(?<![\w.-])cache/", json.dumps(finding)) is not None


def snapshot(top):
    found = []
    for folder, folders, files in os.walk(top):
        folders.sort()
        for entry in sorted(files):
            path = os.path.join(folder, entry)
            with open(path, "rb") as handle:
                found.append((os.path.relpath(path, top), handle.read()))
    return found


def mode_of(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def git_env(home):
    kept = {key: value for key, value in os.environ.items() if key not in STRIPPED}
    return {**kept, **GIT_IDENTITY, "HOME": home}


def git_in(folder, env, *args):
    completed = subprocess.run(
        ["git", *args], cwd=folder, env=env, capture_output=True, text=True, timeout=CALL_LIMIT
    )
    if completed.returncode != 0:
        raise RuntimeError("scaffold git %s failed: %s" % (" ".join(args), completed.stderr))
    return completed.stdout


class Folder:
    def __init__(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="mitosis-warm-"))
        self.repo = os.path.join(self.tmp, "repo")
        self.clone = os.path.join(self.tmp, "clone")
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        os.makedirs(self.repo)
        os.makedirs(self.clone)
        self.env = git_env(home)

    def close(self):
        remove_tree(self.tmp)

    def git(self, *args):
        return git_in(self.repo, self.env, *args)

    def repository(self, committed, loose=None):
        self.git("init", "-q", "-b", "main", self.repo)
        for path, content in sorted(committed.items()):
            put(os.path.join(self.repo, path), content)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "seed")
        for path, content in sorted((loose or {}).items()):
            put(os.path.join(self.repo, path), content)
        return self.repo

    def link(self, path, target):
        full = os.path.join(self.repo, path)
        if os.path.dirname(full):
            os.makedirs(os.path.dirname(full), exist_ok=True)
        os.symlink(target, full)
        return full


class Scratch:
    def __init__(self, worktreeinclude=LISTED, committed=None):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="mitosis-warm-"))
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
        os.makedirs(self.stubs)
        self.checked("init", "-q", "-b", "main", self.repo)
        self.checked("init", "-q", "--bare", self.remote)
        put(os.path.join(self.repo, "README.md"), "seed\n")
        put(os.path.join(self.repo, ".gitignore"), IGNORES)
        if worktreeinclude is not None:
            put(os.path.join(self.repo, ".worktreeinclude"), worktreeinclude)
        self.checked("-C", self.repo, "add", "-A")
        self.checked("-C", self.repo, "commit", "-q", "-m", "seed")
        self.checked("-C", self.repo, "checkout", "-q", "-b", "feature")
        put(os.path.join(self.repo, "NOTES.md"), "feature\n")
        for path, content in sorted((committed or {}).items()):
            put(os.path.join(self.repo, path), content)
        self.checked("-C", self.repo, "add", "-A")
        self.checked("-C", self.repo, "commit", "-q", "-m", "seed the feature branch")
        self.checked("-C", self.repo, "checkout", "-q", "main")
        self.checked("-C", self.repo, "remote", "add", "origin", self.remote)
        put(os.path.join(self.repo, SEEDED_FILE), WARM)
        put(os.path.join(self.stubs, "worker.py"), WORKER)
        put(os.path.join(self.stubs, "accept.py"), ACCEPT)
        put(os.path.join(self.stubs, "gate_accept.py"), GATE_ACCEPT)

    def close(self):
        remove_tree(self.tmp)

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

    def source(self):
        path = put(os.path.join(self.tmp, "spec.md"), SPEC_DOCUMENT)
        return {"path": os.path.abspath(path), "sha256": hashlib.sha256(SPEC_DOCUMENT.encode("utf-8")).hexdigest()}

    def items(self, steps):
        source = self.source()
        return put(os.path.join(self.tmp, "items.json"), json.dumps([{**entry, "source": source} for entry in steps]))

    def jobs(self, jobs):
        return put(os.path.join(self.stubs, "worker.json"), json.dumps(jobs))

    def stub(self, script, *words):
        return " ".join(
            [shlex.quote(sys.executable), shlex.quote(os.path.join(self.stubs, script))]
            + list(words)
        )

    def flags(self, items, *extra, gate=False):
        acceptance = (
            self.stub("gate_accept.py", "{file}", "{test}")
            if gate
            else self.stub("accept.py", "unittest", "{file}", "{test}")
        )
        return [
            "--items",
            items,
            "--run-dir",
            self.run_dir,
            "--feature-branch",
            "feature",
            "--dispatch-command",
            self.stub("worker.py", "{msp}", "{task}", "{session_id}", "{schema}"),
            "--reask-command",
            REASK_COMMAND,
            "--acceptance-command",
            acceptance,
            "--timeout",
            "120",
            "--no-push",
            *extra,
        ]

    def build(self, items, *extra, gate=False):
        return subprocess.run(
            [sys.executable, MITOSIS, *self.flags(items, *extra, gate=gate)],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=RUN_LIMIT,
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

    def gate_runs(self):
        path = os.path.join(self.stubs, "gate.log")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines() if line]

    def state(self):
        path = os.path.join(self.run_dir, "state.json")
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def write_state(self, state):
        put(os.path.join(self.run_dir, "state.json"), json.dumps(state, indent=2, sort_keys=True))

    def lane_records(self):
        return [record for _, record in sorted((self.state().get("lanes") or {}).items())]

    def tree_paths(self, branch):
        return self.checked("ls-tree", "-r", "--name-only", branch).splitlines()

    def invoke(self, argv):
        before = os.getcwd()
        out, err = io.StringIO(), io.StringIO()
        os.chdir(self.repo)
        try:
            with unittest.mock.patch.dict(os.environ, self.env, clear=True):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    try:
                        code = load("mitosis").main(argv)
                    except SystemExit as stop:
                        code = stop.code
        finally:
            os.chdir(before)
        return code, out.getvalue(), err.getvalue()


class WarmClones(unittest.TestCase):
    def setUp(self):
        self.scratch = None
        self.folder = None

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()
        if self.folder is not None:
            self.folder.close()

    def _darwin_only(self):
        if sys.platform != "darwin":
            self.skipTest(APFS_ONLY)

    def _scratch(self, worktreeinclude=LISTED, committed=None):
        self.scratch = Scratch(worktreeinclude, committed)
        return self.scratch

    def _folder(self):
        self.folder = Folder()
        return self.folder

    def _lane(self, summary, name):
        found = [lane for lane in summary.get("lanes") or [] if name in (lane.get("steps") or [])]
        self.assertEqual(len(found), 1, "the JSON summary lists no Lane for %s" % name)
        return found[0]

    def _only_lane_record(self):
        records = self.scratch.lane_records()
        self.assertEqual(len(records), 1, "state.json lane records: %r" % (records,))
        return records[0]

    def _warm_record(self, name, completed=None):
        record = self.scratch.record(name)
        self.assertIs(
            record.get("present"),
            True,
            "the Worker for %s saw no %s" % (name, SEEDED_FILE) + (tail(completed) if completed else ""),
        )
        self.assertEqual(record.get("bytes"), WARM)

    def _skipped_paths(self, skipped):
        for entry in skipped:
            self.assertIsInstance(entry.get("reason"), str)
            self.assertTrue(entry["reason"].strip(), "a skipped entry carries an empty reason")
        return [entry.get("path") for entry in skipped]

    def _names_a_symbolic_link(self, skipped, path):
        found = [entry for entry in skipped if entry.get("path") == path]
        self.assertEqual(len(found), 1, "skipped holds no single entry for %s: %r" % (path, skipped))
        self.assertIn("symbolic link", (found[0].get("reason") or "").lower())

    def a_listed_ignored_folder_is_in_every_lane_clone(self):
        self._darwin_only()
        scratch = self._scratch()
        items = scratch.items([step("left", ["left.py"], "demo"), step("right", ["right.py"], "demo")])
        scratch.jobs(
            {
                "left": {"writes": {"left.py": LEFT}, "script": RECORD_SEEDED},
                "right": {"writes": {"right.py": RIGHT}, "script": RECORD_SEEDED},
            }
        )
        completed = scratch.build(items, "--concurrency", "1")
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        self._warm_record("left", completed)
        self._warm_record("right", completed)
        records = scratch.lane_records()
        self.assertEqual(len(records), 2, "state.json lane records" + tail(completed))
        for record in records:
            with self.subTest(lane=record.get("lane")):
                self.assertEqual(record.get("seeded"), ["cache/"])
                self.assertEqual(record.get("seed_skipped"), [])
                seconds = record.get("seed_seconds")
                self.assertIsInstance(seconds, (int, float))
                self.assertNotIsInstance(seconds, bool)
                self.assertGreaterEqual(seconds, 0)

    def a_write_to_the_seeded_copy_leaves_the_checkout_unchanged(self):
        self._darwin_only()
        scratch = self._scratch()
        checkout_file = os.path.join(scratch.repo, SEEDED_FILE)
        checkout_ino = os.lstat(checkout_file).st_ino
        items = scratch.items([step("left", ["left.py"], "demo")])
        scratch.jobs({"left": {"writes": {"left.py": LEFT}, "script": LSTAT_THEN_DIRTY}})
        completed = scratch.build(items)
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        record = scratch.record("left")
        self.assertIs(record.get("regular"), True, "the Worker saw no regular file" + tail(completed))
        self.assertIs(record.get("link"), False)
        self.assertNotEqual(record.get("ino"), checkout_ino)
        self.assertEqual(read_text(checkout_file), WARM)
        self.assertFalse(os.path.lexists(os.path.join(scratch.repo, "cache", "new.bin")))

    def the_check_author_clone_holds_the_listed_folder(self):
        self._darwin_only()
        scratch = self._scratch()
        items = scratch.items([check_author(), builder()])
        scratch.jobs(
            {
                "check-author": {
                    "writes": {CHECK_AUTHOR_TEST_FILE: EXACT_TEST, "calc.py": CORRECT_ADD},
                    "msp": "lock",
                    "script": RECORD_SEEDED,
                },
                "add": {"writes": {"calc.py": CORRECT_ADD}, "msp": "calc", "script": RECORD_SEEDED},
            }
        )
        completed = scratch.build(items, "--plan-file", saved_plan(items, scratch.repo))
        self._warm_record("check-author", completed)
        lock = ((scratch.state().get("msps") or {}).get("0") or {}).get("lock")
        self.assertIsInstance(lock, dict, "state.json holds no lock record for MSP calc" + tail(completed))
        self.assertEqual(lock.get("seeded"), ["cache/"])
        self.assertEqual(lock.get("state"), "ok", tail(completed))

    def no_worktreeinclude_seeds_nothing(self):
        scratch = self._scratch(worktreeinclude=None)
        items = scratch.items([step("left", ["left.py"], "demo")])
        scratch.jobs({"left": {"writes": {"left.py": LEFT}, "script": RECORD_SEEDED}})
        completed = scratch.build(items)
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        self.assertIs(scratch.record("left").get("cache"), False, "the Worker recorded a cache/")
        record = self._only_lane_record()
        self.assertEqual(record.get("seeded"), [])
        self.assertEqual(record.get("seed_skipped"), [])

    def an_empty_worktreeinclude_seeds_nothing(self):
        scratch = self._scratch(worktreeinclude=COMMENTS_ONLY)
        items = scratch.items([step("left", ["left.py"], "demo")])
        scratch.jobs({"left": {"writes": {"left.py": LEFT}, "script": RECORD_SEEDED}})
        completed = scratch.build(items)
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        self.assertIs(scratch.record("left").get("cache"), False, "the Worker recorded a cache/")
        record = self._only_lane_record()
        self.assertEqual(record.get("seeded"), [])
        self.assertEqual(record.get("seed_skipped"), [])

    def seed_plan_keeps_only_ignored_untracked_matches(self):
        folder = self._folder()
        repo = folder.repository(
            {
                ".gitignore": "cache/\nbuild/\nmixed/*.o\n",
                "mixed/keep.c": "int keep;\n",
                "tracked.txt": "tracked\n",
            },
            {
                "cache/sub/a.bin": WARM,
                "build/out": "out\n",
                "mixed/obj.o": "obj\n",
                "loose.txt": "loose\n",
                ".worktreeinclude": "cache/\nmixed/\ntracked.txt\nloose.txt\nmissing/\n",
            },
        )
        plan = load("isolation").seed_plan(repo)
        self.assertEqual(plan.get("paths"), ["cache/", "mixed/obj.o"])
        self.assertEqual(plan.get("skipped"), [])

    def seed_plan_refuses_a_virtual_environment(self):
        folder = self._folder()
        repo = folder.repository(
            {".gitignore": ".venv/\nvenv2/\n"},
            {
                ".venv/pyvenv.cfg": "home = /usr/bin\n",
                "venv2/pyvenv.cfg": "home = /usr/bin\n",
                ".worktreeinclude": ".venv/\nvenv2/\n",
            },
        )
        plan = load("isolation").seed_plan(repo)
        self.assertEqual(plan.get("paths"), [])
        self.assertEqual(sorted(self._skipped_paths(plan.get("skipped") or [])), [".venv/", "venv2/"])

    def seed_plan_refuses_secret_looking_names(self):
        folder = self._folder()
        listed = "".join(name + "\n" for name in SECRET_NAMES) + "keep/\n"
        repo = folder.repository(
            {".gitignore": "*\n!.gitignore\n!.worktreeinclude\n", ".worktreeinclude": listed},
            {**{name: "secret\n" for name in SECRET_NAMES}, "keep/a.bin": WARM},
        )
        plan = load("isolation").seed_plan(repo)
        self.assertEqual(plan.get("paths"), ["keep/"])
        self.assertEqual(
            sorted(self._skipped_paths(plan.get("skipped") or [])), sorted(SECRET_NAMES)
        )

    def copy_on_write_refuses_a_platform_without_it(self):
        folder = self._folder()
        source = os.path.join(folder.tmp, "src")
        put(os.path.join(source, "a.bin"), WARM)
        target = os.path.join(folder.tmp, "dst")
        isolation = load("isolation")
        with unittest.mock.patch.object(sys, "platform", "win32"):
            with self.assertRaises(OSError):
                isolation.copy_on_write(source, target)
        self.assertFalse(os.path.lexists(target))

    def seed_clone_skips_a_failed_copy_and_never_copies_plainly(self):
        folder = self._folder()
        repo = folder.repository({".gitignore": "cache/\n"}, {"cache/a.bin": WARM})
        isolation = load("isolation")
        plan = {"paths": ["cache/"], "skipped": []}
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                unittest.mock.patch.object(
                    isolation, "copy_on_write", side_effect=OSError(errno.ENOTSUP, "no clone")
                )
            )
            copiers = [stack.enter_context(unittest.mock.patch(name)) for name in PLAIN_COPIERS]
            result = isolation.seed_clone(repo, plan, folder.clone)
        self.assertEqual(result.get("seeded"), [])
        self.assertEqual(self._skipped_paths(result.get("skipped") or []), ["cache/"])
        self.assertFalse(os.path.lexists(os.path.join(folder.clone, "cache")))
        for name, copier in zip(PLAIN_COPIERS, copiers):
            with self.subTest(copier=name):
                self.assertFalse(copier.called, "%s was called" % name)

    def the_linux_branch_copies_by_reflink_or_leaves_nothing(self):
        folder = self._folder()
        source = os.path.join(folder.tmp, "src")
        put(os.path.join(source, "sub", "a.bin"), WARM)
        target = os.path.join(folder.tmp, "dst")
        isolation = load("isolation")
        refused = None
        with unittest.mock.patch.object(sys, "platform", "linux"):
            try:
                isolation.copy_on_write(source, target)
            except OSError as error:
                refused = error
        if refused is not None:
            self.assertFalse(os.path.lexists(target), "a refused copy left %s behind" % target)
            return
        copied = os.path.join(target, "sub", "a.bin")
        self.assertEqual(read_text(copied), WARM)
        self.assertNotEqual(
            os.lstat(copied).st_ino, os.lstat(os.path.join(source, "sub", "a.bin")).st_ino
        )

    def seed_clone_refuses_a_copy_with_a_file_link_leaving_the_clone(self):
        self._darwin_only()
        folder = self._folder()
        repo = folder.repository(
            {".gitignore": "cache/\ninner/\n", "README.md": "readme\n"},
            {"cache/a.bin": WARM, "inner/b.bin": "inner\n"},
        )
        folder.link("cache/rel", "a.bin")
        folder.link("cache/out", os.path.join(repo, "README.md"))
        folder.link("inner/rel", "b.bin")
        result = load("isolation").seed_clone(
            repo, {"paths": ["cache/", "inner/"], "skipped": []}, folder.clone
        )
        self.assertEqual(result.get("seeded"), ["inner/"])
        skipped = result.get("skipped") or []
        self.assertEqual(self._skipped_paths(skipped), ["cache/"])
        self._names_a_symbolic_link(skipped, "cache/")
        self.assertTrue(os.path.isfile(os.path.join(folder.clone, "inner", "b.bin")))
        self.assertTrue(os.path.lexists(os.path.join(folder.clone, "inner", "rel")))
        self.assertFalse(os.path.lexists(os.path.join(folder.clone, "cache")))

    def seed_clone_refuses_a_copy_with_a_folder_link_leaving_the_clone(self):
        self._darwin_only()
        folder = self._folder()
        repo = folder.repository(
            {".gitignore": "cache/\n", "docs/guide.md": "guide\n"}, {"cache/a.bin": WARM}
        )
        docs = os.path.join(repo, "docs")
        folder.link("cache/outdir", docs)
        before = snapshot(docs)
        result = load("isolation").seed_clone(repo, {"paths": ["cache/"], "skipped": []}, folder.clone)
        self.assertEqual(result.get("seeded"), [])
        skipped = result.get("skipped") or []
        self.assertEqual(self._skipped_paths(skipped), ["cache/"])
        self._names_a_symbolic_link(skipped, "cache/")
        self.assertFalse(os.path.lexists(os.path.join(folder.clone, "cache")))
        self.assertEqual(snapshot(docs), before)

    def seed_clone_refuses_a_copy_with_a_dangling_link_leaving_the_clone(self):
        self._darwin_only()
        folder = self._folder()
        repo = folder.repository({".gitignore": "cache/\n"}, {"cache/a.bin": WARM})
        folder.link("cache/gone", os.path.join(repo, "absent", "target"))
        result = load("isolation").seed_clone(repo, {"paths": ["cache/"], "skipped": []}, folder.clone)
        self.assertEqual(result.get("seeded"), [])
        skipped = result.get("skipped") or []
        self.assertEqual(self._skipped_paths(skipped), ["cache/"])
        self._names_a_symbolic_link(skipped, "cache/")
        self.assertFalse(os.path.lexists(os.path.join(folder.clone, "cache")))

    def seed_clone_refuses_a_seeded_path_that_is_a_link(self):
        self._darwin_only()
        folder = self._folder()
        outside = os.path.join(folder.tmp, "outside")
        put(os.path.join(outside, "a.bin"), WARM)
        repo = folder.repository({".gitignore": "linked\n"})
        folder.link("linked", outside)
        result = load("isolation").seed_clone(repo, {"paths": ["linked"], "skipped": []}, folder.clone)
        self.assertEqual(result.get("seeded"), [])
        skipped = result.get("skipped") or []
        self.assertEqual(self._skipped_paths(skipped), ["linked"])
        self._names_a_symbolic_link(skipped, "linked")
        self.assertFalse(os.path.lexists(os.path.join(folder.clone, "linked")))
        self.assertEqual(read_text(os.path.join(outside, "a.bin")), WARM)

    def seed_clone_makes_a_read_only_copy_removable(self):
        self._darwin_only()
        folder = self._folder()
        repo = folder.repository({".gitignore": "cache/\n"}, {"cache/sub/a.bin": WARM})
        checkout_sub = os.path.join(repo, "cache", "sub")
        os.chmod(checkout_sub, 0o555)
        result = load("isolation").seed_clone(repo, {"paths": ["cache/"], "skipped": []}, folder.clone)
        self.assertEqual(result.get("seeded"), ["cache/"])
        copied_sub = os.path.join(folder.clone, "cache", "sub")
        self.assertTrue(os.stat(copied_sub).st_mode & stat.S_IWUSR, "the copy's cache/sub is not owner-writable")
        shutil.rmtree(folder.clone)
        self.assertEqual(mode_of(checkout_sub), 0o555)

    def a_lane_runs_cold_when_copy_on_write_fails(self):
        scratch = self._scratch()
        items = scratch.items([step("left", ["left.py"], "demo")])
        scratch.jobs({"left": {"writes": {"left.py": LEFT}}})
        isolation = load("isolation")
        with unittest.mock.patch.object(
            isolation, "copy_on_write", side_effect=OSError(errno.ENOTSUP, "no clone")
        ):
            code, out, err = scratch.invoke(scratch.flags(items))
        self.assertEqual(code, 0, "mitosis.main returned %r\n%s\n%s" % (code, out[-4000:], err[-4000:]))
        record = self._only_lane_record()
        self.assertEqual(record.get("state"), "ok")
        self.assertEqual(record.get("seeded"), [])
        self.assertEqual(self._skipped_paths(record.get("seed_skipped") or []), ["cache/"])
        self.assertEqual(scratch.checked("show", "mitosis/demo:left.py"), LEFT)

    def seeded_writes_never_land(self):
        self._darwin_only()
        scratch = self._scratch()
        items = scratch.items([step("left", ["left.py", ".gitignore"], "demo")])
        scratch.jobs(
            {
                "left": {
                    "writes": {"left.py": LEFT},
                    "declared": ["left.py", ".gitignore"],
                    "script": UNIGNORE_THEN_DIRTY,
                }
            }
        )
        completed = scratch.build(items)
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        paths = scratch.tree_paths("mitosis/demo")
        self.assertIn("left.py", paths)
        self.assertIn(".gitignore", paths)
        self.assertEqual([path for path in paths if under_cache(path)], [])
        findings = summary_of(completed.stdout).get("findings") or []
        self.assertEqual([finding for finding in findings if names_cache(finding)], [])
        record = self._only_lane_record()
        self.assertEqual([path for path in record.get("ignored") or [] if under_cache(path)], [])

    def the_gate_checkout_holds_no_seeded_path(self):
        self._darwin_only()
        scratch = self._scratch()
        acceptance = [{"file": "tests/test_left.py", "test": "Left.defines"}]
        items = scratch.items([step("left", ["left.py", "tests/test_left.py"], "demo", acceptance)])
        scratch.jobs(
            {
                "left": {
                    "writes": {"left.py": LEFT, "tests/test_left.py": LEFT_TEST},
                    "script": RECORD_SEEDED,
                }
            }
        )
        completed = scratch.build(items, gate=True)
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        self._warm_record("left", completed)
        self.assertEqual(self._only_lane_record().get("seeded"), ["cache/"])
        runs = scratch.gate_runs()
        self.assertEqual(len(runs), 1, "acceptance runs: %r" % (runs,))
        self.assertEqual([entry for entry in runs if entry.get("cache") is not False], [])

    def a_resumed_lane_gets_a_fresh_seeded_copy(self):
        self._darwin_only()
        scratch = self._scratch()
        items = scratch.items([step("left", ["left.py"], "demo")])
        scratch.jobs(
            {"left": {"declared": ["left.py"], "left": LEFT, "script": DIRTY_FIRST_THEN_RECORD}}
        )
        first = scratch.build(items)
        self.assertEqual(self._lane(summary_of(first.stdout), "left").get("state"), "failed", tail(first))
        resumed = scratch.build(items, "--resume")
        self.assertEqual(resumed.returncode, 0, "resumed mitosis exit code" + tail(resumed))
        self.assertEqual(scratch.dispatched(), ["left", "left", "left"])
        record = scratch.record("left")
        self.assertIs(record.get("present"), True, "the second Worker saw no seeded file" + tail(resumed))
        self.assertEqual(record.get("bytes"), WARM)
        self.assertEqual(self._only_lane_record().get("seeded"), ["cache/"])

    def a_held_lane_keeps_its_seeded_list_across_resume(self):
        self._darwin_only()
        scratch = self._scratch()
        items = scratch.items([step("left", ["left.py"], "demo")])
        scratch.jobs(
            {"left": {"declared": ["left.py"], "left": LEFT, "script": WRITE_DIRTY_THEN_FAIL}}
        )
        first = scratch.build(items)
        dispatched = scratch.dispatched()
        state = scratch.state()
        lanes = state.get("lanes") or {}
        self.assertIn("0", lanes, "state.json holds no Lane 0" + tail(first))
        self.assertEqual(lanes["0"].get("seeded"), ["cache/"], tail(first))
        held = {**lanes["0"], "state": load("run").LANE_HELD, "returned": LEFT_SUCCESS}
        scratch.write_state({**state, "lanes": {**lanes, "0": held}})
        resumed = scratch.build(items, "--resume")
        self.assertEqual(resumed.returncode, 0, "resumed mitosis exit code" + tail(resumed))
        self.assertEqual(scratch.dispatched(), dispatched)
        paths = scratch.tree_paths("mitosis/demo")
        self.assertIn("left.py", paths)
        self.assertEqual([path for path in paths if under_cache(path)], [])
        record = self._only_lane_record()
        self.assertEqual(record.get("seeded"), ["cache/"])
        self.assertEqual([path for path in record.get("ignored") or [] if under_cache(path)], [])

    def a_landed_lane_leaves_no_seeded_clone(self):
        self._darwin_only()
        scratch = self._scratch()
        checkout_sub = os.path.join(scratch.repo, "cache", "sub")
        os.chmod(checkout_sub, 0o555)
        items = scratch.items([step("left", ["left.py"], "demo")])
        scratch.jobs({"left": {"writes": {"left.py": LEFT}, "script": RECORD_SEEDED}})
        completed = scratch.build(items)
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        self._warm_record("left", completed)
        self.assertEqual(self._only_lane_record().get("seeded"), ["cache/"])
        self.assertFalse(os.path.lexists(os.path.join(scratch.run_dir, "clones", "lane-0")))
        self.assertEqual(mode_of(checkout_sub), 0o555)

    def a_kept_seeded_clone_is_removable_by_rmtree(self):
        self._darwin_only()
        scratch = self._scratch()
        checkout_sub = os.path.join(scratch.repo, "cache", "sub")
        os.chmod(checkout_sub, 0o555)
        items = scratch.items([step("left", ["left.py"], "demo")])
        scratch.jobs({"left": {"script": FAIL_WITHOUT_WRITING}})
        completed = scratch.build(items)
        self.assertEqual(self._lane(summary_of(completed.stdout), "left").get("state"), "failed", tail(completed))
        self.assertTrue(
            os.path.isdir(os.path.join(scratch.run_dir, "clones", "lane-0", "cache", "sub")),
            "the kept clone holds no seeded cache/sub" + tail(completed),
        )
        shutil.rmtree(scratch.run_dir)
        self.assertEqual(mode_of(checkout_sub), 0o555)

    def the_readme_documents_the_worktreeinclude_opt_in(self):
        text = read_text(os.path.join(ROOT, "README.md"))
        for phrase in (".worktreeinclude", "copy-on-write"):
            with self.subTest(phrase=phrase):
                self.assertTrue(phrase in text, "README.md does not name %s" % phrase)

    def the_version_is_above_0_12_0_everywhere(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as handle:
            plugin = json.load(handle)
        version = load("core").__version__
        self.assertGreater(tuple(int(part) for part in version.split(".")), (0, 12, 0))
        self.assertEqual(plugin.get("version"), version)
        self.assertIn(version, read_text(os.path.join(ROOT, "CHANGELOG.md")))


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names_ in (
        (
            WarmClones,
            (
                "a_listed_ignored_folder_is_in_every_lane_clone",
                "a_write_to_the_seeded_copy_leaves_the_checkout_unchanged",
                "the_check_author_clone_holds_the_listed_folder",
                "no_worktreeinclude_seeds_nothing",
                "an_empty_worktreeinclude_seeds_nothing",
                "seed_plan_keeps_only_ignored_untracked_matches",
                "seed_plan_refuses_a_virtual_environment",
                "seed_plan_refuses_secret_looking_names",
                "copy_on_write_refuses_a_platform_without_it",
                "seed_clone_skips_a_failed_copy_and_never_copies_plainly",
                "the_linux_branch_copies_by_reflink_or_leaves_nothing",
                "seed_clone_refuses_a_copy_with_a_file_link_leaving_the_clone",
                "seed_clone_refuses_a_copy_with_a_folder_link_leaving_the_clone",
                "seed_clone_refuses_a_copy_with_a_dangling_link_leaving_the_clone",
                "seed_clone_refuses_a_seeded_path_that_is_a_link",
                "seed_clone_makes_a_read_only_copy_removable",
                "a_lane_runs_cold_when_copy_on_write_fails",
                "seeded_writes_never_land",
                "the_gate_checkout_holds_no_seeded_path",
                "a_resumed_lane_gets_a_fresh_seeded_copy",
                "a_held_lane_keeps_its_seeded_list_across_resume",
                "a_landed_lane_leaves_no_seeded_clone",
                "a_kept_seeded_clone_is_removable_by_rmtree",
                "the_readme_documents_the_worktreeinclude_opt_in",
                "the_version_is_above_0_12_0_everywhere",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names_)
    return suite


if __name__ == "__main__":
    unittest.main()
