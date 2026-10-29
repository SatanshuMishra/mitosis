import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import run

HUNKS = 80000
TIME_LIMIT = 1


def alternating_inputs():
    old = b"".join(b"k%d = 0\n" % index for index in range(2 * HUNKS))
    new = b"".join(
        (b"k%d = 1\n" if index % 2 == 0 else b"k%d = 0\n") % index
        for index in range(2 * HUNKS)
    )
    return old, new


class LinearAddedLines(unittest.TestCase):
    def a_file_of_many_hunks_diffs_within_the_time_limit(self):
        old, new = alternating_inputs()
        started = time.monotonic()
        added = run._added_lines(old, new)
        elapsed = time.monotonic() - started
        self.assertEqual(added, [2 * index + 1 for index in range(HUNKS)])
        self.assertLess(elapsed, TIME_LIMIT, "the added-line diff took %.2f seconds" % elapsed)


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (LinearAddedLines, ("a_file_of_many_hunks_diffs_within_the_time_limit",)),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
