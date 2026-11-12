import hashlib
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
LOCK_TIMEOUT = ("--timeout", "600")
STRIPPED = (
    "CLAUDE_CODE_SESSION_ID",
    "PLUGIN_METRICS_PARENT",
    "SHIP",
    "PAGER",
    "GH_PROMPT_DISABLED",
)
GIT_IDENTITY = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "mitosis test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "mitosis test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}

SRC_A = "src/a.py"
SRC_B = "src/b.py"
TEST_A = "tests/test_a.py"
TEST_B = "tests/test_b.py"
TEST_C = "tests/test_c.py"
BASE_SOURCE = "def value():\n    return 1\n"
WORK_SOURCE = "def value():\n    return 2\n"
HIT_LIMIT = "You've hit your limit \u00b7 resets 3pm (America/Vancouver)"
SSH_OPTIONS = "-o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
HTTP_SETTINGS = (["http.lowSpeedLimit", "1"], ["http.lowSpeedTime", "60"])
SPEC_SECTION = "1.1"
SPEC_DOCUMENT = "# Spec\n\n## 1.1 Build\n"
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

WORKER = r'''
import json
import os
import re
import select
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
SESSION = sys.argv[-2]
del sys.argv[-2:]


def replied(reply):
    print(json.dumps({"type": "result", "session_id": SESSION, "result": "done", "structured_output": reply}))


def at_end():
    try:
        ready, _, _ = select.select([0], [], [], 0)
    except (OSError, ValueError):
        return True
    if not ready:
        return False
    try:
        return os.read(0, 1) == b""
    except OSError:
        return True


def config_entries():
    count = int(os.environ.get("GIT_CONFIG_COUNT") or "0")
    return [
        [os.environ.get("GIT_CONFIG_KEY_%d" % position), os.environ.get("GIT_CONFIG_VALUE_%d" % position)]
        for position in range(count)
    ]


def put(path, content):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)


def failed(name, why):
    replied({"item": name, "status": "failed", "files_changed": [], "notes": why})


with open(os.path.join(here, "worker.json"), encoding="utf-8") as handle:
    jobs = json.load(handle)
brief = " ".join(sys.argv[1:])
named = [
    name
    for name in sorted(jobs)
    if re.search(r"(?<![\w-])task-%s(?![\w-])" % re.escape(name), brief)
]
if len(named) != 1:
    failed("unknown", "no job")
    sys.exit(1)
name = named[0]
job = jobs[name]
with open(os.path.join(here, "dispatch.log"), "a", encoding="utf-8") as handle:
    handle.write(name + "\n")
record = {
    "eof": at_end(),
    "env": {
        key: os.environ.get(key)
        for key in ("GIT_TERMINAL_PROMPT", "GH_PROMPT_DISABLED", "GIT_PAGER", "PAGER", "GIT_SSH_COMMAND")
    },
    "config": config_entries(),
}
os.makedirs(os.path.join(here, "records"), exist_ok=True)
with open(os.path.join(here, "records", name + ".json"), "w", encoding="utf-8") as handle:
    json.dump(record, handle)
if job.get("fail_once"):
    marker = os.path.join(here, "failed-" + name)
    if not os.path.exists(marker):
        open(marker, "w").close()
        failed(name, "first run fails")
        sys.exit(1)
if job.get("limit"):
    sys.stdout.buffer.write((job["limit"] + "\n").encode("utf-8"))
    sys.stdout.flush()
    sys.exit(1)
if job.get("exit"):
    failed(name, "scripted failure")
    sys.exit(job["exit"])
time.sleep(job.get("sleep", 0))
for path, content in sorted(job.get("writes", {}).items()):
    put(path, content)
replied({"item": name, "status": "ok", "files_changed": sorted(job.get("writes", {})), "notes": "done"})
'''

ACCEPT = r'''
import os
import subprocess
import sys

file, test = sys.argv[1], sys.argv[2]
if not os.path.isfile(file):
    sys.exit(2)
folder, name = os.path.split(file)
module = os.path.splitext(name)[0]
env = dict(
    os.environ,
    PYTHONDONTWRITEBYTECODE="1",
    PYTHONPATH=os.pathsep.join([os.path.abspath(folder), os.path.abspath("src")]),
)
try:
    code = subprocess.run(
        [sys.executable, "-m", "unittest", module + "." + test],
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=300,
    ).returncode
except subprocess.TimeoutExpired:
    sys.exit(2)
sys.exit(code if code in (0, 1) else 2)
'''

PULL_REQUEST = r'''
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "pr.log"), "a", encoding="utf-8") as handle:
    handle.write(sys.argv[1] + "\n")
print("https://example.invalid/pull/1")
'''

RENDER = r'''
import json
print(json.dumps({"title": "feat: lock each MSP", "body": "## Problem\nCallers need it.\n\n## Change\n- Lock each MSP.\n\n## Impact\nEach MSP carries its own tests.\n\n## Testing\n| Check | Result |\n|---|---|\n| tests/test_per_msp_lock_run.py | passed |"}))
'''

PR_FLAGS = (
    "--pr-type",
    "feat",
    "--pr-subject",
    "lock each MSP",
    "--pr-why",
    "Callers need it.",
    "--pr-impact",
    "Each MSP carries its own tests.",
)


def module_of(path):
    return os.path.splitext(os.path.basename(path))[0][len("test_") :]


def test_name(path):
    return "%s.returns_two" % module_of(path).upper()


def checking(path, imported=None):
    module = imported or module_of(path)
    return (
        "import unittest\n"
        "\n"
        "import %s\n"
        "\n"
        "\n"
        "class %s(unittest.TestCase):\n"
        "    def returns_two(self):\n"
        "        self.assertEqual(%s.value(), 2)\n" % (module, module_of(path).upper(), module)
    )


def passing(path):
    module = module_of(path)
    return (
        "import unittest\n"
        "\n"
        "import %s\n"
        "\n"
        "\n"
        "class %s(unittest.TestCase):\n"
        "    def returns_two(self):\n"
        "        self.assertIn(%s.value(), (1, 2))\n" % (module, module.upper(), module)
    )


def crashing(path):
    module = module_of(path)
    return (
        "import os\n"
        "import unittest\n"
        "\n"
        "import %s\n"
        "\n"
        "if %s.value() != 2:\n"
        "    os._exit(2)\n"
        "\n"
        "\n"
        "class %s(unittest.TestCase):\n"
        "    def returns_two(self):\n"
        "        self.assertEqual(%s.value(), 2)\n" % (module, module, module.upper(), module)
    )


def check_author(name, files, msp=None):
    found = {
        "name": name,
        "task": "Run task-" + name + ".",
        "files": list(files),
        "acceptance": [],
        "role": "check-author",
    }
    return found if msp is None else {**found, "msp": msp}


def builder(name, files, msp, acceptance, after):
    return {
        "name": name,
        "task": "Run task-" + name + ".",
        "files": list(files),
        "acceptance": [{"file": path, "test": test_name(path)} for path in acceptance],
        "msp": msp,
        "after": list(after),
    }


def labelled(
    beta_author_msp="beta",
    b_extra_files=(),
    b_extra_acceptance=(),
    b_after_a=False,
    gamma=False,
):
    return (
        [
            check_author("check-author-alpha", [TEST_A], "alpha"),
            check_author("check-author-beta", [TEST_B], beta_author_msp),
            builder("build-a", [SRC_A], "alpha", [TEST_A], ["check-author-alpha"]),
            builder(
                "build-b",
                [SRC_B, *b_extra_files],
                "beta",
                [TEST_B, *b_extra_acceptance],
                ["check-author-beta"] + (["build-a"] if b_after_a else []),
            ),
        ]
        + ([builder("build-c", ["src/c.py"], "gamma", [], ["build-a"])] if gamma else [])
    )


def unlabelled(files, b_extra_acceptance=()):
    return [
        check_author("check-author", files),
        builder("build-a", [SRC_A], "alpha", [TEST_A], ["check-author"]),
        builder("build-b", [SRC_B], "beta", [TEST_B, *b_extra_acceptance], ["check-author"]),
    ]


def standard_jobs(**changed):
    jobs = {
        "check-author-alpha": {"writes": {TEST_A: checking(TEST_A), SRC_A: WORK_SOURCE}},
        "check-author-beta": {"writes": {TEST_B: checking(TEST_B), SRC_B: WORK_SOURCE}},
        "build-a": {"writes": {SRC_A: WORK_SOURCE}},
        "build-b": {"writes": {SRC_B: WORK_SOURCE}},
        "build-c": {"writes": {"src/c.py": WORK_SOURCE}},
    }
    return {**jobs, **changed}


