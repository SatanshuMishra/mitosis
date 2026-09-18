import hashlib
import json
import os
import shlex
import sys
import tempfile
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
    for case in (Contract, Run, Coverage):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
