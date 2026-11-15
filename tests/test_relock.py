import contextlib
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import referee
import run
import test_per_msp_lock_run as stage
import test_referee_lock as lock_fixtures

step = lock_fixtures.step
tail = lock_fixtures.tail

LABEL = "demo"
BRANCH = "mitosis/demo"
CHECK = "tests/test_calc.py"
OTHER = "tests/test_total.py"
PROPERTY = {"file": CHECK, "test": "Calc.adds"}
OTHER_PROPERTY = {"file": OTHER, "test": "Total.sums"}
GUARD = "tests/test_guard.py"
GUARD_PROPERTY = {"file": GUARD, "test": "Guard.holds"}
GUARD_CHECK = (
    "import unittest\n\n\nclass Guard(unittest.TestCase):\n    def holds(self):\n        self.assertEqual(1 + 1, 2)\n"
)
CALC = "def add(a, b):\n    return a + b\n\n\ndef total(values):\n    return sum(values)\n"
CHECK_HEAD = (
    "import unittest\n\nfrom calc import add\n\n\n"
    "class Calc(unittest.TestCase):\n    def adds(self):\n"
)
LOCKED_CHECK = CHECK_HEAD + "        self.assertEqual(add(2, 3), 5)\n"
REPAIRED_CHECK = LOCKED_CHECK + "        self.assertEqual(add(-2, 2), 0)\n"
FAILING_CHECK = CHECK_HEAD + "        self.assertEqual(add(2, 3), 6)\n"
WEAKENED_CHECK = (
    "import unittest\n\n\nclass Calc(unittest.TestCase):\n    def adds(self):\n        self.assertTrue(True)\n"
)
OTHER_CHECK = (
    "import unittest\n\nfrom calc import total\n\n\n"
    "class Total(unittest.TestCase):\n    def sums(self):\n        self.assertEqual(total([1, 2]), 3)\n"
)
OTHER_REPAIRED = OTHER_CHECK + "        self.assertEqual(total([]), 0)\n"
CHARTER = "# Charter\n\n- Python 3.9 or later.\n- Locked: %s\n- Locked: %s\n" % (CHECK, OTHER)
STAMP = "2001-01-01T00:00:00+00:00"
STAMPING_CHECK = (
    "import json\nimport unittest\n\nfrom calc import add\n\n\n"
    "class Calc(unittest.TestCase):\n    def adds(self):\n"
    "        self.assertEqual(add(2, 3), 5)\n"
    "        with open(%r, encoding=\"utf-8\") as handle:\n"
    "            state = json.load(handle)\n"
    "        with open(%r, \"w\", encoding=\"utf-8\") as handle:\n"
    "            json.dump({**state, \"started\": %r}, handle)\n"
)


def last_line(completed):
    lines = completed.stdout.splitlines()
    return json.loads(lines[-1]) if lines else None


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