def put(path, content):
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content.encode("utf-8"))
    return path


def owned_steps(steps, folder):
    spec = put(os.path.join(folder, "spec.md"), SPEC_DOCUMENT)
    source = {"path": os.path.abspath(spec), "sha256": hashlib.sha256(SPEC_DOCUMENT.encode("utf-8")).hexdigest()}
    return [{**step, "source": source, "spec_ref": [SPEC_SECTION]} for step in steps]


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


def names_path(text, path):
    return re.search(r"(?<![\w/.-])%s(?![\w.-])" % re.escape(path), text or "") is not None


def names_word(text, word):
    return re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(word), text or "") is not None


def kept_environment():
    return {
        key: value
        for key, value in os.environ.items()
        if key not in STRIPPED and not key.startswith("GIT_")
    }


class Scratch:
    def __init__(self, seeded=None):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="mitosis-per-msp-lock-"))
        self.repo = os.path.join(self.tmp, "repo")
        self.remote = os.path.join(self.tmp, "remote.git")
        self.stubs = os.path.join(self.tmp, "stubs")
        self.run_dir = os.path.join(self.tmp, "runs", "run")
        self.env = {
            **kept_environment(),
            **GIT_IDENTITY,
            "HOME": os.path.join(self.tmp, "home"),
            "PLUGIN_METRICS_DIR": os.path.join(self.tmp, "metrics"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        os.makedirs(self.env["HOME"])
        os.makedirs(self.repo)
        os.makedirs(self.stubs)
        self.checked("init", "-q", "-b", "main", self.repo)
        self.checked("init", "-q", "--bare", self.remote)
        put(os.path.join(self.repo, "README.md"), "seed\n")
        put(os.path.join(self.repo, ".gitignore"), "__pycache__/\n")
        self.checked("-C", self.repo, "add", "-A")
        self.checked("-C", self.repo, "commit", "-q", "-m", "seed")
        self.checked("-C", self.repo, "checkout", "-q", "-b", "feature")
        put(os.path.join(self.repo, SRC_A), BASE_SOURCE)
        put(os.path.join(self.repo, SRC_B), BASE_SOURCE)
        for path, content in sorted((seeded or {}).items()):
            put(os.path.join(self.repo, path), content)
        self.checked("-C", self.repo, "add", "-A")
        self.checked("-C", self.repo, "commit", "-q", "-m", "seed the feature branch")
        self.checked("-C", self.repo, "checkout", "-q", "main")
        self.checked("-C", self.repo, "remote", "add", "origin", self.remote)
        put(os.path.join(self.stubs, "worker.py"), WORKER)
        put(os.path.join(self.stubs, "accept.py"), ACCEPT)
        put(os.path.join(self.stubs, "pr.py"), PULL_REQUEST)
        self.render = put(
            os.path.join(self.stubs, "render.py"), "#!%s\n%s" % (sys.executable, RENDER)
        )
        os.chmod(self.render, 0o755)

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

    def remote_checked(self, *args):
        return self.checked("--git-dir", self.remote, *args)

    def remote_ref(self, ref):
        completed = self.git("--git-dir", self.remote, "rev-parse", "--verify", "--quiet", ref)
        return completed.stdout.strip() if completed.returncode == 0 else None

    def remote_refs(self):
        return self.remote_checked("for-each-ref", "--format=%(refname)").split()

    def feature(self):
        return self.checked("rev-parse", "feature").strip()

    def ref(self, ref):
        completed = self.git("rev-parse", "--verify", "--quiet", ref)
        return completed.stdout.strip() if completed.returncode == 0 else None

    def tree(self, ref):
        return frozenset(self.checked("ls-tree", "-r", "--name-only", ref).split("\n")) - {""}

    def first_parent_commits(self, base, ref, git_dir=None):
        prefix = ("--git-dir", git_dir) if git_dir else ()
        return self.checked(*prefix, "rev-list", "--reverse", "--first-parent", base + ".." + ref).split()

    def first_after(self, base, ref, git_dir=None):
        commits = self.first_parent_commits(base, ref, git_dir)
        return commits[0] if commits else None

    def parents(self, commit, git_dir=None):
        prefix = ("--git-dir", git_dir) if git_dir else ()
        return self.checked(*prefix, "rev-list", "--parents", "-n", "1", commit).split()[1:]

    def changes(self, before, after, git_dir=None):
        prefix = ("--git-dir", git_dir) if git_dir else ()
        listed = self.checked(*prefix, "diff", "--no-renames", "--name-status", before, after)
        return sorted(tuple(line.split("\t", 1)) for line in listed.splitlines() if line)

    def changed_paths(self, before, after):
        return frozenset(path for _, path in self.changes(before, after))

    def is_ancestor(self, commit, ref):
        return self.git("merge-base", "--is-ancestor", commit, ref).returncode == 0

    def items(self, steps, name="items.json"):
        return put(os.path.join(self.tmp, name), json.dumps(owned_steps(steps, self.tmp)))

    def jobs(self, jobs):
        return put(os.path.join(self.stubs, "worker.json"), json.dumps(jobs))

    def stub(self, script, *words):
        return " ".join(
            [shlex.quote(sys.executable), shlex.quote(os.path.join(self.stubs, script))]
            + list(words)
        )

    def mitosis(self, *flags, env=None):
        reader, writer = os.pipe()
        try:
            return subprocess.run(
                [sys.executable, MITOSIS, *flags],
                cwd=self.repo,
                env={**self.env, **(env or {})},
                stdin=reader,
                capture_output=True,
                text=True,
                timeout=RUN_LIMIT,
            )
        finally:
            os.close(reader)
            os.close(writer)

    def build(self, items, *extra, env=None):
        return self.mitosis(
            "--items",
            items,
            "--run-dir",
            self.run_dir,
            "--feature-branch",
            "feature",
            "--dispatch-command",
            self.stub("worker.py", "{task}", "{session_id}", "{schema}"),
            "--reask-command",
            REASK_COMMAND,
            "--acceptance-command",
            self.stub("accept.py", "{file}", "{test}"),
            *extra,
            env=env,
        )

    def pushing(self, items, *extra, env=None):
        return self.build(
            items,
            *PR_FLAGS,
            "--pr-command",
            self.stub("pr.py", "{branch}"),
            "--render-command",
            self.render,
            *extra,
            env=env,
        )

    def saved(self, items, *flags):
        return saved_plan(items, self.repo, *flags)

    def plan_only(self, steps, label, *flags):
        return self.mitosis(
            "--items",
            self.items(steps, label + ".json"),
            "--plan-only",
            "--feature-branch",
            "feature",
            "--run-dir",
            os.path.join(self.tmp, "runs", label),
            *flags,
        )

    def saved_plan_only(self, steps, label, saved_steps=None):
        saved = self.saved(self.items(saved_steps or steps, label + "-saved.json"))
        return self.plan_only(steps, label, "--plan-file", saved)

    def plan(self, label):
        with open(os.path.join(self.tmp, "runs", label, "plan.json"), encoding="utf-8") as handle:
            return json.load(handle)

    def state(self):
        with open(os.path.join(self.run_dir, "state.json"), encoding="utf-8") as handle:
            return json.load(handle)

    def dispatched(self):
        path = os.path.join(self.stubs, "dispatch.log")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return handle.read().splitlines()

    def records(self):
        folder = os.path.join(self.stubs, "records")
        if not os.path.isdir(folder):
            return {}
        found = {}
        for name in sorted(os.listdir(folder)):
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                found = {**found, name[: -len(".json")]: json.load(handle)}
        return found

    def pull_requests(self):
        path = os.path.join(self.stubs, "pr.log")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return handle.read().splitlines()


def msp_summary(test, summary, label, completed):
    found = [msp for msp in summary.get("msps") or [] if msp.get("msp") == label]
    test.assertEqual(len(found), 1, "the JSON result lists no MSP %s" % label + tail(completed))
    return found[0]


def lock_of(test, summary, label, completed):
    lock = msp_summary(test, summary, label, completed).get("lock")
    test.assertIsInstance(lock, dict, "MSP %s carries no lock record" % label + tail(completed))
    return lock


def probe_paths(lock):
    return [entry if isinstance(entry, str) else (entry or {}).get("path") for entry in lock.get("probes") or []]


class PerMspLockRun(unittest.TestCase):
    def _scratch(self, seeded=None):
        scratch = Scratch(seeded)
        self.addCleanup(scratch.close)
        return scratch

    def _run(self, steps=None, jobs=None, seeded=None):
        scratch = self._scratch(seeded)
        before = scratch.feature()
        scratch.jobs(jobs or standard_jobs())
        items = scratch.items(steps or labelled())
        completed = scratch.build(items, "--plan-file", scratch.saved(items), *LOCK_TIMEOUT, "--no-push")
        return scratch, before, completed, summary_of(completed)

    def _committed(self, summary, label, completed):
        found = msp_summary(self, summary, label, completed)
        self.assertEqual(found.get("state"), "committed", "MSP %s state" % label + tail(completed))
        return found

    def _gate_passed(self, summary, label, completed):
        found = self._committed(summary, label, completed)
        self.assertEqual(
            (found.get("gate") or {}).get("outcome"), "pass", "MSP %s gate" % label + tail(completed)
        )
        return found

    def _lock_commit_only(self, scratch, before, branch, path, completed):
        first = scratch.first_after(before, branch)
        self.assertIsNotNone(first, "%s holds no commit after the feature base" % branch + tail(completed))
        self.assertEqual(scratch.parents(first), [before], "%s's first commit parents" % branch)
        self.assertEqual(scratch.changes(before, first), [("A", path)], "%s's first commit" % branch)
        return first

    def _failed_lock(self, jobs, fragment):
        scratch, before, completed, summary = self._run(None, standard_jobs(**jobs))
        lock = lock_of(self, summary, "alpha", completed)
        self.assertEqual(lock.get("state"), "failed", "alpha's lock state" + tail(completed))
        reason = lock.get("reason") or ""
        self.assertTrue(names_path(reason, TEST_A), "the reason names no %s: %s" % (TEST_A, reason))
        self.assertIn(fragment, reason)
        return scratch, before, completed, summary, lock

    def test_each_msp_tree_holds_only_its_own_locked_tests(self):
        scratch, before, completed, summary = self._run()
        self.assertIsNotNone(scratch.ref("refs/heads/mitosis/alpha"), "no alpha branch" + tail(completed))
        self.assertIsNotNone(scratch.ref("refs/heads/mitosis/beta"), "no beta branch" + tail(completed))
        alpha = scratch.tree("mitosis/alpha")
        beta = scratch.tree("mitosis/beta")
        self.assertIn(TEST_A, alpha)
        self.assertNotIn(TEST_B, alpha)
        self.assertIn(TEST_B, beta)
        self.assertNotIn(TEST_A, beta)
        self._lock_commit_only(scratch, before, "mitosis/alpha", TEST_A, completed)
        self._lock_commit_only(scratch, before, "mitosis/beta", TEST_B, completed)

    def test_the_feature_branch_is_not_moved_by_the_lock(self):
        scratch, before, completed, summary = self._run()
        self._committed(summary, "alpha", completed)
        self._committed(summary, "beta", completed)
        self.assertIn(TEST_A, scratch.tree("mitosis/alpha"))
        self.assertIn(TEST_B, scratch.tree("mitosis/beta"))
        self.assertEqual(scratch.feature(), before)
        self.assertNotIn(TEST_A, scratch.tree("feature"))
        self.assertNotIn(TEST_B, scratch.tree("feature"))
        self.assertEqual(scratch.remote_refs(), [])

    def test_a_consumer_holds_its_producers_tests_through_the_merge(self):
        scratch, before, completed, summary = self._run(labelled(b_after_a=True))
        self.assertIsNotNone(scratch.ref("refs/heads/mitosis/beta"), "no beta branch" + tail(completed))
        beta = scratch.tree("mitosis/beta")
        self.assertIn(TEST_A, beta)
        self.assertIn(TEST_B, beta)
        commits = scratch.first_parent_commits(before, "mitosis/beta")
        introducing = [
            commit
            for commit in commits
            if TEST_A in scratch.changed_paths(scratch.parents(commit)[0], commit)
        ]
        self.assertEqual(len(introducing), 1, "commits bringing %s into beta" % TEST_A + tail(completed))
        parents = scratch.parents(introducing[0])
        self.assertGreater(len(parents), 1, "%s reached beta outside a merge" % TEST_A)
        self.assertTrue(
            any(scratch.is_ancestor(parent, "mitosis/alpha") for parent in parents[1:]),
            "the merge that brought %s merged no alpha commit" % TEST_A,
        )
        self.assertNotIn(TEST_A, scratch.tree(commits[0]))

    def test_a_consumer_ends_committed_with_its_gate_passed(self):
        scratch, before, completed, summary = self._run(labelled(b_after_a=True))
        self._gate_passed(summary, "alpha", completed)
        beta = self._gate_passed(summary, "beta", completed)
        self.assertNotIn(TEST_A, (beta.get("reconcile") or {}).get("referee") or [])
        referee_findings = [
            finding
            for finding in summary.get("findings") or []
            if finding.get("kind") == "referee" and names_path(finding.get("detail"), TEST_A)
        ]
        self.assertEqual(referee_findings, [], "a referee write named %s" % TEST_A + tail(completed))

    def test_nothing_is_pushed_at_lock_time(self):
        scratch = self._scratch()
        before = scratch.feature()
        scratch.checked("push", "-q", "origin", "refs/heads/feature:refs/heads/feature")
        scratch.jobs(standard_jobs(**{"check-author-beta": {"exit": 1}}))
        items = scratch.items(labelled())
        completed = scratch.pushing(items, "--plan-file", scratch.saved(items), *LOCK_TIMEOUT)
        self.assertEqual(scratch.remote_ref("refs/heads/feature"), before, "remote feature" + tail(completed))
        fallen_back = scratch.first_after(before, "refs/heads/mitosis/beta", git_dir=scratch.remote)
        self.assertNotIn(("A", TEST_B), scratch.changes(before, fallen_back, git_dir=scratch.remote))
        self.assertIsNotNone(
            scratch.remote_ref("refs/heads/mitosis/alpha"), "alpha was never pushed" + tail(completed)
        )
        first = scratch.first_after(before, "refs/heads/mitosis/alpha", git_dir=scratch.remote)
        self.assertEqual(scratch.changes(before, first, git_dir=scratch.remote), [("A", TEST_A)])
        self.assertIn("mitosis/alpha", scratch.pull_requests())
        self.assertIn("mitosis/beta", scratch.pull_requests())

    def test_locked_files_already_at_the_base_need_no_check_author(self):
        scratch, before, completed, summary = self._run(None, None, {TEST_A: checking(TEST_A)})
        dispatched = scratch.dispatched()
        self.assertNotIn("check-author-alpha", dispatched)
        self.assertIn("check-author-beta", dispatched)
        alpha = lock_of(self, summary, "alpha", completed)
        self.assertEqual(alpha.get("state"), "ok")
        self.assertEqual(alpha.get("commit"), before)
        beta = lock_of(self, summary, "beta", completed)
        self.assertEqual(beta.get("state"), "ok")
        first = self._lock_commit_only(scratch, before, "mitosis/beta", TEST_B, completed)
        self.assertEqual(beta.get("commit"), first)

    def test_a_failed_lock_falls_back_for_its_msp_alone(self):
        scratch, before, completed, summary = self._run(
            None, standard_jobs(**{"check-author-alpha": {"exit": 1}})
        )
        self._committed(summary, "alpha", completed)
        self.assertEqual(lock_of(self, summary, "alpha", completed).get("state"), "failed")
        self.assertIn("build-a", scratch.dispatched())
        self._gate_passed(summary, "beta", completed)

    def test_a_failed_lock_never_blocks_the_msps_that_merge_it(self):
        scratch, before, completed, summary = self._run(
            labelled(b_after_a=True), standard_jobs(**{"check-author-alpha": {"exit": 1}})
        )
        self._committed(summary, "alpha", completed)
        self._committed(summary, "beta", completed)
        self.assertIn("build-a", scratch.dispatched())
        self.assertIn("build-b", scratch.dispatched())

    def test_a_parked_lock_parks_only_its_msp_and_its_consumers(self):
        scratch, before, completed, summary = self._run(
            labelled(gamma=True), standard_jobs(**{"check-author-alpha": {"limit": HIT_LIMIT}})
        )
        self.assertEqual(msp_summary(self, summary, "alpha", completed).get("state"), "parked")
        self.assertEqual(msp_summary(self, summary, "gamma", completed).get("state"), "parked")
        self._committed(summary, "beta", completed)
        self.assertNotIn("build-a", scratch.dispatched())
        self.assertNotIn("build-c", scratch.dispatched())

    def test_a_resume_never_reruns_a_lock_that_fell_back(self):
        scratch, before, completed, summary = self._run(
            None, standard_jobs(**{"check-author-alpha": {"exit": 1}})
        )
        self.assertEqual(lock_of(self, summary, "alpha", completed).get("state"), "failed")
        first_run = len(scratch.dispatched())
        items = os.path.join(scratch.tmp, "items.json")
        resumed = scratch.build(
            items, "--plan-file", scratch.saved(items), *LOCK_TIMEOUT, "--no-push", "--resume"
        )
        self.assertEqual(scratch.dispatched()[first_run:], [], tail(resumed))
        result = summary_of(resumed)
        self._committed(result, "alpha", resumed)
        self._committed(result, "beta", resumed)
        self.assertEqual(lock_of(self, result, "alpha", resumed).get("state"), "failed")

    def test_the_check_author_brief_names_the_files_its_probe_may_write(self):
        scratch = self._scratch()
        completed = scratch.saved_plan_only(labelled(b_after_a=True), "brief")
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        entries = {entry.get("name"): entry for entry in scratch.plan("brief").get("check_authors") or []}
        self.assertIn("check-author-beta", entries, tail(completed))
        self.assertIn("check-author-alpha", entries, tail(completed))
        beta = ((entries["check-author-beta"].get("brief") or {}).get("text")) or ""
        beta_lines = [line for line in beta.splitlines() if "probe" in line.lower()]
        self.assertTrue(
            any(names_path(line, SRC_A) and names_path(line, SRC_B) for line in beta_lines),
            "no probe line of check-author-beta's brief names %s and %s:\n%s" % (SRC_A, SRC_B, beta),
        )
        self.assertIn("leave the probe in place", " ".join(beta.lower().split()))
        alpha = ((entries["check-author-alpha"].get("brief") or {}).get("text")) or ""
        alpha_lines = [line for line in alpha.splitlines() if "probe" in line.lower()]
        self.assertTrue(any(names_path(line, SRC_A) for line in alpha_lines), alpha)
        self.assertFalse(any(names_path(line, SRC_B) for line in alpha_lines), alpha)

    def test_a_test_passing_with_its_probe_locks_and_the_probe_never_lands(self):
        scratch, before, completed, summary = self._run()
        lock = lock_of(self, summary, "alpha", completed)
        self.assertEqual(lock.get("state"), "ok", "alpha's lock" + tail(completed))
        self.assertIn(SRC_A, probe_paths(lock))
        self.assertEqual(scratch.changes(before, lock.get("commit")), [("A", TEST_A)])
        self.assertEqual(scratch.first_after(before, "mitosis/alpha"), lock.get("commit"))

    def test_a_test_already_passing_is_refused(self):
        self._failed_lock(
            {"check-author-alpha": {"writes": {TEST_A: passing(TEST_A), SRC_A: WORK_SOURCE}}},
            "passes on the feature base without the build",
        )

    def test_a_test_failing_with_its_probe_is_refused(self):
        self._failed_lock(
            {"check-author-alpha": {"writes": {TEST_A: checking(TEST_A)}}},
            "fails with the check-author's probe",
        )

    def test_a_test_that_cannot_run_is_refused(self):
        self._failed_lock(
            {"check-author-alpha": {"writes": {TEST_A: crashing(TEST_A), SRC_A: WORK_SOURCE}}},
            "exit 2",
        )

    def test_a_probe_outside_the_allowed_files_is_not_overlaid(self):
        scratch, before, completed, summary, lock = self._failed_lock(
            {"check-author-alpha": {"writes": {TEST_A: checking(TEST_A, "b"), SRC_B: WORK_SOURCE}}},
            "fails with the check-author's probe",
        )
        self.assertIn(SRC_B, lock.get("undeclared") or [], "alpha's undeclared writes" + tail(completed))
        self.assertNotIn(SRC_B, probe_paths(lock))


if __name__ == "__main__":
    unittest.main()
