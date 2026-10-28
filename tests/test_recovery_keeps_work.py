import contextlib
import io
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

import run
import test_interrupted_gate as interrupted

teeth = interrupted.teeth

BRANCH = "mitosis/one"
PROBE = "probe/" + BRANCH
BACKUP = "refs/mitosis/recovered/" + BRANCH
GATED = "LIMIT = 3\n"
TOKEN = "LATER-WORK-TOKEN\n"
NOTES = "base notes\n"


def later_commit(tree):
    teeth.put(os.path.join(tree, "notes.md"), TOKEN)
    git_in(tree, "commit", "-q", "-a", "-m", "later commit")


def tracked_edit(tree):
    teeth.put(os.path.join(tree, "notes.md"), TOKEN)


def marked_path_edit(tree):
    teeth.put(os.path.join(tree, "calc.py"), GATED + "# " + TOKEN)


def untracked_file(tree):
    teeth.put(os.path.join(tree, "later.txt"), TOKEN)


def other_branch_with_edit(tree):
    git_in(tree, "checkout", "-q", "-b", "operator/side")
    teeth.put(os.path.join(tree, "notes.md"), TOKEN)


def ignored_referee_file(tree):
    teeth.put(os.path.join(tree, "tests", "__snapshots__", "later.snap"), TOKEN)


def ignored_plain_file(tree):
    teeth.put(os.path.join(tree, "build", "out.txt"), TOKEN)


COMMITTED = (
    ("head", later_commit),
    ("probe", later_commit),
    ("head", other_branch_with_edit),
)

UNCOMMITTED = (
    ("head", tracked_edit, "notes.md", TOKEN),
    ("probe", tracked_edit, "notes.md", TOKEN),
    ("head", marked_path_edit, "calc.py", GATED + "# " + TOKEN),
    ("head", untracked_file, "later.txt", TOKEN),
    ("probe", untracked_file, "later.txt", TOKEN),
    ("head", ignored_referee_file, "tests/__snapshots__/later.snap", TOKEN),
)


def git_in(cwd, *args):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=teeth.CALL_LIMIT,
    ).stdout.strip()


def git_result(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=teeth.CALL_LIMIT
    )


def shown(cwd, spec):
    completed = subprocess.run(
        ["git", "show", spec], cwd=cwd, capture_output=True, timeout=teeth.CALL_LIMIT
    )
    return completed.returncode, completed.stdout.decode("utf-8")


def read_text(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


class RecoveryKeepsWork(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mitosis-recovery-")
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        patcher = mock.patch.dict(os.environ, {**teeth.GIT_IDENTITY, "HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _interrupted(self, site):
        root = tempfile.mkdtemp(dir=self.tmp)
        repo = os.path.join(root, "repo")
        teeth.put(os.path.join(repo, "calc.py"), "LIMIT = 1\n")
        teeth.put(os.path.join(repo, "notes.md"), NOTES)
        teeth.put(os.path.join(repo, ".gitignore"), "tests/__snapshots__/\nbuild/\n")
        git_in(repo, "init", "-q", "-b", "main")
        git_in(repo, "add", "-A")
        git_in(repo, "commit", "-q", "-m", "base")
        git_in(repo, "branch", "feature")
        tree = os.path.join(root, "tree")
        git_in(repo, "worktree", "add", "-q", "-b", BRANCH, tree, "feature")
        teeth.put(os.path.join(tree, "calc.py"), GATED)
        gated = run.commit_all(tree, "worker work")
        run.mark_gate(tree, BRANCH, gated, ("calc.py",), (), "feature")
        if site == "probe":
            git_in(tree, "checkout", "-q", "-B", PROBE)
        return tree, gated

    def _recovered(self, tree):
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            recovered = run.recover_gate(tree)
        return recovered, said.getvalue()

    def _moved_cause(self, tree, site, route, gated):
        if route is other_branch_with_edit:
            return (
                "HEAD is on operator/side, not %s or %s, so recovering the interrupted gate "
                "would discard the work there" % (BRANCH, PROBE)
            )
        moved = BRANCH if site == "head" else PROBE
        later = git_in(tree, "rev-parse", "HEAD")
        return (
            "%s moved from the gated commit %s to %s after the gate was interrupted, so "
            "recovering it would discard that work" % (moved, gated[:12], later[:12])
        )

    def _refuses_committed_work(self, site, route):
        tree, gated = self._interrupted(site)
        route(tree)
        head = git_in(tree, "rev-parse", "HEAD")
        marker = run.gate_marker(tree)
        cause = self._moved_cause(tree, site, route, gated)
        with self.assertRaises(run.GateRecoveryError) as refused:
            self._recovered(tree)
        self.assertEqual(
            str(refused.exception),
            "could not recover the interrupted gate in %s: %s; %s is kept, so the next run "
            "tries the recovery again" % (tree, cause, marker),
        )
        self.assertTrue(os.path.isfile(marker), "the gate marker is gone")
        self.assertEqual(git_in(tree, "rev-parse", "HEAD"), head)
        self.assertEqual(read_text(os.path.join(tree, "notes.md")), TOKEN)
        self.assertEqual(read_text(os.path.join(tree, "calc.py")), GATED)
        self.assertNotEqual(git_result(tree, "rev-parse", "--verify", BACKUP).returncode, 0)

    def _backs_up_uncommitted_work(self, site, route, path, later):
        tree, gated = self._interrupted(site)
        route(tree)
        recovered, said = self._recovered(tree)
        self.assertTrue(recovered)
        self.assertEqual(shown(tree, "%s:%s" % (BACKUP, path)), (0, later))
        backup = git_in(tree, "rev-parse", "--verify", BACKUP)
        self.assertEqual(git_in(tree, "rev-parse", "HEAD"), gated)
        self.assertEqual(git_in(tree, "symbolic-ref", "HEAD"), "refs/heads/" + BRANCH)
        self.assertEqual(read_text(os.path.join(tree, "calc.py")), GATED)
        self.assertEqual(git_in(tree, "rev-parse", BACKUP + "^1"), gated)
        self.assertEqual(
            said,
            "mitosis kept the work recovering the interrupted gate in %s replaced in %s at %s\n"
            % (tree, BACKUP, backup[:12]),
        )
        self.assertFalse(os.path.isfile(run.gate_marker(tree)), "the gate marker is kept")
        self.assertNotEqual(git_result(tree, "rev-parse", "--verify", PROBE).returncode, 0)

    def recovery_keeps_work_made_after_the_interruption(self):
        for site, route in COMMITTED:
            with self.subTest(site=site, route=route.__name__):
                self._refuses_committed_work(site, route)
        for site, route, path, later in UNCOMMITTED:
            with self.subTest(site=site, route=route.__name__):
                self._backs_up_uncommitted_work(site, route, path, later)

    def recovery_that_replaces_nothing_writes_no_backup(self):
        for site in ("head", "probe"):
            with self.subTest(site=site):
                tree, gated = self._interrupted(site)
                ignored_plain_file(tree)
                recovered, said = self._recovered(tree)
                self.assertTrue(recovered)
                self.assertEqual(said, "")
                self.assertEqual(read_text(os.path.join(tree, "build", "out.txt")), TOKEN)
                self.assertEqual(git_in(tree, "rev-parse", "HEAD"), gated)
                self.assertNotEqual(git_result(tree, "rev-parse", "--verify", BACKUP).returncode, 0)

    def a_second_recovery_keeps_the_first_backup(self):
        tree, gated = self._interrupted("head")
        tracked_edit(tree)
        self._recovered(tree)
        self.assertEqual(shown(tree, "%s:notes.md" % BACKUP), (0, TOKEN))
        first = git_in(tree, "rev-parse", "--verify", BACKUP)
        run.mark_gate(tree, BRANCH, gated, ("calc.py",), (), "feature")
        untracked_file(tree)
        self._recovered(tree)
        second = git_in(tree, "rev-parse", "--verify", BACKUP)
        self.assertEqual(git_in(tree, "rev-parse", second + "^1"), gated)
        self.assertEqual(git_in(tree, "rev-parse", second + "^2"), first)
        self.assertEqual(shown(tree, "%s:later.txt" % second), (0, TOKEN))
        self.assertEqual(shown(tree, "%s:notes.md" % second), (0, NOTES))


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            RecoveryKeepsWork,
            (
                "recovery_keeps_work_made_after_the_interruption",
                "recovery_that_replaces_nothing_writes_no_backup",
                "a_second_recovery_keeps_the_first_backup",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
