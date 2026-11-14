import json
import os
import shlex
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import core
import isolation
import joint
import own_tests
import run
import test_every_finding as fixtures
import test_finishes_the_work as finishes
import test_run

TESTS = "tests/test_all.py"
BASE = (
    "import unittest\n\n\nclass Seed(unittest.TestCase):\n"
    "    def holds(self):\n        self.assertTrue(True)\n"
)
FIRST_BLOCK = (
    "\n\nclass First(unittest.TestCase):\n"
    "    def works(self):\n        import a\n\n        self.assertEqual(a.value(), 2)\n"
)
SECOND_BLOCK = (
    "\n\nclass Second(unittest.TestCase):\n"
    "    def works(self):\n        import b\n\n        self.assertEqual(b.value(), 2)\n"
)
RENAMED = BASE.replace("class Seed", "class Renamed")
RETITLED = BASE.replace("class Seed", "class Retitled")
BOTH_KEPT = BASE.replace("class Seed", "class Renamed") + SECOND_BLOCK
FIRST_TEST = {"file": TESTS, "test": "First.works"}
SECOND_TEST = {"file": TESTS, "test": "Second.works"}
RIGHT = "def value():\n    return 2\n"

RESOLVER = r'''
import json
import sys

log, resolved, prompt, session = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(%(path)r, encoding="utf-8") as handle:
    seen = handle.read()
with open(log, "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"prompt": prompt, "session": session, "seen": seen}) + "\n")
if resolved != "-":
    with open(%(path)r, "w", encoding="utf-8") as handle:
        handle.write(resolved)
reply = {"item": "second", "status": "ok", "files_changed": [%(path)r], "notes": "joined"}
print(json.dumps({"type": "result", "session_id": session, "result": "done", "structured_output": reply}))
''' % {"path": TESTS}


def writer(name, work, test, after=()):
    step = test_run.step(name, [work, TESTS], acceptance=[test])
    return {**step, "after": list(after)} if after else step


def two_writers():
    return [writer("first", "a.py", FIRST_TEST), writer("second", "b.py", SECOND_TEST)]


def lanes_of(items):
    return [[items[index]["name"] for index in lane] for lane in core.lane_items(items)]


def never(path):
    return False


def always(path):
    return True


class Lanes(unittest.TestCase):
    def steps_that_each_add_tests_to_one_file_build_in_lanes_of_their_own(self):
        self.assertEqual(lanes_of(two_writers()), [["first"], ["second"]])

    def they_still_ship_as_one_msp(self):
        self.assertEqual(core.msp_items(two_writers()), ((0, 1),))

    def they_may_run_in_the_same_wave(self):
        _, _, waves = core.build_waves(two_writers())
        self.assertEqual(waves, [["first", "second"]])

    def a_step_that_edits_the_test_file_as_its_work_keeps_every_writer_in_its_lane(self):
        editor = test_run.step("editor", ["c.py", TESTS])
        self.assertEqual(lanes_of([*two_writers(), editor]), [["first", "second", "editor"]])

    def a_work_file_two_steps_share_still_joins_them(self):
        items = [writer("first", "a.py", FIRST_TEST), writer("second", "a.py", SECOND_TEST)]
        self.assertEqual(lanes_of(items), [["first", "second"]])

    def a_coupling_signal_between_two_such_steps_is_still_reviewed(self):
        items = two_writers()
        review = core.coupling_review(items, core.lane_items(items), {"a.py": ["b.py"]})
        self.assertEqual([entry["steps"] for entry in review], [["first", "second"]])

    def each_lanes_brief_says_other_lanes_add_tests_to_the_file(self):
        plan = core.plan(two_writers())
        self.assertEqual([brief.get("joint") for brief in plan["briefs"]], [[TESTS], [TESTS]])
        for brief in plan["briefs"]:
            self.assertIn(core.JOINT_LINE % TESTS, brief["text"])

    def a_plan_with_no_such_file_keeps_its_briefs_as_they_were(self):
        plan = core.plan([test_run.step("first", ["a.py"]), test_run.step("second", ["b.py"])])
        self.assertTrue(all("joint" not in brief for brief in plan["briefs"]))
        self.assertTrue(all("add their own tests" not in brief["text"] for brief in plan["briefs"]))


