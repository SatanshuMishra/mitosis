import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import mitosis
import run
import test_clean_gate as clean_fixtures
import test_referee_lock as lock_fixtures

BRANCH = "mitosis/demo"
FILE_PROTOCOL = ("-c", "protocol.file.allow=always")
LOCKED_CALC_TEST = (
    "import unittest\nfrom calc import add\n\n"
    "class Calc(unittest.TestCase):\n    def adds(self):\n        self.assertEqual(add(2, 3), 5)\n\n"
    "def load_tests(loader, standard, pattern):\n"
    "    return unittest.TestSuite([Calc('adds')])\n"
)
TAMPERED_CALC_TEST = (
    "import unittest\nfrom calc import add\n\n"
    "class Calc(unittest.TestCase):\n    def adds(self):\n        self.assertEqual(add(2, 3), 0)\n\n"
    "def load_tests(loader, standard, pattern):\n"
    "    return unittest.TestSuite([Calc('adds')])\n"
)
BROKEN_CALC = "def add(a, b):\n    return 0\n"
LOCKED_CALC_CHARTER = "# Charter\n\n- Python 3.9 or later.\n- Locked: tests/test_calc.py\n"
CALC_ACCEPTANCE = [{"file": "tests/test_calc.py", "test": "Calc.adds"}]
USAGE_LIMIT_LINE = "You've hit your usage limit; it resets at 5pm"
NOTES = "worker notes\n"
CHECKOUT_LISTING = [".git", "home.png.txt", "uncommitted-notes.txt"]

WORKER = r'''
import json
import os
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "pinned-worker.json"), encoding="utf-8") as handle:
    job = json.load(handle)[sys.argv[1]]


def write(path, content):
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)


for action in job["actions"]:
    kind = action[0]
    if kind == "write":
        write(action[1], action[2])
    elif kind == "git":
        subprocess.run(["git", *action[1:]], check=True, stdout=sys.stderr, timeout=60)
    elif kind == "sh":
        subprocess.run(action[1], shell=True, check=True, stdout=sys.stderr, timeout=60)
    elif kind == "limit":
        print(action[1])
        sys.exit(1)
    elif kind == "exit":
        sys.exit(action[1])
    else:
        sys.exit("unknown action %r" % (kind,))
print(json.dumps({"item": job["step"], "status": "ok", "files_changed": job["declared"], "notes": "done"}))
'''


def git_text(args, cwd):
    return subprocess.run(
        ["git", *FILE_PROTOCOL, *args], cwd=cwd, check=True, capture_output=True, timeout=60
    ).stdout.decode("utf-8")


