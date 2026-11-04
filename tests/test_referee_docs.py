import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import core

DOCS = ("README.md", os.path.join("skills", "mitosis", "SKILL.md"))
TERMS = (
    "- Locked:",
    "referee",
    "weak",
    "mutant",
    "gate-failed",
    "parked-limit",
    "findings",
    "--trajectory",
    "9",
    "13",
    "24",
)
REMOVED = "never withholds a pull request"
RELEASED = (0, 12, 0)


def text_of(relative):
    with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
        return handle.read()


class RefereeDocs(unittest.TestCase):
    def docs_describe_the_referee_and_the_new_exit_codes(self):
        for relative in DOCS:
            with self.subTest(doc=relative):
                text = text_of(relative)
                missing = [term for term in TERMS if term not in text]
                self.assertEqual(missing, [], "%s does not mention %s" % (relative, missing))
                self.assertNotIn(REMOVED, text)

    def version_is_above_0_12_0(self):
        version = core.__version__
        self.assertGreater(tuple(int(part) for part in version.split(".")), RELEASED)
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["version"], version)
        headed = [
            line for line in text_of("CHANGELOG.md").splitlines() if line.startswith("## " + version)
        ]
        self.assertTrue(headed, "CHANGELOG.md has no line starting ## %s" % version)


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            RefereeDocs,
            (
                "docs_describe_the_referee_and_the_new_exit_codes",
                "version_is_above_0_12_0",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