class CreationOrder(unittest.TestCase):
    def a_file_not_on_the_base_is_created_by_the_first_step_and_the_rest_wait(self):
        third = writer("third", "c.py", {"file": TESTS, "test": "Third.works"})
        ordered, lines = own_tests.created_once([*two_writers(), third], never)
        self.assertEqual([item.get("after") for item in ordered], [None, ["first"], ["first"]])
        self.assertEqual(lines, (own_tests.CREATED_LINE % (TESTS, "first", "second, third"),))

    def a_file_on_the_base_orders_nothing(self):
        ordered, lines = own_tests.created_once(two_writers(), always)
        self.assertEqual(ordered, two_writers())
        self.assertEqual(lines, ())

    def a_step_that_already_waits_on_another_writer_gains_no_edge(self):
        items = [writer("first", "a.py", FIRST_TEST), writer("second", "b.py", SECOND_TEST, ["first"])]
        ordered, lines = own_tests.created_once(items, never)
        self.assertEqual(ordered, items)
        self.assertEqual(lines, ())

    def writers_that_wait_on_each_other_are_left_for_the_cycle_refusal(self):
        items = [
            writer("first", "a.py", FIRST_TEST, ["second"]),
            writer("second", "b.py", SECOND_TEST, ["first"]),
        ]
        ordered, lines = own_tests.created_once(items, never)
        self.assertEqual(ordered, items)
        self.assertEqual(lines, ())

    def two_new_files_never_leave_their_writers_waiting_on_each_other(self):
        other = "tests/test_other.py"
        items = [
            {**test_run.step("c1", ["c1.py", TESTS], acceptance=[{"file": TESTS, "test": "A.a"}]), "after": ["m2"]},
            {**test_run.step("c2", ["c2.py", other], acceptance=[{"file": other, "test": "B.a"}]), "after": ["m1"]},
            test_run.step("m1", ["m1.py", TESTS], acceptance=[{"file": TESTS, "test": "A.b"}]),
            test_run.step("m2", ["m2.py", other], acceptance=[{"file": other, "test": "B.b"}]),
        ]
        ordered, _ = own_tests.created_once(items, never)
        self.assertEqual(core.cycles(ordered), ())
        self.assertEqual(core.lane_cycles(ordered, core.lane_items(ordered)), ())


class Joined(unittest.TestCase):
    def additions_at_the_same_place_are_both_kept_the_landed_one_first(self):
        found = joint.joined(BASE.encode(), (BASE + FIRST_BLOCK).encode(), (BASE + SECOND_BLOCK).encode())
        self.assertEqual(found.text.decode(), BASE + FIRST_BLOCK + SECOND_BLOCK)
        self.assertEqual(found.conflicts, 0)

    def additions_at_different_places_are_both_kept(self):
        ours = BASE.replace("import unittest\n", "import os\nimport unittest\n")
        found = joint.joined(BASE.encode(), ours.encode(), (BASE + SECOND_BLOCK).encode())
        self.assertEqual(found.text.decode(), ours + SECOND_BLOCK)
        self.assertEqual(found.conflicts, 0)

    def a_line_both_changed_is_left_marked_with_each_side(self):
        found = joint.joined(BASE.encode(), RENAMED.encode(), RETITLED.encode())
        self.assertEqual(found.conflicts, 1)
        marked = found.text.decode()
        self.assertIn(joint.OURS_MARK + "\nclass Renamed(unittest.TestCase):\n", marked)
        self.assertIn(joint.SPLIT_MARK + "\nclass Retitled(unittest.TestCase):\n" + joint.THEIRS_MARK + "\n", marked)

    def two_files_made_apart_are_left_marked_whole(self):
        found = joint.joined(None, (BASE + FIRST_BLOCK).encode(), (BASE + SECOND_BLOCK).encode())
        self.assertEqual(found.conflicts, 1)
        self.assertTrue(joint.marked(found.text))

    def a_joined_file_holds_no_mark(self):
        found = joint.joined(BASE.encode(), (BASE + FIRST_BLOCK).encode(), (BASE + SECOND_BLOCK).encode())
        self.assertFalse(joint.marked(found.text))


class Landing(test_run.RepoCase):
    def setUp(self):
        super().setUp()
        self.seed_existing(TESTS, BASE)
        self.plan = core.plan(two_writers())
        self.built = run.prepare_worktrees(self.plan["msps"], "main", self.trees, self.repo)
        self.tree = self.built[0]

    def _held(self, lane, writes, session=None):
        folders = isolation.lane_folders(self.run_dir, lane)
        start = isolation.cut_clone(self.tree, folders)
        for path, content in writes.items():
            test_run.write(os.path.join(folders["clone"], path), content)
        return {"lane": lane, "msp": 0, "state": run.LANE_HELD, "commit": None, "start": start, "session_id": session}

    def _resolver(self, resolved):
        script = os.path.join(self.tmp, "resolver.py")
        test_run.write(script, RESOLVER)
        self.resolved_log = os.path.join(self.tmp, "resolver.jsonl")
        return "%s %s %s %s {prompt} {session_id} {schema}" % (
            shlex.quote(sys.executable), shlex.quote(script), shlex.quote(self.resolved_log), shlex.quote(resolved),
        )

    def _asked(self):
        with open(self.resolved_log, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def _on_branch(self, path):
        return test_run.sh(["git", "show", "%s:%s" % (self.tree["branch"], path)], self.repo)

    def two_lanes_built_side_by_side_both_land_their_tests_in_the_file(self):
        prior = {
            0: self._held(0, {"a.py": RIGHT, TESTS: BASE + FIRST_BLOCK}),
            1: self._held(1, {"b.py": RIGHT, TESTS: BASE + SECOND_BLOCK}),
        }
        records = self.dispatch(self.plan, self.built, prior=prior)
        self.assertEqual([records[lane]["state"] for lane in (0, 1)], ["ok", "ok"])
        self.assertEqual(self._on_branch(TESTS), (BASE + FIRST_BLOCK + SECOND_BLOCK).strip())
        self.assertEqual(self.reask_calls(), [])

    def a_lane_that_left_the_file_alone_keeps_what_the_other_landed(self):
        prior = {
            0: self._held(0, {"a.py": RIGHT, TESTS: BASE + FIRST_BLOCK}),
            1: self._held(1, {"b.py": RIGHT}),
        }
        records = self.dispatch(self.plan, self.built, prior=prior)
        self.assertEqual([records[lane]["state"] for lane in (0, 1)], ["ok", "ok"])
        self.assertEqual(self._on_branch(TESTS), (BASE + FIRST_BLOCK).strip())

    def a_lane_whose_change_meets_anothers_is_asked_to_join_them_in_its_session(self):
        prior = {
            0: self._held(0, {"a.py": RIGHT, TESTS: RENAMED}),
            1: self._held(1, {"b.py": RIGHT, TESTS: RETITLED + SECOND_BLOCK}, session="session-of-second"),
        }
        records = self.dispatch(self.plan, self.built, prior=prior, reask=self._resolver(BOTH_KEPT))
        self.assertEqual([records[lane]["state"] for lane in (0, 1)], ["ok", "ok"])
        self.assertEqual(self._on_branch(TESTS), BOTH_KEPT.strip())
        asked = self._asked()
        self.assertEqual([entry["session"] for entry in asked], ["session-of-second"])
        self.assertIn(TESTS, asked[0]["prompt"])
        self.assertIn(joint.OURS_MARK + "\nclass Renamed(unittest.TestCase):\n", asked[0]["seen"])
        self.assertIn(SECOND_BLOCK, asked[0]["seen"])

    def a_lane_that_leaves_a_mark_in_the_file_fails_and_names_the_file(self):
        prior = {
            0: self._held(0, {"a.py": RIGHT, TESTS: RENAMED}),
            1: self._held(1, {"b.py": RIGHT, TESTS: RETITLED}, session="session-of-second"),
        }
        records = self.dispatch(self.plan, self.built, prior=prior, reask=self._resolver("-"))
        self.assertEqual([records[lane]["state"] for lane in (0, 1)], ["ok", "failed"])
        self.assertIn(TESTS, records[1]["reason"])
        self.assertEqual(self._on_branch(TESTS), RENAMED.strip())
        self.assertNotIn("b.py", test_run.sh(["git", "ls-tree", "-r", "--name-only", self.tree["branch"]], self.repo))

    def a_lane_with_no_session_to_ask_fails_and_names_the_file(self):
        prior = {
            0: self._held(0, {"a.py": RIGHT, TESTS: RENAMED}),
            1: self._held(1, {"b.py": RIGHT, TESTS: RETITLED}),
        }
        records = self.dispatch(self.plan, self.built, prior=prior)
        self.assertEqual([records[lane]["state"] for lane in (0, 1)], ["ok", "failed"])
        self.assertIn(TESTS, records[1]["reason"])
        self.assertEqual(self.reask_calls(), [])


JOBS = {
    "first": [{"writes": {"a.py": RIGHT, TESTS: BASE + FIRST_BLOCK}}],
    "second": [{"writes": {"b.py": RIGHT, TESTS: BASE + SECOND_BLOCK}}],
}


def author():
    return {**finishes.lock_author(), "files": [TESTS]}


def builders():
    return [
        fixtures.step("first", ["a.py"], "one", [FIRST_TEST], ["check-author"]),
        fixtures.step("second", ["b.py"], "one", [SECOND_TEST], ["check-author"]),
    ]


class Build(unittest.TestCase):
    def a_folded_items_file_builds_its_steps_apart_and_gates_both_tests(self):
        scratch = finishes.Scratch({TESTS: BASE})
        self.addCleanup(scratch.close)
        completed, summary = scratch.built([author(), *builders()], JOBS, flags=("--concurrency", "2"))
        self.assertEqual(completed.returncode, 0, fixtures.tail(completed))
        self.assertEqual([lane["steps"] for lane in summary["lanes"]], [["first"], ["second"]])
        self.assertEqual(len(set(scratch.sessions("first") + scratch.sessions("second"))), 2)
        gate = finishes.msp_of(summary, "one")["gate"]
        self.assertEqual(gate["outcome"], "pass", fixtures.tail(completed))
        self.assertEqual(gate["counts"]["pass"], 2, fixtures.tail(completed))

    def a_new_test_file_is_created_by_the_first_step_and_the_plan_says_so(self):
        scratch = finishes.Scratch()
        self.addCleanup(scratch.close)
        completed = scratch.mitosis(
            "--items", scratch.items([author(), *builders()]), "--plan-only", "--no-trajectories",
            "--run-dir", scratch.run_dir, "--feature-branch", "feature",
        )
        self.assertEqual(completed.returncode, 0, fixtures.tail(completed))
        with open(os.path.join(scratch.run_dir, "plan.json"), encoding="utf-8") as handle:
            plan = json.load(handle)
        self.assertEqual([lane["steps"] for lane in plan["lanes"]], [["first", "second"]])
        self.assertIn("mitosis: " + own_tests.CREATED_LINE % (TESTS, "first", "second"), completed.stderr.splitlines())


def load_tests(loader, standard, pattern):
    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            return sorted(
                name
                for name, value in vars(case).items()
                if callable(value) and not name.startswith("_") and name != "setUp"
            )

    suite = unittest.TestSuite()
    for case in (Lanes, CreationOrder, Joined, Landing, Build):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite
