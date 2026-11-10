import json
import os
import sys
import tempfile
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


def planned_tier(files, complexity, history):
    return core.plan([step("a", files, complexity=complexity)], history=history)["tiers"]["a"]


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
        self.assertEqual(len(core.lane_items(unpinned)), 1)

    def an_unpinned_contract_group_is_built_by_one_worker(self):
        items = [
            step("server", ["server.py"], contract_group="g"),
            step("client", ["client.py"], contract_group="g"),
            step("docs", ["docs.py"], contract_group="g"),
        ]
        self.assertEqual(len(core.msp_items(items)), 1)
        self.assertEqual(core.lane_items(items), ((0, 1, 2),))
        self.assertEqual(core.pinned_groups(items), frozenset())

    def a_group_with_two_contract_steps_is_pinned(self):
        items = [
            step("shape-a", ["a.py"], type="contract", contract_group="g"),
            step("shape-b", ["b.py"], type="contract", contract_group="g"),
            step("user", ["u.py"], contract_group="g", after=["shape-a", "shape-b"]),
        ]
        self.assertEqual(core.pinned_groups(items), frozenset({"g"}))
        self.assertEqual(core.lane_items(items), ((0,), (1,), (2,)))

    def a_member_that_never_waits_on_the_contract_unpins_the_group(self):
        items = [
            step("iface", ["iface.py"], type="contract", contract_group="g"),
            step("server", ["server.py"], contract_group="g", after=["iface"]),
            step("client", ["client.py"], contract_group="g"),
        ]
        self.assertEqual(core.pinned_groups(items), frozenset())
        self.assertEqual(len(core.lane_items(items)), 1)

    def a_member_waiting_on_the_contract_through_another_step_unpins_the_group(self):
        items = [
            step("iface", ["iface.py"], type="contract", contract_group="g"),
            step("server", ["server.py"], contract_group="g", after=["iface"]),
            step("adapter", ["adapter.py"], contract_group="g", after=["iface"]),
            step("client", ["client.py"], contract_group="g", after=["adapter"]),
        ]
        self.assertEqual(core.pinned_groups(items), frozenset())
        self.assertEqual(len(core.msp_items(items)), 1)
        lanes = core.lane_items(items)
        self.assertEqual(lanes, ((0, 2, 1, 3),))
        self.assertEqual(core.lane_cycles(items, lanes), ())

    def a_cycle_inside_one_msp_is_contracted_so_the_plan_can_still_run(self):
        items = [
            step("core", ["core.py", "shared.py"], msp="m"),
            step("p1", ["p1.py"], msp="m", after=["core"]),
            step("p2", ["p2.py"], msp="m", after=["core"]),
            step("tail", ["tail.py", "shared.py"], msp="m", after=["p1", "p2"]),
        ]
        uncontracted = core.uncontracted_lanes(items)
        self.assertEqual(len(uncontracted), 3)
        self.assertEqual(len(core.lane_cycles(items, uncontracted)), 1)

        lanes = core.lane_items(items)
        self.assertEqual(len(lanes), 1)
        self.assertEqual(core.lane_cycles(items, lanes), ())
        walked = [items[index]["name"] for index in lanes[0]]
        self.assertEqual(walked, ["core", "p1", "p2", "tail"])

    def msps_that_wait_on_each_other_stay_two_msps(self):
        items = [
            step("core", ["core.py", "shared.py"]),
            step("mid", ["mid.py"], after=["core"]),
            step("tail", ["tail.py", "shared.py"], after=["mid"]),
        ]
        lanes = core.lane_items(items)
        self.assertEqual(core.msp_items(items), ((0, 2), (1,)))
        self.assertEqual(lanes, ((0, 2), (1,)))
        self.assertEqual(core.lane_cycles(items, lanes), ((0, 1),))

    def two_steps_that_wait_on_each_other_stay_in_two_msps_and_two_lanes(self):
        items = [step("a", ["a.py"], after=["b"]), step("b", ["b.py"], after=["a"])]
        self.assertEqual(core.msp_items(items), ((0,), (1,)))
        self.assertEqual(core.lane_items(items), ((0,), (1,)))

    def contraction_never_puts_two_msps_in_one_lane(self):
        items = [
            step("a", ["a.py", "x.py"]),
            step("b", ["b.py"], after=["a"]),
            step("c", ["c.py", "x.py"], after=["b"]),
            step("d", ["d.py"], msp="w"),
        ]
        msps = core.msp_items(items)
        owner = core._owners(msps, len(items))
        for lane in core.lane_items(items):
            self.assertEqual(len({owner[index] for index in lane}), 1)

    def a_lane_cycle_is_reported_when_fusion_turns_a_step_dag_into_a_loop(self):
        items = [
            step("a1", ["pkg/a.py"]),
            step("a2", ["pkg/a.py"]),
            step("b1", ["pkg/b.py"], after=["a1"]),
            step("b2", ["pkg/b.py"]),
            step("c", ["c.py"], after=["a1"]),
            step("d", ["d.py"], after=["b2"]),
            step("a3", ["pkg/a.py"], after=["b2"]),
        ]
        self.assertEqual(core.cycles(items), ())
        self.assertEqual(core.msp_items(items), ((0, 1, 6), (2, 3), (4,), (5,)))
        found = core.lane_cycles(items, core.uncontracted_lanes(items))
        self.assertEqual(len(found), 1)
        self.assertEqual(len(found[0]), 2)
        self.assertEqual(len(core.lane_cycles(items, core.lane_items(items))), 1)

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

    def a_full_item_still_requires_a_task(self):
        without_task = {"name": "a", "files": ["a.py"], "source": None, "acceptance": []}
        errors = core.validate([without_task])["errors"]
        self.assertTrue(any("task" in e for e in errors))
        errors = core.validate([without_task], required=core.REQUIRED_ITEM_FIELDS)["errors"]
        self.assertTrue(any("task" in e for e in errors))

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
        self.assertEqual(core.tier_for(["a.py"], (), "simple"), "cheap")
        plan = core.plan([step("a", ["a.py"], complexity="simple", assumptions=["took the narrow reading"])])
        self.assertEqual(plan["tiers"]["a"], "cheap")

    def tier_needs_both_axes_to_be_cheap(self):
        markers = ("migrations/", "*.sql")
        self.assertEqual(core.tier_for(["src/a.py"], markers, "simple"), "cheap")
        self.assertEqual(core.tier_for(["db/migrations/0001.py"], markers, "simple"), "top")
        self.assertEqual(core.tier_for(["schema.sql"], markers, "simple"), "cheap")
        self.assertEqual(core.tier_for(["db/*.sql"], markers, "simple"), "top")
        self.assertEqual(core.tier_for(["src/a.py"], markers, "complex"), "top")
        self.assertEqual(core.tier_for(["src/a.py"], markers, None), "cheap")
        self.assertEqual(core.tier_for(["src/a.py"], markers), "cheap")
        self.assertEqual(core.tier_for(["src/a.py"], markers, "LOW"), "cheap")
        self.assertEqual(core.tier_for(["src/a.py"], markers, "Mechanical"), "cheap")
        self.assertEqual(core.tier_for(["db/migrations/0001.py"], markers, "complex"), "top")

    def history_only_ever_raises_a_tier(self):
        reverted = ({"files": ["src/a.py"], "outcome": "reverted"},)
        failed = ({"files": ["src/a.py"], "outcome": "failed"},)
        clean = ({"files": ["src/a.py"], "outcome": "ok"},)
        self.assertEqual(core.tier_for(["src/a.py"], (), "simple"), "cheap")
        self.assertEqual(planned_tier(["src/a.py"], "simple", reverted), "top")
        self.assertEqual(planned_tier(["src/a.py"], "simple", failed), "cheap")
        self.assertEqual(planned_tier(["src/a.py"], "simple", clean), "cheap")
        self.assertEqual(planned_tier(["src/a.py"], "complex", clean), "top")
        self.assertEqual(planned_tier(["src/b.py"], "simple", reverted), "cheap")
        nested = ({"files": ["src/auth/"], "outcome": "reverted"},)
        self.assertEqual(planned_tier(["src/auth/login.py"], "simple", nested), "cheap")
        self.assertEqual(core.history_tier_bump(step("a", ["src/auth/login.py"]), nested), (False, ""))

    def only_a_record_that_went_wrong_raises_a_tier(self):
        def bump(*records):
            return core.history_tier_bump(step("a", ["src/a.py"]), records)

        self.assertEqual(bump({"files": ["src/a.py"], "outcome": "fixed"}), (False, ""))
        self.assertEqual(bump({"files": ["src/a.py"], "outcome": "unverified-reasoned"}), (False, ""))
        self.assertEqual(bump({"files": ["src/a.py"], "outcome": "failed"}), (False, ""))
        self.assertEqual(bump({"files": ["src/a.py"], "outcome": "reverted"}), (True, "1 reverted"))
        self.assertEqual(bump({"files": ["src/a.py"], "outcome": "speculative"}), (True, "1 speculative"))
        self.assertEqual(
            bump({"files": ["src/a.py"], "outcome": "fixed", "regressed": ["src/b.py"]}),
            (True, "1 caused-regression"),
        )
        trap = {"files": ["src/a.py"], "outcome": "fixed", "what_failed": ["patched the wrong layer"]}
        self.assertEqual(bump(trap), (False, ""))
        self.assertEqual(bump(trap, dict(trap)), (True, "2 prior traps"))

    def a_superseded_record_is_not_read(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
            handle.write('{"id": "old", "files": ["src/a.py"], "outcome": "reverted"}\n')
            handle.write('{"id": "new", "supersedes": "old", "files": ["src/a.py"], "outcome": "fixed"}\n')
            path = handle.name
        try:
            loaded = core.trajectory_store(path)
        finally:
            os.unlink(path)
        self.assertEqual([record["id"] for record in loaded], ["new"])
        self.assertEqual(planned_tier(["src/a.py"], "simple", loaded), "cheap")

    def a_recorded_breakage_couples_the_two_surfaces_and_a_fix_does_not(self):
        items = [step("a", ["src/a.py"]), step("b", ["src/b.py"])]
        lanes = core.lane_items(items)
        broke = ({"files": ["src/a.py"], "outcome": "fixed", "regressed": ["src/b.py"]},)
        fixed = ({"files": ["src/a.py", "src/b.py"], "outcome": "fixed"},)
        signals = {tuple(e["steps"]): e["signals"] for e in core.coupling_review(items, lanes, None, history=broke)}
        self.assertEqual(signals[("a", "b")], ["regression-history"])
        self.assertEqual(core.coupling_review(items, lanes, None, history=fixed), [])

    def a_marker_matches_a_mixed_case_path(self):
        self.assertTrue(core.marker_matches("app/Services/Auth/Login.php", "Services/Auth"))
        self.assertTrue(core.marker_matches("app/Services/Auth/Login.php", "services/auth"))
        self.assertTrue(core.marker_matches("db/Migrate/001.SQL", ".sql"))
        self.assertFalse(core.risk_marker_matches("DB/0004_add.SQL", "db/*.sql"))
        self.assertTrue(core.risk_marker_matches("legacy/DB/*.SQL.bak", "db/*.sql"))
        self.assertTrue(core.risk_marker_matches("app/types/AuthResponse.ts", "auth"))
        self.assertFalse(core.risk_marker_matches("src/waverider.py", "wav"))
        self.assertTrue(core.marker_matches("DB/0004_add.SQL", "db/*.sql"))
        self.assertTrue(core.marker_matches("app/types/AuthResponse.ts", "auth"))
        self.assertFalse(core.marker_matches("src/waverider.py", "wav"))
        self.assertEqual(
            core.tier_for(["app/Services/Auth/Login.php"], ["Services/Auth"], "simple"), "top"
        )

    def a_missing_trajectory_store_changes_nothing(self):
        import tempfile

        missing = os.path.join(tempfile.gettempdir(), "mitosis-no-such-store.jsonl")
        self.assertFalse(os.path.exists(missing))
        history = core.trajectory_store(missing)
        self.assertEqual(history, ())
        for complexity, expected in (("simple", "cheap"), ("complex", "top")):
            self.assertEqual(planned_tier(["src/a.py"], complexity, history), expected)
            self.assertEqual(core.tier_for(["src/a.py"], (), complexity), expected)
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
            handle.write('{"files": ["src/a.py"], "outcome": "failed"}\n')
            handle.write("not json\n")
            handle.write('{"files": ["src/b.py"], "outcome": "ok"}\n')
            path = handle.name
        try:
            loaded = core.trajectory_store(path)
        finally:
            os.unlink(path)
        self.assertEqual(
            list(loaded),
            [{"files": ["src/a.py"], "outcome": "failed"}, {"files": ["src/b.py"], "outcome": "ok"}],
        )
        self.assertEqual(planned_tier(["src/a.py"], "simple", loaded), "cheap")

    def same_migration_directory_is_flagged_for_review(self):
        items = [
            step("seven", ["db/migrations/0007_add_x.py"]),
            step("eight", ["db/migrations/0008_add_y.py"]),
            step("sql-seven", ["db/0007_a.sql"]),
            step("sql-eight", ["db/0008_b.sql"]),
            step("root-one", ["migrations/0001_one.py"]),
            step("root-two", ["migrations/0002_two.py"]),
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
        by_pair = {tuple(entry["steps"]): (entry["signals"], entry["default"]) for entry in review}
        self.assertEqual(by_pair[("seven", "eight")], (["same-migration-app:db"], "serialize"))
        self.assertEqual(by_pair[("root-one", "root-two")], (["same-migration-app:<root>"], "serialize"))
        self.assertEqual(by_pair[("plain-a", "plain-b")], (["import-adjacent"], "parallel"))
        self.assertEqual(by_pair[("auth-a", "auth-b")], (["shared-risk-marker:auth/"], "serialize"))
        self.assertNotIn(("sql-seven", "sql-eight"), by_pair)
        self.assertNotIn(("plain-a", "auth-b"), by_pair)
        self.assertNotIn(("shared-a", "shared-b"), by_pair)
        self.assertNotIn(("seven", "plain-a"), by_pair)
        self.assertEqual(len(review), 4)
        for entry in review:
            self.assertEqual(len(entry["lanes"]), 2)
            self.assertNotEqual(entry["lanes"][0], entry["lanes"][1])
        self.assertEqual(
            [(tuple(entry["steps"]), entry["signals"]) for entry in core.coupling_review(items, lanes, None)],
            [
                (("seven", "eight"), ["same-migration-app:db"]),
                (("root-one", "root-two"), ["same-migration-app:<root>"]),
            ],
        )

    def a_strong_coupling_signal_flags_the_pair_serialize_and_an_import_link_leaves_it_parallel(self):
        items = [
            step("seven", ["db/migrations/0007_add_x.py"]),
            step("eight", ["db/migrations/0008_add_y.py"]),
            step("plain-a", ["src/a.py"]),
            step("plain-b", ["src/b.py"]),
            step("auth-a", ["src/auth/a.py"]),
            step("auth-b", ["lib/auth/b.py"]),
        ]
        history = ({"files": ["src/a.py"], "outcome": "fixed", "regressed": ["src/auth/a.py"]},)
        plan = core.plan(items, graph={"src/a.py": ["src/b.py"]}, risk_markers=("auth/",), history=history)
        self.assertNotIn("coupling_order", plan)
        self.assertNotIn("lane_after", plan)
        self.assertFalse(any("after" in item for item in plan["items"]))
        review = {tuple(entry["steps"]): (entry["signals"], entry["default"]) for entry in plan["coupling_review"]}
        self.assertEqual(
            review,
            {
                ("seven", "eight"): (["same-migration-app:db"], "serialize"),
                ("plain-a", "plain-b"): (["import-adjacent"], "parallel"),
                ("plain-a", "auth-a"): (["regression-history"], "serialize"),
                ("auth-a", "auth-b"): (["shared-risk-marker:auth/"], "serialize"),
            },
        )
        self.assertFalse(any("after" in item for item in items))

    def an_import_listed_only_under_the_later_step_is_import_adjacent(self):
        items = [step("plain-a", ["src/app/a.py"]), step("plain-b", ["src/app/b.py"])]
        lanes = core.lane_items(items)
        self.assertEqual(
            core.coupling_review(items, lanes, {"app/b.py": ["app/a.py"]}),
            [{"steps": ["plain-a", "plain-b"], "lanes": [0, 1], "signals": ["import-adjacent"], "default": "parallel"}],
        )

    def verdict_groups_bucket_pairs_by_signals_and_default_biggest_first(self):
        review = [
            {"steps": ["a", "b"], "lanes": [0, 1], "signals": ["import-adjacent"], "default": "parallel"},
            {"steps": ["c", "d"], "lanes": [2, 3], "signals": ["shared-risk-marker:auth"], "default": "serialize"},
            {"steps": ["c", "e"], "lanes": [2, 4], "signals": ["shared-risk-marker:auth"], "default": "serialize"},
        ]
        self.assertEqual(
            core.verdict_groups(review),
            [
                {
                    "signals": ["shared-risk-marker:auth"],
                    "default": "serialize",
                    "count": 2,
                    "pairs": [["c", "d"], ["c", "e"]],
                },
                {"signals": ["import-adjacent"], "default": "parallel", "count": 1, "pairs": [["a", "b"]]},
            ],
        )
        self.assertEqual(core.verdict_groups([]), [])

    def a_pair_already_ordered_by_an_after_edge_gets_no_verdict(self):
        items = [step("a", ["src/a.py"]), step("b", ["src/b.py"], after=["a"])]
        lanes = core.lane_items(items)
        self.assertEqual(core.coupling_review(items, lanes, {"src/a.py": ["src/b.py"]}), [])

    def verify_mode_splits_serial_from_offloadable(self):
        items = [
            step("page", ["src/ui/page.tsx"]),
            step("api", ["src/api/handler.py"]),
            step("flow", ["src/api/flow.py"], acceptance=[{"file": "tests/ui/flow_test.py", "test": "F.t"}]),
        ]
        modes = core.verify_modes(items, ("ui/",))
        self.assertEqual(modes, {"page": "serial", "api": "offload", "flow": "offload"})
        self.assertEqual(
            core.verify_modes([step("x", ["app/unauthenticated/x.ts"])], ("auth",)), {"x": "serial"}
        )
        self.assertEqual(core.verify_modes([step("x", ["src/UI/page.tsx"])], ("ui/",)), {"x": "offload"})
        self.assertEqual(set(modes.values()) <= set(core.VERIFY_MODES), True)
        self.assertEqual(core.verify_modes(items, ()), {"page": "offload", "api": "offload", "flow": "offload"})


class Cost(unittest.TestCase):
    def coalesce_groups_cheap_steps_of_one_wave_by_name_whatever_their_msp(self):
        items = [
            step("a1", ["a1.py"], msp="a", complexity="simple"),
            step("a2", ["a2.py"], msp="a", complexity="simple"),
            step("b1", ["b1.py"], msp="b", complexity="simple"),
            step("b2", ["b2.py"], msp="b", complexity="simple"),
        ]
        self.assertEqual(core.plan(items)["coalesce"], {"0": [["a1", "a2", "b1", "b2"]]})
        self.assertEqual(core.plan(items, coalesce_max=3)["coalesce"], {"0": [["a1", "a2", "b1"]]})
        self.assertNotIn("coalesce", core.plan(items, coalesce_below=0))
        wide = [step("w%d" % i, ["w%d.py" % i, "v%d.py" % i, "u%d.py" % i], complexity="simple") for i in range(2)]
        self.assertNotIn("coalesce", core.plan(wide))

    def coalesce_never_takes_a_top_tier_step(self):
        items = [
            step("c1", ["c1.py"], msp="m"),
            step("c2", ["c2.py"], msp="m"),
            step("risky", ["risky.py"], msp="m"),
            step("c3", ["c3.py"], msp="m"),
        ]
        result = core.plan([{**item, "complexity": "simple"} for item in items], risk_markers=("risky",))
        self.assertEqual(result["tiers"]["risky"], "top")
        self.assertEqual(result["coalesce"], {"0": [["c1", "c2", "c3"]]})
        dependent = [
            step("p", ["p.py"], msp="m", complexity="simple"),
            step("q", ["q.py"], msp="m", after=["p"], complexity="simple"),
            step("r", ["r.py"], msp="m", after=["p"], complexity="simple"),
            step("s", ["s.py"], msp="m", complexity="simple"),
            step("t", ["t.py"], msp="m", complexity="simple"),
        ]
        self.assertEqual(core.plan(dependent)["coalesce"], {"0": [["p", "s", "t"]], "1": [["q", "r"]]})

    def context_pack_reports_truncation_instead_of_hiding_it(self):
        items = [step("a", ["a.py"])]
        adjacency = {"a.py": ["n5.py", "n1.py", "n4.py", "n2.py", "n3.py"], "n1.py": ["far.py"]}
        capped = core.context_packs(items, adjacency, 1, 2)["a"]
        self.assertEqual(capped, {"edit": ["a.py"], "read": ["n1.py", "n2.py"], "read_truncated": 3})
        full = core.context_packs(items, adjacency, 1, 40)["a"]
        self.assertEqual(full, {"edit": ["a.py"], "read": ["n1.py", "n2.py", "n3.py", "n4.py", "n5.py"]})
        two_hops = core.context_packs(items, adjacency, 2, 40)["a"]
        self.assertEqual(
            two_hops,
            {"edit": ["a.py"], "read": ["far.py", "n1.py", "n2.py", "n3.py", "n4.py", "n5.py"]},
        )

    def context_pack_works_without_a_graph(self):
        items = [step("a", ["a.py", "b.py"]), step("c", ["c.py"])]
        packs = core.context_packs(items, None, 2, 10)
        self.assertEqual(packs, {"a": {"edit": ["a.py", "b.py"]}, "c": {"edit": ["c.py"]}})
        zero_hops = core.context_packs(items, {"a.py": ["z.py"]}, 0, 10)
        self.assertEqual(zero_hops["a"], {"edit": ["a.py", "b.py"]})

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


RICH_GRAPH = {"core.py": ["run.py"], "README.md": ["SKILL.md"]}


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
        result = core.plan(rich_items(), charter="docs/charter.md", graph=RICH_GRAPH)
        self.assertEqual(
            set(result),
            set(core.PLAN_KEYS)
            - {
                "locked",
                "check_authors",
                "folded_check_authors",
                "tier_notes",
                "shared_context",
                "shared_files",
                "integration_order",
                "shared_file_candidates",
            },
        )
        self.assertEqual(list(result), [key for key in core.PLAN_KEYS if key in result])
        for gone in ("version", "clusters", "session_tokens", "sizes"):
            self.assertNotIn(gone, core.PLAN_KEYS)
        self.assertEqual(result["source"], SOURCE)
        self.assertEqual(len(result["msps"]), 3)
        self.assertEqual(len(result["lanes"]), 5)
        self.assertEqual(result["counts"]["assumptions"], 1)
        self.assertEqual(result["tiers"]["docs-skill"], "cheap")
        self.assertEqual(result["tiers"]["docs-readme"], "cheap")
        self.assertTrue(result["coupling_review"])
        self.assertTrue(result["lane_after"])
        self.assertEqual(
            result["coalesce"], {"0": [["core-vocab", "docs-readme", "docs-skill", "docs-lint"]]}
        )

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
            self.assertIn(brief["spec"], brief["text"])
            self.assertIn("Edit ONLY the files listed under `edit`.", brief["text"])
            self.assertNotIn("Write-set for this Lane", brief["text"])
            self.assertIn("docs/charter.md", brief["text"])
            self.assertIn(SOURCE["path"], brief["text"])
            self.assertIn(core.RETURN_CONTRACT, brief["text"])
        by_step = {s["name"]: b for b in result["briefs"] for s in b["steps"]}
        self.assertEqual(by_step["core-vocab"]["write_set"], ["core.py"])
        self.assertEqual(by_step["core-vocab"]["read_set"], ["run.py"])
        self.assertIn("run.py", by_step["core-vocab"]["text"])
        chain = [step("first", ["x.py"], msp="m"), step("second", ["y.py"], msp="m", after=["first"])]
        chained = core.plan(chain)
        self.assertEqual([[s["name"] for s in b["steps"]] for b in chained["briefs"]], [["first", "second"]])

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
            result = core.plan(rich_items(), charter="docs/charter.md", graph=RICH_GRAPH)
        self.assertEqual(
            set(result),
            set(core.PLAN_KEYS)
            - {
                "locked",
                "check_authors",
                "folded_check_authors",
                "tier_notes",
                "shared_context",
                "shared_files",
                "integration_order",
                "shared_file_candidates",
            },
        )

    def a_shape_only_plan_holds_no_briefs(self):
        shaped = core.plan([check_author(), calc_builder()], briefs=False)
        self.assertNotIn("briefs", shaped)
        self.assertNotIn("check_authors", shaped)
        self.assertEqual(shaped["lanes"][0]["steps"], ["add"])
        self.assertEqual(shaped["plan_id"], core.plan_id_for(shaped))

    def plan_refuses_invalid_items_with_messages(self):
        with self.assertRaises(core.ValidationError) as caught:
            core.plan([step("a", ["a.py"], after=["ghost"])])
        self.assertEqual(len(caught.exception.errors), 1)
        self.assertIn("ghost", caught.exception.errors[0])
        self.assertIn("ghost", str(caught.exception))


def node_link(links):
    nodes = [
        {"id": "a1", "file_type": "code", "source_file": "pkg/a.py"},
        {"id": "a2", "file_type": "code", "source_file": "pkg/a.py"},
        {"id": "b1", "file_type": "code", "source_file": "pkg/b.py"},
        {"id": "c1", "source_file": "pkg/c.py"},
        {"id": "doc", "file_type": "document", "source_file": "README.md"},
        {"id": "ext", "file_type": "concept", "source_file": ""},
    ]
    return {"directed": True, "nodes": nodes, "links": [
        {"source": source, "target": target, "relation": "calls"} for source, target in links
    ]}


class ReadSetCap(unittest.TestCase):
    def a_hub_file_fills_no_brief_past_the_default_cap(self):
        graph = {"hub.py": ["n%03d.py" % n for n in range(120)]}
        plan = core.plan([step("edit-hub", ["hub.py"])], graph=graph)
        brief = plan["briefs"][0]
        self.assertEqual(len(brief["read_set"]), core.CONTEXT_CAP)
        self.assertEqual(brief["read_overflow"], 120 - core.CONTEXT_CAP)
        self.assertNotIn("more not shown", brief["text"])
        self.assertIn('"read": [\n        "n000.py",', brief["text"])
        self.assertNotIn('"n%03d.py"' % core.CONTEXT_CAP, brief["text"])
        self.assertEqual(core.CONTEXT_CAP, 40)

    def every_step_of_a_lane_is_capped_on_its_own(self):
        graph = {
            "a.py": ["a%02d.py" % n for n in range(50)],
            "b.py": ["b%02d.py" % n for n in range(30)],
        }
        items = [step("a", ["a.py"], msp="m"), step("b", ["b.py"], msp="m", after=["a"])]
        plan = core.plan(items, graph=graph)
        self.assertEqual(lane_steps(plan), [["a", "b"]])
        brief = plan["briefs"][0]
        self.assertEqual([len(leaf["read"]) for leaf in brief["steps"]], [core.CONTEXT_CAP, 30])
        self.assertEqual(brief["steps"][1]["read"][0], "b00.py")
        self.assertEqual(brief["read_overflow"], 50 - core.CONTEXT_CAP)

    def an_explicit_cap_is_still_honoured(self):
        graph = {"hub.py": ["n%03d.py" % n for n in range(12)]}
        brief = core.plan([step("edit-hub", ["hub.py"])], graph=graph, cap=5)["briefs"][0]
        self.assertEqual((len(brief["read_set"]), brief["read_overflow"]), (5, 7))

    def a_check_author_brief_keeps_each_steps_capped_read_list_whole(self):
        graph = {
            "tests/test_a.py": ["n1.py", "n2.py", "n5.py"],
            "tests/test_b.py": ["n3.py", "n4.py", "n6.py"],
        }
        items = [
            step("a", ["a.py", "shared.py"], msp="x", acceptance=[{"file": "tests/test_a.py", "test": "a_works"}]),
            step("b", ["b.py", "shared.py"], msp="y", acceptance=[{"file": "tests/test_b.py", "test": "b_works"}]),
            step("check-author-x", ["tests/test_a.py"], msp="x", role="check-author"),
            step("check-author-y", ["tests/test_b.py"], msp="y", role="check-author"),
        ]
        authors = core.plan(items, graph=graph, cap=2)["check_authors"]
        self.assertEqual(len(authors), 1)
        self.assertEqual(authors[0]["brief"]["read_set"], ["n1.py", "n2.py", "n3.py", "n4.py"])
        self.assertEqual(authors[0]["brief"]["read_overflow"], 2)


class OwnClone(unittest.TestCase):
    def every_lane_brief_says_the_worker_builds_in_its_own_clone(self):
        plan = core.plan([step("a", ["a.py"]), step("b", ["b.py"], msp="m"), step("c", ["c.py"], msp="m")])
        for brief in plan["briefs"]:
            self.assertIn(core.CLONE_LINE, brief["text"])
            self.assertIn("changes the repository's index or history", brief["text"])
            self.assertNotIn("same worktree", brief["text"])
            self.assertNotIn("Other agents are working in this tree", brief["text"])
            self.assertIn("Edit ONLY the files listed under `edit`.\n\n" + core.CLONE_LINE, brief["text"])


class GraphInput(unittest.TestCase):
    LINKS = [("a1", "b1"), ("a2", "a1"), ("doc", "a1"), ("ext", "b1"), ("b1", "c1"), ("a1", "gone")]

    def _coupling(self):
        graph = node_link(self.LINKS)
        return core.build_file_coupling({node["id"]: node for node in graph["nodes"]}, graph["links"])

    def links_between_files_become_neighbours_both_ways_whatever_the_node_type(self):
        self.assertEqual(
            self._coupling(),
            {
                "pkg/a.py": {"pkg/b.py", "README.md"},
                "pkg/b.py": {"pkg/a.py", "pkg/c.py"},
                "README.md": {"pkg/a.py"},
                "pkg/c.py": {"pkg/b.py"},
            },
        )

    def a_read_set_reaches_a_neighbour_named_only_by_a_node_link_graph(self):
        adjacency = {path: sorted(neighbours) for path, neighbours in self._coupling().items()}
        plan = core.plan([step("edit-c", ["pkg/c.py"])], graph=adjacency)
        self.assertEqual(plan["briefs"][0]["read_set"], ["pkg/b.py"])
        self.assertEqual(plan["context_packs"]["edit-c"], {"edit": ["pkg/c.py"], "read": ["pkg/b.py"]})


class SharedContext(unittest.TestCase):
    def a_file_two_steps_of_one_wave_read_is_shared_under_that_wave(self):
        packs = {
            "a": {"edit": ["a.py"], "read": ["hub.py", "x.py"]},
            "b": {"edit": ["b.py"], "read": ["hub.py"]},
            "c": {"edit": ["c.py"], "read": ["x.py"]},
        }
        self.assertEqual(core.shared_context(packs, [["a", "b"], ["c"]]), {"0": ["hub.py"]})
        self.assertEqual(core.shared_context(packs, [["a"], ["b", "c"]]), {})

    def a_plan_carries_shared_context_only_when_some_wave_shares_a_file(self):
        graph = {"a.py": ["hub.py"], "c.py": ["hub.py"], "hub.py": ["a.py", "c.py"]}
        shared = core.plan([step("a", ["a.py"]), step("c", ["c.py"])], graph=graph)
        self.assertEqual(shared["shared_context"], {"0": ["hub.py"]})
        alone = core.plan([step("a", ["a.py"])], graph=graph)
        self.assertNotIn("shared_context", alone)


def shared_lane(count, **extra):
    return [step("s%d" % i, ["shared.py", "f%d.py" % i], **extra) for i in range(count)]


def lane_steps(result):
    return [lane["steps"] for lane in result["lanes"]]


class OneSessionPerLane(unittest.TestCase):
    def steps_sharing_a_file_are_one_lane_with_one_brief_that_walks_them_in_order(self):
        result = core.plan(shared_lane(3))
        self.assertEqual(lane_steps(result), [["s0", "s1", "s2"]])
        self.assertNotIn("session", result["lanes"][0])
        self.assertNotIn("lane_after", result)
        brief = result["briefs"][0]
        self.assertEqual(brief["write_set"], ["shared.py", "f0.py", "f1.py", "f2.py"])
        self.assertEqual([s["name"] for s in brief["steps"]], ["s0", "s1", "s2"])
        self.assertNotIn("session", brief)
        self.assertNotIn("Session ", brief["text"])
        text = brief["text"]
        self.assertLess(text.index('"task": "do s0"'), text.index('"task": "do s1"'))
        self.assertLess(text.index('"task": "do s1"'), text.index('"task": "do s2"'))

    def a_lane_takes_its_steps_in_dependency_order(self):
        items = [
            step("late", ["shared.py", "late.py"], after=["early"]),
            step("early", ["shared.py", "early.py"]),
            step("middle", ["shared.py", "middle.py"], after=["early"]),
        ]
        result = core.plan(items)
        self.assertEqual(lane_steps(result), [["early", "late", "middle"]])
        self.assertNotIn("lane_after", result)

    def a_lane_takes_the_top_tier_any_of_its_steps_needs(self):
        items = shared_lane(2, complexity="simple") + [
            step("risky", ["shared.py", "auth.py"], complexity="simple")
        ]
        result = core.plan(items, risk_markers=("auth",))
        self.assertEqual([lane["tier"] for lane in result["lanes"]], ["top"])
        self.assertEqual(result["tiers"]["s0"], "cheap")

    def a_lane_waiting_on_a_step_of_a_shared_lane_waits_for_that_lane(self):
        items = shared_lane(3) + [step("after", ["after.py"], after=["s0"])]
        result = core.plan(items)
        consumer = next(i for i, lane in enumerate(result["lanes"]) if lane["steps"] == ["after"])
        producer = next(i for i, lane in enumerate(result["lanes"]) if "s0" in lane["steps"])
        self.assertEqual(result["lane_after"][str(consumer)], [producer])

    def a_coupling_pair_names_the_lanes_its_steps_run_in(self):
        items = shared_lane(3) + [step("near", ["near.py"])]
        result = core.plan(items, graph={"f2.py": ["near.py"]})
        pairs = result["coupling_review"]
        self.assertEqual([pair["steps"] for pair in pairs], [["s2", "near"]])
        self.assertEqual(pairs[0]["lanes"], [0, 1])

    def steps_of_one_lane_are_never_reviewed_as_a_coupled_pair(self):
        items = [
            step("a", ["shared.py", "a.py"]),
            step("b", ["shared.py", "b.py"]),
        ]
        result = core.plan(items, graph={"a.py": ["b.py"]})
        self.assertEqual(len(result["lanes"]), 1)
        self.assertNotIn("coupling_review", result)


class Referee(unittest.TestCase):
    def _planned(self, charter_text, items):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "charter.md"), "w", encoding="utf-8") as handle:
                handle.write(charter_text)
            return core.plan(items, charter="charter.md", root=root)

    def locked_paths_reads_the_locked_lines_in_order_once_each(self):
        import referee

        text = "# Charter\n- Locked: `tests/b.py`\n- Locked: tests\\a.py \n- Locked: tests/b.py\n- Other: x\n"
        self.assertEqual(referee.locked_paths(text), ("tests/b.py", "tests/a.py"))

    def locked_paths_refuses_an_absolute_or_climbing_path(self):
        import referee

        for line in ("- Locked: /etc/passwd", "- Locked: tests/../core.py"):
            with self.subTest(line=line):
                with self.assertRaises(ValueError) as caught:
                    referee.locked_paths("# Charter\n" + line + "\n")
                self.assertIn(line.split(": ", 1)[1], str(caught.exception))

    def referee_reason_names_the_lock_or_the_first_marker(self):
        import referee

        locked = ("tests/test_locked.py",)
        self.assertEqual(referee.referee_reason("tests/test_locked.py", locked), "locked by the charter")
        self.assertEqual(
            referee.referee_reason("web/jest.config.js", ()), "matches the referee marker *jest.config.*"
        )
        self.assertEqual(
            referee.referee_reason("tests/perf_baseline.json", ()), "matches the referee marker *baseline*"
        )
        self.assertEqual(
            referee.referee_reason("src/calc.test.ts.snap", ()), "matches the referee marker *.snap"
        )
        self.assertIsNone(referee.referee_reason("src/auth/ip_allowlist.py", ()))
        self.assertIsNone(referee.referee_reason("perf_baseline.json", ()))
        self.assertIsNone(referee.referee_reason("calc.py", locked))

    def in_test_location_reads_folders_and_test_file_names(self):
        import referee

        for path in ("tests/x.json", "src/__tests__/a.js", "e2e/flow.ts", "test_calc.py", "calc_test.go", "a.spec.ts"):
            self.assertTrue(referee.in_test_location(path), path)
        for path in ("src/calc.py", "contest/x.py", "docs/testing.md"):
            self.assertFalse(referee.in_test_location(path), path)

    def the_plan_holds_the_locked_list_and_hands_a_locked_acceptance_file_read_only(self):
        result = self._planned(
            "- Locked: tests/test_locked.py\n",
            [
                step(
                    "build",
                    ["calc.py", "tests/test_locked.py"],
                    acceptance=[{"file": "tests/test_locked.py", "test": "Locked.works"}],
                )
            ],
        )
        self.assertEqual(result["locked"], ["tests/test_locked.py"])
        self.assertEqual(result["msps"][0]["files"], ["calc.py"])
        self.assertEqual(result["items"][0]["files"], ["calc.py"])
        brief = result["briefs"][0]
        self.assertEqual(brief["write_set"], ["calc.py"])
        self.assertEqual(brief["locked"], ["tests/test_locked.py"])
        self.assertIn(
            "The locked checks tests/test_locked.py are read-only. Never write them or any test "
            "configuration, snapshot, baseline, allowlist or skip list: mitosis reverts such a "
            "write and holds the MSP back.",
            brief["text"],
        )
        self.assertIn("Task: do build", brief["text"].splitlines())
        self.assertIn('"edit": [\n        "calc.py"\n      ],', brief["text"])
        self.assertIn("Edit ONLY the files listed under `edit`.", brief["text"])
        self.assertNotIn("Write-set for this Lane", brief["text"])

    def a_brief_with_nothing_locked_forbids_only_the_referee_files(self):
        text = core.plan([step("build", ["calc.py", "tests/test_calc.py"])])["briefs"][0]["text"]
        self.assertIn(
            "Never write any test configuration, snapshot, baseline, allowlist or skip list: mitosis "
            "reverts such a write and holds the MSP back.",
            text,
        )
        self.assertNotIn("Never write them", text)

    def the_plan_refuses_a_step_writing_a_referee_path(self):
        with self.assertRaises(core.ValidationError) as caught:
            self._planned(
                "- Locked: tests/test_locked.py\n",
                [step("build", ["calc.py", "tests/test_locked.py"]), step("conf", ["tests/conftest.py"])],
            )
        self.assertEqual(
            caught.exception.errors,
            (
                "build: writes tests/test_locked.py, which locked by the charter; no Step may "
                "write the referee",
                "conf: writes tests/conftest.py, which matches the referee marker conftest.py; no "
                "Step may write the referee",
            ),
        )

    def a_bad_locked_line_is_a_validation_error(self):
        with self.assertRaises(core.ValidationError) as caught:
            self._planned("- Locked: ../outside.py\n", [step("build", ["calc.py"])])
        self.assertEqual(len(caught.exception.errors), 1)
        self.assertIn("../outside.py", caught.exception.errors[0])


def check_author(name="write-checks", files=("tests/test_calc.py",), **extra):
    return step(name, files, role="check-author", **extra)


def calc_builder(files=("calc.py",), after=("write-checks",)):
    return step(
        "add",
        files,
        msp="calc",
        acceptance=[{"file": "tests/test_calc.py", "test": "Calc.adds"}],
        after=list(after),
    )


def chained_plan():
    return {
        "msps": [{"files": ["a.py"]}, {"files": ["b.py"]}, {"files": ["c.py"]}, {"files": ["d.py"]}],
        "lanes": [{"msp": 0}, {"msp": 1}, {"msp": 2}, {"msp": 3}],
        "lane_after": {"1": [0], "2": [1]},
    }


class CheckAuthor(unittest.TestCase):
    def a_check_author_is_the_lock_stage_and_never_a_lane(self):
        result = core.plan([check_author(), calc_builder()])
        self.assertEqual([msp["label"] for msp in result["msps"]], ["calc"])
        self.assertEqual([lane["steps"] for lane in result["lanes"]], [["add"]])
        self.assertEqual([item["name"] for item in result["items"]], ["add"])
        self.assertNotIn("after", result["items"][0])
        self.assertEqual(result["locked"], ["tests/test_calc.py"])
        self.assertEqual(
            [
                (entry["msp"], entry["label"], entry["name"], entry["slice"])
                for entry in result["check_authors"]
            ],
            [(0, "calc", "write-checks", ["tests/test_calc.py"])],
        )

    def the_check_author_brief_names_its_files_and_probe_files_without_the_referee_line(self):
        text = core.plan([check_author(), calc_builder()])["check_authors"][0]["brief"]["text"]
        self.assertTrue(text.startswith("Lane check-author of MSP calc\n"))
        self.assertIn("Task: do write-checks", text.splitlines())
        self.assertIn(
            json.dumps(
                {
                    "leaves": [
                        {
                            "name": "write-checks",
                            "edit": ["tests/test_calc.py"],
                            "read": [],
                        }
                    ]
                },
                indent=2,
            ),
            text,
        )
        self.assertIn("Edit ONLY the files listed under `edit`.", text)
        self.assertNotIn("Write-set for this Lane", text)
        self.assertIn("Probe files, the only files your probe may write: calc.py (and no others).", text)
        self.assertIn("leave the probe in place in the clone and never commit it", text)
        self.assertNotIn("are read-only", text)
        self.assertNotIn("mitosis reverts such a write", text)

    def charter_locks_come_first_and_duplicates_drop(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "charter.md"), "w", encoding="utf-8") as handle:
                handle.write("- Locked: tests/test_old.py\n- Locked: tests/test_calc.py\n")
            result = core.plan([check_author(), calc_builder()], charter="charter.md", root=root)
        self.assertEqual(result["locked"], ["tests/test_old.py", "tests/test_calc.py"])
        brief = result["check_authors"][0]["brief"]
        self.assertEqual(brief["locked"], ["tests/test_old.py"])
        self.assertIn("The locked checks tests/test_old.py are read-only.", brief["text"])

    def the_check_author_is_inside_the_plan_id(self):
        first = core.plan([check_author(), calc_builder()])
        changed = core.plan([check_author(task="write other checks"), calc_builder()])
        self.assertNotEqual(first["plan_id"], changed["plan_id"])

    def a_malformed_check_author_is_refused_naming_the_step(self):
        cases = (
            (
                [
                    check_author("lock-one"),
                    check_author("lock-two", files=("tests/test_two.py",)),
                    calc_builder(after=("lock-one", "lock-two")),
                ],
                "more than one Step has role check-author and no msp: lock-one, lock-two",
            ),
            (
                [
                    check_author("lock-one", msp="calc"),
                    check_author("lock-two", files=("tests/test_two.py",), msp="calc"),
                    calc_builder(after=("lock-one", "lock-two")),
                ],
                "lock-one, lock-two: 2 check-author Steps carry msp calc, and one MSP's label "
                "holds one check-author",
            ),
            (
                [
                    check_author("lock-one", msp="calc"),
                    check_author("lock-two", msp="other"),
                    calc_builder(after=("lock-one", "lock-two")),
                ],
                "lock-two: lists tests/test_calc.py in its files, which the check-author Step "
                "lock-one also writes",
            ),
            (
                [
                    check_author(acceptance=[{"file": "tests/test_calc.py", "test": "Calc.adds"}]),
                    calc_builder(),
                ],
                "write-checks: a check-author Step writes the locked checks and declares no "
                "acceptance entries, but it declares 1",
            ),
            (
                [step("add", ["calc.py"], role="builder")],
                "add: role 'builder' is not one of check-author",
            ),
            (
                [check_author(), calc_builder(files=("calc.py", "tests/test_calc.py"))],
                "add: lists tests/test_calc.py in its files, which the check-author Step "
                "write-checks owns as a locked check",
            ),
        )
        for items, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(core.validate(items)["errors"], [expected])
                with self.assertRaises(core.ValidationError) as raised:
                    core.plan(items)
                self.assertIn(expected, raised.exception.errors)

    def a_check_author_file_is_refused_when_no_one_msp_can_lock_it(self):
        cases = (
            (
                [check_author(files=("tests/test_calc.py", "tests/test_other.py")), calc_builder()],
                "write-checks: writes tests/test_other.py, which no Step's acceptance names, so "
                "no MSP can lock it",
            ),
            (
                [check_author(msp="gone"), calc_builder()],
                "write-checks: carries msp gone, which names no MSP: no Step carries msp gone",
            ),
        )
        for items, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(core.ValidationError) as raised:
                    core.plan(items)
                self.assertEqual(raised.exception.errors, (expected,))

    def a_builder_may_name_a_check_author_file_as_its_acceptance(self):
        self.assertEqual(core.validate([check_author(), calc_builder()])["errors"], [])

    def the_lock_tier_joins_the_plan_tiers(self):
        with_author = core.plan([check_author(), calc_builder()])
        without = core.plan([calc_builder(after=())])
        self.assertEqual(core.plan_tiers(with_author), ("cheap", "top"))
        self.assertEqual(core.plan_tiers(without), ("cheap",))
        cheap = core.plan([step("a", ["a.py"], complexity="simple")])
        self.assertEqual(core.plan_tiers(cheap), ("cheap",))
        cheap_locked = core.plan(
            [
                check_author(),
                step(
                    "a",
                    ["a.py"],
                    complexity="simple",
                    acceptance=[{"file": "tests/test_calc.py", "test": "Calc.adds"}],
                    after=["write-checks"],
                ),
            ]
        )
        self.assertEqual(core.plan_tiers(cheap_locked), ("cheap", "top"))

    def an_msp_merges_every_producer_directly_or_through_another(self):
        plan = chained_plan()
        self.assertEqual(core.msp_producers(plan), {0: (), 1: (0,), 2: (1,), 3: ()})
        self.assertEqual(core.msp_merges(plan), {0: (), 1: (0,), 2: (0, 1), 3: ()})
        self.assertEqual(core.msp_mergers(plan), {0: (1, 2), 1: (2,), 2: (), 3: ()})
        self.assertEqual(core.probe_files(plan, 2), ("c.py", "a.py", "b.py"))
        self.assertEqual(core.probe_files(plan, 3), ("d.py",))


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
    for case in (Grouping, Validation, Tiering, Cost, Plan, GraphInput, SharedContext, OwnClone, ReadSetCap, OneSessionPerLane, Referee, CheckAuthor):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
