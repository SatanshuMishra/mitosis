import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXCLUDED_DIR_NAMES = frozenset(
    (
        "__pycache__",
        "graphify-out",
        "specs",
        "node_modules",
        ".pytest_cache",
        ".venv",
        "venv",
    )
)

FENCE = re.compile(r"```.*?```", re.S)
INLINE_CODE = re.compile(r"`([^`\n]+)`")
FLAG_SHAPE = re.compile(r"^--[a-z][a-z-]*$")
WORD_SHAPE = re.compile(r"^[a-z][a-z0-9_-]*$")

TRIGGER_WORDS = (
    "flag",
    "flags",
    "key",
    "keys",
    "field",
    "fields",
    "state",
    "states",
    "outcome",
    "outcomes",
)

VOCABULARY_WORDS = frozenset(
    core.PLAN_KEYS + core.ITEM_FIELDS + core.LANE_STATES + core.MSP_STATES + core.GATE_OUTCOMES
)

CHANGELOG_CANDIDATES = ("changelog.md", "changelog.rst", "changelog", "history.md")


def _markdown_files(root):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in EXCLUDED_DIR_NAMES and not name.startswith(".")
        ]
        for name in filenames:
            if name.lower().endswith(".md"):
                found.append(os.path.join(dirpath, name))
    return sorted(found)


def _looks_like_identifier(token, has_trigger_word):
    if FLAG_SHAPE.match(token):
        return True
    if not WORD_SHAPE.match(token):
        return False
    compound = "_" in token or "-" in token
    return compound or has_trigger_word


def _unknown_identifiers(text):
    unknown = []
    for line in FENCE.sub("", text).splitlines():
        has_trigger_word = any(word in line.lower() for word in TRIGGER_WORDS)
        for token in INLINE_CODE.findall(line):
            if not _looks_like_identifier(token, has_trigger_word):
                continue
            if FLAG_SHAPE.match(token):
                if token not in core.FLAG_NAMES:
                    unknown.append(token)
            elif token not in VOCABULARY_WORDS:
                unknown.append(token)
    return unknown


def _changelog_path(root):
    entries = {name.lower(): name for name in os.listdir(root) if os.path.isfile(os.path.join(root, name))}
    for candidate in CHANGELOG_CANDIDATES:
        if candidate in entries:
            return os.path.join(root, entries[candidate])
    return None


def _changelog_head(text):
    lines = text.splitlines()
    headings = [index for index, line in enumerate(lines) if line.startswith("## ")]
    if len(headings) >= 2:
        return "\n".join(lines[headings[0]:headings[1]])
    return text


class Docs(unittest.TestCase):
    def every_identifier_named_in_a_document_exists_in_core(self):
        offenses = {}
        for path in _markdown_files(ROOT):
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            unknown = _unknown_identifiers(text)
            if unknown:
                offenses[os.path.relpath(path, ROOT)] = unknown
        self.assertEqual(offenses, {})

    def the_skill_file_is_within_its_size_cap(self):
        path = os.path.join(ROOT, "adapters", "claude-code", "SKILL.md")
        if not os.path.isfile(path):
            self.skipTest("adapters/claude-code/SKILL.md does not exist yet")
        self.assertLessEqual(os.path.getsize(path), 20480)

    def the_declared_version_matches_the_changelog_head(self):
        path = _changelog_path(ROOT)
        if path is None:
            self.skipTest("no changelog file exists yet")
        with open(path, encoding="utf-8") as handle:
            head = _changelog_head(handle.read())
        self.assertIn(core.__version__, head)


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
    for case in (Docs,):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