class Built:
    def __init__(self, case, charter_locked=False, guarded=False, sandboxed=False):
        self.case = case
        self.scratch = lock_fixtures.Scratch(
            {CHECK: LOCKED_CHECK, OTHER: OTHER_CHECK} if charter_locked else None
        )
        case.addCleanup(self.scratch.close)
        self.tree = os.path.join(self.scratch.run_dir, "trees", LABEL)
        self.state = os.path.join(self.scratch.run_dir, "state.json")
        self.journal = os.path.join(self.scratch.run_dir, "relock", "msp-0.json")
        guard = ((GUARD, GUARD_CHECK),) if guarded else ()
        builder = step(
            "build", ["calc.py"], LABEL, [PROPERTY, OTHER_PROPERTY, *((GUARD_PROPERTY,) if guarded else ())]
        )
        confined = ("--worker-sandbox", "--worker-write", self.scratch.stubs) if sandboxed else ()
        if charter_locked:
            self.flags = ("--charter", self.scratch.file("charter.md", CHARTER), *confined)
            self.items = self.scratch.items([builder])
            written = {"calc.py": CALC}
        else:
            self.flags = confined
            author = {
                **step("check-author", [CHECK, OTHER, *(path for path, _ in guard)], LABEL),
                "role": "check-author",
            }
            self.items = self.scratch.items(
                [
                    {key: value for key, value in author.items() if key != "msp"},
                    {**builder, "after": ["check-author"]},
                ]
            )
            written = {"calc.py": CALC, CHECK: LOCKED_CHECK, OTHER: OTHER_CHECK, **dict(guard)}
        self.scratch.jobs({LABEL: {"step": "build", "writes": written}})
        completed = self.scratch.build(self.items, *self.flags)
        case.assertEqual(completed.returncode, 0, "the build" + tail(completed))

    def tree_git(self, *args):
        completed = subprocess.run(
            ["git", "-C", self.tree, *args],
            env=self.scratch.env,
            capture_output=True,
            text=True,
            timeout=lock_fixtures.CALL_LIMIT,
        )
        self.case.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout.strip()

    def commit(self, files):
        for path, content in files.items():
            lock_fixtures.put(os.path.join(self.tree, path), content)
        self.tree_git("add", "-A")
        self.tree_git("commit", "-q", "-m", "fix: repair a locked check")
        return self.head()

    def head(self):
        return self.tree_git("rev-parse", "HEAD")

    def relock(self, *repairable, label=LABEL, extra=()):
        named = tuple(flag for path in repairable for flag in ("--repairable", path))
        return self.scratch.build(self.items, *self.flags, "--relock", label, *named, *extra)

    def ship(self):
        return self.scratch.build(
            self.items, *self.flags, "--resume", "--proven-head", "%s=%s" % (LABEL, self.head()), push=True
        )

    def ship_gating_again(self):
        return self.scratch.build(self.items, *self.flags, "--resume", push=True)

    def shipped(self, path):
        return self.scratch.checked("--git-dir", self.scratch.remote, "show", "%s:%s" % (BRANCH, path))

    def lock_record(self):
        journal = read_bytes(self.journal) if os.path.exists(self.journal) else None
        return read_bytes(self.state), journal

    def referee(self):
        with open(self.state, encoding="utf-8") as handle:
            return json.load(handle)["msps"]["0"].get("referee")

    def started(self):
        with open(self.state, encoding="utf-8") as handle:
            return json.load(handle)["started"]

    @contextlib.contextmanager
    def branch_held(self):
        name = hashlib.sha256(BRANCH.encode("utf-8")).hexdigest() + ".lock"
        folder = os.path.join(self.scratch.repo, ".git", run.BRANCH_HOLDS)
        os.makedirs(folder, exist_ok=True)
        handle = os.open(os.path.join(folder, name), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(handle)

    def dispatched(self):
        with open(os.path.join(self.scratch.stubs, "dispatch.log"), encoding="utf-8") as handle:
            return handle.read()


class RelockCase(unittest.TestCase):
    def _relocked(self, built, completed, paths=(CHECK,)):
        self.assertEqual(completed.returncode, 0, "the relock" + tail(completed))
        self.assertEqual(last_line(completed), {"relocked": list(paths), "refused": None}, tail(completed))

    def _refused(self, built, completed, before, *named):
        self.assertEqual(completed.returncode, 1, "the relock" + tail(completed))
        line = last_line(completed)
        self.assertEqual(line["relocked"], [], tail(completed))
        for part in named:
            self.assertIn(part, line["refused"])
        self.assertNotIn("\n", line["refused"])
        self.assertEqual(built.lock_record(), before)


class RelockFoldedChecks(RelockCase):
    def a_repaired_check_relocks_and_the_next_ship_holds_it(self):
        built = Built(self)
        head = built.commit({CHECK: REPAIRED_CHECK})
        self._relocked(built, built.relock(CHECK))
        self.assertEqual(
            built.referee(),
            {
                "locked": [CHECK, OTHER],
                "receipt_lock": referee.receipt_lock(((CHECK, REPAIRED_CHECK), (OTHER, OTHER_CHECK))),
                "commit": head,
            },
        )
        shipped = built.ship()
        self.assertEqual(shipped.returncode, 0, "the ship" + tail(shipped))
        self.assertEqual(built.shipped(CHECK), REPAIRED_CHECK)
        self.assertEqual(built.referee()["commit"], head)

    def a_guard_that_passes_at_the_base_as_locked_and_as_repaired_refuses_nothing(self):
        built = Built(self, guarded=True)
        built.commit({CHECK: REPAIRED_CHECK})
        self._relocked(built, built.relock(CHECK))

    def a_guard_repaired_into_a_failing_check_at_the_base_relocks(self):
        built = Built(self, guarded=True)
        built.commit({GUARD: "from calc import add\n" + GUARD_CHECK})
        self._relocked(built, built.relock(GUARD), (GUARD,))

    def a_locked_check_changed_before_a_later_gate_still_counts_as_changed(self):
        built = Built(self)
        built.commit({CHECK: REPAIRED_CHECK})
        shipped = built.ship_gating_again()
        self.assertEqual(shipped.returncode, 0, "the ship" + tail(shipped))
        before = built.lock_record()
        self._refused(built, built.relock(), before, CHECK, "not named")
        self._relocked(built, built.relock(CHECK))

    @unittest.skipUnless(sys.platform == "darwin", "the Worker sandbox is sandbox-exec, which only macOS has")
    def a_relock_under_the_worker_sandbox_proves_and_refuses_as_without_it(self):
        built = Built(self, sandboxed=True)
        built.commit({CHECK: REPAIRED_CHECK})
        self._relocked(built, built.relock(CHECK))
        before = built.lock_record()
        built.commit({CHECK: WEAKENED_CHECK})
        self._refused(built, built.relock(CHECK), before, "passes")

    def a_check_weakened_so_it_passes_at_the_base_is_refused(self):
        built = Built(self)
        before = built.lock_record()
        built.commit({CHECK: WEAKENED_CHECK})
        self._refused(built, built.relock(CHECK), before, "tests/test_calc.py::Calc.adds", "passes")

    def a_changed_locked_check_that_was_not_named_is_refused(self):
        built = Built(self)
        before = built.lock_record()
        built.commit({CHECK: REPAIRED_CHECK, OTHER: OTHER_REPAIRED})
        self._refused(built, built.relock(CHECK), before, OTHER, "not named")

    def a_refusal_after_a_relock_leaves_the_lock_record_byte_for_byte_as_it_was(self):
        built = Built(self)
        built.commit({CHECK: REPAIRED_CHECK})
        self._relocked(built, built.relock(CHECK))
        before = built.lock_record()
        self.assertIsNotNone(before[1])
        built.commit({CHECK: WEAKENED_CHECK})
        self._refused(built, built.relock(CHECK), before, "passes")

    def a_repaired_check_that_fails_against_the_work_is_refused(self):
        built = Built(self)
        before = built.lock_record()
        built.commit({CHECK: FAILING_CHECK})
        self._refused(built, built.relock(CHECK), before, "tests/test_calc.py::Calc.adds", "fails")

    def a_removed_locked_check_is_refused(self):
        built = Built(self)
        before = built.lock_record()
        built.tree_git("rm", "-q", OTHER)
        built.tree_git("commit", "-q", "-m", "fix: drop a locked check")
        self._refused(built, built.relock(OTHER), before, OTHER, "removed")

    def a_run_whose_locked_checks_did_not_change_relocks_nothing(self):
        built = Built(self)
        before = built.lock_record()
        built.commit({"calc.py": CALC + "\n"})
        self._relocked(built, built.relock(CHECK), ())
        self.assertEqual(built.lock_record(), before)

    def a_relock_dispatches_no_worker_pushes_nothing_and_moves_no_branch(self):
        built = Built(self)
        head = built.commit({CHECK: REPAIRED_CHECK})
        dispatched = built.dispatched()
        feature = built.scratch.checked("rev-parse", "refs/heads/feature")
        self._relocked(built, built.relock(CHECK))
        self.assertEqual(built.dispatched(), dispatched)
        self.assertEqual(built.scratch.checked("rev-parse", "refs/heads/" + BRANCH).strip(), head)
        self.assertEqual(built.scratch.checked("rev-parse", "refs/heads/feature"), feature)
        self.assertEqual(built.scratch.checked("--git-dir", built.scratch.remote, "for-each-ref"), "")
        self.assertEqual(built.tree_git("status", "--porcelain"), "")

    def a_state_change_made_during_the_proof_survives_the_relock(self):
        built = Built(self)
        self.assertNotEqual(built.started(), STAMP)
        head = built.commit({CHECK: STAMPING_CHECK % (built.state, built.state, STAMP)})
        self._relocked(built, built.relock(CHECK))
        self.assertEqual(built.started(), STAMP)
        self.assertEqual(built.referee()["commit"], head)

    def a_second_relock_of_a_proved_repair_relocks_nothing(self):
        built = Built(self)
        built.commit({CHECK: REPAIRED_CHECK})
        self._relocked(built, built.relock(CHECK))
        before = built.lock_record()
        self._relocked(built, built.relock(), ())
        self.assertEqual(built.lock_record(), before)

    def a_repair_reverted_after_its_relock_counts_as_unchanged(self):
        built = Built(self)
        built.commit({CHECK: REPAIRED_CHECK})
        self._relocked(built, built.relock(CHECK))
        built.tree_git("revert", "--no-edit", "HEAD")
        built.commit({OTHER: OTHER_REPAIRED})
        self._relocked(built, built.relock(OTHER), (OTHER,))


class RelockCharterLockedChecks(RelockCase):
    def a_repaired_charter_locked_check_ships_as_repaired(self):
        built = Built(self, charter_locked=True)
        built.commit({CHECK: REPAIRED_CHECK})
        self._relocked(built, built.relock(CHECK))
        shipped = built.ship()
        self.assertEqual(shipped.returncode, 0, "the ship" + tail(shipped))
        self.assertEqual(built.shipped(CHECK), REPAIRED_CHECK)

    def a_charter_locked_check_changed_after_its_relock_is_restored_to_the_repair(self):
        built = Built(self, charter_locked=True)
        built.commit({CHECK: REPAIRED_CHECK})
        self._relocked(built, built.relock(CHECK))
        built.commit({CHECK: WEAKENED_CHECK})
        shipped = built.ship()
        self.assertEqual(shipped.returncode, 9, "the ship" + tail(shipped))
        self.assertEqual(built.scratch.checked("show", "%s:%s" % (BRANCH, CHECK)), REPAIRED_CHECK)

    def a_charter_locked_check_changed_after_its_relock_was_dropped_is_restored_to_the_charter(self):
        built = Built(self, charter_locked=True)
        built.commit({CHECK: REPAIRED_CHECK})
        self._relocked(built, built.relock(CHECK))
        built.tree_git("reset", "-q", "--hard", "HEAD~1")
        built.commit({CHECK: WEAKENED_CHECK})
        shipped = built.ship()
        self.assertEqual(shipped.returncode, 9, "the ship" + tail(shipped))
        self.assertEqual(built.scratch.checked("show", "%s:%s" % (BRANCH, CHECK)), LOCKED_CHECK)

    def a_charter_repair_reverted_after_its_relock_ships_as_the_charter_locked_it(self):
        built = Built(self, charter_locked=True)
        built.commit({CHECK: REPAIRED_CHECK})
        self._relocked(built, built.relock(CHECK))
        built.tree_git("revert", "--no-edit", "HEAD")
        shipped = built.ship()
        self.assertEqual(shipped.returncode, 0, "the ship" + tail(shipped))
        self.assertEqual(built.shipped(CHECK), LOCKED_CHECK)


class Staged:
    def __init__(self, case):
        self.case = case
        self.scratch = stage.Scratch()
        case.addCleanup(self.scratch.close)
        self.scratch.jobs(stage.standard_jobs())
        self.items = self.scratch.items(stage.labelled())
        self.flags = ("--plan-file", self.scratch.saved(self.items), *stage.LOCK_TIMEOUT)
        self.tree = os.path.join(self.scratch.run_dir, "trees", "alpha")
        self.state = os.path.join(self.scratch.run_dir, "state.json")
        self.journal = os.path.join(self.scratch.run_dir, "relock", "msp-0.json")
        completed = self.scratch.build(self.items, *self.flags, "--no-push")
        case.assertEqual(completed.returncode, 0, "the build" + tail(completed))

    def commit(self, content):
        stage.put(os.path.join(self.tree, stage.TEST_A), content)
        self.scratch.checked("add", "-A", cwd=self.tree)
        self.scratch.checked("commit", "-q", "-m", "fix: repair a locked check", cwd=self.tree)
        return self.scratch.checked("rev-parse", "HEAD", cwd=self.tree).strip()

    def relock(self, *repairable):
        named = tuple(flag for path in repairable for flag in ("--repairable", path))
        return self.scratch.build(self.items, *self.flags, "--no-push", "--relock", "alpha", *named)

    def ship(self, head):
        return self.scratch.pushing(self.items, *self.flags, "--resume", "--proven-head", "alpha=" + head)

    def lock_record(self):
        journal = read_bytes(self.journal) if os.path.exists(self.journal) else None
        return read_bytes(self.state), journal


STAGE_REPAIRED = stage.checking(stage.TEST_A) + "        self.assertNotEqual(a.value(), 1)\n"


class RelockLockStageChecks(RelockCase):
    def a_repaired_lock_stage_check_ships_as_repaired(self):
        built = Staged(self)
        head = built.commit(STAGE_REPAIRED)
        self._relocked(built, built.relock(stage.TEST_A), (stage.TEST_A,))
        shipped = built.ship(head)
        self.assertEqual(shipped.returncode, 0, "the ship" + tail(shipped))
        self.assertEqual(
            built.scratch.remote_checked("show", "mitosis/alpha:" + stage.TEST_A), STAGE_REPAIRED
        )
        self.assertEqual(built.scratch.state()["msps"]["0"]["referee"]["commit"], head)

    def a_lock_stage_check_changed_without_a_relock_ships_as_the_lock_stage_locked_it(self):
        built = Staged(self)
        head = built.commit(STAGE_REPAIRED)
        shipped = built.ship(head)
        self.assertEqual(shipped.returncode, 9, "the ship" + tail(shipped))
        self.assertEqual(
            built.scratch.checked("show", "mitosis/alpha:" + stage.TEST_A), stage.checking(stage.TEST_A)
        )

    def a_lock_stage_check_weakened_so_it_passes_at_the_base_is_refused(self):
        built = Staged(self)
        before = built.lock_record()
        built.commit(stage.passing(stage.TEST_A))
        self._refused(built, built.relock(stage.TEST_A), before, "tests/test_a.py::A.returns_two", "passes")


class RelockUsage(RelockCase):
    def _usage_error(self, completed):
        self.assertEqual(completed.returncode, 2, tail(completed))
        self.assertEqual(completed.stdout, "")

    def repairable_without_relock_is_a_usage_error(self):
        built = Built(self)
        before = built.lock_record()
        self._usage_error(built.scratch.build(built.items, "--repairable", CHECK))
        self.assertEqual(built.lock_record(), before)

    def relock_beside_a_flag_that_builds_plans_or_ships_is_a_usage_error(self):
        built = Built(self)
        built.commit({CHECK: REPAIRED_CHECK})
        before = built.lock_record()
        for extra in (
            ("--resume",),
            ("--proven-head", "%s=%s" % (LABEL, built.head())),
            ("--plan-only",),
            ("--dry-run",),
            ("--revise",),
            ("--no-run-dir",),
        ):
            with self.subTest(flag=extra[0]):
                self._usage_error(built.relock(CHECK, extra=extra))
                self.assertEqual(built.lock_record(), before)

    def a_repairable_path_outside_the_repository_is_a_usage_error(self):
        built = Built(self)
        for path in ("/etc/hosts", "../outside.py", "tests/../../outside.py"):
            with self.subTest(path=path):
                self._usage_error(built.relock(path))

    def relock_of_an_msp_the_run_does_not_hold_is_a_usage_error(self):
        built = Built(self)
        built.commit({CHECK: REPAIRED_CHECK})
        before = built.lock_record()
        completed = built.relock(CHECK, label="absent")
        self._usage_error(completed)
        self.assertIn("names MSP absent, which the run", completed.stderr)
        self.assertEqual(built.lock_record(), before)

    def a_worktree_off_its_branch_is_unusable(self):
        built = Built(self)
        built.commit({CHECK: REPAIRED_CHECK})
        built.tree_git("checkout", "-q", "--detach", "HEAD~1")
        before = built.lock_record()
        completed = built.relock(CHECK)
        self._usage_error(completed)
        self.assertIn("is not on " + BRANCH, completed.stderr)
        self.assertEqual(built.lock_record(), before)

    def a_run_dir_holding_the_state_of_another_plan_is_unusable(self):
        built = Built(self)
        built.commit({CHECK: REPAIRED_CHECK})
        plan = os.path.join(built.scratch.run_dir, "plan.json")
        with open(plan, encoding="utf-8") as handle:
            held = json.load(handle)
        lock_fixtures.put(plan, json.dumps({**held, "plan_id": "000000000000"}))
        before = built.lock_record()
        completed = built.relock(CHECK)
        self._usage_error(completed)
        self.assertIn("beside another plan", completed.stderr)
        self.assertEqual(built.lock_record(), before)

    def a_relock_beside_a_running_mitosis_on_the_branch_is_unusable(self):
        built = Built(self)
        built.commit({CHECK: REPAIRED_CHECK})
        before = built.lock_record()
        with built.branch_held():
            completed = built.relock(CHECK)
        self._usage_error(completed)
        self.assertIn("in use", completed.stderr)
        self.assertEqual(built.lock_record(), before)

    def a_plan_mitosis_did_not_write_is_unusable_and_never_reads_as_a_refusal(self):
        built = Built(self)
        built.commit({CHECK: REPAIRED_CHECK})
        plan = os.path.join(built.scratch.run_dir, "plan.json")
        with open(plan, encoding="utf-8") as handle:
            held = json.load(handle)
        lock_fixtures.put(plan, json.dumps({**held, "msps": 5}))
        before = built.lock_record()
        self._usage_error(built.relock(CHECK))
        self.assertEqual(built.lock_record(), before)

    def a_relock_journal_mitosis_did_not_write_is_unusable(self):
        built = Built(self)
        built.commit({CHECK: REPAIRED_CHECK})
        lock_fixtures.put(built.journal, json.dumps({"relocked": ["not-a-commit"]}))
        before = built.lock_record()
        self._usage_error(built.relock(CHECK))
        self.assertEqual(built.lock_record(), before)

    def items_that_lost_the_folded_check_author_are_unusable(self):
        built = Built(self)
        built.commit({CHECK: REPAIRED_CHECK})
        before = built.lock_record()
        builder = step("build", ["calc.py"], LABEL, [PROPERTY, OTHER_PROPERTY])
        completed = built.scratch.build(
            built.scratch.items([builder], "other-items.json"), "--relock", LABEL, "--repairable", CHECK
        )
        self._usage_error(completed)
        self.assertIn("check-author", completed.stderr)
        self.assertEqual(built.lock_record(), before)

    def relock_outside_the_repository_is_unusable(self):
        built = Built(self)
        built.commit({CHECK: REPAIRED_CHECK})
        before = built.lock_record()
        completed = subprocess.run(
            [
                sys.executable,
                lock_fixtures.MITOSIS,
                "--items",
                built.items,
                "--run-dir",
                built.scratch.run_dir,
                "--acceptance-command",
                built.scratch.stub("accept.py", "unittest", "{file}", "{test}"),
                "--relock",
                LABEL,
                "--repairable",
                CHECK,
            ],
            cwd=built.scratch.tmp,
            env=built.scratch.env,
            capture_output=True,
            text=True,
            timeout=lock_fixtures.RUN_LIMIT,
        )
        self._usage_error(completed)
        self.assertIn("inside the repository", completed.stderr)
        self.assertEqual(built.lock_record(), before)

    def a_branch_reset_behind_the_commit_its_lanes_landed_is_unusable(self):
        built = Built(self)
        built.tree_git("reset", "-q", "--hard", "HEAD~1")
        before = built.lock_record()
        completed = built.relock(CHECK)
        self._usage_error(completed)
        self.assertIn("Lanes landed", completed.stderr)
        self.assertEqual(built.lock_record(), before)

    def relock_in_a_run_dir_that_holds_no_built_run_is_unusable(self):
        scratch = lock_fixtures.Scratch()
        self.addCleanup(scratch.close)
        items = scratch.items([step("build", ["calc.py"], LABEL)])
        self._usage_error(scratch.build(items, "--relock", LABEL))
        self.assertFalse(os.path.exists(os.path.join(scratch.run_dir, "state.json")))


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            RelockFoldedChecks,
            (
                "a_repaired_check_relocks_and_the_next_ship_holds_it",
                "a_guard_that_passes_at_the_base_as_locked_and_as_repaired_refuses_nothing",
                "a_guard_repaired_into_a_failing_check_at_the_base_relocks",
                "a_locked_check_changed_before_a_later_gate_still_counts_as_changed",
                "a_relock_under_the_worker_sandbox_proves_and_refuses_as_without_it",
                "a_check_weakened_so_it_passes_at_the_base_is_refused",
                "a_changed_locked_check_that_was_not_named_is_refused",
                "a_refusal_after_a_relock_leaves_the_lock_record_byte_for_byte_as_it_was",
                "a_repaired_check_that_fails_against_the_work_is_refused",
                "a_removed_locked_check_is_refused",
                "a_run_whose_locked_checks_did_not_change_relocks_nothing",
                "a_relock_dispatches_no_worker_pushes_nothing_and_moves_no_branch",
                "a_state_change_made_during_the_proof_survives_the_relock",
                "a_second_relock_of_a_proved_repair_relocks_nothing",
                "a_repair_reverted_after_its_relock_counts_as_unchanged",
            ),
        ),
        (
            RelockCharterLockedChecks,
            (
                "a_repaired_charter_locked_check_ships_as_repaired",
                "a_charter_locked_check_changed_after_its_relock_is_restored_to_the_repair",
                "a_charter_locked_check_changed_after_its_relock_was_dropped_is_restored_to_the_charter",
                "a_charter_repair_reverted_after_its_relock_ships_as_the_charter_locked_it",
            ),
        ),
        (
            RelockLockStageChecks,
            (
                "a_repaired_lock_stage_check_ships_as_repaired",
                "a_lock_stage_check_changed_without_a_relock_ships_as_the_lock_stage_locked_it",
                "a_lock_stage_check_weakened_so_it_passes_at_the_base_is_refused",
            ),
        ),
        (
            RelockUsage,
            (
                "repairable_without_relock_is_a_usage_error",
                "relock_beside_a_flag_that_builds_plans_or_ships_is_a_usage_error",
                "a_repairable_path_outside_the_repository_is_a_usage_error",
                "relock_of_an_msp_the_run_does_not_hold_is_a_usage_error",
                "a_worktree_off_its_branch_is_unusable",
                "a_run_dir_holding_the_state_of_another_plan_is_unusable",
                "a_relock_beside_a_running_mitosis_on_the_branch_is_unusable",
                "a_plan_mitosis_did_not_write_is_unusable_and_never_reads_as_a_refusal",
                "a_relock_journal_mitosis_did_not_write_is_unusable",
                "items_that_lost_the_folded_check_author_are_unusable",
                "relock_outside_the_repository_is_unusable",
                "a_branch_reset_behind_the_commit_its_lanes_landed_is_unusable",
                "relock_in_a_run_dir_that_holds_no_built_run_is_unusable",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
