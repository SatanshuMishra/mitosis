import importlib
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKILL_LIMIT = 20480
RELEASED = (0, 11, 0)
HANDOFF_RELEASE = "0.13.0"
NAMED_IN_CHANGELOG = (
    "h8",
    "h9",
    "h10",
    "h10b",
    "h10c",
    "h12",
    "h15",
    "h16",
    "k2",
    "k6",
    "--reask-command",
    "contracts/",
)


def module(name):
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    return importlib.import_module(name)


def read_text(relative):
    with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
        return handle.read()


def changelog_entries(changelog):
    lines = changelog.splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith("## ")]
    ends = starts[1:] + [len(lines)]
    return tuple(
        (lines[start][len("## ") :].split()[0], "\n".join(lines[start:end]))
        for start, end in zip(starts, ends)
    )


class Release(unittest.TestCase):
    def the_template_contract_documents_schema_and_reask(self):
        for relative in ("README.md", os.path.join("skills", "mitosis", "SKILL.md")):
            text = read_text(relative)
            for term in ("--reask-command", "{schema}", "structured_output"):
                self.assertIn(term, text, relative)
        size = os.path.getsize(os.path.join(ROOT, "skills", "mitosis", "SKILL.md"))
        self.assertLessEqual(size, SKILL_LIMIT)

    def the_release_carries_the_handoff_checks(self):
        core = module("core")
        version = core.__version__
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as handle:
            plugin = json.load(handle)
        self.assertEqual(version, plugin["version"])
        self.assertGreater(tuple(int(part) for part in version.split(".")), RELEASED)
        entries = changelog_entries(read_text("CHANGELOG.md"))
        self.assertEqual(entries[0][0], version)
        handoff_release = dict(entries)[HANDOFF_RELEASE]
        for term in NAMED_IN_CHANGELOG:
            self.assertIn(term, handoff_release, term)


class Loader(unittest.TestLoader):
    def getTestCaseNames(self, case):
        return sorted(
            name
            for name in vars(case)
            if not name.startswith("_") and callable(getattr(case, name))
        )


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    suite.addTests(Loader().loadTestsFromTestCase(Release))
    return suite


if __name__ == "__main__":
    unittest.main()
