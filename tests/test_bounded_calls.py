import ast
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import run

SUBPROCESS_CALLS = ("run", "call", "check_call", "check_output")
PROCESS_WAITS = ("wait", "communicate")
CALL_LIMIT = 60
HOOK_LIMIT = 2
ENDS_WITHIN = 20


def shipped_sources():
    top = sorted(
        os.path.join(ROOT, name)
        for name in os.listdir(ROOT)
        if name.endswith(".py") and os.path.isfile(os.path.join(ROOT, name))
    )
    scripts = os.path.join(ROOT, "scripts")
    nested = sorted(
        os.path.join(scripts, name)
        for name in (os.listdir(scripts) if os.path.isdir(scripts) else ())
        if name.endswith(".py") and os.path.isfile(os.path.join(scripts, name))
    )
    return top + nested


def named_call(node):
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    func = node.func
    through_subprocess = (
        isinstance(func.value, ast.Name)
        and func.value.id == "subprocess"
        and func.attr in SUBPROCESS_CALLS
    )
    return through_subprocess or func.attr in PROCESS_WAITS


def named_calls(path):
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    return [node for node in ast.walk(tree) if named_call(node)]


def bounded(node):
    return any(keyword.arg == "timeout" for keyword in node.keywords)


class BoundedCalls(unittest.TestCase):
    def setUp(self):
        self.tmp = None

    def tearDown(self):
        if self.tmp is not None:
            shutil.rmtree(self.tmp, ignore_errors=True)

    def every_subprocess_call_has_a_timeout(self):
        sources = shipped_sources()
        self.assertIn(os.path.join(ROOT, "run.py"), sources)
        self.assertIn(os.path.join(ROOT, "scripts", "release_check.py"), sources)
        found = [(path, node) for path in sources for node in named_calls(path)]
        self.assertGreater(len(found), 0, "the walk found no subprocess call at all")
        offending = [
            "%s:%d" % (os.path.relpath(path, ROOT), node.lineno)
            for path, node in found
            if not bounded(node)
        ]
        self.assertEqual(
            offending,
            [],
            "subprocess calls or process waits without a timeout keyword: %s"
            % ", ".join(offending),
        )

    def a_hanging_pre_commit_hook_ends_with_a_named_error(self):
        self.assertTrue(hasattr(run, "GitTimeout"), "run has no GitTimeout error")
        self.tmp = tempfile.mkdtemp(prefix="mitosis-hook-")
        repo = os.path.join(self.tmp, "repo")
        home = os.path.join(self.tmp, "home")
        os.makedirs(repo)
        os.makedirs(home)
        env = {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_NAME": "mitosis test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "mitosis test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "HOME": home,
        }
        scaffold = {**os.environ, **env}
        for args in (
            ["git", "init", "-q", "-b", "main", repo],
            ["git", "-C", repo, "commit", "-q", "--allow-empty", "-m", "seed"],
        ):
            subprocess.run(args, check=True, capture_output=True, env=scaffold, timeout=CALL_LIMIT)
        with open(os.path.join(repo, "staged.txt"), "w", encoding="utf-8") as handle:
            handle.write("staged\n")
        subprocess.run(
            ["git", "-C", repo, "add", "staged.txt"],
            check=True,
            capture_output=True,
            env=scaffold,
            timeout=CALL_LIMIT,
        )
        hook = os.path.join(repo, ".git", "hooks", "pre-commit")
        os.makedirs(os.path.dirname(hook), exist_ok=True)
        with open(hook, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nsleep 60\n")
        os.chmod(hook, os.stat(hook).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        started = time.monotonic()
        with unittest.mock.patch.dict(os.environ, env):
            with self.assertRaises(run.GitTimeout) as caught:
                run.git(["commit", "-q", "-m", "x"], repo, timeout=HOOK_LIMIT)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, ENDS_WITHIN)
        self.assertIsInstance(caught.exception, run.GitError)
        self.assertIn("git commit -q -m x timed out after 2 seconds", str(caught.exception))
        self.assertIn("hook", str(caught.exception))


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            BoundedCalls,
            (
                "every_subprocess_call_has_a_timeout",
                "a_hanging_pre_commit_hook_ends_with_a_named_error",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
