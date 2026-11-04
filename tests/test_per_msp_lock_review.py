import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_per_msp_lock_plan as planning
import test_per_msp_lock_run as scaffold

INTERPHASE_0_14_MSP = "check-author"


def interphase_0_14(files):
    plain, *builders = scaffold.unlabelled(files)
    return [{**plain, "msp": INTERPHASE_0_14_MSP}, *builders]


class InterphaseCheckAuthorShape(unittest.TestCase):
    def setUp(self):
        self.scratch = scaffold.Scratch()
        self.addCleanup(self.scratch.close)

    def test_the_check_author_interphase_0_14_renders_is_split_by_acceptance(self):
        completed = self.scratch.plan_only(
            interphase_0_14([scaffold.TEST_A, scaffold.TEST_B]), "interphase-0-14"
        )
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + scaffold.tail(completed))
        plan = self.scratch.plan("interphase-0-14")
        alpha = planning.msp_of(plan, "build-a")
        beta = planning.msp_of(plan, "build-b")
        self.assertEqual(
            {entry.get("msp"): planning.slice_of(entry) for entry in plan.get("check_authors") or []},
            {alpha: [scaffold.TEST_A], beta: [scaffold.TEST_B]},
        )


if __name__ == "__main__":
    unittest.main()
