import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.join(ROOT, "scripts"))

import release_check

GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "mitosis test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "mitosis test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}

TERM = "zebracorn"


def _git(root, *args):
    return subprocess.run(
        ("git", "-C", root) + args, check=True, capture_output=True, text=True, env=GIT_ENV
    ).stdout.strip()


def _write(root, name, content):
    path = os.path.join(root, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _version(text):
    return '__version__ = "%s"\n' % text


def _commit(root, files, message="change"):
    for name, content in files.items():
        _write(root, name, content)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


class Repo(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.root = self._directory.name
        _git(self.root, "init", "-q", "-b", "main")
        self.base = _commit(
            self.root,
            {"core.py": _version("0.2.0"), "run.py": "x = 1\n", "README.md": "# mitosis\n"},
            "start",
        )

    def tearDown(self):
        self._directory.cleanup()

    def _main(self, terms=TERM, pr_text=""):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = release_check.main(
                ["--base", self.base, "--root", self.root], {"DENY_TERMS": terms, "PR_TEXT": pr_text}
            )
        return code, out.getvalue() + err.getvalue()


class VersionBump(Repo):
    def a_shipped_change_without_a_bump_is_refused(self):
        _commit(self.root, {"run.py": "x = 2\n"})
        problems = release_check.bump_problems(self.root, self.base)
        self.assertEqual(len(problems), 1)
        self.assertIn("run.py", problems[0])
        self.assertIn("0.2.0 to 0.2.0", problems[0])

    def a_version_that_does_not_rise_is_refused(self):
        _commit(self.root, {"run.py": "x = 2\n", "core.py": _version("0.1.9")})
        self.assertEqual(len(release_check.bump_problems(self.root, self.base)), 1)

    def a_raised_version_clears_a_shipped_change(self):
        _commit(self.root, {"run.py": "x = 2\n", "core.py": _version("0.2.1")})
        self.assertEqual(release_check.bump_problems(self.root, self.base), ())

    def the_skill_and_the_plugin_manifest_ship(self):
        _commit(self.root, {"skills/mitosis/SKILL.md": "text\n"})
        self.assertEqual(len(release_check.bump_problems(self.root, self.base)), 1)
        _commit(self.root, {".claude-plugin/plugin.json": "{}\n", "core.py": _version("0.3.0")})
        self.assertEqual(release_check.bump_problems(self.root, self.base), ())

    def a_change_to_what_does_not_ship_needs_no_bump(self):
        _commit(self.root, {"README.md": "# mitosis\nmore\n", "tests/test_run.py": "y = 1\n"})
        self.assertEqual(release_check.shipped_changes(self.root, self.base), ())
        self.assertEqual(release_check.bump_problems(self.root, self.base), ())


class DeniedTerms(Repo):
    def a_term_in_a_tracked_file_is_found_by_line_in_any_case(self):
        _commit(self.root, {"docs/notes.md": "one\nbuilt like ZebraCorn does\n", "core.py": _version("0.2.1")})
        self.assertEqual(
            release_check.denied(self.root, self.base, (TERM,)), ("denied term in docs/notes.md:2",)
        )

    def a_term_in_a_path_is_found(self):
        _commit(self.root, {"zebracorn/notes.md": "clean\n"})
        self.assertIn(
            "denied term in zebracorn/notes.md (path)", release_check.denied(self.root, self.base, (TERM,))
        )

    def a_term_in_a_commit_message_since_the_base_is_found(self):
        head = _commit(self.root, {"README.md": "# mitosis\n\n"}, "match the zebracorn defaults")
        self.assertEqual(
            release_check.denied(self.root, self.base, (TERM,)), ("denied term in commit %s" % head[:7],)
        )

    def a_commit_before_the_base_is_not_rescanned(self):
        _commit(self.root, {"README.md": "# mitosis\n\n"}, "zebracorn once")
        newer = _git(self.root, "rev-parse", "HEAD")
        self.assertEqual(release_check.denied(self.root, newer, (TERM,)), ())

    def a_term_in_the_pull_request_text_is_found(self):
        self.assertEqual(
            release_check.denied(self.root, self.base, (TERM,), "Before this change zebracorn delivered."),
            ("denied term in pull request title or body",),
        )

    def terms_split_on_commas_and_lines(self):
        self.assertEqual(release_check.terms_from(" Alpha, beta\n\ngamma ,"), ("alpha", "beta", "gamma"))


class Main(Repo):
    def a_clean_release_exits_zero(self):
        _commit(self.root, {"run.py": "x = 2\n", "core.py": _version("0.2.1")})
        self.assertEqual(self._main()[0], release_check.EXIT_OK)

    def a_hit_exits_one_and_never_prints_the_term(self):
        _commit(self.root, {"run.py": "zebracorn = 2\n", "core.py": _version("0.2.1")})
        code, output = self._main()
        self.assertEqual(code, release_check.EXIT_FAILED)
        self.assertIn("denied term in run.py:1", output)
        self.assertNotIn(TERM, output.replace("run.py:1", ""))

    def a_missing_bump_exits_one(self):
        _commit(self.root, {"run.py": "x = 2\n"})
        self.assertEqual(self._main()[0], release_check.EXIT_FAILED)

    def no_terms_is_a_misconfiguration_not_a_pass(self):
        self.assertEqual(self._main(terms="")[0], release_check.EXIT_MISCONFIGURED)
        self.assertEqual(self._main(terms=" ,\n")[0], release_check.EXIT_MISCONFIGURED)

    def an_unknown_base_is_a_misconfiguration(self):
        self.base = "0" * 40
        self.assertEqual(self._main()[0], release_check.EXIT_MISCONFIGURED)


class ThisRepository(unittest.TestCase):
    def the_version_line_the_check_reads_is_the_one_core_declares(self):
        with open(os.path.join(ROOT, "core.py"), encoding="utf-8") as handle:
            match = release_check.VERSION_LINE.search(handle.read())
        self.assertIsNotNone(match)
        sys.path.insert(0, ROOT)
        import core

        self.assertEqual(".".join(match.groups()), core.__version__)


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
    for case in (VersionBump, DeniedTerms, Main, ThisRepository):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
