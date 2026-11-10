import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core
import run


def step(name, files, **extra):
    base = {
        "name": name,
        "task": "do " + name,
        "files": list(files),
        "source": None,
        "acceptance": [],
    }
    return {**base, **extra}


def check_author(name="write-checks", files=("tests/test_calc.py",), **extra):
    return step(name, files, role="check-author", **extra)


def calc_builder(files=("calc.py",), after=("write-checks",)):
    return step(
        "add",
        files,
        msp="calc",
        acceptance=[{"file": "tests/test_calc.py", "test": "Calc.adds"}],
        after=list(after),
    )


class WorkerPromptScope(unittest.TestCase):
    def test_a_worker_brief_limits_the_tests_it_runs(self):
        text = core.plan([step("a", ["a.py"])])["briefs"][0]["text"]
        self.assertIn(core.TEST_SCOPE_LINE, text)
        self.assertIn("full test suite", text)

    def test_a_check_author_brief_limits_the_tests_it_runs(self):
        text = core.plan([check_author(), calc_builder()])["check_authors"][0]["brief"]["text"]
        self.assertIn(core.TEST_SCOPE_LINE, text)
        self.assertIn("full test suite", text)

    def test_a_one_leaf_brief_states_its_task_once(self):
        sentence = "Teach the zebra planner to hum quietly at dusk"
        text = core.plan([step("a", ["a.py"], task=sentence)])["briefs"][0]["text"]
        self.assertEqual(text.count(sentence), 1)
        self.assertIn("Task: " + sentence, text.splitlines())

    def test_a_two_leaf_brief_keeps_every_task_in_its_json(self):
        first = "Teach the zebra planner to hum quietly at dusk"
        second = "Teach the otter planner to whistle at dawn"
        result = core.plan(
            [step("a", ["shared.py"], task=first), step("b", ["shared.py"], task=second)]
        )
        self.assertEqual([[leaf["name"] for leaf in brief["steps"]] for brief in result["briefs"]], [["a", "b"]])
        text = result["briefs"][0]["text"]
        self.assertIn('"task": "%s"' % first, text)
        self.assertIn('"task": "%s"' % second, text)
        self.assertEqual(text.count(first), 1)
        self.assertEqual(text.count(second), 1)

    def test_a_worker_is_asked_for_shorter_notes_than_the_contract_allows(self):
        schema = os.path.join(os.path.dirname(os.path.abspath(core.__file__)), "contracts", "h10-worker-return.schema.json")
        with open(schema, encoding="utf-8") as handle:
            allowed = json.load(handle)["properties"]["notes"]["maxLength"]
        asked = re.findall(r"at most (\d+) characters", core.RETURN_CONTRACT)
        self.assertEqual(len(asked), 1, core.RETURN_CONTRACT)
        self.assertLess(int(asked[0]), allowed)

    def test_a_one_leaf_prompt_template_still_carries_its_task(self):
        sentence = "Teach the zebra planner to hum quietly at dusk"
        plan = core.plan([step("a", ["a.py"], task=sentence)])
        self.assertIn(sentence, run.templated_task(plan, 0, "m", "{spec}"))

    def test_the_steps_of_a_one_leaf_brief_keep_their_task(self):
        sentence = "Teach the zebra planner to hum quietly at dusk"
        brief = core.plan([step("a", ["a.py"], task=sentence)])["briefs"][0]
        self.assertEqual(brief["steps"][0]["task"], sentence)


if __name__ == "__main__":
    unittest.main()
