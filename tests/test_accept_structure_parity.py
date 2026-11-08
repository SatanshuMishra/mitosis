import hashlib
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_accept_execution as accept
import test_accept_any_input as any_input
from test_accept_execution import tail


def spec_steps(*steps):
    return [{**step, "spec_ref": ["1.1"], "acceptance": []} for step in steps]


class ParityCase(any_input.AsksCase):
    def make_scratch(self):
        scratch = any_input.AsksCase.make_scratch(self)
        accept.put(os.path.join(scratch.repo, "spec.md"), accept.SPEC_DOCUMENT)
        return scratch

    def _spec_plan_only(self, items, *flags):
        scratch = self.scratch
        accept.put_json(
            os.path.join(scratch.stubs, "structure-reply.json"),
            {"items": items, "assumptions": [], "constraints": []},
        )
        return scratch.mitosis(
            "--spec",
            "spec.md",
            "--plan-only",
            "--run-dir",
            scratch.run_dir,
            "--feature-branch",
            "feature",
            "--decompose-command",
            scratch.stub("structure.py", "{session_id}", "{schema}"),
            "--reask-command",
            scratch.stub("fail.py", "{prompt}", "{session_id}", "{schema}"),
            *flags,
        )

    def _recorded(self, name, completed):
        path = os.path.join(self.scratch.stubs, name)
        self.assertTrue(os.path.isfile(path), "%s was not recorded" % name + tail(completed))
        return accept.read_text(path)


class ReconPromptTemplate(ParityCase):
    def the_structure_prompt_is_the_callers_template(self):
        completed = self._spec_plan_only(
            [{"name": "a", "files": ["a.py"], "spec_ref": ["1.1"], "acceptance": []}],
            "--recon-prompt-template",
            "ASK<{ask}>GRAPH<{graph}>",
        )
        self.exits(completed, 0)
        self.assertEqual(self._recorded("structure-prompt.txt", completed), "ASK<# Spec\n\n## 1.1 Build\n>GRAPH<>")


class DuplicateStructureNames(ParityCase):
    def a_repeated_structure_name_gets_a_dash_two_suffix(self):
        completed = self._spec_plan_only(
            spec_steps(
                {"name": "dup", "files": ["a.py"]},
                {"name": "dup", "files": ["b.py"]},
                {"name": "dup", "files": ["c.py"]},
                {"name": "tail", "files": ["d.py"], "after": ["dup"]},
            )
        )
        self.exits(completed, 0)
        steps = self.loaded(os.path.join(self.scratch.run_dir, "structure.json"), completed)
        self.assertEqual([step.get("name") for step in steps], ["dup", "dup-2", "dup-2-2", "tail"])
        self.assertEqual(steps[3].get("after"), ["dup"])


class PlanningModel(ParityCase):
    def _reader_run(self, models):
        replies = {"add a reader": any_input.reply(any_input.item("reader", ["reader.py"], "reader prints the count"))}
        return self._asks_run("add a reader\n", replies, models=models)

    def an_asks_call_gets_the_top_tier_model(self):
        completed = self._reader_run(("top=test-top", "cheap=test-cheap"))
        self.exits(completed, 0)
        calls = self._calls()
        self.assertEqual(len(calls), 1, tail(completed))
        argv = calls[0]["argv"]
        self.assertIn("--model", argv, argv)
        self.assertEqual(argv[argv.index("--model") + 1], "test-top", argv)

    def an_asks_template_using_model_with_no_top_model_is_refused(self):
        completed = self._reader_run(())
        self.exits(completed, 3)
        self.assertIn("the decompose command uses {model} but no model is mapped for tier top", completed.stderr)
        self.assertEqual(self._calls(), [], tail(completed))


class StructureEnvironment(ParityCase):
    def the_structure_call_carries_the_planners_recon_environment(self):
        completed = self._spec_plan_only([{"name": "a", "files": ["a.py"], "spec_ref": ["1.1"], "acceptance": []}])
        self.exits(completed, 0)
        recorded = json.loads(self._recorded("structure-env.json", completed))
        self.assertEqual(
            recorded,
            {"MITOSIS_RECON": "1", "MITOSIS_RECON_ASK": "# Spec\n\n## 1.1 Build\n", "MITOSIS_RECON_GRAPH": ""},
        )


class EmitItems(ParityCase):
    def the_structure_steps_are_written_where_the_caller_asked(self):
        target = self.scratch.path("emitted.json")
        completed = self._spec_plan_only(
            spec_steps({"name": "a", "files": ["a.py"]}, {"name": "b", "files": ["b.py"]}),
            "--emit-items",
            target,
        )
        self.exits(completed, 0)
        self.assertTrue(os.path.isfile(target), tail(completed))
        text = accept.read_text(target)
        emitted = json.loads(text)
        self.assertIsInstance(emitted, list, text)
        self.assertEqual([step.get("name") for step in emitted], ["a", "b"])
        self.assertEqual(text, json.dumps(emitted, indent=2))


class TextAlias(accept.Case):
    def a_step_with_text_and_no_task_is_planned_with_that_text(self):
        source = {
            "path": self.scratch.spec,
            "sha256": hashlib.sha256(accept.SPEC_DOCUMENT.encode("utf-8")).hexdigest(),
        }
        completed = self.scratch.plan_only(
            [
                {
                    "name": "a",
                    "text": "Build a from text",
                    "files": ["a.py"],
                    "source": source,
                    "spec_ref": ["1.1"],
                    "acceptance": [],
                }
            ]
        )
        self.exits(completed, 0)
        plan = self.plan(completed)
        self.assertEqual(((plan.get("items") or [{}])[0]).get("task"), "Build a from text", tail(completed))
        self.assertIn("Build a from text", ((plan.get("briefs") or [{}])[0]).get("text") or "")


def load_tests(loader, standard, pattern):
    helpers = ("make_scratch",)

    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            return sorted(
                name
                for name in vars(case)
                if not name.startswith("_") and callable(getattr(case, name)) and name not in helpers
            )

    suite = unittest.TestSuite()
    for case in (
        ReconPromptTemplate,
        DuplicateStructureNames,
        PlanningModel,
        StructureEnvironment,
        EmitItems,
        TextAlias,
    ):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite
