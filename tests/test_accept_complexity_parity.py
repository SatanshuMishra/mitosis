import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_accept_structure_parity as parity
from test_accept_execution import tail

SIMPLE_MEANING = "simple  (mechanical, fully specified)"
COMPLEX_MEANING = "complex (needs judgement, unfamiliar, or easy to get wrong)"


class ComplexityMeaning(parity.ParityCase):
    def the_structure_prompt_defines_simple_and_complex_as_the_reference_planner_does(self):
        completed = self._spec_plan_only(parity.spec_steps({"name": "a", "files": ["a.py"]}))
        self.exits(completed, 0)
        prompt = self._recorded("structure-prompt.txt", completed)
        self.assertIn(SIMPLE_MEANING, prompt)
        self.assertIn(COMPLEX_MEANING, prompt)
        self.assertNotIn("never rated simple", prompt)


class AssumptionsKeepComplexity(parity.ParityCase):
    def a_simple_step_carrying_an_assumption_stays_simple(self):
        completed = self._spec_plan_only(
            parity.spec_steps(
                {"name": "a", "files": ["a.py"], "complexity": "simple", "assumptions": ["took the narrow reading"]}
            )
        )
        self.exits(completed, 0)
        steps = self.loaded(os.path.join(self.scratch.run_dir, "structure.json"), completed)
        self.assertEqual(steps[0].get("complexity"), "simple", tail(completed))
        self.assertEqual(steps[0].get("assumptions"), ["took the narrow reading"], tail(completed))
        self.assertNotIn("raised from simple", completed.stdout)


def load_tests(loader, standard, pattern):
    helpers = ("make_scratch",)

    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            return sorted(
                name
                for name in vars(case)
                if not name.startswith("_") and callable(getattr(case, name)) and name not in helpers
            )

    suite = unittest.TestSuite()
    for case in (ComplexityMeaning, AssumptionsKeepComplexity):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite
