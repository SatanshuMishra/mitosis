import errno
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
ADDS = {"file": TEST_FILE, "test": "Calc.adds"}
CORRECT_ADD = "def add(a, b):\n    return a + b\n"
OUTSIDE_ADD = "def add(a, b):\n    return 0\n"
SHIM_CALC = "from shim import add\n"
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
LOCKED_CHARTER = "# Charter\n\n- Python 3.9 or later.\n- Locked: tests/test_calc.py\n"

PROBE_GIT = r'''
git_dir = os.path.realpath(git("rev-parse", "--absolute-git-dir"))
common_dir = os.path.realpath(os.path.join(os.getcwd(), git("rev-parse", "--git-common-dir")))
objects = os.path.join(common_dir, "objects")
record = {
    **record,
    "git_dir": git_dir,
    "common_dir": common_dir,
    "alternates": any(
        os.path.exists(os.path.join(folder, "objects", "info", "alternates"))
        for folder in (git_dir, common_dir)
    ),
    "link_counts": sorted(
        {
            os.lstat(os.path.join(top, entry)).st_nlink
            for top, _, entries in os.walk(objects)
            for entry in entries
        }
    ),
    "remotes": git("remote"),
}
'''

MOVE_REFS = r'''
forged = git("commit-tree", git("rev-parse", "HEAD~1^{tree}"), "-m", "forged")
git("update-ref", "refs/heads/feature", forged)
git("update-ref", "refs/heads/mitosis/demo", forged)
git("commit", "-q", "-m", "forged", "--allow-empty")
'''

TAMPER_GIT_STATE = r'''
base = git("rev-parse", "HEAD")
forged = git("commit-tree", git("rev-parse", "HEAD^{tree}"), "-m", "forged")
git("replace", base, forged)
put(git("rev-parse", "--git-path", "info/grafts"), base + "\n")
git("config", "core.hooksPath", job["hooks"])
git("config", "core.fsmonitor", job["monitor"])
git("config", "filter.flip.clean", "sed -e 's/a + b/a - b/'")
git("config", "filter.flip.smudge", "cat")
put(git("rev-parse", "--git-path", "info/attributes"), "calc.py filter=flip\n")
'''

LINK_PARENT = r'''
shutil.rmtree("pkg")
os.symlink(job["outside"], "pkg")
'''

COMMIT_IN_CLONE = r'''
git("add", "-A")
git("commit", "-q", "-m", "tamper")
'''

ATTEMPT_ESCAPES = r'''
targets = job["targets"]
before = {key: digest(path) for key, path in targets.items()}
attempts = {key: attempt(path, job["marker"]) for key, path in targets.items()}
after = {key: digest(path) for key, path in targets.items()}
record = {**record, "attempts": attempts, "before": before, "after": after}
'''

