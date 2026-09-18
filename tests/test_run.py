import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core
import run

ISOLATED_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "mitosis test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "mitosis test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "HOME": tempfile.gettempdir(),
}


def sh(args, cwd):
    return subprocess.run(
        args, cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.saved_env = {key: os.environ.get(key) for key in ISOLATED_GIT_ENV}
        os.environ.update(ISOLATED_GIT_ENV)
        self.tmp = tempfile.mkdtemp(prefix="mitosis-")
        self.repo = os.path.join(self.tmp, "repo")
        self.trees = os.path.join(self.tmp, "trees")
        os.makedirs(self.repo)
        sh(["git", "init", "-q", "-b", "main"], self.repo)
        write(os.path.join(self.repo, "README.md"), "seed\n")
        sh(["git", "add", "-A"], self.repo)
        sh(["git", "commit", "-q", "-m", "seed"], self.repo)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def branches(self):
        out = sh(["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"], self.repo)
        return sorted(out.splitlines())

    def worktree_paths(self):
        out = sh(["git", "worktree", "list", "--porcelain"], self.repo)
        return sorted(
            line[len("worktree "):]
            for line in out.splitlines()
            if line.startswith("worktree ")
        )


class Worktrees(RepoCase):
    def each_msp_gets_its_own_branch_and_worktree(self):
        msps = [{"label": "core", "steps": ["a"]}, {"label": "run", "steps": ["b"]}]
        trees = run.prepare_worktrees(msps, "main", self.trees, self.repo)
        self.assertEqual([t["msp"] for t in trees], [0, 1])
        self.assertEqual(len({t["branch"] for t in trees}), 2)
        self.assertEqual(len({t["path"] for t in trees}), 2)
        for tree in trees:
            self.assertTrue(os.path.isdir(tree["path"]))
            self.assertIn(tree["branch"], self.branches())
            self.assertEqual(sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], tree["path"]), tree["branch"])
            self.assertEqual(
                sh(["git", "rev-parse", "HEAD"], tree["path"]),
                sh(["git", "rev-parse", "main"], self.repo),
            )
        self.assertEqual(
            sorted(os.path.realpath(t["path"]) for t in trees),
            sorted(p for p in map(os.path.realpath, self.worktree_paths()) if p != os.path.realpath(self.repo)),
        )

    def a_rerun_reuses_an_existing_branch(self):
        msps = [{"label": "core", "steps": ["a"]}]
        first = run.prepare_worktrees(msps, "main", self.trees, self.repo)
        write(os.path.join(first[0]["path"], "work.txt"), "committed on the branch\n")
        sh(["git", "add", "-A"], first[0]["path"])
        sh(["git", "commit", "-q", "-m", "work"], first[0]["path"])
        head = sh(["git", "rev-parse", "HEAD"], first[0]["path"])
        second = run.prepare_worktrees(msps, "main", self.trees, self.repo)
        self.assertEqual(second[0]["branch"], first[0]["branch"])
        self.assertEqual(second[0]["path"], first[0]["path"])
        self.assertEqual(sh(["git", "rev-parse", "HEAD"], second[0]["path"]), head)
        self.assertEqual(self.branches().count(first[0]["branch"]), 1)
        shutil.rmtree(first[0]["path"])
        third = run.prepare_worktrees(msps, "main", self.trees, self.repo)
        self.assertEqual(third[0]["branch"], first[0]["branch"])
        self.assertTrue(os.path.isfile(os.path.join(third[0]["path"], "work.txt")))
        self.assertEqual(sh(["git", "rev-parse", "HEAD"], third[0]["path"]), head)

    def labels_that_slug_alike_get_separate_worktrees(self):
        msps = [{"label": "Core!", "steps": ["a"]}, {"label": "core", "steps": ["b"]}]
        trees = run.prepare_worktrees(msps, "main", self.trees, self.repo)
        self.assertNotEqual(trees[0]["path"], trees[1]["path"])
        self.assertNotEqual(trees[0]["branch"], trees[1]["branch"])
        self.assertTrue(all(os.path.isdir(t["path"]) for t in trees))
        again = run.prepare_worktrees(msps, "main", self.trees, self.repo)
        self.assertEqual([t["branch"] for t in again], [t["branch"] for t in trees])

    def a_git_failure_raises_with_its_stderr(self):
        with self.assertRaises(run.GitError) as caught:
            run.git(["checkout", "no-such-branch"], self.repo)
        self.assertIn("no-such-branch", str(caught.exception))


def load_tests(loader, tests, pattern):
    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            names = [
                name
                for name, value in vars(case).items()
                if callable(value) and not name.startswith("_")
            ]
            return sorted(names)

    suite = unittest.TestSuite()
    for case in (Worktrees,):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
