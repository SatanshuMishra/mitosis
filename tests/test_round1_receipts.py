import glob
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
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import isolation
import run
from isolation_scratch import (
    ADDS,
    GIT_IDENTITY,
    CORRECT_ADD,
    TEST_FILE,
    Scratch,
    put,
    step,
    summary_of,
    tail,
)
from test_check_author_lane import saved_plan

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

FOLDED_GIT = (".GIT", ".Git", ".gIT", ".g\u200cit", "\ufeff.GIT")


def escaping(targets):
    return (
        "for _path in %r:\n"
        "    try:\n"
        "        with open(_path, 'a') as _handle:\n"
        "            _handle.write('escaped\\n')\n"
        "    except OSError:\n"
        "        pass\n" % (list(targets),)
    )


def check_author(files=(TEST_FILE,)):
    labelled = {**step("check-author", files, None), "role": "check-author"}
    return {key: value for key, value in labelled.items() if key != "msp"}


def msp_lock(summary, label):
    found = [msp for msp in summary.get("msps") or [] if msp.get("msp") == label]
    return (found[0].get("lock") if len(found) == 1 else None) or {}


class Receipts(unittest.TestCase):
    def setUp(self):
        self.scratch = None

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()

    def _scratch(self, seeded=None):
        self.scratch = Scratch(seeded)
        return self.scratch

    def _first_property(self, completed, label="demo"):
        found = [
            msp for msp in summary_of(completed).get("msps") or [] if msp.get("msp") == label
        ]
        self.assertEqual(len(found), 1, "the JSON summary lists no MSP %s" % label + tail(completed))
        properties = (found[0].get("gate") or {}).get("properties") or []
        self.assertTrue(properties, "the MSP's gate carries no properties" + tail(completed))
        return properties[0]

    def _escape_targets(self):
        scratch = self.scratch
        return (
            os.path.join(scratch.repo, ".git", "refs", "heads", "escaped"),
            os.path.join(scratch.run_dir, "escaped.txt"),
            os.path.join(scratch.repo, "escaped.txt"),
        )

    def _assert_confined(self, completed):
        self.assertEqual(summary_of(completed).get("sandbox"), "sandbox-exec", tail(completed))
        for target in self._escape_targets():
            with self.subTest(target=target):
                self.assertFalse(
                    os.path.lexists(target),
                    "Worker-written code wrote %s outside the sandbox" % target + tail(completed),
                )
        self.assertEqual(self.scratch.git("rev-parse", "--verify", "-q", "refs/heads/escaped").returncode, 1)

    def _sandboxed(
        self,
        seeded,
        jobs,
        flags=lambda scratch: (),
        acceptance="unittest",
        steps=None,
        hooks=None,
        saved=False,
    ):
        scratch = self._scratch(seeded)
        for name, body in (hooks(self._escape_targets()) if hooks else {}).items():
            path = os.path.join(scratch.repo, ".git", "hooks", name)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("#!/bin/sh\n" + body)
            os.chmod(path, 0o755)
        items = scratch.items(steps or [step("add", ["calc.py"], "demo", acceptance=[ADDS])])
        scratch.jobs(jobs(self._escape_targets()))
        planned = ("--plan-file", saved_plan(items, scratch.repo)) if saved else ()
        return scratch.build(
            items,
            "--worker-sandbox",
            "--worker-write",
            scratch.stubs,
            *planned,
            *flags(scratch),
            acceptance=acceptance,
        )

    def _skip_without_sandbox(self):
        if sys.platform != "darwin":
            self.skipTest("sandbox-exec exists only on macOS")

    def sandboxed_worker_code_cannot_write_outside_at_the_acceptance_runs(self):
        self._skip_without_sandbox()
        completed = self._sandboxed(
            {TEST_FILE: EXACT_TEST},
            lambda targets: {"add": {"writes": {"calc.py": escaping(targets) + CORRECT_ADD}}},
        )
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        self.assertEqual(self._first_property(completed)["outcome"], "pass")
        self._assert_confined(completed)

    def sandboxed_repository_hook_cannot_write_outside(self):
        self._skip_without_sandbox()
        completed = self._sandboxed(
            {TEST_FILE: EXACT_TEST},
            lambda targets: {"add": {"writes": {"calc.py": CORRECT_ADD}}},
            hooks=lambda targets: {
                "pre-commit": "".join('touch "%s" 2>/dev/null\n' % target for target in targets)
                + "echo the hook ran >&2\nexit 1\n"
            },
        )
        self.assertEqual(completed.returncode, 21, "mitosis exit code" + tail(completed))
        self.assertIn("the hook ran", completed.stdout)
        self._assert_confined(completed)

    def sandboxed_repository_hook_cannot_write_the_repositorys_git_folder(self):
        self._skip_without_sandbox()
        completed = self._sandboxed(
            {TEST_FILE: EXACT_TEST},
            lambda targets: {"add": {"writes": {"calc.py": CORRECT_ADD}}},
            hooks=lambda targets: {
                "pre-commit": 'touch "$(git rev-parse --absolute-git-dir)/locked" 2>/dev/null\n'
                "echo the hook ran >&2\nexit 1\n"
            },
        )
        self.assertEqual(completed.returncode, 21, "mitosis exit code" + tail(completed))
        self.assertIn("the hook ran", completed.stdout)
        left = glob.glob(os.path.join(self.scratch.repo, ".git", "worktrees", "*", "locked"))
        self.assertEqual(left, [], "the hook locked its worktree in the real repository" + tail(completed))

    def sandboxed_repository_hook_can_still_stage_in_its_checkout(self):
        self._skip_without_sandbox()
        completed = self._sandboxed(
            {TEST_FILE: EXACT_TEST},
            lambda targets: {"add": {"writes": {"calc.py": CORRECT_ADD}}},
            hooks=lambda targets: {
                "pre-commit": "git add -A || exit 0\necho the hook staged its checkout >&2\nexit 1\n"
            },
        )
        self.assertEqual(completed.returncode, 21, "mitosis exit code" + tail(completed))
        self.assertIn("the hook staged its checkout", completed.stdout)

    def sandboxed_worker_code_cannot_write_outside_at_the_gate_setup(self):
        self._skip_without_sandbox()
        completed = self._sandboxed(
            {TEST_FILE: EXACT_TEST},
            lambda targets: {"add": {"writes": {"calc.py": escaping(targets) + CORRECT_ADD}}},
            lambda scratch: ("--gate-setup", "%s -c 'import calc'" % shlex.quote(sys.executable)),
        )
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        self.assertEqual(self._first_property(completed)["outcome"], "pass")
        self._assert_confined(completed)

    def sandboxed_check_author_code_cannot_write_outside_at_the_lock_stage(self):
        self._skip_without_sandbox()
        completed = self._sandboxed(
            None,
            lambda targets: {
                "check-author": {
                    "writes": {TEST_FILE: escaping(targets) + EXACT_TEST, "calc.py": CORRECT_ADD},
                    "msp": "lock",
                },
                "add": {"writes": {"calc.py": CORRECT_ADD}, "msp": "demo"},
            },
            steps=[
                check_author(),
                step("add", ["calc.py"], "demo", [ADDS], ("check-author",)),
            ],
            saved=True,
        )
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        self.assertNotIn("lock", summary_of(completed))
        self.assertEqual(msp_lock(summary_of(completed), "demo").get("state"), "ok")
        self._assert_confined(completed)

    def _folded_clone(self):
        folder = os.path.realpath(tempfile.mkdtemp(prefix="mitosis-folded-"))
        self.addCleanup(shutil.rmtree, folder, True)
        checkout = os.path.join(folder, "checkout")
        clone = os.path.join(folder, "clone")
        env = {**os.environ, **GIT_IDENTITY}
        for args in (
            ["init", "-q", "-b", "main", checkout],
            ["-C", checkout, "commit", "-q", "--allow-empty", "-m", "seed"],
            ["clone", "-q", "--no-local", checkout, clone],
        ):
            subprocess.run(["git", *args], env=env, check=True, capture_output=True, timeout=60)
        put(os.path.join(clone, "a.py"), "A = 1\n")
        return checkout, clone

    def _outside(self, checkout, clone):
        head = run.branch_commit(checkout, "main", required=True)
        return isolation.outside_writes(
            clone, checkout, run._committed_entries(checkout, head), ("a.py",), ()
        )

    def a_case_folded_nested_git_folder_is_never_replayed(self):
        for name in FOLDED_GIT:
            with self.subTest(folder=name):
                checkout, clone = self._folded_clone()
                put(os.path.join(clone, "sub", name, "HEAD"), "ref: refs/heads/main\n")
                outside = self._outside(checkout, clone)
                self.assertEqual(outside["writes"], {})
                self.assertEqual(outside["unreplayed"], ["sub/" + name])

    def a_case_folded_top_level_git_folder_is_never_replayed(self):
        for name in FOLDED_GIT:
            with self.subTest(folder=name):
                checkout, clone = self._folded_clone()
                os.rename(os.path.join(clone, ".git"), os.path.join(clone, name))
                outside = self._outside(checkout, clone)
                self.assertEqual(outside["writes"], {})
                self.assertEqual(outside["unreplayed"], [name])

    def a_worker_written_nested_git_folder_is_reported_undeclared(self):
        scratch = self._scratch()
        items = scratch.items([step("first", ["a.py"], "demo")])
        scratch.jobs(
            {
                "first": {
                    "writes": {"a.py": "A = 1\n", "sub/.GIT/HEAD": "ref: refs/heads/main\n"},
                    "declared": ["a.py"],
                }
            }
        )
        completed = scratch.build(items)
        findings = [
            finding
            for finding in summary_of(completed).get("findings") or []
            if finding.get("kind") == "undeclared"
            and re.search(r"(?<![\w/.-])sub/\.GIT(?![\w.-])", finding.get("detail") or "")
        ]
        self.assertEqual(len(findings), 1, "undeclared findings naming sub/.GIT" + tail(completed))
        self.assertEqual(scratch.checked("ls-tree", "mitosis/demo", "sub"), "")
        self.assertFalse(os.path.lexists(os.path.join(scratch.run_dir, "trees", "demo", "sub")))
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            Receipts,
            (
                "sandboxed_worker_code_cannot_write_outside_at_the_acceptance_runs",
                "sandboxed_worker_code_cannot_write_outside_at_the_gate_setup",
                "sandboxed_repository_hook_cannot_write_outside",
                "sandboxed_repository_hook_cannot_write_the_repositorys_git_folder",
                "sandboxed_repository_hook_can_still_stage_in_its_checkout",
                "sandboxed_check_author_code_cannot_write_outside_at_the_lock_stage",
                "a_case_folded_nested_git_folder_is_never_replayed",
                "a_case_folded_top_level_git_folder_is_never_replayed",
                "a_worker_written_nested_git_folder_is_reported_undeclared",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
