import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_accept_execution as accept
from test_accept_execution import S, tail


def store(scratch, *entries):
    return accept.put(
        scratch.path("store.jsonl"), "".join(json.dumps(entry) + "\n" for entry in entries)
    )


class TwoAxisTier(accept.Case):
    def a_step_without_complexity_is_cheap(self):
        completed, plan = self.planned([S("a", ["a.py"])])
        self.assertEqual((plan.get("tiers") or {}).get("a"), "cheap", tail(completed))

    def assumptions_no_longer_force_top(self):
        completed, plan = self.planned(
            [S("a", ["a.py"], complexity="simple", assumptions=["took the narrow reading"])]
        )
        self.assertEqual((plan.get("tiers") or {}).get("a"), "cheap", tail(completed))


class HistoryStore(accept.Case):
    def default_store(self):
        return {"MITOSIS_TRAJECTORY_STORE": store(self.scratch, {"files": ["a.py"], "outcome": "reverted"})}

    def the_default_store_raises_a_tier(self):
        completed, plan = self.planned(
            [S("a", ["a.py"], complexity="simple")], env=self.default_store()
        )
        self.assertEqual((plan.get("tiers") or {}).get("a"), "top", tail(completed))

    def no_trajectories_ignores_the_default_store(self):
        completed = self.scratch.plan_only(
            [S("a", ["a.py"], complexity="simple")], "--no-trajectories", env=self.default_store()
        )
        self.exits(completed, 0)
        plan = self.plan(completed)
        self.assertEqual((plan.get("tiers") or {}).get("a"), "cheap", tail(completed))


class HistoryTrigger(accept.Case):
    def a_failed_outcome_alone_does_not_raise_a_tier(self):
        history = store(self.scratch, {"files": ["a.py"], "outcome": "failed"})
        completed, plan = self.planned(
            [S("a", ["a.py"], complexity="simple")], "--trajectory", history
        )
        self.assertEqual((plan.get("tiers") or {}).get("a"), "cheap", tail(completed))


class HistoryMatching(accept.Case):
    def a_prose_surface_naming_the_file_matches(self):
        history = store(self.scratch, {"surface": "the parser in parse.py broke", "outcome": "reverted"})
        completed, plan = self.planned(
            [S("parse", ["pkg/parse.py"], complexity="simple")], "--trajectory", history
        )
        self.assertEqual((plan.get("tiers") or {}).get("parse"), "top", tail(completed))

    def a_prefixed_record_path_matches_by_suffix(self):
        history = store(self.scratch, {"files": ["src/pkg/parse.py"], "outcome": "reverted"})
        completed, plan = self.planned(
            [S("parse", ["pkg/parse.py"], complexity="simple")], "--trajectory", history
        )
        self.assertEqual((plan.get("tiers") or {}).get("parse"), "top", tail(completed))


class GlobMarkers(accept.Case):
    def a_glob_marker_is_read_as_a_substring(self):
        completed, plan = self.planned(
            [S("x", ["app/x.py"], complexity="simple")], "--risk-markers", "app/*.py"
        )
        self.assertEqual((plan.get("tiers") or {}).get("x"), "cheap", tail(completed))


class MarkerList(accept.Case):
    def a_comma_separated_marker_list_is_split(self):
        completed, plan = self.planned(
            [S("b", ["billing/x.py"], complexity="simple")], "--risk-markers", "auth,billing"
        )
        self.assertEqual((plan.get("tiers") or {}).get("b"), "top", tail(completed))


class VerifyMarkers(accept.Case):
    def a_serial_marker_is_a_raw_substring_of_a_file(self):
        completed, plan = self.planned(
            [S("x", ["app/unauthenticated/x.ts"])], "--serial-markers", "auth"
        )
        self.assertEqual((plan.get("verify_modes") or {}).get("x"), "serial", tail(completed))

    def an_acceptance_file_alone_never_makes_a_step_serial(self):
        completed = self.scratch.plan_only(
            [S("a", ["a.py"], acceptance=[{"file": "auth/test_a.py", "test": "a_works"}])],
            "--serial-verify-markers",
            "auth",
        )
        self.exits(completed, 0)
        plan = self.plan(completed)
        self.assertEqual((plan.get("verify_modes") or {}).get("a"), "offload", tail(completed))


def load_tests(loader, standard, pattern):
    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            return sorted(
                name
                for name in vars(case)
                if not name.startswith("_") and callable(getattr(case, name)) and name != "default_store"
            )

    suite = unittest.TestSuite()
    for case in (
        TwoAxisTier,
        HistoryStore,
        HistoryTrigger,
        HistoryMatching,
        GlobMarkers,
        MarkerList,
        VerifyMarkers,
    ):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite
