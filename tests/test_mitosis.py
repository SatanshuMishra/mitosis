import contextlib
import io
import itertools
import json
import os
import shlex
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


class ReturnParsing(unittest.TestCase):
    def _out(self, text):
        import tempfile

        path = os.path.join(tempfile.mkdtemp(), "out")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    RETURN = '{"item":"a","status":"ok","files_changed":["x.py"],"notes":"n"}'

    def a_fenced_return_is_still_found(self):
        got = run.last_return(self._out("prose\n```json\n" + self.RETURN + "\n```\n"))
        self.assertEqual(got["item"], "a")

    def prose_after_the_return_does_not_discard_it(self):
        got = run.last_return(self._out(self.RETURN + "\nAll done.\n"))
        self.assertEqual(got["status"], "ok")

    def the_last_return_wins_when_there_are_several(self):
        first = '{"item":"a","status":"failed","files_changed":[],"notes":""}'
        got = run.last_return(self._out(first + "\n" + self.RETURN + "\n"))
        self.assertEqual(got["status"], "ok")

    def narration_that_merely_looks_like_json_is_skipped(self):
        got = run.last_return(self._out(self.RETURN + "\n[1, 2, 3]\n\"a string\"\n"))
        self.assertEqual(got["item"], "a")

    def a_worker_that_returns_nothing_still_reports_nothing(self):
        self.assertIsNone(run.last_return(self._out("no json here at all\n")))


class RiskMarkers(unittest.TestCase):
    def a_marker_does_not_fire_inside_a_longer_word(self):
        self.assertEqual(core.tier_for(["src/waverider.py"], ["wav"], "simple", None), "cheap")
        self.assertEqual(
            core.tier_for(["app/(unauthenticated)/page.tsx"], ["auth"], "simple", None), "cheap"
        )

    def a_marker_fires_on_a_path_segment(self):
        self.assertEqual(core.tier_for(["bleep/wav.py"], ["wav"], "simple", None), "top")
        self.assertEqual(core.tier_for(["src/wav/writer.py"], ["wav"], "simple", None), "top")

    def a_marker_fires_on_a_camel_case_word(self):
        self.assertEqual(
            core.tier_for(["app/types/AuthResponse.ts"], ["auth"], "simple", None), "top"
        )

    def a_marker_carrying_a_separator_keeps_substring_behaviour(self):
        self.assertEqual(core.tier_for(["api/auth/login.py"], ["api/auth"], "simple", None), "top")
        self.assertEqual(core.tier_for(["db/0004_add.sql"], [".sql"], "simple", None), "top")

    def a_glob_marker_still_matches(self):
        self.assertEqual(core.tier_for(["db/0004_add.sql"], ["db/*.sql"], "simple", None), "top")


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
    print(handle.read().strip())
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
print(json.dumps({"name": step, "task": "build " + step}))
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
    return command(root, "structurer.py", STRUCTURER, "{document}")


def briefer(root, failing=()):
    for name in failing:
        write(root, "worker/fail-%s" % name, "")
    return command(root, "briefer.py", BRIEFER, "{step}", "{document}")


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
    return {"name": name, "files": [name + ".py"], "source": None, "acceptance": [], **extra}


