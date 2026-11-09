import json
import os
import shlex
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

DROPPED_ENVIRONMENT = ("CLAUDE_CODE_SESSION_ID", "PLUGIN_METRICS_PARENT", "SHIP")

RECON = r'''
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
prompt = sys.stdin.read()
reasked = sys.argv[1] != "--model"
recon = {key: os.environ.get(key) for key in ("MITOSIS_RECON", "MITOSIS_RECON_ASK", "MITOSIS_RECON_GRAPH")}
log = "reasks.jsonl" if reasked else "calls.jsonl"
with open(os.path.join(here, log), "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": sys.argv[1:], "prompt": prompt, "recon": recon}) + "\n")
replies = "reask-replies.json" if reasked else "replies.json"
with open(os.path.join(here, replies), encoding="utf-8") as handle:
    reply = json.load(handle)[recon["MITOSIS_RECON_ASK"]]
print(json.dumps({"type": "result", "session_id": sys.argv[-2], "result": "done", "structured_output": reply}))
'''

READER = {
    "name": "reader",
    "task": "Read it.",
    "files": ["reader.py"],
    "acceptance": "reader prints the count",
    "tests": [{"file": "tests/test_reader.py", "test": "R.reads"}],
    "file_notes": {"reader.py": "new"},
    "type": "feature",
    "complexity": "simple",
}


def writer(**extra):
    return {
        "name": "writer",
        "task": "Write it.",
        "files": ["writer.py"],
        "acceptance": "writer writes the file",
        "tests": [],
        **extra,
    }


class AsksCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mitosis-asks-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        with open(os.path.join(self.tmp, "recon.py"), "w", encoding="utf-8") as handle:
            handle.write(RECON)
        script = shlex.quote(sys.executable) + " " + shlex.quote(os.path.join(self.tmp, "recon.py"))
        self.command = script + " --model {model} {session_id} {schema}"
        self.reask = script + " {prompt} {session_id} {schema}"

    def _write(self, name, value):
        with open(os.path.join(self.tmp, name), "w", encoding="utf-8") as handle:
            json.dump(value, handle)

    def _lines(self, name):
        path = os.path.join(self.tmp, name)
        if not os.path.isfile(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def _plan(self, asks_given, replies, reask_replies=None):
        import asks

        self._write("replies.json", replies)
        self._write("reask-replies.json", reask_replies or {})
        environment = {key: value for key, value in os.environ.items() if key not in DROPPED_ENVIRONMENT}
        return asks.plan_asks(
            asks_given,
            self.command,
            self.tmp,
            model="m-top",
            reask=self.reask,
            env=environment,
        )


class ReadAsks(AsksCase):
    def one_ask_per_stripped_line(self):
        import asks

        path = os.path.join(self.tmp, "asks.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("add a reader\n\n  add a writer  \n\n")
        self.assertEqual(asks.read_asks(path), ("add a reader", "add a writer"))


class PlanAsks(AsksCase):
    def a_reply_item_becomes_a_step_with_its_observable(self):
        result = self._plan(("add a reader",), {"add a reader": {"items": [READER]}})
        self.assertEqual(
            result["items"],
            [
                {
                    "name": "reader",
                    "task": "Read it.",
                    "files": ["reader.py"],
                    "source": None,
                    "observable": "reader prints the count",
                    "acceptance": [{"file": "tests/test_reader.py", "test": "R.reads"}],
                    "file_notes": {"reader.py": "new"},
                    "type": "feature",
                    "complexity": "simple",
                }
            ],
        )
        self.assertEqual(result["errors"], [])
        calls = self._lines("calls.jsonl")
        self.assertEqual(len(calls), 1)
        argv = calls[0]["argv"]
        self.assertEqual(argv[argv.index("--model") + 1], "m-top")
        prompt = calls[0]["prompt"]
        self.assertTrue(
            prompt.startswith("You are doing RECON for a parallel build planner. You write NO code."), prompt
        )
        self.assertTrue(prompt.endswith("\n\nRequest:\nadd a reader"), prompt)
        self.assertEqual(
            calls[0]["recon"],
            {"MITOSIS_RECON": "1", "MITOSIS_RECON_ASK": "add a reader", "MITOSIS_RECON_GRAPH": ""},
        )

    def a_duplicate_name_is_suffixed_and_an_unknown_after_dropped(self):
        import asks

        result = self._plan(
            ("a", "b"),
            {
                "a": {"items": [writer()]},
                "b": {"items": [writer(after=["nowhere", "writer"])]},
            },
        )
        self.assertEqual([step["name"] for step in result["items"]], ["writer", "writer-2"])
        self.assertEqual(result["items"][1]["after"], ["writer"])
        self.assertEqual(result["dropped"], [{"step": "writer-2", "dropped_after": ["nowhere"]}])
        self.assertIn(
            "Step writer-2: recon named producers that do not exist in this work-set; the edges were dropped: nowhere",
            asks.notices(result),
        )

    def an_item_without_files_is_re_asked_once_then_refused(self):
        bad = {"items": [dict(READER, files=[])]}
        result = self._plan(("add a reader",), {"add a reader": bad}, {"add a reader": bad})
        self.assertEqual(result["items"], [])
        reasks = self._lines("reasks.jsonl")
        self.assertEqual(len(reasks), 1)
        self.assertIn("item reader has no files", reasks[0]["prompt"])
        self.assertTrue(
            any("add a reader" in error and "item reader has no files" in error for error in result["errors"]),
            result["errors"],
        )

    def an_empty_reply_is_reported_and_no_steps_is_an_error(self):
        result = self._plan(("a", "b"), {"a": {"items": []}, "b": {"items": [writer()]}})
        self.assertEqual(result["errors"], [])
        self.assertEqual(
            result["empty"],
            [{"ask": "a", "error": "recon returned no usable items - this ask produced no work and no PR"}],
        )
        alone = self._plan(("a",), {"a": {"items": []}})
        self.assertEqual(alone["errors"], ["recon produced no items"])


def load_tests(loader, standard, pattern):
    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            return sorted(
                name for name in vars(case) if not name.startswith("_") and callable(getattr(case, name))
            )

    suite = unittest.TestSuite()
    for case in (ReadAsks, PlanAsks):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite
