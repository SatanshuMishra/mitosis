import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core
import mitosis
import run


def lanes(*spans):
    return {
        str(index): {"started": start, "finished": finish, "state": "ok"}
        for index, (start, finish) in enumerate(spans)
    }


class AchievedParallelism(unittest.TestCase):
    def a_handoff_at_one_timestamp_is_not_an_overlap(self):
        peak, counted = mitosis.achieved_parallelism(
            lanes(
                ("2026-09-19T02:04:09+00:00", "2026-09-19T02:08:17+00:00"),
                ("2026-09-19T02:08:17+00:00", "2026-09-19T02:12:40+00:00"),
            )
        )
        self.assertEqual(peak, 1)
        self.assertEqual(counted, 2)

    def two_lanes_handing_off_to_one_survivor_peaks_at_the_survivor_count(self):
        peak, _ = mitosis.achieved_parallelism(
            lanes(
                ("2026-09-19T02:04:09+00:00", "2026-09-19T02:08:17+00:00"),
                ("2026-09-19T02:04:09+00:00", "2026-09-19T02:08:24+00:00"),
                ("2026-09-19T02:08:17+00:00", "2026-09-19T02:12:40+00:00"),
                ("2026-09-19T02:08:17+00:00", "2026-09-19T02:15:00+00:00"),
            )
        )
        self.assertEqual(peak, 3)

    def genuine_overlap_is_still_counted(self):
        peak, _ = mitosis.achieved_parallelism(
            lanes(
                ("2026-09-19T02:04:00+00:00", "2026-09-19T02:10:00+00:00"),
                ("2026-09-19T02:05:00+00:00", "2026-09-19T02:11:00+00:00"),
                ("2026-09-19T02:06:00+00:00", "2026-09-19T02:12:00+00:00"),
            )
        )
        self.assertEqual(peak, 3)

    def a_lane_with_no_timestamps_is_not_counted(self):
        peak, counted = mitosis.achieved_parallelism(
            {
                "0": {"started": "2026-09-19T02:04:00+00:00", "finished": None, "state": "failed"},
                "1": {"state": "blocked"},
            }
        )
        self.assertEqual((peak, counted), (0, 0))

    def no_lanes_at_all_report_nothing(self):
        self.assertEqual(mitosis.achieved_parallelism({}), (0, 0))
        self.assertEqual(mitosis.achieved_parallelism(None), (0, 0))

    def the_reported_peak_never_exceeds_the_concurrency_cap(self):
        spans = lanes(
            ("2026-09-19T02:04:09+00:00", "2026-09-19T02:08:17+00:00"),
            ("2026-09-19T02:04:09+00:00", "2026-09-19T02:08:24+00:00"),
            ("2026-09-19T02:08:17+00:00", "2026-09-19T02:12:40+00:00"),
            ("2026-09-19T02:08:17+00:00", "2026-09-19T02:15:00+00:00"),
            ("2026-09-19T02:08:24+00:00", "2026-09-19T02:09:55+00:00"),
            ("2026-09-19T02:15:00+00:00", "2026-09-19T02:19:26+00:00"),
            ("2026-09-19T02:19:26+00:00", "2026-09-19T02:20:03+00:00"),
            ("2026-09-19T02:19:26+00:00", "2026-09-19T02:23:28+00:00"),
            ("2026-09-19T02:23:28+00:00", "2026-09-19T02:26:05+00:00"),
        )
        peak, counted = mitosis.achieved_parallelism(spans)
        self.assertLessEqual(peak, 3)
        self.assertEqual(counted, 9)


