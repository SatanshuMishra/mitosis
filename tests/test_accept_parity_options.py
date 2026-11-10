import json
import os
import re
import shutil
import stat
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_accept_execution as accept
import test_accept_any_input as any_input
from test_accept_execution import S, tail

SHARED = "reg/types.py"
NO_RUN_DIR = "run directory: none"

READ_ONLY_WORKER = r'''
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
tmp = os.path.dirname(here)
with open(os.path.join(tmp, "ran-read-only"), "w", encoding="utf-8") as handle:
    handle.write("ran\n")
os.makedirs("ro", exist_ok=True)
with open(os.path.join("ro", "held.txt"), "w", encoding="utf-8") as handle:
    handle.write("held\n")
os.chmod("ro", 0o500)
sys.exit(1)
'''


def steps_of(plan):
    return [entry.get("steps") for entry in plan.get("msps") or []]


def lane_shape(plan):
    return [(lane.get("msp"), lane.get("steps"), lane.get("session")) for lane in plan.get("lanes") or []]


def two_editors(first, second):
    return [S("a", [first, "a.py"]), S("b", [second, "b.py"])]


def standalone(word):
    return r"(?<![\w.-])%s(?![\w-]|\.\w)" % re.escape(word)


def ordered_mention(text, first, second):
    return re.search(standalone(first) + r".*" + standalone(second), text) is not None


def opened(folder):
    for top, names, _ in os.walk(folder):
        for name in names:
            path = os.path.join(top, name)
            if not os.path.islink(path):
                os.chmod(path, stat.S_IMODE(os.lstat(path).st_mode) | stat.S_IRWXU)


class OptionsCase(any_input.AsksCase):
    def _empty_tmp(self):
        folder = self.scratch.path("private-tmp")
        os.makedirs(folder)
        self.addCleanup(opened, folder)
        return folder

    def _left_in(self, folder, completed):
        opened(folder)
        self.assertEqual(sorted(os.listdir(folder)), [], tail(completed))

    def _reconcile(self, completed, msp="0"):
        record = ((self.state(completed).get("msps") or {}).get(msp) or {}).get("reconcile")
        self.assertIsInstance(record, dict, "state.json holds no reconcile of MSP %s" % msp + tail(completed))
        return record

    def _fresh_scratch(self, seeded):
        self.scratch.close()
        self.scratch = accept.Scratch(seeded=seeded)
        self.addCleanup(self.scratch.close)

    def _worktree_paths(self, completed):
        return [entry.get("path") for entry in self.state(completed).get("worktrees") or []]

    def _build(self, steps, *flags, cwd=None, env=None, writes=None, control=None):
        jobs = {
            step["name"]: {path: "work of %s\n" % step["name"] for path in step["files"]}
            for step in steps
        }
        accept.put_json(os.path.join(self.scratch.stubs, "worker.json"), {**jobs, **(writes or {})})
        accept.put_json(os.path.join(self.scratch.stubs, "control.json"), control or {})
        return self.scratch.mitosis(
            "--items",
            self.scratch.items(steps),
            "--run-dir",
            self.scratch.run_dir,
            "--feature-branch",
            "feature",
            "--dispatch-command",
            self.scratch.stub("worker.py", "{task}", "{session_id}", "{schema}"),
            "--reask-command",
            self.scratch.stub("reask.py", "{prompt}", "{session_id}", "{schema}"),
            "--acceptance-command",
            self.scratch.stub("accept.py", "{file}", "{test}"),
            "--no-push",
            *flags,
            cwd=cwd,
            env=env,
        )


class SharedEntries(OptionsCase):
    def an_exact_path_keeps_two_steps_apart(self):
        completed, plan = self.planned(two_editors(SHARED, SHARED), "--shared-files", SHARED)
        self.assertEqual(steps_of(plan), [["a"], ["b"]], tail(completed))

    def a_path_suffix_keeps_two_steps_apart(self):
        completed, plan = self.planned(
            two_editors("src/reg/types.py", "src/reg/types.py"), "--shared-files", SHARED
        )
        self.assertEqual(steps_of(plan), [["a"], ["b"]], tail(completed))

    def a_glob_keeps_two_steps_apart(self):
        completed, plan = self.planned(two_editors(SHARED, SHARED), "--shared-files", "reg/*.py")
        self.assertEqual(steps_of(plan), [["a"], ["b"]], tail(completed))

    def a_path_of_glob_characters_matches_itself(self):
        page = "app/(x)/[id]/page.tsx"
        completed, plan = self.planned(two_editors(page, page), "--shared-files", page)
        self.assertEqual(steps_of(plan), [["a"], ["b"]], tail(completed))

    def entries_are_stripped_and_blank_ones_dropped(self):
        completed, plan = self.planned(two_editors(SHARED, SHARED), "--shared-files", " reg/types.py , ,")
        self.assertEqual(len(plan.get("msps") or []), 2, tail(completed))
        self.assertEqual(plan.get("shared_files"), [SHARED], tail(completed))

    def part_of_a_file_name_shares_nothing(self):
        completed, plan = self.planned(two_editors("xtypes.py", "xtypes.py"), "--shared-files", "types.py")
        self.assertEqual(steps_of(plan), [["a", "b"]], tail(completed))

    def an_empty_list_declares_nothing(self):
        completed, plan = self.planned(two_editors(SHARED, SHARED), "--shared-files", "")
        self.assertEqual(steps_of(plan), [["a", "b"]], tail(completed))
        self.assertNotIn("shared_files", plan, tail(completed))
        self.assertNotIn("integration_order", plan, tail(completed))


class SharedOverlap(OptionsCase):
    def an_overlap_only_on_a_shared_file_makes_no_edge(self):
        completed, plan = self.planned(two_editors(SHARED, SHARED), "--shared-files", SHARED)
        self.assertEqual(len(plan.get("msps") or []), 2, tail(completed))
        self.assertEqual(len(plan.get("lanes") or []), 2, tail(completed))
        self.assertNotIn("lane_after", plan, tail(completed))
        items = plan.get("items") or []
        self.assertEqual(sorted(item.get("name") for item in items), ["a", "b"], tail(completed))
        self.assertEqual([item.get("after") for item in items if item.get("after")], [], tail(completed))

    def an_overlap_on_another_file_still_fuses(self):
        completed, plan = self.planned(
            [S("a", [SHARED, "x.py"]), S("b", [SHARED, "x.py"])], "--shared-files", SHARED
        )
        self.assertEqual(steps_of(plan), [["a", "b"]], tail(completed))
        self.assertEqual(lane_shape(plan), [(0, ["a"], [1, 2]), (0, ["b"], [2, 2])], tail(completed))
        self.assertEqual(plan.get("lane_after"), {"1": [0]}, tail(completed))

    def steps_of_one_msp_on_a_shared_file_share_a_lane(self):
        completed, plan = self.planned(
            [S("a", [SHARED, "a.py"], msp="m"), S("b", [SHARED, "b.py"], msp="m")], "--shared-files", SHARED
        )
        self.assertEqual(len(plan.get("msps") or []), 1, tail(completed))
        self.assertEqual(lane_shape(plan), [(0, ["a"], [1, 2]), (0, ["b"], [2, 2])], tail(completed))
        self.assertEqual(plan.get("lane_after"), {"1": [0]}, tail(completed))

    def a_shared_overlap_is_no_scheduling_conflict(self):
        completed, plan = self.planned(
            [S("a", [SHARED]), S("b", [SHARED]), S("c", [SHARED])], "--shared-files", SHARED
        )
        self.assertEqual(plan.get("coalesce"), {"0": [["a", "b", "c"]]}, tail(completed))


class SharedCoupling(OptionsCase):
    def a_shared_overlap_is_flagged_and_stays_parallel(self):
        completed, plan = self.planned(two_editors(SHARED, SHARED), "--shared-files", SHARED)
        self.assertEqual(
            plan.get("coupling_review"),
            [{"steps": ["a", "b"], "lanes": [0, 1], "signals": ["shared-file:" + SHARED], "default": "parallel"}],
            tail(completed),
        )

    def each_shared_file_is_its_own_signal(self):
        completed, plan = self.planned(
            [S("a", ["r/x.py", "s/y.py", "a.py"]), S("b", ["r/x.py", "s/y.py", "b.py"])],
            "--shared-files",
            "r/x.py,s/y.py",
        )
        review = plan.get("coupling_review") or []
        self.assertEqual(len(review), 1, tail(completed))
        self.assertEqual(review[0].get("signals"), ["shared-file:r/x.py", "shared-file:s/y.py"], tail(completed))

    def a_shared_risk_marker_still_decides_the_default(self):
        completed, plan = self.planned(
            two_editors(SHARED, SHARED), "--shared-files", SHARED, "--risk-markers", "reg"
        )
        review = plan.get("coupling_review") or []
        self.assertEqual(len(review), 1, tail(completed))
        self.assertEqual(
            review[0].get("signals"), ["shared-risk-marker:reg", "shared-file:" + SHARED], tail(completed)
        )
        self.assertEqual(review[0].get("default"), "serialize", tail(completed))


class IntegrationOrder(OptionsCase):
    def _locked_pair(self, b_acceptance):
        return [
            accept.check_author("check-author-a", ["tests/test_a.py"], "m1"),
            accept.check_author("check-author-b", ["tests/test_b.py"], "m2"),
            S("a", [SHARED, "a.py"], msp="m1", acceptance=[{"file": "tests/test_a.py", "test": "A.works"}]),
            S("b", [SHARED, "b.py"], msp="m2", acceptance=b_acceptance),
        ]

    def _saved_first_plan(self):
        steps = self._locked_pair([{"file": "tests/test_b.py", "test": "B.works"}])
        completed = self.scratch.plan_only(steps, "--shared-files", SHARED)
        self.exits(completed, 0)
        saved = self.scratch.path("saved-plan.json")
        shutil.copyfile(os.path.join(self.scratch.run_dir, "plan.json"), saved)
        return saved

    def the_plan_echoes_the_entries_and_orders_each_file(self):
        completed, plan = self.planned(two_editors(SHARED, SHARED), "--shared-files", SHARED)
        self.assertEqual(plan.get("shared_files"), [SHARED], tail(completed))
        self.assertEqual(plan.get("integration_order"), {SHARED: [0, 1]}, tail(completed))

    def a_producers_msp_is_integrated_first(self):
        completed, plan = self.planned(
            [S("b", [SHARED, "b.py"], after=["a"]), S("a", [SHARED, "a.py"])], "--shared-files", SHARED
        )
        self.assertEqual(plan.get("integration_order"), {SHARED: [1, 0]}, tail(completed))

    def the_longest_remaining_chain_is_integrated_first(self):
        completed, plan = self.planned(
            [S("x", [SHARED, "x.py"]), S("y", [SHARED, "y1.py", "y2.py", "y3.py"])], "--shared-files", SHARED
        )
        self.assertEqual(plan.get("integration_order"), {SHARED: [1, 0]}, tail(completed))

    def a_file_one_msp_edits_has_no_order(self):
        completed, plan = self.planned([S("a", [SHARED, "a.py"]), S("b", ["b.py"])], "--shared-files", SHARED)
        self.assertEqual(plan.get("shared_files"), [SHARED], tail(completed))
        self.assertNotIn("integration_order", plan, tail(completed))

    def a_saved_plan_keeps_its_shared_files(self):
        saved = self._saved_first_plan()
        other = self.scratch.path("run-again")
        steps = self._locked_pair([{"file": "tests/test_b.py", "test": "B.works"}])
        completed = self.scratch.plan_only(steps, "--plan-file", saved, run_dir=other)
        self.exits(completed, 0)
        plan = self.plan(completed, other)
        self.assertEqual(len(plan.get("msps") or []), 2, tail(completed))
        self.assertEqual(plan.get("shared_files"), [SHARED], tail(completed))

    def a_saved_plan_is_judged_with_its_shared_files(self):
        saved = self._saved_first_plan()
        other = self.scratch.path("run-again")
        steps = self._locked_pair(
            [{"file": "tests/test_b.py", "test": "B.works"}, {"file": "tests/test_a.py", "test": "A.also"}]
        )
        completed = self.scratch.plan_only(steps, "--plan-file", saved, run_dir=other)
        self.exits(completed, 3)
        self.assertIn("tests/test_a.py", completed.stderr)

    def the_lock_stage_makes_one_author_per_separate_msp(self):
        built = any_input.reply(
            any_input.item(
                "a", [SHARED, "a.py"], "a works", tests=[{"file": "tests/test_a.py", "test": "A.works"}]
            ),
            any_input.item(
                "b", [SHARED, "b.py"], "b works", tests=[{"file": "tests/test_b.py", "test": "B.works"}]
            ),
        )
        completed = self._asks_run("add a and b\n", {"add a and b": built}, "--shared-files", SHARED)
        self.exits(completed, 0)
        plan = self.plan(completed)
        self.assertEqual(len(plan.get("msps") or []), 2, tail(completed))
        authors = plan.get("check_authors") or []
        self.assertEqual(sorted(entry.get("msp") for entry in authors), [0, 1], tail(completed))


class SharedCandidates(OptionsCase):
    def a_file_three_steps_edit_is_suggested_and_not_applied(self):
        completed, plan = self.planned(
            [S("a", ["hub.py", "a.py"]), S("b", ["hub.py", "b.py"]), S("c", ["hub.py", "c.py"])]
        )
        self.assertEqual(steps_of(plan), [["a", "b", "c"]], tail(completed))
        self.assertEqual(
            plan.get("shared_file_candidates"),
            {"msps_now": 1, "files": [{"file": "hub.py", "items": 3, "msps_after": 3}]},
            tail(completed),
        )

    def a_file_two_steps_edit_is_not_suggested(self):
        completed, plan = self.planned(two_editors(SHARED, SHARED), "--shared-files", "unrelated.py")
        self.assertNotIn("shared_file_candidates", plan, tail(completed))

    def a_declared_file_is_not_suggested(self):
        completed, plan = self.planned(
            [S("a", ["hub.py", "a.py"]), S("b", ["hub.py", "b.py"]), S("c", ["hub.py", "c.py"])],
            "--shared-files",
            "hub.py",
        )
        self.assertEqual(len(plan.get("msps") or []), 3, tail(completed))
        self.assertNotIn("shared_file_candidates", plan, tail(completed))

    def files_that_split_only_together_are_reported_together(self):
        completed, plan = self.planned(
            [
                S("a", ["h1.py", "h2.py", "a.py"]),
                S("b", ["h1.py", "h2.py", "b.py"]),
                S("c", ["h1.py", "h2.py", "c.py"]),
            ]
        )
        self.assertEqual(
            plan.get("shared_file_candidates"),
            {
                "msps_now": 1,
                "files": [
                    {"file": "h1.py", "items": 3, "msps_after": 1},
                    {"file": "h2.py", "items": 3, "msps_after": 3},
                ],
            },
            tail(completed),
        )

    def the_walk_stops_at_its_last_gain(self):
        completed, plan = self.planned(
            [
                S("a", ["h1.py", "k.py", "z.py"]),
                S("b", ["h1.py", "k.py", "m.py", "z.py"]),
                S("c", ["h1.py", "c.py"]),
                S("e", ["m.py", "z.py"]),
            ]
        )
        self.assertEqual(
            plan.get("shared_file_candidates"),
            {"msps_now": 1, "files": [{"file": "h1.py", "items": 3, "msps_after": 2}]},
            tail(completed),
        )

    def the_report_holds_at_most_twenty_files(self):
        hubs = ["h%02d.py" % number for number in range(22)]
        completed, plan = self.planned([S(name, hubs + [name + ".py"]) for name in ("a", "b", "c")])
        candidates = plan.get("shared_file_candidates") or {}
        self.assertEqual(candidates.get("msps_now"), 1, tail(completed))
        files = candidates.get("files") or []
        self.assertEqual(len(files), 20, tail(completed))
        self.assertEqual(files[-1].get("file"), "h19.py", tail(completed))


class SharedReconcile(OptionsCase):
    def a_write_into_a_declared_shared_file_is_reported_and_not_fatal(self):
        completed = self.scratch.full_run(
            [S("a", ["a.py"]), S("b", ["s.txt", "b.py"])],
            "--shared-files",
            "s.txt",
            writes={"a": {"a.py": "a\n", "s.txt": "a\n"}},
        )
        self.exits(completed, 0)
        overlaps = [{"path": "s.txt", "also_in": ["b"], "integration_order": ["b", "a"]}]
        record = self._reconcile(completed)
        self.assertIs(record.get("fatal"), False, tail(completed))
        self.assertEqual(record.get("undeclared"), [], tail(completed))
        self.assertEqual(record.get("crossing"), [], tail(completed))
        self.assertEqual(record.get("shared_overlaps"), overlaps, tail(completed))
        first = (accept.summary_of(completed).get("msps") or [{}])[0]
        self.assertEqual((first.get("reconcile") or {}).get("shared_overlaps"), overlaps, tail(completed))
        named = [
            line
            for line in completed.stdout.splitlines()[:-1]
            if "s.txt" in line and ordered_mention(line, "b", "a")
        ]
        self.assertTrue(named, tail(completed))

    def a_write_into_an_undeclared_file_of_another_msp_stays_fatal(self):
        completed = self.scratch.full_run(
            [S("a", ["a.py"]), S("b", ["x.txt", "b.py"])],
            "--shared-files",
            "unrelated.txt",
            writes={"a": {"a.py": "a\n", "x.txt": "a\n"}},
        )
        self.exits(completed, 7)
        record = self._reconcile(completed)
        self.assertIs(record.get("fatal"), True, tail(completed))
        self.assertEqual([entry.get("path") for entry in record.get("crossing") or []], ["x.txt"], tail(completed))
        self.assertNotIn("shared_overlaps", record, tail(completed))

    def a_shared_file_no_other_msp_names_stays_undeclared(self):
        completed = self.scratch.full_run(
            [S("a", ["a.py"]), S("b", ["b.py"])],
            "--shared-files",
            "s.txt",
            writes={"a": {"a.py": "a\n", "s.txt": "a\n"}},
        )
        self.exits(completed, 6)
        record = self._reconcile(completed)
        self.assertEqual(record.get("undeclared"), ["s.txt"], tail(completed))
        self.assertNotIn("shared_overlaps", record, tail(completed))

    def two_msps_that_declare_a_shared_file_both_build(self):
        completed = self.scratch.full_run(
            [S("a", ["s.txt", "a.py"]), S("b", ["s.txt", "b.py"])], "--shared-files", "s.txt"
        )
        self.exits(completed, 0)
        self.assertEqual(len(self._worktree_paths(completed)), 2, tail(completed))
        for name in ("a", "b"):
            shown = self.scratch.git("show", "mitosis/%s:s.txt" % name)
            self.assertEqual(shown.returncode, 0, shown.stderr + tail(completed))
            self.assertEqual(shown.stdout, "work of %s\n" % name, tail(completed))
            self.assertNotIn("shared_overlaps", self._reconcile(completed, {"a": "0", "b": "1"}[name]))

    def a_locked_test_written_as_a_shared_file_stays_fatal(self):
        self._fresh_scratch({"tests/locked.py": "import unittest\n"})
        charter = accept.put(self.scratch.path("charter.md"), "# Charter\n\n- Locked: tests/locked.py\n")
        completed = self.scratch.full_run(
            [S("a", ["a.py"]), S("b", ["b.py"])],
            "--charter",
            charter,
            "--shared-files",
            "tests/locked.py",
            writes={"a": {"a.py": "a\n", "tests/locked.py": "rewritten\n"}},
        )
        self.assertNotEqual(completed.returncode, 0, tail(completed))
        record = self._reconcile(completed)
        self.assertIs(record.get("fatal"), True, tail(completed))
        self.assertIn("tests/locked.py", record.get("referee") or [], tail(completed))


class SharedPullRequest(unittest.TestCase):
    def a_shared_overlap_becomes_a_tradeoff_with_the_order(self):
        import test_pr_facts as given
        import pr_facts

        plan = given.plan_of(
            [{"label": "a", "steps": ["a"]}, {"label": "b", "steps": ["b"]}],
            [
                {"name": "a", "task": "Build a.", "files": ["a.py"], "acceptance": []},
                {"name": "b", "task": "Build b.", "files": ["s.txt", "b.py"], "acceptance": []},
            ],
        )
        result = pr_facts.facts(
            given.HEADER,
            plan,
            0,
            given.first_gate(),
            findings={
                "undeclared": [],
                "unwritten": [],
                "crossing": [],
                "foreign": [],
                "shared_overlaps": [{"path": "s.txt", "also_in": ["b"], "integration_order": ["b", "a"]}],
            },
        )
        tradeoffs = result.get("tradeoffs") or []
        named = [entry for entry in tradeoffs if "s.txt" in entry and ordered_mention(entry, "b", "a")]
        self.assertEqual(len(named), 1, json.dumps(tradeoffs))


class WorktreeRoot(OptionsCase):
    def _unmakeable(self):
        accept.put(self.scratch.path("blocker"), "a regular file\n")
        return self.scratch.path("blocker", "trees")

    def each_msp_worktree_is_placed_under_the_named_folder(self):
        elsewhere = self.scratch.path("elsewhere")
        completed = self.scratch.full_run([S("a", ["a.py"])], "--worktree-root", elsewhere)
        self.exits(completed, 0)
        self.assertEqual(self._worktree_paths(completed)[:1], [os.path.join(elsewhere, "a")], tail(completed))
        self.assertTrue(os.path.isfile(os.path.join(elsewhere, "a", "a.py")), tail(completed))
        self.assertFalse(os.path.exists(os.path.join(self.scratch.run_dir, "trees")), tail(completed))

    def a_relative_root_is_read_against_the_repository_root(self):
        self._fresh_scratch({"app/seed.txt": "seed\n"})
        completed = self._build(
            [S("a", ["app/a.py"])],
            "--worktree-root",
            os.path.join("..", "elsewhere"),
            cwd=os.path.join(self.scratch.repo, "app"),
        )
        self.exits(completed, 0)
        self.assertEqual(
            self._worktree_paths(completed)[:1], [self.scratch.path("elsewhere", "a")], tail(completed)
        )

    def a_worker_write_inside_the_root_is_refused(self):
        elsewhere = self.scratch.path("elsewhere")
        completed = self.scratch.full_run(
            [S("a", ["a.py"])],
            "--worktree-root",
            elsewhere,
            "--worker-write",
            os.path.join(elsewhere, "cache"),
        )
        self.exits(completed, 3)
        self.assertIn("--worker-write", completed.stderr)
        self.assertIn("checkout folder", completed.stderr)
        self.assertFalse(os.path.exists(self.scratch.path("started-a")), tail(completed))

    def an_empty_root_uses_the_default(self):
        completed = self.scratch.full_run([S("a", ["a.py"])], "--worktree-root", "")
        self.exits(completed, 0)
        self.assertEqual(
            self._worktree_paths(completed)[:1], [os.path.join(self.scratch.run_dir, "trees", "a")], tail(completed)
        )

    def a_root_that_cannot_be_created_fails_and_names_it(self):
        unmakeable = self._unmakeable()
        completed = self.scratch.full_run([S("a", ["a.py"])], "--worktree-root", unmakeable)
        self.exits(completed, 3)
        self.assertIn(unmakeable, completed.stderr)
        self.assertFalse(os.path.exists(self.scratch.path("started-a")), tail(completed))

    def a_root_that_cannot_be_created_starts_no_check_author(self):
        unmakeable = self._unmakeable()
        completed = self.scratch.full_run(
            [
                accept.check_author("check-author-lock", ["tests/test_a.py"], "m"),
                S("a", ["a.py"], msp="m", acceptance=[{"file": "tests/test_a.py", "test": "A.works"}]),
            ],
            "--worktree-root",
            unmakeable,
        )
        self.exits(completed, 3)
        self.assertIn(unmakeable, completed.stderr)
        self.assertFalse(os.path.exists(self.scratch.path("started-check-author-lock")), tail(completed))
        self.assertFalse(os.path.exists(self.scratch.path("started-a")), tail(completed))


class NoRunDir(OptionsCase):
    def _runs(self):
        return os.path.join(self.scratch.env["HOME"], ".mitosis", "runs")

    def _reply_lines(self, completed):
        found = []
        for number, line in enumerate(completed.stdout.splitlines()):
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict) and (parsed.get("structured_output") or {}).get("item") == "a":
                found.append(number)
        return found

    def a_build_keeps_no_run_state(self):
        private = self._empty_tmp()
        completed = self.scratch.full_run([S("a", ["a.py"])], "--no-run-dir", env={"TMPDIR": private})
        self.exits(completed, 0)
        self._left_in(private, completed)
        self.assertFalse(os.path.exists(self.scratch.run_dir), tail(completed))
        result = accept.summary_of(completed)
        self.assertTrue(result, tail(completed))
        self.assertNotIn("run_dir", result, tail(completed))
        self.assertIn(NO_RUN_DIR, completed.stdout)
        branch = self.scratch.git("rev-parse", "--verify", "--quiet", "refs/heads/mitosis/a")
        self.assertEqual(branch.returncode, 0, tail(completed))

    def the_worktree_stays_where_a_run_of_the_input_keeps_it(self):
        completed = self.scratch.full_run([S("a", ["a.py"])], "--no-run-dir")
        self.exits(completed, 0)
        repository = os.path.join(self._runs(), "repo")
        self.assertTrue(os.path.isdir(repository), tail(completed))
        kept = sorted(os.listdir(repository))
        self.assertEqual(len(kept), 1, "%s%s" % (kept, tail(completed)))
        folder = os.path.join(repository, kept[0])
        self.assertEqual(sorted(os.listdir(folder)), ["trees"], tail(completed))
        self.assertTrue(os.path.isfile(os.path.join(folder, "trees", "a", "a.py")), tail(completed))

    def a_named_root_holds_the_worktree(self):
        elsewhere = self.scratch.path("elsewhere")
        completed = self.scratch.full_run([S("a", ["a.py"])], "--no-run-dir", "--worktree-root", elsewhere)
        self.exits(completed, 0)
        self.assertTrue(os.path.isfile(os.path.join(elsewhere, "a", "a.py")), tail(completed))
        self.assertFalse(os.path.exists(self._runs()), tail(completed))

    def each_workers_output_reaches_stdout(self):
        completed = self.scratch.full_run([S("a", ["a.py"])], "--no-run-dir")
        self.exits(completed, 0)
        lines = completed.stdout.splitlines()
        replies = self._reply_lines(completed)
        self.assertTrue(replies, tail(completed))
        heading = "%s:" % "Coverage map"
        self.assertIn(heading, lines, tail(completed))
        self.assertLess(replies[0], lines.index(heading), tail(completed))
        result = accept.summary_of(completed)
        self.assertIn("exit", result, tail(completed))
        self.assertIn("msps", result, tail(completed))

    def a_failed_build_keeps_no_run_state(self):
        private = self._empty_tmp()
        completed = self.scratch.full_run(
            [S("a", ["a.py"])], "--no-run-dir", control={"a": {"status": "failed"}}, env={"TMPDIR": private}
        )
        self.assertNotEqual(completed.returncode, 0, tail(completed))
        self._left_in(private, completed)
        self.assertTrue(self._reply_lines(completed), tail(completed))

    def a_refused_run_keeps_no_run_state(self):
        private = self._empty_tmp()
        completed = self.scratch.mitosis(
            "--items",
            self.scratch.items([S("a", ["a.py"]), S("a", ["b.py"])]),
            "--plan-only",
            "--feature-branch",
            "feature",
            "--no-run-dir",
            env={"TMPDIR": private},
        )
        self.assertNotIn(completed.returncode, (0, 2), tail(completed))
        self._left_in(private, completed)

    def a_failed_worker_leaving_a_read_only_folder_keeps_no_run_state(self):
        private = self._empty_tmp()
        accept.put(os.path.join(self.scratch.stubs, "read_only.py"), READ_ONLY_WORKER)
        completed = self.scratch.mitosis(
            "--items",
            self.scratch.items([S("a", ["a.py"])]),
            "--no-run-dir",
            "--run-dir",
            self.scratch.run_dir,
            "--feature-branch",
            "feature",
            "--dispatch-command",
            self.scratch.stub("read_only.py", "{task}", "{session_id}", "{schema}"),
            "--reask-command",
            self.scratch.stub("reask.py", "{prompt}", "{session_id}", "{schema}"),
            "--acceptance-command",
            self.scratch.stub("accept.py", "{file}", "{test}"),
            "--no-push",
            env={"TMPDIR": private},
        )
        self.assertNotEqual(completed.returncode, 0, tail(completed))
        self.assertTrue(os.path.isfile(self.scratch.path("ran-read-only")), tail(completed))
        self._left_in(private, completed)

    def a_resume_starts_from_nothing(self):
        built = any_input.reply(any_input.item("reader", ["reader.py"], "reader prints the count"))
        completed = self._asks_run("add a reader\n", {"add a reader": built}, "--resume", "--no-run-dir")
        self.exits(completed, 0)
        self.assertEqual(len(self._calls()), 1, tail(completed))

    def a_spec_resume_still_needs_its_planning_command(self):
        document = accept.put(self.scratch.path("one.md"), "# One line\n")
        completed = self.scratch.mitosis(
            "--spec",
            document,
            "--resume",
            "--no-run-dir",
            "--plan-only",
            "--feature-branch",
            "feature",
        )
        self.exits(completed, 2)
        self.assertIn("--spec needs --decompose-command", completed.stderr)

    def a_plan_only_run_keeps_nothing(self):
        private = self._empty_tmp()
        completed = self.scratch.plan_only([S("a", ["a.py"])], "--no-run-dir", env={"TMPDIR": private})
        self.exits(completed, 0)
        self._left_in(private, completed)
        self.assertFalse(os.path.exists(self.scratch.run_dir), tail(completed))
        self.assertFalse(os.path.exists(self._runs()), tail(completed))
        result = accept.summary_of(completed)
        self.assertTrue(result, tail(completed))
        self.assertNotIn("run_dir", result, tail(completed))


def load_tests(loader, standard, pattern):
    helpers = ("make_scratch",)

    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            return sorted(
                name
                for name in vars(case)
                if not name.startswith("_") and callable(getattr(case, name)) and name not in helpers
            )

    suite = unittest.TestSuite()
    for case in (
        SharedEntries,
        SharedOverlap,
        SharedCoupling,
        IntegrationOrder,
        SharedCandidates,
        SharedReconcile,
        SharedPullRequest,
        WorktreeRoot,
        NoRunDir,
    ):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite
