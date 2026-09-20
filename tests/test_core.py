import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core


def step(name, files, **extra):
    base = {
        "name": name,
        "task": "do " + name,
        "files": list(files),
        "source": None,
        "acceptance": [],
    }
    return {**base, **extra}


def lane_of(lanes, items, name):
    index = [item["name"] for item in items].index(name)
    return next(i for i, lane in enumerate(lanes) if index in lane)


def msp_of(msps, items, name):
    index = [item["name"] for item in items].index(name)
    return next(i for i, msp in enumerate(msps) if index in msp)


class Grouping(unittest.TestCase):
    def a_lane_never_spans_two_msps(self):
        items = [
            step("a", ["a.py"]),
            step("b", ["a.py", "b.py"]),
            step("c", ["c.py"], after=["b"]),
            step("d", ["d.py"], contract_group="g"),
            step("e", ["e.py"], contract_group="g"),
            step("f", ["f.py"], msp="x"),
            step("g", ["g.py"], msp="x", after=["a"]),
        ]
        msps = core.msp_items(items)
        lanes = core.lane_items(items)
        owners = core.lane_msps(items, lanes)
        for lane_index, lane in enumerate(lanes):
            containing = {msp_of(msps, items, items[i]["name"]) for i in lane}
            self.assertEqual(len(containing), 1)
            self.assertEqual(owners[lane_index], containing.pop())
        self.assertEqual(sorted(sum((list(l) for l in lanes), [])), list(range(len(items))))
        self.assertNotEqual(msp_of(msps, items, "b"), msp_of(msps, items, "c"))
        self.assertNotEqual(lane_of(lanes, items, "b"), lane_of(lanes, items, "c"))
        self.assertNotEqual(msp_of(msps, items, "a"), msp_of(msps, items, "g"))
        self.assertNotEqual(lane_of(lanes, items, "a"), lane_of(lanes, items, "g"))

    def a_pinned_contract_splits_into_parallel_lanes(self):
        pinned = [
            step("iface", ["iface.py"], type="contract", contract_group="g"),
            step("server", ["server.py"], contract_group="g", after=["iface"]),
            step("client", ["client.py"], contract_group="g", after=["iface"]),
        ]
        msps = core.msp_items(pinned)
        lanes = core.lane_items(pinned)
        self.assertEqual(len(msps), 1)
        self.assertEqual(len(lanes), 3)
        edges = core.lane_after(pinned, lanes)
        iface = lane_of(lanes, pinned, "iface")
        server = lane_of(lanes, pinned, "server")
        client = lane_of(lanes, pinned, "client")
        self.assertEqual(edges, {server: (iface,), client: (iface,)})

        unpinned = [
            step("server", ["server.py"], contract_group="g"),
            step("client", ["client.py"], contract_group="g"),
        ]
        self.assertEqual(len(core.msp_items(unpinned)), 1)
        self.assertEqual(len(core.lane_items(unpinned)), 2)
        self.assertEqual(core.lane_after(unpinned, core.lane_items(unpinned)), {})

    def an_unpinned_contract_group_ships_as_one_msp_without_serialising_its_steps(self):
        items = [
            step("server", ["server.py"], contract_group="g"),
            step("client", ["client.py"], contract_group="g"),
            step("docs", ["docs.py"], contract_group="g"),
        ]
        self.assertEqual(len(core.msp_items(items)), 1)
        self.assertEqual(len(core.lane_items(items)), 3)

    def a_lane_cycle_is_reported_when_fusion_turns_a_step_dag_into_a_loop(self):
        items = [
            step("core", ["core.py", "shared.py"]),
            step("mid", ["mid.py"], after=["core"]),
            step("tail", ["tail.py", "shared.py"], after=["mid"]),
        ]
        self.assertEqual(core.cycles(items), ())
        lanes = core.lane_items(items)
        found = core.lane_cycles(items, lanes)
        self.assertEqual(len(found), 1)
        self.assertEqual(len(found[0]), 2)

    def a_plan_without_a_loop_reports_no_lane_cycle(self):
        items = [step("a", ["a.py"]), step("b", ["b.py"], after=["a"])]
        self.assertEqual(core.lane_cycles(items, core.lane_items(items)), ())

    def a_branching_after_edge_does_not_fuse(self):
        branching = [
            step("p", ["p.py"], msp="m"),
            step("c1", ["c1.py"], msp="m", after=["p"]),
            step("c2", ["c2.py"], msp="m", after=["p"]),
        ]
        lanes = core.lane_items(branching)
        self.assertEqual(len(core.msp_items(branching)), 1)
        self.assertEqual(len(lanes), 3)
        p = lane_of(lanes, branching, "p")
        edges = core.lane_after(branching, lanes)
        self.assertEqual(edges[lane_of(lanes, branching, "c1")], (p,))
        self.assertEqual(edges[lane_of(lanes, branching, "c2")], (p,))
        self.assertNotIn(p, edges)

        chain = [
            step("second", ["b.py"], msp="m", after=["first"]),
            step("first", ["a.py"], msp="m"),
        ]
        lanes = core.lane_items(chain)
        self.assertEqual(lanes, ((1, 0),))
        self.assertEqual(core.lane_after(chain, lanes), {})

        cross = [
            step("producer", ["a.py"]),
            step("consumer", ["b.py"], after=["producer"]),
        ]
        self.assertEqual(len(core.msp_items(cross)), 2)
        self.assertEqual(len(core.lane_items(cross)), 2)

    def a_dependent_lane_waits_and_its_siblings_do_not(self):
        items = [
            step("y", ["y.py"]),
            step("x1", ["x1.py"], msp="x"),
            step("x2", ["x2.py"], msp="x", after=["y"]),
            step("x3", ["x3.py"], msp="x"),
            step("z", ["z.py"]),
        ]
        msps = core.msp_items(items)
        lanes = core.lane_items(items)
        edges = core.lane_after(items, lanes)
        self.assertEqual(set(edges), {lane_of(lanes, items, "x2")})
        self.assertEqual(edges[lane_of(lanes, items, "x2")], (lane_of(lanes, items, "y"),))
        self.assertNotIn(lane_of(lanes, items, "x1"), edges)
        self.assertNotIn(lane_of(lanes, items, "x3"), edges)
        groups = core.clusters(items, lanes)
        y, x, z = (msp_of(msps, items, n) for n in ("y", "x1", "z"))
        self.assertEqual(sorted(sorted(g) for g in groups), sorted([sorted([y, x]), [z]]))

    def a_declared_msp_tag_fuses_but_never_splits(self):
        fused = [step("a", ["a.py"], msp="t"), step("b", ["b.py"], msp="t")]
        self.assertEqual(core.msp_items(fused), ((0, 1),))

        conflicting = [
            step("c", ["shared.py"], msp="u"),
            step("d", ["shared.py", "d.py"], msp="v"),
            step("e", ["e.py"], contract_group="g", msp="w"),
            step("f", ["f.py"], contract_group="g"),
        ]
        self.assertEqual(core.msp_items(conflicting), ((0, 1), (2, 3)))

        untagged_neighbour = [step("g", ["g.py"], msp="t"), step("h", ["g.py"])]
        self.assertEqual(core.msp_items(untagged_neighbour), ((0, 1),))


class Validation(unittest.TestCase):
    def an_after_naming_a_missing_step_is_fatal(self):
        clean = [step("a", ["a.py"]), step("b", ["b.py"], after=["a"])]
        self.assertEqual(core.validate(clean)["errors"], [])
        broken = [step("a", ["a.py"]), step("b", ["b.py"], after=["zz"])]
        errors = core.validate(broken)["errors"]
        self.assertEqual(len(errors), 1)
        self.assertIn("zz", errors[0])
        self.assertIn("b", errors[0])

    def a_dependency_cycle_names_its_members(self):
        items = [
            step("alpha", ["a.py"], after=["gamma"]),
            step("beta", ["b.py"], after=["alpha"]),
            step("gamma", ["c.py"], after=["beta"]),
            step("delta", ["d.py"], after=["alpha"]),
        ]
        errors = core.validate(items)["errors"]
        self.assertEqual(len(errors), 1)
        for member in ("alpha", "beta", "gamma"):
            self.assertIn(member, errors[0])
        self.assertNotIn("delta", errors[0])

    def prose_acceptance_is_rejected_at_validate(self):
        prose = [step("a", ["a.py"], acceptance="python3 -m unittest passes")]
        self.assertTrue(any("acceptance" in e for e in core.validate(prose)["errors"]))
        listed_prose = [step("a", ["a.py"], acceptance=["the tests pass"])]
        self.assertTrue(any("acceptance" in e for e in core.validate(listed_prose)["errors"]))
        half = [step("a", ["a.py"], acceptance=[{"file": "tests/test_a.py"}])]
        self.assertTrue(any("acceptance" in e for e in core.validate(half)["errors"]))
        runnable = [step("a", ["a.py"], acceptance=[{"file": "tests/test_a.py", "test": "A.works"}])]
        self.assertEqual(core.validate(runnable)["errors"], [])

    def items_declaring_different_sources_are_fatal(self):
        one = {"path": "docs/spec.md", "sha256": "aa"}
        other = {"path": "docs/spec.md", "sha256": "bb"}
        agreeing = [step("a", ["a.py"], source=one), step("b", ["b.py"], source=dict(one))]
        self.assertEqual(core.validate(agreeing)["errors"], [])
        disagreeing = [step("a", ["a.py"], source=one), step("b", ["b.py"], source=other)]
        errors = core.validate(disagreeing)["errors"]
        self.assertEqual(len(errors), 1)
        self.assertIn("source", errors[0])
        mixed = [step("a", ["a.py"], source=one), step("b", ["b.py"])]
        self.assertEqual(len(core.validate(mixed)["errors"]), 1)

    def a_nonexistent_write_set_path_is_counted_not_fatal(self):
        import tempfile

        with tempfile.TemporaryDirectory() as root:
            present = os.path.join(root, "present.py")
            with open(present, "w") as handle:
                handle.write("")
            items = [
                step("a", ["present.py"], assumptions=["chose the narrow reading"]),
                step("b", ["absent/new.py"], acceptance=[{"file": "tests/t.py", "test": "T.t"}]),
                step("c", ["also/absent.py", "present.py"]),
            ]
            result = core.validate(items, root=root)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["counts"], {"missing_paths": 2, "no_acceptance": 2, "assumptions": 1})

    def a_structure_item_validates_without_a_task(self):
        self.assertEqual(
            core.STRUCTURE_ITEM_FIELDS,
            tuple(field for field in core.REQUIRED_ITEM_FIELDS if field != "task"),
        )
        without_task = {"name": "a", "files": ["a.py"], "source": None, "acceptance": []}
        result = core.validate([without_task], required=core.STRUCTURE_ITEM_FIELDS)
        self.assertEqual(result["errors"], [])
        with_task = {**without_task, "task": "do a"}
        result = core.validate([with_task], required=core.STRUCTURE_ITEM_FIELDS)
        self.assertEqual(result["errors"], [])

    def a_full_item_still_requires_a_task(self):
        without_task = {"name": "a", "files": ["a.py"], "source": None, "acceptance": []}
        errors = core.validate([without_task])["errors"]
        self.assertTrue(any("task" in e for e in errors))
        errors = core.validate([without_task], required=core.REQUIRED_ITEM_FIELDS)["errors"]
        self.assertTrue(any("task" in e for e in errors))

    def an_after_edge_to_a_missing_step_is_reported_in_structure_mode(self):
        broken = [
            {
                "name": "a",
                "files": ["a.py"],
                "source": None,
                "acceptance": [],
                "after": ["zz"],
            }
        ]
        errors = core.validate(broken, required=core.STRUCTURE_ITEM_FIELDS)["errors"]
        self.assertEqual(len(errors), 1)
        self.assertIn("zz", errors[0])
        self.assertIn("a", errors[0])

    def a_missing_required_field_is_a_message_not_a_traceback(self):
        missing = [{"name": "a", "task": "t", "source": None, "acceptance": []}]
        errors = core.validate(missing)["errors"]
        self.assertTrue(any("files" in e for e in errors))
        empty = [step("a", [])]
        self.assertTrue(any("files" in e for e in core.validate(empty)["errors"]))
        duplicate = [step("a", ["a.py"]), step("a", ["b.py"])]
        self.assertTrue(any("duplicate" in e for e in core.validate(duplicate)["errors"]))
        self.assertTrue(core.validate({"not": "a list"})["errors"])
        self.assertTrue(core.validate(["not a dict"])["errors"])


