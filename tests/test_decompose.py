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
        prompt = decompose.render_prompt(document, codebase, graph="graph.json", charter="CHARTER.md")
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
        self.assertEqual(argv, ["worker", "--prompt", prompt, "--model", "top-model", "--doc=docs/spec.md"])

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
    for case in (Contract,):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
