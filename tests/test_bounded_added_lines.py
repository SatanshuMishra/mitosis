import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import run

TIME_LIMIT = 5
ENTRIES = 1000


def lock_file(shift):
    return "".join(
        '  "k%d": %d,\n  },\n  {\n' % (index, index + shift) for index in range(ENTRIES)
    ).encode()


class BoundedAddedLines(unittest.TestCase):
    def a_regenerated_lock_file_diffs_within_the_time_limit(self):
        started = time.monotonic()
        added = run._added_lines(lock_file(0), lock_file(1))
        elapsed = time.monotonic() - started
        self.assertEqual(added, [3 * index + 1 for index in range(ENTRIES)])
        self.assertLess(elapsed, TIME_LIMIT, "the added-line diff took %.1f seconds" % elapsed)

    def a_small_change_reports_its_inserted_and_replaced_lines(self):
        self.assertEqual(run._added_lines(b"a\nb\nc\n", b"a\nB\nc\nd\n"), [2, 4])
        self.assertEqual(run._added_lines(b"", b"x\ny\n"), [1, 2])


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            BoundedAddedLines,
            (
                "a_regenerated_lock_file_diffs_within_the_time_limit",
                "a_small_change_reports_its_inserted_and_replaced_lines",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
