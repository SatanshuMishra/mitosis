import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core
import decompose
import shape


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

    def a_repository_is_inventoried_from_what_git_tracks(self):
        with tempfile.TemporaryDirectory() as root:
            subprocess.run(["git", "init", "-q", root], check=True)
            for relative in (".gitignore", "a.py", "pkg/c.py", "gone.py"):
                write(root, relative, "build/\n" if relative == ".gitignore" else "")
            subprocess.run(["git", "-C", root, "add", "."], check=True)
            os.remove(os.path.join(root, "gone.py"))
            for relative in ("build/out.o", "build/deep/cache.bin", "untracked.py"):
                write(root, relative, "")
            listed = decompose.inventory(root)
        self.assertEqual(listed["paths"], [".gitignore", "a.py", "pkg/c.py"])
        self.assertEqual(listed["overflow"], 0)

    def a_file_in_conflict_is_listed_once(self):
        with tempfile.TemporaryDirectory() as root:
            subprocess.run(["git", "init", "-q", root], check=True)
            write(root, "f.py", "conflicted\n")
            write(root, "other.py", "")
            subprocess.run(["git", "-C", root, "add", "other.py"], check=True)
            blob = subprocess.run(
                ["git", "-C", root, "hash-object", "-w", "f.py"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            stages = "".join("100644 %s %d\tf.py\n" % (blob, stage) for stage in (1, 2, 3))
            subprocess.run(
                ["git", "-C", root, "update-index", "--index-info"],
                input=stages, check=True, text=True,
            )
            listed = decompose.inventory(root)
        self.assertEqual(listed["paths"], ["f.py", "other.py"])


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
        self.assertEqual(len(covered["sections"]), 5)

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
        self.assertEqual(covered["unmatched_claims"], [])

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
        self.assertEqual(result["coverage"]["uncovered"][0]["title"], "Three")


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
            rendered.index("Return contract:"),
        ]
        self.assertEqual(positions, sorted(positions))
        heading = rendered[: rendered.index(compact)].rstrip("\n").splitlines()[-1].lower()
        self.assertIn("revis", heading)
        self.assertIn(decompose.DELTA_CONTRACT, rendered)
        self.assertNotIn(decompose.STRUCTURE_CONTRACT, rendered)


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


def delta(**parts):
    return {
        "keep": [],
        "change": [],
        "add": [],
        "remove": [],
        "assumptions": [],
        "constraints": [],
        **parts,
    }


class Delta(unittest.TestCase):
    def the_delta_contract_names_every_key_and_the_step_fields(self):
        for key in decompose.DELTA_KEYS:
            self.assertIn('"%s"' % key, decompose.DELTA_CONTRACT)
        self.assertEqual(
            decompose.DELTA_KEYS,
            ("keep", "change", "add", "remove", "assumptions", "constraints"),
        )
        for field in core.STRUCTURE_ITEM_FIELDS:
            self.assertIn(field, decompose.DELTA_CONTRACT)
        self.assertNotIn("  task:", decompose.DELTA_CONTRACT)

    def parse_delta_reports_a_missing_or_mistyped_key_instead_of_raising(self):
        parsed = decompose.parse_delta(json.dumps({"keep": "a", "add": []}))
        self.assertEqual(sorted(parsed), sorted(decompose.DELTA_KEYS + ("errors",)))
        self.assertEqual(parsed["keep"], [])
        self.assertEqual(parsed["add"], [])
        self.assertTrue(any("keep" in error and "list" in error for error in parsed["errors"]))
        for key in ("change", "remove", "assumptions", "constraints"):
            self.assertTrue(any("missing '%s'" % key in error for error in parsed["errors"]))
            self.assertEqual(parsed[key], [])
        self.assertEqual(decompose.parse_delta(None)["errors"], ["the decompose Worker printed no return line"])
        self.assertTrue(any("JSON" in e for e in decompose.parse_delta("not json")["errors"]))
        self.assertTrue(any("object" in e for e in decompose.parse_delta("[1]")["errors"]))
        whole = decompose.parse_delta(json.dumps(delta(keep=["a"], remove=["b"])))
        self.assertEqual(whole["errors"], [])
        self.assertEqual(whole["keep"], ["a"])
        self.assertEqual(whole["remove"], ["b"])

    def a_kept_step_is_copied_verbatim_including_its_brief(self):
        kept = bare("kept", task="the whole brief", after=["other"], file_notes={"kept.py": "x"})
        prior = [bare("other"), kept, bare("gone")]
        changed = bare("other", files=["other.py", "extra.py"])
        added = bare("fresh", after=["kept"])
        items, errors = decompose.apply_delta(
            prior, delta(keep=["kept"], change=[changed], add=[added], remove=["gone"])
        )
        self.assertEqual(errors, [])
        self.assertEqual([step["name"] for step in items], ["other", "kept", "fresh"])
        self.assertEqual(items[1], kept)
        self.assertIsNot(items[1], kept)
        self.assertEqual(items[1]["task"], "the whole brief")
        self.assertEqual(items[0], changed)
        self.assertNotIn("task", items[0])
        self.assertEqual(items[2], added)

    def a_delta_that_does_not_partition_the_prior_is_rejected_whole(self):
        prior = [bare("a"), bare("b"), bare("c"), bare("d")]
        items, errors = decompose.apply_delta(
            prior, delta(keep=["a", "b"], change=[bare("b")], remove=["c"])
        )
        self.assertEqual(items, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("b", errors[0])
        self.assertIn("d", errors[0])
        self.assertNotIn("'a'", errors[0])
        self.assertNotIn("'c'", errors[0])
        items, errors = decompose.apply_delta(prior, delta(keep=["a", "a", "b", "c", "d"]))
        self.assertEqual(items, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("a", errors[0])
        items, errors = decompose.apply_delta(prior, delta(keep=["a", "b", "c", "d"]))
        self.assertEqual(errors, [])
        self.assertEqual(items, prior)

    def a_missing_keep_or_remove_name_is_named(self):
        prior = [bare("a"), bare("b")]
        items, errors = decompose.apply_delta(prior, delta(keep=["a", "ghost"], remove=["b", "phantom"]))
        self.assertEqual(items, [])
        self.assertTrue(any("keep" in e and "ghost" in e for e in errors))
        self.assertTrue(any("remove" in e and "phantom" in e for e in errors))
        self.assertFalse(any("'a'" in e or "'b'" in e for e in errors))

    def a_changed_step_that_is_not_in_the_prior_is_an_error(self):
        prior = [bare("a"), bare("b")]
        items, errors = decompose.apply_delta(prior, delta(keep=["a", "b"], change=[bare("zed")]))
        self.assertEqual(items, [])
        self.assertTrue(any("change" in e and "zed" in e for e in errors))
        self.assertEqual(len(errors), 1)

    def an_added_step_whose_name_already_exists_is_an_error(self):
        prior = [bare("a"), bare("b")]
        items, errors = decompose.apply_delta(prior, delta(keep=["a", "b"], add=[bare("a")]))
        self.assertEqual(items, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("add", errors[0])
        self.assertIn("a", errors[0])
        items, errors = decompose.apply_delta(
            prior, delta(keep=["a"], remove=["b"], add=[bare("b", files=["new.py"])])
        )
        self.assertEqual(items, [])
        self.assertTrue(any("add" in e and "b" in e for e in errors))

    def a_duplicate_name_in_the_result_is_an_error(self):
        prior = [bare("a")]
        items, errors = decompose.apply_delta(prior, delta(keep=["a"], add=[bare("n"), bare("n")]))
        self.assertEqual(items, [])
        self.assertTrue(any("duplicate" in e and "n" in e for e in errors))
        items, errors = decompose.apply_delta(prior, delta(change=[bare("a"), bare("a")]))
        self.assertEqual(items, [])
        self.assertTrue(any("a" in e for e in errors))

    def a_delta_never_mutates_the_prior_it_was_given(self):
        prior = [bare("a", task="brief a", after=["b"]), bare("b", file_notes={"b.py": "x"})]
        before = json.dumps(prior, sort_keys=True)
        given = delta(keep=["a"], change=[bare("b", files=["b.py", "c.py"])], add=[bare("n")])
        given_before = json.dumps(given, sort_keys=True)
        items, errors = decompose.apply_delta(prior, given)
        self.assertEqual(errors, [])
        items[0]["after"].append("n")
        items[0]["task"] = "rewritten"
        items[1]["files"].append("d.py")
        items[2]["name"] = "renamed"
        self.assertEqual(json.dumps(prior, sort_keys=True), before)
        self.assertEqual(json.dumps(given, sort_keys=True), given_before)
        rejected, errors = decompose.apply_delta(prior, delta(keep=["a"]))
        self.assertEqual(rejected, [])
        self.assertTrue(errors)
        self.assertEqual(json.dumps(prior, sort_keys=True), before)

    def a_wrongly_shaped_delta_entry_is_reported_not_raised(self):
        prior = [bare("a")]
        items, errors = decompose.apply_delta(
            prior, delta(keep=[1], change=["a"], add=[{"files": []}], remove=[None])
        )
        self.assertEqual(items, [])
        self.assertTrue(any("keep" in e for e in errors))
        self.assertTrue(any("change" in e for e in errors))
        self.assertTrue(any("add" in e for e in errors))
        self.assertTrue(any("remove" in e for e in errors))

    def a_structure_given_a_prior_asks_for_and_applies_a_delta(self):
        prior = [bare("kept", task="its brief"), bare("gone")]
        reply = delta(keep=["kept"], remove=["gone"], add=[bare("fresh")], constraints=["global"])
        with tempfile.TemporaryDirectory() as root:
            result = run_structure(root, "# One\n\nbody\n", reply, prior=prior)
            prompt = received(root, "prompt.txt")
        self.assertIn(decompose.DELTA_CONTRACT, prompt)
        self.assertNotIn(decompose.STRUCTURE_CONTRACT, prompt)
        self.assertEqual(result["errors"], [])
        self.assertEqual([step["name"] for step in result["items"]], ["kept", "fresh"])
        self.assertEqual(result["items"][0]["task"], "its brief")
        self.assertEqual(result["constraints"], ["global"])
        self.assertEqual(result["counts"]["no_acceptance"], 2)
        self.assertEqual(result["coverage"]["mode"], "headings")
        with tempfile.TemporaryDirectory() as root:
            result = run_structure(root, "# One\n\nbody\n", delta(keep=["kept"]), prior=prior)
        self.assertEqual(result["items"], [])
        self.assertTrue(any("gone" in e for e in result["errors"]))
        with tempfile.TemporaryDirectory() as root:
            result = run_structure(root, "# One\n\nbody\n", {"items": []}, prior=prior)
        self.assertEqual(result["items"], [])
        self.assertTrue(any("missing 'keep'" in e for e in result["errors"]))


SAMPLER = """
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
prompt = sys.stdin.read()
with open(os.path.join(here, "prompt-%d.txt" % os.getpid()), "w", encoding="utf-8") as handle:
    handle.write(prompt)
print(json.dumps({
    "items": [{"name": "step-%d" % os.getpid(), "files": ["a.py"], "source": None, "acceptance": []}],
    "assumptions": [],
    "constraints": [],
}))
"""


def structured(items, errors=()):
    return {"items": items, "errors": list(errors)}


def recorded_splits():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "tests", "fixtures", "splits.json"), encoding="utf-8") as handle:
        return json.load(handle)


class Sampling(unittest.TestCase):
    def samples_are_ranked_by_scalar_and_none_is_chosen_by_a_model(self):
        wide = structured([bare("a"), bare("b")])
        chained = structured([bare("a"), bare("b", after=["a"], files=["a.py"])])
        fused = structured(
            [bare("a"), bare("b", msp="m"), bare("c", msp="m", after=["b"])]
        )
        results = [chained, wide, fused]

        def refuse(*args, **kwargs):
            raise AssertionError("a model was consulted")

        kept = decompose.spawn, decompose.spawn_many
        decompose.spawn = refuse
        decompose.spawn_many = refuse
        try:
            order = decompose.rank(results)
            self.assertEqual(decompose.rank([wide, wide, wide]), [0, 1, 2])
            self.assertEqual(decompose.rank([]), [])
            self.assertEqual(decompose.rank([fused]), [0])
        finally:
            decompose.spawn, decompose.spawn_many = kept
        self.assertEqual(order, [1, 2, 0])
        self.assertEqual(results, [chained, wide, fused])

    def a_sample_mitosis_would_refuse_never_ranks_first(self):
        broken = structured([
            bare("skeleton", files=["pkg/__init__.py"]),
            bare("x", files=["pkg/x.py"], after=["skeleton"]),
            bare("y", files=["pkg/y.py"], after=["skeleton"]),
        ])
        clean = structured([
            bare("x", files=["pkg/x.py"]),
            bare("y", files=["pkg/y.py"], after=["x"]),
            bare("surface", files=["pkg/__init__.py"], after=["y"]),
        ])
        self.assertTrue(shape.manifest_gaps(broken["items"]))
        self.assertEqual(shape.manifest_gaps(clean["items"]), [])
        self.assertGreater(
            shape.scalars(broken["items"])["parallelism"],
            shape.scalars(clean["items"])["parallelism"],
        )
        self.assertEqual(decompose.rank([broken, clean]), [1, 0])

    def ranking_compares_the_scalars_in_priority_order(self):
        two_lanes = [bare("a"), bare("b")]
        self.assertEqual(shape.scalars(two_lanes)["parallelism"], 2)
        fused_once = [bare("a"), bare("b", msp="m"), bare("c", msp="m", after=["b"])]
        fused_twice = [
            bare("a"),
            bare("b", msp="m"),
            bare("c", msp="m", after=["b"]),
            bare("d", msp="m", after=["c"]),
        ]
        self.assertEqual(shape.scalars(fused_once)["fused_without_overlap"], 1)
        self.assertEqual(shape.scalars(fused_twice)["fused_without_overlap"], 3)
        self.assertEqual(
            decompose.rank([structured(fused_twice), structured(fused_once)]), [1, 0]
        )
        denser = [bare("a"), bare("b")]
        sparser = [bare("a"), bare("b"), bare("c", files=["b.py", "c.py"])]
        self.assertEqual(shape.scalars(sparser)["fused_without_overlap"], 0)
        self.assertGreater(
            shape.scalars(denser)["msps_per_step"], shape.scalars(sparser)["msps_per_step"]
        )
        self.assertEqual(decompose.rank([structured(sparser), structured(denser)]), [1, 0])
        self.assertEqual(decompose.rank([structured(denser), structured(sparser)]), [0, 1])

    def a_sample_with_contract_errors_ranks_last(self):
        wide = structured([bare("a"), bare("b")], errors=["a: missing required field 'files'"])
        chained = structured([bare("a"), bare("b", after=["a"], files=["a.py"])])
        self.assertEqual(decompose.rank([wide, chained]), [1, 0])
        also_wide = structured([bare("a"), bare("b")], errors=["x"])
        self.assertEqual(decompose.rank([wide, also_wide, chained]), [2, 0, 1])
        broken = {"items": [bare("a"), "not a step"], "errors": ["item #1: a Step must be an object"]}
        self.assertEqual(decompose.rank([broken, chained]), [1, 0])

    def the_recorded_splits_tie_on_parallelism_and_rank_by_fusion(self):
        splits = recorded_splits()
        names = ["pass-1", "pass-2", "pass-3"]
        results = [structured(splits[name]) for name in names]
        self.assertEqual({shape.scalars(splits[name])["parallelism"] for name in names}, {1})
        order = decompose.rank(results)
        self.assertEqual([names[i] for i in order], ["pass-2", "pass-1", "pass-3"])
        sentences = decompose.disagreements(results)
        self.assertTrue(any("bleep/effects/__init__.py" in s for s in sentences))
        self.assertTrue(any("demo/canyon.blp" in s for s in sentences))
        self.assertFalse(any("bleep/notation.py" in s for s in sentences))
        self.assertTrue(any("MSP" in s for s in sentences))
        self.assertFalse(any("parallelism" in s for s in sentences))

    def samples_that_split_a_file_differently_are_reported_as_a_disagreement(self):
        one_owner = structured([bare("a", files=["a.py", "shared.py"]), bare("b")])
        two_owners = structured(
            [bare("a", files=["a.py", "shared.py"]), bare("b", files=["b.py", "shared.py"])]
        )
        sentences = decompose.disagreements([one_owner, two_owners])
        self.assertEqual(len(sentences), 3)
        for sentence in sentences:
            self.assertTrue(sentence.endswith("."))
            self.assertEqual(sentence.count(". "), 0)
        self.assertTrue(any("shared.py" in s and "own" in s for s in sentences))
        self.assertFalse(any("a.py" in s for s in sentences))
        self.assertFalse(any("b.py" in s for s in sentences))
        self.assertTrue(any("MSP" in s for s in sentences))
        self.assertTrue(any("parallelism" in s for s in sentences))
        self.assertEqual(decompose.disagreements([one_owner, one_owner]), [])
        self.assertEqual(decompose.disagreements([one_owner]), [])
        self.assertEqual(decompose.disagreements([]), [])
        only_in_one = structured([bare("a", files=["a.py", "shared.py"]), bare("b"), bare("c")])
        sentences = decompose.disagreements([one_owner, only_in_one])
        self.assertTrue(any("c.py" in s for s in sentences))
        self.assertFalse(any("shared.py" in s for s in sentences))

    def one_sample_takes_the_same_path_as_a_single_structure_call(self):
        reply = {"items": [bare("a", spec_ref=["1"])], "assumptions": [], "constraints": []}
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            document = write(root, "docs/spec.md", "# 1. One\n\nbody\n")
            template = worker(home, reply) + " --model {model}"
            log = os.path.join(home, "run", "structure.log")
            single = decompose.structure(
                document, template, root, timeout=20, model="m", charter="C.md", log=log
            )
            single_prompt = received(home, "prompt.txt")
            single_argv = received(home, "argv.json")
            sampled = decompose.sample_structures(
                1, document, template, root, timeout=20, model="m", charter="C.md", log=log
            )
            self.assertEqual(received(home, "prompt.txt"), single_prompt)
            self.assertEqual(received(home, "argv.json"), single_argv)
            self.assertTrue(os.path.isfile(log))
        self.assertEqual(sampled, [single])
        self.assertEqual(sampled[0]["log"], log)
        self.assertEqual(single["errors"], [])
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            document = write(root, "docs/spec.md", "# 1. One\n\nbody\n")
            template = worker(home, reply)
            for samples in (0, -1, None):
                self.assertEqual(
                    decompose.sample_structures(samples, document, template, root, timeout=20),
                    [decompose.structure(document, template, root, timeout=20)],
                )

    def several_samples_run_concurrently_against_one_prompt(self):
        with tempfile.TemporaryDirectory() as root:
            document = write(root, "docs/spec.md", "# 1. One\n\nbody\n")
            template = worker(root, "", script=SAMPLER)
            log = os.path.join(root, "run", "structure.log")
            results = decompose.sample_structures(3, document, template, root, timeout=20, log=log)
            prompts = sorted(
                name for name in os.listdir(os.path.join(root, "worker")) if name.startswith("prompt-")
            )
            self.assertEqual(len(prompts), 3)
            texts = {received(root, name) for name in prompts}
            self.assertEqual(len(texts), 1)
            self.assertIn(decompose.STRUCTURE_CONTRACT, texts.pop())
            logs = [result["log"] for result in results]
            self.assertEqual(len(set(logs)), 3)
            for path in logs:
                self.assertTrue(os.path.isfile(path), path)
                self.assertTrue(path.startswith(os.path.join(root, "run", "structure")), path)
                self.assertTrue(path.endswith(".log"), path)
            self.assertFalse(os.path.exists(log))
        self.assertEqual(len(results), 3)
        names = [result["items"][0]["name"] for result in results]
        self.assertEqual(len(set(names)), 3)
        for result in results:
            self.assertEqual(result["errors"], [])
            self.assertEqual(sorted(result), sorted(results[0]))
        self.assertEqual(decompose.rank(results), [0, 1, 2])
        self.assertEqual(decompose.disagreements(results), [])


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


FLAKY = """
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
tally = os.path.join(here, "tally-%s.txt" % sys.argv[1])
seen = 0
if os.path.isfile(tally):
    with open(tally, encoding="utf-8") as handle:
        seen = int(handle.read())
with open(tally, "w", encoding="utf-8") as handle:
    handle.write(str(seen + 1))
sys.stdin.read()
if seen < int(sys.argv[2]):
    print(sys.argv[3])
else:
    print(json.dumps({"ok": True, "attempt": seen + 1}))
"""


def flaky(root, label, bad_rounds, bad_line="not a json object at all"):
    path = write(root, "flaky.py", FLAKY)
    return [sys.executable, path, label, str(bad_rounds), bad_line]


def tally(root, label):
    path = os.path.join(root, "tally-%s.txt" % label)
    if not os.path.isfile(path):
        return 0
    with open(path, encoding="utf-8") as handle:
        return int(handle.read())


def _is_json_object(spawned):
    line = spawned["line"]
    if line is None:
        return False
    try:
        return isinstance(json.loads(line), dict)
    except ValueError:
        return False


class StructureRetry(unittest.TestCase):
    def a_structure_dispatch_that_returns_no_json_object_is_asked_again(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "docs/spec.md", "# One\n\nbody\n")
            good = json.dumps({
                "items": [{
                    "name": "only", "type": "feature", "files": ["pkg/only.py"],
                    "acceptance": [{"file": "tests/test_only.py", "test": "a_thing_holds"}],
                    "spec_ref": "1", "complexity": "low", "source": "1",
                }],
                "assumptions": [], "constraints": [],
            })
            argv = flaky(root, "structure", 1, bad_line="chatter, no object")
            template = " ".join(shlex.quote(part) for part in argv)
            write(root, "flaky.py", FLAKY.replace(
                'print(json.dumps({"ok": True, "attempt": seen + 1}))',
                "print(%r)" % good,
            ))
            result = decompose.structure(
                os.path.join(root, "docs/spec.md"), template, root, timeout=20,
            )
            self.assertEqual(result["errors"], [])
            self.assertEqual([item["name"] for item in result["items"]], ["only"])
            self.assertEqual(tally(root, "structure"), 2)

    def a_valid_revision_is_dispatched_once_and_a_broken_one_twice(self):
        prior = [bare("kept", task="its brief")]
        for bad_rounds, dispatches in ((0, 1), (1, 2)):
            with self.subTest(bad_rounds=bad_rounds), tempfile.TemporaryDirectory() as root:
                write(root, "docs/spec.md", "# One\n\nbody\n")
                argv = flaky(root, "revise", bad_rounds, bad_line="chatter, no object")
                write(root, "flaky.py", FLAKY.replace(
                    'print(json.dumps({"ok": True, "attempt": seen + 1}))',
                    "print(%r)" % json.dumps(delta(keep=["kept"])),
                ))
                result = decompose.structure(
                    os.path.join(root, "docs/spec.md"),
                    " ".join(shlex.quote(part) for part in argv),
                    root,
                    timeout=20,
                    prior=prior,
                )
                self.assertEqual(result["errors"], [])
                self.assertEqual([step["name"] for step in result["items"]], ["kept"])
                self.assertEqual(tally(root, "revise"), dispatches)

    def a_structure_dispatch_that_never_returns_an_object_stops_at_the_bound(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "docs/spec.md", "# One\n\nbody\n")
            argv = flaky(root, "structure", 99, bad_line="chatter, no object")
            template = " ".join(shlex.quote(part) for part in argv)
            result = decompose.structure(
                os.path.join(root, "docs/spec.md"), template, root, timeout=20,
            )
            self.assertTrue(result["errors"])
            self.assertEqual(tally(root, "structure"), decompose.DISPATCH_ATTEMPTS)


class SchedulingGuidance(unittest.TestCase):
    def the_structure_prompt_carries_every_scheduling_rule(self):
        prompt = decompose.render_structure_prompt(frozen(), [], None, None, None, None)
        for line in decompose.SCHEDULING_LINES:
            self.assertIn(line, prompt)

    def the_guidance_names_each_hazard_a_returned_plan_can_carry(self):
        text = " ".join(decompose.SCHEDULING_LINES)
        self.assertIn("same file in their write-sets", text)
        self.assertIn("public surface, not a formality", text)
        self.assertIn("EMPTY acceptance list", text)
        self.assertIn("FAIL against the current code", text)


class SpawnUntil(unittest.TestCase):
    def a_result_the_predicate_rejects_is_dispatched_again(self):
        with tempfile.TemporaryDirectory() as root:
            jobs = [job(flaky(root, "a", 1))]
            results = decompose.spawn_until(
                jobs, timeout=20, concurrency=1, cwd=root,
                accepts=lambda index, spawned: _is_json_object(spawned),
            )
            self.assertTrue(_is_json_object(results[0]))
            self.assertEqual(json.loads(results[0]["line"])["attempt"], 2)
            self.assertEqual(tally(root, "a"), 2)

    def a_result_the_predicate_accepts_is_never_dispatched_twice(self):
        with tempfile.TemporaryDirectory() as root:
            jobs = [job(flaky(root, "a", 0))]
            decompose.spawn_until(
                jobs, timeout=20, concurrency=1, cwd=root,
                accepts=lambda index, spawned: _is_json_object(spawned),
            )
            self.assertEqual(tally(root, "a"), 1)

    def only_the_rejected_job_is_dispatched_again(self):
        with tempfile.TemporaryDirectory() as root:
            jobs = [job(flaky(root, "good", 0)), job(flaky(root, "bad", 1))]
            results = decompose.spawn_until(
                jobs, timeout=20, concurrency=2, cwd=root,
                accepts=lambda index, spawned: _is_json_object(spawned),
            )
            self.assertEqual(tally(root, "good"), 1)
            self.assertEqual(tally(root, "bad"), 2)
            self.assertEqual([_is_json_object(one) for one in results], [True, True])

    def a_result_that_never_satisfies_the_predicate_stops_at_the_bound(self):
        with tempfile.TemporaryDirectory() as root:
            jobs = [job(flaky(root, "a", 99))]
            results = decompose.spawn_until(
                jobs, timeout=20, concurrency=1, cwd=root,
                accepts=lambda index, spawned: _is_json_object(spawned),
            )
            self.assertEqual(tally(root, "a"), decompose.DISPATCH_ATTEMPTS)
            self.assertFalse(_is_json_object(results[0]))

    def the_bound_is_at_least_one_attempt(self):
        with tempfile.TemporaryDirectory() as root:
            jobs = [job(flaky(root, "a", 99))]
            decompose.spawn_until(
                jobs, timeout=20, concurrency=1, cwd=root,
                accepts=lambda index, spawned: _is_json_object(spawned),
                attempts=0,
            )
            self.assertEqual(tally(root, "a"), 1)

    def results_come_back_in_the_order_the_jobs_were_given(self):
        with tempfile.TemporaryDirectory() as root:
            jobs = [job(delayed(0.3, {"n": 0})), job(delayed(0.0, {"n": 1}))]
            results = decompose.spawn_until(
                jobs, timeout=20, concurrency=2, cwd=root,
                accepts=lambda index, spawned: True,
            )
            self.assertEqual([json.loads(one["line"])["n"] for one in results], [0, 1])

    def each_result_carries_how_many_attempts_it_took(self):
        with tempfile.TemporaryDirectory() as root:
            jobs = [job(flaky(root, "good", 0)), job(flaky(root, "bad", 1))]
            results = decompose.spawn_until(
                jobs, timeout=20, concurrency=2, cwd=root,
                accepts=lambda index, spawned: _is_json_object(spawned),
            )
            self.assertEqual([one["attempts"] for one in results], [1, 2])

    def no_jobs_is_no_results(self):
        self.assertEqual(
            decompose.spawn_until([], timeout=1, concurrency=2, cwd=None,
                                  accepts=lambda index, spawned: True),
            (),
        )


class PlainNumberedSections(unittest.TestCase):
    RFC = (
        "1.  Introduction\n\nbody\n\n"
        "2.  Conventions\n\nbody\n\n"
        "3.  Encoding\n\nbody\n\n"
        "3.1.  Padding\n\nbody\n"
    )

    def a_numbered_plain_text_document_parses_as_real_sections(self):
        found = decompose.sections(self.RFC)
        self.assertEqual(found["mode"], "headings")
        self.assertEqual([s["id"] for s in found["sections"]], ["1", "2", "3", "3.1"])
        self.assertEqual(found["sections"][3]["title"], "Padding")

    def a_number_without_a_period_is_not_a_heading(self):
        text = "1  Introduction\n\n2  Conventions\n\n3  Encoding\n"
        self.assertEqual(decompose.sections(text)["mode"], "lines")

    def wrapped_prose_beginning_with_a_number_is_not_a_heading(self):
        text = (
            "256 K is the block size used by clients before version 3.2 and it\n"
            "is subdivided further.\n\n"
            "20 It is to be subdivided into strings of length 20, each of which\n"
            "is the SHA1 hash.\n\n"
            "99 Another wrapped line that happens to start with a number here.\n"
        )
        self.assertEqual(decompose.sections(text)["mode"], "lines")

    def a_numbering_that_does_not_start_at_one_is_not_a_heading_run(self):
        text = "4.  Fourth\n\n5.  Fifth\n\n6.  Sixth\n"
        self.assertEqual(decompose.sections(text)["mode"], "lines")

    def a_numbering_that_runs_backwards_is_not_a_heading_run(self):
        text = "1.  One\n\n5.  Five\n\n3.  Three\n"
        self.assertEqual(decompose.sections(text)["mode"], "lines")

    def fewer_than_three_numbered_lines_is_not_a_heading_run(self):
        text = "1.  One\n\n2.  Two\n"
        self.assertEqual(decompose.sections(text)["mode"], "lines")

    def an_indented_numbered_line_is_not_a_heading(self):
        text = "1.  One\n\n    2.  Indented\n\n3.  Three\n\n4.  Four\n"
        found = decompose.sections(text)
        self.assertEqual([s["id"] for s in found["sections"]], ["1", "3", "4"])

    def a_markdown_document_still_parses_by_its_markdown_headings(self):
        text = "# 1. One\n\nbody\n\n## 2. Two\n\nbody\n\n## 3. Three\n\nbody\n"
        found = decompose.sections(text)
        self.assertEqual(found["mode"], "headings")
        self.assertEqual([s["id"] for s in found["sections"]], ["1", "2", "3"])

    def a_claim_against_a_numbered_plain_text_section_matches(self):
        covered = decompose.coverage(self.RFC, [item("one", spec_ref=["3.1"])])
        self.assertEqual(covered["unmatched_claims"], [])
        self.assertEqual(covered["claimed"], ["3.1"])


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
        Delta,
        Sampling,
        SchedulingGuidance,
        SpawnMany,
        SpawnUntil,
        StructureRetry,
        PlainNumberedSections,
    ):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
