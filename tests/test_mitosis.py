import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mitosis


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
    suite.addTests(Loader().loadTestsFromTestCase(AchievedParallelism))
    return suite


if __name__ == "__main__":
    unittest.main()
