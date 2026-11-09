import copy
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MITOSIS = os.path.join(ROOT, "mitosis.py")
CALL_LIMIT = 60
RUN_LIMIT = 400
STRIPPED = ("CLAUDE_CODE_SESSION_ID", "PLUGIN_METRICS_PARENT", "SHIP")
GIT_IDENTITY = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "mitosis test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "mitosis test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def module(name):
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    return importlib.import_module(name)


def put(path, content):
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)
    return path


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def tail(completed):
    return "\n--- stdout ---\n%s\n--- stderr ---\n%s" % (
        completed.stdout[-4000:],
        completed.stderr[-4000:],
    )


class ItemsRead(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="mitosis-items-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        kept = {key: value for key, value in os.environ.items() if key not in STRIPPED}
        self.env = {
            **kept,
            **GIT_IDENTITY,
            "HOME": os.path.join(self.tmp, "home"),
            "PLUGIN_METRICS_DIR": os.path.join(self.tmp, "metrics"),
        }
        os.makedirs(self.env["HOME"])
        os.makedirs(self.repo)
        put(os.path.join(self.repo, "README.md"), "seed\n")
        put(os.path.join(self.repo, "tests", "test_api.py"), "import unittest\n")
        put(os.path.join(self.repo, "tests", "test_use.py"), "import unittest\n")
        for args in (
            ("init", "-q", "-b", "main", self.repo),
            ("-C", self.repo, "add", "-A"),
            ("-C", self.repo, "commit", "-q", "-m", "seed"),
        ):
            completed = subprocess.run(
                ["git", *args],
                cwd=self.tmp,
                env=self.env,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=CALL_LIMIT,
            )
            if completed.returncode != 0:
                raise RuntimeError("scaffold git %s failed: %s" % (" ".join(args), completed.stderr))
        spec = put(os.path.join(self.tmp, "spec.md"), "# Api\n\n## 5.1 Define\n\n## 5.2 Use\n")
        source = {"path": spec, "sha256": hashlib.sha256(read_bytes(spec)).hexdigest()}
        self.items = [
            {
                "name": "define-api",
                "task": "Define the api shape.",
                "files": ["src/api.py"],
                "source": source,
                "acceptance": [{"file": "tests/test_api.py", "test": "Api.shape", "check": "C1.1"}],
                "spec_ref": ["5.1"],
                "type": "contract",
                "contract_group": "api",
                "complexity": "simple",
            },
            {
                "name": "use-api",
                "task": "Call the api.",
                "files": ["src/use.py"],
                "source": source,
                "acceptance": [{"file": "tests/test_use.py", "test": "Use.calls"}],
                "spec_ref": ["5.2"],
                "after": ["define-api"],
            },
        ]

    def _planned(self, name, document):
        path = put(os.path.join(self.tmp, name + ".json"), document)
        run_dir = os.path.join(self.tmp, "runs", name)
        completed = subprocess.run(
            [sys.executable, MITOSIS, "--items", path, "--plan-only", "--run-dir", run_dir],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=RUN_LIMIT,
        )
        return completed, path, os.path.join(run_dir, "plan.json")

    def _changed(self, change):
        items = copy.deepcopy(self.items)
        change(items)
        return items

    def an_items_file_meeting_h12_with_check_ids_is_planned(self):
        handoff = module("handoff")
        self.assertEqual(handoff.errors("h12-interphase-items", self.items), ())
        completed, _, plan_path = self._planned("valid", json.dumps(self.items))
        self.assertEqual(completed.returncode, 0, tail(completed))
        planned = {item["name"]: item for item in read_json(plan_path)["items"]}
        self.assertIn(
            {"file": "tests/test_api.py", "test": "Api.shape", "check": "C1.1"},
            planned["define-api"]["acceptance"],
        )

    def an_unknown_key_is_refused_naming_interphase_and_h12(self):
        handoff = module("handoff")
        self.assertEqual(handoff.errors("h12-interphase-items", self.items), ())
        for name, change in (
            ("priority", lambda items: items[0].update(priority=1)),
            ("given", lambda items: items[0]["acceptance"][0].update(given="x")),
        ):
            completed, path, plan_path = self._planned(name, json.dumps(self._changed(change)))
            self.assertEqual(completed.returncode, 3, name + tail(completed))
            for part in (path, "mitosis-items", "additionalProperties"):
                self.assertIn(part, completed.stderr, name)
            self.assertFalse(os.path.exists(plan_path), name)

    def _plan_of(self, name, change):
        completed, _, plan_path = self._planned(name, json.dumps(self._changed(change)))
        self.assertEqual(completed.returncode, 0, name + tail(completed))
        with open(plan_path, encoding="utf-8") as handle:
            return json.load(handle)

    def a_complexity_outside_the_simple_words_plans_at_the_top_tier(self):
        plan = self._plan_of("medium", lambda items: items[0].update(complexity="medium"))
        self.assertEqual(plan["tiers"][self.items[0]["name"]], "top")

    def a_type_the_interphase_schema_refuses_is_carried_into_the_plan(self):
        plan = self._plan_of("fix", lambda items: items[1].update(type="fix"))
        self.assertEqual(plan["items"][1]["type"], "fix")

    def a_document_that_is_not_a_non_empty_array_of_items_is_refused(self):
        handoff = module("handoff")
        self.assertEqual(handoff.errors("h12-interphase-items", self.items), ())
        unreferenced = self._changed(lambda items: items[1].pop("spec_ref"))
        for name, document in (("object", "{}"), ("empty", "[]")):
            completed, _, plan_path = self._planned(name, document)
            self.assertEqual(completed.returncode, 3, name + tail(completed))
            self.assertIn("mitosis-items", completed.stderr, name)
            self.assertFalse(os.path.exists(plan_path), name)
        completed, _, plan_path = self._planned("unreferenced", json.dumps(unreferenced))
        self.assertEqual(completed.returncode, 0, tail(completed))
        self.assertTrue(os.path.exists(plan_path))


class OwnedItemsSchema(ItemsRead):
    def mitosis_items_resolves_beside_the_contracts(self):
        handoff = module("handoff")
        self.assertEqual(handoff.file_name("mitosis-items"), "mitosis-items.schema.json")
        self.assertEqual(handoff.errors("h12-interphase-items", self.items), ())
        with self.assertRaises(handoff.HandoffError):
            handoff.file_name("no-such-schema")

    def mitosis_items_accepts_interphase_and_reference_planner_steps(self):
        handoff = module("handoff")
        for value in (
            self.items,
            [
                {
                    "name": "reader",
                    "text": "t",
                    "files": ["reader.py"],
                    "type": "feature",
                    "complexity": "simple",
                    "cost": 2,
                    "file_notes": {"reader.py": "reads"},
                }
            ],
            [{"name": "reader", "text": "t", "files": ["reader.py"], "acceptance": "reader prints the count"}],
            [{"name": "reader", "task": "t", "files": ["reader.py"], "observable": "o", "source": None, "acceptance": []}],
        ):
            self.assertEqual(handoff.errors("mitosis-items", value), (), json.dumps(value))

    def mitosis_items_refuses_a_malformed_step(self):
        handoff = module("handoff")
        for value, expected in (
            ([{"name": "reader", "text": "t", "files": []}], ("#/0/files minItems: 0 items; at least 1 needed",)),
            ([{"name": "reader", "files": ["reader.py"]}], ('#/0 required: lacks required property "task"',)),
            (
                [{"name": "reader", "task": "t", "files": ["reader.py"], "colour": "red"}],
                ('#/0/colour additionalProperties: property "colour" is not allowed',),
            ),
            ([{"task": "t", "files": ["a.py"]}], ('#/0 required: lacks required property "name"',)),
        ):
            self.assertEqual(handoff.errors("mitosis-items", value), expected, json.dumps(value))

    def core_reads_text_as_task(self):
        core = module("core")
        steps = [{"name": "a", "text": "Build a", "files": ["a.py"], "source": None, "acceptance": []}]
        self.assertEqual(core.validate(steps)["errors"], [])
        self.assertEqual(core.plan(steps, history=())["items"][0]["task"], "Build a")
        both = [{"name": "a", "task": "x", "text": "y", "files": ["a.py"], "source": None, "acceptance": []}]
        self.assertEqual(core.plan(both, history=())["items"][0]["task"], "x")
        bare = [{"name": "a", "files": ["a.py"], "source": None, "acceptance": []}]
        self.assertEqual(core.validate(bare)["errors"], ["a: missing required field 'task'"])


class Loader(unittest.TestLoader):
    def getTestCaseNames(self, case):
        return sorted(
            name
            for name in vars(case)
            if not name.startswith("_") and callable(getattr(case, name)) and name != "setUp"
        )


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    suite.addTests(Loader().loadTestsFromTestCase(ItemsRead))
    suite.addTests(Loader().loadTestsFromTestCase(OwnedItemsSchema))
    return suite


if __name__ == "__main__":
    unittest.main()
