import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_accept_execution as accept
from test_accept_execution import S, check_author, tail


def store(scratch, *entries):
    return accept.put(
        scratch.path("store.jsonl"), "".join(json.dumps(entry) + "\n" for entry in entries)
    )


def trailing_array(line):
    decoder = json.JSONDecoder()
    for position in [index for index, char in enumerate(line) if char == "["]:
        try:
            value, end = decoder.raw_decode(line, position)
        except ValueError:
            continue
        if end == len(line.rstrip()) and isinstance(value, list):
            return value
    return None


def checked_steps():
    return [
        S("a", ["a.py"], msp="a", acceptance=[{"file": "tests/test_a.py", "test": "a_works"}]),
        check_author("check-author-a", ["tests/test_a.py"], "a"),
    ]


class TierNotes(accept.Case):
    def a_history_bump_is_explained(self):
        history = store(
            self.scratch,
            {"files": ["a.py"], "outcome": "reverted"},
            {"files": ["a.py"], "outcome": "speculative"},
        )
        completed, plan = self.planned(
            [S("a", ["a.py"], complexity="simple")], "--trajectory", history
        )
        self.assertEqual(plan.get("tier_notes"), {"a": "history: 1 reverted, 1 speculative"}, tail(completed))


class VerdictGroups(accept.Case):
    def pairs_with_one_signal_set_share_a_verdict(self):
        completed, plan = self.planned(
            [S("a", ["auth/a.py"]), S("b", ["auth/b.py"]), S("c", ["auth/c.py"])], "--risk-markers", "auth"
        )
        self.assertEqual(
            plan.get("verdict_groups"),
            [
                {
                    "signals": ["shared-risk-marker:auth"],
                    "default": "serialize",
                    "count": 3,
                    "pairs": [["a", "b"], ["a", "c"], ["b", "c"]],
                }
            ],
            tail(completed),
        )


class SharedContext(accept.Case):
    def a_neighbour_two_steps_read_is_shared(self):
        graph = accept.put_json(
            self.scratch.path("graph.json"), accept.node_link([("a.py", "hub.py"), ("c.py", "hub.py")])
        )
        completed, plan = self.planned([S("a", ["a.py"]), S("c", ["c.py"])], "--graph", graph)
        self.assertEqual(plan.get("shared_context"), {"0": ["hub.py"]}, tail(completed))


class PromptTemplate(accept.Case):
    def a_build_lane_gets_the_callers_template(self):
        completed = self.scratch.full_run(
            [S("a", ["a.py"], complexity="simple")],
            "--prompt-template",
            "CUSTOM task-a <{cluster}> <{tier}> {spec}",
        )
        self.exits(completed, 0)
        record = self.lane("a", completed)
        self.assertEqual(record.get("state"), "ok", tail(completed))
        printed = self.stderr_of(record, completed)
        self.assertTrue(printed.startswith("Lane 0 of MSP a\nCUSTOM task-a <a> <cheap> {"), printed[:2000])
        self.assertIn('"edit": [', printed)

    def a_check_author_lane_keeps_its_brief(self):
        completed = self.scratch.full_run(checked_steps(), "--prompt-template", "CUSTOM task-a {spec}")
        plan = self.plan(completed)
        authors = plan.get("check_authors") or []
        self.assertTrue(authors, "plan.json holds no check_authors" + tail(completed))
        text = (authors[0].get("brief") or {}).get("text")
        self.assertIsInstance(text, str, tail(completed))
        lock = ((self.state(completed).get("msps") or {}).get("0") or {}).get("lock")
        self.assertIsInstance(lock, dict, "msps[0] carries no lock record" + tail(completed))
        printed = self.stderr_of(lock, completed)
        self.assertTrue(printed.startswith(text), printed[:2000])
        self.assertNotIn("CUSTOM", printed)


class WorkerEnvironment(accept.Case):
    def a_worker_sees_the_plan_variables(self):
        completed = self.scratch.full_run([S("a", ["a.py", "b.py"], complexity="simple")])
        state = self.state(completed)
        lines = self.stderr_of(self.lane("a", completed), completed).splitlines()
        branch = ((state.get("worktrees") or [{}])[0]).get("branch")
        for line in (
            "MITOSIS_ITEM=a",
            "MITOSIS_ITEMS=a",
            "MITOSIS_CLUSTER=a",
            "MITOSIS_TIER=cheap",
            "MITOSIS_FILES=a.py,b.py",
            "MITOSIS_PLAN_ID=%s" % state.get("plan_id"),
            "MITOSIS_BRANCH=%s" % branch,
        ):
            self.assertIn(line, lines, tail(completed))


class OutputTail(accept.Case):
    def control(self):
        return {"a": {"stdout": "x" * 5000}, "reask_stdout": "y" * 5000 + "END"}

    def an_unreadable_reply_keeps_its_tail(self):
        completed = self.scratch.full_run([S("a", ["a.py"], complexity="simple")], control=self.control())
        record = self.lane("a", completed)
        self.assertEqual(record.get("state"), "failed", tail(completed))
        self.assertEqual(record.get("output_tail"), ("y" * 5000 + "END")[-4000:], tail(completed))

    def the_tail_length_is_a_flag(self):
        completed = self.scratch.full_run(
            [S("a", ["a.py"], complexity="simple")], "--max-output-bytes", "10", control=self.control()
        )
        record = self.lane("a", completed)
        self.assertEqual(record.get("output_tail"), "yyyyyyyEND", tail(completed))


class DryRun(accept.Case):
    def a_dry_run_prints_argv_and_builds_nothing(self):
        completed = self.scratch.full_run(
            [S("a", ["a.py"], complexity="simple"), S("b", ["b.py"], complexity="simple", after=["a"])],
            "--dry-run",
        )
        self.exits(completed, 0)
        lines = [line for line in completed.stdout.splitlines() if line.startswith("dry run: Lane ")]
        self.assertEqual(len(lines), 2, tail(completed))
        argvs = [trailing_array(line) for line in lines]
        self.assertTrue(all(isinstance(argv, list) and argv for argv in argvs), lines)
        self.assertEqual([argv[0] for argv in argvs], [sys.executable, sys.executable], lines)
        self.assertIn("task-a", " ".join(str(word) for word in argvs[0]), lines[0])
        self.assertFalse(os.path.exists(self.scratch.path("started-a")))
        self.assertFalse(os.path.exists(self.scratch.path("started-b")))
        self.assertEqual(self.scratch.checked("branch", "--list", "mitosis/*"), "")
        self.assertFalse(os.path.exists(os.path.join(self.scratch.run_dir, "state.json")))


class PlanFile(accept.Case):
    def saved(self, source, name, change):
        plan = accept.read_json(os.path.join(source, "plan.json"))
        return accept.put_json(self.scratch.path(name), change(plan)), plan

    def a_saved_plan_is_built_as_written(self):
        steps = [S("a", ["a.py"], complexity="simple")]
        first = self.scratch.path("r1")
        self.planned(steps, run_dir=first)

        def edited(plan):
            briefs = plan["briefs"]
            return {**plan, "briefs": [{**briefs[0], "text": "Lane 0 of MSP a\nEDITED BRIEF task-a"}] + briefs[1:]}

        saved, original = self.saved(first, "saved.json", edited)
        second = self.scratch.path("r2")
        completed = self.scratch.full_run(steps, "--plan-file", saved, run_dir=second)
        self.exits(completed, 0)
        printed = self.stderr_of(self.lane("a", completed, second), completed)
        self.assertTrue(printed.startswith("Lane 0 of MSP a\nEDITED BRIEF task-a"), printed[:2000])
        self.assertEqual(self.state(completed, second).get("plan_id"), original.get("plan_id"))

    def a_saved_plan_gets_back_its_check_author_locks(self):
        first = self.scratch.path("r1")
        self.planned(checked_steps(), run_dir=first)
        saved, original = self.saved(first, "saved.json", lambda plan: {**plan, "locked": []})
        second = self.scratch.path("r2")
        completed, plan = self.planned(checked_steps(), "--plan-file", saved, run_dir=second)
        self.assertEqual(plan.get("locked"), ["tests/test_a.py"], tail(completed))
        self.assertEqual(plan.get("plan_id"), original.get("plan_id"), tail(completed))

    def a_saved_plan_without_its_check_authors_is_refused(self):
        first = self.scratch.path("r1")
        self.planned(checked_steps(), run_dir=first)
        saved, _ = self.saved(
            first, "saved.json", lambda plan: {k: v for k, v in plan.items() if k != "check_authors"}
        )
        second = self.scratch.path("r2")
        completed = self.scratch.plan_only(checked_steps(), "--plan-file", saved, run_dir=second)
        self.exits(completed, 3)
        self.assertIn("has no check_authors", completed.stderr)
        self.assertIn("check-author-a", completed.stderr)
        self.assertFalse(os.path.exists(os.path.join(second, "plan.json")))

    def a_saved_plan_lacking_a_part_is_refused_by_name(self):
        first = self.scratch.path("r1")
        self.planned([S("a", ["a.py"])], run_dir=first)
        for number, key in enumerate(("msps", "lanes", "briefs"), start=1):
            saved, _ = self.saved(
                first, "saved-%d.json" % number, lambda plan: {k: v for k, v in plan.items() if k != key}
            )
            target = self.scratch.path("r-%d" % number)
            completed = self.scratch.plan_only([S("a", ["a.py"])], "--plan-file", saved, run_dir=target)
            self.exits(completed, 3)
            self.assertIn("lacks %s" % key, completed.stderr)
            self.assertFalse(os.path.exists(os.path.join(target, "plan.json")))


def load_tests(loader, standard, pattern):
    helpers = ("control", "saved")

    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            return sorted(
                name
                for name in vars(case)
                if not name.startswith("_") and callable(getattr(case, name)) and name not in helpers
            )

    suite = unittest.TestSuite()
    for case in (
        TierNotes,
        VerdictGroups,
        SharedContext,
        PromptTemplate,
        WorkerEnvironment,
        OutputTail,
        DryRun,
        PlanFile,
    ):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite
