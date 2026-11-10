import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_accept_execution as accept
import test_accept_structure_parity as parity
from test_accept_execution import tail

DOCUMENT_ASK = "# Spec\n\n## 1.1 Build\n"
MODEL_SUPPLIED = ("name", "task", "files", "acceptance")

ARGV_STRUCTURE = r'''
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.stdin.read()
with open(os.path.join(here, "structure-argv.json"), "w", encoding="utf-8") as handle:
    json.dump(sys.argv[1:], handle)
with open(os.path.join(here, "structure-reply.json"), encoding="utf-8") as handle:
    reply = json.load(handle)
print(json.dumps({"type": "result", "session_id": sys.argv[1], "result": "done", "structured_output": {"reply": reply}}))
'''


def structure_reply(*steps):
    return {"items": list(steps), "assumptions": [], "constraints": []}


class StepFieldRecovery(parity.ParityCase):
    def _plan(self, first, again=None):
        scratch = self.scratch
        accept.put(os.path.join(scratch.stubs, "structure_argv.py"), ARGV_STRUCTURE)
        accept.put_json(os.path.join(scratch.stubs, "structure-reply.json"), first)
        self._control(reask_replies={DOCUMENT_ASK: {"reply": again}} if again else {})
        return scratch.mitosis(
            "--spec",
            "spec.md",
            "--plan-only",
            "--run-dir",
            scratch.run_dir,
            "--feature-branch",
            "feature",
            "--decompose-command",
            scratch.stub("structure_argv.py", "{session_id}", "{schema}"),
            "--reask-command",
            self._reask_command(),
        )

    def _first_call(self, completed):
        return json.loads(self._recorded("structure-argv.json", completed))

    def a_step_missing_its_acceptance_is_reasked_in_the_same_session(self):
        omitted = {"name": "a", "task": "Build a.", "files": ["a.py"], "spec_ref": ["1.1"]}
        completed = self._plan(structure_reply(omitted), structure_reply({**omitted, "acceptance": []}))
        self.exits(completed, 0)
        reasks = self._reasks()
        self.assertEqual(len(reasks), 1, tail(completed))
        self.assertEqual(reasks[0]["argv"][-2], self._first_call(completed)[0])
        self.assertIn("a: missing required field 'acceptance'", reasks[0]["argv"][0])
        steps = self.loaded(os.path.join(self.scratch.run_dir, "structure.json"), completed)
        self.assertEqual([(step.get("name"), step.get("acceptance")) for step in steps], [("a", [])])

    def the_structure_call_schema_requires_every_step_field_the_model_supplies(self):
        whole = {"name": "a", "task": "Build a.", "files": ["a.py"], "spec_ref": ["1.1"], "acceptance": []}
        completed = self._plan(structure_reply(whole))
        self.exits(completed, 0)
        schema = json.loads(self._first_call(completed)[1])
        self.assertEqual(sorted(schema["$defs"]["step"]["required"]), sorted(MODEL_SUPPLIED))
        self.assertEqual(self._reasks(), [])

    def a_split_step_carrying_a_role_is_reasked_in_the_same_session(self):
        builder = {"name": "a", "task": "Build a.", "files": ["a.py"], "spec_ref": ["1.1"], "acceptance": []}
        author = {**builder, "name": "lock-a", "files": ["tests/test_a.py"], "role": "check-author"}
        completed = self._plan(structure_reply(builder, author), structure_reply(builder))
        self.exits(completed, 0)
        reasks = self._reasks()
        self.assertEqual(len(reasks), 1, tail(completed))
        self.assertEqual(reasks[0]["argv"][-2], self._first_call(completed)[0])
        self.assertIn("no Step of a plan mitosis makes carries a role", reasks[0]["argv"][0])
        steps = self.loaded(os.path.join(self.scratch.run_dir, "structure.json"), completed)
        self.assertEqual([step.get("name") for step in steps], ["a"])


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name in (
        "a_step_missing_its_acceptance_is_reasked_in_the_same_session",
        "the_structure_call_schema_requires_every_step_field_the_model_supplies",
        "a_split_step_carrying_a_role_is_reasked_in_the_same_session",
    ):
        suite.addTest(StepFieldRecovery(name))
    return suite
