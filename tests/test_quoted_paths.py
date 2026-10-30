import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import run
import test_gate_teeth as gate_fixtures
import test_referee_lock as lock_fixtures

QUOTED_NAMES = ("café.py", "my calc.py", 'say "hi".py', " lead.py")
LOCKED_ACCENT = "tests/test_café.py"
LOCKED_ACCENT_CHARTER = "# Charter\n\n- Python 3.9 or later.\n- Locked: %s\n" % LOCKED_ACCENT
DATA_NAMES = ("my limits.json", "limités.json")

NAME_LISTINGS = frozenset(
    ("--name-only", "--name-status", "ls-files", "ls-tree", "diff-tree", "--raw", "--numstat", "--stat")
)
GIT_CALLS = frozenset(("git", "git_run", "git_ok", "git_paths", "run", "check_output", "Popen"))
PATCH_HEADER = re.compile(r"(\\\+|\+){3} |(\\-|-){3} |diff --git")
SKIPPED_DIRS = frozenset((".git", "tests", "__pycache__"))


def _called_name(func):
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _literal_words(call):
    words = ()
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            words = words + (arg.value,)
        if isinstance(arg, (ast.List, ast.Tuple)):
            words = words + tuple(
                element.value
                for element in arg.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            )
    return frozenset(words)


def _parents(tree):
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _read_for_truth_only(call, parents):
    if _called_name(call.func) == "git_ok":
        return True
    node = call
    parent = parents.get(node)
    while isinstance(parent, ast.Attribute) or (isinstance(parent, ast.Call) and parent.func is node):
        node = parent
        parent = parents.get(node)
    if isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.Not):
        return True
    if isinstance(parent, ast.Call) and _called_name(parent.func) == "bool":
        return True
    return isinstance(parent, (ast.If, ast.IfExp, ast.While)) and parent.test is node


def quoted_name_reads(source, label):
    tree = ast.parse(source)
    parents = _parents(tree)
    found = ()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _called_name(node.func) in GIT_CALLS:
            words = _literal_words(node)
            listings = NAME_LISTINGS | ({"--porcelain"} if "status" in words else frozenset())
            if words & listings and "-z" not in words and not _read_for_truth_only(node, parents):
                found = found + (
                    "%s:%d git name listing read without -z: %s"
                    % (label, node.lineno, sorted(words & listings)),
                )
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and PATCH_HEADER.search(node.value):
            found = found + ("%s:%d path read from a patch header: %r" % (label, node.lineno, node.value),)
    return found


def source_files(root):
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name not in SKIPPED_DIRS)
        for name in sorted(files):
            if name.endswith(".py"):
                path = os.path.join(directory, name)
                yield path, os.path.relpath(path, root)


def git_text(args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, timeout=60
    ).stdout.decode("utf-8")


def write_text(path, content):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


