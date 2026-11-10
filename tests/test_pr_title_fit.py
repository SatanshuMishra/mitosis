import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import pr_facts
import test_every_finding as fixtures
import test_finishes_the_work as finishes

LONG_SUBJECT = "hand pull request facts to render with step names and tests"
SHORTENED_SUBJECT = "hand pull request facts to render with step names"
TWO_MSPS = {
    "msps": [{"label": "m", "steps": ["a"]}, {"label": "n", "steps": ["b"]}],
    "items": [],
}
ONE_MSP = {"msps": [{"label": "m", "steps": ["a"]}], "items": []}


def header(subject, scope="ship"):
    return {"type": "feat", "scope": scope, "subject": subject}


class TitleFit(unittest.TestCase):
    def a_labelled_title_of_75_characters_is_shortened_and_keeps_its_label(self):
        title = pr_facts._title(header(LONG_SUBJECT), TWO_MSPS, 1)
        self.assertEqual(title, "feat(ship): %s (n)" % SHORTENED_SUBJECT)

    def a_subject_that_fits_keeps_its_full_title(self):
        self.assertEqual(
            pr_facts._title(header("hand facts to render"), TWO_MSPS, 1),
            "feat(ship): hand facts to render (n)",
        )

    def a_single_msp_title_is_unchanged(self):
        self.assertEqual(
            pr_facts._title(header(LONG_SUBJECT), ONE_MSP, 0), "feat(ship): %s" % LONG_SUBJECT
        )

    def a_title_with_no_scope_is_shortened_too(self):
        title = pr_facts._title(header(LONG_SUBJECT + " and more words", None), TWO_MSPS, 0)
        self.assertEqual(title, "feat: %s (m)" % LONG_SUBJECT)

    def a_label_with_no_room_gives_way_to_the_msp_number_so_siblings_differ(self):
        plan = {
            "msps": [
                {"label": "checkout-payment-provider-migration-for-every-stored-card-step-one", "steps": ["a"]},
                {"label": "checkout-payment-provider-migration-for-every-stored-card-step-two", "steps": ["b"]},
            ],
            "items": [],
        }
        subject = "move checkout onto the new payment provider"
        self.assertEqual(
            [pr_facts._title(header(subject), plan, msp) for msp in (0, 1)],
            ["feat(ship): %s (part 1 of 2)" % subject, "feat(ship): %s (part 2 of 2)" % subject],
        )


class ThroughTheCommandLine(unittest.TestCase):
    def setUp(self):
        self.scratch = finishes.Scratch()

    def tearDown(self):
        self.scratch.close()

    def two_msps_with_a_long_subject_now_plan_with_fitting_titles(self):
        scratch = self.scratch
        steps = [
            fixtures.step("first", ["a.py"], "first"),
            fixtures.step("second", ["b.py"], "second"),
        ]
        completed = scratch.mitosis(
            "--items",
            scratch.items(steps),
            "--plan-only",
            "--run-dir",
            scratch.run_dir,
            "--feature-branch",
            "feature",
            "--pr-type",
            "feat",
            "--pr-scope",
            "ship",
            "--pr-subject",
            "hand pull request facts to render with step names and every test",
            "--pr-why",
            "Callers need it.",
            "--pr-impact",
            "Callers can add.",
            "--pr-command",
            scratch.stub("pr.py", "{branch}"),
            "--render-command",
            scratch.render,
        )
        self.assertEqual(completed.returncode, 0, fixtures.tail(completed))
        titles = []
        for index in range(2):
            with open(os.path.join(scratch.run_dir, "probe-%d.json" % index), encoding="utf-8") as handle:
                titles.append(json.load(handle)["title"])
        self.assertEqual(
            titles,
            [
                "feat(ship): hand pull request facts to render with step names (first)",
                "feat(ship): hand pull request facts to render with step names (second)",
            ],
        )


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for case in (TitleFit, ThroughTheCommandLine):
        names = [name for name in dir(case) if not name.startswith(("_", "setUp", "tearDown")) and callable(getattr(case, name))]
        suite.addTests(case(name) for name in names if not hasattr(unittest.TestCase, name))
    return suite


if __name__ == "__main__":
    unittest.main()
