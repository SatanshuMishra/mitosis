import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from test_warm_clones import (
    ADDS,
    APFS_ONLY,
    CHECK_AUTHOR_TEST_FILE,
    CORRECT_ADD,
    EXACT_TEST,
    LEFT,
    RECORD_SEEDED,
    SEEDED_FILE,
    WARM,
    Scratch,
    builder,
    check_author,
    step,
    tail,
    under_cache,
)


class SeededWriteSet(unittest.TestCase):
    def setUp(self):
        if sys.platform != "darwin":
            self.skipTest(APFS_ONLY)
        self.scratch = Scratch()

    def tearDown(self):
        self.scratch.close()

    def _cache_paths(self, branch):
        return [path for path in self.scratch.tree_paths(branch) if under_cache(path)]

    def an_untouched_seeded_file_in_a_lane_write_set_never_lands(self):
        scratch = self.scratch
        items = scratch.items([step("left", ["left.py", SEEDED_FILE], "demo")])
        scratch.jobs({"left": {"writes": {"left.py": LEFT}, "script": RECORD_SEEDED}})
        completed = scratch.build(items)
        record = scratch.record("left")
        self.assertEqual(record.get("bytes"), WARM, "the Worker saw no seeded copy" + tail(completed))
        self.assertEqual(scratch.checked("show", "mitosis/demo:left.py"), LEFT, tail(completed))
        self.assertEqual(self._cache_paths("mitosis/demo"), [], tail(completed))

    def an_untouched_seeded_file_in_the_locked_list_never_reaches_the_feature_branch(self):
        scratch = self.scratch
        author = {**check_author(), "files": [CHECK_AUTHOR_TEST_FILE, SEEDED_FILE]}
        seeded_check = {"file": SEEDED_FILE, "test": "Seeded.kept"}
        items = scratch.items(
            [author, {**builder(), "acceptance": [ADDS, seeded_check]}]
        )
        scratch.jobs(
            {
                "check-author": {
                    "writes": {CHECK_AUTHOR_TEST_FILE: EXACT_TEST},
                    "msp": "lock",
                    "script": RECORD_SEEDED,
                },
                "add": {"writes": {"calc.py": CORRECT_ADD}, "msp": "calc"},
            }
        )
        completed = scratch.build(items)
        self.assertEqual(
            scratch.record("check-author").get("bytes"), WARM, "no seeded copy" + tail(completed)
        )
        self.assertEqual(self._cache_paths("feature"), [], tail(completed))
        lock = ((scratch.state().get("msps") or {}).get("0") or {}).get("lock") or {}
        self.assertEqual(lock.get("state"), "failed", tail(completed))
        self.assertEqual(
            lock.get("reason"),
            "the check-author Step check-author left %s unwritten" % SEEDED_FILE,
        )
        self.assertEqual(self._cache_paths("mitosis/calc"), [], tail(completed))


def load_tests(loader, standard, pattern):
    return unittest.TestSuite(
        SeededWriteSet(name)
        for name in (
            "an_untouched_seeded_file_in_a_lane_write_set_never_lands",
            "an_untouched_seeded_file_in_the_locked_list_never_reaches_the_feature_branch",
        )
    )


if __name__ == "__main__":
    unittest.main()
