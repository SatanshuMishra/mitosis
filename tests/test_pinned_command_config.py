import io
import os
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
import test_gate_teeth

PAIRS = ((b"a\nb", b"a\nX\nY\nb"), (b"q", b"q\nz"))

CONFIG_VARIABLES = (
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_KEY_0",
    "GIT_CONFIG_VALUE_0",
)

PARAMETERS_SCOPE = {"GIT_CONFIG_PARAMETERS": "'diff.noprefix'='true'"}

COUNTED_SCOPE = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "diff.mnemonicPrefix",
    "GIT_CONFIG_VALUE_0": "true",
}

WEAK_TEST = "an_existence_only_test_of_a_broken_add_is_weak"


def without_command_scope():
    kept = {key: value for key, value in os.environ.items() if key not in CONFIG_VARIABLES}
    return mock.patch.dict(os.environ, kept, clear=True)


def added_lines_without_command_scope():
    with without_command_scope():
        return run._added_lines_of_each(PAIRS)


def command_scope_lines():
    with tempfile.TemporaryDirectory(prefix="mitosis-config-") as folder:
        shown = run.git_run(
            ["config", "--list", "--show-scope"], folder, env=run.PINNED_GIT
        ).stdout
    return [line for line in shown.splitlines() if line.startswith("command")]


class PinnedCommandConfig(unittest.TestCase):
    def the_baseline_is_the_two_added_runs_of_each_pair(self):
        self.assertEqual(added_lines_without_command_scope(), ([2, 3], [2]))

    def inherited_parameters_do_not_change_the_added_lines(self):
        expected = added_lines_without_command_scope()
        with mock.patch.dict(os.environ, PARAMETERS_SCOPE):
            found = run._added_lines_of_each(PAIRS)
        self.assertEqual(found, expected)

    def inherited_parameters_reach_no_pinned_git_call(self):
        with mock.patch.dict(os.environ, PARAMETERS_SCOPE):
            self.assertEqual(command_scope_lines(), [])

    def an_inherited_config_count_does_not_change_the_added_lines(self):
        expected = added_lines_without_command_scope()
        with mock.patch.dict(os.environ, COUNTED_SCOPE):
            found = run._added_lines_of_each(PAIRS)
        self.assertEqual(found, expected)

    def an_inherited_config_count_reaches_no_pinned_git_call(self):
        with mock.patch.dict(os.environ, COUNTED_SCOPE):
            self.assertEqual(command_scope_lines(), [])

    def the_explicit_settings_still_apply(self):
        with mock.patch.dict(os.environ, {**PARAMETERS_SCOPE, **COUNTED_SCOPE}):
            with tempfile.TemporaryDirectory(prefix="mitosis-config-") as folder:
                subprocess.run(["git", "init", "-q", folder], check=True)
                shown = run.git_run(
                    [*run.HISTORY_SETTINGS, "config", "--list", "--show-scope"],
                    folder,
                    env=run.PINNED_GIT,
                ).stdout
        self.assertEqual(
            [line for line in shown.splitlines() if line.startswith("command")],
            ["command\tlog.showsignature=false", "command\tdiff.relative=false"],
        )

    def a_hunk_header_before_any_file_header_is_an_error(self):
        unreadable = subprocess.CompletedProcess([], 1, b"@@ -1 +1,2 @@\n+x\n", "")
        with mock.patch.object(run, "git_run", return_value=unreadable):
            with self.assertRaises(run.GitError):
                run._added_lines_of_each(PAIRS)

    def the_gate_still_calls_a_broken_add_weak_with_a_config_count_set(self):
        with mock.patch.dict(os.environ, {**COUNTED_SCOPE}):
            outcome = unittest.TextTestRunner(stream=io.StringIO()).run(
                test_gate_teeth.GateTeeth(WEAK_TEST)
            )
        self.assertEqual((outcome.testsRun, len(outcome.failures), len(outcome.errors)), (1, 0, 0))


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            PinnedCommandConfig,
            (
                "the_baseline_is_the_two_added_runs_of_each_pair",
                "inherited_parameters_do_not_change_the_added_lines",
                "inherited_parameters_reach_no_pinned_git_call",
                "an_inherited_config_count_does_not_change_the_added_lines",
                "an_inherited_config_count_reaches_no_pinned_git_call",
                "the_explicit_settings_still_apply",
                "a_hunk_header_before_any_file_header_is_an_error",
                "the_gate_still_calls_a_broken_add_weak_with_a_config_count_set",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
