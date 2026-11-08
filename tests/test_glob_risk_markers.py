import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_accept_execution as accept
from test_accept_execution import S, tail


class GlobRiskMarker(accept.Case):
    def a_glob_marker_is_no_coupling_signal(self):
        completed, plan = self.planned(
            [S("a", ["db/a.sql"], complexity="simple"), S("b", ["db/b.sql"], complexity="simple")],
            "--risk-markers",
            "*.sql",
        )
        self.assertEqual(plan.get("tiers"), {"a": "cheap", "b": "cheap"}, tail(completed))
        self.assertNotIn("coupling_review", plan, tail(completed))


def load_tests(loader, standard, pattern):
    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            return sorted(
                name for name in vars(case) if not name.startswith("_") and callable(getattr(case, name))
            )

    suite = unittest.TestSuite()
    suite.addTests(Loader().loadTestsFromTestCase(GlobRiskMarker))
    return suite


if __name__ == "__main__":
    unittest.main()
