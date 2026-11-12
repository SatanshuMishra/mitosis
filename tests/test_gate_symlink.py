import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import run
import test_gate_teeth as teeth

WORKER_HOOK = 'limit = job.get("limit")'

LINK_WORKER = teeth.WORKER.replace(
    WORKER_HOOK,
    "import shutil\n"
    'for path, target in sorted(job.get("links", {}).items()):\n'
    "    if os.path.isdir(path) and not os.path.islink(path):\n"
    "        shutil.rmtree(path)\n"
    '    os.symlink(target.replace("@TMP@", os.path.dirname(here)), path)\n' + WORKER_HOOK,
)

LIMIT = "LIMIT = 3\n"
KEEP = b"cached bytes\n"
LOCKED = "tests/test_calc.py"
LOCKED_TEXT = "import unittest\n"
CHARTER = "# Charter\n\n- Python 3.9 or later.\n- Locked: %s\n" % LOCKED

LIMIT_TEST = (
    "import unittest\n"
    "\n"
    "import calc\n"
    "\n"
    "\n"
    "class Calc(unittest.TestCase):\n"
    "    def adds(self):\n"
    "        self.assertEqual(calc.LIMIT, 3)\n"
)


def read_bytes(path):
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as handle:
        return handle.read()


class GateSymlink(unittest.TestCase):
    def setUp(self):
        self.scratch = None
        self.tmp = tempfile.mkdtemp(prefix="mitosis-symlink-")

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _linked_build(self, files, writes, links, outside=None, seeded=None, charter=None, acceptance=True):
        self.scratch = teeth.Scratch(seeded)
        placed = {
            name: teeth.put(os.path.join(self.scratch.tmp, name), content)
            for name, content in (outside or {}).items()
        }
        teeth.put(os.path.join(self.scratch.stubs, "worker.py"), LINK_WORKER)
        named = {"acceptance": [teeth.ADDS]} if acceptance else {}
        items = self.scratch.items([teeth.step("add", files, "demo", **named)])
        self.scratch.jobs(
            {"demo": {"step": "add", "writes": writes, "links": links, "declared": list(files)}}
        )
        extra = ("--charter", self.scratch.file("charter.md", charter)) if charter else ()
        return self.scratch.build(items, *extra, push=True), placed

    def _judged_through_the_link(self, completed):
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + teeth.tail(completed))
        msp = teeth.summary_of(completed)["msps"][0]
        self.assertEqual(msp["state"], "shipped")
        first = msp["gate"]["properties"][0]
        self.assertEqual(first["outcome"], "pass", teeth.tail(completed))
        self.assertIsNone(first["reason"])

    def a_symlinked_implementation_is_judged_through_the_link(self):
        completed, _ = self._linked_build(
            ("calc.py", "impl.py", "tests/test_calc.py"),
            {"impl.py": LIMIT, "tests/test_calc.py": LIMIT_TEST},
            {"calc.py": "impl.py"},
        )
        self.assertEqual(self.scratch.checked("show", "mitosis/demo:impl.py"), LIMIT)
        self._judged_through_the_link(completed)

    def a_link_to_an_outside_file_is_judged_and_never_written(self):
        completed, placed = self._linked_build(
            ("calc.py", "tests/test_calc.py"),
            {"tests/test_calc.py": LIMIT_TEST},
            {"calc.py": "@TMP@/victim.py"},
            outside={"victim.py": LIMIT},
        )
        self.assertEqual(read_bytes(placed["victim.py"]), LIMIT.encode("utf-8"))
        self._judged_through_the_link(completed)

    def a_linked_tests_folder_keeps_its_outside_cache_through_a_build(self):
        completed, placed = self._linked_build(
            ("calc.py",),
            {"calc.py": LIMIT},
            {"tests": "@TMP@/outside"},
            outside={"outside/__pycache__/keep.pyc": KEEP.decode("utf-8")},
            seeded={LOCKED: LOCKED_TEXT},
            charter=CHARTER,
            acceptance=False,
        )
        self.assertEqual(read_bytes(placed["outside/__pycache__/keep.pyc"]), KEEP)
        self.assertEqual(completed.returncode, 9, "mitosis exit code" + teeth.tail(completed))
        msp = teeth.summary_of(completed)["msps"][0]
        self.assertEqual(msp["state"], "referee-reverted")
        self.assertEqual(msp["reconcile"]["referee"], [LOCKED])
        self.assertEqual(self.scratch.checked("show", "mitosis/demo:" + LOCKED), LOCKED_TEXT)
        self.assertEqual(self.scratch.checked("ls-remote", "origin", "mitosis/demo"), "")

    def _outside_cache(self):
        tree = os.path.join(self.tmp, "tree")
        outside = os.path.join(self.tmp, "outside")
        keep = os.path.join(outside, "__pycache__", "keep.pyc")
        os.makedirs(os.path.dirname(keep))
        os.makedirs(tree)
        with open(keep, "wb") as handle:
            handle.write(KEEP)
        os.symlink(outside, os.path.join(tree, "tests"))
        return tree, keep

    def clear_caches_beside_refuses_a_linked_parent(self):
        tree, keep = self._outside_cache()
        refusal = None
        try:
            run.clear_caches_beside(tree, [LOCKED])
        except OSError as error:
            refusal = error
        self.assertEqual(read_bytes(keep), KEEP)
        self.assertEqual(
            str(refusal),
            "mitosis refused to act on tests/__pycache__: tests is a symbolic link, so the action "
            "would reach outside the worktree",
        )


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            GateSymlink,
            (
                "a_symlinked_implementation_is_judged_through_the_link",
                "a_link_to_an_outside_file_is_judged_and_never_written",
                "a_linked_tests_folder_keeps_its_outside_cache_through_a_build",
                "clear_caches_beside_refuses_a_linked_parent",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