class Tiering(unittest.TestCase):
    def assumptions_block_a_simple_rating(self):
        self.assertEqual(core.tier_for(["a.py"], (), "simple", []), "cheap")
        self.assertEqual(core.tier_for(["a.py"], (), "simple", ["took the narrow reading"]), "top")

    def tier_needs_both_axes_to_be_cheap(self):
        markers = ("migrations/", "*.sql")
        self.assertEqual(core.tier_for(["src/a.py"], markers, "simple", []), "cheap")
        self.assertEqual(core.tier_for(["db/migrations/0001.py"], markers, "simple", []), "top")
        self.assertEqual(core.tier_for(["schema.sql"], markers, "simple", []), "top")
        self.assertEqual(core.tier_for(["src/a.py"], markers, "complex", []), "top")
        self.assertEqual(core.tier_for(["src/a.py"], markers, None, []), "top")
        self.assertEqual(core.tier_for(["db/migrations/0001.py"], markers, "complex", []), "top")

    def history_only_ever_raises_a_tier(self):
        regression = ({"files": ["src/a.py"], "outcome": "failed"},)
        clean = ({"files": ["src/a.py"], "outcome": "ok"},)
        self.assertEqual(core.tier_for(["src/a.py"], (), "simple", []), "cheap")
        self.assertEqual(core.tier_for(["src/a.py"], (), "simple", [], history=regression), "top")
        self.assertEqual(core.tier_for(["src/a.py"], (), "simple", [], history=clean), "cheap")
        self.assertEqual(core.tier_for(["src/a.py"], (), "complex", [], history=clean), "top")
        self.assertEqual(core.tier_for(["src/b.py"], (), "simple", [], history=regression), "cheap")
        nested = ({"files": ["src/auth/"], "outcome": "failed"},)
        self.assertEqual(core.tier_for(["src/auth/login.py"], (), "simple", [], history=nested), "top")

    def a_missing_trajectory_store_changes_nothing(self):
        import tempfile

        missing = os.path.join(tempfile.gettempdir(), "mitosis-no-such-store.jsonl")
        self.assertFalse(os.path.exists(missing))
        history = core.trajectory_store(missing)
        self.assertEqual(history, ())
        for complexity in ("simple", "complex"):
            self.assertEqual(
                core.tier_for(["src/a.py"], (), complexity, [], history=history),
                core.tier_for(["src/a.py"], (), complexity, []),
            )
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
            handle.write('{"files": ["src/a.py"], "outcome": "failed"}\n')
            handle.write("not json\n")
            handle.write('{"files": ["src/b.py"], "outcome": "ok"}\n')
            path = handle.name
        try:
            loaded = core.trajectory_store(path)
        finally:
            os.unlink(path)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(core.tier_for(["src/a.py"], (), "simple", [], history=loaded), "top")

    def same_migration_directory_is_flagged_for_review(self):
        items = [
            step("seven", ["db/migrations/0007_add_x.sql"]),
            step("eight", ["db/migrations/0008_add_y.sql"]),
            step("plain-a", ["src/a.py"]),
            step("plain-b", ["src/b.py"]),
            step("auth-a", ["src/auth/a.py"]),
            step("auth-b", ["lib/auth/b.py"]),
            step("shared-a", ["src/shared.py"]),
            step("shared-b", ["src/shared.py", "src/other.py"]),
        ]
        lanes = core.lane_items(items)
        graph = {"src/a.py": ["src/b.py"]}
        history = ({"files": ["src/a.py", "lib/auth/b.py"], "outcome": "failed"},)
        review = core.coupling_review(items, lanes, graph, risk_markers=("auth/",), history=history)
        by_pair = {tuple(entry["steps"]): entry["signals"] for entry in review}
        self.assertEqual(by_pair[("seven", "eight")], ["same-migration-directory"])
        self.assertEqual(by_pair[("plain-a", "plain-b")], ["import-adjacency"])
        self.assertEqual(by_pair[("auth-a", "auth-b")], ["shared-risk-marker"])
        self.assertEqual(by_pair[("plain-a", "auth-b")], ["recorded-regression"])
        self.assertNotIn(("shared-a", "shared-b"), by_pair)
        self.assertNotIn(("seven", "plain-a"), by_pair)
        for entry in review:
            self.assertEqual(len(entry["lanes"]), 2)
            self.assertNotEqual(entry["lanes"][0], entry["lanes"][1])
        self.assertEqual(core.coupling_review(items, lanes, None), [
            entry for entry in review if entry["signals"] == ["same-migration-directory"]
        ])

    def verify_mode_splits_serial_from_offloadable(self):
        items = [
            step("page", ["src/ui/page.tsx"]),
            step("api", ["src/api/handler.py"]),
            step("flow", ["src/api/flow.py"], acceptance=[{"file": "tests/ui/flow_test.py", "test": "F.t"}]),
        ]
        modes = core.verify_modes(items, ("ui/",))
        self.assertEqual(modes, {"page": "serial", "api": "offload", "flow": "serial"})
        self.assertEqual(set(modes.values()) <= set(core.VERIFY_MODES), True)
        self.assertEqual(core.verify_modes(items, ()), {"page": "offload", "api": "offload", "flow": "offload"})