ALLOWED_WRITES = r'''
key = re.sub(r"[^A-Za-z0-9]", "-", record["cwd"])
transcript = os.path.join(os.environ["HOME"], ".claude", "projects", key)
try:
    os.makedirs(transcript, exist_ok=True)
except OSError:
    pass
scratch_file = os.path.join(os.environ.get("TMPDIR", "/nonexistent"), "probe.txt")
record = {
    **record,
    "key": key,
    "tmp_write": attempt(scratch_file, "tmp\n", "w"),
    "tmp_read": digest(scratch_file),
    "transcript_write": attempt(os.path.join(transcript, "probe.jsonl"), '{"probe": true}\n', "w"),
    "settings_write": attempt(
        os.path.join(os.environ["HOME"], ".claude", "settings.json"), "{}\n", "w"
    ),
}
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


class WorkerIsolation(unittest.TestCase):
    def setUp(self):
        self.scratch = None

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()

    def _scratch(self, seeded=None):
        self.scratch = Scratch(seeded)
        return self.scratch

    def _msp(self, summary, label="demo"):
        found = [msp for msp in summary.get("msps") or [] if msp.get("msp") == label]
        self.assertEqual(len(found), 1, "the JSON summary lists no MSP %s" % label)
        return found[0]

    def _lane(self, summary, name):
        found = [lane for lane in summary.get("lanes") or [] if name in (lane.get("steps") or [])]
        self.assertEqual(len(found), 1, "the JSON summary lists no Lane for %s" % name)
        return found[0]

    def _first_property(self, msp):
        gate = msp.get("gate") or {}
        self.assertTrue(gate.get("properties"), "the MSP's gate object carries no properties")
        return gate["properties"][0]

    def _finding(self, summary, kind, path, msp=None):
        return [
            finding
            for finding in summary.get("findings") or []
            if finding.get("kind") == kind
            and names(finding.get("detail"), path)
            and (msp is None or finding.get("msp") == msp)
        ]

    def _ran_in_a_clone(self, name, label="demo"):
        record = self.scratch.record(name)
        self.assertIn("cwd", record, "the stub Worker for %s recorded nothing" % name)
        run_dir = os.path.realpath(self.scratch.run_dir)
        self.assertNotEqual(
            record["cwd"],
            os.path.join(run_dir, "trees", label),
            "the Worker for %s ran in the MSP worktree, not in a clone of its own" % name,
        )
        self.assertTrue(
            inside(record["cwd"], run_dir),
            "the Worker for %s ran in %s, outside the run directory" % (name, record["cwd"]),
        )

    def each_lane_worker_gets_its_own_clone(self):
        scratch = self._scratch()
        items = scratch.items(
            [step("left", ["left.py"], "demo"), step("right", ["right.py"], "demo")]
        )
        scratch.jobs(
            {
                "left": {"writes": {"left.py": "LEFT = 1\n"}, "script": PROBE_GIT},
                "right": {"writes": {"right.py": "RIGHT = 2\n"}, "script": PROBE_GIT},
            }
        )
        completed = scratch.build(items, "--concurrency", "1")
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        summary = summary_of(completed)
        self.assertTrue(summary.get("run_dir"), "the JSON summary names no run directory")
        msp_tree = os.path.join(os.path.realpath(summary["run_dir"]), "trees", "demo")
        left, right = scratch.record("left"), scratch.record("right")
        self.assertIn("git_dir", left, "the left Worker recorded nothing" + tail(completed))
        self.assertIn("git_dir", right, "the right Worker recorded nothing" + tail(completed))
        self.assertNotEqual(left["cwd"], right["cwd"])
        for record in (left, right):
            with self.subTest(cwd=record["cwd"]):
                self.assertNotEqual(record["cwd"], msp_tree)
                self.assertTrue(
                    inside(record["git_dir"], record["cwd"]),
                    "git directory %s lies outside %s" % (record["git_dir"], record["cwd"]),
                )
                self.assertTrue(
                    inside(record["common_dir"], record["cwd"]),
                    "common directory %s lies outside %s" % (record["common_dir"], record["cwd"]),
                )
                self.assertIs(record["alternates"], False)
                self.assertEqual(record["link_counts"], [1])
                self.assertEqual(record["remotes"], "")

    def a_ref_move_in_the_clone_changes_nothing_judged(self):
        scratch = self._scratch({TEST_FILE: EXACT_TEST})
        feature_before = scratch.checked("rev-parse", "feature").strip()
        items = scratch.items([step("add", ["calc.py"], "demo", acceptance=[ADDS])])
        scratch.jobs(
            {
                "add": {
                    "writes": {"calc.py": CORRECT_ADD},
                    "declared": ["calc.py"],
                    "script": MOVE_REFS,
                }
            }
        )
        completed = scratch.build(items)
        self.assertEqual(scratch.checked("rev-parse", "feature").strip(), feature_before)
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        head = scratch.checked("rev-parse", "mitosis/demo").strip()
        self.assertEqual(
            scratch.checked("log", "-1", "--format=%s", head).strip(),
            "chore(demo): land Lane 0 (add)",
        )
        self.assertEqual(
            scratch.checked("rev-list", "--parents", "-n", "1", head).split(),
            [head, feature_before],
        )
        self.assertEqual(
            scratch.checked("diff", "--name-status", feature_before, head).strip(), "A\tcalc.py"
        )
        self.assertEqual(scratch.checked("show", head + ":calc.py"), CORRECT_ADD)
        self.assertEqual(self._first_property(self._msp(summary_of(completed)))["outcome"], "pass")

    def replace_refs_grafts_and_config_in_the_clone_change_nothing_judged(self):
        scratch = self._scratch({TEST_FILE: EXACT_TEST})
        hooks = os.path.join(scratch.stubs, "hooks")
        for hook in ("pre-commit", "post-commit", "post-checkout"):
            put(
                os.path.join(hooks, hook),
                "#!/bin/sh\necho %s > %s\n"
                % (hook, shlex.quote(os.path.join(scratch.markers, hook))),
            )
            os.chmod(os.path.join(hooks, hook), 0o755)
        monitor = put(
            os.path.join(scratch.stubs, "fsmonitor.sh"),
            "#!/bin/sh\necho fsmonitor > %s\nexit 1\n"
            % shlex.quote(os.path.join(scratch.markers, "fsmonitor")),
        )
        os.chmod(monitor, 0o755)
        config = os.path.join(scratch.repo, ".git", "config")
        config_before = read_bytes(config)
        items = scratch.items([step("add", ["calc.py"], "demo", acceptance=[ADDS])])
        scratch.jobs(
            {
                "add": {
                    "writes": {"calc.py": CORRECT_ADD},
                    "declared": ["calc.py"],
                    "script": TAMPER_GIT_STATE,
                    "hooks": hooks,
                    "monitor": monitor,
                }
            }
        )
        completed = scratch.build(items)
        markers = sorted(os.listdir(scratch.markers))
        self.assertEqual(markers, [], "a hook, filter or fsmonitor of the clone ran" + tail(completed))
        self.assertEqual(read_bytes(config), config_before)
        self.assertEqual(scratch.checked("for-each-ref", "refs/replace"), "")
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        self.assertEqual(scratch.checked("show", "mitosis/demo:calc.py"), CORRECT_ADD)
        self.assertEqual(self._first_property(self._msp(summary_of(completed)))["outcome"], "pass")

    def a_write_set_path_behind_a_symlink_fails_the_lane(self):
        scratch = self._scratch()
        outside = os.path.join(scratch.tmp, "outside")
        put(os.path.join(outside, "calc.py"), OUTSIDE_ADD)
        items = scratch.items([step("add", ["pkg/calc.py"], "demo")])
        scratch.jobs(
            {
                "add": {
                    "writes": {"pkg/calc.py": CORRECT_ADD},
                    "declared": ["pkg/calc.py"],
                    "script": LINK_PARENT,
                    "outside": outside,
                }
            }
        )
        completed = scratch.build(items)
        self._ran_in_a_clone("add")
        self.assertNotEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        lane = self._lane(summary_of(completed), "add")
        self.assertEqual(lane["state"], "failed")
        self.assertIn("pkg", lane["reason"] or "")
        self.assertIn("link", (lane["reason"] or "").lower())
        landed = scratch.git("show", "mitosis/demo:pkg/calc.py")
        self.assertFalse(
            landed.returncode == 0 and landed.stdout == OUTSIDE_ADD,
            "the MSP branch holds pkg/calc.py from the folder outside the clone",
        )

    def write_set_entries_land_with_their_kind(self):
        scratch = self._scratch({"old.txt": "old\n"})
        items = scratch.items([step("add", ["run.sh", "link.txt", "old.txt"], "demo")])
        scratch.jobs(
            {
                "add": {
                    "writes": {"run.sh": "#!/bin/sh\necho run\n"},
                    "executable": ["run.sh"],
                    "links": {"link.txt": "run.sh"},
                    "deletes": ["old.txt"],
                    "declared": ["link.txt", "old.txt", "run.sh"],
                }
            }
        )
        completed = scratch.build(items)
        self._ran_in_a_clone("add")
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        self.assertEqual(
            scratch.checked("ls-tree", "mitosis/demo", "run.sh").split()[0], "100755"
        )
        self.assertEqual(
            scratch.checked("ls-tree", "mitosis/demo", "link.txt").split()[0], "120000"
        )
        self.assertEqual(scratch.checked("show", "mitosis/demo:link.txt"), "run.sh")
        self.assertEqual(scratch.checked("ls-tree", "mitosis/demo", "old.txt"), "")

    def a_sandboxed_worker_cannot_write_outside_its_clone(self):
        if sys.platform != "darwin":
            self.skipTest("sandbox-exec exists only on macOS")
        scratch = self._scratch()
        elsewhere = put(os.path.join(scratch.tmp, "elsewhere", "target.txt"), "elsewhere\n")
        targets = {
            "git": os.path.join(scratch.repo, ".git", "refs", "heads", "feature"),
            "run": os.path.join(scratch.run_dir, "state.json"),
            "tree": os.path.join(scratch.repo, "README.md"),
            "other": elsewhere,
        }
        kept = {key: read_bytes(targets[key]) for key in ("git", "tree", "other")}
        marker = "sandbox escape\n"
        items = scratch.items([step("add", ["calc.py"], "demo")])
        scratch.jobs(
            {
                "add": {
                    "writes": {"calc.py": CORRECT_ADD},
                    "script": ATTEMPT_ESCAPES,
                    "targets": targets,
                    "marker": marker,
                }
            }
        )
        completed = scratch.build(items, "--worker-sandbox", "--worker-write", scratch.stubs)
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        self.assertEqual(summary_of(completed).get("sandbox"), "sandbox-exec")
        record = scratch.record("add")
        self.assertEqual(record.get("attempts"), {key: errno.EPERM for key in targets})
        self.assertEqual(record["before"], record["after"])
        for key, content in kept.items():
            with self.subTest(target=key):
                self.assertEqual(read_bytes(targets[key]), content)
        self.assertNotIn(marker.encode("utf-8"), read_bytes(targets["run"]))
        self.assertEqual(
            scratch.checked("diff", "--name-only", "feature", "mitosis/demo").split(), ["calc.py"]
        )

    def the_clone_temp_and_transcript_folders_stay_writable(self):
        if sys.platform != "darwin":
            self.skipTest("sandbox-exec exists only on macOS")
        scratch = self._scratch()
        claude_home = os.path.join(scratch.env["HOME"], ".claude")
        os.makedirs(claude_home)
        items = scratch.items([step("add", ["calc.py"], "demo")])
        scratch.jobs({"add": {"writes": {"calc.py": CORRECT_ADD}, "script": ALLOWED_WRITES}})
        completed = scratch.build(items, "--worker-sandbox", "--worker-write", scratch.stubs)
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        record = scratch.record("add")
        self.assertEqual(record.get("tmp_write"), 0, "the TMPDIR write" + tail(completed))
        self.assertIsNotNone(record.get("tmp_read"))
        self.assertEqual(record.get("transcript_write"), 0, "the transcript write")
        key = re.sub(r"[^A-Za-z0-9]", "-", record["cwd"])
        probe = os.path.join(claude_home, "projects", key, "probe.jsonl")
        self.assertTrue(os.path.exists(probe), "%s was not written" % probe)
        self.assertEqual(read_bytes(probe), b'{"probe": true}\n')
        self.assertEqual(record.get("settings_write"), errno.EPERM)
        self.assertFalse(os.path.exists(os.path.join(claude_home, "settings.json")))

    def a_host_without_a_sandbox_refuses_before_any_worker(self):
        scratch = self._scratch()
        bare = os.path.join(scratch.tmp, "bare-bin")
        os.makedirs(bare)
        os.symlink(shutil.which("git", path=os.environ.get("PATH")), os.path.join(bare, "git"))
        os.symlink(sys.executable, os.path.join(bare, "python3"))
        items = scratch.items([step("add", ["calc.py"], "demo")])
        scratch.jobs({"add": {"writes": {"calc.py": CORRECT_ADD}}})
        completed = scratch.build(items, "--worker-sandbox", env={"PATH": bare})
        self.assertEqual(completed.returncode, 3, "mitosis exit code" + tail(completed))
        self.assertIn("sandbox-exec", completed.stderr)
        self.assertFalse(os.path.exists(os.path.join(scratch.stubs, "dispatch.log")))

    def an_unsandboxed_run_says_so(self):
        scratch = self._scratch()
        items = scratch.items([step("add", ["calc.py"], "demo")])
        scratch.jobs({"add": {"writes": {"calc.py": CORRECT_ADD}}})
        completed = scratch.build(items)
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        self.assertEqual(summary_of(completed).get("sandbox"), "off")

    def a_write_outside_the_write_set_is_reported_and_never_landed(self):
        scratch = self._scratch()
        items = scratch.items(
            [
                step("first", ["a.py"], "demo"),
                step("second", ["b.py"], "demo"),
            ]
        )
        scratch.jobs(
            {
                "first": {"writes": {"a.py": "A = 1\n"}},
                "second": {
                    "writes": {"b.py": "B = 2\n", "stray.txt": "stray\n", "a.py": "A = 99\n"},
                    "declared": ["b.py"],
                },
            }
        )
        completed = scratch.build(items, "--concurrency", "1")
        self._ran_in_a_clone("second")
        summary = summary_of(completed)
        self.assertTrue(
            self._finding(summary, "undeclared", "stray.txt"),
            "no undeclared finding names stray.txt" + tail(completed),
        )
        self.assertTrue(
            self._finding(summary, "foreign", "a.py"),
            "no foreign finding names a.py" + tail(completed),
        )
        self.assertEqual(scratch.checked("show", "mitosis/demo:stray.txt"), "stray\n")
        self.assertFalse(os.path.exists(os.path.join(scratch.repo, "stray.txt")))
        self.assertEqual(completed.returncode, 8, "mitosis exit code" + tail(completed))

    def an_outside_write_never_reaches_the_gate(self):
        scratch = self._scratch({TEST_FILE: EXACT_TEST})
        items = scratch.items([step("add", ["calc.py"], "demo", acceptance=[ADDS])])
        scratch.jobs(
            {
                "add": {
                    "writes": {"calc.py": SHIM_CALC, "shim.py": CORRECT_ADD},
                    "declared": ["calc.py"],
                }
            }
        )
        completed = scratch.build(items)
        summary = summary_of(completed)
        self.assertTrue(
            self._finding(summary, "undeclared", "shim.py"),
            "no undeclared finding names shim.py" + tail(completed),
        )
        self.assertEqual(scratch.checked("show", "mitosis/demo:shim.py"), CORRECT_ADD)
        first = self._first_property(self._msp(summary))
        self.assertEqual(first["outcome"], "inconclusive", "gate property" + tail(completed))
        self.assertEqual(first["reason"], "exit 1 with the work present")
        self.assertEqual(completed.returncode, 21, "mitosis exit code" + tail(completed))

    def a_locked_write_in_the_clone_is_reverted_and_fatal(self):
        scratch = self._scratch({TEST_FILE: EXACT_TEST})
        charter = scratch.file("charter.md", LOCKED_CHARTER)
        items = scratch.items([step("add", ["calc.py"], "demo")])
        scratch.jobs(
            {
                "add": {
                    "writes": {
                        "calc.py": CORRECT_ADD,
                        TEST_FILE: "import unittest\nraise SystemExit(0)\n",
                    },
                    "declared": ["calc.py"],
                    "script": COMMIT_IN_CLONE,
                }
            }
        )
        completed = scratch.build(items, "--charter", charter)
        self._ran_in_a_clone("add")
        self.assertEqual(completed.returncode, 9, "mitosis exit code" + tail(completed))
        msp = self._msp(summary_of(completed))
        self.assertEqual(msp["state"], "referee-reverted")
        self.assertEqual((msp.get("reconcile") or {}).get("referee"), [TEST_FILE])
        self.assertEqual(scratch.checked("show", "mitosis/demo:" + TEST_FILE), EXACT_TEST)


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names_ in (
        (
            WorkerIsolation,
            (
                "each_lane_worker_gets_its_own_clone",
                "a_ref_move_in_the_clone_changes_nothing_judged",
                "replace_refs_grafts_and_config_in_the_clone_change_nothing_judged",
                "a_write_set_path_behind_a_symlink_fails_the_lane",
                "write_set_entries_land_with_their_kind",
                "a_sandboxed_worker_cannot_write_outside_its_clone",
                "the_clone_temp_and_transcript_folders_stay_writable",
                "a_host_without_a_sandbox_refuses_before_any_worker",
                "an_unsandboxed_run_says_so",
                "a_write_outside_the_write_set_is_reported_and_never_landed",
                "an_outside_write_never_reaches_the_gate",
                "a_locked_write_in_the_clone_is_reverted_and_fatal",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names_)
    return suite


if __name__ == "__main__":
    unittest.main()
