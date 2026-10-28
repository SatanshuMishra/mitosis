import ast
import functools
import hashlib
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

GENERATED_BLOCK = re.compile(r"<!-- BEGIN receipts gates.*?<!-- END receipts gates -->", re.S)
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


def _field_names(fields):
    return tuple(
        name
        for key, entry in fields.items()
        for name in (key,)
        + _field_names(entry.get("fields") or {})
        + _field_names((entry.get("items") or {}).get("fields") or {})
    )


def _catalogue_names(path):
    with open(path, encoding="utf-8") as handle:
        catalogue = json.load(handle)
    return (
        tuple(catalogue["events"])
        + tuple(name for entry in catalogue["events"].values() for name in _field_names(entry["fields"]))
        + _field_names(catalogue["summary"]["fields"])
    )


@functools.lru_cache(maxsize=None)
def vocabulary_words():
    import metrics_writer

    return frozenset(
        core.PLAN_KEYS
        + core.ITEM_FIELDS
        + core.LANE_STATES
        + core.MSP_STATES
        + core.GATE_OUTCOMES
        + shape.SCALAR_KEYS
        + shape.FINDING_KINDS
        + _public_callables(core, shape, briefs, decompose, mitosis)
        + metrics_writer.ENVELOPE
        + metrics_writer.TOKEN_KINDS
        + _catalogue_names(os.path.join(ROOT, "catalogue.json"))
    )

FORMAT_DOCUMENT = "docs/metrics-format.md"

REFERENCE_SOURCE = os.path.join(ROOT, "tests", "fixtures", "metrics", "source.json")


def _recorded_sha256(path):
    with open(REFERENCE_SOURCE, encoding="utf-8") as handle:
        files = json.load(handle)["files"]
    return next(entry["sha256"] for entry in files if entry["path"] == path)


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

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

METRICS_SKILL_PATH = os.path.join(ROOT, "skills", "metrics", "SKILL.md")

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


def _named_identifiers(text):
    return [
        token
        for line in FENCE.sub("", text).splitlines()
        for has_trigger_word in (any(word in line.lower() for word in TRIGGER_WORDS),)
        for token in INLINE_CODE.findall(line)
        if _looks_like_identifier(token, has_trigger_word)
    ]


def _unknown_identifiers(text, accepted=frozenset()):
    return [
        token
        for token in _named_identifiers(text)
        if token not in accepted
        and (token not in mitosis.flag_names() if FLAG_SHAPE.match(token) else token not in vocabulary_words())
    ]


def _own_identifiers(text):
    if _sha256(text) == _recorded_sha256(FORMAT_DOCUMENT):
        return frozenset(_named_identifiers(text))
    return frozenset()


def _linted_identifiers(text):
    return _unknown_identifiers(GENERATED_BLOCK.sub("", text), _own_identifiers(text))


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
            with open(path, encoding="utf-8", newline="") as handle:
                text = handle.read()
            unknown = _linted_identifiers(text)
            if unknown:
                offenses[os.path.relpath(path, ROOT)] = unknown
        self.assertEqual(offenses, {})

    def a_block_another_tool_generates_is_not_linted_but_the_rest_of_its_file_is(self):
        generated = (
            "<!-- BEGIN receipts gates (generated) -->\nuse `--no-such-flag` here\n<!-- END receipts gates -->\n"
        )
        self.assertEqual(_linted_identifiers(generated), [])
        self.assertEqual(_linted_identifiers(generated + "then `--no-such-flag` again\n"), ["--no-such-flag"])

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

    def the_skill_and_changelog_describe_push_only_shipping(self):
        flags = ("--pr-type", "--pr-scope", "--pr-subject", "--pr-why")
        skill = _skill_text(self)
        for name in flags + ("pushed",):
            self.assertIn("`%s`" % name, skill)
        readme_path = os.path.join(ROOT, "README.md")
        if not os.path.isfile(readme_path):
            self.skipTest("an installed copy carries no README")
        with open(readme_path, encoding="utf-8") as handle:
            readme = handle.read()
        readme_examples = FENCE.findall(readme)
        runs = [block for block in FENCE.findall(skill) + readme_examples if "--dispatch-command" in block]
        self.assertGreaterEqual(len(runs), 3)
        for block in runs:
            for flag in ("--pr-type", "--pr-subject", "--pr-why"):
                self.assertIn(flag, block)
        self.assertTrue(
            any("--pr-type" in block and "--pr-command" not in block for block in readme_examples)
        )
        self.assertGreaterEqual(tuple(int(part) for part in core.__version__.split(".")), (0, 3, 0))
        path = _changelog_path(ROOT)
        self.assertIsNotNone(path, "the plugin carries no changelog")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("\n## 0.3.0", text)
        section = _changelog_head(text[text.index("\n## 0.3.0") + 1 :])
        self.assertTrue(section.startswith("## 0.3.0"))
        for name in flags + ("pushed",):
            self.assertIn(name, section)

    def test_documents_proven_head(self):
        _in_plugin(self)
        changelog = _changelog_path(ROOT)
        self.assertIsNotNone(changelog, "the plugin carries no changelog")
        for path in (os.path.join(ROOT, "README.md"), SKILL_PATH, changelog):
            with open(path, encoding="utf-8") as handle:
                self.assertIn("--proven-head", handle.read(), os.path.relpath(path, ROOT))

    def version_is_0_8_0(self):
        plugin, _ = _manifests(self)
        self.assertEqual(core.__version__, "0.8.0")
        self.assertEqual(plugin.get("version"), "0.8.0")
        with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        self.assertTrue(any(line.startswith("## 0.8.0") for line in lines))

    def documents_the_render_hand_off(self):
        _in_plugin(self)
        for path in (os.path.join(ROOT, "README.md"), SKILL_PATH):
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            for phrase in (
                "pull-request render --fields",
                "0.3.0",
                "--pr-impact",
                "--render-command",
                "<type>(<scope>): <step>",
            ):
                self.assertIn(phrase, text, os.path.relpath(path, ROOT))
            for phrase in ("house format", "What changed", "Verified:", "Not verified:", "land Lane"):
                self.assertNotIn(phrase, text, os.path.relpath(path, ROOT))

    def test_documents_metrics(self):
        _in_plugin(self)
        for path in (os.path.join(ROOT, "README.md"), SKILL_PATH):
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            for name in ("~/.plugin-metrics", "PLUGIN_METRICS_DIR", "PLUGIN_METRICS_PARENT", "{session_id}"):
                self.assertIn(name, text, os.path.relpath(path, ROOT))
        with open(METRICS_SKILL_PATH, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(_frontmatter(text).get("name"), "metrics")
        for name in ("catalogue.json", "plugin=mitosis", "duckdb", "brew install duckdb"):
            self.assertIn(name, text)

    def test_changed_format_copy_loses_its_vocabulary(self):
        with open(os.path.join(ROOT, FORMAT_DOCUMENT), encoding="utf-8", newline="") as handle:
            original = handle.read()
        own = sorted(
            set(_named_identifiers(original))
            - vocabulary_words()
            - set(mitosis.flag_names())
        )
        self.assertTrue(own, "the format document names no identifier of its own")
        with tempfile.TemporaryDirectory() as folder:
            copy = os.path.join(folder, "metrics-format.md")
            changed = original[:2] + original[2].swapcase() + original[3:]
            for text, expected in ((original, []), (changed, own)):
                with open(copy, "w", encoding="utf-8", newline="") as handle:
                    handle.write(text)
                with open(copy, encoding="utf-8", newline="") as handle:
                    self.assertEqual(sorted(set(_linted_identifiers(handle.read()))), expected)

    def test_format_document_is_the_reference_copy(self):
        with open(os.path.join(ROOT, FORMAT_DOCUMENT), "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(digest, _recorded_sha256(FORMAT_DOCUMENT))

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
