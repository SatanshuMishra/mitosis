import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HEADER = {
    "type": "feat",
    "scope": "ship",
    "subject": "hand facts to render",
    "why": ("Bodies are composed twice.",),
    "impact": ("Every new body changes shape.",),
    "door": "two-way",
    "blast_radius": "Ship only.",
}

GATED = "1111111111111111111111111111111111111111"

PRINTED = '{"title":"feat(ship): fake title","body":"## Problem\\nFake body."}'


def plan_of(msps, items):
    return {"msps": msps, "items": items}


def first_plan():
    return plan_of(
        [{"label": "m", "steps": ["a", "b"]}],
        [
            {
                "name": "a",
                "task": "Build   the facts.  Then more.",
                "files": ["a.py"],
                "acceptance": [{"file": "tests/t.py", "test": "one"}],
            },
            {"name": "b", "task": "Wire render.", "files": ["b.py"], "acceptance": []},
        ],
    )


def first_gate(outcome="pass"):
    return {
        "commit": GATED,
        "properties": [{"step": "a", "file": "tests/t.py", "test": "one", "outcome": outcome}],
    }


def child_env():
    return {
        key: value
        for key, value in os.environ.items()
        if key not in ("CLAUDE_CODE_SESSION_ID", "PLUGIN_METRICS_PARENT", "SHIP")
    }


def all_values(value):
    if isinstance(value, dict):
        return [inner for item in value.values() for inner in all_values(item)]
    if isinstance(value, (list, tuple)):
        return [inner for item in value for inner in all_values(item)]
    return [value]


class Facts(unittest.TestCase):
    def a_gated_msp_states_its_steps_and_proofs(self):
        import pr_facts

        result = pr_facts.facts(HEADER, first_plan(), 0, first_gate())
        self.assertEqual(
            result,
            {
                "title": "feat(ship): hand facts to render",
                "problem": ["Bodies are composed twice."],
                "change": ["Build the facts.", "Wire render."],
                "impact": ["Every new body changes shape."],
                "testing": [
                    {"check": "tests/t.py::one", "result": "passes with the work and fails without it"},
                    {"check": "b", "result": "Not run: the Step names no acceptance test"},
                ],
            },
        )

    def blast_radius_stands_in_for_a_missing_impact(self):
        import pr_facts

        header = {key: value for key, value in HEADER.items() if key != "impact"}
        result = pr_facts.facts(header, first_plan(), 0, first_gate())
        self.assertEqual(result["impact"], ["Ship only."])
        self.assertFalse(
            any("two-way" in str(value) for value in all_values(result) + list(result))
        )

    def unproven_outcomes_read_not_run_with_a_reason(self):
        import pr_facts

        result = pr_facts.facts(
            HEADER,
            first_plan(),
            0,
            first_gate("inconclusive"),
            gate_error="boom",
            proven_head="2222222222222222222222222222222222222222",
        )
        self.assertEqual(
            result["testing"],
            [
                {"check": "tests/t.py::one", "result": "Not run: the acceptance command was inconclusive"},
                {"check": "b", "result": "Not run: the Step names no acceptance test"},
                {"check": "Acceptance gate", "result": "Not run: the gate could not run"},
                {
                    "check": "Proven head",
                    "result": "Not run: the caller proved 222222222222 after mitosis gated 111111111111",
                },
            ],
        )
        again = pr_facts.facts(
            HEADER,
            first_plan(),
            0,
            first_gate("not-applicable"),
            gate_error="boom",
            proven_head="2222222222222222222222222222222222222222",
        )
        self.assertEqual(
            again["testing"][0],
            {"check": "tests/t.py::one", "result": "Not run: the gate does not apply to it"},
        )

    def many_tests_fold_into_one_row_per_step(self):
        import pr_facts

        a_tests = ["one", "two", "three", "four", "five", "six"]
        c_tests = ["one", "two", "three", "four", "five"]
        plan = plan_of(
            [{"label": "m", "steps": ["a", "c"]}],
            [
                {
                    "name": "a",
                    "task": "Build a.",
                    "files": ["a.py"],
                    "acceptance": [{"file": "tests/a.py", "test": name} for name in a_tests],
                },
                {
                    "name": "c",
                    "task": "Build c.",
                    "files": ["c.py"],
                    "acceptance": [{"file": "tests/c.py", "test": name} for name in c_tests],
                },
            ],
        )
        gate = {
            "commit": GATED,
            "properties": [
                {"step": "a", "file": "tests/a.py", "test": name, "outcome": "pass"}
                for name in a_tests
            ]
            + [
                {
                    "step": "c",
                    "file": "tests/c.py",
                    "test": name,
                    "outcome": "inconclusive" if name == "five" else "pass",
                }
                for name in c_tests
            ],
        }
        result = pr_facts.facts(HEADER, plan, 0, gate)
        self.assertEqual(
            result["testing"],
            [
                {"check": "a", "result": "6 acceptance tests pass with the work and fail without it"},
                {
                    "check": "c",
                    "result": "4 of 5 acceptance tests pass with the work and fail without it; "
                    "the rest are not proven",
                },
            ],
        )

    def the_probe_states_the_worst_case(self):
        import pr_facts

        probe = pr_facts.probe_facts(HEADER, first_plan(), 0)
        self.assertEqual(
            probe["testing"],
            [
                {"check": "tests/t.py::one", "result": "passes with the work and fails without it"},
                {"check": "b", "result": "Not run: the Step names no acceptance test"},
                {"check": "Acceptance gate", "result": "Not run: the gate could not run"},
                {
                    "check": "Proven head",
                    "result": "Not run: the caller proved 000000000000 after mitosis gated 000000000000",
                },
            ],
        )
        self.assertNotIn("tradeoffs", probe)
        stated = pr_facts.facts(HEADER, first_plan(), 0)
        self.assertEqual(
            {key: value for key, value in probe.items() if key != "testing"},
            {key: value for key, value in stated.items() if key != "testing"},
        )

    def many_why_lines_become_one_problem_paragraph(self):
        import pr_facts

        six = ("One.", "Two.", "Three.", "Four.", "Five.", "Six.")
        result = pr_facts.facts({**HEADER, "why": six}, first_plan(), 0, first_gate())
        self.assertEqual(result["problem"], ["One. Two. Three. Four. Five. Six."])
        four = pr_facts.facts({**HEADER, "why": six[:4]}, first_plan(), 0, first_gate())
        self.assertEqual(four["problem"], ["One.", "Two.", "Three.", "Four."])

    def unsafe_paths_are_counted_not_named(self):
        import pr_facts

        result = pr_facts.facts(
            HEADER,
            first_plan(),
            0,
            first_gate(),
            findings={
                "undeclared": ["routes/$id.tsx", "tests/test_reverted.py", "ok.py"],
                "unwritten": ["a~b"],
                "crossing": [],
            },
        )
        self.assertEqual(
            result["tradeoffs"],
            [
                "Reconcile found 3 undeclared writes: ok.py and 2 more.",
                "Reconcile found 1 declared path never written.",
            ],
        )

    def open_problems_become_tradeoffs(self):
        import pr_facts

        result = pr_facts.facts(
            HEADER,
            first_plan(),
            0,
            first_gate(),
            findings={
                "undeclared": ["x.py", "y.py", "z.py", "w.py", "v.py"],
                "unwritten": ["u.py"],
                "crossing": [],
                "foreign": [],
            },
            exception={"producers": [1, 2]},
        )
        self.assertEqual(
            result["tradeoffs"],
            [
                "Reconcile found 5 undeclared writes: x.py, y.py, z.py and 2 more.",
                "Reconcile found 1 declared path never written: u.py.",
                "This MSP has 2 producers, so its base is the feature branch.",
            ],
        )

    def several_msps_label_the_title(self):
        import pr_facts

        plan = plan_of(
            [{"label": "m", "steps": ["a"]}, {"label": "n", "steps": ["b"]}],
            first_plan()["items"],
        )
        self.assertEqual(
            pr_facts.facts(HEADER, plan, 1)["title"], "feat(ship): hand facts to render (n)"
        )
        self.assertEqual(
            pr_facts.facts({**HEADER, "scope": None}, plan, 1)["title"],
            "feat: hand facts to render (n)",
        )


class Render(unittest.TestCase):
    def _folder(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _script(self, name, body):
        path = os.path.join(self.tmp, name)
        calls = os.path.join(self.tmp, name + ".calls")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                "#!/usr/bin/env python3\n"
                "import json, shutil, sys, time\n"
                "with open(%r, 'a') as calls:\n"
                "    calls.write(json.dumps(sys.argv) + '\\n')\n" % calls + body
            )
        os.chmod(path, 0o755)
        return path, calls

    def _lines(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read().splitlines()

    def returns_what_render_printed(self):
        import pr_facts

        self._folder()
        copy = os.path.join(self.tmp, "fields.copy.json")
        command, calls = self._script(
            "render",
            "shutil.copyfile(sys.argv[sys.argv.index('--fields') + 1], %r)\n"
            "print(%r)\n" % (copy, PRINTED),
        )
        path = os.path.join(self.tmp, "new", "f.json")
        result = pr_facts.render(command, {"title": "feat: x", "problem": ["P."]}, path, 10, None)
        self.assertEqual(result, ("feat(ship): fake title", "## Problem\nFake body."))
        recorded = self._lines(calls)
        self.assertEqual(len(recorded), 1)
        self.assertEqual(json.loads(recorded[0])[-3:], ["render", "--fields", path])
        with open(copy, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"title": "feat: x", "problem": ["P."]})

    def refuses_each_failure_and_runs_once(self):
        import pr_facts

        self._folder()
        path = os.path.join(self.tmp, "facts", "f.json")
        missing = os.path.join(self.tmp, "no-such-command")
        old = self._script(
            "old",
            "sys.stderr.write('pull-request render: unknown flag \"--fields\"; "
            "the accepted flags are --title\\n')\n"
            "sys.exit(2)\n",
        )
        refused = self._script(
            "refused",
            "sys.stderr.write('pull-request render: --change: a value is empty\\n')\n"
            "sys.stderr.write('pull-request render: --impact: at least one value is required\\n')\n"
            "sys.exit(2)\n",
        )
        slow = self._script("slow", "time.sleep(5)\n")
        garbled = self._script("garbled", "print('not json')\n")
        cases = (
            (missing, 10, ("install pull-request 0.3.0 or later",)),
            (old[0], 10, ("older than 0.3.0", "install pull-request 0.3.0 or later")),
            (
                refused[0],
                10,
                (
                    "exited 2",
                    path,
                    "pull-request render: --change: a value is empty; "
                    "pull-request render: --impact: at least one value is required",
                ),
            ),
            (slow[0], 1, ("timed out",)),
            (garbled[0], 10, ("printed no title and body",)),
        )
        for command, timeout, expected in cases:
            with self.assertRaises(pr_facts.RenderError) as raised:
                pr_facts.render(command, {"title": "feat: x"}, path, timeout, child_env())
            for text in expected:
                self.assertIn(text, str(raised.exception))
        for _, calls in (old, refused, slow, garbled):
            self.assertEqual(len(self._lines(calls)), 1)


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
    for case in (Facts, Render):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
