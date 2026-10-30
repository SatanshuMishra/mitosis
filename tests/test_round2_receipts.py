import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import isolation
import run
from test_mutant_coverage import (
    ADDS,
    CORRECT_ADD,
    GIT_IDENTITY,
    TEST_FILE,
    Scratch,
    put,
    step,
    summary_of,
    tail,
)
from test_round1_receipts import EXACT_TEST, check_author

OUTSIDE_BYTES = b"outside the sandbox, never rewritten by mitosis\n"
KEPT_NAME = "keep.txt"
SEALED_MODE = 0o500
CALL_LIMIT = 60

AGAIN = {"file": TEST_FILE, "test": "Calc.adds_again"}
TWO_CHECKS = (
    "import unittest\n"
    "\n"
    "import calc\n"
    "\n"
    "\n"
    "class Calc(unittest.TestCase):\n"
    "    def adds(self):\n"
    "        self.assertEqual(calc.add(2, 3), 5)\n"
    "\n"
    "    def adds_again(self):\n"
    "        self.assertEqual(calc.add(4, 1), 5)\n"
)

CLONE_SWAP = (
    "import os\n"
    "import sys\n"
    "clone, outside, moved = sys.argv[1:4]\n"
    "try:\n"
    "    os.rename(clone, moved)\n"
    "    os.symlink(outside, clone)\n"
    "except OSError:\n"
    "    pass\n"
)


def planting(link, target):
    return (
        "import os as _os\n"
        "try:\n"
        "    _os.symlink(%r, %r)\n"
        "except OSError:\n"
        "    pass\n" % (target, link)
    )


def replacing_the_checkout(target):
    return (
        "import os as _os\n"
        "_root = _os.getcwd()\n"
        "try:\n"
        "    _os.rename(_root, _os.path.join(_os.environ['TMPDIR'], 'moved-checkout'))\n"
        "    _os.symlink(%r, _root)\n"
        "except OSError:\n"
        "    pass\n" % (target,)
    )


class Receipts(unittest.TestCase):
    def setUp(self):
        self.scratch = None

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()

    def _skip_without_sandbox(self):
        if sys.platform != "darwin":
            self.skipTest("sandbox-exec exists only on macOS")

    def _first_property(self, completed, label="demo"):
        found = [
            msp for msp in summary_of(completed).get("msps") or [] if msp.get("msp") == label
        ]
        self.assertEqual(len(found), 1, "the JSON summary lists no MSP %s" % label + tail(completed))
        properties = (found[0].get("gate") or {}).get("properties") or []
        self.assertTrue(properties, "the MSP's gate carries no properties" + tail(completed))
        return properties[0]

    def _outside_file(self):
        path = os.path.join(self.scratch.tmp, "outside.txt")
        with open(path, "wb") as handle:
            handle.write(OUTSIDE_BYTES)
        return path

    def _outside_folder(self):
        folder = os.path.join(self.scratch.tmp, "outside")
        os.makedirs(folder)
        with open(os.path.join(folder, KEPT_NAME), "wb") as handle:
            handle.write(OUTSIDE_BYTES)
        return folder

    def _assert_unchanged_file(self, path, completed):
        self.assertFalse(os.path.islink(path), tail(completed))
        with open(path, "rb") as handle:
            self.assertEqual(
                handle.read(),
                OUTSIDE_BYTES,
                "mitosis rewrote %s through a link sandboxed code planted" % path + tail(completed),
            )

    def _assert_unchanged_folder(self, folder, completed):
        self.assertEqual(
            sorted(os.listdir(folder)),
            [KEPT_NAME],
            "mitosis wrote into %s through a link sandboxed code planted" % folder + tail(completed),
        )
        self._assert_unchanged_file(os.path.join(folder, KEPT_NAME), completed)

    def _sandboxed_build(self, items, jobs):
        self.scratch.jobs(jobs)
        return self.scratch.build(
            items, "--worker-sandbox", "--worker-write", self.scratch.stubs
        )

    def _assert_shipped_pass(self, completed):
        self.assertEqual(summary_of(completed).get("sandbox"), "sandbox-exec", tail(completed))
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        self.assertEqual(self._first_property(completed)["outcome"], "pass")

    def a_planted_gate_log_link_never_redirects_a_mitosis_write(self):
        self._skip_without_sandbox()
        self.scratch = Scratch({TEST_FILE: EXACT_TEST})
        outside = self._outside_file()
        link = os.path.join(self.scratch.run_dir, "gate", "msp-0", "0-reverted.log")
        items = self.scratch.items([step("add", ["calc.py"], "demo", acceptance=[ADDS])])
        completed = self._sandboxed_build(
            items, {"add": {"writes": {"calc.py": planting(link, outside) + CORRECT_ADD}}}
        )
        self._assert_unchanged_file(outside, completed)
        self.assertFalse(os.path.islink(link), "sandboxed code planted %s" % link + tail(completed))
        self._assert_shipped_pass(completed)

    def a_planted_lock_log_link_never_redirects_a_mitosis_write(self):
        self._skip_without_sandbox()
        self.scratch = Scratch()
        outside = self._outside_file()
        link = os.path.join(self.scratch.run_dir, "lock", "1-locked.log")
        items = self.scratch.items(
            [check_author(), step("add", ["calc.py"], "demo", [ADDS, AGAIN], ("check-author",))]
        )
        completed = self._sandboxed_build(
            items,
            {
                "check-author": {
                    "writes": {TEST_FILE: planting(link, outside) + TWO_CHECKS},
                    "msp": "lock",
                },
                "add": {"writes": {"calc.py": CORRECT_ADD}, "msp": "demo"},
            },
        )
        self._assert_unchanged_file(outside, completed)
        self.assertFalse(os.path.islink(link), "sandboxed code planted %s" % link + tail(completed))
        self.assertEqual((summary_of(completed).get("lock") or {}).get("state"), "ok", tail(completed))
        self._assert_shipped_pass(completed)

    def a_replaced_gate_checkout_never_redirects_a_mitosis_write(self):
        self._skip_without_sandbox()
        self.scratch = Scratch({TEST_FILE: EXACT_TEST})
        outside = self._outside_folder()
        items = self.scratch.items([step("add", ["calc.py"], "demo", acceptance=[ADDS])])
        completed = self._sandboxed_build(
            items,
            {"add": {"writes": {"calc.py": replacing_the_checkout(outside) + CORRECT_ADD}}},
        )
        self._assert_unchanged_folder(outside, completed)
        self._assert_shipped_pass(completed)

    def _repository(self):
        folder = os.path.realpath(tempfile.mkdtemp(prefix="mitosis-swap-"))
        self.addCleanup(shutil.rmtree, folder, True)
        checkout = os.path.join(folder, "checkout")
        env = {**os.environ, **GIT_IDENTITY}
        for args in (
            ["init", "-q", "-b", "main", checkout],
            ["-C", checkout, "commit", "-q", "--allow-empty", "-m", "seed"],
        ):
            subprocess.run(["git", *args], env=env, check=True, capture_output=True, timeout=CALL_LIMIT)
        return folder, checkout

    def a_replaced_worker_clone_never_redirects_a_mitosis_write(self):
        self._skip_without_sandbox()
        folder, checkout = self._repository()
        sealed = os.path.join(folder, "outside", "sealed")
        os.makedirs(sealed)
        os.chmod(sealed, SEALED_MODE)
        self.addCleanup(os.chmod, sealed, stat.S_IRWXU)
        folders = isolation.lane_folders(os.path.join(folder, "run"), 0)
        start = isolation.cut_clone({"path": checkout, "branch": "main"}, folders)
        sandbox = {"program": shutil.which(isolation.SANDBOX_PROGRAM), "writes": ()}
        argv = [
            sys.executable,
            "-c",
            CLONE_SWAP,
            folders["clone"],
            os.path.dirname(sealed),
            os.path.join(folders["tmp"], "moved-clone"),
        ]
        env = {**os.environ, "HOME": os.path.join(folder, "home")}
        launched, launch_env = isolation.worker_launch(argv, folders, sandbox, env)
        logs = os.path.join(folder, "worker")
        run.run_command(
            launched, folders["clone"], CALL_LIMIT, logs + ".out", logs + ".err", launch_env
        )
        try:
            isolation.read_lane(folders["clone"], checkout, start, ("a.py",))
        except (OSError, run.GitError):
            pass
        self.assertEqual(
            stat.S_IMODE(os.lstat(sealed).st_mode),
            SEALED_MODE,
            "mitosis changed %s through a link the Worker planted" % sealed,
        )
        self.assertFalse(os.path.islink(folders["clone"]), "the Worker replaced its clone with a link")


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            Receipts,
            (
                "a_planted_gate_log_link_never_redirects_a_mitosis_write",
                "a_planted_lock_log_link_never_redirects_a_mitosis_write",
                "a_replaced_gate_checkout_never_redirects_a_mitosis_write",
                "a_replaced_worker_clone_never_redirects_a_mitosis_write",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
