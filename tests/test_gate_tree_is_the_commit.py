import os
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

import core
import run
import test_referee_lock as lock_fixtures

LOCKED = "tests/test_calc.py"
CONFTEST = "tests/conftest.py"
PYTEST_INI = "tests/pytest.ini"
BRANCH = "mitosis/demo"
PLAN = {"locked": [LOCKED]}
SEEDED_CONFTEST = b"x = 1\n"
TAMPER = 'collect_ignore = ["test_calc.py"]\n'
NO_TESTS = "[pytest]\naddopts = -p no:python\n"
STORED_CALC = b"def add(a, b):\n    return a - b\n"
SHOWN_CALC = "def add(a, b):\n    return a + b\n"
CALL_LIMIT = 60


def git_text(args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, timeout=CALL_LIMIT
    ).stdout.decode("utf-8")


def common_dir(tree):
    found = git_text(["rev-parse", "--git-common-dir"], tree).strip()
    return found if os.path.isabs(found) else os.path.join(tree, found)


def disk_bytes(tree, path):
    full = os.path.join(tree, path)
    if not os.path.lexists(full):
        return None
    with open(full, "rb") as handle:
        return handle.read()


def filter_driver(tree, stored, shown):
    folder = os.path.dirname(tree)
    kept = lock_fixtures.put(os.path.join(folder, "stored"), stored)
    smudged = lock_fixtures.put(os.path.join(folder, "shown"), shown)
    git_text(["config", "filter.f.clean", "cat %s" % kept], tree)
    git_text(["config", "filter.f.smudge", "cat %s" % smudged], tree)


def pyvenv_marker_over_a_skip_worktree_conftest(tree):
    lock_fixtures.put(os.path.join(tree, "tests", "pyvenv.cfg"), "home = /usr/bin\n")
    lock_fixtures.put(os.path.join(tree, "tests", ".gitignore"), "new_conftest_dir/\n")
    lock_fixtures.put(os.path.join(tree, CONFTEST), TAMPER)
    git_text(["update-index", "--skip-worktree", CONFTEST], tree)


def pyvenv_marker_over_an_ignored_pytest_ini(tree):
    lock_fixtures.put(os.path.join(tree, "tests", "pyvenv.cfg"), "home = /usr/bin\n")
    lock_fixtures.put(os.path.join(tree, "tests", ".gitignore"), "pytest.ini\npyvenv.cfg\n")
    lock_fixtures.put(os.path.join(tree, PYTEST_INI), NO_TESTS)


def a_folder_the_worker_names_node_modules(tree):
    lock_fixtures.put(os.path.join(tree, "tests", "node_modules", "conftest.py"), TAMPER)
    lock_fixtures.put(os.path.join(tree, "tests", ".gitignore"), "node_modules/\n")


def an_unlistable_tests_folder(tree):
    lock_fixtures.put(os.path.join(tree, PYTEST_INI), NO_TESTS)
    os.chmod(os.path.join(tree, "tests"), 0o311)


def a_filter_driver_through_info_attributes(tree):
    lock_fixtures.put(os.path.join(common_dir(tree), "info", "attributes"), "%s filter=f\n" % CONFTEST)
    filter_driver(tree, SEEDED_CONFTEST.decode("utf-8"), TAMPER)
    lock_fixtures.put(os.path.join(tree, CONFTEST), TAMPER)


def a_filter_driver_through_an_ignored_gitattributes(tree):
    lock_fixtures.put(os.path.join(tree, "tests", ".gitattributes"), "conftest.py filter=f\n")
    lock_fixtures.put(os.path.join(tree, "tests", ".gitignore"), ".gitattributes\n.gitignore\n")
    filter_driver(tree, SEEDED_CONFTEST.decode("utf-8"), TAMPER)
    lock_fixtures.put(os.path.join(tree, CONFTEST), TAMPER)


def a_filter_driver_on_an_implementation_file(tree):
    lock_fixtures.put(os.path.join(common_dir(tree), "info", "attributes"), "calc.py filter=f\n")
    filter_driver(tree, STORED_CALC.decode("utf-8"), SHOWN_CALC)
    lock_fixtures.put(os.path.join(tree, "calc.py"), SHOWN_CALC)


def an_ignored_module_the_committed_code_imports(tree):
    lock_fixtures.put(os.path.join(tree, ".gitignore"), "__pycache__/\nhelper.py\n")
    lock_fixtures.put(os.path.join(tree, "helper.py"), SHOWN_CALC)
    lock_fixtures.put(os.path.join(tree, "calc.py"), "from helper import add\n")


ROUTES = {
    "pyvenv-skip-worktree": (pyvenv_marker_over_a_skip_worktree_conftest, [CONFTEST], CONFTEST, SEEDED_CONFTEST),
    "pyvenv-ignored-ini": (pyvenv_marker_over_an_ignored_pytest_ini, [PYTEST_INI], PYTEST_INI, None),
    "node-modules-name": (
        a_folder_the_worker_names_node_modules,
        ["tests/node_modules/conftest.py"],
        "tests/node_modules/conftest.py",
        None,
    ),
    "unlistable": (an_unlistable_tests_folder, [PYTEST_INI], PYTEST_INI, None),
    "info-attributes": (a_filter_driver_through_info_attributes, [CONFTEST], CONFTEST, SEEDED_CONFTEST),
    "ignored-gitattributes": (
        a_filter_driver_through_an_ignored_gitattributes,
        [CONFTEST],
        CONFTEST,
        SEEDED_CONFTEST,
    ),
    "implementation-filter": (a_filter_driver_on_an_implementation_file, [], "calc.py", STORED_CALC),
    "ignored-module": (an_ignored_module_the_committed_code_imports, [], "helper.py", None),
}


class GateTreeIsTheCommit(unittest.TestCase):
    def setUp(self):
        self.scratch = None
        self.tmp = tempfile.mkdtemp(prefix="mitosis-gate-tree-")
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        patcher = mock.patch.dict(os.environ, {**lock_fixtures.GIT_IDENTITY, "HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        if self.scratch is not None:
            self.scratch.close()
        for folder, dirs, _ in os.walk(self.tmp):
            for name in dirs:
                full = os.path.join(folder, name)
                if not os.path.islink(full):
                    os.chmod(full, 0o755)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tree(self, name, ignored="__pycache__/\n"):
        repo = os.path.join(self.tmp, name, "repo")
        os.makedirs(repo)
        git_text(["init", "-q", "-b", "feature"], repo)
        lock_fixtures.put(os.path.join(repo, LOCKED), "import unittest\n")
        lock_fixtures.put(os.path.join(repo, CONFTEST), SEEDED_CONFTEST.decode("utf-8"))
        lock_fixtures.put(os.path.join(repo, ".gitignore"), ignored)
        git_text(["add", "-A"], repo)
        git_text(["commit", "-q", "-m", "seed"], repo)
        tree = os.path.join(self.tmp, name, "tree")
        git_text(["worktree", "add", "-q", "-b", BRANCH, tree, "feature"], repo)
        return tree

    def _hidden(self, name, route):
        tree = self._tree(name)
        route(tree)
        run.commit_all(tree, "pre-gate")
        return tree, git_text(["rev-parse", "HEAD"], tree).strip()

    def _committed(self, tree, commit, path):
        shown = subprocess.run(
            ["git", "cat-file", "blob", "%s:%s" % (commit, path)],
            cwd=tree,
            capture_output=True,
            timeout=CALL_LIMIT,
        )
        return shown.stdout if shown.returncode == 0 else None

    def _judged_before_the_first_run(self, key):
        route, reported, payload, expected = ROUTES[key]
        tree, commit = self._hidden(key + "-referee", route)
        self.assertEqual(run.referee_writes(PLAN, tree, BRANCH, "feature"), reported)
        self.assertEqual(disk_bytes(tree, payload), expected)
        self.assertEqual(disk_bytes(tree, payload), self._committed(tree, commit, payload))

    def _restored_after_a_run(self, key):
        route, reported, payload, expected = ROUTES[key]
        tree, commit = self._hidden(key + "-restore", route)
        written = run.restore_gate_commit(tree, BRANCH, commit, (LOCKED,), base="feature")
        self.assertEqual(written, tuple(reported))
        self.assertEqual(disk_bytes(tree, payload), expected)
        self.assertEqual(disk_bytes(tree, payload), self._committed(tree, commit, payload))

    def _route(self, key):
        self._judged_before_the_first_run(key)
        self._restored_after_a_run(key)

    def a_worker_pyvenv_cfg_never_hides_a_skip_worktree_conftest(self):
        self._route("pyvenv-skip-worktree")

    def a_worker_pyvenv_cfg_never_hides_an_ignored_pytest_ini(self):
        self._route("pyvenv-ignored-ini")

    def a_folder_the_worker_names_node_modules_is_judged(self):
        self._route("node-modules-name")

    def an_unlistable_folder_is_opened_and_judged(self):
        self._route("unlistable")

    def a_filter_driver_in_info_attributes_never_decides_the_bytes(self):
        self._route("info-attributes")

    def a_filter_driver_in_an_ignored_gitattributes_never_decides_the_bytes(self):
        self._route("ignored-gitattributes")

    def an_implementation_file_is_judged_as_its_stored_bytes(self):
        self._route("implementation-filter")

    def an_ignored_module_the_commit_lacks_is_removed(self):
        self._route("ignored-module")

    def installed_dependencies_the_feature_branch_ignores_are_kept(self):
        tree = self._tree("installed", "__pycache__/\nnode_modules/\n.venv/\n")
        jest = "node_modules/pkg/jest.config.js"
        venv_conftest = ".venv/lib/python3.9/site-packages/pkg/conftest.py"
        lock_fixtures.put(os.path.join(tree, jest), "module.exports = {};\n")
        lock_fixtures.put(os.path.join(tree, ".venv", "pyvenv.cfg"), "home = /usr/bin\n")
        lock_fixtures.put(os.path.join(tree, venv_conftest), "x = 2\n")
        lock_fixtures.put(os.path.join(tree, "calc.py"), SHOWN_CALC)
        commit = run.commit_all(tree, "pre-gate")
        self.assertEqual(run.referee_writes(PLAN, tree, BRANCH, "feature"), [])
        self.assertEqual(run.restore_gate_commit(tree, BRANCH, commit, (LOCKED,), base="feature"), ())
        self.assertEqual(disk_bytes(tree, jest), b"module.exports = {};\n")
        self.assertEqual(disk_bytes(tree, venv_conftest), b"x = 2\n")

    def a_folder_the_gate_cannot_list_holds_the_msp_back(self):
        tree = self._tree("unlistable-held")
        commit = run.commit_all(tree, "pre-gate")
        listed = os.scandir
        tests = os.path.join(tree, "tests")

        def refuse(path="."):
            if isinstance(path, (str, bytes)) and os.path.normpath(os.fsdecode(path)) == tests:
                raise PermissionError(13, "Permission denied")
            return listed(path)

        plan = {**core.plan([lock_fixtures.step("build", ["calc.py"], "demo")]), "locked": [LOCKED]}
        with mock.patch.object(os, "scandir", side_effect=refuse):
            held = run._referee_stage(
                plan,
                0,
                {"path": tree, "branch": BRANCH},
                (),
                [{"path": tree, "branch": BRANCH}],
                {"feature_branch": "feature"},
            )
        reason = (
            "mitosis could not make %s equal to its commit, so the gate did not run and the MSP "
            "is held back: the gate could not make %s equal to %s: [Errno 13] Permission denied"
            % (BRANCH, tree, commit[:12])
        )
        self.assertEqual(
            held,
            {
                "reconcile": None,
                "state": "gate-failed",
                "reason": reason,
                "gate": None,
                "gate_error": reason,
            },
        )

    def a_worker_pyvenv_cfg_never_hides_a_conftest_end_to_end(self):
        self.scratch = lock_fixtures.Scratch()
        charter = self.scratch.file("charter.md", lock_fixtures.PLAIN_CHARTER)
        items = self.scratch.items([lock_fixtures.step("build", ["calc.py"], "demo")])
        self.scratch.jobs(
            {
                "demo": {
                    "step": "build",
                    "writes": {
                        "calc.py": SHOWN_CALC,
                        CONFTEST: TAMPER,
                        ".gitignore": "__pycache__/\n%s\n" % CONFTEST,
                        "tests/pyvenv.cfg": "home = /usr/bin\n",
                    },
                    "declared": ["calc.py"],
                }
            }
        )
        completed = self.scratch.build(items, "--charter", charter)
        self.assertEqual(completed.returncode, 9, "mitosis exit code" + lock_fixtures.tail(completed))
        msp = lock_fixtures.summary_of(completed)["msps"][0]
        self.assertEqual(msp["state"], "referee-reverted")
        self.assertEqual(msp["reconcile"]["referee"], [CONFTEST])
        self.assertIs(msp["reconcile"]["fatal"], True)
        self.assertFalse(
            os.path.lexists(os.path.join(self.scratch.run_dir, "trees", "demo", CONFTEST))
        )


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            GateTreeIsTheCommit,
            (
                "a_worker_pyvenv_cfg_never_hides_a_skip_worktree_conftest",
                "a_worker_pyvenv_cfg_never_hides_an_ignored_pytest_ini",
                "a_folder_the_worker_names_node_modules_is_judged",
                "an_unlistable_folder_is_opened_and_judged",
                "a_filter_driver_in_info_attributes_never_decides_the_bytes",
                "a_filter_driver_in_an_ignored_gitattributes_never_decides_the_bytes",
                "an_implementation_file_is_judged_as_its_stored_bytes",
                "an_ignored_module_the_commit_lacks_is_removed",
                "installed_dependencies_the_feature_branch_ignores_are_kept",
                "a_folder_the_gate_cannot_list_holds_the_msp_back",
                "a_worker_pyvenv_cfg_never_hides_a_conftest_end_to_end",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
