import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_per_msp_lock_run as scaffold

EXIT_REFUSED = 3


def slice_of(entry):
    return list(entry["slice"] if "slice" in entry else entry.get("files") or [])


def msp_of(plan, step):
    found = [index for index, msp in enumerate(plan.get("msps") or []) if step in (msp.get("steps") or [])]
    return found[0] if len(found) == 1 else None


class PerMspLockPlan(unittest.TestCase):
    def setUp(self):
        self.scratch = scaffold.Scratch()
        self.addCleanup(self.scratch.close)

    def _planned(self, steps, label):
        completed = self.scratch.saved_plan_only(steps, label)
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + scaffold.tail(completed))
        plan = self.scratch.plan(label)
        entries = plan.get("check_authors")
        self.assertIsInstance(entries, list, "the plan holds no check_authors list" + scaffold.tail(completed))
        return plan, entries

    def _refused(self, steps, label, paths=(), words=()):
        completed = self.scratch.saved_plan_only(steps, label, scaffold.labelled())
        self.assertEqual(completed.returncode, EXIT_REFUSED, "mitosis exit code" + scaffold.tail(completed))
        output = completed.stdout + completed.stderr
        for path in paths:
            self.assertTrue(scaffold.names_path(output, path), "the refusal names no %s" % path + scaffold.tail(completed))
        for word in words:
            self.assertTrue(scaffold.names_word(output, word), "the refusal names no %s" % word + scaffold.tail(completed))
        return completed

    def test_labelled_check_authors_map_to_their_msps(self):
        plan, entries = self._planned(scaffold.labelled(), "labelled")
        by_name = {entry.get("name"): entry for entry in entries}
        self.assertEqual(sorted(by_name), ["check-author-alpha", "check-author-beta"])
        self.assertEqual(len(entries), 2)
        alpha, beta = msp_of(plan, "build-a"), msp_of(plan, "build-b")
        self.assertIsNotNone(alpha)
        self.assertIsNotNone(beta)
        self.assertNotEqual(alpha, beta)
        self.assertEqual(by_name["check-author-alpha"].get("msp"), alpha)
        self.assertEqual(slice_of(by_name["check-author-alpha"]), [scaffold.TEST_A])
        self.assertEqual(by_name["check-author-beta"].get("msp"), beta)
        self.assertEqual(slice_of(by_name["check-author-beta"]), [scaffold.TEST_B])

    def test_one_unlabelled_check_author_is_split_by_acceptance(self):
        plan, entries = self._planned(
            scaffold.unlabelled([scaffold.TEST_A, scaffold.TEST_B]), "unlabelled"
        )
        alpha, beta = msp_of(plan, "build-a"), msp_of(plan, "build-b")
        self.assertIsNotNone(alpha)
        self.assertIsNotNone(beta)
        self.assertNotEqual(alpha, beta)
        self.assertEqual(len(entries), 2)
        self.assertEqual(
            {entry.get("msp"): slice_of(entry) for entry in entries},
            {alpha: [scaffold.TEST_A], beta: [scaffold.TEST_B]},
        )
        self.assertEqual([entry.get("name") for entry in entries], ["check-author", "check-author"])

    def test_two_labels_in_one_msp_share_one_lock(self):
        plan, entries = self._planned(scaffold.labelled(b_extra_files=(scaffold.SRC_A,)), "joined")
        self.assertEqual(len(plan.get("msps") or []), 1)
        joined = msp_of(plan, "build-a")
        self.assertEqual(msp_of(plan, "build-b"), joined)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].get("msp"), joined)
        self.assertEqual(slice_of(entries[0]), [scaffold.TEST_A, scaffold.TEST_B])

    def test_a_label_naming_no_msp_is_refused(self):
        self._refused(
            scaffold.labelled(beta_author_msp="gamma"),
            "no-msp",
            words=("check-author-beta", "gamma"),
        )

    def test_two_check_authors_with_one_label_are_refused(self):
        self._refused(
            scaffold.labelled(beta_author_msp="alpha"),
            "one-label",
            words=("check-author-alpha", "check-author-beta", "alpha"),
        )

    def test_a_locked_file_named_by_two_msps_is_refused(self):
        self._refused(
            scaffold.unlabelled([scaffold.TEST_A], b_extra_acceptance=(scaffold.TEST_A,)),
            "two-msps",
            paths=(scaffold.TEST_A,),
            words=("build-a", "build-b"),
        )

    def test_a_locked_file_no_step_names_is_refused(self):
        self._refused(
            scaffold.unlabelled([scaffold.TEST_A, scaffold.TEST_B, scaffold.TEST_C]),
            "unnamed",
            paths=(scaffold.TEST_C,),
        )

    def test_a_step_naming_another_msps_locked_file_is_refused(self):
        self._refused(
            scaffold.labelled(b_extra_acceptance=(scaffold.TEST_A,)),
            "foreign",
            paths=(scaffold.TEST_A,),
            words=("build-b", "check-author-alpha"),
        )


if __name__ == "__main__":
    unittest.main()
