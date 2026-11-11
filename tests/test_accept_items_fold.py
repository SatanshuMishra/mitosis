import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import mitosis
import test_every_finding as fixtures
import test_finishes_the_work as finishes

AUTHOR = "check-author"
OTHER_TEST = "tests/test_b.py"
ALSO = {"file": finishes.TEST_FILE, "test": "A.also"}
FOLDED = (
    "mitosis: the check-author Step check-author is folded: each Step whose acceptance names its "
    "files writes them, so no lock stage runs"
)
DROPPED = (
    "mitosis: tests/test_b.py, written by the check-author Step check-author, is named by no "
    "Step's acceptance and is dropped"
)
HELPER = "tests/support.py"
SHARED = (
    "mitosis: tests/support.py, written by the check-author Step check-author, is named by no "
    "Step's acceptance, so every Step that takes one of that Step's test files writes it too"
)
HELPER_TEXT = "EXPECTED = 2\n"
SECOND_HELPER = "tests/more_support.py"
MARKER_HELPER = "tests/conftest.py"
THIRD_TEST = "tests/test_c.py"
MIDDLE_TEST = "tests/test_m.py"
SECOND_AUTHOR = "check-author-two"
UNNAMED = "mitosis: %s, written by the check-author Step check-author, is named by no Step's acceptance "
KEPT = UNNAMED % HELPER + "and stays with Step first, which already writes it"
REFEREE = (
    UNNAMED % MARKER_HELPER
    + "and is dropped, because it matches the referee marker conftest.py and no Step may write the referee"
)
LOCKED = UNNAMED % HELPER + "and is dropped, because it is locked by the charter and no Step may write the referee"
WAITING = UNNAMED % HELPER + "and is dropped, because the Steps that would write it would wait on each other"
HELPED_TEXT = (
    "import unittest\n\nimport a\nimport support\n\n\nclass A(unittest.TestCase):\n"
    "    def works(self):\n        self.assertEqual(a.value(), support.EXPECTED)\n"
)


def author(files=(finishes.TEST_FILE,)):
    return {**finishes.lock_author(), "files": list(files)}


def two_builders():
    return [
        fixtures.step("first", ["a.py"], "first", [finishes.PROPERTY], [AUTHOR]),
        fixtures.step("second", ["b.py"], "first", [ALSO], [AUTHOR]),
    ]


def steps_either_side_of_another():
    return [
        author((finishes.TEST_FILE, THIRD_TEST, HELPER)),
        fixtures.step("first", ["a.py"], "one", [finishes.PROPERTY], [AUTHOR]),
        fixtures.step("middle", ["m.py", MIDDLE_TEST], "two", [{"file": MIDDLE_TEST, "test": "M.works"}], ["first"]),
        fixtures.step("last", ["c.py"], "three", [{"file": THIRD_TEST, "test": "C.works"}], ["middle", AUTHOR]),
    ]


def plan_of(scratch):
    with open(os.path.join(scratch.run_dir, "plan.json"), encoding="utf-8") as handle:
        return json.load(handle)


def charter_lock_plan(case):
    scratch = finishes.Scratch({finishes.TEST_FILE: finishes.PROPERTY_TEXT})
    case.addCleanup(scratch.close)
    charter = scratch.file("charter.md", "# Charter\n\n- Locked: %s\n" % finishes.TEST_FILE)
    items = scratch.items([finishes.lock_author(), finishes.builder()])
    args = mitosis.build_parser().parse_args(["--items", items, "--no-trajectories", "--charter", charter])
    lock = mitosis.build_plan(args, mitosis.read_items(items), scratch.repo, mitosis.check_charter(charter), None)
    return scratch, items, charter, lock


def planned_from(scratch, items, charter, saved):
    return scratch.mitosis(
        "--items", items, "--plan-only", "--no-trajectories", "--feature-branch", "feature",
        "--charter", charter, "--plan-file", saved, "--run-dir", scratch.run_dir,
    )


def by_name(plan, name):
    return next(item for item in plan["items"] if item["name"] == name)


def folders_named(top, name):
    return [
        os.path.join(folder, entry)
        for folder, entries, _ in os.walk(top)
        for entry in entries
        if entry == name
    ]


