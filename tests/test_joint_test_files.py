import argparse
import json
import os
import shlex
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import core
import isolation
import joint
import mitosis
import own_tests
import run
import test_every_finding as fixtures
import test_finishes_the_work as finishes
import test_run

TESTS = "tests/test_all.py"
MORE_TESTS = "tests/test_more.py"
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


REWRITER = r'''
import json
import sys

writes, session = json.loads(sys.argv[1]), sys.argv[3]
for path, text in writes.items():
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
reply = {"item": "second", "status": "ok", "files_changed": sorted(writes), "notes": "joined"}
print(json.dumps({"type": "result", "session_id": session, "result": "done", "structured_output": reply}))
'''


class Stopped(Exception):
    pass


def writer(name, work, test, after=()):
    step = test_run.step(name, [work, TESTS], acceptance=[test])
    return {**step, "after": list(after)} if after else step


def two_writers():
    return [writer("first", "a.py", FIRST_TEST), writer("second", "b.py", SECOND_TEST)]


def writer_of_two_files(name, work):
    return test_run.step(
        name,
        [work, TESTS, MORE_TESTS],
        acceptance=[
            {"file": TESTS, "test": name.title() + ".works"},
            {"file": MORE_TESTS, "test": name.title() + ".more"},
        ],
    )


def in_msp(items, label):
    return [{**item, "msp": label} for item in items]


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

    def a_test_file_declared_shared_is_never_joint(self):
        first, second = two_writers()
        items = [{**first, "msp": "one"}, {**second, "msp": "two"}]
        self.assertEqual(core.joint_tests(items, (TESTS,)), frozenset())
        self.assertEqual(own_tests.created_once(items, never, (TESTS,)), (items, ()))

    def a_declared_shared_test_file_is_still_a_coupling_signal(self):
        first, second = two_writers()
        items = [{**first, "msp": "one"}, {**second, "msp": "two"}]
        review = core.coupling_review(items, core.lane_items(items, (TESTS,)), {}, shared_files=(TESTS,))
        self.assertEqual([entry["signals"] for entry in review], [["shared-file:" + TESTS]])

    def steps_of_one_msp_that_share_a_declared_shared_test_file_stay_in_one_lane(self):
        plan = core.plan(in_msp(two_writers(), "one"), shared_files=(TESTS,))
        self.assertEqual([lane["steps"] for lane in plan["lanes"]], [["first", "second"]])
        self.assertTrue(all("joint" not in brief for brief in plan["briefs"]))

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

    def a_lone_waiting_step_joins_the_end_of_the_creators_lane(self):
        helper = {**test_run.step("helper", ["a.py"]), "after": ["first"]}
        items = [writer("first", "a.py", FIRST_TEST), helper, writer("second", "b.py", SECOND_TEST)]
        ordered, lines = own_tests.created_once(items, never)
        self.assertEqual(ordered[2]["after"], ["helper"])
        self.assertEqual(lanes_of(ordered), [["first", "helper", "second"]])
        self.assertEqual(lines, (own_tests.CREATED_LINE % (TESTS, "first", "second"),))

    def two_waiting_steps_wait_for_the_creator_and_build_apart(self):
        helper = {**test_run.step("helper", ["a.py"]), "after": ["first"]}
        third = writer("third", "c.py", {"file": TESTS, "test": "Third.works"})
        items = [writer("first", "a.py", FIRST_TEST), helper, writer("second", "b.py", SECOND_TEST), third]
        ordered, _ = own_tests.created_once(items, never)
        self.assertEqual([item.get("after") for item in ordered[2:]], [["first"], ["first"]])
        self.assertEqual(lanes_of(ordered), [["first", "helper"], ["second"], ["third"]])

    def a_lone_waiting_step_never_waits_on_a_step_that_waits_on_it(self):
        helper = {**test_run.step("helper", ["a.py"]), "after": ["first", "second"]}
        items = [writer("first", "a.py", FIRST_TEST), helper, writer("second", "b.py", SECOND_TEST)]
        ordered, _ = own_tests.created_once(items, never)
        self.assertEqual(ordered[2]["after"], ["first"])
        self.assertEqual(core.cycles(ordered), ())

    def a_lone_waiting_step_already_in_the_creators_lane_waits_for_the_creator(self):
        first = test_run.step("first", ["a.py", "shared.py", TESTS], acceptance=[FIRST_TEST])
        second = test_run.step("second", ["b.py", "shared.py", TESTS], acceptance=[SECOND_TEST])
        ordered, _ = own_tests.created_once([first, second], never)
        self.assertEqual(ordered[1]["after"], ["first"])
        self.assertEqual(core.cycles(ordered), ())

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

    def a_join_that_changes_a_line_the_branch_held_is_told_apart_from_one_that_keeps_it(self):
        found = joint.joined(BASE.encode(), (RENAMED + FIRST_BLOCK).encode(), (RETITLED + SECOND_BLOCK).encode())
        self.assertTrue(joint.keeps((RENAMED + FIRST_BLOCK + SECOND_BLOCK).encode(), found.held))
        self.assertFalse(joint.keeps((RETITLED + FIRST_BLOCK + SECOND_BLOCK).encode(), found.held))

    def a_lane_may_still_change_the_lines_it_added_itself(self):
        found = joint.joined(BASE.encode(), (RENAMED + FIRST_BLOCK).encode(), (RETITLED + SECOND_BLOCK).encode())
        reworked = SECOND_BLOCK.replace("class Second", "class Other")
        self.assertTrue(joint.keeps((RENAMED + FIRST_BLOCK + reworked).encode(), found.held))

    def every_line_of_a_file_another_lane_made_is_held(self):
        found = joint.joined(None, (BASE + FIRST_BLOCK).encode(), (BASE + SECOND_BLOCK).encode())
        self.assertTrue(joint.keeps((BASE + FIRST_BLOCK + SECOND_BLOCK).encode(), found.held))
        self.assertFalse(joint.keeps((BASE + SECOND_BLOCK).encode(), found.held))

    def a_joined_file_holds_no_mark(self):
        found = joint.joined(BASE.encode(), (BASE + FIRST_BLOCK).encode(), (BASE + SECOND_BLOCK).encode())
        self.assertFalse(joint.marked(found.text))


