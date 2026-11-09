import contextlib
import hashlib
import io
import itertools
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core
import decompose
import mitosis
import run
import shape

REASK_COMMAND = " ".join(
    (
        shlex.quote(sys.executable),
        "-c",
        shlex.quote("import sys; sys.stderr.write('no re-ask was expected\\n'); sys.exit(1)"),
        "{prompt}",
        "{session_id}",
        "{schema}",
    )
)


def lanes(*spans):
    return {
        str(index): {"started": start, "finished": finish, "state": "ok"}
        for index, (start, finish) in enumerate(spans)
    }


class AchievedParallelism(unittest.TestCase):
    def a_handoff_at_one_timestamp_is_not_an_overlap(self):
        peak, counted = mitosis.achieved_parallelism(
            lanes(
                ("2026-09-19T02:04:09+00:00", "2026-09-19T02:08:17+00:00"),
                ("2026-09-19T02:08:17+00:00", "2026-09-19T02:12:40+00:00"),
            )
        )
        self.assertEqual(peak, 1)
        self.assertEqual(counted, 2)

    def two_lanes_handing_off_to_one_survivor_peaks_at_the_survivor_count(self):
        peak, _ = mitosis.achieved_parallelism(
            lanes(
                ("2026-09-19T02:04:09+00:00", "2026-09-19T02:08:17+00:00"),
                ("2026-09-19T02:04:09+00:00", "2026-09-19T02:08:24+00:00"),
                ("2026-09-19T02:08:17+00:00", "2026-09-19T02:12:40+00:00"),
                ("2026-09-19T02:08:17+00:00", "2026-09-19T02:15:00+00:00"),
            )
        )
        self.assertEqual(peak, 3)

    def genuine_overlap_is_still_counted(self):
        peak, _ = mitosis.achieved_parallelism(
            lanes(
                ("2026-09-19T02:04:00+00:00", "2026-09-19T02:10:00+00:00"),
                ("2026-09-19T02:05:00+00:00", "2026-09-19T02:11:00+00:00"),
                ("2026-09-19T02:06:00+00:00", "2026-09-19T02:12:00+00:00"),
            )
        )
        self.assertEqual(peak, 3)

    def a_lane_with_no_timestamps_is_not_counted(self):
        peak, counted = mitosis.achieved_parallelism(
            {
                "0": {"started": "2026-09-19T02:04:00+00:00", "finished": None, "state": "failed"},
                "1": {"state": "blocked"},
            }
        )
        self.assertEqual((peak, counted), (0, 0))

    def no_lanes_at_all_report_nothing(self):
        self.assertEqual(mitosis.achieved_parallelism({}), (0, 0))
        self.assertEqual(mitosis.achieved_parallelism(None), (0, 0))

    def the_reported_peak_never_exceeds_the_concurrency_cap(self):
        spans = lanes(
            ("2026-09-19T02:04:09+00:00", "2026-09-19T02:08:17+00:00"),
            ("2026-09-19T02:04:09+00:00", "2026-09-19T02:08:24+00:00"),
            ("2026-09-19T02:08:17+00:00", "2026-09-19T02:12:40+00:00"),
            ("2026-09-19T02:08:17+00:00", "2026-09-19T02:15:00+00:00"),
            ("2026-09-19T02:08:24+00:00", "2026-09-19T02:09:55+00:00"),
            ("2026-09-19T02:15:00+00:00", "2026-09-19T02:19:26+00:00"),
            ("2026-09-19T02:19:26+00:00", "2026-09-19T02:20:03+00:00"),
            ("2026-09-19T02:19:26+00:00", "2026-09-19T02:23:28+00:00"),
            ("2026-09-19T02:23:28+00:00", "2026-09-19T02:26:05+00:00"),
        )
        peak, counted = mitosis.achieved_parallelism(spans)
        self.assertLessEqual(peak, 3)
        self.assertEqual(counted, 9)


class RiskMarkers(unittest.TestCase):
    def a_marker_does_not_fire_inside_a_longer_word(self):
        self.assertEqual(core.tier_for(["src/waverider.py"], ["wav"], "simple"), "cheap")
        self.assertEqual(
            core.tier_for(["app/(unauthenticated)/page.tsx"], ["auth"], "simple"), "cheap"
        )

    def a_marker_fires_on_a_path_segment(self):
        self.assertEqual(core.tier_for(["bleep/wav.py"], ["wav"], "simple"), "top")
        self.assertEqual(core.tier_for(["src/wav/writer.py"], ["wav"], "simple"), "top")

    def a_marker_fires_on_a_camel_case_word(self):
        self.assertEqual(
            core.tier_for(["app/types/AuthResponse.ts"], ["auth"], "simple"), "top"
        )

    def a_marker_carrying_a_separator_keeps_substring_behaviour(self):
        self.assertEqual(core.tier_for(["api/auth/login.py"], ["api/auth"], "simple"), "top")
        self.assertEqual(core.tier_for(["db/0004_add.sql"], [".sql"], "simple"), "top")

    def a_glob_marker_matches_only_as_a_substring(self):
        self.assertEqual(core.tier_for(["db/0004_add.sql"], ["db/*.sql"], "simple"), "cheap")
        self.assertEqual(core.tier_for(["legacy/DB/*.SQL"], ["db/*.sql"], "simple"), "top")


class TemplatePlaceholders(unittest.TestCase):
    def a_placeholder_the_template_kind_does_not_offer_is_refused(self):
        with self.assertRaises(run.ConfigError) as caught:
            run.check_template("acceptance", "runner {file} {test} {model}")
        self.assertIn("model", str(caught.exception))

    def every_documented_placeholder_is_accepted(self):
        run.check_template("dispatch", "agent {task} {model} {tier} {worktree} {branch}")
        run.check_template("acceptance", "runner {file} {test} {worktree}")


class CoverageReport(unittest.TestCase):
    def a_decompose_record_supplies_coverage_when_no_Step_carries_a_source(self):
        record = {"coverage": {"mode": "headings", "sections": [{"id": "1", "title": "A", "heading": "1. A", "line": 1}], "uncovered": [{"id": "1", "title": "A", "heading": "1. A", "line": 1}], "unmatched_claims": []}}
        lines = mitosis.coverage_lines({"items": [], "source": None}, record, ".")
        self.assertIn("by headings: 0 of 1 section claimed, 1 unclaimed", lines[0])

    def coverage_is_unavailable_only_when_there_is_no_record_and_no_source(self):
        lines = mitosis.coverage_lines({"items": [], "source": None}, None, ".")
        self.assertTrue(lines[0].startswith("unavailable: the Steps declare no source document"))


STRUCTURER = """
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
prompt = sys.stdin.read()
with open(os.path.join(here, "structure-prompt-%d.txt" % os.getpid()), "w", encoding="utf-8") as handle:
    handle.write(prompt)
name = "structure-reply.txt"
if os.path.isfile(os.path.join(here, "structure-reply-second.txt")):
    try:
        os.close(os.open(os.path.join(here, "first"), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        name = "structure-reply-second.txt"
with open(os.path.join(here, name), encoding="utf-8") as handle:
    reply = json.load(handle)
print(json.dumps({
    "type": "result",
    "session_id": sys.argv[2],
    "result": "done",
    "structured_output": {"reply": reply},
}))
"""

BRIEFER = """
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
step = sys.argv[1]
with open(os.path.join(here, "brief-prompt-%s.txt" % step), "w", encoding="utf-8") as handle:
    handle.write(sys.stdin.read())
if os.path.isfile(os.path.join(here, "fail-%s" % step)):
    sys.exit(2)
print(json.dumps({
    "type": "result",
    "session_id": sys.argv[3],
    "result": "done",
    "structured_output": {"name": step, "task": "Build %s." % step},
}))
"""

REASKER = """
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "reask.log"), "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
sys.stdin.read()
print(json.dumps({"type": "result", "session_id": sys.argv[2], "result": "still prose"}))
"""

ISOLATED_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "mitosis test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "mitosis test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "HOME": tempfile.gettempdir(),
}


def write(root, relative, content):
    path = os.path.join(root, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


SPEC_SECTION = "1.1"
SPEC_DOCUMENT = "# Spec\n\n## 1.1 Build\n"


def items_document(folder, items):
    path = os.path.abspath(write(folder, "spec.md", SPEC_DOCUMENT))
    source = {"path": path, "sha256": hashlib.sha256(SPEC_DOCUMENT.encode("utf-8")).hexdigest()}
    return json.dumps([{**item, "source": item.get("source") or source} for item in items])


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def command(root, script_name, script, *placeholders):
    path = write(root, os.path.join("worker", script_name), script)
    return " ".join((shlex.quote(sys.executable), shlex.quote(path)) + placeholders)


def structurer(root, reply, second=None):
    write(root, "worker/structure-reply.txt", json.dumps(reply))
    if second is not None:
        write(root, "worker/structure-reply-second.txt", json.dumps(second))
    return command(root, "structurer.py", STRUCTURER, "{document}", "{session_id}", "{schema}")


def briefer(root, failing=()):
    for name in failing:
        write(root, "worker/fail-%s" % name, "")
    return command(
        root, "briefer.py", BRIEFER, "{step}", "{document}", "{session_id}", "{schema}"
    )


def reasker(root):
    return command(root, "reasker.py", REASKER, "{prompt}", "{session_id}", "{schema}")


def reasks(root):
    path = os.path.join(root, "worker", "reask.log")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def structure_prompts(root):
    worker = os.path.join(root, "worker")
    if not os.path.isdir(worker):
        return 0
    return sum(1 for name in os.listdir(worker) if name.startswith("structure-prompt-"))


def briefed_steps(root):
    worker = os.path.join(root, "worker")
    if not os.path.isdir(worker):
        return []
    prefix, suffix = "brief-prompt-", ".txt"
    return sorted(
        name[len(prefix) : -len(suffix)]
        for name in os.listdir(worker)
        if name.startswith(prefix) and name.endswith(suffix)
    )


def bare(name, **extra):
    return {"name": name, "files": [name + ".py"], "source": None, "acceptance": [], "spec_ref": [SPEC_SECTION], **extra}


def briefed(name, **extra):
    return bare(name, task="Do %s." % name, **extra)


def structure_reply(*items):
    return {"items": list(items), "assumptions": [], "constraints": []}


def delta_reply(**keys):
    return {
        "keep": [],
        "change": [],
        "add": [],
        "remove": [],
        "assumptions": [],
        "constraints": [],
        **keys,
    }


def section(text, title):
    lines = text.splitlines()
    start = lines.index(title + ":") + 1
    body = itertools.takewhile(lambda line: line.startswith("  "), lines[start:])
    return [line[2:] for line in body]


def invoke(root, argv):
    before = os.getcwd()
    os.chdir(root)
    out, err = io.StringIO(), io.StringIO()
    try:
        with tempfile.TemporaryDirectory() as store, unittest.mock.patch.dict(
            os.environ, {"PLUGIN_METRICS_DIR": store}
        ):
            os.environ.pop("PLUGIN_METRICS_PARENT", None)
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            os.environ.pop("SHIP", None)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = mitosis.main(argv)
                except SystemExit as stop:
                    code = stop.code
    finally:
        os.chdir(before)
    return code, out.getvalue(), err.getvalue()


SUBJECT = "write pull requests in the house format"

WHY = "Callers need it."

IMPACT = "Every new body changes shape."

PR_FLAGS = (
    "--pr-type",
    "feat",
    "--pr-scope",
    "ship",
    "--pr-subject",
    SUBJECT,
    "--pr-why",
    WHY,
    "--pr-impact",
    IMPACT,
)

FAKE_BODY = (
    "## Problem\nFake body.\n\n## Change\n- Fake change.\n\n## Impact\nFake impact.\n\n"
    "## Testing\n| Check | Result |\n|---|---|\n| fake | passed |"
)
VERIFIED_BODY = (
    "## Problem\nFake body.\n\n## Change\n- Fake change.\n\n## Impact\nFake impact.\n\n"
    "## Testing\n| Check | Result |\n|---|---|\n| verification | %s |"
)

ACCEPTING_RENDER = r'''
import json
import os
import shutil
import sys

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls"), "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv) + "\n")
shutil.copyfile(sys.argv[sys.argv.index("--fields") + 1], os.path.join(here, "fields.copy.json"))
print(json.dumps({"title": "feat(ship): fake title", "body": %r}, separators=(",", ":")))
''' % FAKE_BODY


def executable(folder, name, script):
    path = write(folder, name, "#!%s\n%s" % (sys.executable, script))
    os.chmod(path, 0o755)
    return path


def fake_render(folder):
    return executable(folder, "pull-request", ACCEPTING_RENDER)


def refusing_render(folder, name, line):
    return executable(
        folder, name, "import sys\nsys.stderr.write(%r)\nsys.exit(2)\n" % (line + "\n")
    )


def git_repo(root):
    env = {**os.environ, **ISOLATED_GIT_ENV}
    for args in (
        ["git", "init", "-q", root],
        ["git", "-C", root, "commit", "-q", "--allow-empty", "-m", "root"],
        ["git", "-C", root, "branch", "feature"],
    ):
        subprocess.run(args, check=True, capture_output=True, env=env)


class Flags(unittest.TestCase):
    def the_help_lists_every_staging_flag(self):
        parser = mitosis.build_parser()
        declared = mitosis.declared_flags(parser)
        text = parser.format_help()
        for flag in ("--decisions", "--structure-samples", "--pick", "--revise", "--brief-command"):
            self.assertIn(flag, declared)
            self.assertIn(flag, text)
        for placeholder in ("{prompt}", "{model}", "{step}", "{document}"):
            self.assertIn(placeholder, text)

    def a_run_builds_several_lanes_at_once_by_default(self):
        args = mitosis.build_parser().parse_args(["--items", "items.json"])
        self.assertEqual(args.concurrency, 4)
        self.assertIn("(default: 4)", " ".join(mitosis.build_parser().format_help().split()))

    def the_read_set_is_capped_at_forty_by_default(self):
        args = mitosis.build_parser().parse_args(["--items", "items.json"])
        self.assertEqual(args.context_cap, core.CONTEXT_CAP)
        self.assertIn("(default: 40)", " ".join(mitosis.build_parser().format_help().split()))

    def structure_samples_defaults_to_one(self):
        args = mitosis.build_parser().parse_args(["--spec", "x", "--decompose-command", "c"])
        self.assertEqual(args.structure_samples, 1)
        self.assertIsNone(args.pick)
        self.assertFalse(args.revise)
        self.assertIsNone(args.brief_command)
        self.assertIsNone(args.decisions)

    def a_brief_template_with_an_unoffered_placeholder_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "docs/spec.md", "# One\n\nbody\n")
            code, _, err = invoke(
                root,
                [
                    "--spec",
                    "docs/spec.md",
                    "--decompose-command",
                    structurer(root, structure_reply(bare("alpha"))),
                    "--brief-command",
                    briefer(root) + " {worktree}",
                    "--plan-only",
                    "--run-dir",
                    os.path.join(root, "run"),
                ],
            )
            self.assertEqual(code, mitosis.EXIT_REFUSED)
            self.assertIn("{worktree}", err)
            self.assertIn("brief", err)
            self.assertEqual(structure_prompts(root), 0)
            self.assertEqual(briefed_steps(root), [])

    def every_offered_brief_placeholder_is_accepted(self):
        args = mitosis.build_parser().parse_args(
            [
                "--spec",
                "x",
                "--decompose-command",
                "c {prompt} {model} {document} {session_id} {schema}",
                "--brief-command",
                "b {prompt} {model} {step} {document} {session_id} {schema}",
                "--reask-command",
                "r {prompt} {session_id} {schema} {model}",
            ]
        )
        mitosis.check_templates(args)

    def a_model_template_without_schema_or_session_is_refused_before_any_call(self):
        cases = (
            ("--decompose-command", "{session_id}", "{schema}", ()),
            ("--decompose-command", "{schema}", "{session_id}", ()),
            ("--brief-command", "{session_id}", "{schema}", ("--resume",)),
            ("--brief-command", "{schema}", "{session_id}", ("--resume",)),
        )
        for flag, kept, missing, extra in cases:
            with self.subTest(flag=flag, missing=missing), tempfile.TemporaryDirectory() as root:
                write(root, "docs/spec.md", "# One\n\nbody\n")
                write(root, "run/structure.json", json.dumps([bare("alpha")]))
                template = command(root, "model.py", "", "{document}", kept)
                code, _, err = invoke(
                    root,
                    [
                        "--spec",
                        "docs/spec.md",
                        flag,
                        template,
                        "--reask-command",
                        reasker(root),
                        "--timeout",
                        "20",
                        "--plan-only",
                        "--run-dir",
                        os.path.join(root, "run"),
                        *extra,
                    ],
                )
                self.assertEqual(code, mitosis.EXIT_REFUSED, err)
                self.assertIn(flag, err)
                self.assertIn(missing, err)
                self.assertEqual(structure_prompts(root), 0)
                self.assertEqual(briefed_steps(root), [])

    def a_model_run_without_a_complete_reask_command_is_refused_before_any_call(self):
        for reask, named in ((None, "--reask-command is required"), ("r {prompt} {schema}", "{session_id}")):
            with self.subTest(reask=reask), tempfile.TemporaryDirectory() as root:
                write(root, "docs/spec.md", "# One\n\nbody\n")
                code, _, err = invoke(
                    root,
                    [
                        "--spec",
                        "docs/spec.md",
                        "--decompose-command",
                        structurer(root, structure_reply(bare("alpha"))),
                        *(("--reask-command", reask) if reask else ()),
                        "--timeout",
                        "20",
                        "--plan-only",
                        "--run-dir",
                        os.path.join(root, "run"),
                    ],
                )
                self.assertEqual(code, mitosis.EXIT_REFUSED, err)
                self.assertIn("--reask-command", err)
                self.assertIn(named, err)
                self.assertEqual(structure_prompts(root), 0)

    def a_resume_that_calls_no_model_needs_no_reask_command(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "docs/spec.md", "# One\n\nbody\n")
            write(root, "run/structure.json", json.dumps([bare("alpha")]))
            code, _, err = invoke(
                root,
                [
                    "--spec",
                    "docs/spec.md",
                    "--resume",
                    "--decompose-command",
                    "c {prompt}",
                    "--plan-only",
                    "--run-dir",
                    os.path.join(root, "run"),
                ],
            )
            self.assertNotIn("--reask-command", err)
            self.assertNotIn("--decompose-command lacks", err)

    def revise_cannot_be_combined_with_items_or_resume(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "items.json", items_document(root, [briefed("alpha")]))
            code, _, err = invoke(root, ["--items", "items.json", "--revise", "--plan-only"])
            self.assertEqual(code, mitosis.EXIT_USAGE)
            self.assertIn("--revise", err)
            write(root, "docs/spec.md", "# One\n\nbody\n")
            code, _, err = invoke(root, ["--spec", "docs/spec.md", "--resume", "--revise"])
            self.assertEqual(code, mitosis.EXIT_USAGE)
            self.assertIn("--revise", err)

    def a_pick_beyond_the_sample_count_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "docs/spec.md", "# One\n\nbody\n")
            code, _, err = invoke(
                root,
                [
                    "--spec",
                    "docs/spec.md",
                    "--decompose-command",
                    structurer(root, structure_reply(bare("alpha"))),
                    "--structure-samples",
                    "2",
                    "--pick",
                    "3",
                    "--plan-only",
                    "--run-dir",
                    os.path.join(root, "run"),
                ],
            )
            self.assertEqual(code, mitosis.EXIT_USAGE)
            self.assertIn("--pick", err)
            self.assertEqual(structure_prompts(root), 0)


class Staging(unittest.TestCase):
    def _plan_only(self, root, reply, *extra, second=None):
        return invoke(
            root,
            [
                "--spec",
                "docs/spec.md",
                "--decompose-command",
                structurer(root, reply, second),
                "--brief-command",
                briefer(root),
                "--reask-command",
                reasker(root),
                "--plan-only",
                "--run-dir",
                os.path.join(root, "run"),
                *extra,
            ],
        )

    def _resume(self, root, *extra, failing=()):
        return invoke(
            root,
            [
                "--spec",
                "docs/spec.md",
                "--resume",
                "--brief-command",
                briefer(root, failing),
                "--reask-command",
                reasker(root),
                "--run-dir",
                os.path.join(root, "run"),
                *extra,
            ],
        )

    def plan_only_writes_a_structure_and_spawns_no_brief(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "docs/spec.md", "# One\n\nbody\n")
            code, out, err = self._plan_only(
                root, structure_reply(bare("alpha"), bare("beta", after=["alpha"]))
            )
            self.assertEqual(code, mitosis.EXIT_SHIPPED, err)
            run_dir = os.path.join(root, "run")
            structure = read_json(os.path.join(run_dir, mitosis.STRUCTURE_FILE))
            self.assertEqual([step["name"] for step in structure], ["alpha", "beta"])
            self.assertTrue(all("task" not in step for step in structure))
            self.assertEqual(structure[0]["source"]["path"], "docs/spec.md")
            self.assertTrue(os.path.isfile(os.path.join(run_dir, mitosis.DECOMPOSE_FILE)))
            self.assertFalse(os.path.isfile(os.path.join(run_dir, mitosis.ITEMS_FILE)))
            self.assertFalse(os.path.isfile(os.path.join(run_dir, run.PLAN_FILE)))
            self.assertEqual(structure_prompts(root), 1)
            self.assertEqual(briefed_steps(root), [])
            self.assertEqual(section(out, "Split shape")[0].split(", ")[0], "steps 2")
            self.assertEqual(section(out, "Decisions"), ["none: no decisions file was supplied"])
            self.assertIn("by headings", section(out, "Coverage map")[0])
            self.assertTrue(section(out, "Outcome")[-1].startswith("exit 0"))

    def resume_briefs_only_the_unbriefed_steps(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "docs/spec.md", "# One\n\nbody\n")
            run_dir = os.path.join(root, "run")
            write(
                root,
                "run/structure.json",
                json.dumps([briefed("alpha"), bare("beta", after=["alpha"]), bare("gamma")]),
            )
            code, out, err = self._resume(root, "--plan-only")
            self.assertEqual(code, mitosis.EXIT_SHIPPED, err)
            self.assertEqual(briefed_steps(root), ["beta", "gamma"])
            self.assertEqual(structure_prompts(root), 0)
            items = read_json(os.path.join(run_dir, mitosis.ITEMS_FILE))
            self.assertEqual(
                [item["task"] for item in items], ["Do alpha.", "Build beta.", "Build gamma."]
            )
            structure = read_json(os.path.join(run_dir, mitosis.STRUCTURE_FILE))
            self.assertEqual([step["task"] for step in structure], [item["task"] for item in items])
            plan = read_json(os.path.join(run_dir, run.PLAN_FILE))
            self.assertEqual(len(plan["lanes"]), 3)
            self.assertEqual(plan["lane_after"], {"1": [0]})
            self.assertTrue(os.path.isfile(os.path.join(run_dir, "briefs", "beta.log")))
            self.assertTrue(os.path.isfile(os.path.join(run_dir, "briefs", "gamma.log")))
            self.assertEqual(section(out, "Split shape")[0].split(", ")[0], "steps 3")
            self.assertIn("plan id: " + plan["plan_id"], section(out, "Outcome"))

    def resume_continues_into_the_build_once_every_step_is_briefed(self):
        with tempfile.TemporaryDirectory() as root:
            git_repo(root)
            write(root, "docs/spec.md", "# One\n\nbody\n")
            write(root, "run/structure.json", json.dumps([briefed("alpha"), bare("beta")]))
            seen = None

            def fake_execute(args, plan, models, run_dir, repo, root_dir, base=None):
                nonlocal seen
                seen = plan
                return {}, None

            kept = mitosis.execute
            mitosis.execute = fake_execute
            try:
                code, out, err = self._resume(
                    root,
                    "--feature-branch",
                    "feature",
                    "--dispatch-command",
                    "worker {task} {session_id} {schema}",
                    "--reask-command",
                    "reask {prompt} {session_id} {schema}",
                    "--acceptance-command",
                    "probe {file} {test}",
                    "--pr-command",
                    "opener {branch}",
                    *PR_FLAGS,
                    "--render-command",
                    fake_render(os.path.join(root, "worker")),
                )
            finally:
                mitosis.execute = kept
            self.assertEqual(code, mitosis.EXIT_NO_BRANCHES, err)
            self.assertEqual([item["task"] for item in seen["items"]], ["Do alpha.", "Build beta."])
            self.assertEqual(briefed_steps(root), ["beta"])
            items = read_json(os.path.join(root, "run", mitosis.ITEMS_FILE))
            self.assertEqual([item["name"] for item in items], ["alpha", "beta"])

    def a_one_shot_run_without_a_brief_command_refuses_before_any_dispatch(self):
        with tempfile.TemporaryDirectory() as root:
            git_repo(root)
            write(root, "docs/spec.md", "# One\n\nbody\n")
            code, _, err = invoke(
                root,
                [
                    "--spec",
                    "docs/spec.md",
                    "--decompose-command",
                    structurer(root, structure_reply(bare("alpha"))),
                    "--run-dir",
                    os.path.join(root, "run"),
                    "--feature-branch",
                    "feature",
                    "--dispatch-command",
                    "worker {task} {session_id} {schema}",
                    "--reask-command",
                    "reask {prompt} {session_id} {schema}",
                    "--acceptance-command",
                    "probe {file} {test}",
                    "--pr-command",
                    "opener {branch}",
                    *PR_FLAGS,
                ],
            )
            self.assertEqual(code, mitosis.EXIT_REFUSED)
            self.assertIn("--brief-command", err)
            self.assertEqual(structure_prompts(root), 0)
            self.assertFalse(os.path.isfile(os.path.join(root, "run", mitosis.STRUCTURE_FILE)))

    def a_step_left_unbriefed_refuses_with_exit_three(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "docs/spec.md", "# One\n\nbody\n")
            run_dir = os.path.join(root, "run")
            write(root, "run/structure.json", json.dumps([bare("alpha"), bare("beta"), bare("gamma")]))
            code, _, err = self._resume(root, "--plan-only", failing=("beta",))
            self.assertEqual(code, mitosis.EXIT_REFUSED)
            self.assertIn("beta", err)
            self.assertEqual(len(reasks(root)), 1)
            self.assertNotIn("alpha", err.split("mitosis:")[-1].split("\n")[0])
            self.assertEqual(briefed_steps(root), ["alpha", "beta", "gamma"])
            self.assertFalse(os.path.isfile(os.path.join(run_dir, mitosis.ITEMS_FILE)))
            structure = read_json(os.path.join(run_dir, mitosis.STRUCTURE_FILE))
            self.assertEqual(
                [step.get("task") for step in structure], ["Build alpha.", None, "Build gamma."]
            )

    def resume_without_a_structure_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "docs/spec.md", "# One\n\nbody\n")
            write(root, "run/items.json", json.dumps([briefed("alpha")]))
            code, _, err = self._resume(root, "--plan-only")
            self.assertEqual(code, mitosis.EXIT_REFUSED)
            self.assertIn(mitosis.STRUCTURE_FILE, err)
            self.assertEqual(briefed_steps(root), [])

    def a_structure_with_no_steps_for_a_document_with_content_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "docs/spec.md", "# One\n\nbody\n")
            code, _, err = self._plan_only(root, structure_reply())
            self.assertEqual(code, mitosis.EXIT_REFUSED)
            self.assertIn("contract error", err)
            self.assertFalse(os.path.isfile(os.path.join(root, "run", mitosis.STRUCTURE_FILE)))
            self.assertEqual(briefed_steps(root), [])

    def a_structure_carrying_a_task_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "docs/spec.md", "# One\n\nbody\n")
            code, _, err = self._plan_only(root, structure_reply(briefed("alpha")))
            self.assertEqual(code, mitosis.EXIT_REFUSED)
            self.assertIn("contract error", err)
            self.assertFalse(os.path.isfile(os.path.join(root, "run", mitosis.STRUCTURE_FILE)))

    def revise_runs_a_delta_against_the_persisted_structure(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "docs/spec.md", "# One\n\nbody\n")
            write(root, "run/structure.json", json.dumps([briefed("alpha"), bare("beta")]))
            code, out, err = self._plan_only(
                root,
                delta_reply(keep=["alpha"], remove=["beta"], add=[bare("gamma", after=["alpha"])]),
                "--revise",
            )
            self.assertEqual(code, mitosis.EXIT_SHIPPED, err)
            structure = read_json(os.path.join(root, "run", mitosis.STRUCTURE_FILE))
            self.assertEqual([step["name"] for step in structure], ["alpha", "gamma"])
            self.assertEqual(structure[0]["task"], "Do alpha.")
            self.assertNotIn("task", structure[1])
            self.assertEqual(briefed_steps(root), [])
            self.assertEqual(section(out, "Split shape")[0].split(", ")[0], "steps 2")

    def a_delta_that_does_not_partition_is_refused_and_the_structure_survives(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "docs/spec.md", "# One\n\nbody\n")
            prior = [briefed("alpha"), bare("beta")]
            write(root, "run/structure.json", json.dumps(prior))
            code, _, err = self._plan_only(root, delta_reply(keep=["alpha"]), "--revise")
            self.assertEqual(code, mitosis.EXIT_REFUSED)
            self.assertIn("contract error", err)
            self.assertEqual(read_json(os.path.join(root, "run", mitosis.STRUCTURE_FILE)), prior)

    def revise_without_a_persisted_structure_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "docs/spec.md", "# One\n\nbody\n")
            code, _, err = self._plan_only(root, delta_reply(), "--revise")
            self.assertEqual(code, mitosis.EXIT_REFUSED)
            self.assertIn(mitosis.STRUCTURE_FILE, err)
            self.assertEqual(structure_prompts(root), 0)

    def a_named_decisions_file_that_does_not_exist_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "docs/spec.md", "# One\n\nbody\n")
            code, _, err = self._plan_only(
                root, structure_reply(bare("alpha")), "--decisions", "docs/nope.md"
            )
            self.assertEqual(code, mitosis.EXIT_REFUSED)
            self.assertIn("nope.md", err)
            self.assertEqual(structure_prompts(root), 0)

    def the_decisions_section_names_the_file_and_counts_its_questions(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "docs/spec.md", "# One\n\nbody\n")
            decisions = write(root, "docs/spec.decisions.md", "- one\n- two\n\nprose\n")
            code, out, err = self._plan_only(root, structure_reply(bare("alpha")))
            self.assertEqual(code, mitosis.EXIT_SHIPPED, err)
            line = section(out, "Decisions")[0]
            self.assertIn(os.path.basename(decisions), line)
            self.assertIn("2 settled questions", line)

    def the_best_scoring_sample_is_persisted(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "docs/spec.md", "# One\n\nbody\n")
            fused = structure_reply(
                bare("a", contract_group="g", type="contract"), bare("b", contract_group="g")
            )
            wide = structure_reply(bare("c"), bare("d"))
            code, out, err = self._plan_only(
                root, fused, "--structure-samples", "2", second=wide
            )
            self.assertEqual(code, mitosis.EXIT_SHIPPED, err)
            self.assertEqual(structure_prompts(root), 2)
            structure = read_json(os.path.join(root, "run", mitosis.STRUCTURE_FILE))
            self.assertEqual([step["name"] for step in structure], ["c", "d"])
            self.assertIn("2 structure samples", out)
            self.assertIn("parallelism", out.split("Coverage map:")[0])


class ReconcileReporting(unittest.TestCase):
    def _state(self, found):
        return {"msps": {"0": {"state": "shipped", "reconcile": found}}}

    PLAN = {"msps": [{"label": "alpha", "files": ["a.py"], "steps": ["a"]}]}

    def a_path_left_alone_on_purpose_is_shown_but_is_not_a_finding(self):
        found = {"undeclared": [], "unwritten": [], "untouched": ["a.py"],
                 "crossing": [], "fatal": False}
        lines = mitosis.reconcile_lines(self.PLAN, self._state(found))
        self.assertIn("0 findings from the git log", lines[0])
        self.assertTrue(any("1 left unchanged on purpose" in line for line in lines))
        self.assertTrue(any("untouched: a.py" in line for line in lines))
        self.assertFalse(mitosis.reconcile_dirty(found))

    def a_path_that_should_have_been_written_is_still_a_finding(self):
        found = {"undeclared": [], "unwritten": ["a.py"], "untouched": [],
                 "crossing": [], "fatal": False}
        lines = mitosis.reconcile_lines(self.PLAN, self._state(found))
        self.assertIn("1 finding from the git log", lines[0])
        self.assertTrue(mitosis.reconcile_dirty(found))

    def a_referee_write_is_listed_and_carried_in_the_summary(self):
        found = {"undeclared": ["tests/conftest.py"], "unwritten": [], "untouched": [],
                 "crossing": [], "referee": ["tests/conftest.py"], "fatal": True}
        state = {"msps": {"0": {"state": "referee-reverted", "reconcile": found}}}
        lines = mitosis.reconcile_lines(self.PLAN, state)
        self.assertIn("  referee: tests/conftest.py", lines)
        summarised = mitosis._msp_summary(self.PLAN, state)[0]["reconcile"]
        self.assertEqual(
            summarised,
            {"undeclared": ["tests/conftest.py"], "unwritten": [], "untouched": [],
             "referee": ["tests/conftest.py"], "fatal": True},
        )


class ItemsEntryPointRefusals(unittest.TestCase):
    CYCLIC = [
        {"name": "x", "task": "t", "files": ["pkg/shared.py"], "source": None, "spec_ref": [SPEC_SECTION], "acceptance": []},
        {"name": "y", "task": "t", "files": ["pkg/y.py"], "source": None, "spec_ref": [SPEC_SECTION],
         "acceptance": [], "after": ["x"]},
        {"name": "z", "task": "t", "files": ["pkg/shared.py"], "source": None, "spec_ref": [SPEC_SECTION],
         "acceptance": [], "after": ["y"]},
    ]
    EMPTY_MANIFEST = [
        {"name": "surface", "task": "t", "files": ["pkg/__init__.py", "pkg/g.py"],
         "source": None, "spec_ref": [SPEC_SECTION], "acceptance": []},
        {"name": "parse", "task": "t", "files": ["pkg/parse.py"], "source": None, "spec_ref": [SPEC_SECTION],
         "acceptance": [], "after": ["surface"]},
    ]

    def _run(self, items):
        with tempfile.TemporaryDirectory() as root:
            write(root, "items.json", items_document(root, items))
            return invoke(
                root,
                ["--items", "items.json", "--plan-only", "--run-dir", os.path.join(root, "run")],
            )

    def a_plan_whose_packages_wait_on_each_other_is_planned_as_two(self):
        code, out, err = self._run(self.CYCLIC)
        self.assertEqual(code, mitosis.EXIT_SHIPPED, err + out)
        self.assertEqual(core.msp_items(self.CYCLIC), ((0, 2), (1,)))

    GROUP_CYCLE = [
        {"name": "a", "task": "t", "files": ["a.py"], "source": None, "spec_ref": [SPEC_SECTION],
         "acceptance": [], "contract_group": "g"},
        {"name": "x", "task": "t", "files": ["x.py"], "source": None, "spec_ref": [SPEC_SECTION],
         "acceptance": [], "after": ["a"]},
        {"name": "b", "task": "t", "files": ["b.py"], "source": None, "spec_ref": [SPEC_SECTION],
         "acceptance": [], "contract_group": "g", "after": ["x"]},
    ]

    def a_cycle_through_an_unpinned_group_is_built_by_one_worker(self):
        code, out, err = self._run(self.GROUP_CYCLE)
        self.assertEqual(code, mitosis.EXIT_SHIPPED, err + out)
        self.assertEqual(core.lane_items(self.GROUP_CYCLE), ((0, 2), (1,)))

    def a_package_supplied_as_items_that_would_ship_empty_is_planned_and_reported(self):
        code, out, err = self._run(self.EMPTY_MANIFEST)
        self.assertEqual(code, 0, err + out)
        self.assertNotIn("manifest-exports-nothing", out)
        found = json.loads(out.splitlines()[-1])
        self.assertEqual(found["exit"], 0)
        self.assertEqual([line for line in found["attention"] if "nothing exported" in line], [])

    def a_sound_plan_supplied_as_items_still_ships(self):
        code, out, err = self._run(
            [
                {"name": "parse", "task": "t", "files": ["pkg/parse.py"], "source": None, "spec_ref": [SPEC_SECTION],
                 "acceptance": []},
                {"name": "surface", "task": "t", "files": ["pkg/__init__.py"], "source": None, "spec_ref": [SPEC_SECTION],
                 "acceptance": [], "after": ["parse"]},
            ]
        )
        self.assertEqual(code, mitosis.EXIT_SHIPPED, err)


