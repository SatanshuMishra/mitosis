import inspect
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import run

import test_per_msp_lock_plan as planning
import test_per_msp_lock_run as scaffold

INTERPHASE_0_14_MSP = "check-author"
REFEREE_REVERTED = "referee-reverted"
WORKER_WRITES = 'for path, content in sorted(job.get("writes", {}).items()):'
DELETING_WORKER = scaffold.WORKER.replace(
    WORKER_WRITES, 'for path in job.get("deletes", []):\n    os.remove(path)\n' + WORKER_WRITES
)
EMPTIED_TEST = "import unittest\n\n\nclass A(unittest.TestCase):\n    def returns_two(self):\n        pass\n"


def interphase_0_14(files):
    plain, *builders = scaffold.unlabelled(files)
    return [{**plain, "msp": INTERPHASE_0_14_MSP}, *builders]


class InterphaseCheckAuthorShape(unittest.TestCase):
    def setUp(self):
        self.scratch = scaffold.Scratch()
        self.addCleanup(self.scratch.close)

    def test_the_check_author_interphase_0_14_renders_is_split_by_acceptance(self):
        completed = self.scratch.saved_plan_only(
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


def msp_named(completed, label):
    found = [
        record
        for record in scaffold.summary_of(completed).get("msps") or []
        if record.get("msp") == label
    ]
    return found[0] if found else {}


class ConsumerTouchesProducerLock(unittest.TestCase):
    def setUp(self):
        self.scratch = scaffold.Scratch()
        self.addCleanup(self.scratch.close)
        scaffold.put(os.path.join(self.scratch.stubs, "worker.py"), DELETING_WORKER)
        self.items = self.scratch.items(scaffold.labelled(b_after_a=True))
        self.saved = self.scratch.saved(self.items)

    def _consumer_build(self, consumer_job):
        self.scratch.jobs(scaffold.standard_jobs(**{"build-b": consumer_job}))
        return self.scratch.build(self.items, "--plan-file", self.saved, "--no-push")

    def _locked_content(self, ref):
        shown = self.scratch.git("show", "%s:%s" % (ref, scaffold.TEST_A))
        return shown.stdout if shown.returncode == 0 else None

    def test_a_consumer_deleting_a_producers_merged_locked_test_is_a_referee_write(self):
        completed = self._consumer_build(
            {"writes": {scaffold.SRC_B: scaffold.WORK_SOURCE}, "deletes": [scaffold.TEST_A]}
        )
        beta = msp_named(completed, "beta")
        self.assertEqual(beta.get("state"), REFEREE_REVERTED, "beta" + scaffold.tail(completed))
        self.assertIn(scaffold.TEST_A, (beta.get("reconcile") or {}).get("referee") or [])

    def test_the_referee_restore_puts_back_a_producers_deleted_locked_test(self):
        completed = self._consumer_build(
            {"writes": {scaffold.SRC_B: scaffold.WORK_SOURCE}, "deletes": [scaffold.TEST_A]}
        )
        self.assertIn(scaffold.TEST_A, self.scratch.tree("mitosis/beta"), scaffold.tail(completed))
        self.assertEqual(self._locked_content("mitosis/beta"), self._locked_content("mitosis/alpha"))

    def test_a_consumer_whose_producer_failed_holds_no_producer_test(self):
        self.scratch.jobs(scaffold.standard_jobs(**{"build-a": {"exit": 1}}))
        completed = self.scratch.build(self.items, "--plan-file", self.saved, "--no-push")
        beta = msp_named(completed, "beta")
        self.assertNotEqual(beta.get("state"), REFEREE_REVERTED, scaffold.tail(completed))
        self.assertEqual((beta.get("reconcile") or {}).get("referee") or [], [])
        self.assertNotIn(scaffold.TEST_A, self.scratch.tree("mitosis/beta"))

    def test_the_referee_restore_puts_back_a_producers_rewritten_locked_test(self):
        first = self._consumer_build(
            {"writes": {scaffold.SRC_B: scaffold.WORK_SOURCE, scaffold.TEST_A: EMPTIED_TEST}}
        )
        self.assertEqual(msp_named(first, "beta").get("state"), REFEREE_REVERTED, scaffold.tail(first))
        self.assertEqual(self._locked_content("mitosis/beta"), self._locked_content("mitosis/alpha"))
        resumed = self.scratch.build(self.items, "--plan-file", self.saved, "--no-push", "--resume")
        self.assertIn(scaffold.TEST_A, self.scratch.tree("mitosis/beta"), scaffold.tail(resumed))
        self.assertEqual(self._locked_content("mitosis/beta"), self._locked_content("mitosis/alpha"))


class RefereeRestoreSource(unittest.TestCase):
    def test_every_referee_restore_names_the_locked_entries_it_puts_back(self):
        locked = inspect.signature(run.revert_implementation).parameters["locked_entries"]
        self.assertIs(locked.default, inspect.Parameter.empty)


if __name__ == "__main__":
    unittest.main()
