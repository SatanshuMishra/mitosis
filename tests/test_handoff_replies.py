import hashlib
import importlib
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
MITOSIS = os.path.join(ROOT, "mitosis.py")
CALL_LIMIT = 60
RUN_LIMIT = 400
STRIPPED = ("CLAUDE_CODE_SESSION_ID", "PLUGIN_METRICS_PARENT", "SHIP")
GIT_IDENTITY = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "mitosis test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "mitosis test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}

ORIGINAL_CALC = "def add(a, b):\n    return 0\n"
SUM_CALC = "def add(a, b):\n    return a + b\n"

VALID_RETURN = {
    "item": "add-numbers",
    "status": "ok",
    "files_changed": ["src/calc.py"],
    "notes": "done",
}

PROSE_RETURN = (
    'Done.\n{"item": "add-numbers", "status": "ok", "files_changed": ["src/calc.py"], "notes": "done"}'
)

USAGE_LIMIT_LINE = "You've hit your usage limit; it resets at 5pm"

PULL_REQUEST_URL = "https://example.invalid/pull/7"

MODEL = r'''
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
role = os.path.splitext(os.path.basename(os.path.abspath(__file__)))[0]
with open(os.path.join(here, role + ".log"), "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\n")
with open(os.path.join(here, role + ".json"), encoding="utf-8") as handle:
    job = json.load(handle)
for path, content in sorted(job.get("writes", {}).items()):
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)
if job.get("stderr"):
    sys.stderr.write(job["stderr"] + "\n")
    sys.stderr.flush()
if not job.get("silent"):
    printed = {
        "type": "result",
        "session_id": sys.argv[2] if len(sys.argv) > 2 else "",
        "result": job.get("result", "done"),
    }
    if "reply" in job:
        printed["structured_output"] = job["reply"]
    sys.stdout.write(json.dumps(printed) + "\n")
    sys.stdout.flush()
sys.exit(job.get("exit", 0))
'''

PULL_REQUEST = r'''
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "pr.log"), "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\n")
with open(os.path.join(here, "pr.json"), encoding="utf-8") as handle:
    job = json.load(handle)
sys.stdout.write(job["printed"])
sys.stdout.flush()
'''

ACCEPT = r'''
import sys

try:
    with open("src/calc.py", encoding="utf-8", newline="") as handle:
        text = handle.read()
except OSError:
    sys.exit(1)
sys.exit(0 if text == %r else 1)
''' % SUM_CALC

RENDER_BODY = (
    "## Problem\nSums were wrong.\n\n## Change\n- Add numbers.\n\n## Impact\nSums are right.\n\n"
    "## Testing\n| Check | Result |\n|---|---|\n| tests/test_calc.py | passed |"
)

RENDER_REPLY = {"title": "feat(calc): add numbers", "body": RENDER_BODY}

RENDER = r'''
import json
import sys

sys.stdout.write(json.dumps(%r) + "\n")
''' % RENDER_REPLY

PUSH_FLAGS = (
    "--pr-type",
    "feat",
    "--pr-scope",
    "calc",
    "--pr-subject",
    "add numbers",
    "--pr-why",
    "Sums were wrong.",
    "--pr-impact",
    "Sums are right.",
)

SPEC_DOCUMENT = "# Spec\n\n## 1. Alpha\n\nBuild alpha.\n\n## 2. Beta\n\nBuild beta too.\n"

ALPHA = {"name": "alpha", "files": ["alpha.py"], "source": None, "acceptance": []}

STRUCTURE = {"items": [ALPHA], "assumptions": [], "constraints": []}


def module(name):
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    return importlib.import_module(name)


def put(path, content):
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)
    return path


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def tail(completed):
    return "\n--- stdout ---\n%s\n--- stderr ---\n%s" % (
        completed.stdout[-4000:],
        completed.stderr[-4000:],
    )


def result_line(completed):
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return {}
    try:
        parsed = json.loads(lines[-1])
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def executable(path, script):
    put(path, "#!%s\n%s" % (sys.executable, script))
    os.chmod(path, 0o755)
    return path


class Scratch:
    def __init__(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="mitosis-handoff-"))
        self.repo = os.path.join(self.tmp, "repo")
        self.remote = os.path.join(self.tmp, "remote.git")
        self.stubs = os.path.join(self.tmp, "stubs")
        self.run_dir = os.path.join(self.tmp, "runs", "run")
        kept = {key: value for key, value in os.environ.items() if key not in STRIPPED}
        self.env = {
            **kept,
            **GIT_IDENTITY,
            "HOME": os.path.join(self.tmp, "home"),
            "PLUGIN_METRICS_DIR": os.path.join(self.tmp, "metrics"),
        }
        os.makedirs(self.env["HOME"])
        os.makedirs(self.repo)
        self.checked("init", "-q", "-b", "main", self.repo)
        self.checked("init", "-q", "--bare", self.remote)
        put(os.path.join(self.repo, "src", "calc.py"), ORIGINAL_CALC)
        put(os.path.join(self.repo, "docs", "spec.md"), SPEC_DOCUMENT)
        put(os.path.join(self.repo, "tests", "test_calc.py"), "import unittest\n")
        put(os.path.join(self.repo, ".gitignore"), "__pycache__/\n")
        self.checked("-C", self.repo, "add", "-A")
        self.checked("-C", self.repo, "commit", "-q", "-m", "seed")
        self.checked("-C", self.repo, "remote", "add", "origin", self.remote)
        self.spec = put(os.path.join(self.tmp, "spec.md"), "# Calc\n\n## 5.1 Add\n\nAdd numbers.\n")
        self.items = [
            {
                "name": "add-numbers",
                "task": "Make add return the sum of its arguments.",
                "files": ["src/calc.py"],
                "source": {
                    "path": self.spec,
                    "sha256": hashlib.sha256(read_bytes(self.spec)).hexdigest(),
                },
                "acceptance": [{"file": "tests/test_calc.py", "test": "Calc.adds"}],
                "spec_ref": ["5.1"],
            }
        ]
        self.items_path = put(os.path.join(self.tmp, "items.json"), json.dumps(self.items))
        for role in ("dispatch", "reask", "decompose", "brief"):
            put(os.path.join(self.stubs, role + ".py"), MODEL)
        put(os.path.join(self.stubs, "pr.py"), PULL_REQUEST)
        put(os.path.join(self.stubs, "accept.py"), ACCEPT)
        self.render = executable(os.path.join(self.stubs, "render"), RENDER)
        self.job("dispatch", writes={"src/calc.py": SUM_CALC}, reply=VALID_RETURN)
        self.job("reask", reply=VALID_RETURN)
        self.pull_request(PULL_REQUEST_URL + "\n")

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def checked(self, *args):
        completed = subprocess.run(
            ["git", *args],
            cwd=self.tmp,
            env=self.env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=CALL_LIMIT,
        )
        if completed.returncode != 0:
            raise RuntimeError("scaffold git %s failed: %s" % (" ".join(args), completed.stderr))
        return completed.stdout

    def job(self, role, **job):
        put(os.path.join(self.stubs, role + ".json"), json.dumps(job))

    def pull_request(self, printed):
        put(os.path.join(self.stubs, "pr.json"), json.dumps({"printed": printed}))

    def log(self, role):
        return os.path.join(self.stubs, role + ".log")

    def calls(self, role):
        if not os.path.isfile(self.log(role)):
            return []
        with open(self.log(role), encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def stub(self, script, *words):
        return " ".join(
            [shlex.quote(sys.executable), shlex.quote(os.path.join(self.stubs, script))]
            + list(words)
        )

    def dispatch_template(self, *placeholders):
        return self.stub("dispatch.py", *(placeholders or ("{task}", "{session_id}", "{schema}")))

    def reask_template(self, *placeholders):
        return self.stub("reask.py", *(placeholders or ("{prompt}", "{session_id}", "{schema}")))

    def build_flags(self, *extra, dispatch=None, reask=None, with_reask=True):
        return [
            "--items",
            self.items_path,
            "--run-dir",
            self.run_dir,
            "--feature-branch",
            "main",
            "--dispatch-command",
            dispatch or self.dispatch_template(),
            *(("--reask-command", reask or self.reask_template()) if with_reask else ()),
            "--acceptance-command",
            self.stub("accept.py", "{file}", "{test}"),
            "--timeout",
            "120",
            *extra,
        ]

    def held(self, **options):
        return self.mitosis(*self.build_flags("--no-push", **options))

    def shipped(self):
        return self.mitosis(
            *self.build_flags(
                *PUSH_FLAGS,
                "--pr-command",
                self.stub("pr.py", "{branch}"),
                "--render-command",
                self.render,
            )
        )

    def decompose_flags(self, *extra):
        return [
            "--spec",
            "docs/spec.md",
            "--plan-only",
            "--run-dir",
            self.run_dir,
            "--decompose-command",
            self.stub("decompose.py", "{model}", "{session_id}", "{schema}"),
            "--reask-command",
            self.reask_template(),
            "--tier-model",
            "top=claude-opus",
            "--timeout",
            "120",
            *extra,
        ]

    def brief_flags(self, *extra):
        return [
            "--spec",
            "docs/spec.md",
            "--resume",
            "--plan-only",
            "--run-dir",
            self.run_dir,
            "--brief-command",
            self.stub("brief.py", "{step}", "{session_id}", "{schema}"),
            "--reask-command",
            self.reask_template(),
            "--timeout",
            "120",
            *extra,
        ]

    def mitosis(self, *flags):
        return subprocess.run(
            [sys.executable, MITOSIS, *flags],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=RUN_LIMIT,
        )

    def state(self):
        return read_json(os.path.join(self.run_dir, "state.json"))

    def structure_path(self):
        return os.path.join(self.run_dir, "structure.json")


class ScratchCase(unittest.TestCase):
    def setUp(self):
        self.scratch = Scratch()
        self.addCleanup(self.scratch.close)

    def _items_are_h12(self):
        handoff = module("handoff")
        self.assertEqual(handoff.errors("h12-interphase-items", self.scratch.items), ())
        return handoff


class WorkerReplies(ScratchCase):
    def a_valid_structured_reply_lands_the_lane_without_a_reask(self):
        self._items_are_h12()
        completed = self.scratch.held()
        lane = self.scratch.state()["lanes"]["0"]
        self.assertEqual(lane["state"], "ok", tail(completed))
        self.assertEqual(lane["returned"], VALID_RETURN)
        self.assertFalse(os.path.exists(self.scratch.log("reask")))
        self.assertEqual(completed.returncode, 0, tail(completed))

    def a_reply_whose_return_is_only_in_prose_is_reasked_in_the_same_session(self):
        self._items_are_h12()
        self.scratch.job("dispatch", writes={"src/calc.py": SUM_CALC}, result=PROSE_RETURN)
        self.scratch.job("reask", reply={**VALID_RETURN, "notes": "asked again"})
        completed = self.scratch.held()
        dispatched = self.scratch.calls("dispatch")
        reasked = self.scratch.calls("reask")
        self.assertEqual(len(dispatched), 1, tail(completed))
        self.assertEqual(len(reasked), 1, tail(completed))
        prompt, session, schema = reasked[0]
        self.assertEqual(session, dispatched[0][1])
        self.assertEqual(schema, dispatched[0][2])
        self.assertIn("structured_output", prompt)
        lane = self.scratch.state()["lanes"]["0"]
        self.assertEqual(lane["state"], "ok", tail(completed))
        self.assertEqual(lane["returned"]["notes"], "asked again")

    def notes_over_the_cap_are_reasked_never_cut(self):
        self._items_are_h12()
        self.scratch.job(
            "dispatch", writes={"src/calc.py": SUM_CALC}, reply={**VALID_RETURN, "notes": "x" * 201}
        )
        self.scratch.job("reask", reply={**VALID_RETURN, "notes": "short"})
        completed = self.scratch.held()
        reasked = self.scratch.calls("reask")
        self.assertEqual(len(reasked), 1, tail(completed))
        self.assertIn("h10-worker-return", reasked[0][0])
        self.assertIn("#/notes maxLength", reasked[0][0])
        self.assertEqual(self.scratch.state()["lanes"]["0"]["returned"]["notes"], "short")

    def a_second_invalid_reply_fails_the_lane_with_the_errors(self):
        self._items_are_h12()
        self.scratch.job(
            "dispatch", writes={"src/calc.py": SUM_CALC}, reply={**VALID_RETURN, "status": "done"}
        )
        self.scratch.job("reask", reply={**VALID_RETURN, "status": "done"})
        completed = self.scratch.held()
        self.assertEqual(len(self.scratch.calls("reask")), 1, tail(completed))
        lane = self.scratch.state()["lanes"]["0"]
        self.assertEqual(lane["state"], "failed", tail(completed))
        self.assertIsNone(lane["returned"])
        self.assertIn("h10-worker-return", lane["reason"])
        self.assertIn("#/status enum", lane["reason"])
        self.assertEqual(completed.returncode, 10, tail(completed))

    def a_reask_that_meets_a_usage_limit_parks_the_lane(self):
        self._items_are_h12()
        self.scratch.job(
            "dispatch", writes={"src/calc.py": SUM_CALC}, reply={**VALID_RETURN, "status": "done"}
        )
        self.scratch.job("reask", silent=True, stderr=USAGE_LIMIT_LINE, exit=1)
        completed = self.scratch.held()
        self.assertEqual(len(self.scratch.calls("reask")), 1, tail(completed))
        self.assertEqual(self.scratch.state()["lanes"]["0"]["state"], "parked-limit", tail(completed))
        self.assertEqual(completed.returncode, 13, tail(completed))


class DecomposeAndBriefReplies(ScratchCase):
    def _structure_names(self):
        return [step["name"] for step in read_json(self.scratch.structure_path())]

    def a_decompose_reply_is_read_from_structured_output(self):
        self.scratch.job("decompose", reply={"reply": STRUCTURE})
        completed = self.scratch.mitosis(*self.scratch.decompose_flags())
        self.assertEqual(completed.returncode, 0, tail(completed))
        self.assertEqual(self._structure_names(), ["alpha"])
        decomposed = self.scratch.calls("decompose")
        self.assertEqual(len(decomposed), 1, tail(completed))
        schema = json.loads(decomposed[0][2])
        self.assertIsInstance(schema, dict)
        self.assertEqual(schema.get("type"), "object")
        self.assertIn("reply", schema.get("properties", {}))
        self.assertNotIn("$schema", schema)
        self.assertFalse(os.path.exists(self.scratch.log("reask")))

    def a_decompose_reply_with_no_json_is_reasked_never_read_from_its_last_line(self):
        self.scratch.job("decompose", result="Here is the structure: alpha")
        self.scratch.job("reask", reply={"reply": STRUCTURE})
        completed = self.scratch.mitosis(*self.scratch.decompose_flags())
        decomposed = self.scratch.calls("decompose")
        reasked = self.scratch.calls("reask")
        self.assertEqual(len(decomposed), 1, tail(completed))
        self.assertEqual(len(reasked), 1, tail(completed))
        self.assertEqual(reasked[0][1], decomposed[0][1])
        self.assertEqual(completed.returncode, 0, tail(completed))
        self.assertEqual(self._structure_names(), ["alpha"])

    def a_brief_naming_another_step_is_reasked(self):
        self.scratch.job("decompose", reply={"reply": STRUCTURE})
        structured = self.scratch.mitosis(*self.scratch.decompose_flags())
        self.assertEqual(structured.returncode, 0, tail(structured))
        self.assertEqual(self._structure_names(), ["alpha"])
        self.assertFalse(os.path.exists(self.scratch.log("reask")))
        self.scratch.job("brief", reply={"name": "beta", "task": "Build it."})
        self.scratch.job("reask", reply={"name": "alpha", "task": "Build alpha."})
        completed = self.scratch.mitosis(*self.scratch.brief_flags())
        briefed = self.scratch.calls("brief")
        self.assertEqual(len(briefed), 1, tail(completed))
        self.assertEqual(briefed[0][0], "alpha")
        schema = json.loads(briefed[0][2])
        self.assertEqual(schema["properties"]["name"]["const"], "alpha")
        reasked = self.scratch.calls("reask")
        self.assertEqual(len(reasked), 1, tail(completed))
        self.assertIn("alpha", reasked[0][0])
        items = read_json(os.path.join(self.scratch.run_dir, "items.json"))
        tasks = {item["name"]: item.get("task") for item in items}
        self.assertEqual(tasks, {"alpha": "Build alpha."})

    def a_second_invalid_decompose_reply_refuses_with_the_errors(self):
        self.scratch.job("decompose", reply={"reply": {"items": []}})
        self.scratch.job("reask", reply={"reply": {"items": []}})
        completed = self.scratch.mitosis(*self.scratch.decompose_flags())
        self.assertEqual(completed.returncode, 3, tail(completed))
        self.assertIn("h10b-decompose-return", completed.stderr)
        self.assertIn("oneOf", completed.stderr)
        self.assertFalse(os.path.exists(self.scratch.structure_path()))


class TemplateContract(ScratchCase):
    def a_model_template_without_schema_or_session_is_refused(self):
        self._items_are_h12()
        for placeholders, missing in (
            (("{task}", "{session_id}"), "{schema}"),
            (("{task}", "{schema}"), "{session_id}"),
        ):
            completed = self.scratch.held(dispatch=self.scratch.dispatch_template(*placeholders))
            self.assertEqual(completed.returncode, 3, missing + tail(completed))
            self.assertFalse(os.path.exists(self.scratch.log("dispatch")), missing)
            self.assertIn("--dispatch-command", completed.stderr, missing)
            self.assertIn(missing, completed.stderr)

    def plan_refuses_the_worker_commands_a_build_would_refuse(self):
        self._items_are_h12()
        for options, named in (
            ({"dispatch": self.scratch.dispatch_template("{task}", "{session_id}")}, "{schema}"),
            ({"dispatch": self.scratch.dispatch_template("{task}", "{schema}")}, "{session_id}"),
            ({"with_reask": False}, "--reask-command"),
        ):
            completed = self.scratch.mitosis(*self.scratch.build_flags("--plan-only", **options))
            self.assertEqual(completed.returncode, 3, named + tail(completed))
            self.assertIn(named, completed.stderr)

    def a_run_without_a_complete_reask_command_is_refused(self):
        self._items_are_h12()
        for options, named in (
            ({"with_reask": False}, None),
            ({"reask": self.scratch.reask_template("{prompt}", "{schema}")}, "{session_id}"),
            (
                {
                    "reask": self.scratch.reask_template(
                        "{prompt}", "{session_id}", "{schema}", "{task}"
                    )
                },
                "{task}",
            ),
        ):
            completed = self.scratch.held(**options)
            self.assertEqual(completed.returncode, 3, str(named) + tail(completed))
            self.assertFalse(os.path.exists(self.scratch.log("dispatch")), named)
            self.assertIn("--reask-command", completed.stderr, named)
            if named is not None:
                self.assertIn(named, completed.stderr)

    def complete_templates_are_accepted(self):
        self._items_are_h12()
        planned = self.scratch.mitosis(*self.scratch.build_flags("--plan-only"))
        self.assertEqual(planned.returncode, 0, tail(planned))
        helped = self.scratch.mitosis("--help")
        self.assertEqual(helped.returncode, 0, tail(helped))
        self.assertIn("--reask-command", helped.stdout)
        self.assertIn("{schema}", helped.stdout)


class ShipUrl(ScratchCase):
    def _ship_failed(self, printed, named):
        handoff = self._items_are_h12()
        self.scratch.pull_request(printed)
        completed = self.scratch.shipped()
        self.assertEqual(completed.returncode, 22, tail(completed))
        msp = result_line(completed)["msps"][0]
        self.assertEqual(msp["state"], "ship-failed")
        self.assertIsNone(msp["pull_request"])
        self.assertIn(named, msp["reason"])
        state = self.scratch.state()
        self.assertEqual(state["msps"]["0"]["ship"]["pull_request"], "")
        return handoff, state

    def a_pull_request_command_that_prints_no_url_fails_the_ship(self):
        handoff, state = self._ship_failed("created\n", "created")
        self.assertEqual(handoff.errors("h9-mitosis-state", state), ())

    def a_pull_request_command_that_prints_nothing_fails_the_ship(self):
        self._ship_failed("", "nothing")

    def a_url_followed_by_a_trailing_line_fails_the_ship(self):
        self._ship_failed(PULL_REQUEST_URL + "\nDone\n", "Done")

    def a_pull_request_url_ships(self):
        handoff = self._items_are_h12()
        self.scratch.pull_request(PULL_REQUEST_URL + "\n")
        completed = self.scratch.shipped()
        self.assertEqual(completed.returncode, 0, tail(completed))
        summary = result_line(completed)
        self.assertEqual(summary["msps"][0]["state"], "shipped")
        self.assertEqual(summary["msps"][0]["pull_request"], PULL_REQUEST_URL)
        self.assertEqual(handoff.errors("h8-mitosis-result", summary), ())


class Loader(unittest.TestLoader):
    def getTestCaseNames(self, case):
        return sorted(
            name
            for name in vars(case)
            if not name.startswith("_") and callable(getattr(case, name))
        )


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case in (WorkerReplies, DecomposeAndBriefReplies, TemplateContract, ShipUrl):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