def read_text(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


class PinnedRefereeRun(unittest.TestCase):
    def setUp(self):
        self.scratch = None

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()

    def _scratch(self, seeded):
        self.scratch = lock_fixtures.Scratch(seeded)
        lock_fixtures.put(os.path.join(self.scratch.stubs, "pinned_worker.py"), WORKER)
        self.feature_before = self.scratch.checked("rev-parse", "refs/heads/feature").strip()
        return self.scratch

    def _calc_scratch(self):
        return self._scratch({"tests/test_calc.py": LOCKED_CALC_TEST})

    def _worker(self, actions, declared=("calc.py",)):
        lock_fixtures.put(
            os.path.join(self.scratch.stubs, "pinned-worker.json"),
            json.dumps({"demo": {"step": "build", "actions": actions, "declared": list(declared)}}),
        )

    def _run(self, files=("calc.py",), acceptance=(), charter=lock_fixtures.PLAIN_CHARTER, extra=()):
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
            scratch.stub("pinned_worker.py", "{msp}"),
            "--acceptance-command",
            scratch.stub("accept.py", "unittest", "{file}", "{test}"),
            "--timeout",
            "120",
            "--no-push",
            "--charter",
            scratch.file("charter.md", charter),
            *extra,
        )

    def _calc_run(self, extra=()):
        return self._run(acceptance=CALC_ACCEPTANCE, charter=LOCKED_CALC_CHARTER, extra=extra)

    def _summary(self, completed):
        summary = lock_fixtures.summary_of(completed)
        self.assertTrue(summary.get("msps"), "the JSON result lists no MSP" + lock_fixtures.tail(completed))
        return summary

    def _assert_locked_test_reverted(self, completed):
        self.assertEqual(completed.returncode, 9, "mitosis exit code" + lock_fixtures.tail(completed))
        summary = self._summary(completed)
        msp = summary["msps"][0]
        self.assertEqual(msp["state"], "referee-reverted")
        self.assertEqual(msp["reconcile"]["referee"], ["tests/test_calc.py"])
        self.assertIs(msp["reconcile"]["fatal"], True)
        self.assertEqual(
            self.scratch.checked("--no-replace-objects", "show", "refs/heads/%s:tests/test_calc.py" % BRANCH),
            LOCKED_CALC_TEST,
        )
        self.assertEqual(summary["referee"]["commit"], self.feature_before)
        return msp

    def _tamper_then(self, *moves):
        return [
            ["write", "tests/test_calc.py", TAMPERED_CALC_TEST],
            ["git", "add", "-A"],
            ["git", "commit", "-q", "-m", "tamper"],
            *moves,
            ["write", "calc.py", BROKEN_CALC],
        ]

    def a_feature_branch_moved_by_the_worker_still_reverts_the_locked_write(self):
        self._calc_scratch()
        self._worker(self._tamper_then(["git", "update-ref", "refs/heads/feature", "HEAD"]))
        self._assert_locked_test_reverted(self._calc_run())

    def a_tag_shadowing_the_feature_branch_still_reverts_the_locked_write(self):
        self._calc_scratch()
        self._worker(self._tamper_then(["git", "tag", "feature", "HEAD"]))
        self._assert_locked_test_reverted(self._calc_run())

    def a_detached_head_is_judged_by_the_commit_the_gate_certifies(self):
        self._calc_scratch()
        self._worker([["git", "checkout", "-q", "--detach"], *self._tamper_then()])
        msp = self._assert_locked_test_reverted(self._calc_run())
        self.assertIsNone(msp["gate"]["outcome"])

    def a_resume_keeps_the_base_pinned_at_the_first_start(self):
        self._calc_scratch()
        self._worker(
            [
                ["write", "tests/test_calc.py", TAMPERED_CALC_TEST],
                ["git", "add", "-A"],
                ["git", "commit", "-q", "-m", "tamper"],
                ["git", "update-ref", "refs/heads/feature", "HEAD"],
                ["git", "reset", "-q", "--hard", "HEAD~1"],
                ["limit", USAGE_LIMIT_LINE],
            ]
        )
        first = self._calc_run()
        self.assertEqual(first.returncode, 13, "first run exit code" + lock_fixtures.tail(first))
        self.assertNotEqual(
            self.scratch.checked("rev-parse", "refs/heads/feature").strip(), self.feature_before
        )
        self._worker(
            [["write", "tests/test_calc.py", TAMPERED_CALC_TEST], ["write", "calc.py", BROKEN_CALC]]
        )
        self._assert_locked_test_reverted(self._calc_run(extra=("--resume",)))

    def a_moved_base_never_hides_a_conftest_committed_in_a_submodule(self):
        scratch = self._scratch({"tests/test_calc.py": clean_fixtures.CALC_TEST})
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
        self.feature_before = scratch.checked("rev-parse", "refs/heads/feature").strip()
        self._worker(
            [
                ["git", *FILE_PROTOCOL, "submodule", "update", "--init", "-q"],
                ["write", "calc.py", clean_fixtures.CALC],
                ["write", "dep/conftest.py", clean_fixtures.SUBMODULE_CONFTEST],
                ["git", "-C", "dep", "add", "conftest.py"],
                ["git", "-C", "dep", "commit", "-q", "-m", "worker"],
                ["git", "add", "calc.py", "dep"],
                ["git", "commit", "-q", "-m", "feat: bump"],
                ["git", "update-ref", "refs/heads/feature", "HEAD"],
            ],
            declared=("calc.py", "dep"),
        )
        completed = self._run(files=("calc.py", "dep"))
        self.assertEqual(completed.returncode, 9, "mitosis exit code" + lock_fixtures.tail(completed))
        msp = self._summary(completed)["msps"][0]
        self.assertEqual(msp["state"], "referee-reverted")
        self.assertEqual(msp["reconcile"]["referee"], ["dep/conftest.py"])
        self.assertEqual(
            scratch.checked("ls-tree", "refs/heads/" + BRANCH, "dep"),
            scratch.checked("ls-tree", self.feature_before, "dep"),
        )

    def _conftest_run(self, actions, hook=None):
        scratch = self._scratch({"tests/test_calc.py": clean_fixtures.CALC_TEST})
        if hook is not None:
            path = os.path.join(scratch.repo, ".git", "hooks", hook[0])
            lock_fixtures.put(path, hook[1])
            os.chmod(path, 0o755)
        self._worker(actions)
        completed = self._run()
        self.assertEqual(completed.returncode, 9, "mitosis exit code" + lock_fixtures.tail(completed))
        msp = self._summary(completed)["msps"][0]
        self.assertEqual(msp["state"], "referee-reverted")
        self.assertEqual(msp["reconcile"]["referee"], ["tests/conftest.py"])
        self.assertIs(msp["reconcile"]["fatal"], True)
        self.assertNotEqual(
            scratch.git("cat-file", "-e", "refs/heads/%s:tests/conftest.py" % BRANCH).returncode, 0
        )

    def a_refused_lane_landing_commit_still_reverts_the_committed_conftest(self):
        self._conftest_run(
            [
                ["write", "tests/conftest.py", clean_fixtures.HIDDEN_CONFTEST],
                ["git", "add", "tests/conftest.py"],
                ["git", "commit", "-q", "-m", "feat: conftest"],
                ["write", "calc.py", clean_fixtures.CALC],
            ],
            hook=("commit-msg", clean_fixtures.REFUSING_COMMIT_MSG_HOOK),
        )

    def a_worker_failing_after_committing_still_reverts_the_committed_conftest(self):
        self._conftest_run(
            [
                ["write", "calc.py", clean_fixtures.CALC],
                ["write", "tests/conftest.py", clean_fixtures.HIDDEN_CONFTEST],
                ["git", "add", "calc.py", "tests/conftest.py"],
                ["git", "commit", "-q", "-m", "feat: add"],
                ["exit", 1],
            ]
        )

    def a_replace_object_placed_by_the_worker_changes_no_referee_verdict(self):
        self._calc_scratch()
        self._worker(
            self._tamper_then(
                ["sh", "git replace refs/heads/feature \"$(git commit-tree 'HEAD^{tree}' -p refs/heads/feature^ -m shadow)\""]
            )
        )
        msp = self._assert_locked_test_reverted(self._calc_run())
        self.assertEqual(msp["reconcile"]["undeclared"], ["tests/test_calc.py"])

    def an_info_grafts_line_placed_by_the_worker_changes_no_referee_verdict(self):
        self._calc_scratch()
        self._worker(
            self._tamper_then(["sh", "git rev-parse HEAD >> \"$(git rev-parse --git-path info/grafts)\""])
        )
        msp = self._assert_locked_test_reverted(self._calc_run())
        self.assertEqual(msp["reconcile"]["undeclared"], ["tests/test_calc.py"])


class SubmoduleCheckoutKept(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mitosis-pinned-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        patcher = mock.patch.dict(os.environ, {**lock_fixtures.GIT_IDENTITY, "HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _images(self):
        images = os.path.join(self.tmp, "images")
        os.makedirs(images)
        git_text(["init", "-q", "-b", "main"], images)
        lock_fixtures.put(os.path.join(images, "home.png.txt"), "1\n")
        git_text(["add", "-A"], images)
        git_text(["commit", "-q", "-m", "images"], images)
        return images

    def _tree(self, sub, seed):
        images = self._images()
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        git_text(["init", "-q", "-b", "feature"], repo)
        lock_fixtures.put(os.path.join(repo, "src", "a.py"), "a = 1\n")
        if seed == "submodule":
            git_text(["submodule", "add", "-q", images, sub], repo)
        elif seed == "file":
            lock_fixtures.put(os.path.join(repo, sub), "base\n")
        git_text(["add", "-A"], repo)
        git_text(["commit", "-q", "-m", "seed"], repo)
        self.base = git_text(["rev-parse", "refs/heads/feature"], repo).strip()
        tree = os.path.join(self.tmp, "tree")
        git_text(["worktree", "add", "-q", "-b", BRANCH, tree, "feature"], repo)
        git_text(["submodule", "update", "--init", "-q"], tree)
        lock_fixtures.put(os.path.join(tree, "src", "a.py"), "a = 2\n")
        self.images = images
        return tree

    def _stage(self, tree, locked=()):
        plan = {
            "locked": list(locked),
            "msps": [{"files": ["src/a.py"], "steps": ["s"], "label": "demo"}],
            "items": [{"name": "s", "files": ["src/a.py"], "acceptance": []}],
        }
        settings = {"feature_branch": self.base, "base_commit": self.base}
        return run._referee_stage(plan, 0, {"path": tree, "branch": BRANCH}, (), [], settings)

    def _bump(self, tree, sub):
        checkout = os.path.join(tree, sub)
        lock_fixtures.put(os.path.join(checkout, "home.png.txt"), "2\n")
        git_text(["commit", "-qam", "worker bump"], checkout)
        lock_fixtures.put(os.path.join(checkout, "uncommitted-notes.txt"), NOTES)

    def _add(self, tree, sub):
        git_text(["submodule", "add", "-q", self.images, sub], tree)
        lock_fixtures.put(os.path.join(tree, sub, "uncommitted-notes.txt"), NOTES)

    def _assert_reverted(self, held, tree, sub, referee, listing):
        checkout = os.path.join(tree, sub)
        self.assertTrue(
            os.path.isdir(checkout) and not os.path.islink(checkout),
            "the referee restore removed the folder %s from the Worker's worktree" % sub,
        )
        self.assertEqual(sorted(os.listdir(checkout)), listing)
        self.assertEqual(read_text(os.path.join(checkout, "uncommitted-notes.txt")), NOTES)
        self.assertEqual((held or {}).get("state"), "referee-reverted")
        self.assertEqual(held["reconcile"]["referee"], referee)
        self.assertEqual(
            git_text(["ls-tree", "refs/heads/" + BRANCH, sub], tree),
            git_text(["ls-tree", self.base, sub], tree),
        )

    def _assert_bump_reverted(self, sub, locked, referee):
        tree = self._tree(sub, "submodule")
        self._bump(tree, sub)
        run.commit_all(tree, "pre-gate")
        held = self._stage(tree, locked)
        self._assert_reverted(held, tree, sub, referee, CHECKOUT_LISTING)
        self.assertEqual(read_text(os.path.join(tree, sub, "home.png.txt")), "1\n")
        self.assertEqual(
            git_text(["rev-parse", "HEAD"], os.path.join(tree, sub)).strip(),
            git_text(["rev-parse", "%s:%s" % (self.base, sub)], tree).strip(),
        )

    def a_bumped_submodule_at_a_marked_path_keeps_its_checkout(self):
        self._assert_bump_reverted(
            "tests/baseline", (), ["tests/baseline", "tests/baseline/home.png.txt"]
        )

    def a_bumped_submodule_at_a_locked_path_keeps_its_checkout(self):
        self._assert_bump_reverted(
            "vendor/lib", ("vendor/lib",), ["vendor/lib", "vendor/lib/home.png.txt"]
        )

    def an_added_submodule_at_a_marked_path_keeps_its_checkout(self):
        sub = "tests/baseline"
        tree = self._tree(sub, None)
        self._add(tree, sub)
        run.commit_all(tree, "pre-gate")
        held = self._stage(tree)
        self._assert_reverted(held, tree, sub, [sub], CHECKOUT_LISTING)
        self.assertEqual(git_text(["ls-tree", "refs/heads/" + BRANCH, sub], tree), "")

    def a_file_turned_into_a_submodule_keeps_its_checkout(self):
        sub = "tests/baseline"
        tree = self._tree(sub, "file")
        git_text(["rm", "-q", sub], tree)
        self._add(tree, sub)
        run.commit_all(tree, "pre-gate")
        held = self._stage(tree)
        self._assert_reverted(held, tree, sub, [sub], CHECKOUT_LISTING)
        self.assertEqual(
            git_text(["cat-file", "blob", "refs/heads/%s:%s" % (BRANCH, sub)], tree), "base\n"
        )

    def a_file_turned_into_a_folder_keeps_the_uncommitted_files_in_it(self):
        sub = "tests/baseline"
        tree = self._tree(sub, "file")
        git_text(["rm", "-q", sub], tree)
        lock_fixtures.put(os.path.join(tree, sub, "kept.txt"), "committed\n")
        git_text(["add", sub + "/kept.txt"], tree)
        run.commit_all(tree, "pre-gate")
        lock_fixtures.put(os.path.join(tree, sub, "uncommitted-notes.txt"), NOTES)
        held = self._stage(tree)
        self._assert_reverted(
            held, tree, sub, [sub, sub + "/kept.txt"], ["uncommitted-notes.txt"]
        )
        self.assertEqual(
            git_text(["cat-file", "blob", "refs/heads/%s:%s" % (BRANCH, sub)], tree), "base\n"
        )


class UnjudgedVerdict(unittest.TestCase):
    def an_msp_the_referee_never_judged_carries_no_referee_verdict(self):
        plan = {"msps": [{"label": "demo", "steps": ["build"], "files": ["calc.py"]}]}
        state = {"msps": {"0": {"state": "blocked", "reason": "Lane 0 is failed"}}}
        found = mitosis._msp_summary(plan, state)[0]["reconcile"]
        self.assertIsNone(found["referee"])
        self.assertIsNone(found["fatal"])


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            PinnedRefereeRun,
            (
                "a_feature_branch_moved_by_the_worker_still_reverts_the_locked_write",
                "a_tag_shadowing_the_feature_branch_still_reverts_the_locked_write",
                "a_detached_head_is_judged_by_the_commit_the_gate_certifies",
                "a_resume_keeps_the_base_pinned_at_the_first_start",
                "a_moved_base_never_hides_a_conftest_committed_in_a_submodule",
                "a_refused_lane_landing_commit_still_reverts_the_committed_conftest",
                "a_worker_failing_after_committing_still_reverts_the_committed_conftest",
                "a_replace_object_placed_by_the_worker_changes_no_referee_verdict",
                "an_info_grafts_line_placed_by_the_worker_changes_no_referee_verdict",
            ),
        ),
        (
            SubmoduleCheckoutKept,
            (
                "a_bumped_submodule_at_a_marked_path_keeps_its_checkout",
                "a_bumped_submodule_at_a_locked_path_keeps_its_checkout",
                "an_added_submodule_at_a_marked_path_keeps_its_checkout",
                "a_file_turned_into_a_submodule_keeps_its_checkout",
                "a_file_turned_into_a_folder_keeps_the_uncommitted_files_in_it",
            ),
        ),
        (UnjudgedVerdict, ("an_msp_the_referee_never_judged_carries_no_referee_verdict",)),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
