import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_accept_execution as accept
from test_accept_ports import checked_steps


def with_claimed(path):
    def claimed(plan):
        entry = plan["check_authors"][0]
        return {**plan, "check_authors": [{**entry, "slice": entry["slice"] + [path]}]}

    return claimed


class SavedPlanSlices(accept.Case):
    def saved_with(self, source, change):
        plan = accept.read_json(os.path.join(source, "plan.json"))
        return accept.put_json(self.scratch.path("saved.json"), change(plan))

    def refused(self, completed, second, path):
        self.exits(completed, 3)
        self.assertIn(path, completed.stderr)
        self.assertIn("check-author-a", completed.stderr)
        self.assertFalse(os.path.exists(os.path.join(second, "plan.json")))

    def a_saved_plan_whose_slice_claims_a_charter_lock_is_refused(self):
        charter = accept.put(self.scratch.path("charter.md"), "# Charter\n\n- Locked: tests/test_b.py\n")
        first = self.scratch.path("r1")
        self.planned(checked_steps(), "--charter", charter, run_dir=first)
        saved = self.saved_with(first, with_claimed("tests/test_b.py"))
        second = self.scratch.path("r2")
        completed = self.scratch.plan_only(
            checked_steps(), "--charter", charter, "--plan-file", saved, run_dir=second
        )
        self.refused(completed, second, "tests/test_b.py")

    def a_saved_plan_whose_slice_claims_a_build_file_is_refused(self):
        first = self.scratch.path("r1")
        self.planned(checked_steps(), run_dir=first)
        saved = self.saved_with(first, with_claimed("a.py"))
        second = self.scratch.path("r2")
        completed = self.scratch.plan_only(checked_steps(), "--plan-file", saved, run_dir=second)
        self.refused(completed, second, "a.py")


def load_tests(loader, standard, pattern):
    helpers = ("saved_with", "refused")

    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            return sorted(
                name
                for name in vars(case)
                if not name.startswith("_") and callable(getattr(case, name)) and name not in helpers
            )

    suite = unittest.TestSuite()
    suite.addTests(Loader().loadTestsFromTestCase(SavedPlanSlices))
    return suite


if __name__ == "__main__":
    unittest.main()