class QuotedPaths(unittest.TestCase):
    def setUp(self):
        self.scratch = None
        self.tmp = tempfile.mkdtemp(prefix="mitosis-quoted-")
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        patcher = mock.patch.dict(os.environ, {**gate_fixtures.GIT_IDENTITY, "HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _repo(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        git_text(["init", "-q", "-b", "main"], repo)
        write_text(os.path.join(repo, "README.md"), "seed\n")
        git_text(["add", "-A"], repo)
        git_text(["commit", "-q", "-m", "seed"], repo)
        git_text(["checkout", "-q", "-b", "work"], repo)
        for name in QUOTED_NAMES:
            write_text(os.path.join(repo, name), "VALUE = 1\n")
        git_text(["add", "-A"], repo)
        git_text(["commit", "-q", "-m", "add quoted names"], repo)
        return repo

    def git_path_lists_carry_names_as_written(self):
        repo = self._repo()
        self.assertEqual(run.changed_files(repo, "work", "main"), sorted(QUOTED_NAMES))
        self.assertEqual(
            run.referee_writes({"locked": ["café.py"]}, repo, "work", "main"), ["café.py"]
        )
        write_text(os.path.join(repo, "café.py"), "VALUE = 2\n")
        head = run.commit_paths(repo, ["café.py"], "change café")
        self.assertEqual(head, git_text(["rev-parse", "HEAD"], repo).strip())
        self.assertEqual(git_text(["log", "-1", "--format=%s"], repo), "change café\n")
        self.assertEqual(git_text(["show", "HEAD:café.py"], repo), "VALUE = 2\n")

    def a_worker_write_to_a_locked_accented_path_is_reverted_and_fatal(self):
        self.scratch = lock_fixtures.Scratch({LOCKED_ACCENT: lock_fixtures.LOCKED_TEXT})
        charter = self.scratch.file("charter.md", LOCKED_ACCENT_CHARTER)
        items = self.scratch.items([lock_fixtures.step("build", ["calc.py"], "demo")])
        self.scratch.jobs(
            {
                "demo": {
                    "step": "build",
                    "writes": {
                        "calc.py": "def add(a, b):\n    return a + b\n",
                        LOCKED_ACCENT: "import unittest\nraise SystemExit(0)\n",
                    },
                    "declared": ["calc.py"],
                }
            }
        )
        completed = self.scratch.build(items, "--charter", charter)
        self.assertEqual(completed.returncode, 9, "mitosis exit code" + lock_fixtures.tail(completed))
        summary = lock_fixtures.summary_of(completed)
        self.assertTrue(summary.get("msps"), "the JSON summary lists no MSP")
        msp = summary["msps"][0]
        self.assertEqual(msp["state"], "referee-reverted")
        reconcile = msp.get("reconcile") or {}
        self.assertEqual(reconcile.get("referee"), [LOCKED_ACCENT])
        self.assertIs(reconcile.get("fatal"), True)
        self.assertEqual(
            self.scratch.checked("show", "mitosis/demo:" + LOCKED_ACCENT), lock_fixtures.LOCKED_TEXT
        )

    def a_data_file_named_with_a_space_or_accent_is_judged_by_drop_added_lines(self):
        for name in DATA_NAMES:
            with self.subTest(name=name):
                self.scratch = gate_fixtures.Scratch()
                try:
                    test = gate_fixtures.LIMITS_TEST.replace('"limits.json"', json.dumps(name))
                    items = self.scratch.items([gate_fixtures.add_step((name, "tests/test_calc.py"))])
                    self.scratch.jobs(
                        {
                            "demo": {
                                "step": "add",
                                "writes": {name: "{\"cap\": 3}\n", "tests/test_calc.py": test},
                            }
                        }
                    )
                    completed = self.scratch.build(items, push=True)
                    self.assertEqual(
                        completed.returncode, 0, "mitosis exit code" + gate_fixtures.tail(completed)
                    )
                    summary = gate_fixtures.summary_of(completed)
                    self.assertTrue(summary.get("msps"), "the JSON summary lists no MSP")
                    properties = (summary["msps"][0].get("gate") or {}).get("properties") or []
                    self.assertTrue(properties, "the MSP's gate object carries no properties")
                    self.assertEqual(properties[0]["outcome"], "pass")
                    self.assertEqual(
                        (properties[0].get("mutants") or {}).get("killed"),
                        {"file": name, "line": 1, "op": "drop added lines"},
                    )
                finally:
                    self.scratch.close()
                    self.scratch = None

    def the_guard_flags_a_listing_without_nul_separators(self):
        source = (
            "def listed(tree):\n"
            "    names = git(['diff', '--name-only', 'a', 'b'], tree)\n"
            "    safe = git_paths(['ls-files', '-z'], tree)\n"
            "    if git(['ls-files', '--error-unmatch', 'x'], tree):\n"
            "        pass\n"
            "    header = '+++ b/'\n"
            "    return names, safe, header\n"
        )
        self.assertEqual(
            quoted_name_reads(source, "sample.py"),
            (
                "sample.py:2 git name listing read without -z: ['--name-only']",
                "sample.py:6 path read from a patch header: '+++ b/'",
            ),
        )

    def no_source_reads_git_file_names_without_nul_separators(self):
        found = ()
        for path, label in source_files(ROOT):
            with open(path, encoding="utf-8") as handle:
                found = found + quoted_name_reads(handle.read(), label)
        self.assertEqual(found, ())


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            QuotedPaths,
            (
                "git_path_lists_carry_names_as_written",
                "a_worker_write_to_a_locked_accented_path_is_reverted_and_fatal",
                "a_data_file_named_with_a_space_or_accent_is_judged_by_drop_added_lines",
                "the_guard_flags_a_listing_without_nul_separators",
                "no_source_reads_git_file_names_without_nul_separators",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
