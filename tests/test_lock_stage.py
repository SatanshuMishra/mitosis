import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import core

READER = {
    "name": "reader",
    "task": "Read it.",
    "files": ["reader.py"],
    "source": None,
    "observable": "reader prints the count",
    "acceptance": [{"file": "tests/test_reader.py", "test": "R.reads"}],
}

UNWRITTEN_READER = (
    "tests/test_reader.py, named by Step reader, is written by no Step or check-author, "
    "is not locked and is not on the base"
)


def reader():
    return copy.deepcopy(READER)


class CheckAuthors(unittest.TestCase):
    def an_unwritten_test_gets_one_check_author(self):
        import lock_stage

        step = reader()
        result = lock_stage.check_authors([step])
        self.assertEqual(
            result,
            [
                {
                    "name": "check-author-reader",
                    "role": "check-author",
                    "msp": "reader",
                    "task": "Write the locked checks for MSP reader before any of its Steps is built. Write each test below so it checks only the observable named after it, at a public surface of the work (its command line, an exported function or a file it writes), and fails on the current code:\n- tests/test_reader.py::R.reads for Step reader: reader prints the count",
                    "files": ["tests/test_reader.py"],
                    "source": None,
                    "acceptance": [],
                },
                {**READER, "msp": "reader", "after": ["check-author-reader"]},
            ],
        )
        self.assertEqual(step, READER)

    def a_step_that_writes_its_own_test_is_returned_as_given(self):
        import lock_stage

        step = {**reader(), "files": ["reader.py", "tests/test_reader.py"]}
        self.assertEqual(lock_stage.check_authors([step]), [step])

    def an_items_file_check_author_is_kept_as_given(self):
        import lock_stage

        author = {
            "name": "check-author",
            "role": "check-author",
            "task": "Write the tests.",
            "files": ["tests/test_reader.py"],
            "source": None,
            "acceptance": [],
        }
        items = [author, reader()]
        self.assertEqual(lock_stage.check_authors(items), items)

    def a_step_without_an_observable_is_named_with_its_task(self):
        import lock_stage

        step = {key: value for key, value in reader().items() if key != "observable"}
        result = lock_stage.check_authors([step])
        self.assertTrue(result[0]["task"].endswith("for Step reader: Read it."), result[0]["task"])

    def two_msps_get_two_check_authors_in_order(self):
        import lock_stage

        a = {
            "name": "a",
            "task": "t",
            "files": ["a.py"],
            "source": None,
            "observable": "a works",
            "acceptance": [{"file": "tests/test_a.py", "test": "A.works"}],
        }
        b = {
            "name": "b",
            "task": "t",
            "files": ["b.py"],
            "source": None,
            "observable": "b works",
            "acceptance": [{"file": "tests/test_b.py", "test": "B.works"}],
        }
        result = lock_stage.check_authors([a, b])
        self.assertEqual([item["name"] for item in result], ["check-author-a", "check-author-b", "a", "b"])
        self.assertEqual([item["files"] for item in result[:2]], [["tests/test_a.py"], ["tests/test_b.py"]])
        self.assertEqual(result[2]["after"], ["check-author-a"])
        self.assertEqual(result[3]["after"], ["check-author-b"])

    def a_held_or_locked_test_gets_no_check_author(self):
        import lock_stage

        self.assertEqual(
            lock_stage.check_authors([reader()], held=lambda path: path == "tests/test_reader.py"),
            [READER],
        )
        self.assertEqual(lock_stage.check_authors([reader()], locked=("tests/test_reader.py",)), [READER])


class Unwritten(unittest.TestCase):
    def a_planned_check_author_writes_the_named_test(self):
        import lock_stage

        plan = core.plan(lock_stage.check_authors([reader()]))
        entries = plan.get("check_authors") or []
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["slice"], ["tests/test_reader.py"])
        self.assertIn("reader prints the count", entries[0]["brief"]["text"])
        self.assertEqual(lock_stage.unwritten(plan, lambda path: False), ())

    def a_named_test_nothing_writes_is_reported_unless_held(self):
        import lock_stage

        plan = core.plan([reader()])
        self.assertEqual(lock_stage.unwritten(plan, lambda path: False), (UNWRITTEN_READER,))
        self.assertEqual(lock_stage.unwritten(plan, lambda path: True), ())


def load_tests(loader, standard, pattern):
    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            return sorted(
                name for name in vars(case) if not name.startswith("_") and callable(getattr(case, name))
            )

    suite = unittest.TestSuite()
    for case in (CheckAuthors, Unwritten):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite
