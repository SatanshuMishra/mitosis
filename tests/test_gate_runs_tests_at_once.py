import glob
import os
import re
import shlex
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import run
import test_every_finding as fixtures
import test_finishes_the_work as finishes

MEET_LIMIT = 10
RAN_IN = re.compile(r"^ran-in (\S+) among (\d+)$", re.MULTILINE)
INSTALLED_BY = re.compile(r"^installed-by (\S+)$", re.MULTILINE)
FAILS_BESIDE = "fails beside its peer"
FAILS_ALONE = "fails when it runs alone"
UNMARKED = "its checkout holds no mark"
NO_DEPENDENCY = "the folder the repository installed is missing"
UNJUDGED_EXIT = 3
HELD = 21
WAITS = (sys.executable, "-c", "import sys; sys.stdin.read()")

HEAD = '''import glob
import os
import signal
import subprocess
import sys
import unittest

import a

MEET = %(meet)r
ONCE = %(once)r
UNJUDGED_EXIT = %(unjudged)d
OUTSIDE = %(outside)r
PIDS = %(pids)r
SENTINEL = %(sentinel)r


def report():
    here = os.getcwd()
    beside = glob.glob(os.path.join(os.path.dirname(here), %(checkouts)r))
    print("ran-in %%s among %%d" %% (os.path.basename(here), len(beside)), flush=True)


def send(text="met", path=MEET):
    signal.alarm(%(limit)d)
    with open(path, "w") as pipe:
        pipe.write(text)
    signal.alarm(0)


def receive(path=MEET):
    signal.alarm(%(limit)d)
    with open(path) as pipe:
        met = pipe.read()
    signal.alarm(0)
    return met


def mark():
    open(os.path.join("node_modules", "marked"), "w").close()


def marked():
    return os.path.exists(os.path.join("node_modules", "marked"))


def ended(pid):
    try:
        os.kill(pid, signal.SIGKILL)
    except PermissionError:
        return False
    return True


def own_child_ends():
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )
    child.kill()
    return child.wait()


def reach_outside(name):
    try:
        with open(os.path.join(OUTSIDE, "escaped-" + name), "w") as handle:
            handle.write("escaped")
    except OSError:
        pass


class A(unittest.TestCase):
'''


def method(name, *lines):
    return "    def %s(self):\n%s" % (name, "".join("        %s\n" % line for line in lines))


def plain(name):
    return method(name, "report()", "self.assertEqual(a.value(), 2)")


TESTS = {
    **{name: plain(name) for name in ("first", "second", "third", "fourth", "fifth")},
    "left": method("left", "report()", "send()"),
    "right": method("right", "report()", 'self.assertEqual(receive(), "met")'),
    "left_reaching": method("left_reaching", "report()", 'reach_outside("left")', "send()"),
    "right_reaching": method(
        "right_reaching", "report()", 'reach_outside("right")', 'self.assertEqual(receive(), "met")'
    ),
    "fragile": method(
        "fragile",
        "report()",
        "if os.path.exists(ONCE):",
        "    return",
        'open(ONCE, "w").close()',
        "send()",
        "self.fail(%r)" % FAILS_BESIDE,
    ),
    "fragile_reaching": method(
        "fragile_reaching",
        "report()",
        'reach_outside("fragile")',
        "if os.path.exists(ONCE):",
        "    return",
        'open(ONCE, "w").close()',
        "self.fail(%r)" % FAILS_BESIDE,
    ),
    "fragile_needing": method(
        "fragile_needing",
        "report()",
        "if os.path.exists(ONCE):",
        '    self.assertTrue(os.path.exists(os.path.join("node_modules", "dep")), %r)' % NO_DEPENDENCY,
        "    return",
        'open(ONCE, "w").close()',
        "self.fail(%r)" % FAILS_BESIDE,
    ),
    "failing_then_unjudged": method(
        "failing_then_unjudged",
        "report()",
        "if os.path.exists(ONCE):",
        "    os._exit(UNJUDGED_EXIT)",
        'open(ONCE, "w").close()',
        "self.fail(%r)" % FAILS_BESIDE,
    ),
    "unjudged_then_failing": method(
        "unjudged_then_failing",
        "report()",
        "if not os.path.exists(ONCE):",
        '    open(ONCE, "w").close()',
        "    os._exit(UNJUDGED_EXIT)",
        "self.fail(%r)" % FAILS_ALONE,
    ),
    "judged": method("judged", "report()", "self.assertTrue(marked(), %r)" % UNMARKED),
    "left_marking": method("left_marking", "report()", "mark()", "send()"),
    "right_marking": method(
        "right_marking", "report()", "mark()", 'self.assertEqual(receive(), "met")'
    ),
    "left_ending": method(
        "left_ending",
        "report()",
        'print("outside " + ("ended" if ended(SENTINEL) else "refused"), flush=True)',
        "refused = not ended(int(receive(PIDS)))",
        'print("beside " + ("refused" if refused else "ended"), flush=True)',
        "self.assertEqual(own_child_ends(), -signal.SIGKILL)",
        "if refused:",
        "    send()",
    ),
    "right_ended": method(
        "right_ended",
        "report()",
        "if os.path.exists(ONCE):",
        "    return",
        'open(ONCE, "w").close()',
        "send(str(os.getpid()), PIDS)",
        'self.assertEqual(receive(), "met")',
    ),
}

INSTALLING = r'''
import os
import sys
import uuid

folder = os.path.join(os.getcwd(), "node_modules")
if not os.path.isdir(folder):
    os.mkdir(folder)
    with open(os.path.join(folder, "installed-by"), "w") as handle:
        handle.write(uuid.uuid4().hex)
with open(os.path.join(folder, "installed-by")) as handle:
    print("installed-by " + handle.read(), flush=True)
here = os.path.dirname(os.path.abspath(__file__))
exec(open(os.path.join(here, "accept_real.py")).read())
'''


class GateRunsTestsAtOnce(unittest.TestCase):
    def setUp(self):
        self.scratch = None

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()

    def _run(
        self,
        names,
        flags=lambda scratch: (),
        accept=None,
        meet_in=lambda scratch: scratch.tmp,
        sentinel=None,
        rooted=False,
    ):
        self.scratch = finishes.Scratch()
        scratch = self.scratch
        if rooted:
            fixtures.put(os.path.join(scratch.repo, "node_modules", "dep"), "installed\n")
        meet, pids = (os.path.join(meet_in(scratch), name) for name in ("meet", "pids"))
        os.mkfifo(meet)
        os.mkfifo(pids)
        text = HEAD % {
            "meet": meet,
            "pids": pids,
            "sentinel": sentinel,
            "once": os.path.join(meet_in(scratch), "ran-once"),
            "outside": scratch.run_dir,
            "checkouts": run.GATE_FOLDER_PREFIX + "*",
            "limit": MEET_LIMIT,
            "unjudged": UNJUDGED_EXIT,
        } + "\n".join(TESTS[name] for name in names)
        attempt = {"writes": {"a.py": finishes.RIGHT, finishes.TEST_FILE: text}}
        if accept is not None:
            fixtures.put(os.path.join(scratch.stubs, "accept_real.py"), fixtures.ACCEPT)
            fixtures.put(os.path.join(scratch.stubs, "accept.py"), accept)
        properties = [{"file": finishes.TEST_FILE, "test": "A.%s" % name} for name in names]
        return scratch.built(
            [finishes.built_step(acceptance=properties)],
            {"first": [attempt, attempt]},
            flags=flags(scratch),
        )

    def _built(self, names, **given):
        completed, summary = self._run(names, **given)
        scratch = self.scratch
        self.assertEqual(completed.returncode, 0, fixtures.tail(completed))
        gate = finishes.msp_of(summary, "first")["gate"]
        self.assertEqual(
            [entry["outcome"] for entry in gate["properties"]],
            ["pass"] * len(names),
            fixtures.tail(completed),
        )
        self.assertEqual(len(scratch.attempts("first")), 1, fixtures.tail(completed))
        return completed, summary

    def _gate_file(self, name):
        return os.path.join(self.scratch.run_dir, "gate", "msp-0", name)

    def _log(self, index):
        with open(self._gate_file("%d-with-work.log" % index), encoding="utf-8") as handle:
            return handle.read()

    def _ran_in(self, index):
        found = RAN_IN.findall(self._log(index))
        self.assertEqual(len(found), 1, "test %d reported %r" % (index, found))
        return found[0][0], int(found[0][1])

    def _left_behind(self):
        gate = os.path.join(self.scratch.run_dir, "gate")
        return sorted(
            glob.glob(os.path.join(gate, "msp-0", run.GATE_FOLDER_PREFIX + "*"))
            + glob.glob(os.path.join(gate, "msp-0.*"))
        )

    def the_tests_after_the_first_run_at_once_each_in_its_own_checkout(self):
        completed, _ = self._built(("first", "left", "right"))
        left, right = self._ran_in(1), self._ran_in(2)
        self.assertNotEqual(left[0], right[0], "two tests shared a checkout" + fixtures.tail(completed))
        self.assertEqual((left[1], right[1]), (2, 2), fixtures.tail(completed))
        self.assertEqual(self._left_behind(), [], fixtures.tail(completed))

    def the_gate_keeps_no_more_checkouts_than_the_build_runs_at_once(self):
        names = ("first", "second", "third", "fourth", "fifth")
        completed, _ = self._built(names, flags=lambda scratch: ("--concurrency", "2"))
        self.assertEqual(
            [self._ran_in(index)[1] for index in range(len(names))],
            [2] * len(names),
            fixtures.tail(completed),
        )
        self.assertEqual(self._left_behind(), [], fixtures.tail(completed))

    def a_gate_setup_command_keeps_the_gate_to_one_checkout(self):
        names = ("first", "second", "third", "fourth")
        setup = "%s -c pass" % shlex.quote(sys.executable)
        completed, _ = self._built(names, flags=lambda scratch: ("--gate-setup", setup))
        self.assertEqual(
            [self._ran_in(index)[1] for index in range(len(names))],
            [1] * len(names),
            fixtures.tail(completed),
        )

    def the_folder_the_first_test_installs_reaches_every_checkout(self):
        completed, _ = self._built(("first", "left", "right"), accept=INSTALLING)
        installed = [INSTALLED_BY.findall(self._log(index)) for index in range(3)]
        self.assertEqual([len(found) for found in installed], [1, 1, 1], fixtures.tail(completed))
        self.assertNotEqual(self._ran_in(1)[0], self._ran_in(2)[0], fixtures.tail(completed))
        if sys.platform == "darwin":
            self.assertEqual(
                len({found[0] for found in installed}),
                1,
                "a checkout installed the folder again: %r" % installed + fixtures.tail(completed),
            )

    def a_test_that_fails_beside_others_is_run_again_alone_before_it_is_judged(self):
        completed, _ = self._built(("first", "fragile", "right"))
        with open(self._gate_file("1-with-work.beside.log.err"), encoding="utf-8") as handle:
            self.assertIn(FAILS_BESIDE, handle.read(), fixtures.tail(completed))
        with open(self._gate_file("1-with-work.log.err"), encoding="utf-8") as handle:
            self.assertNotIn(FAILS_BESIDE, handle.read(), fixtures.tail(completed))
        again = self._ran_in(1)
        self.assertNotIn(again[0], (self._ran_in(0)[0], self._ran_in(2)[0]), fixtures.tail(completed))
        self.assertEqual(again[1], 3, fixtures.tail(completed))
        self.assertEqual(self._left_behind(), [], fixtures.tail(completed))

    def a_test_run_again_alone_finds_the_folders_the_repository_installed(self):
        completed, _ = self._built(("first", "fragile_needing", "second"), rooted=True)
        with open(self._gate_file("1-with-work.beside.log.err"), encoding="utf-8") as handle:
            self.assertIn(FAILS_BESIDE, handle.read(), fixtures.tail(completed))

    def a_failure_beside_others_stands_when_the_run_alone_cannot_be_judged(self):
        completed, summary = self._run(("first", "failing_then_unjudged", "second"))
        self.assertEqual(completed.returncode, HELD, fixtures.tail(completed))
        verdict = finishes.msp_of(summary, "first")["gate"]["properties"][1]
        self.assertEqual(
            (verdict["outcome"], verdict["reason"]),
            ("inconclusive", run.FAILS_WITH_THE_WORK),
            fixtures.tail(completed),
        )
        self.assertEqual(self._ran_in(1)[0], run.ALONE_FOLDER, fixtures.tail(completed))
        with open(self._gate_file("1-with-work.beside.log.err"), encoding="utf-8") as handle:
            self.assertIn(FAILS_BESIDE, handle.read(), fixtures.tail(completed))

    def a_failure_alone_holds_a_test_that_nothing_judged_beside_others(self):
        completed, _ = self._run(("first", "unjudged_then_failing", "second"))
        self.assertEqual(completed.returncode, HELD, fixtures.tail(completed))
        with open(self._gate_file("1-with-work.log.err"), encoding="utf-8") as handle:
            self.assertIn(FAILS_ALONE, handle.read(), fixtures.tail(completed))

    def a_later_test_cannot_reach_the_checkout_an_earlier_one_is_run_again_in(self):
        completed, summary = self._run(
            ("first", "judged", "left_marking", "right_marking"),
            flags=lambda scratch: ("--concurrency", "2"),
            accept=INSTALLING,
        )
        self.assertEqual(completed.returncode, HELD, fixtures.tail(completed))
        gate = finishes.msp_of(summary, "first")["gate"]
        self.assertEqual(
            [entry["outcome"] for entry in gate["properties"]],
            ["pass", "inconclusive", "pass", "pass"],
            fixtures.tail(completed),
        )
        with open(self._gate_file("1-with-work.log.err"), encoding="utf-8") as handle:
            self.assertIn(UNMARKED, handle.read(), fixtures.tail(completed))
        self.assertNotIn(
            self._ran_in(1)[0],
            [self._ran_in(index)[0] for index in (0, 2, 3)],
            fixtures.tail(completed),
        )

    def a_test_cannot_end_a_process_outside_its_own_run(self):
        if sys.platform != "darwin":
            self.skipTest("sandbox-exec exists only on macOS")
        sentinel = subprocess.Popen(WAITS, stdin=subprocess.PIPE)
        self.addCleanup(sentinel.wait)
        self.addCleanup(sentinel.stdin.close)
        completed, summary = self._built(
            ("first", "left_ending", "right_ended"),
            flags=lambda scratch: ("--worker-sandbox", "--worker-write", scratch.stubs),
            meet_in=lambda scratch: scratch.stubs,
            sentinel=sentinel.pid,
        )
        self.assertEqual(summary.get("sandbox"), "sandbox-exec", fixtures.tail(completed))
        self.assertIsNone(sentinel.poll(), "a test ended a process outside every sandbox")
        self.assertIn("outside refused", self._log(1), fixtures.tail(completed))
        self.assertIn("beside refused", self._log(1), fixtures.tail(completed))
        self.assertEqual(
            [name for name in os.listdir(self._gate_file("")) if ".beside." in name],
            [],
            fixtures.tail(completed),
        )

    def each_checkout_keeps_its_test_inside_its_own_sandbox(self):
        if sys.platform != "darwin":
            self.skipTest("sandbox-exec exists only on macOS")
        completed, summary = self._built(
            ("first", "left_reaching", "right_reaching", "fragile_reaching"),
            flags=lambda scratch: ("--worker-sandbox", "--worker-write", scratch.stubs),
            meet_in=lambda scratch: scratch.stubs,
        )
        self.assertEqual(summary.get("sandbox"), "sandbox-exec", fixtures.tail(completed))
        self.assertNotEqual(self._ran_in(1)[0], self._ran_in(2)[0], fixtures.tail(completed))
        self.assertTrue(
            os.path.exists(self._gate_file("3-with-work.beside.log")),
            "no test was run again alone" + fixtures.tail(completed),
        )
        self.assertEqual(
            glob.glob(os.path.join(self.scratch.run_dir, "escaped-*")),
            [],
            "a test wrote outside its checkout" + fixtures.tail(completed),
        )
        self.assertEqual(self._left_behind(), [], fixtures.tail(completed))


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        GateRunsTestsAtOnce(name)
        for name in (
            "the_tests_after_the_first_run_at_once_each_in_its_own_checkout",
            "the_gate_keeps_no_more_checkouts_than_the_build_runs_at_once",
            "a_gate_setup_command_keeps_the_gate_to_one_checkout",
            "the_folder_the_first_test_installs_reaches_every_checkout",
            "a_test_that_fails_beside_others_is_run_again_alone_before_it_is_judged",
            "a_test_run_again_alone_finds_the_folders_the_repository_installed",
            "a_failure_beside_others_stands_when_the_run_alone_cannot_be_judged",
            "a_failure_alone_holds_a_test_that_nothing_judged_beside_others",
            "a_later_test_cannot_reach_the_checkout_an_earlier_one_is_run_again_in",
            "a_test_cannot_end_a_process_outside_its_own_run",
            "each_checkout_keeps_its_test_inside_its_own_sandbox",
        )
    )
