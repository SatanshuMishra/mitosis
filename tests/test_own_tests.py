import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import core
import own_tests

READER = {
    "name": "reader",
    "task": "Read it.",
    "files": ["reader.py"],
    "source": None,
    "observable": "reader prints the count",
    "acceptance": [
        {"file": "tests/test_reader.py", "test": "R.reads"},
        {"file": "tests/test_reader.py", "test": "R.counts"},
    ],
}

UNWRITTEN_READER = (
    "tests/test_reader.py, named by Step reader, is written by no Step or check-author, "
    "is not locked and is not on the base"
)


def reader():
    return copy.deepcopy(READER)


class Owned(unittest.TestCase):
    def a_step_writes_the_test_file_its_acceptance_names(self):
        step = reader()
        self.assertEqual(own_tests.owned([step]), [{**READER, "files": ["reader.py", "tests/test_reader.py"]}])
        self.assertEqual(step, READER)

    def a_step_that_already_writes_its_test_is_returned_as_given(self):
        step = {**reader(), "files": ["reader.py", "./tests/test_reader.py"]}
        self.assertEqual(own_tests.owned([step]), [step])

    def a_locked_test_stays_out_of_the_write_set(self):
        self.assertEqual(own_tests.owned([reader()], locked=("tests/test_reader.py",)), [READER])


class Unwritten(unittest.TestCase):
    def an_items_file_check_author_writes_the_named_test(self):
        author = {
            "name": "check-author",
            "role": "check-author",
            "task": "Write the tests.",
            "files": ["tests/test_reader.py"],
            "source": None,
            "acceptance": [],
        }
        plan = core.plan([author, reader()])
        self.assertEqual([entry["slice"] for entry in plan.get("check_authors") or ()], [["tests/test_reader.py"]])
        self.assertEqual(own_tests.unwritten(plan, lambda path: False), ())

    def a_named_test_nothing_writes_is_reported_unless_held(self):
        plan = core.plan([reader()])
        self.assertEqual(own_tests.unwritten(plan, lambda path: False), (UNWRITTEN_READER,))
        self.assertEqual(own_tests.unwritten(plan, lambda path: True), ())


def load_tests(loader, standard, pattern):
    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            return sorted(
                name for name in vars(case) if not name.startswith("_") and callable(getattr(case, name))
            )

    suite = unittest.TestSuite()
    for case in (Owned, Unwritten):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite
