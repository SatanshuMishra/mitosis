import json
import os
import shutil
import signal
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_accept_any_input as any_input
import test_accept_execution as accept
import test_accept_parity_options as options
from test_accept_execution import S, tail

MITOSIS = os.path.join(os.path.dirname(HERE), "mitosis.py")
SHARED = "reg/types.py"
WORKER_JOBS_LINE = 'jobs = load("worker.json")\n'
STARTS_A_SECOND_RUN = '''
import subprocess as _nested
_settings = load("nested.json")
_done = _nested.run(
    _settings["argv"], cwd=_settings["cwd"], env=_settings["env"],
    stdin=_nested.DEVNULL, capture_output=True, text=True,
)
with open(_settings["out"], "w", encoding="utf-8") as _handle:
    json.dump({"code": _done.returncode, "output": _done.stdout + _done.stderr}, _handle)
jobs = load("worker.json")
'''
TERMINATES_ITS_RUN = 'jobs = load("worker.json")\nimport signal\nos.kill(os.getppid(), signal.SIGTERM)\n'


class GuardCase(options.OptionsCase):
    def _worker_doing(self, name, replacement):
        with open(os.path.join(self.scratch.stubs, "worker.py"), encoding="utf-8") as handle:
            source = handle.read()
        accept.put(os.path.join(self.scratch.stubs, name), source.replace(WORKER_JOBS_LINE, replacement, 1))

    def _flags(self, worker, items, run_dir, *flags):
        return (
            "--items",
            items,
            "--run-dir",
            run_dir,
            "--feature-branch",
            "feature",
            "--dispatch-command",
            self.scratch.stub(worker, "{task}", "{session_id}", "{schema}"),
            "--reask-command",
            self.scratch.stub("reask.py", "{prompt}", "{session_id}", "{schema}"),
            "--acceptance-command",
            self.scratch.stub("accept.py", "{file}", "{test}"),
            "--no-push",
            *flags,
        )

    def _jobs(self, jobs):
        accept.put_json(os.path.join(self.scratch.stubs, "worker.json"), jobs)
        accept.put_json(os.path.join(self.scratch.stubs, "control.json"), {})


class LiveRuns(GuardCase):
    def _second_run_during_the_first(self, *flags):
        items = self.scratch.items([S("a", ["a.py"])])
        self._jobs({"a": {"a.py": "first\n"}})
        self._worker_doing("outer.py", STARTS_A_SECOND_RUN)
        out = self.scratch.path("nested-result.json")
        accept.put_json(
            os.path.join(self.scratch.stubs, "nested.json"),
            {
                "argv": [
                    sys.executable,
                    MITOSIS,
                    *self._flags("worker.py", items, self.scratch.path("run-two"), *flags),
                ],
                "cwd": self.scratch.repo,
                "env": self.scratch.env,
                "out": out,
            },
        )
        first = self.scratch.mitosis(*self._flags("outer.py", items, self.scratch.path("run-one"), *flags))
        self.exits(first, 0)
        with open(out, encoding="utf-8") as handle:
            return first, json.load(handle)

    def a_second_live_run_under_one_root_is_refused(self):
        first, second = self._second_run_during_the_first("--worktree-root", self.scratch.path("trees"))
        self.assertEqual(second["code"], 5, second["output"] + tail(first))
        self.assertIn("another running mitosis", second["output"])

    def a_second_live_run_with_no_run_dir_is_refused(self):
        first, second = self._second_run_during_the_first("--no-run-dir")
        self.assertEqual(second["code"], 5, second["output"] + tail(first))
        self.assertIn("another running mitosis", second["output"])


class RetriedTrees(GuardCase):
    def _stopped(self, steps, *flags):
        stopped = self._build(steps, *flags, control={"a": {"status": "failed"}})
        self.assertNotEqual(stopped.returncode, 0, tail(stopped))
        return stopped

    def _built_file(self):
        return self.scratch.git("show", "refs/heads/mitosis/a:a.py").stdout

    def a_retry_takes_the_branch_from_a_stopped_tree_under_another_root(self):
        steps = [S("a", ["a.py"])]
        self._stopped(steps, "--worktree-root", self.scratch.path("root-one"))
        retried = self._build(
            steps, "--worktree-root", self.scratch.path("root-two"), "--run-dir", self.scratch.path("run-two")
        )
        self.exits(retried, 0)
        self.assertEqual(self._built_file(), "work of a\n", tail(retried))

    def a_retry_takes_the_branch_from_a_stopped_run_that_kept_no_folder(self):
        steps = [S("a", ["a.py"])]
        self._stopped(steps, "--no-run-dir")
        retried = self._build(steps, "--run-dir", self.scratch.path("run-two"))
        self.exits(retried, 0)
        self.assertEqual(self._built_file(), "work of a\n", tail(retried))

    def a_tree_that_built_other_files_keeps_its_branch(self):
        self._stopped([S("a", ["a.py"])], "--worktree-root", self.scratch.path("root-one"))
        other = self._build(
            [S("a", ["other.py"])],
            "--worktree-root",
            self.scratch.path("root-two"),
            "--run-dir",
            self.scratch.path("run-two"),
        )
        self.exits(other, 5)
        self.assertIn("already used by worktree", other.stdout + other.stderr)


class SavedPlans(GuardCase):
    def a_saved_plans_shared_files_reach_the_lock_stage(self):
        built = any_input.reply(
            any_input.item("a", [SHARED, "a.py"], "a works", tests=[{"file": "tests/test_a.py", "test": "A.works"}]),
            any_input.item("b", [SHARED, "b.py"], "b works", tests=[{"file": "tests/test_b.py", "test": "B.works"}]),
        )
        first = self._asks_run("add a and b\n", {"add a and b": built}, "--shared-files", SHARED)
        self.exits(first, 0)
        saved = self.scratch.path("saved.json")
        shutil.copyfile(os.path.join(self.scratch.run_dir, "plan.json"), saved)
        again = self._asks_run(
            "add a and b\n", {"add a and b": built}, "--plan-file", saved, run_dir=self.scratch.path("run-two")
        )
        self.exits(again, 0)


class IntegrationRank(GuardCase):
    def every_msp_with_no_producer_is_integrated_before_any_consumer(self):
        completed, plan = self.planned(
            [
                S("a", ["s.py", "a.py"]),
                S("c", ["s.py", "c.py"]),
                S("b", ["s.py", "b1.py", "b2.py", "b3.py", "b4.py", "b5.py", "b6.py"], after=["a"]),
            ],
            "--shared-files",
            "s.py",
        )
        self.assertEqual(plan.get("integration_order"), {"s.py": [0, 1, 2]}, tail(completed))


class PrivateRuns(GuardCase):
    def a_lane_names_no_output_file_that_is_gone(self):
        completed = self.scratch.full_run([S("a", ["a.py"])], "--no-run-dir", env={"TMPDIR": self._empty_tmp()})
        self.exits(completed, 0)
        lane = accept.summary_of(completed)["lanes"][0]
        self.assertIsNone(lane["stdout"], tail(completed))
        self.assertIsNone(lane["stderr"], tail(completed))

    def a_kept_run_still_names_its_lane_outputs(self):
        completed = self.scratch.full_run([S("a", ["a.py"])])
        self.exits(completed, 0)
        lane = accept.summary_of(completed)["lanes"][0]
        self.assertTrue(os.path.isfile(lane["stdout"]), tail(completed))

    def a_terminated_run_leaves_no_private_folder(self):
        private = self._empty_tmp()
        items = self.scratch.items([S("a", ["a.py"])])
        self._jobs({"a": {"a.py": "first\n"}})
        self._worker_doing("terminates.py", TERMINATES_ITS_RUN)
        completed = self.scratch.mitosis(
            *self._flags("terminates.py", items, self.scratch.path("unused"), "--no-run-dir"),
            env={"TMPDIR": private},
        )
        self.assertEqual(completed.returncode, -signal.SIGTERM, tail(completed))
        self._left_in(private, completed)


def load_tests(loader, standard, pattern):
    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            return sorted(
                name
                for name in vars(case)
                if not name.startswith("_") and callable(getattr(case, name))
            )

    suite = unittest.TestSuite()
    for case in (LiveRuns, RetriedTrees, SavedPlans, IntegrationRank, PrivateRuns):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite
