import ast
import json
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RELEASE = "0.12.0"
DOCS = ("README.md", os.path.join("skills", "mitosis", "SKILL.md"))
VERSION_HEADING = re.compile(r"^## (\d+\.\d+\.\d+)\b", re.MULTILINE)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def read(relative):
    with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
        return handle.read()


def assigned_version(source):
    found = [
        node.value.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
        and isinstance(node.value, ast.Constant)
    ]
    return found[0] if len(found) == 1 else None


def sentences(text):
    flat = " ".join(text.replace("`", "").replace("\u2019", "'").split())
    return [sentence for sentence in SENTENCE_END.split(flat) if sentence]


def says(text, *phrases):
    return any(all(phrase.lower() in sentence.lower() for phrase in phrases) for sentence in sentences(text))


class Release012(unittest.TestCase):
    def test_the_version_is_0_12_0_everywhere(self):
        self.assertEqual(assigned_version(read("core.py")), RELEASE)
        self.assertEqual(json.loads(read(os.path.join(".claude-plugin", "plugin.json"))).get("version"), RELEASE)
        headings = VERSION_HEADING.findall(read("CHANGELOG.md"))
        self.assertTrue(headings, "CHANGELOG.md holds no version heading")
        self.assertEqual(headings[0], RELEASE)

    def test_the_docs_describe_per_msp_locks_and_no_timeout(self):
        for relative in DOCS:
            with self.subTest(doc=relative):
                text = read(relative)
                self.assertTrue(
                    says(text, "check-author", "MSP's branch"),
                    "%s says nowhere that each MSP's check-author locks its tests on that MSP's branch"
                    % relative,
                )
                self.assertTrue(
                    says(text, "--timeout", "deprecated and ignored"),
                    "%s says nowhere that --timeout is deprecated and ignored" % relative,
                )

    def test_the_bounded_calls_test_is_retired(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "tests", "test_bounded_calls.py")))


if __name__ == "__main__":
    unittest.main()
