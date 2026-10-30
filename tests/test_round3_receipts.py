import os
import shutil
import signal
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import isolation
import run
from test_mutant_coverage import ADDS, CORRECT_ADD, TEST_FILE, Scratch, put, step, summary_of, tail
from test_round1_receipts import EXACT_TEST

EXIT_LINGERING = 14
LINGERING_MEANING = (
    "a run left a process mitosis could not end, so mitosis stopped before reading anything "
    "that process could change"
)
LATE_ADD = "def add(a, b):\n    return b + a\n"
BROKEN_ADD = "def add(a, b):\n    return a - b\n"
LINGER_DELAY = "1"
LINGER_LIFE = "60"
REAP_SECONDS = 10
CALL_SECONDS = 60
SLOW_LANE = "import time\ntime.sleep(5)\n"
STUB_LEFTOVERS = (
    "worker.pid",
    "worker.log",
    "gate.pid",
    "gate.log",
    "gate.once",
    "dispatch.log",
)

LINGER = r'''
import os
import sys
import time

target, content, pid_file, log_file, parent, delay, life = sys.argv[1:8]
with open(pid_file + ".partial", "w", encoding="utf-8") as handle:
    handle.write(str(os.getpid()))
os.replace(pid_file + ".partial", pid_file)
deadline = time.monotonic() + float(life)
while os.getppid() == int(parent) and time.monotonic() < deadline:
    time.sleep(0.01)
time.sleep(float(delay))
staged = pid_file + ".staged"
while time.monotonic() < deadline:
    try:
        with open(staged, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(staged, target)
        with open(log_file, "a", encoding="utf-8") as handle:
            handle.write("wrote\n")
    except OSError:
        pass
    time.sleep(0.05)
'''

ANNOUNCED = (
    "_announced = __import__('time').monotonic() + 30\n"
    "while not os.path.exists(os.path.join(here, %r)) and __import__('time').monotonic() < _announced:\n"
    "    __import__('time').sleep(0.01)\n"
)

LINGERING_ACCEPT = r'''
import os
import runpy
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
mode = sys.argv.pop(1)
try:
    os.close(os.open(os.path.join(here, "gate.once"), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
except FileExistsError:
    pass
else:
    subprocess.Popen(
        [
            sys.executable,
            os.path.join(here, "linger.py"),
            os.path.abspath("calc.py"),
            %r,
            os.path.join(here, "gate.pid"),
            os.path.join(here, "gate.log"),
            str(os.getpid()),
            %r,
            %r,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=mode == "setsid",
    )
%s
sys.argv[0] = os.path.join(here, "accept.py")
runpy.run_path(sys.argv[0], run_name="__main__")
''' % (BROKEN_ADD, LINGER_DELAY, LINGER_LIFE, ANNOUNCED % "gate.pid")

SHIELD = r'''
import os
import signal
import sys

if os.path.basename(sys.argv[0]) == "mitosis.py":
    _pid_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "worker.pid")
    _kill, _killpg = os.kill, os.killpg

    def _shielded():
        try:
            with open(_pid_file, encoding="utf-8") as handle:
                return int(handle.read())
        except (OSError, ValueError):
            return None

    def _guarded_kill(pid, signum):
        if signum == signal.SIGKILL and pid == _shielded():
            return None
        return _kill(pid, signum)

    def _guarded_killpg(group, signum):
        shielded = _shielded()
        if signum == signal.SIGKILL and shielded is not None:
            try:
                if os.getpgid(shielded) == group:
                    return None
            except OSError:
                pass
        return _killpg(group, signum)

    os.kill, os.killpg = _guarded_kill, _guarded_killpg
'''


def lingering(content, mode):
    return (
        "subprocess.Popen([sys.executable, os.path.join(here, 'linger.py'), "
        "os.path.abspath('calc.py'), %r, os.path.join(here, 'worker.pid'), "
        "os.path.join(here, 'worker.log'), str(os.getpid()), %r, %r], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, "
        "start_new_session=%r)\n" % (content, LINGER_DELAY, LINGER_LIFE, mode == "setsid")
    ) + ANNOUNCED % "worker.pid"


def lingering_refusal(folder):
    return (
        "the run in %s left a process mitosis could not end within %d seconds, so mitosis read "
        "nothing that run could change" % (folder, REAP_SECONDS)
    )


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

EXIT_REFUSED = 3
REFUSED_LINE = "exit 3: refused to start"
SPELLINGS = ("absolute", "symlink", "relative", "trailing slash", "case-folded")


def refusal(given, run_dir):
    return (
        "--worker-write %s overlaps the run folder %s; sandboxed code may never write "
        "mitosis's own state, logs or gate folders, so no Worker was started" % (given, run_dir)
    )


def case_insensitive(folder):
    return os.path.exists(folder.swapcase())


class Receipts(unittest.TestCase):
    def setUp(self):
        self.scratch = Scratch({TEST_FILE: EXACT_TEST})
        self.addCleanup(self.scratch.close)
        self.items = self.scratch.items([step("add", ["calc.py"], "demo", acceptance=[ADDS])])
        self.scratch.jobs({"add": {"writes": {"calc.py": CORRECT_ADD}}})

    def _overlapping(self):
        run_dir = self.scratch.run_dir
        return (
            ("equal", run_dir, ""),
            ("ancestor", os.path.dirname(run_dir), ""),
            ("home ancestor", self.scratch.tmp, ""),
            ("inside", run_dir, "gate"),
        )

    def _spelled(self, spelling, target, below, cwd, label):
        if spelling == "symlink":
            link = os.path.join(self.scratch.tmp, "link-%s" % label.replace(" ", "-"))
            if not os.path.lexists(link):
                os.symlink(target, link)
            return os.path.join(link, below) if below else link
        joined = os.path.join(target, below) if below else target
        if spelling == "relative":
            return os.path.relpath(joined, cwd)
        if spelling == "trailing slash":
            return joined + os.sep
        if spelling == "case-folded":
            return joined.swapcase()
        return joined

    def _cases(self, cwd):
        for label, target, below in self._overlapping():
            for spelling in SPELLINGS:
                if spelling == "case-folded" and not case_insensitive(self.scratch.tmp):
                    continue
                yield label, spelling, self._spelled(spelling, target, below, cwd, label)

    def _assert_refused(self, completed, given):
        self.assertEqual(completed.returncode, EXIT_REFUSED, "mitosis exit code" + tail(completed))
        self.assertEqual(
            completed.stderr.splitlines(),
            ["mitosis: " + refusal(given, self.scratch.run_dir), REFUSED_LINE],
            tail(completed),
        )
        self.assertEqual(self.scratch.dispatched(), [], "a Worker started" + tail(completed))
        self.assertFalse(os.path.exists(os.path.join(self.scratch.run_dir, run.STATE_FILE)))

    def _build(self, given, *extra):
        return self.scratch.build(self.items, "--worker-sandbox", "--worker-write", given, *extra)

    def _plan_only(self):
        completed = self.scratch.build(self.items, "--plan-only")
        self.assertEqual(completed.returncode, 0, "plan-only exit code" + tail(completed))

    def a_fresh_run_refuses_a_worker_write_that_overlaps_the_run_folder(self):
        for label, spelling, given in self._cases(self.scratch.repo):
            with self.subTest(relation=label, spelling=spelling):
                completed = self._build(given)
                self._assert_refused(completed, given)
                self.assertFalse(
                    os.path.exists(self.scratch.run_dir),
                    "mitosis wrote the run folder before refusing" + tail(completed),
                )

    def a_resumed_run_refuses_a_worker_write_that_overlaps_the_run_folder(self):
        self._plan_only()
        for label, spelling, given in self._cases(self.scratch.repo):
            with self.subTest(relation=label, spelling=spelling):
                self._assert_refused(self._build(given, "--resume"), given)

    def the_resumed_plan_refuses_a_stored_worker_write_that_overlaps_the_run_folder(self):
        self._plan_only()
        for label, spelling, given in self._cases(os.getcwd()):
            with self.subTest(relation=label, spelling=spelling):
                with self.assertRaises(run.OverlappingWrite) as raised:
                    run.execute(
                        {"lanes": [], "msps": []},
                        self.scratch.repo,
                        "feature",
                        self.scratch.run_dir,
                        self.scratch.stub("worker.py", "{msp}", "{task}"),
                        self.scratch.stub("accept.py", "unittest", "{file}", "{test}"),
                        None,
                        120,
                        1,
                        resume=True,
                        no_push=True,
                        env=self.scratch.env,
                        worker_sandbox=True,
                        worker_writes=(given,),
                    )
                self.assertIsInstance(raised.exception, run.ConfigError)
                self.assertEqual(str(raised.exception), refusal(given, self.scratch.run_dir))
                self.assertEqual(self.scratch.dispatched(), [])

    def a_retargeted_link_never_widens_a_checked_worker_write(self):
        real = os.path.join(self.scratch.tmp, "granted", "real")
        os.makedirs(real)
        link = os.path.join(self.scratch.tmp, "granted", "link")
        os.symlink(real, link)
        os.makedirs(self.scratch.run_dir)
        writes = isolation.refuse_overlapping_writes(self.scratch.run_dir, (link,))
        self.assertEqual(writes, (real,))
        os.remove(link)
        os.symlink(self.scratch.run_dir, link)
        os.rename(real, real + "-moved")
        os.symlink(self.scratch.run_dir, real)
        checkout = os.path.join(self.scratch.tmp, "checkout")
        os.makedirs(checkout)
        confinement = isolation.gate_confinement(
            {"program": isolation.SANDBOX_PROGRAM, "writes": writes},
            os.path.join(self.scratch.tmp, "gate-log"),
            checkout,
        )
        with open(confinement["profile"], encoding="utf-8") as handle:
            profile = handle.read()
        self.assertIn('(subpath "%s")' % real, profile)
        self.assertNotIn('(subpath "%s")' % self.scratch.run_dir, profile)

    def _disjoint(self):
        sibling = self.scratch.run_dir + "-sibling"
        os.makedirs(sibling)
        return ("--worker-write", sibling, "--worker-write", self.scratch.stubs + os.sep)

    def _sandbox_flags(self):
        return ("--worker-sandbox",) if sys.platform == "darwin" else ()

    def _assert_built(self, completed):
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
        self.assertEqual(self.scratch.dispatched(), ["add"], tail(completed))
        expected = "sandbox-exec" if sys.platform == "darwin" else "off"
        self.assertEqual(summary_of(completed).get("sandbox"), expected, tail(completed))

    def a_fresh_run_accepts_a_disjoint_worker_write(self):
        completed = self.scratch.build(self.items, *(self._sandbox_flags() + self._disjoint()))
        self._assert_built(completed)

    def a_resumed_run_accepts_a_disjoint_worker_write(self):
        self._plan_only()
        completed = self.scratch.build(
            self.items, "--resume", *(self._sandbox_flags() + self._disjoint())
        )
        self._assert_built(completed)


class Outlived(unittest.TestCase):
    def setUp(self):
        self.scratch = Scratch({TEST_FILE: EXACT_TEST})
        self.addCleanup(self.scratch.close)
        put(os.path.join(self.scratch.stubs, "linger.py"), LINGER)
        put(os.path.join(self.scratch.stubs, "accept-linger.py"), LINGERING_ACCEPT)
        put(os.path.join(self.scratch.stubs, "shield", "sitecustomize.py"), SHIELD)
        for name in ("worker.pid", "gate.pid"):
            self.addCleanup(self._end_leftover, name)

    def _modes(self):
        sandboxed = ("--worker-sandbox", "--worker-write", self.scratch.stubs)
        return (("group", ()),) + ((("setsid", sandboxed),) if sys.platform == "darwin" else ())

    def _stub_file(self, name):
        return os.path.join(self.scratch.stubs, name)

    def _pid(self, name):
        path = self._stub_file(name)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as handle:
            return int(handle.read())

    def _end_leftover(self, name):
        pid = self._pid(name)
        if pid is not None and alive(pid):
            os.kill(pid, signal.SIGKILL)

    def _fresh(self):
        for name in ("worker.pid", "gate.pid"):
            self._end_leftover(name)
        for name in STUB_LEFTOVERS:
            if os.path.exists(self._stub_file(name)):
                os.remove(self._stub_file(name))
        if os.path.exists(self.scratch.run_dir):
            isolation._remove_left(self.scratch.run_dir)
        self.scratch.git("branch", "-D", "mitosis/demo")

    def _assert_ended(self, name, completed):
        pid = self._pid(name)
        self.assertIsNotNone(pid, "the lingering process never started" + tail(completed))
        self.assertFalse(
            alive(pid),
            "process %d from the run is still alive after mitosis exited" % pid + tail(completed),
        )
        self.assertFalse(
            os.path.exists(self._stub_file(name.replace(".pid", ".log"))),
            "the lingering process wrote after mitosis ended its run" + tail(completed),
        )

    def _landed(self, path):
        return self.scratch.git("show", "mitosis/demo:%s" % path).stdout

    def _build(self, items, acceptance, *flags, env=None):
        return self.scratch.mitosis(
            "--items",
            items,
            "--run-dir",
            self.scratch.run_dir,
            "--feature-branch",
            "feature",
            "--dispatch-command",
            self.scratch.stub("worker.py", "{msp}", "{task}"),
            "--acceptance-command",
            acceptance,
            "--timeout",
            "120",
            "--no-push",
            *flags,
            env=env,
        )

    def _accept(self):
        return self.scratch.stub("accept.py", "unittest", "{file}", "{test}")

    def a_worker_process_left_behind_never_changes_what_mitosis_lands(self):
        items = self.scratch.items(
            [
                step("add", ["calc.py"], "demo", acceptance=[ADDS]),
                step("slow", ["slow.txt"], "demo"),
            ]
        )
        for mode, flags in self._modes():
            with self.subTest(mode=mode):
                self._fresh()
                self.scratch.jobs(
                    {
                        "add": {"writes": {"calc.py": CORRECT_ADD}, "script": lingering(LATE_ADD, mode)},
                        "slow": {"writes": {"slow.txt": "slow\n"}, "script": SLOW_LANE},
                    }
                )
                completed = self._build(items, self._accept(), "--concurrency", "2", *flags)
                self.assertEqual(
                    self._landed("calc.py"),
                    CORRECT_ADD,
                    "mitosis landed bytes written after the Worker ended" + tail(completed),
                )
                self._assert_ended("worker.pid", completed)
                self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))

    def a_gate_process_left_behind_never_writes_the_checkout_after_its_run(self):
        items = self.scratch.items([step("add", ["calc.py"], "demo", acceptance=[ADDS])])
        self.scratch.jobs({"add": {"writes": {"calc.py": CORRECT_ADD}}})
        for mode, flags in self._modes():
            with self.subTest(mode=mode):
                self._fresh()
                acceptance = self.scratch.stub(
                    "accept-linger.py", mode, "unittest", "{file}", "{test}"
                )
                completed = self._build(items, acceptance, *flags)
                self._assert_ended("gate.pid", completed)
                self.assertEqual(completed.returncode, 0, "mitosis exit code" + tail(completed))
                gate = summary_of(completed)["msps"][0]["gate"]
                self.assertEqual(gate["properties"][0]["outcome"], "pass", tail(completed))
                self.assertEqual(gate["properties"][0].get("mutation"), "killed", tail(completed))
                self.assertEqual(self._landed("calc.py"), CORRECT_ADD, tail(completed))

    def a_process_mitosis_cannot_end_stops_the_run_before_it_reads_the_clone(self):
        items = self.scratch.items([step("add", ["calc.py"], "demo", acceptance=[ADDS])])
        shield = {"PYTHONPATH": os.path.join(self.scratch.stubs, "shield")}
        for mode, flags in self._modes():
            with self.subTest(mode=mode):
                self._fresh()
                self.scratch.jobs(
                    {"add": {"writes": {"calc.py": CORRECT_ADD}, "script": lingering(LATE_ADD, mode)}}
                )
                completed = self._build(items, self._accept(), *flags, env=shield)
                self._end_leftover("worker.pid")
                self.assertEqual(
                    completed.returncode, EXIT_LINGERING, "mitosis exit code" + tail(completed)
                )
                clone = isolation.lane_folders(self.scratch.run_dir, 0)["clone"]
                self.assertEqual(
                    completed.stderr.splitlines()[-2:],
                    [
                        "mitosis: " + lingering_refusal(clone),
                        "exit %d: %s" % (EXIT_LINGERING, LINGERING_MEANING),
                    ],
                    tail(completed),
                )
                self.assertEqual(
                    self.scratch.git("log", "--all", "--format=%H", "--", "calc.py").stdout,
                    "",
                    "mitosis landed the clone of a run it could not end" + tail(completed),
                )
                self.assertEqual(self.scratch.dispatched(), ["add"], tail(completed))

    def _sleeper(self, argv):
        proc = subprocess.Popen(
            argv + [sys.executable, "-c", "import time\ntime.sleep(%s)" % LINGER_LIFE],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(proc.wait, CALL_SECONDS)
        self.addCleanup(proc.kill)
        return proc

    def a_sandbox_mark_names_its_own_processes_and_a_missing_mark_names_none(self):
        if sys.platform != "darwin":
            self.skipTest("sandbox-exec exists only on macOS")
        checkout = os.path.join(self.scratch.tmp, "checkout")
        os.makedirs(checkout)
        log_dir = os.path.join(self.scratch.tmp, "gate-log")
        confinement = isolation.gate_confinement(
            {"program": shutil.which(isolation.SANDBOX_PROGRAM), "writes": ()}, log_dir, checkout
        )
        inside = self._sleeper([confinement["program"], "-f", confinement["profile"]])
        outside = self._sleeper([])
        found = ()
        for _ in range(500):
            found = isolation._sandboxed_pids(confinement)
            if found:
                break
        self.assertEqual(found, (inside.pid,))
        self.assertNotIn(outside.pid, found)
        os.remove(confinement["mark"])
        self.assertIsNone(isolation._sandboxed_pids(confinement))


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            Receipts,
            (
                "a_fresh_run_refuses_a_worker_write_that_overlaps_the_run_folder",
                "a_resumed_run_refuses_a_worker_write_that_overlaps_the_run_folder",
                "the_resumed_plan_refuses_a_stored_worker_write_that_overlaps_the_run_folder",
                "a_retargeted_link_never_widens_a_checked_worker_write",
                "a_fresh_run_accepts_a_disjoint_worker_write",
                "a_resumed_run_accepts_a_disjoint_worker_write",
            ),
        ),
        (
            Outlived,
            (
                "a_worker_process_left_behind_never_changes_what_mitosis_lands",
                "a_gate_process_left_behind_never_writes_the_checkout_after_its_run",
                "a_process_mitosis_cannot_end_stops_the_run_before_it_reads_the_clone",
                "a_sandbox_mark_names_its_own_processes_and_a_missing_mark_names_none",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
