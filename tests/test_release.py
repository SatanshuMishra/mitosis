import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if not os.path.isfile(os.path.join(ROOT, "scripts", "release_check.py")):
    raise unittest.SkipTest("a bare copy of the modules carries no release check")

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

    def _main(self, terms=TERM, pr_text="", args=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = release_check.main(
                (args or ["--base", self.base]) + ["--root", self.root], {"DENY_TERMS": terms, "PR_TEXT": pr_text}
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

    def anything_new_the_plugin_would_load_ships(self):
        _commit(self.root, {"hooks/hooks.json": "{}\n"})
        self.assertEqual(release_check.shipped_changes(self.root, self.base), ("hooks/hooks.json",))

    def moving_a_shipped_file_out_of_what_ships_needs_a_bump(self):
        _commit(self.root, {"skills/mitosis/SKILL.md": "text\n", "core.py": _version("0.2.1")})
        released = _git(self.root, "rev-parse", "HEAD")
        os.makedirs(os.path.join(self.root, "docs"))
        _git(self.root, "mv", "skills/mitosis/SKILL.md", "docs/SKILL.md")
        _git(self.root, "commit", "-q", "-m", "move")
        self.assertIn("skills/mitosis/SKILL.md", release_check.shipped_changes(self.root, released))
        self.assertEqual(len(release_check.bump_problems(self.root, released)), 1)

    def a_change_to_what_does_not_ship_needs_no_bump(self):
        _commit(
            self.root,
            {
                "README.md": "# mitosis\nmore\n",
                "CHANGELOG.md": "# Changelog\n",
                "tests/test_run.py": "y = 1\n",
                "docs/notes.md": "n\n",
                "scripts/release_check.py": "z = 1\n",
                ".github/workflows/ci.yml": "name: ci\n",
                "receipts.config.json": "{}\n",
                "AGENTS.md": "agents\n",
                ".receipts/gates.md": "gates\n",
            },
        )
        self.assertEqual(release_check.shipped_changes(self.root, self.base), ())
        self.assertEqual(release_check.bump_problems(self.root, self.base), ())

    def no_release_yet_means_no_bump_is_owed(self):
        _commit(self.root, {"run.py": "x = 2\n"})
        self.assertIsNone(release_check.last_release(self.root))
        self.assertEqual(release_check.bump_problems(self.root, None), ())

    def the_latest_release_tag_is_the_base(self):
        _git(self.root, "tag", "mitosis--v0.2.0")
        _commit(self.root, {"run.py": "x = 2\n", "core.py": _version("0.10.0")})
        _git(self.root, "tag", "mitosis--v0.10.0")
        _commit(self.root, {"run.py": "x = 3\n"})
        self.assertEqual(release_check.last_release(self.root), "mitosis--v0.10.0")
        self.assertEqual(len(release_check.bump_problems(self.root, "mitosis--v0.10.0")), 1)


class DeniedTerms(Repo):
    def a_term_in_a_tracked_file_is_found_by_line_in_any_case(self):
        _commit(self.root, {"docs/notes.md": "one\nbuilt like ZebraCorn does\n", "core.py": _version("0.2.1")})
        self.assertEqual(
            release_check.denied(self.root, self.base, (TERM,)), ("denied term in docs/notes.md:2",)
        )

    def a_term_in_a_path_is_found_and_masked(self):
        _commit(self.root, {"ZebraCorn/notes.md": "clean\n"})
        self.assertIn(
            "denied term in *********/notes.md (path)", release_check.denied(self.root, self.base, (TERM,))
        )

    def a_term_in_text_that_is_not_utf8_is_found(self):
        path = os.path.join(self.root, "docs", "wide.txt")
        os.makedirs(os.path.dirname(path))
        with open(path, "wb") as handle:
            handle.write("built like zebracorn\n".encode("utf-16"))
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "wide")
        self.assertEqual(
            release_check.denied(self.root, self.base, (TERM,)),
            ("denied term in docs/wide.txt (in content that is not UTF-8 text)",),
        )

    def a_term_in_a_symlink_target_is_found(self):
        os.symlink("zebracorn-notes.md", os.path.join(self.root, "link.md"))
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "link")
        self.assertIn("denied term in link.md:1", release_check.denied(self.root, self.base, (TERM,)))

    def with_no_release_every_commit_message_is_scanned(self):
        _commit(self.root, {"README.md": "# mitosis\n\n"}, "zebracorn once")
        _commit(self.root, {"README.md": "# mitosis\n\n\n"}, "clean")
        self.assertEqual(len(release_check.commit_hits(self.root, None, (TERM,))), 1)

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
            release_check.text_hits("Before this change zebracorn delivered.", (TERM,)),
            ("pull request title or body",),
        )
        self.assertEqual(release_check.text_hits("Before this change it delivered.", (TERM,)), ())

    def masking_covers_every_term_that_matches_even_when_they_overlap(self):
        self.assertEqual(
            release_check.masked("docs/AcmeCorp-Internal/a.md", ("acme", "acmecorp-internal")),
            "docs/*****************/a.md",
        )
        self.assertEqual(release_check.masked("xabcdefx", ("abcd", "cdef")), "x******x")
        self.assertEqual(release_check.masked("clean", ("abcd",)), "clean")
        self.assertNotIn("zebracorn", release_check.masked("\u0130 zebracorn", (TERM,)).lower())

    def terms_split_on_commas_and_lines(self):
        self.assertEqual(release_check.terms_from(" Alpha, beta\n\ngamma ,"), ("alpha", "beta", "gamma"))


class Main(Repo):
    def a_clean_release_exits_zero(self):
        _commit(self.root, {"run.py": "x = 2\n", "core.py": _version("0.2.1")})
        self.assertEqual(self._main()[0], release_check.EXIT_OK)

    def a_hit_exits_one_and_never_prints_the_term(self):
        _commit(
            self.root,
            {"run.py": "zebracorn = 2\n", "core.py": _version("0.2.1"), "ZebraCorn/zebracorn.py": "x\n"},
        )
        code, output = self._main()
        self.assertEqual(code, release_check.EXIT_FAILED)
        self.assertIn("denied term in run.py:1", output)
        self.assertIn("*********/*********.py (path)", output)
        self.assertNotIn(TERM, output.lower())

    def a_missing_bump_never_prints_the_term_in_a_path(self):
        base = _commit(self.root, {"zebracorn.py": "x = 1\n", "core.py": _version("0.2.1")})
        _commit(self.root, {"zebracorn.py": "x = 2\n"})
        code, output = self._main(args=["--base", base])
        self.assertEqual(code, release_check.EXIT_FAILED)
        self.assertIn("starting with *********.py", output)
        self.assertNotIn(TERM, output.lower())

    def text_only_scans_the_pull_request_and_nothing_else(self):
        _commit(self.root, {"run.py": "zebracorn = 2\n"})
        self.assertEqual(self._main(args=["--text-only"], pr_text="A clean title")[0], release_check.EXIT_OK)
        code, output = self._main(args=["--text-only"], pr_text="Like ZEBRACORN did")
        self.assertEqual(code, release_check.EXIT_FAILED)
        self.assertNotIn(TERM, output.lower())

    def since_release_compares_with_the_latest_tag(self):
        _git(self.root, "tag", "mitosis--v0.2.0")
        _commit(self.root, {"run.py": "x = 2\n"})
        self.assertEqual(self._main(args=["--since-release"])[0], release_check.EXIT_FAILED)
        _commit(self.root, {"core.py": _version("0.2.1")})
        self.assertEqual(self._main(args=["--since-release"])[0], release_check.EXIT_OK)

    def a_push_compares_with_the_commit_before_it(self):
        _git(self.root, "tag", "mitosis--v0.2.0")
        _commit(self.root, {"run.py": "x = 2\n", "core.py": _version("0.2.1")})
        released = _git(self.root, "rev-parse", "HEAD")
        _commit(self.root, {"run.py": "x = 3\n"})
        args = ["--base", released, "--or-last-release"]
        self.assertEqual(self._main(args=args)[0], release_check.EXIT_FAILED)
        self.assertEqual(self._main(args=["--since-release"])[0], release_check.EXIT_OK)

    def a_message_already_on_main_does_not_fail_every_later_push(self):
        _commit(self.root, {"README.md": "# mitosis\n\n"}, "squashed as zebracorn")
        landed = _git(self.root, "rev-parse", "HEAD")
        self.assertEqual(self._main(args=["--base", self.base, "--or-last-release"])[0], release_check.EXIT_FAILED)
        _commit(self.root, {"README.md": "# mitosis\n\n\n"}, "clean")
        self.assertEqual(self._main(args=["--base", landed, "--or-last-release"])[0], release_check.EXIT_OK)

    def a_base_that_is_not_behind_head_falls_back_to_the_last_release(self):
        _git(self.root, "tag", "mitosis--v0.2.0")
        _commit(self.root, {"run.py": "x = 2\n"})
        for base in ("0" * 40, "no-such-ref"):
            code, _ = self._main(args=["--base", base, "--or-last-release"])
            self.assertEqual(code, release_check.EXIT_FAILED, base)
        _git(self.root, "checkout", "-q", "-b", "side", self.base)
        side = _commit(self.root, {"README.md": "# side\n"})
        _git(self.root, "checkout", "-q", "main")
        self.assertFalse(release_check.is_ancestor(self.root, side))
        self.assertEqual(self._main(args=["--base", side, "--or-last-release"])[0], release_check.EXIT_FAILED)

    def a_failure_to_run_git_is_a_misconfiguration_that_prints_no_term(self):
        error = FileNotFoundError(2, "No such file or directory", "/zebracorn/git")
        with mock.patch.object(release_check, "git", side_effect=error):
            code, output = self._main()
        self.assertEqual(code, release_check.EXIT_MISCONFIGURED)
        self.assertNotIn(TERM, output.lower())

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
