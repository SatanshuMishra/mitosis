import hashlib
import json
import os
import shlex
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core
import decompose


def frozen(path="docs/spec.md", text="# One\n\nbody\n"):
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


WORKER = """
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
prompt = sys.stdin.read()
with open(os.path.join(here, "prompt.txt"), "w", encoding="utf-8") as handle:
    handle.write(prompt)
with open(os.path.join(here, "argv.json"), "w", encoding="utf-8") as handle:
    json.dump(sys.argv[1:], handle)
print("chatter that must stay out of the record")
print("more chatter", file=sys.stderr)
with open(os.path.join(here, "reply.txt"), encoding="utf-8") as handle:
    print(handle.read().strip())
sys.exit(int(os.environ.get("WORKER_EXIT", "0")))
"""

SLEEPER = """
import time
time.sleep(60)
"""


def worker(root, reply, script=WORKER):
    path = write(root, "worker/worker.py", script)
    write(root, "worker/reply.txt", reply if isinstance(reply, str) else json.dumps(reply))
    return "%s %s" % (shlex.quote(sys.executable), shlex.quote(path))


def received(root, name):
    with open(os.path.join(root, "worker", name), encoding="utf-8") as handle:
        return handle.read()


def item(name, **extra):
    return {
        "name": name,
        "task": "do " + name,
        "files": [name + ".py"],
        "source": None,
        "acceptance": [],
        **extra,
    }


def run(root, document_text, reply, **extra):
    document = write(root, "docs/spec.md", document_text)
    return decompose.decompose(document, worker(root, reply), root, timeout=20, **extra)


def bare(name, **extra):
    return {
        "name": name,
        "files": [name + ".py"],
        "source": None,
        "acceptance": [],
        **extra,
    }


def run_structure(root, document_text, reply, **extra):
    document = write(root, "docs/spec.md", document_text)
    return decompose.structure(document, worker(root, reply), root, timeout=20, **extra)


def delayed(seconds, reply):
    return [
        sys.executable,
        "-c",
        "import sys, time; time.sleep(float(sys.argv[1])); print(sys.argv[2])",
        str(seconds),
        json.dumps(reply),
    ]


def job(argv, prompt="", log=None):
    return {"argv": argv, "prompt": prompt, "log": log}


class Contract(unittest.TestCase):
    def the_contract_names_every_required_item_field(self):
        for field in core.REQUIRED_ITEM_FIELDS:
            self.assertIn(field, decompose.CONTRACT)
        for field in core.ITEM_FIELDS:
            self.assertIn(field, decompose.CONTRACT)
        for key in decompose.RETURN_KEYS:
            self.assertIn('"%s"' % key, decompose.CONTRACT)
        self.assertEqual(decompose.RETURN_KEYS, ("items", "assumptions", "constraints"))

    def the_contract_demands_runnable_acceptance_not_prose(self):
        for key in core.ACCEPTANCE_KEYS:
            self.assertIn('"%s"' % key, decompose.CONTRACT)
        self.assertIn("never prose", decompose.CONTRACT)
        source = {"path": "docs/spec.md", "sha256": "0" * 64}
        checked = decompose.check_items(
            [
                {
                    "name": "prose",
                    "task": "do it",
                    "files": ["a.py"],
                    "acceptance": "run the tests and make sure they pass",
                },
                {
                    "name": "runnable",
                    "task": "do it",
                    "files": ["a.py"],
                    "acceptance": [{"file": "tests/test_a.py", "test": "a_passes"}],
                },
            ],
            source,
        )
        self.assertEqual([item["name"] for item in checked["items"]], ["prose", "runnable"])
        self.assertEqual(len(checked["errors"]), 1)
        self.assertIn("prose", checked["errors"][0])
        self.assertIn("acceptance", checked["errors"][0])

    def the_prompt_carries_the_document_path(self):
        document = frozen("docs/specs/change.md", "# Alpha\n\ntext\n\n# Omega\n\nmore\n")
        codebase = {"root": "/repo", "paths": ["core.py", "tests/test_core.py"], "overflow": 3}
        prompt = decompose.render_prompt(
            document, codebase, graph="graph.json", charter="CHARTER.md"
        )
        self.assertIn("docs/specs/change.md", prompt)
        self.assertIn(document["sha256"], prompt)
        self.assertIn("# Alpha", prompt)
        self.assertIn("# Omega", prompt)
        self.assertIn("core.py", prompt)
        self.assertIn("tests/test_core.py", prompt)
        self.assertIn("3 more", prompt)
        self.assertIn("graph.json", prompt)
        self.assertIn("CHARTER.md", prompt)
        self.assertIn(decompose.CONTRACT, prompt)
        bare = decompose.render_prompt(document, None)
        self.assertIn("docs/specs/change.md", bare)
        self.assertNotIn("graph.json", bare)

    def build_argv_splits_before_substituting(self):
        prompt = 'say "hi"; rm -rf / && echo $HOME\nsecond line'
        argv = decompose.build_argv(
            "worker --prompt {prompt} --model {model} --doc={document}",
            {
                decompose.PROMPT_PLACEHOLDER: prompt,
                decompose.MODEL_PLACEHOLDER: "top-model",
                decompose.DOCUMENT_PLACEHOLDER: "docs/spec.md",
            },
        )
        self.assertEqual(
            argv, ["worker", "--prompt", prompt, "--model", "top-model", "--doc=docs/spec.md"]
        )

    def a_template_placeholder_without_a_value_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            decompose.build_argv("worker {model}", {decompose.PROMPT_PLACEHOLDER: "p"})
        self.assertIn("{model}", str(caught.exception))
        with self.assertRaises(ValueError):
            decompose.build_argv("   ", {})

    def freezing_hashes_the_bytes_and_keeps_the_text(self):
        with tempfile.TemporaryDirectory() as root:
            path = write(root, "spec.md", "# Title\n\nbody\n")
            document = decompose.freeze(path)
            self.assertEqual(document["path"], path)
            self.assertEqual(document["sha256"], hashlib.sha256(b"# Title\n\nbody\n").hexdigest())
            self.assertEqual(document["text"], "# Title\n\nbody\n")
            self.assertEqual(
                decompose.source_of(document),
                {"path": path, "sha256": document["sha256"]},
            )
            with open(os.path.join(root, "blob.bin"), "wb") as handle:
                handle.write(b"\xff\xfe\x00binary")
            blob = decompose.freeze(os.path.join(root, "blob.bin"))
            self.assertIsNone(blob["text"])
            self.assertEqual(len(blob["sha256"]), 64)

    def the_inventory_reports_overflow_instead_of_hiding_it(self):
        with tempfile.TemporaryDirectory() as root:
            for relative in ("b.py", "a.py", "pkg/c.py", ".git/HEAD", "node_modules/x.js"):
                write(root, relative, "")
            full = decompose.inventory(root)
            self.assertEqual(full["paths"], ["a.py", "b.py", "pkg/c.py"])
            self.assertEqual(full["overflow"], 0)
            capped = decompose.inventory(root, cap=2)
            self.assertEqual(capped["paths"], ["a.py", "b.py"])
            self.assertEqual(capped["overflow"], 1)


class Run(unittest.TestCase):
    def an_edge_between_distant_sections_survives(self):
        text = "".join("# %d. Section %d\n\nbody %d\n\n" % (n, n, n) for n in range(1, 10))
        reply = {
            "items": [
                item("from-one", spec_ref=["1"]),
                item("from-nine", after=["from-one"], spec_ref=["9"]),
            ],
            "assumptions": [],
            "constraints": [],
        }
        with tempfile.TemporaryDirectory() as root:
            result = run(root, text, reply)
            prompt = received(root, "prompt.txt")
            self.assertIn("# 1. Section 1", prompt)
            self.assertIn("# 9. Section 9", prompt)
            self.assertEqual(prompt.count("# 5. Section 5"), 1)
        self.assertEqual(result["errors"], [])
        by_name = {step["name"]: step for step in result["items"]}
        self.assertEqual(by_name["from-nine"]["after"], ["from-one"])
        self.assertEqual(core.validate(result["items"])["errors"], [])

    def a_document_with_no_global_statements_reports_zero(self):
        reply = {"items": [item("only")], "assumptions": [], "constraints": []}
        with tempfile.TemporaryDirectory() as root:
            result = run(root, "# One\n\nbody\n", reply)
        self.assertEqual(result["constraints"], [])
        self.assertIn("0 global constraints extracted", decompose.report(result))
        reply = {**reply, "constraints": ["never write comments", "standard library only"]}
        with tempfile.TemporaryDirectory() as root:
            result = run(root, "# One\n\nbody\n", reply)
        self.assertEqual(len(result["constraints"]), 2)
        self.assertIn("2 global constraints extracted", decompose.report(result))

    def the_frozen_hash_is_stamped_into_every_item(self):
        text = "# One\n\nbody\n\n# Two\n\nmore\n"
        reply = {
            "items": [
                item("a"),
                item("b", source={"path": "wrong.md", "sha256": "f" * 64}),
                item("c"),
            ],
            "assumptions": [],
            "constraints": [],
        }
        with tempfile.TemporaryDirectory() as root:
            result = run(root, text, reply)
            document = os.path.join(root, "docs", "spec.md")
        expected = {"path": document, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        self.assertEqual(result["source"], expected)
        self.assertEqual(len(result["items"]), 3)
        for step in result["items"]:
            self.assertEqual(step["source"], expected)
            self.assertEqual(tuple(step["source"]), core.SOURCE_KEYS)
        self.assertEqual(result["errors"], [])

    def an_item_missing_a_required_field_is_reported_not_dropped(self):
        broken = {"name": "no-files", "task": "do it", "acceptance": []}
        reply = {"items": [item("whole"), broken], "assumptions": [], "constraints": []}
        with tempfile.TemporaryDirectory() as root:
            result = run(root, "# One\n\nbody\n", reply)
        self.assertEqual([step["name"] for step in result["items"]], ["whole", "no-files"])
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("no-files", result["errors"][0])
        self.assertIn("files", result["errors"][0])
        self.assertIn(result["errors"][0], decompose.report(result))

    def assumptions_reach_the_item_they_belong_to(self):
        reply = {
            "items": [
                item("plain", complexity="simple"),
                item("vague", complexity="simple", assumptions=["inline reading"]),
            ],
            "assumptions": [
                {"step": "vague", "text": "the endpoint is idempotent"},
                {"step": "vague", "text": "inline reading"},
                {"step": "ghost", "text": "belongs to nobody"},
            ],
            "constraints": [],
        }
        with tempfile.TemporaryDirectory() as root:
            result = run(root, "# One\n\nbody\n", reply)
        by_name = {step["name"]: step for step in result["items"]}
        self.assertEqual(
            by_name["vague"]["assumptions"], ["inline reading", "the endpoint is idempotent"]
        )
        self.assertEqual(by_name["vague"]["complexity"], "complex")
        self.assertNotIn("assumptions", by_name["plain"])
        self.assertEqual(by_name["plain"]["complexity"], "simple")
        self.assertEqual(result["raised"], ["vague"])
        self.assertEqual(result["counts"]["assumptions"], 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("ghost", result["errors"][0])

    def a_worker_that_never_exits_is_killed_and_reported(self):
        with tempfile.TemporaryDirectory() as root:
            document = write(root, "docs/spec.md", "# One\n\nbody\n")
            template = worker(root, "", script=SLEEPER)
            result = decompose.decompose(document, template, root, timeout=0.5)
        self.assertEqual(result["items"], [])
        self.assertTrue(any("timeout" in error for error in result["errors"]))

    def a_return_that_is_not_json_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            result = run(root, "# One\n\nbody\n", "I decomposed it, here are the steps")
        self.assertEqual(result["items"], [])
        self.assertTrue(any("JSON" in error for error in result["errors"]))
        with tempfile.TemporaryDirectory() as root:
            result = run(root, "# One\n\nbody\n", json.dumps({"items": {"a": 1}}))
        self.assertEqual(result["items"], [])
        self.assertTrue(any("items" in error for error in result["errors"]))
        self.assertTrue(any("assumptions" in error for error in result["errors"]))
        self.assertTrue(any("constraints" in error for error in result["errors"]))

    def a_non_zero_exit_with_a_valid_line_is_still_a_failure(self):
        reply = {"items": [item("a")], "assumptions": [], "constraints": []}
        kept = os.environ.get("WORKER_EXIT")
        os.environ["WORKER_EXIT"] = "3"
        try:
            with tempfile.TemporaryDirectory() as root:
                result = run(root, "# One\n\nbody\n", reply)
        finally:
            if kept is None:
                del os.environ["WORKER_EXIT"]
            else:
                os.environ["WORKER_EXIT"] = kept
        self.assertEqual([step["name"] for step in result["items"]], ["a"])
        self.assertEqual(result["exit"], 3)
        self.assertTrue(any("exited 3" in error for error in result["errors"]))

    def worker_output_goes_to_disk_and_the_record_stays_small(self):
        reply = {"items": [item("a")], "assumptions": [], "constraints": []}
        with tempfile.TemporaryDirectory() as root:
            log = os.path.join(root, "run", "decompose.log")
            result = run(root, "# One\n\nbody\n", reply, log=log)
            with open(log, encoding="utf-8") as handle:
                transcript = handle.read()
            argv = json.loads(received(root, "argv.json"))
        self.assertIn("chatter that must stay out of the record", transcript)
        self.assertIn("more chatter", transcript)
        self.assertEqual(result["log"], log)
        self.assertNotIn("chatter", json.dumps(result))
        self.assertEqual(argv, [])

    def a_template_can_name_the_document_and_the_model(self):
        reply = {"items": [item("a")], "assumptions": [], "constraints": []}
        with tempfile.TemporaryDirectory() as root:
            document = write(root, "docs/spec.md", "# One\n\nbody\n")
            template = worker(root, reply) + " --doc {document} --model {model}"
            result = decompose.decompose(document, template, root, timeout=20, model="top-model")
            argv = json.loads(received(root, "argv.json"))
        self.assertEqual(argv, ["--doc", document, "--model", "top-model"])
        self.assertEqual(result["errors"], [])
        with tempfile.TemporaryDirectory() as root:
            document = write(root, "docs/spec.md", "# One\n\nbody\n")
            with self.assertRaises(ValueError):
                decompose.decompose(document, worker(root, reply) + " {model}", root, timeout=20)

    def a_command_that_cannot_start_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            document = write(root, "docs/spec.md", "# One\n\nbody\n")
            result = decompose.decompose(document, "/nonexistent/worker --go", root, timeout=5)
        self.assertEqual(result["items"], [])
        self.assertIsNone(result["exit"])
        self.assertTrue(any("could not start" in error for error in result["errors"]))


SPEC = "\n".join(
    (
        "# 1. Intro",
        "",
        "words",
        "",
        "## 1.1 Scope",
        "",
        "more words",
        "",
        "```",
        "# not a heading",
        "```",
        "",
        "## 2. Build",
        "",
        "Setext title",
        "============",
        "",
        "### Unnumbered heading",
        "",
        "tail",
        "",
    )
)


class Coverage(unittest.TestCase):
    def sections_come_from_markdown_headings(self):
        found = decompose.sections(SPEC)
        self.assertEqual(found["mode"], "headings")
        self.assertIsNone(found["reason"])
        self.assertEqual(
            [(s["id"], s["title"], s["level"]) for s in found["sections"]],
            [
                ("1", "Intro", 1),
                ("1.1", "Scope", 2),
                ("2", "Build", 2),
                ("Setext title", "Setext title", 1),
                ("Unnumbered heading", "Unnumbered heading", 3),
            ],
        )
        self.assertEqual([s["line"] for s in found["sections"]], [1, 5, 13, 15, 18])

    def an_unclaimed_section_is_reported(self):
        items = [
            item("intro", spec_ref=["1", "scope"]),
            item("tail", spec_ref="Unnumbered heading"),
            item("build", spec_ref=["## 2. Build"]),
        ]
        covered = decompose.coverage(SPEC, items)
        self.assertEqual(covered["mode"], "headings")
        self.assertEqual([s["id"] for s in covered["uncovered"]], ["Setext title"])
        self.assertEqual(covered["claimed"], ["1", "1.1", "2", "Unnumbered heading"])
        self.assertEqual(covered["unmatched_claims"], [])
        lines = decompose.report({"items": items, "coverage": covered})
        self.assertTrue(any("1 of 5 sections unclaimed" in line for line in lines))
        self.assertTrue(any("Setext title" in line for line in lines))

    def a_claim_matching_no_section_is_reported(self):
        items = [item("intro", spec_ref=["1", "99"]), item("none")]
        covered = decompose.coverage(SPEC, items)
        self.assertEqual(covered["unmatched_claims"], ["intro: 99"])
        self.assertEqual(len(covered["uncovered"]), 4)

    def a_document_without_headings_falls_back_to_lines(self):
        text = "first ask\nsecond ask\n\nthird ask\nfourth ask\n"
        items = [item("one", spec_ref=["1"]), item("rest", spec_ref="3-4")]
        covered = decompose.coverage(text, items)
        self.assertEqual(covered["mode"], "lines")
        self.assertIsNone(covered["reason"])
        self.assertEqual([s["id"] for s in covered["sections"]], ["1", "2", "4", "5"])
        self.assertEqual(
            [(s["id"], s["title"]) for s in covered["uncovered"]],
            [("2", "second ask"), ("5", "fourth ask")],
        )
        self.assertEqual(covered["claimed"], ["1", "4"])
        lines = decompose.report({"items": items, "coverage": covered})
        self.assertTrue(any("by lines" in line for line in lines))

    def an_uncomputable_coverage_says_why(self):
        empty = decompose.coverage("", [item("a", spec_ref=["1"])])
        self.assertEqual(empty["mode"], "none")
        self.assertIn("no headings", empty["reason"])
        self.assertIn("no non-blank lines", empty["reason"])
        self.assertEqual(empty["sections"], [])
        self.assertEqual(empty["uncovered"], [])
        blank = decompose.coverage("\n\n   \n", [])
        self.assertEqual(blank["mode"], "none")
        binary = decompose.coverage(None, [])
        self.assertEqual(binary["mode"], "none")
        self.assertIn("UTF-8", binary["reason"])
        lines = decompose.report({"items": [], "coverage": binary})
        self.assertTrue(any("not computable" in line and "UTF-8" in line for line in lines))

    def no_document_is_ever_refused(self):
        reply = {"items": [item("a", spec_ref=["1"])], "assumptions": [], "constraints": []}
        documents = {
            "empty.md": "",
            "blank.txt": "\n\n",
            "prose.txt": "just one ask\nand another\n",
            "data.json": '{"key": "value"}\n',
            "fenced.md": "```\n# code\n```\n",
            "headed.md": "# 1. Only\n\nbody\n",
        }
        with tempfile.TemporaryDirectory() as root:
            for name, text in documents.items():
                document = write(root, name, text)
                result = decompose.decompose(document, worker(root, reply), root, timeout=20)
                self.assertEqual([step["name"] for step in result["items"]], ["a"], name)
                self.assertIn(result["coverage"]["mode"], ("headings", "lines", "none"), name)
                self.assertEqual(result["errors"], [], name)
            with open(os.path.join(root, "blob.bin"), "wb") as handle:
                handle.write(b"\xff\xfe\x00\x01")
            result = decompose.decompose(
                os.path.join(root, "blob.bin"), worker(root, reply), root, timeout=20
            )
            self.assertEqual(result["coverage"]["mode"], "none")
            self.assertIn("UTF-8", result["coverage"]["reason"])
            self.assertEqual([step["name"] for step in result["items"]], ["a"])
            self.assertIn("blob.bin", received(root, "prompt.txt"))

    def coverage_reaches_the_decompose_result(self):
        reply = {
            "items": [item("one", spec_ref=["1"]), item("two", spec_ref=["Two"])],
            "assumptions": [],
            "constraints": [],
        }
        with tempfile.TemporaryDirectory() as root:
            result = run(root, "# 1. One\n\nbody\n\n# Two\n\nbody\n\n# 3. Three\n\nbody\n", reply)
            prompt = received(root, "prompt.txt")
        self.assertIn("Sections you may claim", prompt)
        self.assertIn("  3 Three", prompt)
        self.assertEqual(result["coverage"]["mode"], "headings")
        self.assertEqual([s["id"] for s in result["coverage"]["uncovered"]], ["3"])
        self.assertTrue(any("3 Three" in line for line in decompose.report(result)))


class ReturnLine(unittest.TestCase):
    def a_bare_json_line_is_the_return(self):
        self.assertEqual(decompose.last_return(['{"items": []}']), '{"items": []}')

    def a_fence_around_the_return_is_stepped_over(self):
        lines = ["```json", '{"items": []}', "```"]
        self.assertEqual(decompose.last_return(lines), '{"items": []}')

    def narration_after_the_return_is_stepped_over(self):
        lines = ['{"items": []}', "That completes the decomposition."]
        self.assertEqual(decompose.last_return(lines), '{"items": []}')

    def the_latest_json_object_wins_over_an_earlier_one(self):
        lines = ['{"items": ["first"]}', "then I revised it", '{"items": ["second"]}']
        self.assertEqual(decompose.last_return(lines), '{"items": ["second"]}')

    def a_json_array_is_not_a_return(self):
        self.assertEqual(decompose.last_return(['{"items": []}', "[1, 2]"]), '{"items": []}')

    def output_with_no_json_reports_the_last_line_it_saw(self):
        self.assertEqual(decompose.last_return(["I could not", "do it"]), "do it")

    def empty_output_has_no_return(self):
        self.assertIsNone(decompose.last_return([]))

    def a_fenced_return_survives_the_whole_spawn(self):
        payload = '```json\n{"items": [], "assumptions": [], "constraints": []}\n```'
        spawned = decompose.spawn(
            [sys.executable, "-c", "import sys; sys.stdout.write(sys.argv[1])", payload], "", 30
        )
        self.assertEqual(decompose.parse_return(spawned["line"])["errors"], [])


class EmptyReturn(unittest.TestCase):
    def no_Steps_from_a_document_with_content_is_a_contract_error(self):
        errors = decompose._empty_errors({"text": "# A\n\nbody\n"}, [])
        self.assertEqual(errors, ["the decompose Worker returned no Steps for a document that has content"])

    def no_Steps_from_an_empty_document_is_not_an_error(self):
        self.assertEqual(decompose._empty_errors({"text": "   \n"}, []), [])

    def Steps_returned_is_never_an_error(self):
        self.assertEqual(decompose._empty_errors({"text": "# A\n"}, [{"name": "a"}]), [])


class Structure(unittest.TestCase):
    def the_structure_contract_forbids_a_task(self):
        contract = decompose.STRUCTURE_CONTRACT
        sentence = "A Step must not carry a task"
        self.assertIn(sentence, contract)
        forbidding = [line for line in contract.splitlines() if sentence in line]
        self.assertEqual(len(forbidding), 1)
        self.assertIn("rejected", forbidding[0])
        self.assertNotIn("task", contract.replace(forbidding[0], ""))
        for field in core.STRUCTURE_ITEM_FIELDS:
            self.assertIn(field, contract)
        for field in decompose.OPTIONAL_ITEM_FIELDS:
            self.assertIn(field, contract)
        self.assertIn(", ".join(core.STRUCTURE_ITEM_FIELDS) + ".", contract)
        self.assertNotIn(", ".join(core.REQUIRED_ITEM_FIELDS) + ".", contract)
        self.assertEqual(contract.count(sentence), 1)

    def a_structure_return_carrying_a_task_is_an_error(self):
        reply = {
            "items": [bare("clean"), bare("prosy", task="a whole brief"), bare("also", task="x")],
            "assumptions": [],
            "constraints": [],
        }
        with tempfile.TemporaryDirectory() as root:
            result = run_structure(root, "# One\n\nbody\n", reply)
        self.assertEqual([step["name"] for step in result["items"]], ["clean", "prosy", "also"])
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("prosy", result["errors"][0])
        self.assertIn("also", result["errors"][0])
        self.assertNotIn("clean", result["errors"][0])
        self.assertIn("task", result["errors"][0])
        self.assertIn(result["errors"][0], decompose.report(result))

    def a_structure_return_without_a_task_validates(self):
        text = "# 1. One\n\nbody\n\n# 2. Two\n\nmore\n"
        reply = {
            "items": [
                bare("first", spec_ref=["1"], task=""),
                bare("second", after=["first"], spec_ref=["2"]),
            ],
            "assumptions": [{"step": "second", "text": "a reading"}],
            "constraints": ["one global"],
        }
        with tempfile.TemporaryDirectory() as root:
            result = run_structure(root, text, reply)
            prompt = received(root, "prompt.txt")
            document = os.path.join(root, "docs", "spec.md")
        self.assertEqual(result["errors"], [])
        expected = {"path": document, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        self.assertEqual(result["source"], expected)
        for step in result["items"]:
            self.assertEqual(step["source"], expected)
        by_name = {step["name"]: step for step in result["items"]}
        self.assertEqual(by_name["second"]["assumptions"], ["a reading"])
        self.assertEqual(by_name["second"]["after"], ["first"])
        self.assertEqual(result["constraints"], ["one global"])
        self.assertEqual(result["coverage"]["uncovered"], [])
        self.assertEqual(result["exit"], 0)
        self.assertIn(decompose.STRUCTURE_CONTRACT, prompt)
        self.assertNotIn(decompose.CONTRACT, prompt)
        self.assertEqual(
            core.validate(result["items"], required=core.STRUCTURE_ITEM_FIELDS)["errors"], []
        )

    def a_structure_result_has_the_shape_a_decompose_result_has(self):
        reply = {"items": [bare("a")], "assumptions": [], "constraints": []}
        with tempfile.TemporaryDirectory() as root:
            structured = run_structure(root, "# One\n\nbody\n", reply)
        with tempfile.TemporaryDirectory() as root:
            decomposed = run(root, "# One\n\nbody\n", {**reply, "items": [item("a")]})
        self.assertEqual(sorted(structured), sorted(decomposed))
        self.assertEqual(structured["counts"], decomposed["counts"])

    def a_structure_prompt_omits_the_decisions_block_when_there_are_none(self):
        document = frozen("docs/specs/change.md", "# Alpha\n\ntext\n\n# Omega\n\nmore\n")
        codebase = {"root": "/repo", "paths": ["core.py"], "overflow": 0}
        expected = decompose.render_prompt(
            document, codebase, graph="graph.json", charter="CHARTER.md"
        ).replace(decompose.CONTRACT, decompose.STRUCTURE_CONTRACT)
        self.assertNotEqual(expected, decompose.render_prompt(document, codebase))
        for decisions in (None, "", {"path": "d.md", "text": "", "count": 0}):
            for prior in (None, [], ()):
                rendered = decompose.render_structure_prompt(
                    document,
                    codebase,
                    graph="graph.json",
                    charter="CHARTER.md",
                    decisions=decisions,
                    prior=prior,
                )
                self.assertEqual(rendered, expected)
        self.assertEqual(decompose.render_structure_prompt(document, codebase), expected.replace(
            "\n\nImport graph: graph.json (adjacency from each path to its neighbours)", ""
        ).replace(
            "\nCharter: CHARTER.md (binding on every Step; every Worker receives it unchanged)", ""
        ))
        self.assertNotIn("settled", expected.lower())
        self.assertNotIn("revis", expected.lower())

    def a_structure_prompt_renders_its_blocks_in_order(self):
        document = frozen("docs/specs/change.md", "# Alpha\n\ntext\n")
        codebase = {"root": "/repo", "paths": ["core.py"], "overflow": 0}
        prior = [bare("kept", task="its brief")]
        rendered = decompose.render_structure_prompt(
            document,
            codebase,
            graph={"core.py": ["shape.py"]},
            charter="CHARTER.md",
            decisions={"path": "d.md", "text": "- the registry is owned by core\n", "count": 1},
            prior=prior,
        )
        compact = json.dumps(prior, separators=(",", ":"))
        self.assertIn(compact, rendered)
        self.assertIn("- the registry is owned by core", rendered)
        positions = [
            rendered.index("Document: docs/specs/change.md"),
            rendered.index("CHARTER.md"),
            rendered.index("Codebase root"),
            rendered.index("Import graph"),
            rendered.index("- the registry is owned by core"),
            rendered.index(compact),
            rendered.index(decompose.DOCUMENT_OPEN),
            rendered.index("Sections you may claim"),
            rendered.index(decompose.STRUCTURE_CONTRACT),
        ]
        self.assertEqual(positions, sorted(positions))
        heading = rendered[: rendered.index(compact)].rstrip("\n").splitlines()[-1].lower()
        self.assertIn("revis", heading)


class Decisions(unittest.TestCase):
    def the_default_decisions_path_sits_beside_the_document(self):
        self.assertEqual(
            decompose.default_decisions_path("docs/specs/change.md"),
            "docs/specs/change.decisions.md",
        )
        self.assertEqual(decompose.default_decisions_path("/a/b/spec.txt"), "/a/b/spec.decisions.md")
        self.assertEqual(decompose.default_decisions_path("plain"), "plain.decisions.md")
        self.assertEqual(
            decompose.default_decisions_path("docs/v1.2/spec.md"), "docs/v1.2/spec.decisions.md"
        )

    def decisions_reach_the_structure_prompt(self):
        text = "- the registry is owned by core\n- the CLI stays single-threaded\n\nprose\n"
        reply = {"items": [bare("a")], "assumptions": [], "constraints": []}
        with tempfile.TemporaryDirectory() as root:
            write(root, "docs/spec.decisions.md", text)
            result = run_structure(root, "# One\n\nbody\n", reply)
            prompt = received(root, "prompt.txt")
        self.assertEqual(result["errors"], [])
        self.assertNotIn("registry is owned by core", prompt)
        with tempfile.TemporaryDirectory() as root:
            path = write(root, "docs/settled.md", text)
            loaded = decompose.load_decisions(root, path="docs/settled.md")
            self.assertEqual(loaded, {"path": path, "text": text, "count": 2})
            result = run_structure(root, "# One\n\nbody\n", reply, decisions=loaded)
            prompt = received(root, "prompt.txt")
        self.assertEqual(result["errors"], [])
        self.assertIn(text.rstrip("\n"), prompt)
        block = prompt[: prompt.index("- the registry is owned by core")]
        heading = block.rstrip("\n").splitlines()[-1].lower()
        self.assertIn("settled", heading)
        self.assertIn("binding", heading)
        self.assertIn("not be re-opened", heading)
        self.assertLess(prompt.index("Codebase root"), prompt.index("registry"))
        self.assertLess(prompt.index("registry"), prompt.index(decompose.DOCUMENT_OPEN))

    def a_named_decisions_file_that_does_not_exist_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError) as caught:
                decompose.load_decisions(root, path="docs/missing.md")
            self.assertIn("docs/missing.md", str(caught.exception))
            absolute = os.path.join(root, "elsewhere.md")
            with self.assertRaises(ValueError) as caught:
                decompose.load_decisions(root, path=absolute, document="docs/spec.md")
            self.assertIn(absolute, str(caught.exception))

    def a_default_decisions_file_that_does_not_exist_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as root:
            document = write(root, "docs/spec.md", "# One\n\nbody\n")
            self.assertIsNone(decompose.load_decisions(root, document=document))
            self.assertIsNone(decompose.load_decisions(root))
            written = write(root, "docs/spec.decisions.md", "- settled\n")
            loaded = decompose.load_decisions(root, document=document)
            self.assertEqual(loaded, {"path": written, "text": "- settled\n", "count": 1})
            relative = decompose.load_decisions(root, document="docs/spec.md")
            self.assertEqual(relative["count"], 1)
            self.assertEqual(os.path.abspath(relative["path"]), os.path.abspath(written))

    def a_decisions_file_with_no_list_items_counts_zero_and_still_loads(self):
        text = "﻿The registry belongs to core.\n  * not a dash item\n-not spaced\n"
        with tempfile.TemporaryDirectory() as root:
            path = write(root, "docs/settled.md", text)
            loaded = decompose.load_decisions(root, path="docs/settled.md")
            self.assertEqual(loaded["count"], 0)
            self.assertEqual(loaded["text"], text.lstrip("﻿"))
            self.assertEqual(loaded["path"], path)
            empty = write(root, "docs/empty.md", "")
            self.assertEqual(
                decompose.load_decisions(root, path=empty), {"path": empty, "text": "", "count": 0}
            )
        document = frozen("docs/spec.md")
        prompt = decompose.render_structure_prompt(document, None, decisions=loaded)
        self.assertIn("The registry belongs to core.", prompt)

    def the_count_is_of_dash_items_after_leading_whitespace(self):
        text = "- one\n  - two nested\n\t- three tabbed\n-- not one\n- \n"
        with tempfile.TemporaryDirectory() as root:
            write(root, "d.md", text)
            self.assertEqual(decompose.load_decisions(root, path="d.md")["count"], 4)


class SpawnMany(unittest.TestCase):
    def spawn_many_returns_results_in_the_order_it_was_given_them(self):
        jobs = [
            job(delayed(0.4, {"n": 0})),
            job(delayed(0.0, {"n": 1})),
            job(delayed(0.2, {"n": 2})),
            job(delayed(0.0, {"n": 3})),
        ]
        results = decompose.spawn_many(jobs, timeout=20, concurrency=4)
        self.assertEqual([json.loads(r["line"])["n"] for r in results], [0, 1, 2, 3])
        self.assertEqual([r["exit"] for r in results], [0, 0, 0, 0])
        self.assertEqual([r["reason"] for r in results], [None] * 4)
        self.assertEqual(decompose.spawn_many([], timeout=1, concurrency=4), [])

    def spawn_many_runs_jobs_concurrently(self):
        jobs = [job(delayed(0.5, {"n": n})) for n in range(4)]
        started = time.monotonic()
        results = decompose.spawn_many(jobs, timeout=20, concurrency=4)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 2.0)
        self.assertEqual([json.loads(r["line"])["n"] for r in results], [0, 1, 2, 3])

    def a_concurrency_at_or_below_zero_runs_one_at_a_time(self):
        for concurrency in (0, -3):
            jobs = [job(delayed(0.3, {"n": n})) for n in range(2)]
            started = time.monotonic()
            results = decompose.spawn_many(jobs, timeout=20, concurrency=concurrency)
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 0.6)
            self.assertEqual([json.loads(r["line"])["n"] for r in results], [0, 1])

    def each_job_reads_its_own_prompt_and_writes_its_own_log(self):
        echo = [
            sys.executable,
            "-c",
            "import sys, json; print(json.dumps({'got': sys.stdin.read()}))",
        ]
        with tempfile.TemporaryDirectory() as root:
            jobs = [
                job(echo, "first", os.path.join(root, "a", "one.log")),
                job(echo, "second", os.path.join(root, "two.log")),
            ]
            results = decompose.spawn_many(jobs, timeout=20, concurrency=2, cwd=root)
            self.assertEqual([json.loads(r["line"])["got"] for r in results], ["first", "second"])
            with open(jobs[0]["log"], encoding="utf-8") as handle:
                self.assertIn("first", handle.read())
            with open(jobs[1]["log"], encoding="utf-8") as handle:
                self.assertIn("second", handle.read())
        self.assertEqual(jobs[0], job(echo, "first", os.path.join(root, "a", "one.log")))

    def a_job_that_times_out_does_not_hold_the_others(self):
        jobs = [
            job([sys.executable, "-c", "import time; time.sleep(60)"]),
            job(delayed(0.0, {"n": 1})),
        ]
        results = decompose.spawn_many(jobs, timeout=0.5, concurrency=2)
        self.assertIn("timeout", results[0]["reason"])
        self.assertIsNone(results[0]["line"])
        self.assertEqual(json.loads(results[1]["line"])["n"], 1)


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
    for case in (
        Contract,
        Run,
        Coverage,
        ReturnLine,
        EmptyReturn,
        Structure,
        Decisions,
        SpawnMany,
    ):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
