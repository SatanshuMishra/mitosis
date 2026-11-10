import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mutants

PUSH = (
    "LIMIT = 3\n"
    "CALLS = []\n"
    "def push(terms):\n"
    "    if not terms:\n"
    "        return\n"
    "    CALLS.append(terms)\n"
)

TWO_FUNCTIONS = (
    "def first(value):\n"
    "    if value:\n"
    "        return 1\n"
    "    return 2\n"
    "\n"
    "\n"
    "def second(value):\n"
    "    if value:\n"
    "        return 3\n"
    "    return 4\n"
)

SAMPLE = (
    "import functools\n"
    "\n"
    "\n"
    "def traced(call):\n"
    "    @functools.wraps(call)\n"
    "    def inner(*args):\n"
    "        if args:\n"
    "            return call(*args)\n"
    "        elif call:\n"
    "            return None\n"
    "        else:\n"
    "            return 0\n"
    "    return inner\n"
    "\n"
    "\n"
    "@traced\n"
    "async def fetch(value):\n"
    "    \"\"\"keeps a docstring\"\"\"\n"
    "    if value > 1:\n"
    "        return value\n"
    "    return -value\n"
    "\n"
    "\n"
    "class Box:\n"
    "    def size(self, items): return len(items) if items else 0\n"
)


def reader(texts):
    return lambda path: texts[path]


def structural(path, text, lines):
    return mutants.structural({path: lines}, reader({path: text}))


def by_op(found, op):
    return [mutant for mutant in found if mutant["op"] == op]