def briefed(name, **extra):
    return bare(name, task="do " + name, **extra)


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
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = mitosis.main(argv)
            except SystemExit as stop:
                code = stop.code
    finally:
        os.chdir(before)
    return code, out.getvalue(), err.getvalue()


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
                    "--timeout",
                    "20",
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
                "c {prompt} {model} {document}",
                "--brief-command",
                "b {prompt} {model} {step} {document}",
            ]
        )
        mitosis.check_templates(args)

    def revise_cannot_be_combined_with_items_or_resume(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "items.json", json.dumps([briefed("alpha")]))
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
                    "--timeout",
                    "20",
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
                "--timeout",
                "20",
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
                "--timeout",
                "20",
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
                [item["task"] for item in items], ["do alpha", "build beta", "build gamma"]
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

            def fake_execute(args, plan, models, run_dir, repo, root_dir):
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
                    "worker {task}",
                    "--acceptance-command",
                    "probe {file} {test}",
                    "--pr-command",
                    "opener {branch}",
                )
            finally:
                mitosis.execute = kept
            self.assertEqual(code, mitosis.EXIT_NO_BRANCHES, err)
            self.assertEqual([item["task"] for item in seen["items"]], ["do alpha", "build beta"])
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
                    "--timeout",
                    "20",
                    "--run-dir",
                    os.path.join(root, "run"),
                    "--feature-branch",
                    "feature",
                    "--dispatch-command",
                    "worker {task}",
                    "--acceptance-command",
                    "probe {file} {test}",
                    "--pr-command",
                    "opener {branch}",
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
            self.assertNotIn("alpha", err.split("mitosis:")[-1].split("\n")[0])
            self.assertEqual(briefed_steps(root), ["alpha", "beta", "gamma"])
            self.assertFalse(os.path.isfile(os.path.join(run_dir, mitosis.ITEMS_FILE)))
            structure = read_json(os.path.join(run_dir, mitosis.STRUCTURE_FILE))
            self.assertEqual(
                [step.get("task") for step in structure], ["build alpha", None, "build gamma"]
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
            self.assertEqual(structure[0]["task"], "do alpha")
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


class ItemsEntryPointRefusals(unittest.TestCase):
    CYCLIC = [
        {"name": "x", "task": "t", "files": ["pkg/shared.py"], "source": None, "acceptance": []},
        {"name": "y", "task": "t", "files": ["pkg/y.py"], "source": None,
         "acceptance": [], "after": ["x"]},
        {"name": "z", "task": "t", "files": ["pkg/shared.py"], "source": None,
         "acceptance": [], "after": ["y"]},
    ]
    EMPTY_MANIFEST = [
        {"name": "surface", "task": "t", "files": ["pkg/__init__.py", "pkg/g.py"],
         "source": None, "acceptance": []},
        {"name": "parse", "task": "t", "files": ["pkg/parse.py"], "source": None,
         "acceptance": [], "after": ["surface"]},
    ]

    def _run(self, items):
        with tempfile.TemporaryDirectory() as root:
            write(root, "items.json", json.dumps(items))
            return invoke(
                root,
                ["--items", "items.json", "--plan-only", "--run-dir", os.path.join(root, "run")],
            )

    def a_plan_whose_packages_wait_on_each_other_is_planned_as_one(self):
        code, out, err = self._run(self.CYCLIC)
        self.assertEqual(code, mitosis.EXIT_SHIPPED, err + out)
        self.assertEqual(len(core.msp_items(self.CYCLIC)), 1)

    GROUP_CYCLE = [
        {"name": "a", "task": "t", "files": ["a.py"], "source": None,
         "acceptance": [], "contract_group": "g"},
        {"name": "x", "task": "t", "files": ["x.py"], "source": None,
         "acceptance": [], "after": ["a"]},
        {"name": "b", "task": "t", "files": ["b.py"], "source": None,
         "acceptance": [], "contract_group": "g", "after": ["x"]},
    ]

    def a_cycle_through_an_unpinned_group_is_built_by_one_worker(self):
        code, out, err = self._run(self.GROUP_CYCLE)
        self.assertEqual(code, mitosis.EXIT_SHIPPED, err + out)
        self.assertEqual(len(core.lane_items(self.GROUP_CYCLE)), 1)

    def a_package_supplied_as_items_that_would_ship_empty_is_planned_and_reported(self):
        code, out, err = self._run(self.EMPTY_MANIFEST)
        self.assertEqual(code, mitosis.EXIT_MANIFEST, err + out)
        self.assertIn("manifest-exports-nothing", out)
        found = json.loads(out.splitlines()[-1])
        self.assertEqual(found["exit"], mitosis.EXIT_MANIFEST)
        self.assertIn("nothing exported", found["attention"][0])

    def a_sound_plan_supplied_as_items_still_ships(self):
        code, out, err = self._run(
            [
                {"name": "parse", "task": "t", "files": ["pkg/parse.py"], "source": None,
                 "acceptance": []},
                {"name": "surface", "task": "t", "files": ["pkg/__init__.py"], "source": None,
                 "acceptance": [], "after": ["parse"]},
            ]
        )
        self.assertEqual(code, mitosis.EXIT_SHIPPED, err)


class GraphLoading(unittest.TestCase):
    def _load(self, graph):
        with tempfile.TemporaryDirectory() as root:
            path = write(root, "graph.json", json.dumps(graph))
            return mitosis.load_graph(path, root)

    def a_node_link_graph_is_read_as_the_files_its_code_links(self):
        graph = {
            "directed": False,
            "nodes": [
                {"id": "f", "file_type": "code", "source_file": "src/parse.py"},
                {"id": "g", "file_type": "code", "source_file": "src/emit.py"},
                {"id": "d", "file_type": "document", "source_file": "README.md"},
            ],
            "links": [{"source": "f", "target": "g"}, {"source": "d", "target": "f"}],
        }
        self.assertEqual(
            self._load(graph), {"src/emit.py": ["src/parse.py"], "src/parse.py": ["src/emit.py"]}
        )

    def a_mapping_is_still_read_as_it_is(self):
        self.assertEqual(self._load({"a.py": ["b.py"]}), {"a.py": ["b.py"]})

    def a_mapping_carrying_metadata_and_nulls_is_read_without_them(self):
        graph = {"version": "1", "a.py": ["b.py", None, 3], "b.py": None}
        self.assertEqual(self._load(graph), {"a.py": ["b.py"]})

    def a_nodes_list_without_links_is_refused_rather_read_as_a_mapping(self):
        for graph in (
            {"nodes": [{"id": "a", "source_file": "a.py"}], "meta": []},
            {"nodes": [{"id": "a"}], "connections": [], "languages": ["python"]},
        ):
            with self.assertRaises(mitosis.Refusal) as caught:
                self._load(graph)
            self.assertIn("no links or edges list", str(caught.exception))

    def an_object_that_is_neither_shape_is_refused_by_name(self):
        with self.assertRaises(mitosis.Refusal) as caught:
            self._load({"directed": False, "graph": {}, "nodes": "not a list"})
        self.assertIn("node-link", str(caught.exception))

    def a_node_link_graph_that_names_no_file_is_refused(self):
        graph = {"nodes": [{"id": "x"}, {"id": "y"}], "links": [{"source": "x", "target": "y"}]}
        with self.assertRaises(mitosis.Refusal) as caught:
            self._load(graph)
        self.assertIn("source_file", str(caught.exception))


HOLDING_WORKER = r'''
import json
import sys

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
print(json.dumps({"item": "lane", "status": "ok", "files_changed": paths, "notes": "done"}))
'''


class HeldRun(unittest.TestCase):
    def _run(self, *extra, worker=None):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as elsewhere:
            git_repo(root)
            write(root, "items.json", json.dumps([briefed("alpha")]))
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
                        command(elsewhere, "worker.py", worker or HOLDING_WORKER, "{task}"),
                        "--acceptance-command",
                        "probe {file} {test}",
                        "--timeout",
                        "30",
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
        self.assertIn("exit 4", found["lanes"][0]["reason"])
        self.assertTrue(found["lanes"][0]["stderr"].endswith("0.err"))
        self.assertEqual(len(found["attention"]), 2)
        self.assertIn("Lane 0 (alpha) is failed", found["attention"][0])
        self.assertIn("MSP alpha is blocked", found["attention"][1])

    def a_held_run_needs_no_pull_request_command_and_pushes_nothing(self):
        code, out, err = self._run("--no-push")
        self.assertEqual(code, mitosis.EXIT_SHIPPED, out + err)
        self.assertIn("MSP alpha: committed", out)

    def a_held_run_with_a_pull_request_command_is_refused(self):
        code, _, err = self._run("--no-push", "--pr-command", "opener {branch}")
        self.assertEqual(code, mitosis.EXIT_REFUSED)
        self.assertIn("--no-push", err)

    def a_run_that_pushes_still_needs_a_pull_request_command(self):
        code, _, err = self._run()
        self.assertEqual(code, mitosis.EXIT_REFUSED)
        self.assertIn("--pr-command", err)


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

    def a_shipped_msp_whose_gate_passed_exits_zero(self):
        state = self._state(gate={"outcome": "pass", "counts": {}, "blocks": False})
        self.assertEqual(mitosis.exit_code(state, self.PLAN), mitosis.EXIT_SHIPPED)
        passing = self._state(gate={"outcome": "not-applicable", "blocks": False})
        self.assertEqual(mitosis.exit_code(passing, self.PLAN), mitosis.EXIT_SHIPPED)


class CouplingReport(unittest.TestCase):
    ITEMS = [
        briefed("seven", files=["db/migrations/0007_x.sql"]),
        briefed("eight", files=["db/migrations/0008_y.sql"]),
        briefed("plain-a", files=["src/a.py"]),
        briefed("plain-b", files=["src/b.py"]),
    ]

    def _plan_only(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "items.json", json.dumps(self.ITEMS))
            write(root, "graph.json", json.dumps({"src/a.py": ["src/b.py"]}))
            return invoke(
                root,
                ["--items", "items.json", "--graph", "graph.json", "--plan-only",
                 "--run-dir", os.path.join(root, "run")],
            )

    def a_strong_signal_is_ordered_and_an_import_link_is_named_before_the_build(self):
        code, out, err = self._plan_only()
        self.assertEqual(code, mitosis.EXIT_SHIPPED, err + out)
        body = section(out, "Coupling")
        self.assertIn("seven then eight: put in order because of same-migration-directory", body)
        self.assertTrue(any(line.startswith("plain-a and plain-b") and "run in parallel" in line for line in body))
        found = json.loads(out.splitlines()[-1])
        self.assertEqual(
            [(entry["first"], entry["then"]) for entry in found["coupling"]["ordered"]], [("seven", "eight")]
        )
        self.assertEqual(found["coupling"]["parallel"], [{"steps": ["plain-a", "plain-b"], "signals": ["import-adjacency"]}])
        self.assertEqual(
            found["attention"],
            ["1 pair of Steps run in parallel with only import-adjacency between them: plain-a+plain-b; "
             "add an after edge where one needs the other's output"],
        )

    def many_parallel_pairs_are_named_in_one_line_with_the_rest_counted(self):
        coupling = {
            "ordered": [],
            "parallel": [{"steps": ["a%d" % n, "b%d" % n], "signals": ["import-adjacency"]} for n in range(10)],
        }
        lines = mitosis._coupling_attention(coupling)
        self.assertEqual(len(lines), 1)
        self.assertIn("10 pairs of Steps", lines[0])
        self.assertIn("and 2 more", lines[0])


class ReportSections(unittest.TestCase):
    def the_split_shape_section_prints_every_scalar(self):
        with tempfile.TemporaryDirectory() as root:
            write(
                root,
                "items.json",
                json.dumps([briefed("alpha"), briefed("beta", after=["alpha"]), briefed("gamma")]),
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
            {"written": ["a", "b"], "reused": [], "retried": ["b"], "errors": []}
        )
        self.assertIn("1 brief re-dispatched after a return that broke the contract: b", lines)

    def a_run_where_every_brief_returned_cleanly_says_nothing_about_retries(self):
        lines = mitosis.brief_lines(
            {"written": ["a"], "reused": [], "retried": [], "errors": []}
        )
        self.assertTrue(all("re-dispatched" not in line for line in lines))


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

    def a_cycle_spanning_msps_is_planned_as_one_msp_and_never_refused(self):
        self.assertEqual(mitosis.manifest_lines(self.CYCLIC), ())
        self.assertEqual(mitosis.planned_code(self.CYCLIC), mitosis.EXIT_SHIPPED)
        self.assertEqual(len(core.msp_items(self.CYCLIC)), 1)
        self.assertEqual(len(core.lane_items(self.CYCLIC)), 1)

    def a_plan_without_a_cycle_keeps_its_msps_apart(self):
        self.assertEqual(mitosis.manifest_lines(self.CLEAN), ())
        self.assertEqual(mitosis.planned_code(self.CLEAN), mitosis.EXIT_SHIPPED)
        self.assertEqual(len(core.msp_items(self.CLEAN)), 2)

    def a_manifest_that_can_export_nothing_is_reported_not_refused(self):
        items = [
            {"name": "gregorian", "task": "t", "files": ["pkg/__init__.py", "pkg/g.py"],
             "source": None, "acceptance": []},
            {"name": "parse", "task": "t", "files": ["pkg/parse.py"], "source": None,
             "acceptance": [], "after": ["gregorian"]},
        ]
        reported = mitosis.manifest_lines(items)
        self.assertEqual(len(reported), 1)
        self.assertIn("pkg/__init__.py would ship with nothing exported", reported[0])
        self.assertIn("gregorian owns it", reported[0])
        self.assertEqual(mitosis.planned_code(items), mitosis.EXIT_MANIFEST)

    def a_manifest_written_last_is_never_refused(self):
        items = [
            {"name": "parse", "task": "t", "files": ["pkg/parse.py"], "source": None,
             "acceptance": []},
            {"name": "surface", "task": "t", "files": ["pkg/__init__.py"], "source": None,
             "acceptance": [], "after": ["parse"]},
        ]
        self.assertEqual(mitosis.manifest_lines(items), ())
        self.assertEqual(mitosis.planned_code(items), mitosis.EXIT_SHIPPED)

    def a_cycle_inside_one_msp_is_contracted_and_never_refused(self):
        items = [{**step, "msp": "m"} for step in self.CYCLIC]
        self.assertEqual(mitosis.manifest_lines(items), ())
        self.assertEqual(mitosis.planned_code(items), mitosis.EXIT_SHIPPED)

    def an_outcome_line_states_the_meaning_of_every_exit_code(self):
        for code, meaning in mitosis.EXIT_MEANING.items():
            with self.subTest(code=code):
                lines = mitosis.outcome_lines(None, None, "/run", code)
                if code == mitosis.EXIT_SHIPPED:
                    self.assertIn("no brief was bought", lines[-1])
                else:
                    self.assertIn(meaning, lines[-1])
                self.assertTrue(lines[-1].startswith("exit %d:" % code))


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
        ReturnParsing,
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
    ):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
