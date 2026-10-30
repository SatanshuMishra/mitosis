import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import test_clean_gate as clean_gate
from test_clean_gate import CALC, lock_fixtures

PHPUNIT = "<phpunit/>\n"
VENDOR_FILE = "vendor/laravel/prompts/phpunit.xml"
VENDOR_FOLDER = "vendor/"


class InstalledVendorTree(clean_gate.CleanGate):
    def _assert_the_vendor_tree_never_holds_the_msp(self, completed):
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + lock_fixtures.tail(completed))
        msp = self._msp(completed)
        self.assertEqual(msp["reconcile"]["referee"], [])
        self.assertEqual(self._ignored(msp), [VENDOR_FOLDER])
        entries = self._accept_log()
        self.assertTrue(entries, "accept.log records no acceptance run")
        for entry in entries:
            self.assertEqual(
                [path for path in entry["listing"] if path.startswith(VENDOR_FOLDER)], []
            )

    def a_gitignored_vendor_tree_never_holds_the_msp(self):
        self._scratch(ignored=(VENDOR_FOLDER,))
        self._worker([["write", "calc.py", CALC], ["write", VENDOR_FILE, PHPUNIT]])
        completed = self._build(flags=self._charter(lock_fixtures.PLAIN_CHARTER))
        self._assert_the_vendor_tree_never_holds_the_msp(completed)

    def a_vendor_tree_hidden_by_the_clone_exclude_is_reverted_before_the_gate(self):
        self._scratch()
        self._worker(
            [
                ["write", "calc.py", CALC],
                ["write", VENDOR_FILE, PHPUNIT],
                ["append-git-path", "info/exclude", VENDOR_FOLDER],
            ]
        )
        completed = self._build(flags=self._charter(lock_fixtures.PLAIN_CHARTER))
        self.assertEqual(completed.returncode, 9, "mitosis exit code" + lock_fixtures.tail(completed))
        self.assertEqual(self._accept_log(), [])
        msp = self._msp(completed)
        self.assertEqual(msp["state"], "referee-reverted")
        self.assertEqual(msp["reconcile"]["referee"], [VENDOR_FILE])
        self.assertIs(msp["reconcile"]["fatal"], True)
        self.assertEqual(self._ignored(msp), [])
        self.assertNotEqual(
            self.scratch.git("cat-file", "-e", "mitosis/demo:" + VENDOR_FILE).returncode, 0
        )


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    suite.addTests(
        InstalledVendorTree(name)
        for name in (
            "a_gitignored_vendor_tree_never_holds_the_msp",
            "a_vendor_tree_hidden_by_the_clone_exclude_is_reverted_before_the_gate",
        )
    )
    return suite


if __name__ == "__main__":
    unittest.main()
