import hashlib
import json
import os
import shlex
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import briefs
import core
import decompose


def frozen(path="docs/spec.md", text="# One\n\nthe body of the document\n"):
    return {
        "path": path,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text": text,
    }


def write(root, relative, content):
    path = os.path.join(root, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


BRIEFER = """
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
step = sys.argv[1]
prompt = sys.stdin.read()
with open(os.path.join(here, "prompt-%s.txt" % step), "w", encoding="utf-8") as handle:
    handle.write(prompt)
with open(os.path.join(here, "argv-%s.json" % step), "w", encoding="utf-8") as handle:
    json.dump(sys.argv[1:], handle)
print("chatter that must stay out of the record")
print("more chatter", file=sys.stderr)
reply = os.path.join(here, "reply-%s.txt" % step)
if not os.path.isfile(reply):
    sys.exit(2)
with open(reply, encoding="utf-8") as handle:
    sys.stdout.write(handle.read())
"""


def briefer(root, replies):
    path = write(root, "worker/worker.py", BRIEFER)
    for name, reply in replies.items():
        write(
            root,
            "worker/reply-%s.txt" % name,
            reply if isinstance(reply, str) else json.dumps(reply),
        )
    return "%s %s {step} {model} {document}" % (shlex.quote(sys.executable), shlex.quote(path))


def received(root, name):
    with open(os.path.join(root, "worker", name), encoding="utf-8") as handle:
        return handle.read()


def was_dispatched(root, name):
    return os.path.isfile(os.path.join(root, "worker", "prompt-%s.txt" % name))


def bare(name, **extra):
    return {
        "name": name,
        "files": [name + ".py"],
        "source": None,
        "acceptance": [],
        **extra,
    }


def briefed(name, **extra):
    return bare(name, task="do " + name, **extra)


def good(name):
    return {"name": name, "task": "build " + name}


STRUCTURE = [
    briefed(
        "alpha",
        after=[],
        contract_group="api",
        type="feature",
        complexity="complex",
        acceptance=[{"file": "tests/test_alpha.py", "test": "alpha_works"}],
        file_notes={"alpha.py": "the alpha note"},
        spec_ref=["1"],
        msp="ship-one",
        assumptions=["the alpha reading"],
        source={"path": "docs/spec.md", "sha256": "f" * 64},
    ),
    bare(
        "beta",
        after=["alpha"],
        type="fix",
        complexity="simple",
        file_notes={"beta.py": "the beta note"},
    ),
    bare("gamma", after=["beta"], contract_group="api", assumptions=["the gamma reading"]),
]


class Contract(unittest.TestCase):
    def the_contract_states_the_return_shape(self):
        self.assertIn('"name"', briefs.BRIEF_CONTRACT)
        self.assertIn('"task"', briefs.BRIEF_CONTRACT)
        self.assertIn("exactly", briefs.BRIEF_CONTRACT)
        self.assertIn("one line of JSON", briefs.BRIEF_CONTRACT)
        lowered = briefs.BRIEF_CONTRACT.lower()
        self.assertIn("last line", lowered)
        self.assertIn("json object", lowered)
        self.assertIn("fence", lowered)
        self.assertIn("narration", lowered)
        self.assertEqual(briefs.RETURN_KEYS, ("name", "task"))


class Pending(unittest.TestCase):
    def only_steps_without_a_usable_task_are_pending(self):
        items = [
            briefed("alpha"),
            bare("beta"),
            bare("gamma", task=None),
            bare("delta", task=""),
            bare("epsilon", task="   \n"),
            bare("zeta", task=["not", "a", "string"]),
            briefed("eta"),
        ]
        self.assertEqual(
            [item["name"] for item in briefs.pending(items)],
            ["beta", "gamma", "delta", "epsilon", "zeta"],
        )
        self.assertEqual(briefs.pending([]), [])
        self.assertEqual(briefs.pending([briefed("alpha")]), [])


class Tier(unittest.TestCase):
    def a_simple_step_briefs_cheap_and_anything_else_briefs_top(self):
        self.assertEqual(briefs.tier_for(bare("alpha", complexity="simple")), "cheap")
        self.assertEqual(briefs.tier_for(bare("alpha", complexity="complex")), "top")
        self.assertEqual(briefs.tier_for(bare("alpha")), "top")
        self.assertEqual(briefs.tier_for(bare("alpha", complexity="Simple")), "top")
        self.assertEqual(briefs.tier_for(bare("alpha", complexity=None)), "top")
        for tier in (briefs.tier_for(bare("a", complexity="simple")), briefs.tier_for(bare("a"))):
            self.assertIn(tier, core.TIERS)

    def a_step_carrying_an_assumption_never_briefs_cheap(self):
        parsed = {
            "items": [
                bare("alpha", complexity="simple"),
                bare("beta", complexity="simple", assumptions=["the beta reading"]),
                bare("gamma", complexity="simple"),
            ],
            "assumptions": [{"step": "alpha", "text": "the alpha reading"}],
            "constraints": [],
            "errors": [],
        }
        source = {"path": "docs/spec.md", "sha256": "0" * 64}
        collected = decompose.collect(parsed, source, required=core.STRUCTURE_ITEM_FIELDS)
        self.assertEqual(collected["errors"], [])
        self.assertEqual(sorted(collected["raised"]), ["alpha", "beta"])
        self.assertEqual(
            [briefs.tier_for(item) for item in collected["items"]], ["top", "top", "cheap"]
        )


class Prompt(unittest.TestCase):
    def the_prompt_carries_every_sibling_step(self):
        pack = {"paths": ["beta.py", "core.py", "shape.py"], "overflow": 1}
        decisions = {
            "path": "docs/spec.decisions.md",
            "text": "- the registry is owned by core\n",
            "count": 1,
        }
        prompt = briefs.render_brief_prompt(
            frozen(),
            STRUCTURE,
            STRUCTURE[1],
            pack=pack,
            charter="docs/charter.md",
            decisions=decisions,
        )
        for name in ("alpha", "beta", "gamma"):
            self.assertIn('"name":"%s"' % name, prompt)
            self.assertIn('"files":["%s.py"]' % name, prompt)
        self.assertIn('"after":["alpha"]', prompt)
        self.assertIn('"after":["beta"]', prompt)
        self.assertIn('"contract_group":"api"', prompt)
        self.assertIn('"type":"feature"', prompt)
        self.assertIn('"complexity":"complex"', prompt)
        self.assertIn('"test":"alpha_works"', prompt)
        self.assertNotIn("do alpha", prompt)
        self.assertNotIn("the alpha note", prompt)
        self.assertNotIn("the alpha reading", prompt)
        self.assertNotIn("the gamma reading", prompt)
        self.assertNotIn("ship-one", prompt)
        self.assertNotIn("spec_ref", prompt)
        self.assertNotIn("f" * 64, prompt)
        self.assertIn("the beta note", prompt)
        subject = [
            line for line in prompt.splitlines() if "beta" in line and "brief" in line.lower()
        ]
        self.assertEqual(len(subject), 1)
        read_set = [line for line in prompt.splitlines() if "core.py" in line]
        self.assertEqual(len(read_set), 1)
        self.assertIn("shape.py", read_set[0])
        self.assertNotIn("beta.py", read_set[0])
        self.assertIn("never edit", read_set[0])
        self.assertIn("1 more", read_set[0])
        self.assertIn("docs/charter.md", prompt)
        self.assertIn(decompose.DECISIONS_HEADING, prompt)
        self.assertIn("- the registry is owned by core", prompt)
        self.assertIn("Document: docs/spec.md", prompt)
        self.assertIn(frozen()["sha256"], prompt)
        opened = prompt.index(decompose.DOCUMENT_OPEN)
        closed = prompt.index(decompose.DOCUMENT_CLOSE)
        self.assertLess(opened, prompt.index("the body of the document"))
        self.assertLess(prompt.index("the body of the document"), closed)
        self.assertLess(prompt.index("- the registry is owned by core"), opened)
        self.assertLess(prompt.index('"name":"alpha"'), opened)
        self.assertTrue(prompt.endswith(briefs.BRIEF_CONTRACT + "\n"))

    def the_prompt_omits_what_it_was_not_given(self):
        prompt = briefs.render_brief_prompt(frozen(), STRUCTURE, STRUCTURE[2])
        self.assertNotIn(decompose.DECISIONS_HEADING, prompt)
        self.assertNotIn("Charter:", prompt)
        self.assertNotIn("never edit", prompt)
        self.assertIn(decompose.DOCUMENT_OPEN, prompt)
        self.assertTrue(prompt.endswith(briefs.BRIEF_CONTRACT + "\n"))
        blank = briefs.render_brief_prompt(
            frozen(),
            STRUCTURE,
            STRUCTURE[2],
            pack={"paths": ["gamma.py"], "overflow": 0},
            decisions={"path": "x", "text": "  \n", "count": 0},
        )
        self.assertNotIn(decompose.DECISIONS_HEADING, blank)
        self.assertNotIn("never edit", blank)
        without = briefs.render_brief_prompt(None, STRUCTURE, STRUCTURE[2])
        self.assertNotIn(decompose.DOCUMENT_OPEN, without)
        self.assertNotIn("Document:", without)
        self.assertIn('"name":"gamma"', without)

    def the_read_set_excludes_the_write_set_however_it_is_spelled(self):
        step = bare("beta", files=["./beta.py", "src/../beta_extra.py"])
        pack = {"paths": ["beta.py", "beta_extra.py", "core.py", "core.py"], "overflow": 0}
        prompt = briefs.render_brief_prompt(frozen(), [step], step, pack=pack)
        read_set = [line for line in prompt.splitlines() if "never edit" in line]
        self.assertEqual(len(read_set), 1)
        self.assertTrue(read_set[0].endswith(": core.py"))

    def the_subject_step_is_rendered_without_its_task_or_source(self):
        prompt = briefs.render_brief_prompt(frozen(), STRUCTURE, STRUCTURE[0])
        self.assertNotIn("do alpha", prompt)
        self.assertNotIn("f" * 64, prompt)
        self.assertIn("the alpha note", prompt)
        self.assertIn("the alpha reading", prompt)
        self.assertIn("ship-one", prompt)
        self.assertNotIn("the gamma reading", prompt)


class Return(unittest.TestCase):
    def a_good_return_parses(self):
        parsed = briefs.parse_brief('{"name":"beta","task":"build beta"}', "beta")
        self.assertEqual(parsed, {"name": "beta", "task": "build beta", "errors": []})

    def a_return_naming_another_step_is_an_error(self):
        parsed = briefs.parse_brief('{"name":"alpha","task":"build beta"}', "beta")
        self.assertEqual(parsed["name"], "alpha")
        self.assertEqual(parsed["task"], "build beta")
        self.assertEqual(len(parsed["errors"]), 1)
        self.assertIn("alpha", parsed["errors"][0])
        self.assertIn("beta", parsed["errors"][0])
        missing = briefs.parse_brief('{"task":"build beta"}', "beta")
        self.assertEqual(len(missing["errors"]), 1)
        self.assertIn("beta", missing["errors"][0])

    def a_missing_or_empty_task_is_an_error(self):
        for line in (
            '{"name":"beta"}',
            '{"name":"beta","task":""}',
            '{"name":"beta","task":"  \\n"}',
            '{"name":"beta","task":7}',
            '{"name":"beta","task":null}',
        ):
            parsed = briefs.parse_brief(line, "beta")
            self.assertEqual(parsed["name"], "beta")
            self.assertEqual(len(parsed["errors"]), 1, line)
            self.assertIn("task", parsed["errors"][0])
            self.assertIn("beta", parsed["errors"][0])

    def a_line_that_is_not_a_json_object_is_an_error(self):
        for line in ("not json", "[1, 2]", '"a string"', "42", ""):
            parsed = briefs.parse_brief(line, "beta")
            self.assertEqual(parsed["name"], None)
            self.assertEqual(parsed["task"], None)
            self.assertEqual(len(parsed["errors"]), 1, line)
            self.assertIn("JSON object", parsed["errors"][0])
            self.assertIn("beta", parsed["errors"][0])
        absent = briefs.parse_brief(None, "beta")
        self.assertEqual(len(absent["errors"]), 1)
        self.assertIn("beta", absent["errors"][0])

    def a_wrong_name_and_an_empty_task_are_two_errors(self):
        parsed = briefs.parse_brief('{"name":"alpha","task":""}', "beta")
        self.assertEqual(len(parsed["errors"]), 2)


class Write(unittest.TestCase):
    def only_unbriefed_steps_are_dispatched(self):
        items = [briefed("alpha"), bare("beta", complexity="simple"), bare("gamma")]
        before = json.loads(json.dumps(items))
        with tempfile.TemporaryDirectory() as root:
            document = write(root, "docs/spec.md", "# One\n\nbody\n")
            template = briefer(root, {"beta": good("beta"), "gamma": good("gamma")})
            result = briefs.write(
                items,
                template,
                root,
                timeout=20,
                models={"top": "big", "cheap": "small"},
                concurrency=2,
                document=document,
            )
            self.assertFalse(was_dispatched(root, "alpha"))
            self.assertTrue(was_dispatched(root, "beta"))
            self.assertTrue(was_dispatched(root, "gamma"))
            self.assertEqual(json.loads(received(root, "argv-beta.json")), ["beta", "small", document])
            self.assertEqual(json.loads(received(root, "argv-gamma.json")), ["gamma", "big", document])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["reused"], ["alpha"])
        self.assertEqual(result["written"], ["beta", "gamma"])
        self.assertEqual(
            [item["task"] for item in result["items"]], ["do alpha", "build beta", "build gamma"]
        )
        self.assertEqual(sorted(result), ["errors", "items", "retried", "reused", "written"])
        self.assertEqual(items, before)
        for original, returned in zip(items, result["items"]):
            self.assertIsNot(original, returned)
        self.assertEqual(result["items"][0], items[0])
        self.assertEqual(result["items"][1], {**items[1], "task": "build beta"})

    def a_fenced_return_is_read(self):
        fenced = (
            "Here is the brief.\n\n| col | col |\n|---|---|\n| a | b |\n\n"
            "```json\n"
            + json.dumps(good("beta"))
            + "\n```\n\nDone.\n"
        )
        with tempfile.TemporaryDirectory() as root:
            document = write(root, "docs/spec.md", "# One\n\nbody\n")
            template = briefer(root, {"beta": fenced, "gamma": json.dumps(good("gamma"))})
            result = briefs.write(
                [bare("beta"), bare("gamma")],
                template,
                root,
                timeout=20,
                models={"top": "big"},
                document=document,
            )
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["written"], ["beta", "gamma"])
        self.assertEqual([item["task"] for item in result["items"]], ["build beta", "build gamma"])

    def every_pending_step_is_attempted_before_the_refusal(self):
        items = [bare("alpha"), bare("beta"), bare("gamma"), briefed("delta")]
        with tempfile.TemporaryDirectory() as root:
            document = write(root, "docs/spec.md", "# One\n\nbody\n")
            template = briefer(root, {"alpha": good("alpha"), "beta": "this is not a return"})
            result = briefs.write(
                items, template, root, timeout=20, models={"top": "big"}, document=document
            )
            for name in ("alpha", "beta", "gamma"):
                self.assertTrue(was_dispatched(root, name), name)
            self.assertFalse(was_dispatched(root, "delta"))
        self.assertEqual(result["written"], ["alpha"])
        self.assertEqual(result["reused"], ["delta"])
        self.assertEqual(result["items"][0]["task"], "build alpha")
        self.assertNotIn("task", result["items"][1])
        self.assertNotIn("task", result["items"][2])
        self.assertEqual(result["items"][3]["task"], "do delta")
        self.assertTrue(any("beta" in error and "JSON object" in error for error in result["errors"]))
        self.assertTrue(any("gamma" in error and "exited 2" in error for error in result["errors"]))
        self.assertTrue(all("beta" in error or "gamma" in error for error in result["errors"]))
        self.assertFalse(any("alpha" in error for error in result["errors"]))

    def a_return_for_the_wrong_step_leaves_that_step_unbriefed(self):
        with tempfile.TemporaryDirectory() as root:
            document = write(root, "docs/spec.md", "# One\n\nbody\n")
            template = briefer(root, {"beta": good("alpha")})
            result = briefs.write(
                [bare("beta")], template, root, timeout=20, models={"top": "big"}, document=document
            )
        self.assertEqual(result["written"], [])
        self.assertNotIn("task", result["items"][0])
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("alpha", result["errors"][0])
        self.assertIn("beta", result["errors"][0])

    def each_dispatch_carries_the_whole_structure_and_its_own_read_set(self):
        items = [
            STRUCTURE[0],
            STRUCTURE[1],
            bare("gamma", after=["beta"], contract_group="api"),
        ]
        packs = {
            "beta": {"paths": ["beta.py", "core.py"], "overflow": 0},
            "gamma": {"paths": ["gamma.py", "shape.py"], "overflow": 0},
        }
        decisions = {"path": "docs/settled.md", "text": "- settled here\n", "count": 1}
        with tempfile.TemporaryDirectory() as root:
            document = write(root, "docs/spec.md", "# One\n\nthe body of the document\n")
            template = briefer(root, {"beta": good("beta"), "gamma": good("gamma")})
            result = briefs.write(
                items,
                template,
                root,
                timeout=20,
                models={"top": "big", "cheap": "small"},
                concurrency=2,
                document=document,
                packs=packs,
                charter="docs/charter.md",
                decisions=decisions,
            )
            beta = received(root, "prompt-beta.txt")
            gamma = received(root, "prompt-gamma.txt")
        self.assertEqual(result["errors"], [])
        for prompt in (beta, gamma):
            for name in ("alpha", "beta", "gamma"):
                self.assertIn('"name":"%s"' % name, prompt)
            self.assertNotIn("do alpha", prompt)
            self.assertIn("- settled here", prompt)
            self.assertIn("docs/charter.md", prompt)
            self.assertIn("the body of the document", prompt)
            self.assertTrue(prompt.endswith(briefs.BRIEF_CONTRACT + "\n"))
        self.assertIn("core.py", beta)
        self.assertNotIn("shape.py", beta)
        self.assertIn("shape.py", gamma)
        self.assertNotIn("core.py", gamma)
        self.assertEqual(
            len([line for line in beta.splitlines() if "beta" in line and "brief" in line.lower()]),
            1,
        )

    def each_brief_dispatch_writes_its_own_log(self):
        with tempfile.TemporaryDirectory() as root:
            document = write(root, "docs/spec.md", "# One\n\nbody\n")
            log_dir = os.path.join(root, "run", "briefs")
            template = briefer(root, {"beta": good("beta"), "gamma": good("gamma")})
            result = briefs.write(
                [briefed("alpha"), bare("beta"), bare("gamma")],
                template,
                root,
                timeout=20,
                models={"top": "big"},
                log_dir=log_dir,
                document=document,
            )
            self.assertEqual(result["errors"], [])
            self.assertEqual(sorted(os.listdir(log_dir)), ["beta.log", "gamma.log"])
            with open(os.path.join(log_dir, "beta.log"), encoding="utf-8") as handle:
                self.assertIn("build beta", handle.read())

    def a_template_using_a_model_no_tier_maps_raises_before_any_dispatch(self):
        with tempfile.TemporaryDirectory() as root:
            document = write(root, "docs/spec.md", "# One\n\nbody\n")
            template = briefer(root, {"beta": good("beta")})
            with self.assertRaises(ValueError):
                briefs.write([bare("beta")], template, root, timeout=20, document=document)
            with self.assertRaises(ValueError):
                briefs.write(
                    [bare("beta")],
                    template,
                    root,
                    timeout=20,
                    models={"cheap": "small"},
                    document=document,
                )
            self.assertFalse(was_dispatched(root, "beta"))

    def a_frozen_document_is_accepted_in_place_of_a_path(self):
        with tempfile.TemporaryDirectory() as root:
            template = briefer(root, {"beta": good("beta")})
            result = briefs.write(
                [bare("beta")],
                template,
                root,
                timeout=20,
                models={"top": "big"},
                document=frozen(),
            )
            self.assertEqual(json.loads(received(root, "argv-beta.json")), ["beta", "big", "docs/spec.md"])
            self.assertIn("the body of the document", received(root, "prompt-beta.txt"))
        self.assertEqual(result["errors"], [])

    def nothing_pending_dispatches_nothing(self):
        items = [briefed("alpha"), briefed("beta")]
        with tempfile.TemporaryDirectory() as root:
            template = briefer(root, {})
            result = briefs.write(items, template, root, timeout=20, models={"top": "big"})
            self.assertFalse(os.path.isdir(os.path.join(root, "worker", "prompt-alpha.txt")))
        self.assertEqual(
            result,
            {"items": items, "written": [], "reused": ["alpha", "beta"], "retried": [], "errors": []},
        )


FLAKY_BRIEFER = """
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
step = sys.argv[1]
sys.stdin.read()
tally = os.path.join(here, "tally-%s.txt" % step)
seen = 0
if os.path.isfile(tally):
    with open(tally, encoding="utf-8") as handle:
        seen = int(handle.read())
with open(tally, "w", encoding="utf-8") as handle:
    handle.write(str(seen + 1))
bad = int(sys.argv[2])
if seen < bad:
    print("- `a_thing_holds` - a bullet, which is not a JSON object")
else:
    print(json.dumps({"name": step, "task": "do the thing for " + step}))
"""


def flaky_briefer(root, bad_rounds):
    path = write(root, "worker/flaky.py", FLAKY_BRIEFER)
    return "%s %s {step} %d" % (shlex.quote(sys.executable), shlex.quote(path), bad_rounds)


def attempts(root, name):
    path = os.path.join(root, "worker", "tally-%s.txt" % name)
    if not os.path.isfile(path):
        return 0
    with open(path, encoding="utf-8") as handle:
        return int(handle.read())


class WriteRetry(unittest.TestCase):
    def a_brief_whose_last_line_is_not_a_json_object_is_asked_again(self):
        items = [bare("beta"), bare("gamma")]
        with tempfile.TemporaryDirectory() as root:
            template = flaky_briefer(root, 1)
            result = briefs.write(items, template, root, timeout=20, concurrency=2)
            self.assertEqual(attempts(root, "beta"), 2)
            self.assertEqual(attempts(root, "gamma"), 2)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["written"], ["beta", "gamma"])
        self.assertEqual(result["items"][0]["task"], "do the thing for beta")
        self.assertEqual(result["retried"], ["beta", "gamma"])

    def a_brief_that_returns_cleanly_is_never_asked_twice(self):
        items = [bare("beta")]
        with tempfile.TemporaryDirectory() as root:
            template = flaky_briefer(root, 0)
            result = briefs.write(items, template, root, timeout=20)
            self.assertEqual(attempts(root, "beta"), 1)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["retried"], [])

    def a_brief_that_never_returns_an_object_stops_at_the_bound_and_refuses(self):
        items = [bare("beta")]
        with tempfile.TemporaryDirectory() as root:
            template = flaky_briefer(root, 99)
            result = briefs.write(items, template, root, timeout=20)
            self.assertEqual(attempts(root, "beta"), decompose.DISPATCH_ATTEMPTS)
        self.assertTrue(result["errors"])
        self.assertEqual(result["written"], [])
        self.assertEqual(briefs.pending(result["items"]), [items[0]])


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
    for case in (Contract, Pending, Tier, Prompt, Return, Write, WriteRetry):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
