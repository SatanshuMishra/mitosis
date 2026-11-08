import hashlib
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_accept_execution as accept
from test_accept_execution import S, check_author, tail


def graph_at(scratch, graph):
    return accept.put_json(scratch.path("graph.json"), graph)


class MspCycles(accept.Case):
    def msps_that_wait_on_each_other_stay_two_msps(self):
        completed, plan = self.planned(
            [
                S("core", ["core.py", "shared.py"]),
                S("mid", ["mid.py"], after=["core"]),
                S("tail", ["tail.py", "shared.py"], after=["mid"]),
            ]
        )
        msps = plan.get("msps") or []
        self.assertEqual(
            sorted(sorted(msp.get("steps") or []) for msp in msps), [["core", "tail"], ["mid"]], tail(completed)
        )


class PinnedGroups(accept.Case):
    def two_contract_producers_still_pin_the_group(self):
        completed, plan = self.planned(
            [
                S("p1", ["p1.py"], type="contract", contract_group="g"),
                S("p2", ["p2.py"], type="contract", contract_group="g"),
                S("c1", ["c1.py"], contract_group="g", after=["p1", "p2"]),
                S("c2", ["c2.py"], contract_group="g", after=["p1", "p2"]),
            ]
        )
        lanes = plan.get("lanes") or []
        self.assertEqual(len(lanes), 4, tail(completed))
        self.assertEqual([lane for lane in lanes if "session" in lane], [], json.dumps(lanes))

    def a_member_waiting_only_through_another_step_unpins_the_group(self):
        completed, plan = self.planned(
            [
                S("p", ["p.py"], type="contract", contract_group="g"),
                S("c1", ["c1.py"], contract_group="g", after=["p"]),
                S("c2", ["c2.py"], contract_group="g", after=["p"]),
                S("c3", ["c3.py"], contract_group="g", after=["c1"]),
            ]
        )
        lanes = plan.get("lanes") or []
        self.assertEqual(len(lanes), 4, json.dumps(lanes))
        self.assertEqual(
            sorted(lane.get("session") or [] for lane in lanes), [[1, 4], [2, 4], [3, 4], [4, 4]], json.dumps(lanes)
        )


class LaneOrder(accept.Case):
    def the_step_with_the_longest_critical_path_goes_first(self):
        completed, plan = self.planned(
            [S("a", ["shared.py"]), S("b", ["shared.py", "x.py", "y.py", "z.py"])]
        )
        lanes = plan.get("lanes") or []
        self.assertEqual(len(lanes), 2, json.dumps(lanes))
        self.assertEqual((lanes[0].get("steps"), lanes[0].get("session")), (["b"], [1, 2]), json.dumps(lanes))
        self.assertEqual((lanes[1].get("steps"), lanes[1].get("session")), (["a"], [2, 2]), json.dumps(lanes))


class TokenEstimates(accept.Case):
    def a_plan_carries_no_token_estimate(self):
        completed, plan = self.planned([S("a", ["a.py"])])
        lanes = plan.get("lanes") or [{}]
        self.assertNotIn("tokens", lanes[0], json.dumps(lanes))
        self.assertNotIn("session_tokens", plan.keys())
        self.assertNotIn("sizes", plan.keys())

    def the_session_limit_flag_is_gone(self):
        completed = self.scratch.plan_only([S("a", ["a.py"])], "--session-tokens", "5")
        self.exits(completed, 2)
        self.assertIn("--session-tokens", completed.stderr)


class Clusters(accept.Case):
    def a_plan_names_no_clusters(self):
        completed, plan = self.planned([S("p", ["p.py"]), S("c", ["c.py"], after=["p"])])
        self.assertNotIn("clusters", plan.keys())
        self.assertEqual([line for line in completed.stdout.splitlines() if "Cluster" in line], [])


class Coalesce(accept.Case):
    def steps(self):
        return [
            S("a", ["x.py"], complexity="simple"),
            S("b", ["x.py"], complexity="simple"),
            S("c", ["c.py"], complexity="simple"),
            S("d", ["d.py"], complexity="simple"),
        ]

    def small_cheap_steps_of_one_wave_are_grouped(self):
        completed, plan = self.planned(self.steps())
        self.assertEqual(plan.get("coalesce"), {"0": [["a", "c", "d"]]}, tail(completed))

    def the_group_size_cap_is_a_flag(self):
        completed = self.scratch.plan_only(self.steps(), "--coalesce-max", "2")
        self.exits(completed, 0)
        plan = self.plan(completed)
        self.assertEqual(plan.get("coalesce"), {"0": [["a", "c"]]}, tail(completed))


class PlanId(accept.Case):
    def plan_id_is_a_hash_of_the_plan(self):
        completed, plan = self.planned([S("a", ["a.py"])])
        self.assertNotIn("version", plan.keys())
        expected = hashlib.sha256(
            json.dumps({k: v for k, v in plan.items() if k != "plan_id"}, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        self.assertEqual(plan.get("plan_id"), expected)


class ContextPacks(accept.Case):
    def a_pack_is_edit_and_sorted_read(self):
        graph = graph_at(
            self.scratch,
            {
                "nodes": [
                    {"id": "a", "source_file": "a.py"},
                    {"id": "z", "source_file": "z.py"},
                    {"id": "m", "source_file": "m.py"},
                ],
                "links": [{"source": "a", "target": "z"}, {"source": "a", "target": "m"}],
            },
        )
        completed, plan = self.planned([S("a", ["a.py"])], "--graph", graph)
        self.assertEqual(
            (plan.get("context_packs") or {}).get("a"), {"edit": ["a.py"], "read": ["m.py", "z.py"]}, tail(completed)
        )

    def graph_anchors_are_found_by_path_suffix(self):
        graph = graph_at(
            self.scratch,
            {
                "nodes": [{"id": "a", "source_file": "app/a.py"}, {"id": "b", "source_file": "app/b.py"}],
                "links": [{"source": "a", "target": "b"}],
            },
        )
        completed, plan = self.planned([S("a", ["src/app/a.py"])], "--graph", graph)
        self.assertEqual(
            (plan.get("context_packs") or {}).get("a"),
            {"edit": ["src/app/a.py"], "read": ["app/b.py"]},
            tail(completed),
        )


class ReadCap(accept.Case):
    def a_merged_read_set_is_not_capped_again(self):
        graph = graph_at(
            self.scratch,
            accept.node_link(
                [
                    ("tests/test_a.py", "n1.py"),
                    ("tests/test_a.py", "n2.py"),
                    ("tests/test_b.py", "n3.py"),
                    ("tests/test_b.py", "n4.py"),
                ]
            ),
        )
        steps = [
            S("a", ["a.py", "shared.py"], msp="x", acceptance=[{"file": "tests/test_a.py", "test": "a_works"}]),
            S("b", ["b.py", "shared.py"], msp="y", acceptance=[{"file": "tests/test_b.py", "test": "b_works"}]),
            check_author("check-author-x", ["tests/test_a.py"], "x"),
            check_author("check-author-y", ["tests/test_b.py"], "y"),
        ]
        completed, plan = self.planned(steps, "--graph", graph, "--context-cap", "2")
        authors = plan.get("check_authors") or []
        self.assertEqual(len(authors), 1, json.dumps(authors))
        brief = authors[0].get("brief") or {}
        self.assertEqual(brief.get("read_set"), ["n1.py", "n2.py", "n3.py", "n4.py"], json.dumps(brief))
        self.assertEqual(brief.get("read_overflow"), 0, json.dumps(brief))


class GraphInput(accept.Case):
    def document_graph(self):
        return graph_at(
            self.scratch,
            {
                "nodes": [
                    {"id": "c", "source_file": "a.py", "file_type": "code"},
                    {"id": "d", "source_file": "docs/guide.md", "file_type": "document"},
                ],
                "links": [{"source": "c", "target": "d"}],
            },
        )

    def an_adjacency_map_is_refused(self):
        graph = graph_at(self.scratch, {"a.py": ["b.py"]})
        completed = self.scratch.plan_only([S("a", ["a.py"])], "--graph", graph)
        self.exits(completed, 3)
        self.assertIn("--graph", completed.stderr)

    def a_document_node_is_linked_like_code(self):
        completed, plan = self.planned([S("a", ["a.py"])], "--graph", self.document_graph())
        pack = (plan.get("context_packs") or {}).get("a") or {}
        self.assertEqual(pack.get("read"), ["docs/guide.md"], tail(completed))

    def a_structure_prompt_keeps_the_graphs_neighbour_lines(self):
        scratch = self.scratch
        spec = os.path.join(scratch.repo, "spec.md")
        accept.put_json(
            os.path.join(scratch.stubs, "structure-reply.json"),
            {
                "items": [{"name": "a", "files": ["a.py"], "spec_ref": ["1.1"], "acceptance": []}],
                "assumptions": [],
                "constraints": [],
            },
        )
        completed = scratch.mitosis(
            "--spec",
            spec,
            "--plan-only",
            "--graph",
            self.document_graph(),
            "--run-dir",
            scratch.run_dir,
            "--feature-branch",
            "feature",
            "--decompose-command",
            scratch.stub("structurer.py", "{session_id}", "{schema}"),
            "--reask-command",
            scratch.stub("reask.py", "{prompt}", "{session_id}", "{schema}"),
        )
        self.exits(completed, 0)
        recorded = os.path.join(scratch.stubs, "structure-prompt.txt")
        self.assertTrue(os.path.isfile(recorded), tail(completed))
        lines = accept.read_text(recorded).splitlines()
        self.assertIn("Import graph, each path to its neighbours:", lines)
        self.assertIn("  a.py: docs/guide.md", lines)
        self.assertIn("  docs/guide.md: a.py", lines)

    def make_scratch(self):
        return accept.Scratch(seeded={"spec.md": accept.SPEC_DOCUMENT})


class Findings(accept.Case):
    def a_lane_cycle_is_not_a_finding(self):
        steps = [
            S("a1", ["pkg/a.py"]),
            S("a2", ["pkg/a.py"]),
            S("b1", ["pkg/b.py"], after=["a1"]),
            S("b2", ["pkg/b.py"]),
            S("c", ["c.py"], after=["a1"]),
            S("d", ["d.py"], after=["b2"]),
            S("a3", ["pkg/a.py"], after=["b2"]),
        ]
        completed, plan = self.planned([{**step, "msp": "m"} for step in steps])
        self.assertEqual([line for line in completed.stdout.splitlines() if "lane-cycle" in line], [])

    def an_early_manifest_is_not_a_finding(self):
        completed = self.scratch.plan_only(
            [S("gregorian", ["pkg/__init__.py", "pkg/g.py"]), S("parse", ["pkg/parse.py"], after=["gregorian"])]
        )
        self.assertEqual(
            [line for line in completed.stdout.splitlines() if "manifest-exports-nothing" in line], [], tail(completed)
        )


class ManifestExit(accept.Case):
    def an_early_manifest_exits_zero(self):
        completed = self.scratch.plan_only(
            [S("gregorian", ["pkg/__init__.py", "pkg/g.py"]), S("parse", ["pkg/parse.py"], after=["gregorian"])]
        )
        self.exits(completed, 0)
        summary = accept.summary_of(completed)
        self.assertEqual(summary.get("exit"), 0, tail(completed))
        self.assertEqual(
            [line for line in summary.get("attention") or [] if "nothing exported" in json.dumps(line)], [], tail(completed)
        )


class NoBriefs(accept.Case):
    def a_shape_only_plan_omits_the_briefs(self):
        completed = self.scratch.plan_only([S("a", ["a.py"])], "--no-briefs")
        self.exits(completed, 0)
        self.assertNotIn("briefs", self.plan(completed).keys())

    def a_run_with_no_briefs_is_refused(self):
        completed = self.scratch.full_run([S("a", ["a.py"])], "--no-briefs")
        self.exits(completed, 2)
        self.assertIn("--no-briefs omits the briefs a run dispatches from; use it with --plan-only", completed.stderr)
        self.assertFalse(os.path.exists(self.scratch.path("started-a")))


def load_tests(loader, standard, pattern):
    helpers = ("steps", "make_scratch", "document_graph")

    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            return sorted(
                name
                for name in vars(case)
                if not name.startswith("_") and callable(getattr(case, name)) and name not in helpers
            )

    suite = unittest.TestSuite()
    for case in (
        MspCycles,
        PinnedGroups,
        LaneOrder,
        TokenEstimates,
        Clusters,
        Coalesce,
        PlanId,
        ContextPacks,
        ReadCap,
        GraphInput,
        Findings,
        ManifestExit,
        NoBriefs,
    ):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite
