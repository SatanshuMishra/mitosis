import ast
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
RUN = os.path.join(os.path.dirname(HERE), "run.py")


def _strings(node):
    return [
        part.value
        for part in ast.walk(node)
        if isinstance(part, ast.Constant) and isinstance(part.value, str)
    ]


def _is_commit_lookup(listed):
    words = _strings(listed)
    return (
        "rev-parse" in words
        and "--verify" in words
        and any(word.endswith("^{commit}") for word in words)
    )


def commit_lookup_holders(source):
    tree = ast.parse(source)
    return sorted(
        function.name
        for function in ast.walk(tree)
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(node, (ast.List, ast.Tuple)) and _is_commit_lookup(node)
            for node in ast.walk(function)
        )
    )


class SingleRefLookup(unittest.TestCase):
    def one_function_resolves_a_ref_to_its_commit(self):
        with open(RUN, encoding="utf-8") as handle:
            holders = commit_lookup_holders(handle.read())
        self.assertEqual(len(holders), 1, "functions holding the lookup: %s" % ", ".join(holders))


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    suite.addTest(SingleRefLookup("one_function_resolves_a_ref_to_its_commit"))
    return suite