class ItemsFold(unittest.TestCase):
    def setUp(self):
        self.scratch = finishes.Scratch()

    def tearDown(self):
        self.scratch.close()

    def _exit(self, completed, code):
        self.assertEqual(completed.returncode, code, "mitosis exit code" + fixtures.tail(completed))

    def _plan_only(self, items):
        return self.scratch.mitosis(
            "--items",
            items,
            "--plan-only",
            "--no-trajectories",
            "--run-dir",
            self.scratch.run_dir,
            "--feature-branch",
            "feature",
        )

    def a_check_author_is_folded_into_the_steps_that_name_its_files(self):
        completed = self._plan_only(self.scratch.items([author(), *two_builders()]))
        self._exit(completed, 0)
        plan = plan_of(self.scratch)
        self.assertFalse(plan.get("check_authors"), fixtures.tail(completed))
        self.assertEqual(by_name(plan, "first")["files"], ["a.py", finishes.TEST_FILE])
        self.assertEqual(by_name(plan, "second")["files"], ["b.py", finishes.TEST_FILE])
        self.assertEqual([item["name"] for item in plan["items"]], ["first", "second"])
        self.assertTrue(all(AUTHOR not in (item.get("after") or ()) for item in plan["items"]))
        self.assertTrue(all("after" not in item for item in plan["items"]), plan["items"])
        self.assertIn(FOLDED, completed.stderr.splitlines())
        self.assertIn(FOLDED[len("mitosis: "):], fixtures.summary_of(completed)["attention"])

    def a_folded_build_lands_and_gates_the_work_with_no_lock_folder(self):
        scratch = self.scratch
        completed, summary = scratch.built(
            [finishes.lock_author(), finishes.builder()],
            {"first": [{"writes": {"a.py": finishes.RIGHT, finishes.TEST_FILE: finishes.PROPERTY_TEXT}}]},
        )
        self._exit(completed, 0)
        self.assertEqual(scratch.attempts(AUTHOR), [], fixtures.tail(completed))
        self.assertEqual(len(scratch.attempts("first")), 1, fixtures.tail(completed))
        record = finishes.msp_of(summary, "first")
        self.assertEqual(record["gate"]["outcome"], "pass", fixtures.tail(completed))
        self.assertTrue(scratch.on_branch("mitosis/first", "a.py"))
        self.assertTrue(scratch.on_branch("mitosis/first", finishes.TEST_FILE))
        self.assertEqual(folders_named(scratch.run_dir, "lock"), [])
        self.assertIn(FOLDED, completed.stderr.splitlines())

    def items_with_no_check_author_keep_their_plan_id(self):
        scratch = finishes.Scratch({finishes.TEST_FILE: finishes.PROPERTY_TEXT})
        self.addCleanup(scratch.close)
        self.scratch, kept = scratch, self.scratch
        self.addCleanup(kept.close)
        items = scratch.items([fixtures.step("first", ["a.py"], "first", [finishes.PROPERTY])])
        completed = self._plan_only(items)
        self._exit(completed, 0)
        args = mitosis.build_parser().parse_args(["--items", items, "--plan-only", "--no-trajectories"])
        before = mitosis.build_plan(args, mitosis.read_items(items), scratch.repo, None, None)
        self.assertEqual(plan_of(scratch)["plan_id"], before["plan_id"])
        self.assertEqual(by_name(plan_of(scratch), "first")["files"], ["a.py"])
        self.assertNotIn("folded", completed.stderr)

    def a_folded_plan_saved_with_a_charter_lock_plans_again_from_its_file(self):
        scratch = finishes.Scratch({finishes.TEST_FILE: finishes.PROPERTY_TEXT})
        self.addCleanup(scratch.close)
        charter = scratch.file("charter.md", "# Charter\n\n- Locked: %s\n" % finishes.TEST_FILE)
        items = scratch.items([finishes.lock_author(), finishes.builder()])
        common = ("--items", items, "--plan-only", "--no-trajectories", "--feature-branch", "feature", "--charter", charter)
        first = scratch.mitosis(*common, "--run-dir", scratch.run_dir)
        self._exit(first, 0)
        saved = scratch.file("saved.json", json.dumps(plan_of(scratch)))
        again = scratch.mitosis(*common, "--plan-file", saved, "--run-dir", scratch.run_dir + "-again")
        self._exit(again, 0)

    def a_folded_plan_plans_again_from_its_file_without_its_charter(self):
        scratch = finishes.Scratch({finishes.TEST_FILE: finishes.PROPERTY_TEXT})
        self.addCleanup(scratch.close)
        charter = scratch.file("charter.md", "# Charter\n\n- Locked: %s\n" % finishes.TEST_FILE)
        items = scratch.items([finishes.lock_author(), finishes.builder()])
        common = ("--items", items, "--plan-only", "--no-trajectories", "--feature-branch", "feature")
        first = scratch.mitosis(*common, "--charter", charter, "--run-dir", scratch.run_dir)
        self._exit(first, 0)
        saved = scratch.file("saved.json", json.dumps(plan_of(scratch)))
        again = scratch.mitosis(*common, "--plan-file", saved, "--run-dir", scratch.run_dir + "-again")
        self._exit(again, 0)

    def a_lock_plan_saved_with_a_charter_lock_keeps_its_check_authors(self):
        scratch, items, charter, lock = charter_lock_plan(self)
        authors = [entry["name"] for entry in lock["check_authors"]]
        self.assertEqual(authors, [AUTHOR])
        again = planned_from(scratch, items, charter, scratch.file("saved.json", json.dumps(lock)))
        self._exit(again, 0)
        self.assertEqual([entry["name"] for entry in plan_of(scratch)["check_authors"]], authors)
        self.assertNotIn(FOLDED, again.stderr.splitlines())

    def a_lock_plan_saved_with_a_charter_lock_that_lost_its_check_authors_is_refused(self):
        scratch, items, charter, lock = charter_lock_plan(self)
        stripped = {key: value for key, value in lock.items() if key != "check_authors"}
        again = planned_from(scratch, items, charter, scratch.file("saved.json", json.dumps(stripped)))
        self._exit(again, 3)
        self.assertIn("has no check_authors", again.stderr)
        self.assertNotIn(FOLDED, again.stderr.splitlines())

    def a_check_author_file_no_step_names_goes_to_every_step_that_takes_its_tests(self):
        apart = fixtures.step("third", ["c.py", OTHER_TEST], "first", [{"file": OTHER_TEST, "test": "B.works"}], [AUTHOR])
        steps = [author((finishes.TEST_FILE, HELPER)), *two_builders(), apart]
        completed = self._plan_only(self.scratch.items(steps))
        self._exit(completed, 0)
        plan = plan_of(self.scratch)
        self.assertEqual(by_name(plan, "first")["files"], ["a.py", finishes.TEST_FILE, HELPER])
        self.assertEqual(by_name(plan, "second")["files"], ["b.py", finishes.TEST_FILE, HELPER])
        self.assertEqual(by_name(plan, "third")["files"], ["c.py", OTHER_TEST])
        self.assertIn(SHARED, completed.stderr.splitlines())
        self.assertIn(SHARED[len("mitosis: "):], fixtures.summary_of(completed)["attention"])
        self.assertNotIn("is dropped", completed.stderr)

    def a_helper_some_step_already_writes_stays_with_that_step(self):
        first = fixtures.step("first", ["a.py", HELPER], "first", [finishes.PROPERTY], [AUTHOR])
        second = fixtures.step("second", ["b.py"], "first", [ALSO], [AUTHOR])
        completed = self._plan_only(self.scratch.items([author((finishes.TEST_FILE, HELPER)), first, second]))
        self._exit(completed, 0)
        plan = plan_of(self.scratch)
        self.assertEqual(by_name(plan, "first")["files"], ["a.py", HELPER, finishes.TEST_FILE])
        self.assertEqual(by_name(plan, "second")["files"], ["b.py", finishes.TEST_FILE])
        self.assertIn(KEPT, completed.stderr.splitlines())

    def a_helper_that_matches_a_referee_marker_is_dropped_and_the_plan_stands(self):
        steps = [author((finishes.TEST_FILE, MARKER_HELPER)), finishes.builder()]
        completed = self._plan_only(self.scratch.items(steps))
        self._exit(completed, 0)
        self.assertEqual(by_name(plan_of(self.scratch), "first")["files"], ["a.py", finishes.TEST_FILE])
        self.assertIn(REFEREE, completed.stderr.splitlines())

    def a_helper_the_charter_locks_is_dropped_and_the_plan_stands(self):
        scratch = finishes.Scratch({HELPER: HELPER_TEXT})
        self.addCleanup(scratch.close)
        charter = scratch.file("charter.md", "# Charter\n\n- Locked: %s\n" % HELPER)
        items = scratch.items([author((finishes.TEST_FILE, HELPER)), finishes.builder()])
        completed = scratch.mitosis(
            "--items", items, "--plan-only", "--no-trajectories", "--feature-branch", "feature",
            "--charter", charter, "--run-dir", scratch.run_dir,
        )
        self._exit(completed, 0)
        self.assertEqual(by_name(plan_of(scratch), "first")["files"], ["a.py", finishes.TEST_FILE])
        self.assertIn(LOCKED, completed.stderr.splitlines())

    def a_helper_is_dropped_when_sharing_it_would_leave_lanes_waiting_on_each_other(self):
        completed = self._plan_only(self.scratch.items(steps_either_side_of_another()))
        self._exit(completed, 0)
        plan = plan_of(self.scratch)
        self.assertEqual([entry["steps"] for entry in plan["msps"]], [["first"], ["middle"], ["last"]])
        self.assertEqual(plan["lane_after"], {"1": [0], "2": [1]})
        self.assertIn(WAITING, completed.stderr.splitlines())
        self.assertNotIn(SHARED, completed.stderr.splitlines())

    def a_helper_declared_shared_reaches_steps_either_side_of_another_step(self):
        completed = self.scratch.mitosis(
            "--items", self.scratch.items(steps_either_side_of_another()), "--plan-only", "--no-trajectories",
            "--feature-branch", "feature", "--shared-files", HELPER, "--run-dir", self.scratch.run_dir,
        )
        self._exit(completed, 0)
        plan = plan_of(self.scratch)
        self.assertEqual(by_name(plan, "first")["files"], ["a.py", finishes.TEST_FILE, HELPER])
        self.assertEqual(by_name(plan, "last")["files"], ["c.py", THIRD_TEST, HELPER])
        self.assertEqual(plan["lane_after"], {"1": [0], "2": [1]})

    def a_dependency_cycle_beside_a_helper_is_still_refused_by_name(self):
        steps = [
            author((finishes.TEST_FILE, HELPER)),
            fixtures.step("first", ["a.py"], "first", [finishes.PROPERTY], [AUTHOR, "second"]),
            fixtures.step("second", ["b.py"], "first", [ALSO], [AUTHOR, "first"]),
        ]
        completed = self._plan_only(self.scratch.items(steps))
        self._exit(completed, 3)
        self.assertIn("dependency cycle: ", completed.stderr)

    def every_helper_reaches_a_taker_once_in_the_order_the_check_author_lists_them(self):
        files = (finishes.TEST_FILE, HELPER, SECOND_HELPER, HELPER.replace("/", "\\"))
        completed = self._plan_only(self.scratch.items([author(files), finishes.builder()]))
        self._exit(completed, 0)
        self.assertEqual(
            by_name(plan_of(self.scratch), "first")["files"], ["a.py", finishes.TEST_FILE, HELPER, SECOND_HELPER]
        )

    def a_step_gets_the_helpers_of_only_the_check_authors_whose_tests_it_takes(self):
        steps = [
            author((finishes.TEST_FILE, HELPER)),
            {**author((OTHER_TEST, SECOND_HELPER)), "name": SECOND_AUTHOR},
            fixtures.step("first", ["a.py"], "first", [finishes.PROPERTY], [AUTHOR]),
            fixtures.step("second", ["b.py"], "second", [{"file": OTHER_TEST, "test": "B.works"}], [SECOND_AUTHOR]),
        ]
        completed = self._plan_only(self.scratch.items(steps))
        self._exit(completed, 0)
        plan = plan_of(self.scratch)
        self.assertEqual(by_name(plan, "first")["files"], ["a.py", finishes.TEST_FILE, HELPER])
        self.assertEqual(by_name(plan, "second")["files"], ["b.py", OTHER_TEST, SECOND_HELPER])

    def steps_under_different_labels_that_share_a_helper_build_in_one_msp(self):
        steps = [
            author((finishes.TEST_FILE, OTHER_TEST, HELPER)),
            fixtures.step("first", ["a.py"], "one", [finishes.PROPERTY], [AUTHOR]),
            fixtures.step("second", ["b.py"], "two", [{"file": OTHER_TEST, "test": "B.works"}], [AUTHOR]),
        ]
        completed = self._plan_only(self.scratch.items(steps))
        self._exit(completed, 0)
        self.assertEqual([entry["steps"] for entry in plan_of(self.scratch)["msps"]], [["first", "second"]])

    def a_helper_two_check_authors_list_is_not_reported_dropped_once_a_step_writes_it(self):
        steps = [
            author((finishes.TEST_FILE, HELPER)),
            {**author((OTHER_TEST, HELPER)), "name": SECOND_AUTHOR},
            finishes.builder(),
        ]
        completed = self._plan_only(self.scratch.items(steps))
        self._exit(completed, 0)
        self.assertEqual(by_name(plan_of(self.scratch), "first")["files"], ["a.py", finishes.TEST_FILE, HELPER])
        self.assertIn(SHARED, completed.stderr.splitlines())
        self.assertNotIn("%s, written by the check-author Step %s" % (HELPER, SECOND_AUTHOR), completed.stderr)

    def a_check_author_file_is_dropped_when_no_step_takes_one_of_its_tests(self):
        scratch = finishes.Scratch({finishes.TEST_FILE: finishes.PROPERTY_TEXT})
        self.addCleanup(scratch.close)
        self.scratch, kept = scratch, self.scratch
        self.addCleanup(kept.close)
        completed = self._plan_only(scratch.items([author((OTHER_TEST,)), finishes.builder()]))
        self._exit(completed, 0)
        self.assertIn(DROPPED, completed.stderr.splitlines())
        self.assertIn(DROPPED[len("mitosis: "):], fixtures.summary_of(completed)["attention"])
        self.assertNotIn(OTHER_TEST, json.dumps(plan_of(scratch)["items"]))

    def a_folded_build_commits_the_helper_its_test_reads(self):
        scratch = self.scratch
        completed, summary = scratch.built(
            [author((finishes.TEST_FILE, HELPER)), finishes.builder()],
            {"first": [{"writes": {"a.py": finishes.RIGHT, finishes.TEST_FILE: HELPED_TEXT, HELPER: HELPER_TEXT}}]},
        )
        self._exit(completed, 0)
        record = finishes.msp_of(summary, "first")
        self.assertEqual(record["gate"]["outcome"], "pass", fixtures.tail(completed))
        self.assertTrue(scratch.on_branch("mitosis/first", HELPER))


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        ItemsFold(name)
        for name in (
            "a_check_author_is_folded_into_the_steps_that_name_its_files",
            "a_folded_build_lands_and_gates_the_work_with_no_lock_folder",
            "items_with_no_check_author_keep_their_plan_id",
            "a_check_author_file_no_step_names_goes_to_every_step_that_takes_its_tests",
            "a_helper_some_step_already_writes_stays_with_that_step",
            "a_check_author_file_is_dropped_when_no_step_takes_one_of_its_tests",
            "a_helper_that_matches_a_referee_marker_is_dropped_and_the_plan_stands",
            "a_helper_the_charter_locks_is_dropped_and_the_plan_stands",
            "a_helper_is_dropped_when_sharing_it_would_leave_lanes_waiting_on_each_other",
            "a_helper_declared_shared_reaches_steps_either_side_of_another_step",
            "a_dependency_cycle_beside_a_helper_is_still_refused_by_name",
            "every_helper_reaches_a_taker_once_in_the_order_the_check_author_lists_them",
            "a_step_gets_the_helpers_of_only_the_check_authors_whose_tests_it_takes",
            "steps_under_different_labels_that_share_a_helper_build_in_one_msp",
            "a_helper_two_check_authors_list_is_not_reported_dropped_once_a_step_writes_it",
            "a_folded_build_commits_the_helper_its_test_reads",
            "a_folded_plan_saved_with_a_charter_lock_plans_again_from_its_file",
            "a_lock_plan_saved_with_a_charter_lock_keeps_its_check_authors",
            "a_lock_plan_saved_with_a_charter_lock_that_lost_its_check_authors_is_refused",
            "a_folded_plan_plans_again_from_its_file_without_its_charter",
        )
    )
