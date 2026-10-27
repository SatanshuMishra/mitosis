import contextlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

ISOLATED_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "mitosis test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "mitosis test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "HOME": tempfile.gettempdir(),
}

UNSET_FOR_RUNS = ("PLUGIN_METRICS_PARENT", "CLAUDE_CODE_SESSION_ID")

STRUCTURER = """
import os
import sys
import tempfile

here = os.path.dirname(os.path.abspath(__file__))
sys.stdin.read()
handle, _ = tempfile.mkstemp(dir=os.path.join(here, "sessions"), prefix="structure-")
os.write(handle, sys.argv[-1].encode("utf-8"))
os.close(handle)
with open(os.path.join(here, "structure-reply.txt"), encoding="utf-8") as reply:
    print(reply.read().strip())
"""

BRIEFER = """
import json
import os
import sys
import tempfile

here = os.path.dirname(os.path.abspath(__file__))
step = sys.argv[1]
sys.stdin.read()
handle, _ = tempfile.mkstemp(dir=os.path.join(here, "sessions"), prefix="brief-%s-" % step)
os.write(handle, sys.argv[-1].encode("utf-8"))
os.close(handle)
marker = os.path.join(here, "fail-once-%s" % step)
if os.path.isfile(marker):
    os.remove(marker)
    sys.exit(2)
print(json.dumps({"name": step, "task": "build " + step}))
"""

WORKER = r'''
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
prefix = "Write-set for this Lane, the only files you may edit: "
paths = [
    path.strip()
    for line in sys.argv[1].splitlines()
    if line.startswith(prefix)
    for path in line[len(prefix):].split(",")
    if path.strip()
]
for path in paths:
    with open(path, "a") as handle:
        handle.write("work\n")
if len(sys.argv) > 3:
    with open(os.path.join(here, "lane-%s.txt" % sys.argv[2]), "w", encoding="utf-8") as handle:
        handle.write(sys.argv[3])
print(json.dumps({"item": "lane", "status": "ok", "files_changed": paths, "notes": "done"}))
'''


