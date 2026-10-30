import json
import os
import re
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import test_referee_lock as lock_fixtures

CALC = "def add(a, b):\n    return a + b\n"
SUBTRACTING_CALC = "def add(a, b):\n    return a - b\n"
LOCAL_ONLY = "raise SystemExit(1)\n"
HIDDEN_CONFTEST = 'collect_ignore = ["test_calc.py"]\n'
SUBMODULE_CONFTEST = 'collect_ignore = ["lib.py"]\n'
TAMPERED_LOCKED = "import unittest\nraise SystemExit(0)\n"
GATE_RUN_LOCKED = "raise SystemExit(0)\n"
CALC_CHECK = "import calc; assert calc.add(2, 3) == 5"
ACCEPTANCE = [{"file": "tests/test_calc.py", "test": "Calc.adds"}]
SETUP_LIMIT = 25
FILE_PROTOCOL = ("-c", "protocol.file.allow=always")

CALC_TEST = """import unittest

import calc


class Calc(unittest.TestCase):
    def adds(self):
        self.assertEqual(calc.add(2, 3), 5)


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    suite.addTest(Calc("adds"))
    return suite
"""

CALLABLE_TEST = """import unittest

import calc


class Calc(unittest.TestCase):
    def adds(self):
        self.assertTrue(callable(calc.add))


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    suite.addTest(Calc("adds"))
    return suite
"""

REFUSING_COMMIT_MSG_HOOK = """#!/bin/sh
case "$(head -n 1 "$1")" in
  "chore("*) echo "refused: chore commits" >&2; exit 1 ;;
esac
exit 0
"""

WORKER = r'''
import json
import os
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "clean-worker.json"), encoding="utf-8") as handle:
    job = json.load(handle)[sys.argv[1]]


def git(*args, env=None):
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, env=env, timeout=60
    ).stdout.strip()


def write(path, content):
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)


def git_path(name):
    found = git("rev-parse", "--git-path", name)
    return found if os.path.isabs(found) else os.path.join(os.getcwd(), found)


def append_line(path, line):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as handle:
        handle.write(line + "\n")


def replace_tree(base, path):
    env = dict(os.environ, GIT_INDEX_FILE=os.path.join(here, "replace.index"))
    git("read-tree", base, env=env)
    blob = git("hash-object", "-w", "--", path)
    git("update-index", "--add", "--cacheinfo", "100644,%s,%s" % (blob, path), env=env)
    replacement = git("write-tree", env=env)
    git("replace", git("rev-parse", base + "^{tree}"), replacement)


for action in job["actions"]:
    kind = action[0]
    if kind == "write":
        write(action[1], action[2])
    elif kind == "append-git-path":
        append_line(git_path(action[1]), action[2])
    elif kind == "git":
        git(*action[1:])
    elif kind == "replace-tree":
        replace_tree(action[1], action[2])
    else:
        sys.exit("unknown action %r" % (kind,))
print(json.dumps({"item": job["step"], "status": "ok", "files_changed": job["declared"], "notes": "done"}))
'''

LOGGING_ACCEPT = r'''
import json
import os
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
scratch = os.path.dirname(here)
folder = sys.argv[1]
with open(os.path.join(here, "accept.json"), encoding="utf-8") as handle:
    config = json.load(handle)


def listing(top_folder):
    found = []
    for top, folders, files in os.walk(top_folder):
        relative = os.path.relpath(top, top_folder)
        for name in folders + files:
            path = name if relative == "." else os.path.join(relative, name)
            found.append(path.replace(os.sep, "/"))
    return sorted(found)


def seen(path):
    try:
        with open(os.path.join(folder, path), "rb") as handle:
            return handle.read().decode("utf-8", "surrogateescape")
    except OSError:
        return None


entry = {
    "worktree": folder,
    "listing": listing(folder),
    "files": {path: seen(path) for path in config["read"]},
}
with open(os.path.join(scratch, "accept.log"), "a", encoding="utf-8") as handle:
    handle.write(json.dumps(entry) + "\n")
for path, content in sorted(config["writes"].items()):
    target = os.path.join(folder, path)
    if os.path.dirname(target):
        os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)
try:
    code = subprocess.run(
        [sys.executable, "-B", "-c", config["check"]], cwd=folder, capture_output=True, timeout=60
    ).returncode
except subprocess.TimeoutExpired:
    sys.exit(2)
sys.exit(0 if code == 0 else 1)
'''

UNITTEST_ACCEPT = r'''
import os
import subprocess
import sys

folder, file, test = sys.argv[1], sys.argv[2], sys.argv[3]
tests_folder, name = os.path.split(file)
module = os.path.splitext(name)[0]
env = dict(
    os.environ,
    PYTHONPATH=os.pathsep.join([os.path.join(folder, tests_folder), folder]),
    PYTHONDONTWRITEBYTECODE="1",
)
try:
    code = subprocess.run(
        [sys.executable, "-m", "unittest", module + "." + test], cwd=folder, env=env, timeout=60
    ).returncode
except subprocess.TimeoutExpired:
    sys.exit(2)
sys.exit(code)
'''

SETUP_OK = r'''
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
folder = sys.argv[1]
with open(os.path.join(folder, "setup-ran"), "w", encoding="utf-8") as handle:
    handle.write("setup-ran\n")
with open(os.path.join(os.path.dirname(here), "setup.log"), "a", encoding="utf-8") as handle:
    handle.write(folder + "\n")
'''

SETUP_FAILS = "import sys\nsys.exit(3)\n"

SETUP_SLEEPS = "import time\ntime.sleep(30)\n"


def read_text(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def inside(path, folder):
    real = os.path.realpath(path)
    top = os.path.realpath(folder)
    return real == top or real.startswith(top + os.sep)


def paragraphs(text):
    return re.split(r"\n[ \t]*\n", text)


class CleanGate(unittest.TestCase):
    def setUp(self):
        self.scratch = None

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()

    def _scratch(self, ignored=(), seeded=None):
        files = {
            ".gitignore": "".join(line + "\n" for line in ("__pycache__/",) + tuple(ignored)),
            "tests/test_calc.py": CALC_TEST,
            **(seeded or {}),
        }
        self.scratch = lock_fixtures.Scratch(files)
        lock_fixtures.put(os.path.join(self.scratch.stubs, "clean_worker.py"), WORKER)
        lock_fixtures.put(os.path.join(self.scratch.stubs, "accept_log.py"), LOGGING_ACCEPT)
        lock_fixtures.put(os.path.join(self.scratch.stubs, "accept_unittest.py"), UNITTEST_ACCEPT)
        lock_fixtures.put(os.path.join(self.scratch.stubs, "setup_ok.py"), SETUP_OK)
        lock_fixtures.put(os.path.join(self.scratch.stubs, "setup_fails.py"), SETUP_FAILS)
        lock_fixtures.put(os.path.join(self.scratch.stubs, "setup_sleeps.py"), SETUP_SLEEPS)
        self._accepting()
        return self.scratch

    def _accepting(self, read=(), writes=None):
        lock_fixtures.put(
            os.path.join(self.scratch.stubs, "accept.json"),
            json.dumps({"read": list(read), "writes": writes or {}, "check": CALC_CHECK}),
        )

    def _worker(self, actions, declared=("calc.py",)):
        lock_fixtures.put(
            os.path.join(self.scratch.stubs, "clean-worker.json"),
            json.dumps({"demo": {"step": "build", "actions": actions, "declared": list(declared)}}),
        )

    def _build(self, files=("calc.py",), acceptance=ACCEPTANCE, accept=None, flags=(), timeout="120"):
        scratch = self.scratch
        items = scratch.items([lock_fixtures.step("build", files, "demo", acceptance=acceptance)])
        return scratch.mitosis(
            "--items",
            items,
            "--run-dir",
            scratch.run_dir,
            "--feature-branch",
            "feature",
            "--dispatch-command",
            scratch.stub("clean_worker.py", "{msp}"),
            "--acceptance-command",
            accept or scratch.stub("accept_log.py", "{worktree}"),
            "--timeout",
            timeout,
            "--no-push",
            *flags,
        )

    def _charter(self, text):
        return ("--charter", self.scratch.file("charter.md", text))

    def _setup(self, script):
        return ("--gate-setup", self.scratch.stub(script, "{worktree}"))

    def _msp(self, completed):
        summary = lock_fixtures.summary_of(completed)
        self.assertTrue(summary.get("msps"), "the JSON result lists no MSP" + lock_fixtures.tail(completed))
        return summary["msps"][0]

    def _recorded(self):
        with open(os.path.join(self.scratch.run_dir, "state.json"), encoding="utf-8") as handle:
            return json.load(handle)["msps"]["0"]

    def _ignored(self, msp):
        self.assertIn("ignored", msp, "the MSP entry of the JSON result carries no ignored list")
        return msp["ignored"]

    def _tree(self, *parts):
        return os.path.join(self.scratch.run_dir, "trees", "demo", *parts)

    def _accept_log(self):
        path = os.path.join(self.scratch.tmp, "accept.log")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def _gate_folders(self, entries):
        self.assertTrue(entries, "accept.log records no acceptance run")
        for entry in entries:
            self.assertFalse(
                inside(entry["worktree"], self._tree()),
                "the acceptance run's folder %s is inside the MSP worktree %s"
                % (entry["worktree"], self._tree()),
            )
            self.assertFalse(
                os.path.lexists(entry["worktree"]),
                "the acceptance run's folder %s still exists after the run" % entry["worktree"],
            )

    def _no_remote_branch(self):
        listed = self.scratch.git("ls-remote", "--heads", self.scratch.remote, "refs/heads/mitosis/demo")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(listed.stdout, "")

    def the_gate_runs_in_a_clean_checkout_of_the_commit(self):
        self._scratch(ignored=("local_only.py",))
        self._worker([["write", "calc.py", CALC], ["write", "local_only.py", LOCAL_ONLY]])
        completed = self._build()
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + lock_fixtures.tail(completed))
        entries = self._accept_log()
        self._gate_folders(entries)
        self.assertIn("calc.py", entries[0]["listing"])
        for entry in entries:
            self.assertNotIn("local_only.py", entry["listing"])
        self.assertIn("local_only.py", self._ignored(self._msp(completed)))
        self.assertFalse(os.path.lexists(self._tree("local_only.py")))

    def a_referee_file_hidden_by_the_clone_exclude_is_reverted_before_the_gate(self):
        self._scratch()
        self._worker(
            [
                ["write", "calc.py", CALC],
                ["write", "tests/conftest.py", HIDDEN_CONFTEST],
                ["append-git-path", "info/exclude", "tests/conftest.py"],
            ]
        )
        completed = self._build(flags=self._charter(lock_fixtures.PLAIN_CHARTER))
        self.assertEqual(completed.returncode, 9, "mitosis exit code" + lock_fixtures.tail(completed))
        self.assertEqual(self._accept_log(), [])
        msp = self._msp(completed)
        self.assertEqual(msp["state"], "referee-reverted")
        self.assertEqual(msp["reconcile"]["referee"], ["tests/conftest.py"])
        self.assertIs(msp["reconcile"]["fatal"], True)
        self.assertNotEqual(
            self.scratch.git("cat-file", "-e", "mitosis/demo:tests/conftest.py").returncode, 0
        )

    def worker_written_git_settings_never_shape_the_gate_bytes(self):
        self._scratch()
        self._accepting(read=("calc.py",))
        self._worker(
            [
                ["write", "calc.py", CALC],
                ["append-git-path", "info/attributes", "calc.py filter=shape"],
                ["git", "config", "filter.shape.smudge", "sed s/+/-/"],
                ["git", "config", "filter.shape.clean", "cat"],
            ]
        )
        completed = self._build()
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + lock_fixtures.tail(completed))
        entries = self._accept_log()
        self._gate_folders(entries)
        self.assertIn("return a + b", entries[0]["files"]["calc.py"] or "")

    def a_locked_edit_hidden_by_a_replace_ref_is_reverted_and_fatal(self):
        self._scratch(seeded={lock_fixtures.LOCKED: lock_fixtures.LOCKED_TEXT})
        self._worker(
            [
                ["write", "calc.py", CALC],
                ["write", lock_fixtures.LOCKED, TAMPERED_LOCKED],
                ["replace-tree", "HEAD", lock_fixtures.LOCKED],
            ]
        )
        completed = self._build(acceptance=(), flags=self._charter(lock_fixtures.LOCKED_CHARTER))
        self.assertEqual(completed.returncode, 9, "mitosis exit code" + lock_fixtures.tail(completed))
        msp = self._msp(completed)
        self.assertEqual(msp["state"], "referee-reverted")
        self.assertEqual(msp["reconcile"]["referee"], [lock_fixtures.LOCKED])
        self.assertIs(msp["reconcile"]["fatal"], True)
        self.assertEqual(
            self.scratch.checked("--no-replace-objects", "show", "mitosis/demo:" + lock_fixtures.LOCKED),
            lock_fixtures.LOCKED_TEXT,
        )

    def dependency_folders_come_from_the_repository_root(self):
        self._scratch(ignored=("node_modules/", ".venv/"))
        repo = self.scratch.repo
        lock_fixtures.put(os.path.join(repo, "node_modules", "dep", "index.js"), "root\n")
        lock_fixtures.put(os.path.join(repo, ".venv", "pyvenv.cfg"), "home = /usr\n")
        lock_fixtures.put(os.path.join(repo, ".venv", "marker.txt"), "root-venv\n")
        self._accepting(read=("node_modules/dep/index.js", ".venv/marker.txt"))
        self._worker(
            [["write", "calc.py", CALC], ["write", "node_modules/dep/index.js", "worker\n"]]
        )
        completed = self._build()
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + lock_fixtures.tail(completed))
        entries = self._accept_log()
        self._gate_folders(entries)
        for entry in entries:
            self.assertEqual(entry["files"]["node_modules/dep/index.js"], "root\n")
            self.assertEqual(entry["files"][".venv/marker.txt"], "root-venv\n")
        self.assertEqual(read_text(os.path.join(repo, "node_modules", "dep", "index.js")), "root\n")
        self.assertEqual(read_text(os.path.join(repo, ".venv", "marker.txt")), "root-venv\n")
        self.assertIn("node_modules/", self._ignored(self._msp(completed)))
        self.assertFalse(os.path.lexists(self._tree("node_modules")))

    def gate_setup_runs_in_the_clean_checkout_first(self):
        self._scratch(ignored=("node_modules/",))
        lock_fixtures.put(os.path.join(self.scratch.repo, "node_modules", "dep", "index.js"), "root\n")
        self._worker([["write", "calc.py", CALC]])
        completed = self._build(flags=self._setup("setup_ok.py"))
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + lock_fixtures.tail(completed))
        entries = self._accept_log()
        self._gate_folders(entries)
        setups = read_text(os.path.join(self.scratch.tmp, "setup.log")).splitlines()
        self.assertEqual(len(setups), 1, "setup.log lines: %r" % setups)
        self.assertFalse(inside(setups[0], self._tree()), "the gate setup ran inside the MSP worktree")
        for entry in entries:
            self.assertIn("setup-ran", entry["listing"])
            self.assertEqual(
                [path for path in entry["listing"] if path.split("/")[0] == "node_modules"], []
            )

    def a_failing_gate_setup_holds_the_msp_back(self):
        self._scratch()
        self._worker([["write", "calc.py", CALC]])
        completed = self._build(flags=self._setup("setup_fails.py"))
        self.assertEqual(completed.returncode, 21, "mitosis exit code" + lock_fixtures.tail(completed))
        self.assertEqual(self._msp(completed)["state"], "gate-failed")
        self.assertIn("gate-setup", self._recorded().get("gate_error") or "")
        self.assertFalse(os.path.exists(os.path.join(self.scratch.tmp, "accept.log")))
        self._no_remote_branch()

    def a_no_diff_attribute_never_hides_the_added_lines_from_the_mutants(self):
        self._scratch(seeded={"tests/test_calc.py": CALLABLE_TEST})
        self._worker(
            [
                ["write", "calc.py", SUBTRACTING_CALC],
                ["append-git-path", "info/attributes", "calc.py -diff"],
            ]
        )
        completed = self._build(
            accept=self.scratch.stub("accept_unittest.py", "{worktree}", "{file}", "{test}")
        )
        self.assertEqual(completed.returncode, 24, "mitosis exit code" + lock_fixtures.tail(completed))
        msp = self._msp(completed)
        self.assertEqual(msp["gate"]["properties"][0]["outcome"], "weak")
        self.assertEqual(msp["state"], "gate-failed")

    def a_locked_path_a_gate_run_writes_is_fatal(self):
        self._scratch(seeded={lock_fixtures.LOCKED: lock_fixtures.LOCKED_TEXT})
        self._accepting(writes={lock_fixtures.LOCKED: GATE_RUN_LOCKED})
        self._worker([["write", "calc.py", CALC]])
        completed = self._build(flags=self._charter(lock_fixtures.LOCKED_CHARTER))
        self.assertEqual(completed.returncode, 9, "mitosis exit code" + lock_fixtures.tail(completed))
        self.assertIn(lock_fixtures.LOCKED, (self._recorded().get("gate") or {}).get("referee") or [])
        self._no_remote_branch()
        self.assertEqual(
            self.scratch.checked("show", "mitosis/demo:" + lock_fixtures.LOCKED),
            lock_fixtures.LOCKED_TEXT,
        )

    def a_file_one_gate_run_leaves_never_reaches_the_next_run(self):
        self._scratch()
        self._accepting(writes={"leftover.txt": "left\n"})
        self._worker([["write", "calc.py", CALC]])
        completed = self._build()
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + lock_fixtures.tail(completed))
        entries = self._accept_log()
        self.assertGreaterEqual(len(entries), 2)
        for entry in entries:
            self.assertNotIn("leftover.txt", entry["listing"])

    def a_gate_setup_that_times_out_holds_the_msp_back(self):
        self._scratch()
        self._worker([["write", "calc.py", CALC]])
        started = time.monotonic()
        completed = self._build(flags=self._setup("setup_sleeps.py"), timeout="2")
        elapsed = time.monotonic() - started
        self.assertEqual(completed.returncode, 21, "mitosis exit code" + lock_fixtures.tail(completed))
        self.assertLess(elapsed, SETUP_LIMIT)
        self.assertEqual(self._msp(completed)["state"], "gate-failed")
        self.assertIn("gate-setup", self._recorded().get("gate_error") or "")
        self.assertFalse(os.path.exists(os.path.join(self.scratch.tmp, "accept.log")))

    def a_conftest_committed_inside_a_base_submodule_fails_the_lane(self):
        self._scratch()
        scratch = self.scratch
        dep = os.path.join(scratch.tmp, "dep")
        os.makedirs(dep)
        scratch.checked("init", "-q", "-b", "main", dep)
        lock_fixtures.put(os.path.join(dep, "lib.py"), "VALUE = 1\n")
        scratch.checked("-C", dep, "add", "-A")
        scratch.checked("-C", dep, "commit", "-q", "-m", "seed the dependency")
        scratch.checked("checkout", "-q", "feature")
        scratch.checked(*FILE_PROTOCOL, "submodule", "add", "-q", dep, "dep")
        scratch.checked("commit", "-q", "-m", "add the dep submodule")
        scratch.checked("checkout", "-q", "main")
        self._worker(
            [
                ["git", *FILE_PROTOCOL, "submodule", "update", "--init", "-q"],
                ["write", "calc.py", CALC],
                ["write", "dep/conftest.py", SUBMODULE_CONFTEST],
                ["git", "-C", "dep", "add", "conftest.py"],
                ["git", "-C", "dep", "commit", "-q", "-m", "worker"],
            ],
            declared=("calc.py", "dep"),
        )
        completed = self._build(files=("calc.py", "dep"), acceptance=())
        self.assertEqual(completed.returncode, 10, "mitosis exit code" + lock_fixtures.tail(completed))
        lane = lock_fixtures.summary_of(completed)["lanes"][0]
        self.assertEqual(lane["state"], "failed")
        self.assertIn("dep is a folder", lane["reason"])
        self.assertEqual(self._msp(completed)["state"], "blocked")
        self.assertNotEqual(scratch.git("cat-file", "-e", "mitosis/demo:dep/conftest.py").returncode, 0)
        self.assertEqual(
            scratch.checked("ls-tree", "mitosis/demo", "dep"), scratch.checked("ls-tree", "feature", "dep")
        )

    def a_committed_referee_write_never_lands_when_the_landing_commit_fails(self):
        self._scratch()
        hook = os.path.join(self.scratch.repo, ".git", "hooks", "commit-msg")
        lock_fixtures.put(hook, REFUSING_COMMIT_MSG_HOOK)
        os.chmod(hook, 0o755)
        self._worker(
            [
                ["write", "calc.py", CALC],
                ["write", "tests/conftest.py", HIDDEN_CONFTEST],
                ["git", "add", "calc.py", "tests/conftest.py"],
                ["git", "commit", "-q", "-m", "feat: add"],
                ["write", "notes.txt", "notes\n"],
            ]
        )
        completed = self._build(acceptance=(), flags=self._charter(lock_fixtures.PLAIN_CHARTER))
        self.assertEqual(completed.returncode, 10, "mitosis exit code" + lock_fixtures.tail(completed))
        lane = lock_fixtures.summary_of(completed)["lanes"][0]
        self.assertEqual(lane["state"], "failed")
        self.assertIn("refused: chore commits", lane["reason"])
        self.assertEqual(self._msp(completed)["state"], "blocked")
        self.assertNotEqual(
            self.scratch.git("cat-file", "-e", "mitosis/demo:tests/conftest.py").returncode, 0
        )
        self.assertFalse(os.path.lexists(self._tree("tests", "conftest.py")))
        self.assertFalse(os.path.lexists(self._tree("notes.txt")))


class CleanGateDocs(unittest.TestCase):
    def the_docs_describe_the_clean_gate_checkout(self):
        documents = {
            name: read_text(os.path.join(ROOT, *name.split("/")))
            for name in ("README.md", "skills/mitosis/SKILL.md", "CHANGELOG.md")
        }
        for name, text in sorted(documents.items()):
            with self.subTest(document=name):
                for phrase in ("--gate-setup", "clean checkout", "not a git repository", "ignored"):
                    self.assertTrue(phrase in text, "%s does not say %r" % (name, phrase))
                for phrase in ("normalize", "writes no gate judged", "worktree is clean"):
                    self.assertFalse(phrase in text, "%s still says %r" % (name, phrase))
        for name in ("README.md", "skills/mitosis/SKILL.md"):
            with self.subTest(document=name):
                self.assertTrue(
                    any(
                        "exit 21" in paragraph and "gate-setup" in paragraph
                        for paragraph in paragraphs(documents[name])
                    ),
                    "%s has no paragraph that names gate-setup beside exit 21" % name,
                )


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            CleanGate,
            (
                "the_gate_runs_in_a_clean_checkout_of_the_commit",
                "a_referee_file_hidden_by_the_clone_exclude_is_reverted_before_the_gate",
                "worker_written_git_settings_never_shape_the_gate_bytes",
                "a_locked_edit_hidden_by_a_replace_ref_is_reverted_and_fatal",
                "dependency_folders_come_from_the_repository_root",
                "gate_setup_runs_in_the_clean_checkout_first",
                "a_failing_gate_setup_holds_the_msp_back",
                "a_no_diff_attribute_never_hides_the_added_lines_from_the_mutants",
                "a_locked_path_a_gate_run_writes_is_fatal",
                "a_file_one_gate_run_leaves_never_reaches_the_next_run",
                "a_gate_setup_that_times_out_holds_the_msp_back",
                "a_conftest_committed_inside_a_base_submodule_fails_the_lane",
                "a_committed_referee_write_never_lands_when_the_landing_commit_fails",
            ),
        ),
        (CleanGateDocs, ("the_docs_describe_the_clean_gate_checkout",)),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