class ReturnParsing(unittest.TestCase):
    def _out(self, text):
        import tempfile

        path = os.path.join(tempfile.mkdtemp(), "out")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    RETURN = '{"item":"a","status":"ok","files_changed":["x.py"],"notes":"n"}'

    def a_fenced_return_is_still_found(self):
        got = run.last_return(self._out("prose\n```json\n" + self.RETURN + "\n```\n"))
        self.assertEqual(got["item"], "a")

    def prose_after_the_return_does_not_discard_it(self):
        got = run.last_return(self._out(self.RETURN + "\nAll done.\n"))
        self.assertEqual(got["status"], "ok")

    def the_last_return_wins_when_there_are_several(self):
        first = '{"item":"a","status":"failed","files_changed":[],"notes":""}'
        got = run.last_return(self._out(first + "\n" + self.RETURN + "\n"))
        self.assertEqual(got["status"], "ok")

    def narration_that_merely_looks_like_json_is_skipped(self):
        got = run.last_return(self._out(self.RETURN + "\n[1, 2, 3]\n\"a string\"\n"))
        self.assertEqual(got["item"], "a")

    def a_worker_that_returns_nothing_still_reports_nothing(self):
        self.assertIsNone(run.last_return(self._out("no json here at all\n")))


class RiskMarkers(unittest.TestCase):
    def a_marker_does_not_fire_inside_a_longer_word(self):
        self.assertEqual(core.tier_for(["src/waverider.py"], ["wav"], "simple", None), "cheap")
        self.assertEqual(
            core.tier_for(["app/(unauthenticated)/page.tsx"], ["auth"], "simple", None), "cheap"
        )

    def a_marker_fires_on_a_path_segment(self):
        self.assertEqual(core.tier_for(["bleep/wav.py"], ["wav"], "simple", None), "top")
        self.assertEqual(core.tier_for(["src/wav/writer.py"], ["wav"], "simple", None), "top")

    def a_marker_fires_on_a_camel_case_word(self):
        self.assertEqual(
            core.tier_for(["app/types/AuthResponse.ts"], ["auth"], "simple", None), "top"
        )

    def a_marker_carrying_a_separator_keeps_substring_behaviour(self):
        self.assertEqual(core.tier_for(["api/auth/login.py"], ["api/auth"], "simple", None), "top")
        self.assertEqual(core.tier_for(["db/0004_add.sql"], [".sql"], "simple", None), "top")

    def a_glob_marker_still_matches(self):
        self.assertEqual(core.tier_for(["db/0004_add.sql"], ["db/*.sql"], "simple", None), "top")


class TemplatePlaceholders(unittest.TestCase):
    def a_placeholder_the_template_kind_does_not_offer_is_refused(self):
        with self.assertRaises(run.ConfigError) as caught:
            run.check_template("acceptance", "runner {file} {test} {model}")
        self.assertIn("model", str(caught.exception))

    def every_documented_placeholder_is_accepted(self):
        run.check_template("dispatch", "agent {task} {model} {tier} {worktree} {branch}")
        run.check_template("acceptance", "runner {file} {test} {worktree}")


class CoverageReport(unittest.TestCase):
    def a_decompose_record_supplies_coverage_when_no_Step_carries_a_source(self):
        record = {"coverage": {"mode": "headings", "sections": [{"id": "1", "title": "A", "heading": "1. A", "line": 1}], "uncovered": [{"id": "1", "title": "A", "heading": "1. A", "line": 1}], "unmatched_claims": []}}
        lines = mitosis.coverage_lines({"items": [], "source": None}, record, ".")
        self.assertIn("by headings: 0 of 1 section claimed, 1 unclaimed", lines[0])

    def coverage_is_unavailable_only_when_there_is_no_record_and_no_source(self):
        lines = mitosis.coverage_lines({"items": [], "source": None}, None, ".")
        self.assertTrue(lines[0].startswith("unavailable: the Steps declare no source document"))


def load_tests(loader, tests, pattern):
    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            return sorted(
                name
                for name in dir(case)
                if not name.startswith("_")
                and callable(getattr(case, name))
                and name not in dir(unittest.TestCase)
            )

    suite = unittest.TestSuite()
    for case in (AchievedParallelism, ReturnParsing, RiskMarkers, TemplatePlaceholders, CoverageReport):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
