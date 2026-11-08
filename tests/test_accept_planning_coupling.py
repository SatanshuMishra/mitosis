import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import test_accept_execution as accept
from test_accept_execution import S, tail


def store(scratch, *entries):
    return accept.put(
        scratch.path("store.jsonl"), "".join(json.dumps(entry) + "\n" for entry in entries)
    )


def item(plan, name):
    found = [entry for entry in plan.get("items") or [] if entry.get("name") == name]
    return found[0] if found else {}


class CoupledPairs(accept.Case):
    def a_serialize_default_pair_is_flagged_not_ordered(self):
        completed, plan = self.planned(
            [S("a", ["auth/a.py"]), S("b", ["auth/b.py"])], "--risk-markers", "auth"
        )
        self.assertNotIn("coupling_order", plan, tail(completed))
        self.assertNotIn("lane_after", plan, tail(completed))
        self.assertNotIn("after", item(plan, "b"), tail(completed))
        review = plan.get("coupling_review") or []
        self.assertEqual(len(review), 1, json.dumps(review))
        self.assertEqual(review[0].get("steps"), ["a", "b"], json.dumps(review))
        self.assertEqual(review[0].get("default"), "serialize", json.dumps(review))


class ImportAdjacency(accept.Case):
    def graph_files_resolved_by_suffix_are_import_adjacent(self):
        graph = accept.put_json(
            self.scratch.path("graph.json"),
            {
                "nodes": [
                    {"id": "n1", "source_file": "app/a.py"},
                    {"id": "n2", "source_file": "app/b.py"},
                ],
                "links": [{"source": "n1", "target": "n2"}],
            },
        )
        completed, plan = self.planned(
            [S("a", ["src/app/a.py"]), S("b", ["src/app/b.py"])], "--graph", graph
        )
        self.assertEqual(
            plan.get("coupling_review"),
            [{"steps": ["a", "b"], "lanes": [0, 1], "signals": ["import-adjacent"], "default": "parallel"}],
            tail(completed),
        )
        summary = accept.summary_of(completed)
        self.assertEqual(
            accept.contract_errors("h8-mitosis-result", summary), [], json.dumps(summary.get("coupling"))
        )


class SharedMarker(accept.Case):
    def a_shared_marker_is_named_in_the_signal(self):
        completed, plan = self.planned(
            [S("a", ["auth/a.py"]), S("b", ["auth/b.py"])], "--risk-markers", "auth"
        )
        review = plan.get("coupling_review") or [{}]
        self.assertEqual(review[0].get("signals"), ["shared-risk-marker:auth"], tail(completed))
        self.assertEqual(review[0].get("default"), "serialize", tail(completed))


class RegressionSignal(accept.Case):
    def an_entry_touching_both_sides_is_no_signal(self):
        history = store(self.scratch, {"files": ["a.py", "b.py"], "outcome": "reverted"})
        completed, plan = self.planned(
            [S("a", ["a.py"]), S("b", ["b.py"])], "--trajectory", history
        )
        self.assertNotIn("coupling_review", plan, tail(completed))
        self.assertNotIn("coupling_order", plan, tail(completed))
        self.assertNotIn("after", item(plan, "b"), tail(completed))

    def a_recorded_breakage_is_regression_history(self):
        history = store(self.scratch, {"files": ["a.py"], "outcome": "fixed", "regressed": ["b.py"]})
        completed, plan = self.planned(
            [S("a", ["a.py"]), S("b", ["b.py"])], "--trajectory", history
        )
        review = plan.get("coupling_review") or [{}]
        self.assertEqual(review[0].get("signals"), ["regression-history"], tail(completed))
        self.assertEqual(review[0].get("default"), "serialize", tail(completed))


class MigrationSignal(accept.Case):
    def python_migrations_in_one_app_collide(self):
        completed, plan = self.planned(
            [S("m1", ["app/migrations/0002_x.py"]), S("m2", ["app/migrations/0003_y.py"])]
        )
        review = plan.get("coupling_review") or [{}]
        self.assertEqual(review[0].get("signals"), ["same-migration-app:app"], tail(completed))
        self.assertEqual(review[0].get("default"), "serialize", tail(completed))

    def numbered_sql_files_are_no_signal(self):
        completed, plan = self.planned([S("s1", ["db/0002_a.sql"]), S("s2", ["db/0003_b.sql"])])
        self.assertNotIn("coupling_review", plan, tail(completed))
        self.assertNotIn("coupling_order", plan, tail(completed))


def load_tests(loader, standard, pattern):
    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            return sorted(
                name for name in vars(case) if not name.startswith("_") and callable(getattr(case, name))
            )

    suite = unittest.TestSuite()
    for case in (CoupledPairs, ImportAdjacency, SharedMarker, RegressionSignal, MigrationSignal):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite
