import ast
import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import briefs
import core
import decompose
import mitosis
import shape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXCLUDED_DIR_NAMES = frozenset(
    (
        "__pycache__",
        "graphify-out",
        "specs",
        "decisions",
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

def _public_callables(*modules):
    return tuple(
        name
        for module in modules
        for name, value in vars(module).items()
        if callable(value) and not name.startswith("_") and getattr(value, "__module__", None) == module.__name__
    )


VOCABULARY_WORDS = frozenset(
    core.PLAN_KEYS
    + core.ITEM_FIELDS
    + core.LANE_STATES
    + core.MSP_STATES
    + core.GATE_OUTCOMES
    + shape.SCALAR_KEYS
    + shape.FINDING_KINDS
    + _public_callables(core, shape, briefs, decompose, mitosis)
)

def _local_imports(module, root=ROOT):
    with open(os.path.join(root, module + ".py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0
    }
    return tuple(sorted(name for name in imported if os.path.isfile(os.path.join(root, name + ".py"))))


def _local_closure(start, root=ROOT):
    reached = frozenset((start,))
    frontier = (start,)
    while frontier:
        fresh = tuple(
            dict.fromkeys(
                name
                for module in frontier
                for name in _local_imports(module, root)
                if name not in reached
            )
        )
        reached = reached | frozenset(fresh)
        frontier = fresh
    return frozenset(name + ".py" for name in reached)


CHANGELOG_CANDIDATES = ("changelog.md", "changelog.rst", "changelog", "history.md")

PLUGIN_DIRECTORY = os.path.join(ROOT, ".claude-plugin")

PLUGIN_MANIFEST = os.path.join(PLUGIN_DIRECTORY, "plugin.json")

MARKETPLACE_MANIFEST = os.path.join(ROOT, ".claude-plugin", "marketplace.json")

SKILL_PATH = os.path.join(ROOT, "skills", "mitosis", "SKILL.md")

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

YAML_UNSAFE_START = frozenset("-?:,[]{}#&*!|>'\"%@`")


def _in_plugin(case):
    if not os.path.isdir(PLUGIN_DIRECTORY):
        case.skipTest("a bare copy of the modules carries no plugin")
    for path in (PLUGIN_MANIFEST, MARKETPLACE_MANIFEST, SKILL_PATH):
        case.assertTrue(os.path.isfile(path), "the plugin is missing %s" % os.path.relpath(path, ROOT))


def _skill_text(case):
    _in_plugin(case)
    with open(SKILL_PATH, encoding="utf-8", newline="") as handle:
        return handle.read()


def _manifests(case):
    _in_plugin(case)
    with open(PLUGIN_MANIFEST, encoding="utf-8") as handle:
        plugin = json.load(handle)
    with open(MARKETPLACE_MANIFEST, encoding="utf-8") as handle:
        marketplace = json.load(handle)
    return plugin, marketplace


def _frontmatter(text):
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not match:
        return {}
    pairs = (line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
    return {key.strip(): value.strip() for key, value in pairs}


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
                if token not in mitosis.flag_names():
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
    if headings:
        return "\n".join(lines[headings[0]:])
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

    def a_document_may_name_a_function_the_code_defines_but_not_one_it_does_not(self):
        self.assertEqual(_unknown_identifiers("the `manifest_gaps` check"), [])
        self.assertEqual(_unknown_identifiers("the `lane_items` check"), [])
        self.assertEqual(_unknown_identifiers("the `manifest_gapz` check"), ["manifest_gapz"])

    def a_corpus_decisions_file_is_not_linted_for_mitosis_identifiers(self):
        directory = os.path.join(ROOT, "docs", "evaluation", "decisions")
        if not os.path.isdir(directory):
            self.skipTest("no decisions file exists yet")
        self.assertTrue(
            [name for name in os.listdir(directory) if name.endswith(".md")],
            "the decisions directory holds no markdown to exclude",
        )
        self.assertEqual(
            [path for path in _markdown_files(ROOT) if os.path.dirname(path) == directory],
            [],
        )

    def every_flag_the_skill_names_exists_in_the_cli(self):
        text = _skill_text(self)
        named = {
            token
            for line in FENCE.sub("", text).splitlines()
            for token in INLINE_CODE.findall(line)
            if FLAG_SHAPE.match(token)
        }
        declared = set(mitosis.flag_names())
        self.assertEqual(sorted(named - declared), [])

    def the_install_section_names_every_module_the_cli_imports(self):
        readme = os.path.join(ROOT, "README.md")
        text = ""
        if os.path.isfile(readme):
            with open(readme, encoding="utf-8") as handle:
                text = handle.read()
        if not text.startswith("# mitosis\n"):
            self.skipTest("no mitosis README here; an installed copy does not carry one")
        self.assertIn("## Install", text)
        install = text.split("## Install", 1)[1].split("\n## ", 1)[0]
        named = set(re.findall(r"`([a-z_]+\.py)`", install))
        self.assertEqual(sorted(named), sorted(_local_closure("mitosis")))

    def the_import_closure_follows_every_import_form(self):
        with tempfile.TemporaryDirectory() as root:
            for name, text in (
                ("main", "from alpha import thing\nif True:\n    import beta\nimport gamma, os\n"),
                ("alpha", "import delta.sub\n"),
                ("beta", ""),
                ("gamma", ""),
                ("delta", ""),
                ("unused", ""),
            ):
                with open(os.path.join(root, name + ".py"), "w", encoding="utf-8") as handle:
                    handle.write(text)
            self.assertEqual(
                _local_closure("main", root),
                frozenset(("main.py", "alpha.py", "beta.py", "gamma.py", "delta.py")),
            )

    def the_skill_file_is_within_its_size_cap(self):
        _in_plugin(self)
        self.assertLessEqual(os.path.getsize(SKILL_PATH), 20480)

    def the_skill_runs_the_installed_copy_and_names_no_placeholder(self):
        text = _skill_text(self)
        commands = re.findall(r"^python3 (?!-m )(\S+)", text, re.M)
        self.assertTrue(commands)
        self.assertEqual(set(commands), {'"${CLAUDE_PLUGIN_ROOT}/mitosis.py"'})
        self.assertNotIn("/path/to", text)

    def the_plugin_the_marketplace_and_the_skill_name_one_thing(self):
        plugin, marketplace = _manifests(self)
        front = _frontmatter(_skill_text(self))
        entries = marketplace["plugins"]
        self.assertEqual([entry["name"] for entry in entries], ["mitosis"])
        self.assertEqual(plugin["name"], "mitosis")
        self.assertEqual(front.get("name"), "mitosis")
        self.assertTrue(front.get("description"))
        self.assertEqual(entries[0]["source"], "./")

    def the_skill_frontmatter_is_plain_yaml(self):
        front = _frontmatter(_skill_text(self))
        self.assertEqual(sorted(front), ["description", "name"])
        for key, value in front.items():
            self.assertNotIn(": ", value, key)
            self.assertNotIn(" #", value, key)
            self.assertNotIn(value[:1], YAML_UNSAFE_START, key)

    def the_plugin_declares_the_version_the_code_reports(self):
        plugin, marketplace = _manifests(self)
        self.assertRegex(core.__version__, SEMVER)
        self.assertEqual(plugin.get("version"), core.__version__)
        self.assertNotIn("version", marketplace["plugins"][0])

    def the_declared_version_matches_the_changelog_head(self):
        path = _changelog_path(ROOT)
        if path is None and not os.path.isdir(PLUGIN_DIRECTORY):
            self.skipTest("a bare copy of the modules carries no changelog")
        self.assertIsNotNone(path, "the plugin carries no changelog")
        with open(path, encoding="utf-8") as handle:
            head = _changelog_head(handle.read())
        heading = re.match(r"^## (\S+)", head)
        self.assertIsNotNone(heading, "the changelog has no release heading")
        self.assertEqual(heading.group(1), core.__version__)


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
