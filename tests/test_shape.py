import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import shape

SCALAR_KEYS = (
    "steps",
    "lanes",
    "msps",
    "largest_lane",
    "msps_per_step",
    "parallelism",
    "fused_without_overlap",
    "lane_cycles",
)

RECORDED = {
    "pass-1": {
        "steps": 8,
        "lanes": 3,
        "msps": 3,
        "largest_lane": 4,
        "msps_per_step": 3 / 8,
        "parallelism": 1,
        "fused_without_overlap": 8,
        "lane_cycles": 0,
    },
    "pass-2": {
        "steps": 8,
        "lanes": 5,
        "msps": 5,
        "largest_lane": 3,
        "msps_per_step": 5 / 8,
        "parallelism": 1,
        "fused_without_overlap": 0,
        "lane_cycles": 0,
    },
    "pass-3": {
        "steps": 8,
        "lanes": 2,
        "msps": 2,
        "largest_lane": 7,
        "msps_per_step": 2 / 8,
        "parallelism": 1,
        "fused_without_overlap": 20,
        "lane_cycles": 0,
    },
}


def step(name, files, **extra):
    base = {
        "name": name,
        "task": "do " + name,
        "files": list(files),
        "source": None,
        "acceptance": [],
    }
    return {**base, **extra}


def recorded_splits():
    with open(os.path.join(ROOT, "tests", "fixtures", "splits.json"), encoding="utf-8") as handle:
        return json.load(handle)


class Scalars(unittest.TestCase):
    def scalars_returns_exactly_the_declared_keys(self):
        self.assertEqual(tuple(shape.scalars([step("a", ["a.py"])])), SCALAR_KEYS)
    def an_empty_structure_scores_zero_everywhere(self):
        self.assertEqual(
            shape.scalars([]),
            {
                "steps": 0,
                "lanes": 0,
                "msps": 0,
                "largest_lane": 0,
                "msps_per_step": 0.0,
                "parallelism": 0,
                "fused_without_overlap": 0,
                "lane_cycles": 0,
            },
        )
    def fused_without_overlap_counts_a_lane_with_no_file_reason(self):
        items = [
            step("a", ["a.py"], msp="m"),
            step("b", ["b.py"], msp="m", after=["a"]),
            step("c", ["c.py"]),
        ]
        result = shape.scalars(items)
        self.assertEqual(result["lanes"], 2)
        self.assertEqual(result["fused_without_overlap"], 1)
    def fused_without_overlap_counts_a_pair_joined_by_an_after_edge(self):
        items = [
            step("a", ["a.py"], msp="m"),
            step("b", ["b.py"], msp="m", after=["a"]),
        ]
        result = shape.scalars(items)
        self.assertEqual(result["lanes"], 1)
        self.assertEqual(result["fused_without_overlap"], 1)

    def fused_without_overlap_ignores_a_pair_that_shares_a_file(self):
        items = [
            step("a", ["a.py", "shared.py"]),
            step("b", ["b.py", "shared.py"]),
        ]
        result = shape.scalars(items)
        self.assertEqual(result["lanes"], 1)
        self.assertEqual(result["fused_without_overlap"], 0)

    def largest_lane_is_the_most_steps_in_one_lane(self):
        items = [
            step("a", ["a.py", "x.py"]),
            step("b", ["b.py", "x.py"]),
            step("c", ["c.py", "x.py"]),
            step("d", ["d.py"]),
        ]
        self.assertEqual(shape.scalars(items)["largest_lane"], 3)

    def msps_per_step_divides_msps_by_steps(self):
        items = [
            step("a", ["a.py", "x.py"]),
            step("b", ["b.py", "x.py"]),
            step("c", ["c.py"]),
            step("d", ["d.py"]),
        ]
        result = shape.scalars(items)
        self.assertEqual(result["msps"], 3)
        self.assertEqual(result["steps"], 4)
        self.assertEqual(result["msps_per_step"], 0.75)

    def parallelism_is_the_widest_layer_of_the_lane_dag(self):
        items = [
            step("a", ["a.py"]),
            step("b", ["b.py"]),
            step("c", ["c.py"], after=["a", "b"]),
            step("d", ["d.py"], after=["a"]),
            step("e", ["e.py"], after=["c", "d"]),
        ]
        result = shape.scalars(items)
        self.assertEqual(result["lanes"], 5)
        self.assertEqual(result["parallelism"], 2)

    def parallelism_is_one_for_a_pure_chain_of_lanes(self):
        items = [
            step("a", ["a.py"]),
            step("b", ["b.py"], after=["a"]),
            step("c", ["c.py"], after=["b"]),
        ]
        self.assertEqual(shape.scalars(items)["parallelism"], 1)

    def parallelism_survives_a_cycle_between_lanes(self):
        items = [
            step("a", ["a.py"], after=["b"]),
            step("b", ["b.py"], after=["a"]),
        ]
        result = shape.scalars(items)
        self.assertEqual(result["lanes"], 2)
        self.assertEqual(result["parallelism"], 1)

    def the_three_recorded_splits_score_as_recorded(self):
        splits = recorded_splits()
        self.assertEqual(sorted(splits), sorted(RECORDED))
        for name, expected in RECORDED.items():
            with self.subTest(split=name):
                self.assertEqual(shape.scalars(splits[name]), expected)

    def shape_imports_only_core_and_the_standard_library(self):
        with open(os.path.join(ROOT, "shape.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("subprocess", source)
        self.assertNotIn("import os", source)
        self.assertNotIn("import git", source)


def kinds(found):
    return sorted(finding["kind"] for finding in found)


def of_kind(found, kind):
    return [finding for finding in found if finding["kind"] == kind]


class Findings(unittest.TestCase):
    def every_finding_has_exactly_kind_and_detail_as_strings(self):
        items = [
            step("iface", ["iface.py"], contract_group="g"),
            step("a", ["a.py"], contract_group="g", after=["iface"]),
            step("b", ["b.py"], contract_group="g", after=["a"]),
        ]
        found = shape.findings(items)
        self.assertTrue(found)
        for finding in found:
            self.assertEqual(sorted(finding), ["detail", "kind"])
            self.assertIsInstance(finding["kind"], str)
            self.assertIsInstance(finding["detail"], str)

    def a_group_whose_members_chain_is_reported(self):
        items = [
            step("iface", ["iface.py"], type="contract", contract_group="g"),
            step("a", ["a.py"], contract_group="g", after=["iface"]),
            step("b", ["b.py"], contract_group="g", after=["a"]),
        ]
        chains = of_kind(shape.findings(items), "group-is-a-chain")
        self.assertEqual(len(chains), 1)
        self.assertIn("g", chains[0]["detail"])
        self.assertIn("iface", chains[0]["detail"])
        self.assertIn("a", chains[0]["detail"])
        self.assertIn("b", chains[0]["detail"])

    def a_group_chain_is_measured_by_depth_not_by_path_shape(self):
        items = [
            step("iface", ["iface.py"], type="contract", contract_group="g"),
            step("a", ["a.py"], contract_group="g", after=["iface"]),
            step("b", ["b.py"], contract_group="g", after=["iface"]),
            step("c", ["c.py"], contract_group="g", after=["a", "b"]),
        ]
        self.assertEqual(len(of_kind(shape.findings(items), "group-is-a-chain")), 1)

    def an_after_edge_leaving_the_group_does_not_make_a_chain(self):
        items = [
            step("outside", ["outside.py"]),
            step("iface", ["iface.py"], type="contract", contract_group="g", after=["outside"]),
            step("a", ["a.py"], contract_group="g", after=["iface"]),
        ]
        self.assertEqual(of_kind(shape.findings(items), "group-is-a-chain"), [])

    def a_fan_out_contract_group_is_not_reported(self):
        items = [
            step("iface", ["iface.py"], type="contract", contract_group="g"),
            step("server", ["server.py"], contract_group="g", after=["iface"]),
            step("client", ["client.py"], contract_group="g", after=["iface"]),
        ]
        self.assertEqual(shape.findings(items), [])

    def a_fused_pair_names_both_steps_and_the_edge_that_joined_them(self):
        items = [
            step("a", ["a.py"], msp="m"),
            step("b", ["b.py"], msp="m", after=["a"]),
        ]
        fused = of_kind(shape.findings(items), "fused-without-overlap")
        self.assertEqual(len(fused), 1)
        self.assertIn("a", fused[0]["detail"])
        self.assertIn("b", fused[0]["detail"])
        self.assertIn("after edges", fused[0]["detail"])

    def a_fusion_caused_by_cycle_contraction_says_so_and_never_blames_a_group(self):
        items = [
            step("core", ["core.py", "shared.py"], msp="m", contract_group="g", type="contract"),
            step("p1", ["p1.py"], msp="m", contract_group="g", after=["core"]),
            step("p2", ["p2.py"], msp="m", contract_group="g", after=["core"]),
            step("tail", ["tail.py", "shared.py"], msp="m", after=["p1", "p2"]),
        ]
        fused = of_kind(shape.findings(items), "fused-without-overlap")
        self.assertTrue(fused)
        for entry in fused:
            self.assertNotIn("interface", entry["detail"])
        self.assertTrue(any("cycle forced" in entry["detail"] for entry in fused))

    def an_unpinned_contract_group_fuses_its_steps_and_says_why(self):
        items = [
            step("a", ["a.py"], contract_group="g"),
            step("b", ["b.py"], contract_group="g"),
        ]
        result = shape.scalars(items)
        self.assertEqual(result["lanes"], 1)
        self.assertEqual(result["msps"], 1)
        self.assertEqual(result["fused_without_overlap"], 1)
        fused = of_kind(shape.findings(items), "fused-without-overlap")
        self.assertIn("two halves of one interface", fused[0]["detail"])

    def a_pinned_contract_group_still_runs_its_consumers_in_parallel(self):
        items = [
            step("iface", ["iface.py"], type="contract", contract_group="g"),
            step("a", ["a.py"], contract_group="g", after=["iface"]),
            step("b", ["b.py"], contract_group="g", after=["iface"]),
        ]
        result = shape.scalars(items)
        self.assertEqual(result["lanes"], 3)
        self.assertEqual(result["msps"], 1)
        self.assertEqual(result["parallelism"], 2)
    def a_fused_pair_names_the_after_edge_that_joined_them(self):
        items = [
            step("a", ["a.py"], msp="m"),
            step("b", ["b.py"], msp="m", after=["a"]),
        ]
        fused = of_kind(shape.findings(items), "fused-without-overlap")
        self.assertEqual(len(fused), 1)
        self.assertIn("after", fused[0]["detail"])
        self.assertNotIn("contract_group", fused[0]["detail"])

    def a_fused_pair_says_when_neither_joined_them_directly(self):
        items = [
            step("a", ["a.py", "x.py"]),
            step("b", ["b.py", "x.py", "y.py"]),
            step("c", ["c.py", "y.py"]),
        ]
        fused = of_kind(shape.findings(items), "fused-without-overlap")
        self.assertEqual(len(fused), 1)
        self.assertTrue(fused[0]["detail"].startswith("a and c"))
        self.assertIn("run of shared files", fused[0]["detail"])
        self.assertNotIn("contract_group", fused[0]["detail"])
    def a_pair_joined_through_a_group_and_a_chain_names_both(self):
        items = [
            step("a", ["a.py"], contract_group="g"),
            step("b", ["b.py"], contract_group="g", msp="m"),
            step("c", ["c.py"], msp="m", after=["b"]),
        ]
        fused = of_kind(shape.findings(items), "fused-without-overlap")
        through = [entry["detail"] for entry in fused if entry["detail"].startswith("a and c")]
        self.assertEqual(len(through), 1)
        self.assertIn("a chain of after edges and an interface no contract Step pins", through[0])
        self.assertNotIn("shared files", through[0])

    def a_pair_sharing_a_file_is_not_a_fused_finding(self):
        items = [
            step("a", ["a.py", "x.py"]),
            step("b", ["b.py", "x.py"]),
        ]
        self.assertEqual(of_kind(shape.findings(items), "fused-without-overlap"), [])

    def a_single_shared_manifest_file_is_reported(self):
        items = [
            step("delay", ["pkg/delay.py", "pkg/__init__.py"]),
            step("tremolo", ["pkg/tremolo.py", "pkg/__init__.py"]),
        ]
        manifests = of_kind(shape.findings(items), "shared-directory-manifest")
        self.assertEqual(len(manifests), 1)
        self.assertIn("delay", manifests[0]["detail"])
        self.assertIn("tremolo", manifests[0]["detail"])
        self.assertIn("pkg/__init__.py", manifests[0]["detail"])

    def a_shared_file_with_no_sibling_on_one_side_is_not_a_manifest(self):
        items = [
            step("registry", ["pkg/__init__.py", "tests/test_pkg.py"]),
            step("delay", ["pkg/delay.py", "pkg/__init__.py"]),
        ]
        self.assertEqual(of_kind(shape.findings(items), "shared-directory-manifest"), [])

    def two_shared_files_are_not_a_manifest(self):
        items = [
            step("a", ["pkg/a.py", "pkg/__init__.py", "pkg/common.py"]),
            step("b", ["pkg/b.py", "pkg/__init__.py", "pkg/common.py"]),
        ]
        self.assertEqual(of_kind(shape.findings(items), "shared-directory-manifest"), [])

    def a_sibling_in_another_directory_is_not_a_manifest(self):
        items = [
            step("a", ["pkg/__init__.py", "other/a.py"]),
            step("b", ["pkg/__init__.py", "elsewhere/b.py"]),
        ]
        self.assertEqual(of_kind(shape.findings(items), "shared-directory-manifest"), [])

    def findings_are_ordered_by_severity_so_a_cause_precedes_its_symptoms(self):
        items = [
            step("core", ["core.py", "shared.py"]),
            step("mid", ["mid.py"], after=["core"]),
            step("tail", ["tail.py", "shared.py"], after=["mid"]),
            step("x", ["x.py"], msp="w"),
            step("y", ["y.py"], msp="w", after=["x"]),
        ]
        found = shape.findings(items)
        listed = [entry["kind"] for entry in found]
        self.assertEqual(
            listed, ["lane-cycle", "shared-directory-manifest", "fused-without-overlap"]
        )
        self.assertNotEqual(listed, sorted(listed))
        order = [shape.FINDING_KINDS.index(entry["kind"]) for entry in found]
        self.assertEqual(order, sorted(order))

    def a_lane_cycle_names_the_lanes_that_wait_on_each_other(self):
        items = [
            step("core", ["core.py", "shared.py"]),
            step("mid", ["mid.py"], after=["core"]),
            step("tail", ["tail.py", "shared.py"], after=["mid"]),
        ]
        cycles = of_kind(shape.findings(items), "lane-cycle")
        self.assertEqual(len(cycles), 1)
        self.assertIn("none of them can start", cycles[0]["detail"])
        self.assertEqual(shape.scalars(items)["lane_cycles"], 2)

    def a_plan_with_no_cycle_scores_zero_lane_cycles(self):
        items = [step("a", ["a.py"]), step("b", ["b.py"], after=["a"])]
        self.assertEqual(shape.scalars(items)["lane_cycles"], 0)
        self.assertEqual(of_kind(shape.findings(items), "lane-cycle"), [])

    def a_long_list_of_fused_pairs_is_capped_and_says_how_many_are_hidden(self):
        items = [
            step("s%d" % i, ["own%d.py" % i, "link%d.py" % i, "link%d.py" % (i + 1)])
            for i in range(8)
        ]
        fused = of_kind(shape.findings(items), "fused-without-overlap")
        self.assertEqual(len(fused), shape.FUSED_SHOWN + 1)
        self.assertIn("further fused pairs are not listed", fused[-1]["detail"])
    def a_malformed_item_produces_no_finding_rather_than_an_error(self):
        self.assertEqual(shape.findings(None), [])
        self.assertEqual(shape.findings("not a list"), [])
        self.assertEqual(shape.findings([None, "text", 3]), [])
        self.assertEqual(shape.findings([{"name": ["unhashable"], "files": "not a list"}]), [])
        self.assertEqual(shape.findings([{"files": None, "after": "x", "type": 3}]), [])

    def a_well_formed_structure_is_still_read_beside_an_odd_field(self):
        items = [
            step("a", ["a.py"], contract_group=7),
            step("b", ["b.py"], contract_group=7, after=["a"]),
        ]
        self.assertEqual(kinds(shape.findings(items)), ["fused-without-overlap"])
        self.assertEqual(shape.scalars(items)["lane_cycles"], 0)

    def the_recorded_splits_produce_their_recorded_findings(self):
        splits = recorded_splits()
        for name in ("pass-1", "pass-3"):
            with self.subTest(split=name):
                self.assertTrue(of_kind(shape.findings(splits[name]), "group-is-a-chain"))
        self.assertTrue(of_kind(shape.findings(splits["pass-2"]), "shared-directory-manifest"))
        for name, expected in RECORDED.items():
            with self.subTest(split=name):
                fused = of_kind(shape.findings(splits[name]), "fused-without-overlap")
                listed = [entry for entry in fused if "further fused" not in entry["detail"]]
                further = sum(
                    int(entry["detail"].split()[0])
                    for entry in fused
                    if "further fused" in entry["detail"]
                )
                self.assertEqual(len(listed) + further, expected["fused_without_overlap"])


class ManifestExport(unittest.TestCase):
    def a_manifest_owner_built_before_its_modules_is_reported(self):
        items = [
            step("gregorian", ["pkg/__init__.py", "pkg/gregorian.py"]),
            step("parse", ["pkg/parse.py"], after=["gregorian"]),
            step("format", ["pkg/format.py"], after=["gregorian"]),
        ]
        found = of_kind(shape.findings(items), "manifest-exports-nothing")
        self.assertEqual(len(found), 1)
        self.assertIn("pkg/__init__.py", found[0]["detail"])
        self.assertIn("parse", found[0]["detail"])

    def a_manifest_owner_that_reaches_every_module_is_not_reported(self):
        items = [
            step("parse", ["pkg/parse.py"]),
            step("format", ["pkg/format.py"]),
            step("surface", ["pkg/__init__.py"], after=["parse", "format"]),
        ]
        self.assertEqual(of_kind(shape.findings(items), "manifest-exports-nothing"), [])

    def reachability_through_another_step_counts(self):
        items = [
            step("lexer", ["pkg/lexer.py"]),
            step("parser", ["pkg/parser.py"], after=["lexer"]),
            step("surface", ["pkg/__init__.py"], after=["parser"]),
        ]
        self.assertEqual(of_kind(shape.findings(items), "manifest-exports-nothing"), [])

    def a_package_whose_only_module_is_the_manifest_is_not_reported(self):
        items = [step("solo", ["pkg/__init__.py"]), step("other", ["elsewhere/thing.py"])]
        self.assertEqual(of_kind(shape.findings(items), "manifest-exports-nothing"), [])

    def a_late_co_owner_repairs_an_early_one_and_nothing_is_reported(self):
        items = [
            step("skeleton", ["pkg/__init__.py", "pkg/base.py"]),
            step("parse", ["pkg/parse.py"], after=["skeleton"]),
            step("surface", ["pkg/__init__.py"], after=["parse"]),
        ]
        self.assertEqual(of_kind(shape.findings(items), "manifest-exports-nothing"), [])

    def a_manifest_with_no_owner_that_reaches_its_modules_is_still_reported(self):
        items = [
            step("skeleton", ["pkg/__init__.py", "pkg/base.py"]),
            step("also-early", ["pkg/__init__.py"]),
            step("parse", ["pkg/parse.py"], after=["skeleton"]),
        ]
        found = of_kind(shape.findings(items), "manifest-exports-nothing")
        self.assertEqual(len(found), 1)

    def one_manifest_file_yields_at_most_one_finding(self):
        items = [
            step("a", ["pkg/__init__.py"]),
            step("b", ["pkg/__init__.py"]),
            step("c", ["pkg/__init__.py"]),
            step("mod", ["pkg/mod.py"], after=["a"]),
        ]
        self.assertEqual(len(of_kind(shape.findings(items), "manifest-exports-nothing")), 1)

    def a_manifest_at_the_repository_root_still_finds_its_siblings(self):
        items = [
            step("skeleton", ["index.ts"]),
            step("parser", ["parser.ts"], after=["skeleton"]),
            step("writer", ["writer.ts"], after=["skeleton"]),
        ]
        found = of_kind(shape.findings(items), "manifest-exports-nothing")
        self.assertEqual(len(found), 1)
        self.assertIn("index.ts", found[0]["detail"])

    def a_root_manifest_does_not_claim_files_in_subdirectories(self):
        items = [
            step("skeleton", ["index.ts"]),
            step("nested", ["src/deep.ts"], after=["skeleton"]),
        ]
        self.assertEqual(of_kind(shape.findings(items), "manifest-exports-nothing"), [])

    def a_manifest_owner_that_owns_the_modules_itself_is_not_reported(self):
        items = [step("all", ["pkg/__init__.py", "pkg/a.py", "pkg/b.py"])]
        self.assertEqual(of_kind(shape.findings(items), "manifest-exports-nothing"), [])

    def the_check_covers_other_ecosystems_manifest_names(self):
        for manifest in ("index.ts", "mod.rs", "index.js"):
            with self.subTest(manifest=manifest):
                items = [
                    step("root", ["pkg/" + manifest, "pkg/core.x"]),
                    step("leaf", ["pkg/leaf.x"], after=["root"]),
                ]
                self.assertEqual(
                    len(of_kind(shape.findings(items), "manifest-exports-nothing")), 1
                )

    def a_manifest_finding_outranks_the_symptoms_it_causes(self):
        items = [
            step("gregorian", ["pkg/__init__.py", "pkg/gregorian.py"]),
            step("parse", ["pkg/parse.py"], after=["gregorian"]),
            step("format", ["pkg/format.py"], after=["gregorian"]),
        ]
        listed = [entry["kind"] for entry in shape.findings(items)]
        self.assertEqual(listed[0], "manifest-exports-nothing")


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
    for case in (Scalars, Findings, ManifestExport):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
