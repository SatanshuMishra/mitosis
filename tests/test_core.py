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
        self.assertEqual(len(core.lane_items(unpinned)), 1)

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
    for case in (Grouping, Validation):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
