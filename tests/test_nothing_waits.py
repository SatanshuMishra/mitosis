import ast
import glob
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import test_per_msp_lock_run as scaffold

DISPATCHED = ("build-a", "build-b", "check-author-alpha", "check-author-beta")
LIMIT_NAMES = (
    "GIT_TIMEOUT",
    "KILL_GRACE",
    "SEED_TIMEOUT",
    "REAP_LIMIT",
    "LingeringProcess",
    "EXIT_LINGERING",
)
STARTERS = ("run", "Popen", "check_output", "check_call")
CLOCKS = ("monotonic", "time", "perf_counter", "monotonic_ns", "time_ns")

STRUCTURER = r'''
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.stdin.read()
with open(os.path.join(here, "structure-reply.txt"), encoding="utf-8") as handle:
    reply = json.load(handle)
print(json.dumps({"type": "result", "session_id": sys.argv[-2], "result": "done", "structured_output": {"reply": reply}}))
'''

BRIEFER = r'''
import json
import sys

sys.stdin.read()
reply = {"name": sys.argv[1], "task": "build " + sys.argv[1]}
print(json.dumps({"type": "result", "session_id": sys.argv[-2], "result": "done", "structured_output": reply}))
'''

RENDER = r'''
import json
import os

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "render-calls"), "a", encoding="utf-8") as handle:
    handle.write("render\n")
print(json.dumps({"title": "feat(ship): fake title", "body": "## Problem\nBodies are composed twice.\n\n## Change\n- Hand facts to render.\n\n## Impact\nEvery new body changes shape.\n\n## Testing\n| Check | Result |\n|---|---|\n| tests/test_nothing_waits.py | passed |"}))
'''


def sources():
    found = sorted(glob.glob(os.path.join(ROOT, "*.py"))) + [
        os.path.join(ROOT, "scripts", "release_check.py")
    ]
    parsed = ()
    for path in found:
        with open(path, encoding="utf-8") as handle:
            parsed = parsed + ((os.path.relpath(path, ROOT), ast.parse(handle.read(), path)),)
    return parsed


def attribute_name(node):
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def is_subprocess_attribute(node, names):
    return (
        isinstance(node, ast.Attribute)
        and node.attr in names
        and isinstance(node.value, ast.Name)
        and node.value.id == "subprocess"
    )


def imported_starters(tree):
    return frozenset(
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess"
        for alias in node.names
        if alias.name in STARTERS
    )


def starts_process(call, imported):
    return is_subprocess_attribute(call.func, STARTERS) or (
        isinstance(call.func, ast.Name) and call.func.id in imported
    )


def keyword(call, name):
    found = [entry.value for entry in call.keywords if entry.arg == name]
    return found[0] if found else None


def stdin_choices(value):
    if isinstance(value, ast.IfExp):
        return stdin_choices(value.body) + stdin_choices(value.orelse)
    if is_subprocess_attribute(value, ("DEVNULL",)):
        return ("DEVNULL",)
    if is_subprocess_attribute(value, ("PIPE",)):
        return ("PIPE",)
    return ("other",)


def writes_input(scope):
    return any(
        isinstance(node, ast.Call)
        and attribute_name(node.func) == "communicate"
        and (node.args or keyword(node, "input") is not None)
        for node in ast.walk(scope)
    )


def scopes(tree):
    return [tree] + [
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def own_calls(scope):
    pending = list(ast.iter_child_nodes(scope))
    found = ()
    while pending:
        node = pending.pop(0)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Call):
            found = found + (node,)
        pending.extend(ast.iter_child_nodes(node))
    return found


def input_faults(path, tree):
    imported = imported_starters(tree)
    faults = ()
    for scope in scopes(tree):
        for call in own_calls(scope):
            if not starts_process(call, imported):
                continue
            if any(entry.arg is None for entry in call.keywords):
                faults = faults + ("%s:%d passes keywords it unpacks" % (path, call.lineno),)
                continue
            value = keyword(call, "stdin")
            if value is None:
                if keyword(call, "input") is None:
                    faults = faults + ("%s:%d sets no stdin" % (path, call.lineno),)
                continue
            choices = stdin_choices(value)
            if "other" in choices:
                faults = faults + ("%s:%d passes stdin other than DEVNULL or PIPE" % (path, call.lineno),)
            elif "PIPE" in choices and not writes_input(scope):
                faults = faults + (
                    "%s:%d passes stdin as PIPE with no input written in its function" % (path, call.lineno),
                )
    return faults


def limit_faults(path, tree):
    faults = ()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called = attribute_name(node.func)
            if keyword(node, "timeout") is not None:
                faults = faults + ("%s:%d passes timeout" % (path, node.lineno),)
            if called == "communicate" and len(node.args) > 1:
                faults = faults + ("%s:%d communicate passes a timeout" % (path, node.lineno),)
            if called == "wait" and isinstance(node.func, ast.Attribute) and node.args:
                faults = faults + ("%s:%d wait passes a timeout" % (path, node.lineno),)
            if called == "wait" and isinstance(node.func, ast.Name) and len(node.args) > 1:
                faults = faults + ("%s:%d wait passes a timeout" % (path, node.lineno),)
        defined = ()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined = (node.name,)
        elif isinstance(node, ast.Assign):
            defined = tuple(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined = (node.target.id,)
        faults = faults + tuple(
            "%s:%d defines %s" % (path, node.lineno, name) for name in defined if name in LIMIT_NAMES
        )
    return faults


def reads_clock(node):
    return any(
        isinstance(inner, ast.Call)
        and (
            (
                isinstance(inner.func, ast.Attribute)
                and inner.func.attr in CLOCKS
                and isinstance(inner.func.value, ast.Name)
                and inner.func.value.id == "time"
            )
            or (isinstance(inner.func, ast.Name) and inner.func.id in ("monotonic", "perf_counter"))
        )
        for inner in ast.walk(node)
    )


def names_deadline(node):
    return any(
        (isinstance(inner, ast.Name) and ("deadline" in inner.id.lower() or inner.id == "REAP_LIMIT"))
        or (isinstance(inner, ast.Attribute) and ("deadline" in inner.attr.lower() or inner.attr == "REAP_LIMIT"))
        for inner in ast.walk(node)
    )


def put(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def stage_env(home):
    return {
        **scaffold.kept_environment(),
        **scaffold.GIT_IDENTITY,
        "HOME": home,
        "PLUGIN_METRICS_DIR": os.path.join(home, "metrics"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


class NothingWaits(unittest.TestCase):
    def _scratch(self):
        scratch = scaffold.Scratch()
        self.addCleanup(scratch.close)
        return scratch

    def _committed(self, summary, completed):
        for label in ("alpha", "beta"):
            found = scaffold.msp_summary(self, summary, label, completed)
            self.assertEqual(found.get("state"), "committed", "MSP %s state" % label + scaffold.tail(completed))

    def _recorded(self, env=None, configure=None):
        scratch = self._scratch()
        if configure is not None:
            configure(scratch)
        scratch.jobs(scaffold.standard_jobs())
        completed = scratch.build(scratch.items(scaffold.labelled()), "--no-push", env=env)
        records = scratch.records()
        self.assertEqual(sorted(records), sorted(DISPATCHED), "recorded dispatches" + scaffold.tail(completed))
        return records

    def _stage(self, root, *argv):
        home = os.path.join(root, "home")
        os.makedirs(home, exist_ok=True)
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "mitosis.py"), *argv],
            cwd=root,
            env=stage_env(home),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=scaffold.RUN_LIMIT,
        )

    def _folder(self):
        folder = os.path.realpath(tempfile.mkdtemp(prefix="mitosis-stage-"))
        self.addCleanup(shutil.rmtree, folder, True)
        return folder

    def _command(self, root, name, script, *words):
        path = put(os.path.join(root, "worker", name), script)
        return " ".join([shlex.quote(sys.executable), shlex.quote(path)] + list(words))

    def test_a_run_without_timeout_starts(self):
        scratch = self._scratch()
        scratch.jobs(scaffold.standard_jobs())
        completed = scratch.build(scratch.items(scaffold.labelled()), "--no-push")
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + scaffold.tail(completed))
        self._committed(scaffold.summary_of(completed), completed)

    def test_timeout_is_ignored_with_a_notice(self):
        scratch = self._scratch()
        scratch.jobs(
            {
                name: {**job, "sleep": 3}
                for name, job in scaffold.standard_jobs().items()
            }
        )
        completed = scratch.build(scratch.items(scaffold.labelled()), "--timeout", "1", "--no-push")
        self.assertEqual(completed.returncode, 0, "mitosis exit code" + scaffold.tail(completed))
        self._committed(scaffold.summary_of(completed), completed)
        notices = [line for line in completed.stderr.splitlines() if "deprecated and ignored" in line]
        self.assertEqual(len(notices), 1, "deprecation lines" + scaffold.tail(completed))
        self.assertIn("--timeout", notices[0])

    def test_every_stage_starts_without_timeout(self):
        with self.subTest(stage="structure"):
            root = self._folder()
            put(os.path.join(root, "docs", "spec.md"), "# One\n\nbody\n")
            reply = {
                "items": [{"name": "alpha", "files": ["alpha.py"], "source": None, "acceptance": []}],
                "assumptions": [],
                "constraints": [],
            }
            put(os.path.join(root, "worker", "structure-reply.txt"), json.dumps(reply))
            completed = self._stage(
                root,
                "--spec",
                "docs/spec.md",
                "--decompose-command",
                self._command(root, "structurer.py", STRUCTURER, "{document}", "{session_id}", "{schema}"),
                "--reask-command",
                scaffold.REASK_COMMAND,
                "--brief-command",
                self._command(root, "briefer.py", BRIEFER, "{step}", "{document}", "{session_id}", "{schema}"),
                "--reask-command",
                scaffold.REASK_COMMAND,
                "--plan-only",
                "--run-dir",
                os.path.join(root, "run"),
            )
            self.assertEqual(completed.returncode, 0, scaffold.tail(completed))
            self.assertNotIn("--timeout", completed.stderr)
            self.assertTrue(os.path.isfile(os.path.join(root, "run", "structure.json")))
        with self.subTest(stage="brief"):
            root = self._folder()
            put(os.path.join(root, "docs", "spec.md"), "# One\n\nbody\n")
            put(
                os.path.join(root, "run", "structure.json"),
                json.dumps(
                    [
                        {"name": "alpha", "files": ["alpha.py"], "source": None, "acceptance": [], "task": "do alpha"},
                        {"name": "beta", "files": ["beta.py"], "source": None, "acceptance": []},
                    ]
                ),
            )
            completed = self._stage(
                root,
                "--spec",
                "docs/spec.md",
                "--resume",
                "--brief-command",
                self._command(root, "briefer.py", BRIEFER, "{step}", "{document}", "{session_id}", "{schema}"),
                "--reask-command",
                scaffold.REASK_COMMAND,
                "--plan-only",
                "--run-dir",
                os.path.join(root, "run"),
            )
            self.assertEqual(completed.returncode, 0, scaffold.tail(completed))
            self.assertNotIn("--timeout", completed.stderr)
            with open(os.path.join(root, "run", "structure.json"), encoding="utf-8") as handle:
                self.assertEqual([step.get("task") for step in json.load(handle)], ["do alpha", "build beta"])
        with self.subTest(stage="render-probe"):
            root = self._folder()
            env = stage_env(os.path.join(root, "home"))
            for argv in (
                ["git", "init", "-q", root],
                ["git", "-C", root, "commit", "-q", "--allow-empty", "-m", "root"],
                ["git", "-C", root, "branch", "feature"],
            ):
                subprocess.run(argv, check=True, capture_output=True, env=env, stdin=subprocess.DEVNULL, timeout=scaffold.CALL_LIMIT)
            step = {
                "name": "alpha",
                "task": "Do alpha.",
                "files": ["alpha.py", "alpha_check.txt"],
                "acceptance": [{"file": "alpha_check.txt", "test": "property"}],
            }
            put(os.path.join(root, "items.json"), json.dumps(scaffold.owned_steps([step], root)))
            render = put(os.path.join(root, "worker", "pull-request"), "#!%s\n%s" % (sys.executable, RENDER))
            os.chmod(render, 0o755)
            completed = self._stage(
                root,
                "--items",
                "items.json",
                "--run-dir",
                os.path.join(root, "run"),
                "--feature-branch",
                "feature",
                "--dispatch-command",
                self._command(root, "spawn.py", "print('spawned')\n", "{task}", "{session_id}", "{schema}"),
                "--reask-command",
                scaffold.REASK_COMMAND,
                "--acceptance-command",
                self._command(root, "accept.py", "import sys\nsys.exit(1)\n", "{file}", "{test}"),
                "--plan-only",
                "--pr-type",
                "feat",
                "--pr-subject",
                "hand facts to render",
                "--pr-why",
                "Bodies are composed twice.",
                "--pr-impact",
                "Every new body changes shape.",
                "--render-command",
                render,
            )
            self.assertEqual(completed.returncode, 0, scaffold.tail(completed))
            self.assertNotIn("--timeout", completed.stderr)
            self.assertTrue(os.path.isfile(os.path.join(root, "worker", "render-calls")))

    def test_the_reaper_reads_no_clock(self):
        path = os.path.join(ROOT, "isolation.py")
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), path)
        found = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "end_run"
        ]
        self.assertEqual(len(found), 1, "isolation.py defines no single end_run")
        reaper = found[0]
        self.assertFalse(reads_clock(reaper), "end_run reads the clock")
        comparisons = [
            node.lineno
            for node in ast.walk(reaper)
            if isinstance(node, ast.Compare) and (names_deadline(node) or reads_clock(node))
        ]
        self.assertEqual(comparisons, [], "end_run compares against a deadline")
        self.assertFalse(names_deadline(reaper), "end_run holds a deadline")

    def test_no_module_holds_a_limit_in_seconds(self):
        faults = tuple(fault for path, tree in sources() for fault in limit_faults(path, tree))
        self.assertEqual(faults, ())

    def test_workers_get_no_input_and_the_non_interactive_environment(self):
        records = self._recorded()
        for name, record in sorted(records.items()):
            with self.subTest(dispatch=name):
                env = record.get("env") or {}
                self.assertIs(record.get("eof"), True)
                self.assertEqual(env.get("GIT_TERMINAL_PROMPT"), "0")
                self.assertEqual(env.get("GH_PROMPT_DISABLED"), "1")
                self.assertEqual(env.get("GIT_PAGER"), "cat")
                self.assertEqual(env.get("PAGER"), "cat")
                self.assertTrue((env.get("GIT_SSH_COMMAND") or "").endswith(" " + scaffold.SSH_OPTIONS), env)
                for setting in scaffold.HTTP_SETTINGS:
                    self.assertIn(list(setting), record.get("config") or [])

    def test_a_callers_ssh_command_and_git_config_are_kept(self):
        records = self._recorded(
            env={
                "GIT_SSH_COMMAND": "ssh -i key",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.autocrlf",
                "GIT_CONFIG_VALUE_0": "false",
            }
        )
        for name, record in sorted(records.items()):
            with self.subTest(dispatch=name):
                command = (record.get("env") or {}).get("GIT_SSH_COMMAND") or ""
                self.assertTrue(command.startswith("ssh -i key "), command)
                self.assertTrue(command.endswith(" " + scaffold.SSH_OPTIONS), command)
                config = record.get("config") or []
                self.assertEqual(config[:1], [["core.autocrlf", "false"]])
                for setting in scaffold.HTTP_SETTINGS:
                    self.assertIn(list(setting), config[1:])

    def test_a_configured_ssh_command_is_kept(self):
        records = self._recorded(
            configure=lambda scratch: scratch.checked("config", "core.sshCommand", "ssh -i deploy_key")
        )
        for name, record in sorted(records.items()):
            with self.subTest(dispatch=name):
                command = (record.get("env") or {}).get("GIT_SSH_COMMAND") or ""
                self.assertTrue(command.startswith("ssh -i deploy_key "), command)
                self.assertTrue(command.endswith(" " + scaffold.SSH_OPTIONS), command)

    def test_every_subprocess_call_sets_its_input(self):
        faults = tuple(fault for path, tree in sources() for fault in input_faults(path, tree))
        self.assertEqual(faults, ())


if __name__ == "__main__":
    unittest.main()
