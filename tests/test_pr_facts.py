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

PRINTED_BODY = (
    "## Problem\nBodies are composed twice.\n\n## Change\n- Wire render.\n\n"
    "## Impact\nEvery new body changes shape.\n\n"
    "## Testing\n| Check | Result |\n|---|---|\n| tests/test_pr_facts.py | passed |"
)

PRINTED = json.dumps({"title": "feat(ship): fake title", "body": PRINTED_BODY})

FIELDS = {
    "title": "feat(ship): hand facts to render",
    "problem": ["Bodies are composed twice."],
    "change": ["Wire render."],
    "impact": ["Every new body changes shape."],
    "testing": [{"check": "tests/test_pr_facts.py", "result": "passed"}],
}


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
                    {"check": "tests/t.py::one", "result": "passes against the finished work"},
                    {"check": "b", "result": "Not run: the Step names no acceptance test"},
                ],
            },
        )

    def steps_opening_with_one_sentence_each_get_their_own_line(self):
        import handoff
        import pr_facts

        shared = "The repository is the pipeline Claude Code plugin. Then its own work."
        plan = plan_of(
            [{"label": "m", "steps": ["author", "naming", "feature-tip"]}],
            [
                {"name": "author", "task": "Write the tests first.", "files": ["t.py"], "acceptance": []},
                {"name": "naming", "task": shared, "files": ["a.py"], "acceptance": []},
                {"name": "feature-tip", "task": shared, "files": ["b.py"], "acceptance": []},
            ],
        )
        result = pr_facts.probe_facts(HEADER, plan, 0)
        self.assertEqual(handoff.errors("h15-pr-fields", result), ())
        self.assertEqual(
            result["change"],
            ["Write the tests first.", "Build the Step naming.", "Build the Step feature-tip."],
        )

    def an_opening_the_format_refuses_gives_way_to_the_steps_name(self):
        import handoff
        import pr_facts

        plan = plan_of(
            [{"label": "m", "steps": ["home", "lower", "reverted-home"]}],
            [
                {"name": "home", "task": "A run home lives under ~/.mitosis/repo.", "files": ["a.py"], "acceptance": []},
                {"name": "lower", "task": "pipeline clean removes links.", "files": ["b.py"], "acceptance": []},
                {"name": "reverted-home", "task": "", "files": ["c.py"], "acceptance": []},
            ],
        )
        result = pr_facts.probe_facts(HEADER, plan, 0)
        self.assertEqual(handoff.errors("h15-pr-fields", result), ())
        self.assertEqual(
            result["change"],
            ["Build the Step home.", "Build the Step lower.", "Build Step 3."],
        )

    def more_steps_than_the_format_lists_are_counted_in_a_last_line(self):
        import handoff
        import pr_facts

        names = ["s%d" % number for number in range(1, 16)]
        plan = plan_of(
            [{"label": "m", "steps": names}],
            [{"name": name, "task": "Do %s." % name, "files": [name], "acceptance": []} for name in names],
        )
        result = pr_facts.probe_facts(HEADER, plan, 0)
        self.assertEqual(handoff.errors("h15-pr-fields", result), ())
        self.assertEqual(len(result["change"]), 12)
        self.assertEqual(result["change"][-1], "Build 4 more Steps.")

    def openings_past_the_sections_word_ceiling_give_way_to_step_names(self):
        import pr_facts

        names = ["s%d" % number for number in range(1, 11)]
        plan = plan_of(
            [{"label": "m", "steps": names}],
            [
                {"name": name, "task": "Change %s %s." % (name, "word " * 60), "files": [name], "acceptance": []}
                for name in names
            ],
        )
        result = pr_facts.probe_facts(HEADER, plan, 0)
        self.assertEqual(result["change"], ["Build the Step %s." % name for name in names])

    def a_test_name_the_format_refuses_gives_way_to_a_numbered_check(self):
        import handoff
        import pr_facts

        plan = plan_of(
            [{"label": "m", "steps": ["a"]}],
            [
                {
                    "name": "a",
                    "task": "Build the facts.",
                    "files": ["a.py"],
                    "acceptance": [{"file": "tests/t.py", "test": "a_reverted_lane_lands_again"}],
                },
            ],
        )
        result = pr_facts.probe_facts(HEADER, plan, 0)
        self.assertEqual(handoff.errors("h15-pr-fields", result), ())
        self.assertEqual(result["testing"][0]["check"], "Acceptance test 1")

    def more_rows_than_the_format_lists_fold_into_their_totals(self):
        import pr_facts

        names = ["s%d" % number for number in range(1, 15)]
        plan = plan_of(
            [{"label": "m", "steps": names}],
            [
                {
                    "name": name,
                    "task": "Do %s." % name,
                    "files": [name],
                    "acceptance": [{"file": "tests/t.py", "test": name}] if name == "s1" else [],
                }
                for name in names
            ],
        )
        gate = {
            "commit": GATED,
            "properties": [{"step": "s1", "file": "tests/t.py", "test": "s1", "outcome": "pass"}],
        }
        self.assertEqual(
            pr_facts.facts(HEADER, plan, 0, gate)["testing"],
            [
                {"check": "Acceptance tests", "result": "1 acceptance test passes against the finished work"},
                {"check": "Steps with no acceptance test", "result": "Not run: 13 Steps name no acceptance test"},
            ],
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

    def a_caller_classed_property_is_never_reported_proven(self):
        import pr_facts

        for name, outcome in (("covered-elsewhere", "pass"), ("not-applicable", "not-applicable")):
            gate = first_gate(outcome)
            classed = {**gate, "properties": [{**gate["properties"][0], "class": name}]}
            self.assertEqual(
                pr_facts.facts(HEADER, first_plan(), 0, classed)["testing"][0],
                {
                    "check": "tests/t.py::one",
                    "result": "Not run: the caller classed it %s, so no run judged it" % name,
                },
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
                {"check": "a", "result": "6 acceptance tests pass against the finished work"},
                {
                    "check": "c",
                    "result": "4 of 5 acceptance tests pass against the finished work; "
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
                {"check": "tests/t.py::one", "result": "passes against the finished work"},
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
        result = pr_facts.render(command, FIELDS, path, None)
        self.assertEqual(result, ("feat(ship): fake title", PRINTED_BODY))
        recorded = self._lines(calls)
        self.assertEqual(len(recorded), 1)
        self.assertEqual(json.loads(recorded[0])[-3:], ["render", "--fields", path])
        with open(copy, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), FIELDS)

    def refuses_facts_h15_refuses_before_writing_or_running(self):
        import pr_facts

        self._folder()
        command, calls = self._script("render", "print(%r)\n" % PRINTED)
        path = os.path.join(self.tmp, "facts", "f.json")
        with self.assertRaises(pr_facts.RenderError) as raised:
            pr_facts.render(command, {"title": "feat: x", "problem": ["P."]}, path, None)
        message = str(raised.exception)
        for text in (path, "mitosis", "h15-pr-fields", "change", "impact", "testing"):
            self.assertIn(text, message)
        self.assertFalse(os.path.exists(path))
        self.assertFalse(os.path.exists(calls))

    def refuses_a_reply_with_more_than_one_json_value(self):
        import pr_facts

        self._folder()
        command, calls = self._script("render", "print('[]')\nprint(%r)\n" % PRINTED)
        with self.assertRaises(pr_facts.RenderError) as raised:
            pr_facts.render(command, FIELDS, os.path.join(self.tmp, "f.json"), None)
        for text in ("pull-request render", "h16-pr-render"):
            self.assertIn(text, str(raised.exception))
        self.assertEqual(len(self._lines(calls)), 1)

    def waits_for_a_slow_render_to_print(self):
        import pr_facts

        self._folder()
        command, calls = self._script("slow", "time.sleep(2)\nprint(%r)\n" % PRINTED)
        path = os.path.join(self.tmp, "slow-facts", "f.json")
        result = pr_facts.render(command, FIELDS, path, child_env())
        self.assertEqual(result, ("feat(ship): fake title", PRINTED_BODY))
        self.assertEqual(len(self._lines(calls)), 1)

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
        garbled = self._script("garbled", "print('not json')\n")
        cases = (
            (missing, ("install pull-request 0.3.0 or later",)),
            (old[0], ("older than 0.3.0", "install pull-request 0.3.0 or later")),
            (
                refused[0],
                (
                    "exited 2",
                    path,
                    "pull-request render: --change: a value is empty; "
                    "pull-request render: --impact: at least one value is required",
                ),
            ),
            (garbled[0], ("printed no title and body", "pull-request render", "h16-pr-render")),
        )
        for command, expected in cases:
            with self.assertRaises(pr_facts.RenderError) as raised:
                pr_facts.render(command, FIELDS, path, child_env())
            for text in expected:
                self.assertIn(text, str(raised.exception))
        for _, calls in (old, refused, garbled):
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
