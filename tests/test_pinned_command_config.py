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

PARAMETERS_SCOPE = {"GIT_CONFIG_PARAMETERS": "'diff.noprefix'='true'"}

COUNTED_SCOPE = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "diff.mnemonicPrefix",
    "GIT_CONFIG_VALUE_0": "true",
}


def command_scope_lines():
    with tempfile.TemporaryDirectory(prefix="mitosis-config-") as folder:
        shown = run.git_run(
            ["config", "--list", "--show-scope"], folder, env=run.PINNED_GIT
        ).stdout
    return [line for line in shown.splitlines() if line.startswith("command")]


class PinnedCommandConfig(unittest.TestCase):

    def inherited_parameters_reach_no_pinned_git_call(self):
        with mock.patch.dict(os.environ, PARAMETERS_SCOPE):
            self.assertEqual(command_scope_lines(), [])

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


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            PinnedCommandConfig,
            (
                "inherited_parameters_reach_no_pinned_git_call",
                "an_inherited_config_count_reaches_no_pinned_git_call",
                "the_explicit_settings_still_apply",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