class ResumedBase(test_run.RepoCase):
    def a_resumed_run_orders_a_new_test_file_by_the_base_it_was_pinned_to(self):
        pinned = test_run.sh(["git", "rev-parse", "main"], self.repo).strip()
        fresh = argparse.Namespace(feature_branch="main", resume=False, plan_file=None, shared_files=None)
        first_run, _, _ = mitosis.folded_items(fresh, two_writers(), self.repo, None, self.repo, self.run_dir)
        run.write_json(os.path.join(self.run_dir, run.STATE_FILE), {"base_commit": pinned})
        self.seed_existing(TESTS, BASE)
        resumed = argparse.Namespace(**{**vars(fresh), "resume": True})
        again, _, _ = mitosis.folded_items(resumed, two_writers(), self.repo, None, self.repo, self.run_dir)
        self.assertEqual([item.get("after") for item in first_run], [None, ["first"]])
        self.assertEqual(again, first_run)

    def a_new_run_reads_the_branch_as_it_stands(self):
        pinned = test_run.sh(["git", "rev-parse", "main"], self.repo).strip()
        run.write_json(os.path.join(self.run_dir, run.STATE_FILE), {"base_commit": pinned})
        self.seed_existing(TESTS, BASE)
        fresh = argparse.Namespace(feature_branch="main", resume=False, plan_file=None, shared_files=None)
        ordered, _, _ = mitosis.folded_items(fresh, two_writers(), self.repo, None, self.repo, self.run_dir)
        self.assertEqual(ordered, two_writers())


class Confinement(unittest.TestCase):
    def a_lanes_confinement_is_made_afresh_for_a_second_turn_in_its_clone(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        folders = isolation.lane_folders(folder, 0)
        os.makedirs(folders["clone"])
        sandbox = {"program": "sandbox", "writes": ()}
        env = {**os.environ, "HOME": os.path.join(folder, "home")}
        first = isolation.worker_confinement(folders, sandbox, env)
        test_run.write(os.path.join(folders["tmp"], "left-by-the-first-turn"), "x")
        again = isolation.worker_confinement(folders, sandbox, env)
        self.assertEqual(again, first)
        self.assertEqual(os.listdir(folders["tmp"]), [])


class Landing(test_run.RepoCase):
    SEEDED = (TESTS,)

    def _steps(self):
        return two_writers()

    def setUp(self):
        super().setUp()
        for path in self.SEEDED:
            self.seed_existing(path, BASE)
        self.plan = core.plan(self._steps())
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

    def _rewriter(self, writes):
        script = os.path.join(self.tmp, "rewriter.py")
        test_run.write(script, REWRITER)
        return "%s %s %s {prompt} {session_id} {schema}" % (
            shlex.quote(sys.executable), shlex.quote(script), shlex.quote(json.dumps(writes)),
        )

    def _tracked(self):
        return test_run.sh(["git", "ls-tree", "-r", "--name-only", self.tree["branch"]], self.repo).split()

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

    def a_lane_whose_join_drops_a_line_the_branch_held_fails_and_lands_nothing(self):
        prior = {
            0: self._held(0, {"a.py": RIGHT, TESTS: RENAMED}),
            1: self._held(1, {"b.py": RIGHT, TESTS: RETITLED + SECOND_BLOCK}, session="session-of-second"),
        }
        records = self.dispatch(
            self.plan, self.built, prior=prior, reask=self._resolver(RETITLED + SECOND_BLOCK)
        )
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

    def a_join_that_cannot_be_started_fails_the_lane_and_not_the_run(self):
        prior = {
            0: self._held(0, {"a.py": RIGHT, TESTS: RENAMED}),
            1: self._held(1, {"b.py": RIGHT, TESTS: RETITLED}, session="session-of-second"),
        }
        absent = os.path.join(self.tmp, "no-such-program")
        records = self.dispatch(
            self.plan, self.built, prior=prior, reask=shlex.quote(absent) + " {prompt} {session_id} {schema}"
        )
        self.assertEqual([records[lane]["state"] for lane in (0, 1)], ["ok", "failed"])
        self.assertIn(TESTS, records[1]["reason"])
        self.assertNotIn("b.py", self._tracked())

    def lanes_that_landed_before_a_stop_land_their_tests_once_when_landed_again(self):
        prior = {
            0: self._held(0, {"a.py": RIGHT, TESTS: BASE + FIRST_BLOCK}),
            1: self._held(1, {"b.py": RIGHT, TESTS: BASE + SECOND_BLOCK}),
        }

        def stopped_before_any_record(lane, record):
            raise Stopped()

        with self.assertRaises(Stopped):
            self.dispatch(self.plan, self.built, prior=prior, on_lane=stopped_before_any_record)
        self.assertEqual(self._on_branch(TESTS), (BASE + FIRST_BLOCK + SECOND_BLOCK).strip())
        records = self.dispatch(self.plan, self.built, prior=prior)
        self.assertEqual([records[lane]["state"] for lane in (0, 1)], ["ok", "ok"])
        self.assertEqual(self._on_branch(TESTS), (BASE + FIRST_BLOCK + SECOND_BLOCK).strip())

    def a_lane_whose_kept_tests_cannot_be_read_fails_and_lands_nothing(self):
        prior = {
            0: self._held(0, {"a.py": RIGHT, TESTS: BASE + FIRST_BLOCK}),
            1: self._held(1, {"b.py": RIGHT, TESTS: BASE + SECOND_BLOCK}),
        }
        kept = isolation.lane_folders(self.run_dir, 1)["kept"]
        test_run.write(kept, "not json")
        records = self.dispatch(self.plan, self.built, prior=prior)
        self.assertEqual([records[lane]["state"] for lane in (0, 1)], ["ok", "failed"])
        self.assertIn(kept, records[1]["reason"])
        self.assertNotIn("b.py", self._tracked())

    def a_clone_cut_again_forgets_the_tests_kept_for_an_earlier_join(self):
        self._held(1, {"b.py": RIGHT})
        folders = isolation.lane_folders(self.run_dir, 1)
        test_run.write(folders["kept"], "{}")
        isolation.cut_clone(self.tree, folders)
        self.assertFalse(os.path.exists(folders["kept"]))

    def a_join_that_was_cut_short_is_asked_again_from_the_lanes_own_file(self):
        prior = {
            0: self._held(0, {"a.py": RIGHT, TESTS: RENAMED + FIRST_BLOCK}),
            1: self._held(1, {"b.py": RIGHT, TESTS: RETITLED + SECOND_BLOCK}, session="session-of-second"),
        }
        joined = RENAMED + FIRST_BLOCK + SECOND_BLOCK
        clone = isolation.lane_folders(self.run_dir, 1)["clone"]

        def cut_short_after_the_edit(argv, reask, cwd, files, env=None, sandbox=None):
            test_run.write(os.path.join(clone, TESTS), joined)
            raise Stopped()

        with mock.patch.object(run, "worker_call", cut_short_after_the_edit), self.assertRaises(Stopped):
            self.dispatch(self.plan, self.built, prior=prior)
        records = self.dispatch(self.plan, self.built, prior=prior, reask=self._resolver(joined))
        self.assertEqual([records[lane]["state"] for lane in (0, 1)], ["ok", "ok"])
        self.assertEqual(self._on_branch(TESTS), joined.strip())
        self.assertIn(joint.SPLIT_MARK + "\nclass Retitled(unittest.TestCase):\n", self._asked()[0]["seen"])


    def a_split_mark_left_behind_fails_the_lane_and_never_lands(self):
        prior = {
            0: self._held(0, {"a.py": RIGHT, TESTS: RENAMED}),
            1: self._held(1, {"b.py": RIGHT, TESTS: RETITLED + SECOND_BLOCK}, session="session-of-second"),
        }
        left = RENAMED + joint.SPLIT_MARK + "\n" + SECOND_BLOCK
        records = self.dispatch(self.plan, self.built, prior=prior, reask=self._resolver(left))
        self.assertEqual([records[lane]["state"] for lane in (0, 1)], ["ok", "failed"])
        self.assertIn(TESTS, records[1]["reason"])
        self.assertEqual(self._on_branch(TESTS), RENAMED.strip())

    def a_line_of_equals_signs_the_lane_wrote_itself_is_no_mark(self):
        ruled = SECOND_BLOCK + '\n\nRULE = """\n' + joint.SPLIT_MARK + '\n"""\n'
        prior = {
            0: self._held(0, {"a.py": RIGHT, TESTS: RENAMED}),
            1: self._held(1, {"b.py": RIGHT, TESTS: RETITLED + ruled}, session="session-of-second"),
        }
        records = self.dispatch(self.plan, self.built, prior=prior, reask=self._resolver(RENAMED + ruled))
        self.assertEqual([records[lane]["state"] for lane in (0, 1)], ["ok", "ok"])
        self.assertEqual(self._on_branch(TESTS), (RENAMED + ruled).strip())


class LandingThreeLanes(Landing):
    STRAY = BASE.replace("import unittest\n", "import unittest\nimport c\n")

    def _steps(self):
        first, second = two_writers()
        return in_msp([first, test_run.step("outsider", ["c.py"]), second], "one")

    def _all_held(self):
        return {
            0: self._held(0, {"a.py": RIGHT, TESTS: BASE + FIRST_BLOCK}),
            1: self._held(1, {"c.py": RIGHT, TESTS: self.STRAY}),
            2: self._held(2, {"b.py": RIGHT}),
        }

    def a_stray_write_to_the_file_never_lands_under_a_lane_that_left_it_alone(self):
        self.assertEqual([lane["steps"] for lane in self.plan["lanes"]], [["first"], ["outsider"], ["second"]])
        records = self.dispatch(self.plan, self.built, prior=self._all_held())
        self.assertEqual([records[lane]["state"] for lane in (0, 1, 2)], ["ok", "ok", "ok"])
        self.assertEqual(self._on_branch(TESTS), (BASE + FIRST_BLOCK).strip())

    def a_stray_write_never_lands_under_a_lane_landed_again_after_a_stop(self):
        prior = self._all_held()

        def stopped_before_any_record(lane, record):
            raise Stopped()

        with self.assertRaises(Stopped):
            self.dispatch(self.plan, self.built, prior=prior, on_lane=stopped_before_any_record)
        records = self.dispatch(self.plan, self.built, prior=prior)
        self.assertEqual([records[lane]["state"] for lane in (0, 1, 2)], ["ok", "ok", "ok"])
        self.assertEqual(self._on_branch(TESTS), (BASE + FIRST_BLOCK).strip())


class LandingTwoFiles(Landing):
    SEEDED = (TESTS, MORE_TESTS)

    def _steps(self):
        return [writer_of_two_files("first", "a.py"), writer_of_two_files("second", "b.py")]

    def a_join_turn_that_leaves_the_other_file_meeting_fails_the_lane_and_not_the_run(self):
        prior = {
            0: self._held(0, {"a.py": RIGHT, TESTS: RENAMED, MORE_TESTS: RENAMED}),
            1: self._held(1, {"b.py": RIGHT, TESTS: RETITLED}, session="session-of-second"),
        }
        records = self.dispatch(
            self.plan,
            self.built,
            prior=prior,
            reask=self._rewriter({TESTS: RENAMED + SECOND_BLOCK, MORE_TESTS: RETITLED}),
        )
        self.assertEqual([records[lane]["state"] for lane in (0, 1)], ["ok", "failed"])
        self.assertIn(MORE_TESTS, records[1]["reason"])
        self.assertEqual(self._on_branch(MORE_TESTS), RENAMED.strip())
        self.assertNotIn("b.py", self._tracked())

    def a_mark_left_in_the_other_file_during_a_join_never_reaches_the_branch(self):
        prior = {
            0: self._held(0, {"a.py": RIGHT, TESTS: RENAMED, MORE_TESTS: BASE + FIRST_BLOCK}),
            1: self._held(1, {"b.py": RIGHT, TESTS: RETITLED}, session="session-of-second"),
        }
        records = self.dispatch(
            self.plan,
            self.built,
            prior=prior,
            reask=self._rewriter({TESTS: RENAMED + SECOND_BLOCK, MORE_TESTS: joint.OURS_MARK + "\n" + BASE}),
        )
        self.assertEqual([records[lane]["state"] for lane in (0, 1)], ["ok", "failed"])
        self.assertIn(MORE_TESTS, records[1]["reason"])
        self.assertEqual(self._on_branch(MORE_TESTS), (BASE + FIRST_BLOCK).strip())
        self.assertNotIn("b.py", self._tracked())


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
    for case in (
        Lanes, CreationOrder, Joined, ResumedBase, Confinement, Landing, LandingThreeLanes, LandingTwoFiles, Build
    ):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite
