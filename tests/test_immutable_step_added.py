import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import run

ADDED_FILE = "m.py"
BEFORE_ENTRIES = {}
AFTER_ENTRIES = {ADDED_FILE: ("100644", "0" * 40)}
ARGUMENTS = ("tree", "base", "commit", (ADDED_FILE,))


def changes(tree, base, commit, paths):
    return (ADDED_FILE,), BEFORE_ENTRIES, AFTER_ENTRIES


def text_blobs(tree, entries, names):
    if entries is AFTER_ENTRIES:
        return {ADDED_FILE: b"a = 1\n"}
    return {}


def containers_in(value):
    if isinstance(value, (list, dict, set, bytearray)):
        yield value
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from containers_in(item)
    if isinstance(value, dict):
        for item in value.values():
            yield from containers_in(item)


class ImmutableStepAdded(unittest.TestCase):
    def setUp(self):
        run._step_added.cache_clear()
        self.addCleanup(run._step_added.cache_clear)

    def stepped_twice(self):
        with mock.patch.object(run, "_changes", changes), mock.patch.object(run, "_text_blobs", text_blobs):
            return run._step_added(*ARGUMENTS), run._step_added(*ARGUMENTS)

    def the_cached_value_is_a_tuple_of_tuples_all_the_way_down(self):
        first, _ = self.stepped_twice()
        self.assertIsInstance(first, tuple)
        self.assertEqual(first[0], ((ADDED_FILE, (1,)),))
        self.assertEqual(first[1], ((ADDED_FILE, b"a = 1\n"),))
        self.assertEqual([type(held).__name__ for held in containers_in(first)], [])
        self.assertEqual(self.tuple_types(first), {tuple, str, int, bytes})

    def tuple_types(self, value):
        found = {type(value)}
        if isinstance(value, tuple):
            for item in value:
                found |= self.tuple_types(item)
        return found

    def two_calls_with_the_same_arguments_return_equal_values(self):
        first, second = self.stepped_twice()
        self.assertEqual(first, second)
        self.assertIs(first, second)


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    suite.addTests(
        ImmutableStepAdded(name)
        for name in (
            "the_cached_value_is_a_tuple_of_tuples_all_the_way_down",
            "two_calls_with_the_same_arguments_return_equal_values",
        )
    )
    return suite