class GraphLoading(unittest.TestCase):
    def _load(self, graph):
        with tempfile.TemporaryDirectory() as root:
            path = write(root, "graph.json", json.dumps(graph))
            return mitosis.load_graph(path)

    def _refused(self, graph):
        with self.assertRaises(mitosis.Refusal) as caught:
            self._load(graph)
        self.assertIn("must be a node-link graph with nodes and links", str(caught.exception))

    def a_node_link_graph_is_read_as_the_files_its_code_links(self):
        graph = {
            "directed": True,
            "nodes": [
                {"id": "f", "file_type": "code", "source_file": "src/parse.py"},
                {"id": "g", "file_type": "code", "source_file": "src/emit.py"},
                {"id": "d", "file_type": "document", "source_file": "README.md"},
            ],
            "links": [{"source": "f", "target": "g"}, {"source": "d", "target": "f"}],
        }
        self.assertEqual(
            self._load(graph),
            {
                "src/parse.py": ["README.md", "src/emit.py"],
                "src/emit.py": ["src/parse.py"],
                "README.md": ["src/parse.py"],
            },
        )

    def a_mapping_is_still_read_as_it_is(self):
        self._refused({"a.py": ["b.py"]})

    def a_mapping_carrying_metadata_and_nulls_is_read_without_them(self):
        self._refused({"version": "1", "a.py": ["b.py", None, 3], "b.py": None})

    def a_nodes_list_without_links_is_refused_rather_read_as_a_mapping(self):
        for graph in (
            {"nodes": [{"id": "a", "source_file": "a.py"}], "meta": []},
            {"nodes": [{"id": "a"}], "edges": [], "languages": ["python"]},
        ):
            self._refused(graph)

    def an_object_that_is_neither_shape_is_refused_by_name(self):
        for graph in (
            {"directed": False, "graph": {}, "nodes": "not a list"},
            ["a.py"],
            {"nodes": ["a"], "links": []},
            {"nodes": [{"id": "a", "source_file": 3}], "links": []},
            {"nodes": [{"id": "a"}], "links": [{"source": ["a"], "target": "a"}]},
        ):
            self._refused(graph)

    def a_node_link_graph_that_names_no_file_is_refused(self):
        graph = {"nodes": [{"id": "x"}, {"id": "y"}], "links": [{"source": "x", "target": "y"}]}
        self.assertEqual(self._load(graph), {})


HOLDING_WORKER = r'''
import json
import sys

SESSION = sys.argv[2] if len(sys.argv) > 2 else ""

lines = sys.argv[1].splitlines()
opened = lines.index("{")
spec = json.loads("\n".join(lines[opened : lines.index("}", opened) + 1]))
paths = list(dict.fromkeys(path for leaf in spec["leaves"] for path in leaf["edit"]))
for path in paths:
    with open(path, "a") as handle:
        handle.write("work\n")
reply = {"item": "lane", "status": "ok", "files_changed": paths, "notes": "done"}
print(json.dumps({"type": "result", "session_id": SESSION, "result": "done", "structured_output": reply}))
'''


class HeldRun(unittest.TestCase):
    def _run(self, *extra, worker=None):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as elsewhere:
            git_repo(root)
            write(root, "items.json", items_document(root, [briefed("alpha")]))
            with unittest.mock.patch.dict(os.environ, ISOLATED_GIT_ENV):
                return invoke(
                    root,
                    [
                        "--items",
                        "items.json",
                        "--run-dir",
                        os.path.join(elsewhere, "run"),
                        "--feature-branch",
                        "feature",
                        "--dispatch-command",
                        command(elsewhere, "worker.py", worker or HOLDING_WORKER, "{task}", "{session_id}", "{schema}"),
                        "--reask-command",
                        REASK_COMMAND,
                        "--acceptance-command",
                        "probe {file} {test}",
                        *extra,
                    ],
                )

    def the_last_line_is_a_json_summary_the_caller_can_act_on(self):
        code, out, err = self._run("--no-push")
        self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
        found = json.loads(out.splitlines()[-1])
        self.assertEqual(found["exit"], mitosis.EXIT_SHIPPED)
        self.assertEqual(found["meaning"], mitosis.EXIT_MEANING[mitosis.EXIT_SHIPPED])
        self.assertEqual([lane["state"] for lane in found["lanes"]], ["ok"])
        self.assertEqual(found["lanes"][0]["steps"], ["alpha"])
        self.assertTrue(found["lanes"][0]["stdout"].endswith("0.out"))
        self.assertEqual([msp["state"] for msp in found["msps"]], [run.MSP_COMMITTED])
        self.assertEqual(found["attention"], [])
        self.assertEqual(len(found["plan_id"]), 12)
        self.assertTrue(found["run_dir"].endswith("run"))

    def a_summary_names_what_needs_attention_when_a_worker_fails(self):
        code, out, err = self._run("--no-push", worker="import sys\nsys.exit(4)\n")
        self.assertEqual(code, 10, out + err)
        found = json.loads(out.splitlines()[-1])
        self.assertEqual(found["lanes"][0]["state"], "failed")
        self.assertIn("exit 4 with no valid reply; re-asked once: exit 1", found["lanes"][0]["reason"])
        self.assertTrue(found["lanes"][0]["stderr"].endswith("0.reask.err"))
        self.assertEqual(len(found["attention"]), 2)
        self.assertIn("Lane 0 (alpha) is failed", found["attention"][0])
        self.assertIn("MSP alpha is blocked", found["attention"][1])

    def a_held_run_needs_no_pull_request_command_and_pushes_nothing(self):
        code, out, err = self._run("--no-push")
        self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
        self.assertIn("MSP alpha: committed", out)

    def a_run_whose_charter_locks_a_file_the_branch_lacks_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            charter = write(folder, "charter.md", "- Locked: tests/test_locked.py\n")
            code, out, err = self._run("--no-push", "--charter", charter)
        self.assertEqual(code, mitosis.EXIT_REFUSED, out + err)
        self.assertIn(
            "--charter locks tests/test_locked.py, which feature does not hold; commit the "
            "locked checks on it before the build",
            err,
        )

    def a_plan_only_run_reports_an_unsealable_lock_as_attention(self):
        with tempfile.TemporaryDirectory() as folder:
            charter = write(folder, "charter.md", "- Locked: tests/test_locked.py\n")
            code, out, err = self._run("--plan-only", "--charter", charter)
        self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
        found = json.loads(out.splitlines()[-1])
        self.assertIsNone(found["referee"])
        self.assertIn(
            "--charter locks tests/test_locked.py, which feature does not hold; commit the "
            "locked checks on it before the build",
            found["attention"],
        )

    def a_held_run_with_a_pull_request_command_is_refused(self):
        code, _, err = self._run("--no-push", "--pr-command", "opener {branch}")
        self.assertEqual(code, mitosis.EXIT_REFUSED)
        self.assertIn("--no-push", err)


MARKING_WORKER = r'''
import os

open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "spawned"), "w").close()
'''


class PullRequestFlags(unittest.TestCase):
    def _attempt(self, *flags, items=None):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as elsewhere:
            git_repo(root)
            write(root, "items.json", items_document(root, items or [briefed("alpha")]))
            with unittest.mock.patch.dict(os.environ, ISOLATED_GIT_ENV):
                code, out, err = invoke(
                    root,
                    [
                        "--items",
                        "items.json",
                        "--run-dir",
                        os.path.join(elsewhere, "run"),
                        "--feature-branch",
                        "feature",
                        "--dispatch-command",
                        command(elsewhere, "worker.py", MARKING_WORKER, "{task}", "{session_id}", "{schema}"),
                        "--reask-command",
                        REASK_COMMAND,
                        "--acceptance-command",
                        "probe {file} {test}",
                        "--render-command",
                        fake_render(elsewhere),
                        *flags,
                    ],
                )
            return code, err, os.path.exists(os.path.join(elsewhere, "worker", "spawned"))

    def a_run_that_may_push_needs_valid_pull_request_flags(self):
        typed = ("--pr-type", "feat", "--pr-subject", SUBJECT, "--pr-impact", IMPACT)
        for flag, flags in (
            ("--pr-type", ("--pr-subject", SUBJECT, "--pr-why", WHY)),
            ("--pr-subject", ("--pr-type", "feat", "--pr-why", WHY)),
            ("--pr-why", typed),
            ("--pr-type", ("--pr-type", "feature", "--pr-subject", SUBJECT, "--pr-why", WHY)),
            ("--pr-why", typed + ("--pr-why", WHY) * 9),
            ("--pr-door", typed + ("--pr-why", WHY, "--pr-door", "two-way")),
            ("--pr-blast-radius", typed + ("--pr-why", WHY, "--pr-blast-radius", "Docs only.")),
            (
                "--pr-door",
                typed + ("--pr-why", WHY, "--pr-door", "sliding", "--pr-blast-radius", "Docs only."),
            ),
        ):
            with self.subTest(flags=flags):
                code, err, spawned = self._attempt(*flags)
                self.assertEqual(code, mitosis.EXIT_REFUSED, err)
                self.assertIn(flag, err.splitlines()[0])
                self.assertFalse(spawned)

    def a_door_call_reaches_the_header_with_its_blast_radius(self):
        parser = mitosis.build_parser()
        typed = ("--pr-type", "feat", "--pr-subject", SUBJECT, "--pr-why", WHY)
        called = parser.parse_args(
            typed + ("--pr-door", "one-way", "--pr-blast-radius", "Every stored manifest.")
        )
        self.assertIsNone(mitosis.check_pull_request_flags(called))
        header = mitosis.pull_request_header(called)
        self.assertEqual(
            (header["door"], header["blast_radius"]), ("one-way", "Every stored manifest.")
        )
        bare = mitosis.pull_request_header(parser.parse_args(typed))
        self.assertEqual((bare["door"], bare["blast_radius"]), (None, None))


class PushOnly(unittest.TestCase):
    def the_json_summary_carries_each_pull_request_text(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as elsewhere:
            git_repo(root)
            remote = os.path.join(elsewhere, "remote.git")
            env = {**os.environ, **ISOLATED_GIT_ENV}
            for args in (
                ["git", "init", "-q", "--bare", remote],
                ["git", "-C", root, "remote", "add", "origin", remote],
            ):
                subprocess.run(args, check=True, capture_output=True, env=env)
            write(root, "items.json", items_document(root, [briefed("alpha")]))
            with unittest.mock.patch.dict(os.environ, ISOLATED_GIT_ENV):
                code, out, err = invoke(
                    root,
                    [
                        "--items",
                        "items.json",
                        "--run-dir",
                        os.path.join(elsewhere, "run"),
                        "--feature-branch",
                        "feature",
                        "--dispatch-command",
                        command(elsewhere, "worker.py", HOLDING_WORKER, "{task}", "{session_id}", "{schema}"),
                        "--reask-command",
                        REASK_COMMAND,
                        "--acceptance-command",
                        "probe {file} {test}",
                        *PR_FLAGS,
                        "--render-command",
                        fake_render(elsewhere),
                    ],
                )
            heads = subprocess.run(
                ["git", "--git-dir", remote, "for-each-ref", "--format=%(refname)"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.split()
        self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
        found = json.loads(out.splitlines()[-1])
        self.assertEqual(found["exit"], mitosis.EXIT_SHIPPED)
        self.assertEqual(len(found["msps"]), 1)
        entry = found["msps"][0]
        self.assertEqual(entry["state"], run.MSP_PUSHED)
        self.assertEqual(entry["branch"], "mitosis/alpha")
        self.assertIsNone(entry["pull_request"])
        self.assertEqual(entry["title"], "feat(ship): fake title")
        self.assertEqual(entry["body"], FAKE_BODY)
        self.assertEqual(heads, ["refs/heads/mitosis/alpha"])
        self.assertEqual(found["attention"], [])
        held = mitosis._msp_summary(
            {"msps": [{"label": "alpha"}]},
            {"msps": {"0": {"state": run.MSP_COMMITTED, "ship": {"branch": "mitosis/alpha"}}}},
        )
        self.assertEqual((held[0]["title"], held[0]["body"]), (None, None))


COUNTING_ACCEPTANCE = r'''
import os
import sys

with open(sys.argv[1], "a") as handle:
    handle.write("%s %s\n" % (sys.argv[2], sys.argv[3]))
sys.exit(0 if os.path.exists("alpha.py") and "work" in open("alpha.py").read() else 1)
'''


def git_out(cwd, *args):
    return subprocess.run(
        ["git", "-C", cwd, *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **ISOLATED_GIT_ENV},
    ).stdout.strip()


VERIFYING_RENDER = r'''
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls"), "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv) + "\n")
with open(sys.argv[sys.argv.index("--fields") + 1], encoding="utf-8") as handle:
    verified = json.load(handle)["testing"][-1]["result"]
print(json.dumps({"title": "feat(ship): fake title", "body": %r %% verified}))
''' % VERIFIED_BODY

OPENING_PR = r'''
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "opened"), "a", encoding="utf-8") as handle:
    handle.write(sys.argv[1] + "\n")
print("https://example.invalid/pull/1")
'''


class ProvenHead(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.elsewhere = tempfile.mkdtemp()
        self.run_dir = os.path.join(self.elsewhere, "run")
        self.counter = os.path.join(self.elsewhere, "acceptance-calls")
        git_repo(self.root)
        remote = os.path.join(self.elsewhere, "remote.git")
        subprocess.run(
            ["git", "init", "-q", "--bare", remote],
            check=True,
            capture_output=True,
            env={**os.environ, **ISOLATED_GIT_ENV},
        )
        git_out(self.root, "remote", "add", "origin", remote)
        step = briefed(
            "alpha",
            files=["alpha.py", "alpha_check.txt"],
            acceptance=[{"file": "alpha_check.txt", "test": "property"}],
        )
        write(self.root, "items.json", items_document(self.root, [step]))

    def tearDown(self):
        subprocess.run(["rm", "-rf", self.root, self.elsewhere], check=True)

    def _invoke(self, *extra):
        with unittest.mock.patch.dict(os.environ, ISOLATED_GIT_ENV):
            return invoke(
                self.root,
                [
                    "--items",
                    "items.json",
                    "--run-dir",
                    self.run_dir,
                    "--feature-branch",
                    "feature",
                    "--dispatch-command",
                    command(self.elsewhere, "worker.py", HOLDING_WORKER, "{task}", "{session_id}", "{schema}"),
                    "--reask-command",
                    REASK_COMMAND,
                    "--acceptance-command",
                    command(
                        self.elsewhere,
                        "accept.py",
                        COUNTING_ACCEPTANCE,
                        shlex.quote(self.counter),
                        "{file}",
                        "{test}",
                    ),
                    *PR_FLAGS,
                    "--render-command",
                    executable(self.elsewhere, "pull-request", VERIFYING_RENDER),
                    *extra,
                ],
            )

    def _calls(self):
        if not os.path.exists(self.counter):
            return 0
        with open(self.counter, encoding="utf-8") as handle:
            return len(handle.read().splitlines())

    def _held(self):
        code, out, err = self._invoke("--no-push")
        self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
        self.assertGreater(self._calls(), 0)
        tree = read_json(os.path.join(self.run_dir, run.STATE_FILE))["worktrees"][0]["path"]
        return tree, git_out(tree, "rev-parse", "HEAD")

    def _new_commit(self, tree):
        write(tree, "alpha.py", "work\nmore work\n")
        git_out(tree, "commit", "-q", "-a", "-m", "more work")
        return git_out(tree, "rev-parse", "HEAD")

    def _lines(self, name):
        path = os.path.join(self.elsewhere, name)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return handle.read().splitlines()

    def _pushed(self, *extra):
        code, out, err = self._invoke(*extra)
        self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
        tree = read_json(os.path.join(self.run_dir, run.STATE_FILE))["worktrees"][0]["path"]
        return tree, git_out(tree, "rev-parse", "HEAD")

    def _origin(self):
        return git_out(self.root, "ls-remote", "origin", "refs/heads/mitosis/alpha").split("\t")[0]

    def _opener(self):
        return ("--pr-command", command(self.elsewhere, "opener.py", OPENING_PR, "{branch}"))

    def test_a_later_proven_head_is_pushed_and_its_body_rendered_again(self):
        tree, pushed = self._pushed()
        self.assertEqual(self._origin(), pushed)
        calls = self._calls()
        new_head = self._new_commit(tree)
        code, out, err = self._invoke("--resume", "--proven-head", "alpha=" + new_head)
        self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
        self.assertEqual(self._origin(), new_head)
        self.assertEqual(self._calls(), calls)
        found = json.loads(out.splitlines()[-1])["msps"][0]
        self.assertEqual(found["state"], run.MSP_PUSHED)
        self.assertEqual(
            found["body"],
            VERIFIED_BODY
            % ("Not run: the caller proved %s after mitosis gated %s" % (new_head[:12], pushed[:12])),
        )

    def test_a_later_proven_head_keeps_the_open_pull_request(self):
        tree, pushed = self._pushed(*self._opener())
        self.assertEqual(self._lines("worker/opened"), ["mitosis/alpha"])
        new_head = self._new_commit(tree)
        code, out, err = self._invoke(
            "--resume", "--proven-head", "alpha=" + new_head, *self._opener()
        )
        self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
        self.assertEqual(self._origin(), new_head)
        self.assertEqual(self._lines("worker/opened"), ["mitosis/alpha"])
        found = json.loads(out.splitlines()[-1])["msps"][0]
        self.assertEqual(found["state"], "shipped")
        self.assertEqual(found["pull_request"], "https://example.invalid/pull/1")
        self.assertEqual(
            found["body"],
            VERIFIED_BODY
            % ("Not run: the caller proved %s after mitosis gated %s" % (new_head[:12], pushed[:12])),
        )

    def test_the_pushed_head_as_proven_head_pushes_nothing(self):
        tree, pushed = self._pushed()
        before = read_json(os.path.join(self.run_dir, run.STATE_FILE))["msps"]["0"]
        self._new_commit(tree)
        code, out, err = self._invoke("--resume", "--proven-head", "alpha=" + pushed[:12])
        self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
        self.assertEqual(self._origin(), pushed)
        self.assertEqual(read_json(os.path.join(self.run_dir, run.STATE_FILE))["msps"]["0"], before)

    def test_a_proven_head_off_the_pushed_line_is_refused(self):
        tree, pushed = self._pushed()
        git_out(tree, "reset", "-q", "--hard", "HEAD~1")
        write(tree, "alpha.py", "other work\n")
        write(tree, "alpha_check.txt", "other check\n")
        git_out(tree, "add", "alpha.py", "alpha_check.txt")
        git_out(tree, "commit", "-q", "-m", "other work")
        sibling = git_out(tree, "rev-parse", "HEAD")
        code, out, err = self._invoke("--resume", "--proven-head", "alpha=" + sibling)
        self.assertEqual(code, mitosis.MSP_EXIT["ship-failed"], out + err)
        self.assertEqual(self._origin(), pushed)
        found = json.loads(out.splitlines()[-1])["msps"][0]
        self.assertEqual(found["state"], "ship-failed")
        self.assertEqual(
            found["reason"],
            "mitosis/alpha is at %s, which does not descend from %s that mitosis pushed; "
            "mitosis never force-pushes, so it refused to push it" % (sibling[:12], pushed[:12]),
        )
        record = read_json(os.path.join(self.run_dir, run.STATE_FILE))["msps"]["0"]
        self.assertEqual(record["ship"]["pushed"], pushed)

    def _no_build(self):
        branches = git_out(self.root, "for-each-ref", "--format=%(refname)", "refs/heads/mitosis/")
        self.assertEqual(branches, "")
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "trees")))

    def test_proven_head_ships_without_gating(self):
        tree, _ = self._held()
        calls = self._calls()
        new_head = self._new_commit(tree)
        code, out, err = self._invoke("--resume", "--proven-head", "alpha=" + new_head)
        self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
        self.assertEqual(self._calls(), calls)
        found = json.loads(out.splitlines()[-1])
        self.assertEqual(found["msps"][0]["state"], run.MSP_PUSHED)

    def test_proven_head_body_names_both_commits(self):
        tree, gated = self._held()
        new_head = self._new_commit(tree)
        code, out, err = self._invoke("--resume", "--proven-head", "alpha=" + new_head)
        self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
        testing = read_json(os.path.join(self.run_dir, "ship", "msp-0", "fields.json"))["testing"]
        self.assertEqual(
            testing[-1],
            {
                "check": "Proven head",
                "result": "Not run: the caller proved %s after mitosis gated %s"
                % (new_head[:12], gated[:12]),
            },
        )

    def test_a_different_head_gates_again(self):
        tree, gated = self._held()
        calls = self._calls()
        self._new_commit(tree)
        code, out, err = self._invoke("--resume", "--proven-head", "alpha=" + gated)
        self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
        self.assertGreater(self._calls(), calls)

    def test_no_recorded_gate_gates(self):
        tree, gated = self._held()
        calls = self._calls()
        path = os.path.join(self.run_dir, run.STATE_FILE)
        state = read_json(path)
        record = state["msps"]["0"]
        ungated = {**state, "msps": {**state["msps"], "0": {**record, "gate": None}}}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(ungated, handle)
        code, out, err = self._invoke("--resume", "--proven-head", "alpha=" + gated)
        self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
        self.assertGreater(self._calls(), calls)

    def a_label_holding_a_space_or_an_equals_sign_splits_at_the_last_equals_sign(self):
        self.assertEqual(
            mitosis.PROVEN_HEAD.fullmatch("add login=abcdef1").groups(), ("add login", "abcdef1")
        )
        self.assertEqual(mitosis.PROVEN_HEAD.fullmatch("a=b=abcdef1").groups(), ("a=b", "abcdef1"))

    def test_refuses_a_malformed_proven_head(self):
        code, out, err = self._invoke("--proven-head", "core-abc")
        self.assertEqual(code, mitosis.EXIT_USAGE, out + err)
        self._no_build()

    def test_refuses_an_unknown_msp_label(self):
        commit = git_out(self.root, "rev-parse", "HEAD")
        code, out, err = self._invoke("--proven-head", "nosuch=" + commit)
        self.assertEqual(code, mitosis.EXIT_USAGE, out + err)
        self.assertEqual(
            err.splitlines(),
            [
                "mitosis: --proven-head names MSP nosuch, which the plan does not hold",
                "exit 2: the flags did not parse",
            ],
        )
        self._no_build()
        self.assertEqual(self._calls(), 0)


class RenderPreflight(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.elsewhere = tempfile.mkdtemp()
        self.run_dir = os.path.join(self.elsewhere, "run")
        git_repo(self.root)
        self.remote = os.path.join(self.elsewhere, "remote.git")
        subprocess.run(
            ["git", "init", "-q", "--bare", self.remote],
            check=True,
            capture_output=True,
            env={**os.environ, **ISOLATED_GIT_ENV},
        )
        git_out(self.root, "remote", "add", "origin", self.remote)
        step = briefed(
            "alpha",
            files=["alpha.py", "alpha_check.txt"],
            acceptance=[{"file": "alpha_check.txt", "test": "property"}],
        )
        write(self.root, "items.json", items_document(self.root, [step]))

    def tearDown(self):
        subprocess.run(["rm", "-rf", self.root, self.elsewhere], check=True)

    def _invoke(self, worker, *extra):
        with unittest.mock.patch.dict(os.environ, ISOLATED_GIT_ENV):
            return invoke(
                self.root,
                [
                    "--items",
                    "items.json",
                    "--run-dir",
                    self.run_dir,
                    "--feature-branch",
                    "feature",
                    "--dispatch-command",
                    command(self.elsewhere, "worker.py", worker, "{task}", "{session_id}", "{schema}"),
                    "--reask-command",
                    REASK_COMMAND,
                    "--acceptance-command",
                    command(
                        self.elsewhere,
                        "accept.py",
                        COUNTING_ACCEPTANCE,
                        shlex.quote(os.path.join(self.elsewhere, "acceptance-calls")),
                        "{file}",
                        "{test}",
                    ),
                    *extra,
                ],
            )

    def _calls(self):
        with open(os.path.join(self.elsewhere, "calls"), encoding="utf-8") as handle:
            return handle.read().splitlines()

    def refuses_before_any_worker(self):
        header = (
            "--pr-type",
            "feat",
            "--pr-subject",
            "hand facts to render",
            "--pr-why",
            "Bodies are composed twice.",
        )
        impact = ("--pr-impact", "Every new body changes shape.")
        for flags, expected in (
            (header, "--pr-impact"),
            (
                header
                + impact
                + ("--render-command", os.path.join(self.elsewhere, "absent", "pull-request")),
                "install pull-request 0.3.0 or later",
            ),
            (
                header
                + impact
                + (
                    "--render-command",
                    refusing_render(
                        self.elsewhere, "old-render", 'pull-request render: unknown flag "--fields"'
                    ),
                ),
                "older than 0.3.0",
            ),
            (
                header
                + impact
                + (
                    "--render-command",
                    refusing_render(self.elsewhere, "refusing-render", "--change: a value is empty"),
                ),
                "MSP alpha: ",
            ),
        ):
            for mode in (("--plan-only",), ()):
                with self.subTest(expected=expected, mode=mode):
                    code, out, err = self._invoke(MARKING_WORKER, *mode, *flags)
                    self.assertEqual(code, 3, out + err)
                    self.assertIn(expected, err)
                    self.assertFalse(
                        os.path.exists(os.path.join(self.elsewhere, "worker", "spawned"))
                    )
                    self.assertFalse(os.path.exists(os.path.join(self.run_dir, "plan.json")))

    def an_accepting_render_lets_the_plan_run(self):
        code, out, err = self._invoke(
            MARKING_WORKER,
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
            fake_render(self.elsewhere),
        )
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self._calls()), 1)
        testing = read_json(os.path.join(self.elsewhere, "fields.copy.json"))["testing"]
        self.assertEqual(
            [row["check"] for row in testing[-2:]], ["Acceptance gate", "Proven head"]
        )
        self.assertTrue(os.path.isfile(os.path.join(self.run_dir, "probe-0.json")))
        code, out, err = self._invoke(
            MARKING_WORKER,
            "--plan-only",
            "--pr-why",
            "Bodies are composed twice.",
            "--pr-impact",
            "Every new body changes shape.",
        )
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self._calls()), 1)

    def a_resume_carries_the_recorded_blast_radius(self):
        render = fake_render(self.elsewhere)
        header = (
            "--pr-type",
            "feat",
            "--pr-scope",
            "ship",
            "--pr-subject",
            "hand facts to render",
            "--pr-why",
            "Bodies are composed twice.",
            "--render-command",
            render,
        )
        code, out, err = self._invoke(
            HOLDING_WORKER,
            *header,
            "--pr-door",
            "two-way",
            "--pr-blast-radius",
            "Ship only.",
            "--no-push",
        )
        self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
        code, out, err = self._invoke(HOLDING_WORKER, *header, "--resume")
        self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
        self.assertEqual(json.loads(out.splitlines()[-1])["msps"][0]["state"], run.MSP_PUSHED)
        shipped = read_json(os.path.join(self.run_dir, "ship", "msp-0", "fields.json"))
        self.assertEqual(shipped["impact"], ["Ship only."])
        probed = read_json(os.path.join(self.run_dir, "probe-0.json"))
        self.assertEqual(probed["impact"], ["Ship only."])

    def the_probe_runs_without_a_timeout(self):
        args = mitosis.build_parser().parse_args(
            [
                "--items",
                "items.json",
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
                fake_render(self.elsewhere),
            ]
        )
        mitosis.refuse_unrenderable(args, core.plan([briefed("alpha")]), self.run_dir)
        with open(os.path.join(self.elsewhere, "calls"), encoding="utf-8") as handle:
            self.assertEqual(len(handle.read().splitlines()), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.run_dir, "probe-0.json")))

    def a_typed_run_without_a_subject_lands_typed_commits(self):
        code, out, err = self._invoke(
            HOLDING_WORKER, "--pr-type", "feat", "--pr-scope", "ship", "--no-push"
        )
        self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
        branch = read_json(os.path.join(self.run_dir, run.STATE_FILE))["worktrees"][0]["branch"]
        self.assertEqual(git_out(self.root, "log", "-1", "--format=%s", branch).strip(), "feat(ship): alpha")

    def pipeline_flags_ship_what_render_printed(self):
        code, out, err = self._invoke(
            HOLDING_WORKER,
            "--pr-type",
            "feat",
            "--pr-scope",
            "ship",
            "--pr-subject",
            "hand facts to render",
            "--pr-why",
            "Bodies are composed twice.",
            "--pr-why",
            "Reviewers skip them.",
            "--pr-door",
            "two-way",
            "--pr-blast-radius",
            "Ship only.",
            "--render-command",
            fake_render(self.elsewhere),
        )
        self.assertEqual(code, 0, out + err)
        entry = json.loads(out.splitlines()[-1])["msps"][0]
        self.assertEqual(entry["title"], "feat(ship): fake title")
        self.assertEqual(entry["body"], FAKE_BODY)
        path = os.path.join(self.run_dir, "ship", "msp-0", "fields.json")
        fields = read_json(path)
        self.assertEqual(fields["problem"], ["Bodies are composed twice.", "Reviewers skip them."])
        self.assertEqual(fields["impact"], ["Ship only."])
        with open(path, encoding="utf-8") as handle:
            self.assertNotIn("two-way", handle.read())
        for name in ("why_fault", "subject_fault", "blast_radius_fault", "refuse_unfit_titles"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(mitosis, name))
        for name in (
            "value_fault",
            "house_value",
            "composed_fault",
            "composed_value",
            "title_fault",
            "sentence_shaped",
            "receipt_token",
            "pull_request_title",
            "SCOPE_PATTERN",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(run, name))

    def six_why_lines_are_accepted(self):
        whys = tuple(
            word
            for why in ("One.", "Two.", "Three.", "Four.", "Five.", "Six.")
            for word in ("--pr-why", why)
        )
        code, out, err = self._invoke(
            MARKING_WORKER,
            "--plan-only",
            "--pr-type",
            "feat",
            "--pr-subject",
            "hand facts to render",
            *whys,
            "--pr-impact",
            "Every new body changes shape.",
            "--render-command",
            fake_render(self.elsewhere),
        )
        self.assertEqual(code, 0, out + err)
        self.assertEqual(
            read_json(os.path.join(self.elsewhere, "fields.copy.json"))["problem"],
            ["One. Two. Three. Four. Five. Six."],
        )


class GateExit(unittest.TestCase):
    PLAN = {
        "plan_id": "p",
        "lanes": [{"msp": 0, "steps": ["impl"]}],
        "msps": [{"label": "impl", "steps": ["impl"]}],
        "items": [{"name": "impl", "files": ["impl.py"], "acceptance": []}],
    }

    def _state(self, **msp):
        return {
            "worktrees": [{"msp": 0}],
            "lanes": {"0": {"state": "ok"}},
            "msps": {"0": {"state": "shipped", "reconcile": {"undeclared": [], "unwritten": [],
                                                             "crossing": [], "fatal": False}, **msp}},
        }

    def a_shipped_msp_with_an_inert_property_exits_twenty(self):
        state = self._state(gate={"outcome": "inert", "counts": {}, "blocks": True})
        self.assertEqual(mitosis.exit_code(state, self.PLAN), 20)
        self.assertIn("proves nothing", mitosis.EXIT_MEANING[20])

    def a_shipped_msp_the_gate_could_not_judge_exits_twenty_one(self):
        self.assertEqual(
            mitosis.exit_code(self._state(gate={"outcome": "inconclusive", "blocks": True}), self.PLAN), 21
        )
        self.assertEqual(
            mitosis.exit_code(self._state(gate=None, gate_error="no runner"), self.PLAN), 21
        )

    def a_shipped_msp_that_wrote_another_msps_file_exits_seven(self):
        crossing = {"undeclared": ["b.txt"], "unwritten": [], "crossing": [{"path": "b.txt", "label": "beta"}], "fatal": True}
        state = self._state(gate={"outcome": "pass", "blocks": False}, reconcile=crossing)
        self.assertEqual(mitosis.exit_code(state, self.PLAN), mitosis.EXIT_CROSSING)
        self.assertIn("another MSP owns", mitosis.EXIT_MEANING[mitosis.EXIT_CROSSING])
        undeclared = {"undeclared": ["stray.txt"], "unwritten": [], "crossing": [], "fatal": False}
        self.assertEqual(
            mitosis.exit_code(self._state(gate={"outcome": "pass", "blocks": False}, reconcile=undeclared), self.PLAN),
            mitosis.EXIT_RECONCILE,
        )

    def a_lane_that_changed_a_siblings_file_exits_eight(self):
        state = self._state(gate={"outcome": "pass", "blocks": False})
        state = {
            **state,
            "lanes": {"0": {"state": "ok", "foreign_writes": [{"path": "impl.py", "owner": 0, "suspects": [0]}]}},
        }
        self.assertEqual(mitosis.exit_code(state, self.PLAN), mitosis.EXIT_FOREIGN)
        self.assertIn("another Lane", mitosis.EXIT_MEANING[mitosis.EXIT_FOREIGN])

    def a_referee_reverted_msp_exits_nine(self):
        referee = {"undeclared": [], "unwritten": [], "crossing": [],
                   "referee": ["tests/conftest.py"], "fatal": True}
        state = self._state(state="referee-reverted", reconcile=referee)
        self.assertEqual(mitosis.exit_code(state, self.PLAN), 9)
        self.assertEqual(
            mitosis.EXIT_MEANING[9],
            "a Worker wrote a referee path; mitosis restored it and held its MSP back",
        )

    def a_held_back_msp_exits_with_its_gates_flag(self):
        for outcome, code in (("weak", 24), ("inert", 20), ("inconclusive", 21)):
            with self.subTest(outcome=outcome):
                state = self._state(state="gate-failed", gate={"outcome": outcome, "blocks": True})
                self.assertEqual(mitosis.exit_code(state, self.PLAN), code)
        errored = self._state(state="gate-failed", gate=None, gate_error="no runner")
        self.assertEqual(mitosis.exit_code(errored, self.PLAN), 21)
        self.assertEqual(
            mitosis.EXIT_MEANING[24],
            "an acceptance property passes against a mutant of the work, so it cannot tell "
            "working code from broken code",
        )

    def a_proven_head_over_a_weak_gate_exits_zero(self):
        state = self._state(gate={"outcome": "weak", "blocks": True}, proven_head="a" * 40)
        self.assertEqual(mitosis.exit_code(state, self.PLAN), mitosis.EXIT_SHIPPED)

    def a_property_with_no_line_to_mutate_is_named_for_attention(self):
        gate = {
            "outcome": "pass",
            "commit": "c" * 40,
            "properties": [
                {
                    "step": "impl",
                    "file": "tests/test_impl.py",
                    "test": "Impl.works",
                    "outcome": "pass",
                    "reason": None,
                    "mutants": {"tried": 0, "killed": None},
                }
            ],
        }
        state = self._state(gate=gate)
        msps = mitosis._msp_summary(self.PLAN, state)
        self.assertEqual(msps[0]["gate"]["commit"], "c" * 40)
        self.assertEqual(
            msps[0]["gate"]["properties"],
            [
                {
                    "file": "tests/test_impl.py",
                    "test": "Impl.works",
                    "step": "impl",
                    "outcome": "pass",
                    "reason": None,
                    "mutants": {"tried": 0, "killed": None},
                }
            ],
        )
        self.assertIn(
            "MSP impl: tests/test_impl.py::Impl.works passed on red and green alone; its Step "
            "added no line to mutate",
            mitosis._attention(self.PLAN, [], msps),
        )

    def a_shipped_msp_whose_gate_passed_exits_zero(self):
        state = self._state(gate={"outcome": "pass", "counts": {}, "blocks": False})
        self.assertEqual(mitosis.exit_code(state, self.PLAN), mitosis.EXIT_SHIPPED)
        passing = self._state(gate={"outcome": "not-applicable", "blocks": False})
        self.assertEqual(mitosis.exit_code(passing, self.PLAN), mitosis.EXIT_SHIPPED)
        self.assertEqual(mitosis.findings(passing, self.PLAN), [])

    PAIR = {
        "plan_id": "p",
        "lanes": [{"msp": 0, "steps": ["alpha"]}, {"msp": 1, "steps": ["beta"]}],
        "msps": [{"label": "alpha", "steps": ["alpha"]}, {"label": "beta", "steps": ["beta"]}],
        "items": [
            {"name": "alpha", "files": ["a.py"], "acceptance": []},
            {"name": "beta", "files": ["b.py"], "acceptance": []},
        ],
    }

    def _pair(self, first, second):
        return {
            "worktrees": [{"msp": 0}, {"msp": 1}],
            "lanes": {"0": {"state": "ok"}, "1": {"state": "ok"}},
            "msps": {"0": first, "1": second},
        }

    def every_msps_findings_are_listed_and_the_worst_decides_the_exit(self):
        clean = {"undeclared": [], "unwritten": [], "crossing": [], "fatal": False}
        held = {
            "state": "gate-failed",
            "reason": "held",
            "reconcile": clean,
            "gate": {
                "outcome": "inconclusive",
                "blocks": True,
                "properties": [
                    {"file": "tests/test_a.py", "test": "A.works", "outcome": "inconclusive", "reason": "exit 2"},
                    {"file": "tests/test_a.py", "test": "A.other", "outcome": "pass", "reason": None},
                ],
            },
        }
        stray = {"state": "committed", "reconcile": {**clean, "undeclared": ["stray.txt"], "unwritten": ["b.py"]}}
        gate = {
            "msp": "alpha",
            "lane": None,
            "kind": "gate-inconclusive",
            "exit": 21,
            "detail": "tests/test_a.py::A.works: exit 2",
        }
        undeclared = {"msp": "beta", "lane": None, "kind": "undeclared", "exit": 6, "detail": "stray.txt"}
        unwritten = {"msp": "beta", "lane": None, "kind": "unwritten", "exit": 6, "detail": "b.py"}
        forward = self._pair(held, stray)
        self.assertEqual(mitosis.findings(forward, self.PAIR), [gate, undeclared, unwritten])
        self.assertEqual(mitosis.exit_code(forward, self.PAIR), 21)
        backward = self._pair(stray, held)
        self.assertEqual(mitosis.exit_code(backward, self.PAIR), 21)

    def lanes_come_before_msps_and_a_missing_record_is_incomplete(self):
        state = {
            "worktrees": [{"msp": 0}],
            "lanes": {"1": {"state": "failed", "reason": "the Worker said failed"}},
            "msps": {"0": {"state": "blocked", "reason": "a predecessor failed"}},
        }
        self.assertEqual(
            mitosis.findings(state, self.PAIR),
            [
                {"msp": "alpha", "lane": 0, "kind": "incomplete", "exit": 4, "detail": "Lane 0 has no record"},
                {"msp": "beta", "lane": 1, "kind": "failed", "exit": 10, "detail": "the Worker said failed"},
                {"msp": "alpha", "lane": None, "kind": "blocked", "exit": 11, "detail": "a predecessor failed"},
                {"msp": "beta", "lane": None, "kind": "incomplete", "exit": 4, "detail": "MSP beta has no record"},
            ],
        )
        self.assertEqual(mitosis.exit_code(state, self.PAIR), 10)


class CouplingReport(unittest.TestCase):
    ITEMS = [
        briefed("seven", files=["db/migrations/0007_x.py"]),
        briefed("eight", files=["db/migrations/0008_y.py"]),
        briefed("plain-a", files=["src/a.py"]),
        briefed("plain-b", files=["src/b.py"]),
    ]

    def _plan_only(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "items.json", items_document(root, self.ITEMS))
            write(
                root,
                "graph.json",
                json.dumps(
                    {
                        "nodes": [{"id": "a", "source_file": "src/a.py"}, {"id": "b", "source_file": "src/b.py"}],
                        "links": [{"source": "a", "target": "b"}],
                    }
                ),
            )
            write(root, "no-history.jsonl", "")
            with unittest.mock.patch.object(
                core, "DEFAULT_TRAJECTORY_STORE", os.path.join(root, "no-history.jsonl")
            ):
                return invoke(
                    root,
                    ["--items", "items.json", "--graph", "graph.json", "--plan-only",
                     "--run-dir", os.path.join(root, "run")],
                )

    def a_strong_signal_awaits_a_verdict_and_an_import_link_is_named_before_the_build(self):
        code, out, err = self._plan_only()
        self.assertEqual(code, mitosis.EXIT_SHIPPED, err + out)
        body = section(out, "Coupling")
        self.assertIn(
            "seven and eight (Lanes 0, 1) run in parallel awaiting the orchestrator's verdict on "
            "same-migration-app:db, default serialize; add an after edge to serialize them",
            body,
        )
        self.assertIn(
            "plain-a and plain-b (Lanes 2, 3) run in parallel with only import-adjacent between them; "
            "add an after edge if one needs the other's output",
            body,
        )
        found = json.loads(out.splitlines()[-1])
        self.assertEqual(found["coupling"]["ordered"], [])
        self.assertEqual(
            found["coupling"]["parallel"],
            [
                {"steps": ["seven", "eight"], "signals": ["same-migration-app:db"]},
                {"steps": ["plain-a", "plain-b"], "signals": ["import-adjacent"]},
            ],
        )
        self.assertEqual(
            found["attention"],
            [
                "1 pair of Steps run in parallel with only import-adjacent between them: plain-a+plain-b; "
                "add an after edge where one needs the other's output",
                "1 pair of Steps await the orchestrator's verdict on same-migration-app:db, default "
                "serialize: seven+eight; add an after edge to serialize them",
            ],
        )

    def many_parallel_pairs_are_named_in_one_line_with_the_rest_counted(self):
        review = [
            {"steps": ["a%d" % n, "b%d" % n], "signals": ["import-adjacent"], "default": "parallel"}
            for n in range(10)
        ]
        lines = mitosis._coupling_attention(review)
        self.assertEqual(len(lines), 1)
        self.assertIn("10 pairs of Steps run in parallel with only import-adjacent", lines[0])
        self.assertIn("and 2 more", lines[0])

    def serialize_pairs_await_a_verdict_in_their_own_line(self):
        review = [
            {"steps": ["a", "b"], "signals": ["shared-risk-marker:auth"], "default": "serialize"},
            {"steps": ["a", "c"], "signals": ["shared-risk-marker:auth"], "default": "serialize"},
            {"steps": ["d", "e"], "signals": ["shared-risk-marker:auth"], "default": "parallel"},
        ]
        self.assertEqual(
            mitosis._coupling_attention(review),
            [
                "1 pair of Steps run in parallel with only shared-risk-marker:auth between them: d+e; "
                "add an after edge where one needs the other's output",
                "2 pairs of Steps await the orchestrator's verdict on shared-risk-marker:auth, default "
                "serialize: a+b, a+c; add an after edge to serialize them",
            ],
        )

    def a_pair_with_an_unknown_default_is_refused_by_name(self):
        with self.assertRaises(ValueError) as caught:
            mitosis._coupling_attention([{"steps": ["a", "b"], "signals": ["import-adjacent"], "default": "later"}])
        self.assertIn("a+b", str(caught.exception))
        self.assertIn("'later'", str(caught.exception))


class ReportSections(unittest.TestCase):
    def the_split_shape_section_prints_every_scalar(self):
        with tempfile.TemporaryDirectory() as root:
            write(
                root,
                "items.json",
                items_document(root, [briefed("alpha"), briefed("beta", after=["alpha"]), briefed("gamma")]),
            )
            code, out, err = invoke(
                root,
                ["--items", "items.json", "--plan-only", "--run-dir", os.path.join(root, "run")],
            )
            self.assertEqual(code, mitosis.EXIT_SHIPPED, err)
            body = section(out, "Split shape")
            self.assertEqual(len(body), 1)
            scored = shape.scalars(read_json(os.path.join(root, "items.json")))
            for key in shape.SCALAR_KEYS:
                self.assertIn(key, scored)
                self.assertIn("%s %s" % (key, mitosis._number(scored[key])), body[0])
            self.assertIn("items file", section(out, "Decisions")[0])
            self.assertTrue(os.path.isfile(os.path.join(root, "run", mitosis.ITEMS_FILE)))
            self.assertTrue(os.path.isfile(os.path.join(root, "run", run.PLAN_FILE)))

    def every_finding_precedes_the_scalars_so_a_cause_is_read_first(self):
        items = [
            briefed("a", contract_group="g", type="contract"),
            briefed("b", contract_group="g", after=["a"]),
            briefed("c", contract_group="g", after=["b"]),
        ]
        lines = mitosis.shape_lines(items)
        found = shape.findings(items)
        self.assertTrue(found)
        self.assertEqual(len(lines), len(found) + 1)
        for finding, line in zip(found, lines[:-1]):
            self.assertEqual(line, "%s: %s" % (finding["kind"], finding["detail"]))
        self.assertTrue(lines[-1].startswith("steps "))

    def the_report_order_places_the_new_sections(self):
        self.assertEqual(
            mitosis.REPORT_SECTIONS.index("Split shape") + 1,
            mitosis.REPORT_SECTIONS.index("Split quality"),
        )
        self.assertIn("Decisions", mitosis.REPORT_SECTIONS)

    def lane_width_is_the_parallelism_scalar(self):
        items = [
            briefed("a"),
            briefed("b", after=["a"]),
            briefed("c", after=["a"]),
            briefed("d"),
        ]
        plan = core.plan(items)
        self.assertEqual(mitosis.lane_width(plan), shape.scalars(items)["parallelism"])
        self.assertEqual(mitosis.lane_width(plan), 2)
        self.assertEqual(mitosis.lane_width({}), 0)


class BriefStageReport(unittest.TestCase):
    def a_run_with_nothing_to_brief_still_reports_what_it_reused(self):
        lines = mitosis.brief_lines({"written": [], "reused": ["a", "b"], "errors": []})
        self.assertIn("2 briefs reused", lines[0])
        self.assertIn("0 briefs written", lines[0])

    def a_brief_that_had_to_be_asked_again_is_named_in_the_report(self):
        lines = mitosis.brief_lines(
            {"written": ["a", "b"], "reused": [], "reasked": ["b"], "errors": []}
        )
        self.assertIn(
            "1 brief re-asked in the same session after a reply that failed h10c-brief-return: b",
            lines,
        )

    def a_run_where_every_brief_returned_cleanly_says_nothing_about_retries(self):
        lines = mitosis.brief_lines(
            {"written": ["a"], "reused": [], "reasked": [], "errors": []}
        )
        self.assertTrue(all("re-asked" not in line for line in lines))


class CoverageRendering(unittest.TestCase):
    def the_coverage_map_is_rendered_exactly_once_in_a_report(self):
        record = {
            "coverage": {
                "mode": "headings",
                "sections": [
                    {"id": "1", "title": "One", "heading": "1. One", "line": 1},
                    {"id": "2", "title": "Two", "heading": "2. Two", "line": 9},
                ],
                "uncovered": [{"id": "2", "title": "Two", "heading": "2. Two", "line": 9}],
                "unmatched_claims": [],
            },
            "source": {"path": "docs/spec.md", "sha256": "0" * 64},
            "constraints": [],
            "errors": [],
            "counts": {},
        }
        lines = mitosis.coverage_lines({"items": [], "source": None}, record, ".")
        self.assertIn("1 unclaimed", lines[0])
        self.assertEqual(sum(1 for line in lines if "unclaimed: 2 Two" in line), 1)
        printed = decompose.report(record)
        self.assertEqual([line for line in printed if "unclaimed" in line], [])


class LaneCycleFusion(unittest.TestCase):
    CYCLIC = [
        {"name": "core", "task": "t", "files": ["core.py", "shared.py"], "source": None,
         "acceptance": []},
        {"name": "mid", "task": "t", "files": ["mid.py"], "source": None, "acceptance": [],
         "after": ["core"]},
        {"name": "tail", "task": "t", "files": ["tail.py", "shared.py"], "source": None,
         "acceptance": [], "after": ["mid"]},
    ]
    CLEAN = [
        {"name": "a", "task": "t", "files": ["a.py"], "source": None, "acceptance": []},
        {"name": "b", "task": "t", "files": ["b.py"], "source": None, "acceptance": [],
         "after": ["a"]},
    ]

    def a_cycle_spanning_msps_stays_two_msps_and_is_never_refused(self):
        self.assertEqual(mitosis.findings(None, {"items": self.CYCLIC}), [])
        self.assertEqual(core.msp_items(self.CYCLIC), ((0, 2), (1,)))
        self.assertEqual(core.lane_items(self.CYCLIC), ((0, 2), (1,)))

    def a_plan_without_a_cycle_keeps_its_msps_apart(self):
        self.assertEqual(mitosis.findings(None, {"items": self.CLEAN}), [])
        self.assertEqual(len(core.msp_items(self.CLEAN)), 2)

    def a_manifest_that_can_export_nothing_is_reported_not_refused(self):
        items = [
            {"name": "gregorian", "task": "t", "files": ["pkg/__init__.py", "pkg/g.py"],
             "source": None, "acceptance": []},
            {"name": "parse", "task": "t", "files": ["pkg/parse.py"], "source": None,
             "acceptance": [], "after": ["gregorian"]},
        ]
        self.assertEqual(mitosis.findings(None, {"items": items}), [])
        self.assertEqual(
            mitosis.empty_manifests(items),
            [
                {"owner": 0, "manifest": "pkg/__init__.py", "siblings": 1, "reached": 0,
                 "missing": ["parse"]}
            ],
        )
        self.assertEqual(
            [line for line in mitosis.shape_lines(items) if "manifest-exports-nothing" in line], []
        )

    def a_manifest_written_last_is_never_refused(self):
        items = [
            {"name": "parse", "task": "t", "files": ["pkg/parse.py"], "source": None,
             "acceptance": []},
            {"name": "surface", "task": "t", "files": ["pkg/__init__.py"], "source": None,
             "acceptance": [], "after": ["parse"]},
        ]
        self.assertEqual(mitosis.findings(None, {"items": items}), [])
        self.assertEqual(mitosis.empty_manifests(items), [])

    def a_cycle_inside_one_msp_is_contracted_and_never_refused(self):
        items = [{**step, "msp": "m"} for step in self.CYCLIC]
        self.assertEqual(mitosis.findings(None, {"items": items}), [])
        self.assertEqual(
            [line for line in mitosis.shape_lines(items) if line.startswith("lane-cycle")], []
        )

    def an_outcome_line_states_the_meaning_of_every_exit_code(self):
        self.assertNotIn(23, mitosis.EXIT_MEANING)
        for code, meaning in mitosis.EXIT_MEANING.items():
            with self.subTest(code=code):
                lines = mitosis.outcome_lines(None, None, "/run", code)
                if code == mitosis.EXIT_SHIPPED:
                    self.assertIn("no brief was bought", lines[-1])
                else:
                    self.assertIn(meaning, lines[-1])
                self.assertTrue(lines[-1].startswith("exit %d:" % code))


def shared_lane_items(count):
    return [briefed("s%d" % i, files=["shared.py", "f%d.py" % i]) for i in range(count)]


class SessionReport(unittest.TestCase):
    def _plan_only(self, items, *extra):
        with tempfile.TemporaryDirectory() as root:
            write(root, "items.json", items_document(root, items))
            write(root, "no-history.jsonl", "")
            with unittest.mock.patch.object(
                core, "DEFAULT_TRAJECTORY_STORE", os.path.join(root, "no-history.jsonl")
            ):
                return invoke(
                    root,
                    ["--items", "items.json", "--plan-only", "--run-dir", os.path.join(root, "run"), *extra],
                )

    def the_plan_only_report_shows_a_shared_lane_built_as_sessions(self):
        code, out, err = self._plan_only(shared_lane_items(3))
        self.assertEqual(code, mitosis.EXIT_SHIPPED, err + out)
        body = section(out, "Split quality")
        self.assertIn(
            "Lanes 0 to 2 are the Steps of one Lane of MSP s0, each built in its own Worker "
            "session in a row on its branch and landing its own commit",
            body,
        )
        lane_lines = [line for line in body if line.startswith("Lane ")]
        self.assertEqual(len(lane_lines), 3)
        for index, line in enumerate(lane_lines):
            self.assertTrue(line.endswith(", cost 2, session %d of 3: s%d" % (index + 1, index)), line)
            self.assertNotIn("tokens", line)
        found = json.loads(out.splitlines()[-1])
        self.assertEqual([lane["session"] for lane in found["lanes"]], [[1, 3], [2, 3], [3, 3]])
        self.assertEqual(found["attention"], [])

    def a_lane_that_fits_reports_one_session_and_no_split(self):
        code, out, err = self._plan_only([briefed("s0"), briefed("s1")])
        self.assertEqual(code, mitosis.EXIT_SHIPPED, err + out)
        body = section(out, "Split quality")
        self.assertIn("every Lane holds one Step and runs as one Worker session", body)
        found = json.loads(out.splitlines()[-1])
        self.assertEqual([lane["session"] for lane in found["lanes"]], [None, None])

    def a_shared_lane_reports_one_session_per_step(self):
        plan = core.plan(
            [
                briefed("s0", files=["shared.py", "f0.py"], msp="m"),
                briefed("s1", files=["shared.py", "f1.py"], msp="m"),
                briefed("s2", files=["shared.py", "f2.py"], msp="m"),
            ]
        )
        self.assertEqual(
            mitosis.session_lines(plan),
            (
                "Lanes 0 to 2 are the Steps of one Lane of MSP m, each built in its own Worker "
                "session in a row on its branch and landing its own commit",
            ),
        )

    def lone_steps_report_one_session_each(self):
        plan = core.plan([briefed("s0"), briefed("s1")])
        self.assertEqual(
            mitosis.session_lines(plan),
            ("every Lane holds one Step and runs as one Worker session",),
        )

    def a_coalesce_cap_below_one_is_a_usage_error(self):
        code, _, err = self._plan_only(shared_lane_items(1), "--coalesce-max", "0")
        self.assertEqual(code, mitosis.EXIT_USAGE)
        self.assertIn("--coalesce-max must be at least 1", err)


class GateClassRefusals(unittest.TestCase):
    PLAN = {
        "items": [
            {
                "name": "calc",
                "acceptance": [
                    {"file": "tests/test_calc.py", "test": "Calc.adds"},
                    {"file": "tests/test_calc.py", "test": "Calc.exists"},
                ],
            }
        ]
    }

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="mitosis-classes-")
        self.addCleanup(shutil.rmtree, self.folder, True)

    def _classes(self, payload):
        path = os.path.join(self.folder, "classes.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(payload if isinstance(payload, str) else json.dumps(payload))
        return path

    def _refusal(self, payload):
        with self.assertRaises(mitosis.Refusal) as caught:
            mitosis.gate_classes(self._classes(payload), self.PLAN)
        return str(caught.exception)

    def an_accepted_file_returns_its_entries_and_digest(self):
        entry = {"test": "tests/test_calc.py::Calc.exists", "class": "removed", "reason": "duplicate"}
        path = self._classes([entry])
        classes, digest = mitosis.gate_classes(path, self.PLAN)
        self.assertEqual(classes, (entry,))
        self.assertEqual(digest, run.sha256_file(path))

    def no_file_means_no_classes(self):
        self.assertEqual(mitosis.gate_classes(None, self.PLAN), (None, None))

    def each_malformed_entry_is_refused_by_its_name(self):
        exists = "tests/test_calc.py::Calc.exists"
        cases = (
            (
                [{"test": exists, "class": "skipped", "reason": "r"}],
                'entry 1 ("%s") has class "skipped", which is none of' % exists,
            ),
            ([{"test": exists, "class": "removed", "reason": "  "}], "has no reason"),
            (
                [{"test": exists, "class": "removed", "reason": "one\ntwo"}],
                "has a reason of more than one line",
            ),
            (
                [{"test": exists, "class": "removed", "reason": "r" * 501}],
                "has a reason longer than 500 characters",
            ),
            (
                [
                    {"test": exists, "class": "removed", "reason": "r"},
                    {"test": exists, "class": "not-applicable", "reason": "r"},
                ],
                'entry 2 ("%s") names a property an earlier entry already classes' % exists,
            ),
            (
                [{"test": exists, "class": "removed"}],
                "is not an object holding exactly the keys class, reason, test",
            ),
            (["text"], "entry 1 is not an object"),
        )
        for payload, expected in cases:
            with self.subTest(expected=expected):
                self.assertIn(expected, self._refusal(payload))

    def a_file_that_is_not_a_list_is_refused(self):
        self.assertIn("does not hold a JSON list", self._refusal({"test": "x"}))
        self.assertIn("is not UTF-8 JSON", self._refusal("[{"))


MITOSIS_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mitosis.py")

SCRATCH_CALL_LIMIT = 60

SCRATCH_RUN_LIMIT = 300

SCRATCH_STRIPPED = ("CLAUDE_CODE_SESSION_ID", "PLUGIN_METRICS_PARENT", "SHIP")


class FrontEnds(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="mitosis-front-ends-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        self.env = {
            **{key: value for key, value in os.environ.items() if key not in SCRATCH_STRIPPED},
            **ISOLATED_GIT_ENV,
            "HOME": os.path.join(self.tmp, "home"),
            "PLUGIN_METRICS_DIR": os.path.join(self.tmp, "metrics"),
            "MITOSIS_TRAJECTORY_STORE": write(self.tmp, "no-history.jsonl", ""),
        }
        os.makedirs(self.env["HOME"])
        os.makedirs(self.repo)
        write(self.repo, "README.md", "seed\n")
        self._git("init", "-q", "-b", "main")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "seed")
        self._git("branch", "feature")

    def _git(self, *args):
        completed = subprocess.run(
            ["git", *args],
            cwd=self.repo,
            env=self.env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=SCRATCH_CALL_LIMIT,
        )
        if completed.returncode != 0:
            raise RuntimeError("scaffold git %s failed: %s" % (" ".join(args), completed.stderr))

    def _mitosis(self, *flags):
        return subprocess.run(
            [sys.executable, MITOSIS_SCRIPT, *flags],
            cwd=self.repo,
            env=self.env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=SCRATCH_RUN_LIMIT,
        )

    def asks_with_spec_is_a_usage_error_naming_both(self):
        completed = self._mitosis("--asks", "a.txt", "--spec", "docs/spec.md", "--decompose-command", "c")
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertIn("--asks", completed.stderr)
        self.assertIn("--spec", completed.stderr)

    def asks_with_revise_is_a_usage_error_naming_revise(self):
        completed = self._mitosis("--asks", "a.txt", "--decompose-command", "c", "--revise", "--plan-only")
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertIn("--revise", completed.stderr)

    def a_named_test_only_the_feature_branch_holds_plans_only_from_that_branch(self):
        self._git("checkout", "-q", "feature")
        write(self.repo, os.path.join("tests", "test_reader.py"), "import unittest\n")
        self._git("add", "tests/test_reader.py")
        self._git("commit", "-q", "-m", "reader test")
        self._git("checkout", "-q", "main")
        items = write(
            self.tmp,
            "items.json",
            items_document(
                self.tmp,
                [
                    {
                        "name": "reader",
                        "task": "Read it.",
                        "files": ["reader.py"],
                        "acceptance": [{"file": "tests/test_reader.py", "test": "R.reads"}],
                        "spec_ref": [SPEC_SECTION],
                    }
                ],
            ),
        )
        held = self._mitosis(
            "--items", items, "--plan-only", "--feature-branch", "feature", "--run-dir", os.path.join(self.tmp, "run-1")
        )
        self.assertEqual(held.returncode, 0, held.stdout[-2000:] + held.stderr[-2000:])
        unheld = self._mitosis("--items", items, "--plan-only", "--run-dir", os.path.join(self.tmp, "run-2"))
        self.assertEqual(unheld.returncode, 3, unheld.stdout[-2000:] + unheld.stderr[-2000:])
        self.assertIn("tests/test_reader.py", unheld.stderr)


def load_tests(loader, tests, pattern):
    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            return sorted(
                name
                for name in dir(case)
                if not name.startswith("_")
                and callable(getattr(case, name))
                and name not in dir(unittest.TestCase)
            )

    suite = unittest.TestSuite()
    for case in (
        AchievedParallelism,
        RiskMarkers,
        TemplatePlaceholders,
        CoverageReport,
        Flags,
        Staging,
        ItemsEntryPointRefusals,
        ReconcileReporting,
        ReportSections,
        BriefStageReport,
        CoverageRendering,
        LaneCycleFusion,
        HeldRun,
        GateExit,
        CouplingReport,
        GraphLoading,
        PullRequestFlags,
        PushOnly,
        ProvenHead,
        RenderPreflight,
        SessionReport,
        GateClassRefusals,
        FrontEnds,
    ):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