def write(root, relative, content):
    path = os.path.join(root, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def command(root, script_name, script, *placeholders):
    path = write(root, os.path.join("worker", script_name), script)
    return " ".join((shlex.quote(sys.executable), shlex.quote(path)) + placeholders)


def bare(name, **extra):
    return {"name": name, "files": [name + ".py"], "source": None, "acceptance": [], **extra}


def briefed(name, **extra):
    return bare(name, task="do " + name, **extra)


def structure_reply(*items):
    return {"items": list(items), "assumptions": [], "constraints": []}


def git_repo(root):
    env = {**os.environ, **ISOLATED_GIT_ENV}
    for args in (
        ["git", "init", "-q", root],
        ["git", "-C", root, "commit", "-q", "--allow-empty", "-m", "root"],
        ["git", "-C", root, "branch", "feature"],
    ):
        subprocess.run(args, check=True, capture_output=True, env=env)


@contextlib.contextmanager
def isolated_run():
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as store:
        os.makedirs(os.path.join(home, ".claude", "projects"))
        patched = {**ISOLATED_GIT_ENV, "HOME": home, "PLUGIN_METRICS_DIR": store}
        with unittest.mock.patch.dict(os.environ, patched):
            for name in UNSET_FOR_RUNS:
                os.environ.pop(name, None)
            yield


def invoke(root, argv):
    import mitosis

    before = os.getcwd()
    os.chdir(root)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = mitosis.main(argv)
            except SystemExit as stop:
                code = stop.code
    finally:
        os.chdir(before)
    return code, out.getvalue(), err.getvalue()


def build(root, elsewhere, items, worker_template):
    git_repo(root)
    write(root, "items.json", json.dumps(items))
    run_dir = os.path.join(elsewhere, "run")
    code, out, err = invoke(
        root,
        [
            "--items",
            "items.json",
            "--run-dir",
            run_dir,
            "--feature-branch",
            "feature",
            "--dispatch-command",
            worker_template,
            "--acceptance-command",
            "probe {file} {test}",
            "--timeout",
            "30",
            "--no-push",
        ],
    )
    return code, out, err, read_json(os.path.join(run_dir, "state.json"))


def recorded_sessions(root):
    folder = os.path.join(root, "worker", "sessions")
    found = []
    for name in sorted(os.listdir(folder)):
        with open(os.path.join(folder, name), encoding="utf-8") as handle:
            found.append((name.rsplit("-", 1)[0], handle.read()))
    return found


class DispatchSessionId(unittest.TestCase):
    def test_dispatch_fills_a_fresh_session_id(self):
        import mitosis

        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as elsewhere:
            template = command(elsewhere, "worker.py", WORKER, "{task}", "{lane}", "{session_id}")
            with isolated_run():
                code, out, err, state = build(
                    root, elsewhere, [briefed("alpha"), briefed("beta")], template
                )
            self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
            given = {}
            for lane in ("0", "1"):
                with open(
                    os.path.join(elsewhere, "worker", "lane-%s.txt" % lane), encoding="utf-8"
                ) as handle:
                    given = {**given, lane: handle.read()}
            self.assertNotEqual(given["0"], given["1"])
            for lane, value in given.items():
                self.assertRegex(value, UUID4)
                self.assertEqual(state["lanes"][lane]["session_id"], value)

    def test_session_id_null_without_placeholder(self):
        import mitosis

        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as elsewhere:
            template = command(elsewhere, "worker.py", WORKER, "{task}")
            with isolated_run():
                code, out, err, state = build(root, elsewhere, [briefed("alpha")], template)
            self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
            self.assertEqual(state["lanes"]["0"]["state"], "ok")
            self.assertIn("session_id", state["lanes"]["0"])
            self.assertIsNone(state["lanes"]["0"]["session_id"])


class SpawnAttemptSessionId(unittest.TestCase):
    def test_every_spawn_attempt_gets_its_own_id(self):
        import mitosis

        with tempfile.TemporaryDirectory() as root:
            write(root, "docs/spec.md", "# One\n\nalpha\n\n# Two\n\nbeta\n")
            write(root, "worker/structure-reply.txt", json.dumps(
                structure_reply(bare("alpha"), bare("beta"))
            ))
            write(root, "worker/fail-once-alpha", "")
            os.makedirs(os.path.join(root, "worker", "sessions"))
            structurer = command(root, "structurer.py", STRUCTURER, "{document}", "{session_id}")
            briefer = command(root, "briefer.py", BRIEFER, "{step}", "{session_id}")
            run_dir = os.path.join(root, "run")
            with isolated_run():
                code, out, err = invoke(
                    root,
                    [
                        "--spec",
                        "docs/spec.md",
                        "--decompose-command",
                        structurer,
                        "--brief-command",
                        briefer,
                        "--structure-samples",
                        "2",
                        "--timeout",
                        "20",
                        "--plan-only",
                        "--run-dir",
                        run_dir,
                    ],
                )
                self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
                code, out, err = invoke(
                    root,
                    [
                        "--spec",
                        "docs/spec.md",
                        "--resume",
                        "--brief-command",
                        briefer,
                        "--timeout",
                        "20",
                        "--plan-only",
                        "--run-dir",
                        run_dir,
                    ],
                )
            self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
            sessions = recorded_sessions(root)
            kinds = sorted(kind for kind, _ in sessions)
            self.assertEqual(
                kinds, ["brief-alpha", "brief-alpha", "brief-beta", "structure", "structure"]
            )
            values = [value for _, value in sessions]
            for value in values:
                self.assertRegex(value, UUID4)
            self.assertEqual(len(set(values)), len(values))


class SessionPlaceholderOffered(unittest.TestCase):
    def test_session_id_placeholder_offered(self):
        import run

        run.check_template("dispatch", "agent {session_id}")
        with self.assertRaises(run.ConfigError) as caught:
            run.check_template("acceptance", "runner {file} {session_id}")
        self.assertIn("{session_id}", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