class Structural(unittest.TestCase):
    def test_an_added_branch_is_negated(self):
        text = "def f(x):\n    if x:\n        return 2\n    return 1\n"
        found = structural("calc.py", text, [2])
        self.assertEqual(
            [mutant for mutant in found if mutant["op"] == mutants.NEGATE_BRANCH],
            [
                {
                    "file": "calc.py",
                    "line": 2,
                    "op": mutants.NEGATE_BRANCH,
                    "text": "def f(x):\n    if not (x):\n        return 2\n    return 1\n",
                }
            ],
        )

    def test_a_module_level_branch_is_left_alone(self):
        self.assertEqual(structural("calc.py", 'if __name__ == "__main__":\n    run()\n', [1, 2]), [])

    def test_a_lone_carriage_return_gives_nothing(self):
        self.assertEqual(structural("calc.py", "def f(x):\n    if x:\r        return 1\n", [1, 2, 3]), [])

    def test_a_windows_line_ending_still_gives_mutants(self):
        self.assertTrue(structural("calc.py", "def f(x):\r\n    if x:\r\n        return 1\r\n", [2]))

    def test_a_function_that_only_returns_none_gives_no_mutant(self):
        self.assertEqual(structural("calc.py", "def f(a):\n    return None\n", [1, 2]), [])

    def test_a_branch_that_holds_no_added_line_is_left_alone(self):
        self.assertEqual(by_op(structural("calc.py", PUSH, [6]), mutants.NEGATE_BRANCH), [])

    def test_a_changed_line_does_not_drop_the_function_it_is_in(self):
        self.assertEqual(by_op(structural("calc.py", PUSH, [5]), mutants.DROP_FUNCTION), [])

    def test_an_old_branch_that_decides_whether_added_code_runs_is_negated(self):
        found = structural("calc.py", PUSH, [5])
        branches = by_op(found, mutants.NEGATE_BRANCH)
        self.assertEqual(len(branches), 1)
        self.assertEqual(branches[0]["line"], 4)
        self.assertEqual(
            branches[0]["text"], PUSH.replace("if not terms:", "if not (not terms):")
        )

    def test_a_branch_in_an_untouched_function_is_left_alone(self):
        found = structural("calc.py", TWO_FUNCTIONS, [7, 9])
        self.assertEqual(
            [(mutant["line"], mutant["op"]) for mutant in found],
            [(7, mutants.DROP_FUNCTION), (8, mutants.NEGATE_BRANCH)],
        )

    def test_an_added_function_is_dropped(self):
        found = structural("calc.py", PUSH, [3])
        dropped = by_op(found, mutants.DROP_FUNCTION)
        self.assertEqual(
            dropped,
            [
                {
                    "file": "calc.py",
                    "line": 3,
                    "op": mutants.DROP_FUNCTION,
                    "text": "LIMIT = 3\nCALLS = []\ndef push(terms):\n    return None\n",
                }
            ],
        )

    def test_a_one_line_function_is_dropped(self):
        text = "def add(a, b): return a + b\nTOTAL = 1\n"
        found = structural("calc.py", text, [1])
        self.assertEqual(
            [(mutant["op"], mutant["text"]) for mutant in found],
            [(mutants.DROP_FUNCTION, "def add(a, b): return None\nTOTAL = 1\n")],
        )

    def test_a_multi_line_condition_is_negated_whole(self):
        text = "def f(alpha, beta):\n    if (alpha and\n            beta):\n        return 1\n"
        found = structural("calc.py", text, [2, 3, 4])
        self.assertEqual(
            [mutant["text"] for mutant in found if mutant["op"] == mutants.NEGATE_BRANCH],
            ["def f(alpha, beta):\n    if (not (alpha and\n            beta)):\n        return 1\n"],
        )

    def test_non_ascii_text_before_a_condition_keeps_the_offsets_right(self):
        text = "def label(name):\n    \u00e9t\u00e9 = \"\u00fcber\"; x = 1 if name else 2\n    if \u00e9t\u00e9 == name: return \"\u00e7a\"\n    return name\n"
        found = structural("calc.py", text, [1, 3])
        self.assertEqual(
            [(mutant["op"], mutant["text"]) for mutant in found],
            [
                (mutants.DROP_FUNCTION, "def label(name):\n    return None\n"),
                (
                    mutants.NEGATE_BRANCH,
                    text.replace("if \u00e9t\u00e9 == name:", "if not (\u00e9t\u00e9 == name):"),
                ),
            ],
        )

    def test_every_mutant_parses_as_python(self):
        lines = list(range(1, SAMPLE.count("\n") + 1))
        found = structural("sample.py", SAMPLE, lines)
        self.assertEqual(len(by_op(found, mutants.DROP_FUNCTION)), 4)
        self.assertEqual(len(by_op(found, mutants.NEGATE_BRANCH)), 3)
        for mutant in found:
            ast.parse(mutant["text"])
            self.assertNotEqual(mutant["text"], SAMPLE)

    def test_mutants_come_in_line_order(self):
        lines = list(range(1, SAMPLE.count("\n") + 1))
        found = structural("sample.py", SAMPLE, lines)
        places = [(mutant["line"], mutant["op"]) for mutant in found]
        self.assertEqual(places, sorted(places))
        self.assertEqual(
            places,
            [
                (4, mutants.DROP_FUNCTION),
                (6, mutants.DROP_FUNCTION),
                (7, mutants.NEGATE_BRANCH),
                (9, mutants.NEGATE_BRANCH),
                (17, mutants.DROP_FUNCTION),
                (19, mutants.NEGATE_BRANCH),
                (25, mutants.DROP_FUNCTION),
            ],
        )

    def test_a_file_that_does_not_parse_gives_nothing(self):
        found = mutants.structural(
            {"broken.py": [1], "calc.py": [5]},
            reader({"broken.py": "def broken(:\n    if x\n", "calc.py": PUSH}),
        )
        self.assertEqual({mutant["file"] for mutant in found}, {"calc.py"})
        self.assertEqual(structural("nul.py", "x = 1\x00\n", [1]), [])

    def test_another_language_gives_nothing(self):
        text = "function push(terms) {\n  if (!terms) { return; }\n}\n"
        self.assertEqual(structural("calc.js", text, [1, 2, 3]), [])
        self.assertEqual(structural("notes.md", PUSH, [1, 2, 3, 4, 5, 6]), [])

    def test_apply_returns_the_mutant_text(self):
        for mutant in structural("calc.py", PUSH, [4]):
            self.assertEqual(mutants.apply(PUSH, mutant), mutant["text"])
            self.assertEqual(mutants.apply("anything else", mutant), mutant["text"])


if __name__ == "__main__":
    unittest.main()