class Cost(unittest.TestCase):
    def coalesce_never_groups_across_msps(self):
        items = [
            step("a1", ["a1.py"], msp="a"),
            step("a2", ["a2.py"], msp="a"),
            step("b1", ["b1.py"], msp="b"),
            step("b2", ["b2.py"], msp="b"),
        ]
        lanes = core.lane_items(items)
        owners = core.lane_msps(items, lanes)
        tiers = {item["name"]: "cheap" for item in items}
        groups = core.coalesce(items, lanes, tiers, budget=4)
        self.assertEqual(len(groups), 2)
        for group in groups:
            self.assertEqual(len({owners[lane] for lane in group}), 1)
        singles = [step("x", ["x.py"]), step("y", ["y.py"])]
        single_lanes = core.lane_items(singles)
        self.assertEqual(core.coalesce(singles, single_lanes, {"x": "cheap", "y": "cheap"}, budget=4), [])

    def coalesce_never_takes_a_top_tier_step(self):
        items = [
            step("c1", ["c1.py"], msp="m"),
            step("c2", ["c2.py"], msp="m"),
            step("risky", ["risky.py"], msp="m"),
            step("c3", ["c3.py"], msp="m"),
        ]
        lanes = core.lane_items(items)
        tiers = {"c1": "cheap", "c2": "cheap", "risky": "top", "c3": "cheap"}
        groups = core.coalesce(items, lanes, tiers, budget=10)
        risky = lane_of(lanes, items, "risky")
        self.assertEqual(groups, [[lane for lane in range(len(lanes)) if lane != risky]])
        dependent = [
            step("p", ["p.py"], msp="m"),
            step("q", ["q.py"], msp="m", after=["p"]),
            step("r", ["r.py"], msp="m", after=["p"]),
            step("s", ["s.py"], msp="m"),
            step("t", ["t.py"], msp="m"),
        ]
        dependent_lanes = core.lane_items(dependent)
        edges = core.lane_after(dependent, dependent_lanes)
        groups = core.coalesce(dependent, dependent_lanes, dict.fromkeys("pqrst", "cheap"), edges=edges, budget=10)
        self.assertEqual(groups, [[lane_of(dependent_lanes, dependent, "s"), lane_of(dependent_lanes, dependent, "t")]])
        budgeted = core.coalesce(items, lanes, tiers, budget=2)
        self.assertTrue(all(len(group) == 2 for group in budgeted))
        self.assertTrue(all(risky not in group for group in budgeted))

    def context_pack_reports_truncation_instead_of_hiding_it(self):
        items = [step("a", ["a.py"])]
        adjacency = {"a.py": ["n1.py", "n2.py", "n3.py", "n4.py", "n5.py"], "n1.py": ["far.py"]}
        capped = core.context_packs(items, adjacency, 1, 2)["a"]
        self.assertEqual(capped["paths"], ["a.py", "n1.py", "n2.py"])
        self.assertEqual(capped["overflow"], 3)
        full = core.context_packs(items, adjacency, 1, None)["a"]
        self.assertEqual(full["paths"], ["a.py", "n1.py", "n2.py", "n3.py", "n4.py", "n5.py"])
        self.assertEqual(full["overflow"], 0)
        two_hops = core.context_packs(items, adjacency, 2, None)["a"]
        self.assertIn("far.py", two_hops["paths"])
        self.assertEqual(two_hops["paths"][-1], "far.py")

    def context_pack_works_without_a_graph(self):
        items = [step("a", ["a.py", "b.py"]), step("c", ["c.py"])]
        packs = core.context_packs(items, None, 2, 10)
        self.assertEqual(packs, {
            "a": {"paths": ["a.py", "b.py"], "overflow": 0},
            "c": {"paths": ["c.py"], "overflow": 0},
        })
        zero_hops = core.context_packs(items, {"a.py": ["z.py"]}, 0, 10)
        self.assertEqual(zero_hops["a"]["paths"], ["a.py", "b.py"])

    def the_lane_unblocking_the_most_work_goes_first(self):
        items = [
            step("root", ["root.py"]),
            step("mid", ["mid.py"], after=["root"]),
            step("leaf", ["leaf.py"], after=["mid"]),
            step("side", ["s1.py", "s2.py", "s3.py"], after=["root"]),
        ]
        lanes = ((0,), (1,), (2,), (3,))
        edges = core.lane_after(items, lanes)
        self.assertEqual(core.lane_order(lanes, edges, items), (0, 1, 3, 2))
        self.assertEqual(core.lane_order(lanes, {}, items), (3, 0, 1, 2))
        self.assertEqual(core.item_cost(items[3]), 3)
        self.assertEqual(core.item_cost({**items[3], "cost": 7}), 7)
        self.assertEqual(core.item_cost(step("bare", ["one.py"])), 1)


SOURCE = {"path": "docs/spec.md", "sha256": "0" * 64}


def rich_items():
    return [
        step("core-vocab", ["core.py"], source=dict(SOURCE), complexity="simple",
             acceptance=[{"file": "tests/test_core.py", "test": "Vocab.declares"}]),
        step("run-dispatch", ["run.py"], source=dict(SOURCE), complexity="simple", after=["core-vocab"],
             file_notes={"run.py": "dispatch loop"}),
        step("docs-readme", ["README.md"], source=dict(SOURCE), complexity="simple", msp="docs"),
        step("docs-skill", ["SKILL.md"], source=dict(SOURCE), complexity="simple", msp="docs",
             assumptions=["the skill loads on invoke"]),
        step("docs-lint", ["tests/test_docs.py"], source=dict(SOURCE), complexity="simple", msp="docs"),
    ]


class Plan(unittest.TestCase):
    def the_plan_emits_exactly_the_declared_keys(self):
        result = core.plan(rich_items(), charter="docs/charter.md", graph={"core.py": ["run.py"]})
        self.assertEqual(set(result), set(core.PLAN_KEYS))
        self.assertEqual(list(result), [key for key in core.PLAN_KEYS if key in result])
        self.assertEqual(result["version"], core.__version__)
        self.assertEqual(result["source"], SOURCE)
        self.assertEqual(len(result["msps"]), 3)
        self.assertEqual(len(result["lanes"]), 5)
        self.assertEqual(sorted(len(c) for c in result["clusters"]), [1, 2])
        self.assertEqual(result["counts"]["assumptions"], 1)
        self.assertEqual(result["tiers"]["docs-skill"], "top")
        self.assertEqual(result["tiers"]["docs-readme"], "cheap")
        self.assertTrue(result["coupling_review"])
        self.assertTrue(result["lane_after"])
        self.assertEqual(len(result["coalesce"]), 1)

    def empty_sections_are_omitted_not_padded(self):
        result = core.plan([step("only", ["only.py"], complexity="simple")])
        self.assertTrue(set(result) < set(core.PLAN_KEYS))
        for absent in ("source", "lane_after", "coalesce", "coupling_review"):
            self.assertNotIn(absent, result)
        for key, value in result.items():
            self.assertIsNotNone(value)
            self.assertNotEqual(value, [])
            self.assertNotEqual(value, {})
        self.assertEqual(result["counts"], {"missing_paths": 1, "no_acceptance": 1, "assumptions": 0})

    def plan_id_is_stable_and_changes_with_the_plan(self):
        import json

        first = core.plan(rich_items(), charter="docs/charter.md")
        second = core.plan(rich_items(), charter="docs/charter.md")
        self.assertEqual(first["plan_id"], second["plan_id"])
        self.assertRegex(first["plan_id"], r"^[0-9a-f]{12}$")
        reloaded = json.loads(json.dumps(first))
        self.assertEqual(core.plan_id_for(reloaded), first["plan_id"])
        changed = rich_items()
        changed[1] = {**changed[1], "task": "dispatch differently"}
        self.assertNotEqual(core.plan(changed, charter="docs/charter.md")["plan_id"], first["plan_id"])
        self.assertNotEqual(core.plan(rich_items())["plan_id"], first["plan_id"])

    def the_brief_lives_in_the_plan(self):
        items = rich_items()
        result = core.plan(items, charter="docs/charter.md", graph={"core.py": ["run.py"]}, hops=1)
        self.assertEqual(len(result["briefs"]), len(result["lanes"]))
        for lane_index, brief in enumerate(result["briefs"]):
            self.assertEqual(brief["lane"], lane_index)
            self.assertEqual(brief["msp"], result["lanes"][lane_index]["msp"])
            self.assertEqual([s["name"] for s in brief["steps"]], result["lanes"][lane_index]["steps"])
            self.assertEqual(brief["charter"], "docs/charter.md")
            self.assertEqual(brief["document"], SOURCE["path"])
            self.assertEqual(brief["return_contract"], core.RETURN_CONTRACT)
            self.assertEqual(brief["text"], core.brief_text(brief))
            for s in brief["steps"]:
                self.assertIn(s["task"], brief["text"])
            self.assertIn("docs/charter.md", brief["text"])
            self.assertIn(SOURCE["path"], brief["text"])
            self.assertIn(core.RETURN_CONTRACT, brief["text"])
        by_step = {s["name"]: b for b in result["briefs"] for s in b["steps"]}
        self.assertEqual(by_step["core-vocab"]["write_set"], ["core.py"])
        self.assertEqual(by_step["core-vocab"]["read_set"], ["run.py"])
        self.assertIn("run.py", by_step["core-vocab"]["text"])
        chain = [step("first", ["x.py"], msp="m"), step("second", ["y.py"], msp="m", after=["first"])]
        chained = core.plan(chain)
        self.assertEqual(len(chained["briefs"]), 1)
        self.assertEqual([s["name"] for s in chained["briefs"][0]["steps"]], ["first", "second"])
        self.assertLess(chained["briefs"][0]["text"].index("do first"), chained["briefs"][0]["text"].index("do second"))

    def plan_touches_no_git_and_no_subprocess(self):
        import builtins
        import re
        from unittest import mock

        with open(core.__file__, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("subprocess", source)
        self.assertNotIn("os.system", source)
        self.assertNotIn("popen", source)
        self.assertIsNone(re.search(r"\bgit\b", source))
        with mock.patch.object(builtins, "open", side_effect=AssertionError("plan opened a file")):
            result = core.plan(rich_items(), charter="docs/charter.md", graph={"core.py": ["run.py"]})
        self.assertEqual(set(result), set(core.PLAN_KEYS))

    def plan_refuses_invalid_items_with_messages(self):
        with self.assertRaises(core.ValidationError) as caught:
            core.plan([step("a", ["a.py"], after=["ghost"])])
        self.assertEqual(len(caught.exception.errors), 1)
        self.assertIn("ghost", caught.exception.errors[0])
        self.assertIn("ghost", str(caught.exception))


def load_tests(loader, tests, pattern):
    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            names = [
                name
                for name, value in vars(case).items()
                if callable(value) and not name.startswith("_")
            ]
            return sorted(names)

    suite = unittest.TestSuite()
    for case in (Grouping, Validation, Tiering, Cost, Plan):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
