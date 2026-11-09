import hashlib
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_accept_execution as accept
from test_accept_execution import S, tail

ROOT = os.path.dirname(HERE)
CONTRACT_SHA256 = "53c4a2066e659794a94d2f66dd3039e2d3180eb5950a3ca2111c0781dfa2e0cc"
CONTRACT_BYTES = 3202
RECON_LEAD = "You are doing RECON for a parallel build planner"
FIND_FILES = "HOW TO FIND THE FILES"
NO_GRAPH = "No graphify graph is available"
UNWRITTEN = "the plan names tests nothing writes"

RECON = r'''
import json
import os
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
started = time.time()
prompt = sys.stdin.read()
ask = os.environ.get("MITOSIS_RECON_ASK", "")
with open(os.path.join(here, "recon-control.json"), encoding="utf-8") as handle:
    control = json.load(handle)


def append(name, entry):
    with open(os.path.join(here, name), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


append("times.jsonl", {"ask": ask, "start": started})
time.sleep(control.get("sleep", 0))
ended = time.time()
append("times.jsonl", {"ask": ask, "end": ended})
recon = {key: os.environ.get(key) for key in ("MITOSIS_RECON", "MITOSIS_RECON_ASK", "MITOSIS_RECON_GRAPH")}
append("calls.jsonl", {"argv": sys.argv[1:], "prompt": prompt, "ask": ask, "recon": recon, "start": started, "end": ended})
replies = control.get("replies") or {}
if ask in replies:
    reply = replies[ask]
elif control.get("echo"):
    reply = {"items": [{"name": ask, "task": "task-" + ask, "files": [ask + ".py"], "acceptance": ask + " works", "tests": []}]}
else:
    sys.stderr.write("no reply for ask %r\n" % ask)
    sys.exit(1)
sys.stdout.write(json.dumps({"type": "result", "session_id": sys.argv[-2], "result": "done", "structured_output": reply}) + "\n")
'''

REASK_RECON = r'''
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
ask = os.environ.get("MITOSIS_RECON_ASK", "")
with open(os.path.join(here, "reasks.jsonl"), "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": sys.argv[1:], "ask": ask}) + "\n")
with open(os.path.join(here, "recon-control.json"), encoding="utf-8") as handle:
    control = json.load(handle)
replies = control.get("reask_replies") or {}
if ask not in replies:
    sys.stderr.write("no re-ask reply for ask %r\n" % ask)
    sys.exit(1)
sys.stdout.write(json.dumps({"type": "result", "session_id": sys.argv[-2], "result": "done", "structured_output": replies[ask]}) + "\n")
'''

STRUCTURE = r'''
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
prompt = sys.stdin.read()
with open(os.path.join(here, "structure-prompt.txt"), "w", encoding="utf-8") as handle:
    handle.write(prompt)
with open(os.path.join(here, "structure-env.json"), "w", encoding="utf-8") as handle:
    json.dump({key: os.environ.get(key) for key in ("MITOSIS_RECON", "MITOSIS_RECON_ASK", "MITOSIS_RECON_GRAPH")}, handle)
with open(os.path.join(here, "structure-reply.json"), encoding="utf-8") as handle:
    reply = json.load(handle)
print(json.dumps({"type": "result", "session_id": sys.argv[1], "result": "done", "structured_output": {"reply": reply}}))
'''

BRIEF = r'''
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.stdin.read()
with open(os.path.join(here, "briefs.jsonl"), "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": sys.argv[1:]}) + "\n")
with open(os.path.join(here, "brief-reply.json"), encoding="utf-8") as handle:
    reply = json.load(handle)
print(json.dumps({"type": "result", "session_id": sys.argv[-2], "result": "done", "structured_output": reply}))
'''

FAIL = r'''
import sys

sys.exit(1)
'''


def lines_of(path):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def item(name, files, observable, tests=(), **extra):
    return {
        "name": name,
        "task": "Build " + name + ".",
        "files": list(files),
        "acceptance": observable,
        "tests": [dict(test) for test in tests],
        **extra,
    }


def reply(*items):
    return {"items": list(items)}


def named(steps, role=None):
    return [step for step in steps if isinstance(step, dict) and step.get("role") == role]


def by_name(steps, name):
    found = [step for step in steps if isinstance(step, dict) and step.get("name") == name]
    return found[0] if len(found) == 1 else None


def unusage(stderr):
    return "\n".join(
        line for line in stderr.splitlines() if not line.startswith("usage:") and not line[:1].isspace()
    )


def most_at_once(intervals):
    events = sorted([(end, 0) for _, end in intervals] + [(start, 1) for start, _ in intervals])
    live = 0
    most = 0
    for _, opening in events:
        live = live + 1 if opening else live - 1
        most = max(most, live)
    return most


def child_environment(scratch):
    return {**accept.kept_environment(), **accept.GIT_IDENTITY, "HOME": scratch.env["HOME"]}


class AsksCase(accept.Case):
    def make_scratch(self):
        scratch = accept.Scratch()
        accept.put(os.path.join(scratch.stubs, "recon.py"), RECON)
        accept.put(os.path.join(scratch.stubs, "reask_recon.py"), REASK_RECON)
        accept.put(os.path.join(scratch.stubs, "structure.py"), STRUCTURE)
        accept.put(os.path.join(scratch.stubs, "brief.py"), BRIEF)
        accept.put(os.path.join(scratch.stubs, "fail.py"), FAIL)
        return scratch

    def _control(self, replies=None, reask_replies=None, sleep=0, echo=False):
        accept.put_json(
            os.path.join(self.scratch.stubs, "recon-control.json"),
            {"replies": replies or {}, "reask_replies": reask_replies or {}, "sleep": sleep, "echo": echo},
        )

    def _recon_command(self):
        return self.scratch.stub("recon.py", "--model", "{model}", "{session_id}", "{schema}")

    def _reask_command(self):
        return self.scratch.stub("reask_recon.py", "{prompt}", "{session_id}", "{schema}")

    def _calls(self):
        return lines_of(os.path.join(self.scratch.stubs, "calls.jsonl"))

    def _reasks(self):
        return lines_of(os.path.join(self.scratch.stubs, "reasks.jsonl"))

    def _asks_file(self, text):
        return accept.put(self.scratch.path("asks.txt"), text)

    def _asks_run(self, text, replies, *flags, reask_replies=None, sleep=0, run_dir=None, models=("top=test-top",)):
        self._control(replies, reask_replies, sleep)
        tiers = [word for model in models for word in ("--tier-model", model)]
        return self.scratch.mitosis(
            "--asks",
            self._asks_file(text),
            "--decompose-command",
            self._recon_command(),
            "--reask-command",
            self._reask_command(),
            *tiers,
            "--plan-only",
            "--run-dir",
            run_dir or self.scratch.run_dir,
            *flags,
        )

    def _items(self, completed, run_dir=None):
        loaded = self.loaded(os.path.join(run_dir or self.scratch.run_dir, "items.json"), completed)
        self.assertIsInstance(loaded, list, tail(completed))
        return loaded

    def _brief_for(self, plan, name, completed):
        index = self.lane_index(plan, name, completed)
        found = [entry for entry in plan.get("briefs") or [] if entry.get("lane") == index]
        self.assertEqual(len(found), 1, "plan.json holds no single brief for Lane %d" % index + tail(completed))
        return found[0].get("text") or ""


class AsksInput(AsksCase):
    def one_planning_call_per_ask_runs_in_parallel(self):
        asks = ("add a reader", "add a writer", "add a parser")
        replies = {
            ask: reply(item("ask-%d" % number, ["ask-%d.py" % number], "ask %d works" % number))
            for number, ask in enumerate(asks, 1)
        }
        completed = self._asks_run(
            "add a reader\n\n  add a writer  \nadd a parser\n", replies, "--concurrency", "3", sleep=1
        )
        self.exits(completed, 0)
        calls = self._calls()
        self.assertEqual(sorted(call["ask"] for call in calls), sorted(asks), tail(completed))
        for call in calls:
            argv = call["argv"]
            self.assertIn("--model", argv, argv)
            self.assertEqual(argv[argv.index("--model") + 1], "test-top", argv)
            prompt = call["prompt"]
            self.assertIn(RECON_LEAD, prompt)
            self.assertIn(FIND_FILES, prompt)
            self.assertIn(NO_GRAPH, prompt)
            self.assertTrue(prompt.endswith("Request:\n" + call["ask"]), prompt[-300:])
        self.assertLess(max(call["start"] for call in calls), min(call["end"] for call in calls), calls)
        self.assertEqual(self._reasks(), [], tail(completed))
        steps = self._items(completed)
        self.assertEqual(
            sorted(step.get("name") for step in named(steps)), ["ask-1", "ask-2", "ask-3"], tail(completed)
        )

    def asks_with_items_is_refused(self):
        self._control({"add a reader": reply(item("reader", ["reader.py"], "reader prints the count"))})
        asks = self._asks_file("add a reader\n")
        items = self.scratch.items([S("reader", ["reader.py"])])
        conflicting = self.scratch.mitosis(
            "--asks",
            asks,
            "--items",
            items,
            "--decompose-command",
            self._recon_command(),
            "--reask-command",
            self._reask_command(),
            "--tier-model",
            "top=test-top",
            "--plan-only",
            "--run-dir",
            self.scratch.path("run-1"),
        )
        self.exits(conflicting, 2)
        refused = unusage(conflicting.stderr)
        self.assertIn("--asks", refused, tail(conflicting))
        self.assertIn("--items", refused, tail(conflicting))
        lacking = self.scratch.mitosis(
            "--asks",
            asks,
            "--reask-command",
            self._reask_command(),
            "--tier-model",
            "top=test-top",
            "--plan-only",
            "--run-dir",
            self.scratch.path("run-2"),
        )
        self.exits(lacking, 2)
        refused = unusage(lacking.stderr)
        self.assertIn("--asks", refused, tail(lacking))
        self.assertIn("--decompose-command", refused, tail(lacking))
        self.assertEqual(self._calls(), [])
        self.assertEqual(self._reasks(), [])

    def a_resumed_asks_run_makes_no_planning_call(self):
        replies = {"add a reader": reply(item("reader", ["reader.py"], "reader prints the count"))}
        run_dir = self.scratch.path("run")
        first = self._asks_run("add a reader\n", replies, run_dir=run_dir)
        self.exits(first, 0)
        second = self._asks_run("add a reader\n", replies, "--resume", run_dir=run_dir)
        self.exits(second, 0)
        self.assertEqual(len(self._calls()), 1, tail(second))
        plan = self.plan(second, run_dir)
        self.assertIn("reader", [step.get("name") for step in plan.get("items") or []], tail(second))


class AsksConcurrency(AsksCase):
    def no_more_than_the_cap_run_at_once(self):
        import asks

        self._control(echo=True, sleep=0.5)
        result = asks.plan_asks(
            ("a", "b", "c", "d"),
            self._recon_command(),
            self.scratch.repo,
            model="m-top",
            concurrency=2,
            reask=self._reask_command(),
            env=child_environment(self.scratch),
        )
        self.assertEqual(list(result["errors"]), [])
        self.assertEqual(sorted(step["name"] for step in result["items"]), ["a", "b", "c", "d"])
        times = lines_of(os.path.join(self.scratch.stubs, "times.jsonl"))
        starts = {entry["ask"]: entry["start"] for entry in times if "start" in entry}
        ends = {entry["ask"]: entry["end"] for entry in times if "end" in entry}
        self.assertEqual(sorted(starts), ["a", "b", "c", "d"], times)
        self.assertEqual(sorted(ends), ["a", "b", "c", "d"], times)
        self.assertEqual(most_at_once([(starts[ask], ends[ask]) for ask in starts]), 2, times)


class ReconContract(unittest.TestCase):
    def the_prompt_starts_with_the_recon_contract_byte_for_byte(self):
        import recon_text

        encoded = recon_text.prompt("add a reader").encode("utf-8")
        self.assertEqual(hashlib.sha256(encoded[:CONTRACT_BYTES]).hexdigest(), CONTRACT_SHA256)
        self.assertEqual(encoded[CONTRACT_BYTES : CONTRACT_BYTES + 2], b"\n\n")


class AsksReplies(AsksCase):
    def an_item_without_files_is_re_asked_then_refused(self):
        ask = "count the lines"
        empty = reply(item("reader", [], "reader prints the count"))
        completed = self._asks_run(ask + "\n", {ask: empty}, reask_replies={ask: empty})
        self.assertNotEqual(completed.returncode, 0, tail(completed))
        self.assertIn(ask, completed.stderr, tail(completed))
        self.assertTrue(
            any("reader" in line and "files" in line for line in completed.stderr.splitlines()), tail(completed)
        )
        self.assertEqual(len(self._reasks()), 1, tail(completed))

    def a_duplicate_name_gets_a_suffix_and_an_unknown_after_is_dropped(self):
        replies = {
            "add a writer": reply(item("writer", ["writer.py"], "writer writes the file")),
            "add another writer": reply(
                item("writer", ["other_writer.py"], "the other writer writes", after=["nowhere"])
            ),
        }
        completed = self._asks_run("add a writer\nadd another writer\n", replies)
        self.exits(completed, 0)
        plan = self.plan(completed)
        self.assertEqual(sorted(step.get("name") for step in plan.get("items") or []), ["writer", "writer-2"])
        second = by_name(self._items(completed), "writer-2")
        self.assertIsInstance(second, dict, tail(completed))
        self.assertNotIn("after", second, tail(completed))
        attention = accept.summary_of(completed).get("attention") or []
        self.assertTrue(
            any("writer-2" in line and "nowhere" in line for line in attention if isinstance(line, str)),
            tail(completed),
        )
        self.assertTrue(
            any("writer-2" in line and "nowhere" in line for line in completed.stderr.splitlines()), tail(completed)
        )


class AsksPaths(AsksCase):
    def an_absolute_or_climbing_path_is_re_asked_then_refused(self):
        import asks

        ask = "add a reader"
        base = {"name": "reader", "task": "Read it.", "acceptance": "reader prints the count"}
        cases = (
            ({**base, "files": ["/abs/reader.py"], "tests": []}, "/abs/reader.py"),
            (
                {**base, "files": ["reader.py"], "tests": [{"file": "../tests/test_reader.py", "test": "R.reads"}]},
                "../tests/test_reader.py",
            ),
        )
        for number, (bad, path) in enumerate(cases, 1):
            self._control({ask: reply(bad)}, {ask: reply(bad)})
            result = asks.plan_asks(
                (ask,),
                self._recon_command(),
                self.scratch.repo,
                model="m-top",
                reask=self._reask_command(),
                env=child_environment(self.scratch),
            )
            self.assertEqual(list(result["items"]), [], result)
            self.assertEqual(len(self._reasks()), number, result)
            self.assertTrue(
                any(ask in error and path in error for error in result["errors"]), result["errors"]
            )


class AsksObservable(AsksCase):
    def the_observable_is_kept_word_for_word(self):
        observable = "pipeline affected prints one JSON line"
        built = item("affected", ["a.py"], observable, tests=[{"file": "tests/test_a.py", "test": "A.prints_json"}])
        accept.put_json(os.path.join(self.scratch.stubs, "brief-reply.json"), {"name": "affected", "task": "briefed"})
        completed = self._asks_run(
            "report the affected tests\n",
            {"report the affected tests": reply(built)},
            "--brief-command",
            self.scratch.stub("brief.py", "{step}", "{session_id}", "{schema}"),
        )
        self.exits(completed, 0)
        steps = self._items(completed)
        builds = named(steps)
        authors = named(steps, "check-author")
        self.assertEqual(len(builds), 1, tail(completed))
        self.assertEqual([author.get("files") for author in authors], [["tests/test_a.py"]], tail(completed))
        self.assertEqual(builds[0].get("observable"), observable)
        self.assertEqual(builds[0].get("acceptance"), [{"file": "tests/test_a.py", "test": "A.prints_json"}])
        self.assertEqual(builds[0].get("task"), built["task"])
        self.assertEqual(lines_of(os.path.join(self.scratch.stubs, "briefs.jsonl")), [])

    def the_observable_reaches_the_building_workers_brief(self):
        built = item(
            "reader",
            ["reader.py", "tests/test_reader.py"],
            "reader prints the count",
            tests=[{"file": "tests/test_reader.py", "test": "R.reads"}],
        )
        completed = self._asks_run("add a reader\n", {"add a reader": reply(built)})
        self.exits(completed, 0)
        text = self._brief_for(self.plan(completed), "reader", completed)
        self.assertIn("reader prints the count", text)


class ItemsObservable(AsksCase):
    def a_string_acceptance_reaches_the_brief(self):
        items = accept.put_json(
            self.scratch.path("planner-items.json"),
            [{"name": "reader", "text": "Read it.", "files": ["reader.py"], "acceptance": "reader prints the count"}],
        )
        completed = self.scratch.mitosis(
            "--items", items, "--plan-only", "--feature-branch", "feature", "--run-dir", self.scratch.run_dir
        )
        self.exits(completed, 0)
        text = self._brief_for(self.plan(completed), "reader", completed)
        self.assertIn("reader prints the count", text)


class BriefObservable(unittest.TestCase):
    def core_plan_puts_the_observable_in_the_brief(self):
        import core

        step = {
            "name": "reader",
            "task": "Read it.",
            "files": ["reader.py"],
            "source": None,
            "observable": "reader prints the count",
            "acceptance": [],
        }
        bare = {key: value for key, value in step.items() if key != "observable"}
        first = core.plan([step], history=())
        second = core.plan([bare], history=())
        briefs = first.get("briefs") or []
        self.assertEqual(len(briefs), 1, json.dumps(briefs)[:2000])
        self.assertIn("reader prints the count", briefs[0].get("text") or "")
        self.assertFalse(
            any("reader prints the count" in (entry.get("text") or "") for entry in second.get("briefs") or [])
        )


class LockStage(AsksCase):
    def _reader_run(self, files):
        built = item(
            "reader", files, "reader prints the count", tests=[{"file": "tests/test_reader.py", "test": "R.reads"}]
        )
        return self._asks_run("add a reader\n", {"add a reader": reply(built)})

    def an_unwritten_named_test_gets_a_check_author(self):
        completed = self._reader_run(["reader.py"])
        self.exits(completed, 0)
        authors = self.plan(completed).get("check_authors") or []
        self.assertEqual(len(authors), 1, tail(completed))
        self.assertEqual(authors[0].get("slice"), ["tests/test_reader.py"], tail(completed))
        self.assertIn("reader prints the count", (authors[0].get("brief") or {}).get("text") or "")
        steps = self._items(completed)
        written = named(steps, "check-author")
        self.assertEqual([author.get("files") for author in written], [["tests/test_reader.py"]], tail(completed))
        reader = by_name(steps, "reader")
        self.assertIsInstance(reader, dict, tail(completed))
        self.assertIn(written[0].get("name"), reader.get("after") or [], tail(completed))

    def a_step_that_writes_its_own_test_needs_no_check_author(self):
        completed = self._reader_run(["reader.py", "tests/test_reader.py"])
        self.exits(completed, 0)
        self.assertFalse(self.plan(completed).get("check_authors"), tail(completed))
        self.assertEqual(named(self._items(completed), "check-author"), [], tail(completed))

    def a_named_test_the_base_holds_gets_no_check_author(self):
        accept.put(os.path.join(self.scratch.repo, "tests", "test_reader.py"), "import unittest\n")
        self.scratch.checked("add", "tests/test_reader.py")
        self.scratch.checked("commit", "-q", "-m", "reader test")
        completed = self._reader_run(["reader.py"])
        self.exits(completed, 0)
        self.assertFalse(self.plan(completed).get("check_authors"), tail(completed))
        steps = self._items(completed)
        self.assertEqual(named(steps, "check-author"), [], tail(completed))
        reader = by_name(steps, "reader")
        self.assertIsInstance(reader, dict, tail(completed))
        self.assertEqual(reader.get("acceptance"), [{"file": "tests/test_reader.py", "test": "R.reads"}])

    def two_msps_get_two_check_authors(self):
        import lock_stage

        a = {
            "name": "a",
            "task": "t",
            "files": ["a.py"],
            "source": None,
            "observable": "a works",
            "acceptance": [{"file": "tests/test_a.py", "test": "A.works"}],
        }
        b = {
            "name": "b",
            "task": "t",
            "files": ["b.py"],
            "source": None,
            "observable": "b works",
            "acceptance": [{"file": "tests/test_b.py", "test": "B.works"}],
        }
        result = list(lock_stage.check_authors([a, b]))
        authors = named(result, "check-author")
        self.assertEqual(sorted(author.get("files") for author in authors), [["tests/test_a.py"], ["tests/test_b.py"]])
        first = [author for author in authors if author.get("files") == ["tests/test_a.py"]][0]
        second = [author for author in authors if author.get("files") == ["tests/test_b.py"]][0]
        self.assertIn(first.get("name"), (by_name(result, "a") or {}).get("after") or [])
        self.assertIn(second.get("name"), (by_name(result, "b") or {}).get("after") or [])

    def a_spec_run_gets_the_lock_stage(self):
        scratch = self.scratch
        scratch.checked("checkout", "-q", "feature")
        accept.put(os.path.join(scratch.repo, "spec.md"), accept.SPEC_DOCUMENT)
        scratch.checked("add", "spec.md")
        scratch.checked("commit", "-q", "-m", "spec")
        accept.put_json(
            os.path.join(scratch.stubs, "structure-reply.json"),
            {
                "items": [
                    {
                        "name": "reader",
                        "files": ["reader.py"],
                        "spec_ref": ["1.1"],
                        "acceptance": [{"file": "tests/test_reader.py", "test": "R.reads"}],
                    }
                ],
                "assumptions": [],
                "constraints": [],
            },
        )
        accept.put_json(os.path.join(scratch.stubs, "brief-reply.json"), {"name": "reader", "task": "Read it."})
        completed = scratch.mitosis(
            "--spec",
            "spec.md",
            "--run-dir",
            scratch.run_dir,
            "--feature-branch",
            "feature",
            "--no-push",
            "--decompose-command",
            scratch.stub("structure.py", "{session_id}", "{schema}"),
            "--brief-command",
            scratch.stub("brief.py", "{step}", "{session_id}", "{schema}"),
            "--reask-command",
            scratch.stub("fail.py", "{prompt}", "{session_id}", "{schema}"),
            "--dispatch-command",
            scratch.stub("fail.py", "{task}", "{session_id}", "{schema}"),
            "--acceptance-command",
            scratch.stub("fail.py", "{file}", "{test}"),
        )
        steps = self._items(completed)
        written = named(steps, "check-author")
        self.assertEqual([author.get("files") for author in written], [["tests/test_reader.py"]], tail(completed))
        reader = by_name(steps, "reader")
        self.assertIsInstance(reader, dict, tail(completed))
        self.assertIn(written[0].get("name"), reader.get("after") or [], tail(completed))
        self.assertNotIn(UNWRITTEN, completed.stderr)


class UnwrittenTests(AsksCase):
    def an_items_file_naming_an_unwritten_test_is_refused(self):
        completed = self.scratch.plan_only(
            [S("reader", ["reader.py"], acceptance=[{"file": "tests/test_reader.py", "test": "R.reads"}])]
        )
        self.exits(completed, 3)
        self.assertIn("tests/test_reader.py", completed.stderr)
        self.assertTrue(re.search(r"(?<![\w/-])reader(?![\w.-])", completed.stderr), tail(completed))
        self.assertFalse(os.path.exists(os.path.join(self.scratch.run_dir, "plan.json")), tail(completed))


class SpecPrompt(AsksCase):
    def the_structure_prompt_carries_the_recon_text(self):
        scratch = self.scratch
        accept.put(os.path.join(scratch.repo, "spec.md"), accept.SPEC_DOCUMENT)
        accept.put_json(
            os.path.join(scratch.stubs, "structure-reply.json"),
            {
                "items": [{"name": "a", "files": ["a.py"], "spec_ref": ["1.1"], "acceptance": []}],
                "assumptions": [],
                "constraints": [],
            },
        )
        completed = scratch.mitosis(
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
        )
        self.exits(completed, 0)
        recorded = os.path.join(scratch.stubs, "structure-prompt.txt")
        self.assertTrue(os.path.isfile(recorded), tail(completed))
        prompt = accept.read_text(recorded)
        header = "Decompose the document into Steps"
        self.assertIn(header, prompt)
        start = prompt.index(header)
        for text in (
            "Find it by looking at how an",
            "an under-predicted list is the one error that",
            FIND_FILES,
            NO_GRAPH,
        ):
            self.assertIn(text, prompt)
            self.assertGreater(prompt.index(text, start), start, text)


class ItemsSchema(AsksCase):
    def a_reference_planner_shaped_items_file_is_planned(self):
        items = accept.put_json(
            self.scratch.path("planner-items.json"),
            [
                {
                    "name": "reader",
                    "text": "Read the count from the file.",
                    "files": ["reader.py"],
                    "type": "feature",
                    "complexity": "simple",
                    "cost": 2,
                    "file_notes": {"reader.py": "the new reader"},
                    "acceptance": "reader prints the count",
                },
                {
                    "name": "writer",
                    "text": "Write the count to the file.",
                    "files": ["writer.py"],
                    "type": "feature",
                    "complexity": "simple",
                    "cost": 2,
                    "file_notes": {"writer.py": "the new writer"},
                },
            ],
        )
        completed = self.scratch.mitosis(
            "--items", items, "--plan-only", "--feature-branch", "feature", "--run-dir", self.scratch.run_dir
        )
        self.exits(completed, 0)
        planned = self.plan(completed).get("items") or []
        self.assertEqual(sorted(step.get("name") for step in planned), ["reader", "writer"], tail(completed))
        self.assertEqual((by_name(planned, "reader") or {}).get("task"), "Read the count from the file.")

    def a_step_without_files_is_refused_by_name(self):
        completed = self.scratch.plan_only([S("reader", [])])
        self.assertNotEqual(completed.returncode, 0, tail(completed))
        self.assertTrue(
            any("reader" in line and "files" in line for line in completed.stderr.splitlines()), tail(completed)
        )

    def an_unknown_field_and_a_missing_name_are_refused(self):
        import handoff

        try:
            coloured = handoff.errors("mitosis-items", [{"name": "reader", "task": "t", "files": ["reader.py"], "colour": "red"}])
            nameless = handoff.errors("mitosis-items", [{"task": "t", "files": ["a.py"]}])
        except handoff.HandoffError as error:
            self.fail("handoff cannot check against mitosis-items: %s" % error)
        self.assertEqual(coloured, ('#/0/colour additionalProperties: property "colour" is not allowed',))
        self.assertEqual(nameless, ('#/0 required: lacks required property "name"',))


class ItemsPaths(AsksCase):
    def an_absolute_or_climbing_path_is_refused_naming_the_step(self):
        cases = (
            (S("reader", ["/abs/reader.py"], acceptance=[{"file": "tests/test_reader.py", "test": "R.reads"}]), "/abs/reader.py"),
            (
                S("reader", ["reader.py"], acceptance=[{"file": "../tests/test_reader.py", "test": "R.reads"}]),
                "../tests/test_reader.py",
            ),
        )
        for number, (step, path) in enumerate(cases, 1):
            run_dir = self.scratch.path("run-%d" % number)
            completed = self.scratch.plan_only([step], run_dir=run_dir)
            self.exits(completed, 3)
            self.assertTrue(
                any("Step reader:" in line and path in line for line in completed.stderr.splitlines()),
                tail(completed),
            )
            self.assertFalse(os.path.exists(os.path.join(run_dir, "plan.json")), tail(completed))


class RetryReplaced(accept.Case):
    def a_retry_with_one_step_replaced_builds(self):
        scratch = self.scratch
        a = S("a", ["a.py"], msp="m")
        first_dir = scratch.path("run-1")
        first = scratch.full_run([a, S("b", ["b.py"], msp="m")], control={"b": {"status": "failed"}}, run_dir=first_dir)
        trees = [tree for tree in self.state(first, first_dir).get("worktrees") or [] if isinstance(tree, dict)]
        self.assertEqual(len(trees), 1, tail(first))
        earlier = trees[0].get("path")
        self.assertTrue(isinstance(earlier, str) and os.path.isdir(earlier), tail(first))
        left = accept.put(os.path.join(earlier, "left.txt"), "kept\n")
        held = {
            path: accept.read_text(os.path.join(earlier, path))
            for path in ("a.py", "left.txt")
            if os.path.isfile(os.path.join(earlier, path))
        }
        second_dir = scratch.path("run-2")
        second = scratch.full_run(
            [a, S("b2", ["b2.py"], msp="m")],
            writes={"a": {"a.py": "work of a, again\n"}},
            run_dir=second_dir,
        )
        self.assertNotEqual(second.returncode, 5, tail(second))
        msp = ((self.state(second, second_dir).get("msps") or {}).get("0")) or {}
        self.assertIn(msp.get("state"), ("committed", "held"), json.dumps(msp)[:2000] + tail(second))
        self.assertEqual(accept.read_text(left), "kept\n")
        for path, content in held.items():
            self.assertEqual(accept.read_text(os.path.join(earlier, path)), content, path)


class Released(unittest.TestCase):
    def the_version_is_raised(self):
        with open(os.path.join(ROOT, "core.py"), encoding="utf-8") as handle:
            declared = re.search(r'^__version__ = "([0-9.]+)"$', handle.read(), re.M).group(1)
        self.assertGreaterEqual(tuple(int(part) for part in declared.split(".")), (0, 15, 0))
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle).get("version"), declared)

    def the_changelog_describes_the_release(self):
        with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        starts = [index for index, line in enumerate(lines) if line.startswith("## 0.15.0")]
        self.assertTrue(starts, "CHANGELOG.md has no ## 0.15.0 heading")
        rest = lines[starts[0] + 1 :]
        ends = [index for index, line in enumerate(rest) if line.startswith("## ")]
        section = "\n".join(rest[: ends[0]] if ends else rest)
        self.assertIn("ask", section)


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
        AsksInput,
        AsksConcurrency,
        ReconContract,
        AsksReplies,
        AsksPaths,
        AsksObservable,
        ItemsObservable,
        BriefObservable,
        LockStage,
        UnwrittenTests,
        SpecPrompt,
        ItemsSchema,
        ItemsPaths,
        RetryReplaced,
        Released,
    ):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite
