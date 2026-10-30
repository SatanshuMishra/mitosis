import contextlib
import datetime
import glob
import importlib.util
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, ROOT)

MARKER = "MARKER-7f3a"

STAMP = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")

FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

FIELD_KEYS = (
    "type",
    "description",
    "measured_from",
    "unit",
    "includes",
    "excludes",
    "reading",
    "readers",
    "question",
)

ISOLATED_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "mitosis test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "mitosis test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}

UNSET_FOR_RUNS = ("PLUGIN_METRICS_PARENT", "CLAUDE_CODE_SESSION_ID")

WORKER = r'''
import datetime
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(here, "config.json")
config = json.load(open(config_path)) if os.path.isfile(config_path) else {}
lane = sys.argv[2] if len(sys.argv) > 2 else "0"
session = sys.argv[3] if len(sys.argv) > 3 else None
chosen = {**config.get("all", {}), **config.get("lanes", {}).get(lane, {})}
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
stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
projects = os.path.join(os.environ["HOME"], ".claude", "projects", "scratch")


def message(key, model, output):
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": stamp,
            "message": {"id": key, "model": model, "usage": {"output_tokens": output}},
        }
    )


if "transcript" in chosen and session:
    os.makedirs(projects, exist_ok=True)
    with open(os.path.join(projects, session + ".jsonl"), "a") as handle:
        handle.write(message("msg-" + session, chosen["transcript"]["model"], chosen["transcript"]["output"]) + "\n")
if chosen.get("transcript_dir") and session:
    os.makedirs(os.path.join(projects, session + ".jsonl"), exist_ok=True)
if "caller" in chosen:
    os.makedirs(projects, exist_ok=True)
    with open(os.path.join(projects, chosen["caller"]["session"] + ".jsonl"), "a") as handle:
        handle.write(message("msg-caller-during", chosen["caller"]["model"], chosen["caller"]["output"]) + "\n")
if "parent_file" in chosen:
    with open(chosen["parent_file"], "w") as handle:
        handle.write(os.environ.get("PLUGIN_METRICS_PARENT", ""))
if "child" in chosen:
    folder = os.path.join(
        os.environ["PLUGIN_METRICS_DIR"], "plugin=probe", "kind=event", "repo=" + chosen["child"]["repo"]
    )
    os.makedirs(folder, exist_ok=True)
    line = {
        "v": 1,
        "ts": stamp,
        "plugin": "probe",
        "plugin_version": "0.0.1",
        "kind": "event",
        "event": "probe.start",
        "run": chosen["child"]["run"],
        "parent": os.environ.get("PLUGIN_METRICS_PARENT"),
        "session": None,
        "repo": chosen["child"]["repo"],
    }
    with open(os.path.join(folder, chosen["child"]["run"] + ".jsonl"), "a") as handle:
        handle.write(json.dumps(line) + "\n")
if "print" in chosen:
    print(chosen["print"])
    print(chosen["print"], file=sys.stderr)
if "exit" in chosen:
    sys.exit(chosen["exit"])
print(
    json.dumps(
        {
            "item": "lane",
            "status": chosen.get("status", "ok"),
            "files_changed": paths,
            "notes": chosen.get("notes", "done"),
        }
    )
)
'''

ACCEPTANCE = r'''
import glob
import json
import os
import shutil
import sys

here = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(here, "accept.json")
config = json.load(open(config_path)) if os.path.isfile(config_path) else {}
if "copy_summary" in config:
    found = glob.glob(
        os.path.join(os.environ["PLUGIN_METRICS_DIR"], "plugin=mitosis", "kind=summary", "repo=*", "*.json")
    )
    if found:
        shutil.copyfile(found[0], config["copy_summary"])
if "print" in config:
    print(config["print"])
    print(config["print"], file=sys.stderr)
if "exit" in config:
    sys.exit(config["exit"])
sys.exit(0 if os.path.exists("alpha.py") and "work" in open("alpha.py").read() else 1)
'''

STRUCTURER = """
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.stdin.read()
with open(os.path.join(here, "structure-reply.txt"), encoding="utf-8") as handle:
    print(handle.read().strip())
"""

PULL_REQUEST = """
import sys

print(sys.argv[1])
print(sys.argv[1], file=sys.stderr)
sys.exit(1)
"""

RENDER = """
import json
import sys

with open(sys.argv[3], encoding="utf-8") as handle:
    facts = json.load(handle)
print(json.dumps({"title": facts["title"], "body": "## Problem\\n" + " ".join(facts["problem"])}))
"""


def write(root, relative, content):
    path = os.path.join(root, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def read_lines(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def command(root, script_name, script, *placeholders):
    path = write(root, os.path.join("worker", script_name), script)
    return " ".join((shlex.quote(sys.executable), shlex.quote(path)) + placeholders)


def renderer(elsewhere):
    path = write(elsewhere, os.path.join("worker", "render.py"), "#!%s\n%s" % (sys.executable, RENDER))
    os.chmod(path, 0o755)
    return path


def worker(elsewhere, config=None, *placeholders):
    write(elsewhere, os.path.join("worker", "config.json"), json.dumps(config or {}))
    return command(elsewhere, "worker.py", WORKER, "{task}", "{lane}", *placeholders)


def acceptance(elsewhere, config=None):
    write(elsewhere, os.path.join("worker", "accept.json"), json.dumps(config or {}))
    return command(elsewhere, "accept.py", ACCEPTANCE, "{file}", "{test}")


def bare(name, **extra):
    return {"name": name, "files": [name + ".py"], "source": None, "acceptance": [], **extra}


def briefed(name, **extra):
    return bare(name, task="do " + name, **extra)


def gated_alpha(**extra):
    return briefed(
        "alpha",
        files=["alpha.py", "alpha_check.txt"],
        acceptance=[{"file": "alpha_check.txt", "test": "property"}],
        **extra,
    )


def git(*args):
    subprocess.run(args, check=True, capture_output=True, env={**os.environ, **ISOLATED_GIT_ENV})


def git_repo(root):
    git("git", "init", "-q", root)
    git("git", "-C", root, "commit", "-q", "--allow-empty", "-m", "root")
    git("git", "-C", root, "branch", "feature")


@contextlib.contextmanager
def isolated(**chosen):
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as store:
        os.makedirs(os.path.join(home, ".claude", "projects"))
        patched = {**ISOLATED_GIT_ENV, "HOME": home, "PLUGIN_METRICS_DIR": store}
        with unittest.mock.patch.dict(os.environ, patched):
            for name in UNSET_FOR_RUNS:
                os.environ.pop(name, None)
            os.environ.update(chosen)
            yield home, store


@contextlib.contextmanager
def scratch():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as elsewhere:
        git_repo(root)
        yield root, elsewhere


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


def items_run(root, run_dir, items, *extra):
    write(root, "items.json", json.dumps(items))
    return invoke(root, ["--items", "items.json", "--run-dir", run_dir, *extra])


def build(root, elsewhere, items, template, *extra, accept=None, run_dir=None):
    return items_run(
        root,
        run_dir or os.path.join(elsewhere, "run"),
        items,
        "--feature-branch",
        "feature",
        "--dispatch-command",
        template,
        "--acceptance-command",
        accept or acceptance(elsewhere),
        "--timeout",
        "30",
        *extra,
    )


def store_files(store, kind, plugin="mitosis"):
    return sorted(glob.glob(os.path.join(store, "plugin=" + plugin, "kind=" + kind, "repo=*", "*")))


def only(paths):
    if len(paths) != 1:
        raise AssertionError("expected one file, found %r" % (paths,))
    return paths[0]


def events_of(store):
    return read_lines(only(store_files(store, "event")))


def summary_of(store):
    return read_lines(only(store_files(store, "summary")))[0]


def summaries_of(store):
    return [read_lines(path)[0] for path in store_files(store, "summary")]


def named(lines, event):
    return [line for line in lines if line["event"] == event]


def run_id_of(path):
    name = os.path.basename(path)
    return name[: -len(".jsonl")] if name.endswith(".jsonl") else name[: -len(".json")]


def last_json(out):
    return json.loads(out.splitlines()[-1])


def every_file_text(store):
    texts = []
    for folder, _, files in os.walk(store):
        for name in files:
            with open(os.path.join(folder, name), encoding="utf-8", errors="replace") as handle:
                texts.append(handle.read())
    return "\n".join(texts)


def two_lane_items():
    return [briefed("alpha"), briefed("beta")]


def failing_second(elsewhere):
    return worker(elsewhere, {"lanes": {"1": {"exit": 4}}})


def catalogue():
    return read_json(os.path.join(ROOT, "catalogue.json"))


def walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from walk_keys(item)
    if isinstance(value, list):
        for item in value:
            yield from walk_keys(item)


def undescribed(value, entry, trail):
    missing = [trail + "." + key for key in FIELD_KEYS if key not in entry]
    if isinstance(value, dict):
        fields = entry.get("fields") or {}
        for key, item in value.items():
            if key not in fields:
                missing.append(trail + "." + key)
                continue
            missing.extend(undescribed(item, fields[key], trail + "." + key))
    if isinstance(value, list):
        for item in value:
            if "items" not in entry:
                missing.append(trail + "[]")
                break
            missing.extend(undescribed(item, entry["items"], trail + "[]"))
    return missing


def described_fields(catalogue_value):
    names = set()
    for event, entry in catalogue_value["events"].items():
        names.update(event + "." + field for field in entry["fields"])
    names.update("summary." + field for field in catalogue_value["summary"]["fields"])
    return names


def every_field_entry(fields, trail):
    for name, entry in fields.items():
        yield trail + "." + name, entry
        yield from every_field_entry(entry.get("fields") or {}, trail + "." + name)
        if "items" in entry:
            yield trail + "." + name + "[]", entry["items"]
            yield from every_field_entry(entry["items"].get("fields") or {}, trail + "." + name + "[]")


def mitosis_helpers():
    spec = importlib.util.spec_from_file_location(
        "mitosis_test_helpers", os.path.join(ROOT, "tests", "test_mitosis.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildEvents(unittest.TestCase):
    def test_build_writes_events_in_order(self):
        import core

        with isolated() as (home, store), scratch() as (root, elsewhere):
            code, out, err = build(root, elsewhere, [gated_alpha()], worker(elsewhere), "--no-push")
            self.assertEqual(code, 0, out + err)
            lines = events_of(store)
        self.assertEqual(
            [line["event"] for line in lines],
            ["run.start", "plan", "lane.start", "lane.end", "gate", "reconcile", "msp.end", "run.end"],
        )
        for line in lines:
            self.assertEqual(line["plugin"], "mitosis")
            self.assertEqual(line["plugin_version"], core.__version__)
            self.assertEqual(line["repo"], os.path.basename(root))

    def tiered_build(self):
        with isolated() as (home, store), scratch() as (root, elsewhere):
            code, out, err = build(
                root,
                elsewhere,
                [gated_alpha()],
                worker(elsewhere, None, "{session_id}", "{model}"),
                "--no-push",
                "--tier-model",
                "cheap=claude-sonnet-5-5",
                "--tier-model",
                "top=claude-opus-5-5",
            )
            run_dir = os.path.join(elsewhere, "run")
            return (
                code,
                out,
                err,
                events_of(store),
                read_json(os.path.join(run_dir, "state.json")),
                read_json(os.path.join(run_dir, "plan.json")),
            )

    def test_lane_events_carry_session_tier_model(self):
        code, out, err, lines, state, plan = self.tiered_build()
        self.assertEqual(code, 0, out + err)
        tier = plan["lanes"][0]["tier"]
        model = {"cheap": "claude-sonnet-5-5", "top": "claude-opus-5-5"}[tier]
        session = state["lanes"]["0"]["session_id"]
        self.assertIsNotNone(session)
        label = plan["msps"][0]["label"]
        for event in ("lane.start", "lane.end"):
            line = only(named(lines, event))
            self.assertEqual(line["lane"], 0)
            self.assertEqual(line["msp"], label)
            self.assertEqual(line["tier"], tier)
            self.assertEqual(line["model"], model)
            self.assertEqual(line["session_id"], session)
        ended = only(named(lines, "lane.end"))
        self.assertEqual(ended["state"], "ok")
        self.assertEqual(ended["exit"], 0)
        self.assertIs(ended["timed_out"], False)
        self.assertIsInstance(ended["seconds"], (int, float))
        self.assertGreaterEqual(ended["seconds"], 0)

    def test_gate_event_outcome(self):
        import mitosis

        code, out, err, lines, state, plan = self.tiered_build()
        self.assertEqual(code, 0, out + err)
        printed = last_json(out)
        self.assertIsNone(only(named(lines, "run.start"))["plan_id"])
        self.assertEqual(only(named(lines, "plan"))["plan_id"], printed["plan_id"])
        gate = only(named(lines, "gate"))
        self.assertEqual(gate["msp"], plan["msps"][0]["label"])
        self.assertEqual(gate["outcome"], "pass")
        self.assertIs(gate["reused"], False)
        self.assertIsInstance(gate["seconds"], (int, float))
        self.assertEqual(only(named(lines, "msp.end"))["state"], "committed")
        ended = only(named(lines, "run.end"))
        self.assertEqual(ended["exit"], 0)
        self.assertEqual(ended["meaning"], printed["meaning"])
        self.assertEqual(ended["meaning"], mitosis.EXIT_MEANING[0])


class PlanOnly(unittest.TestCase):
    def test_plan_only_writes_plan_and_summary(self):
        items = [
            briefed("alpha", files=["shared.py"]),
            briefed("beta", files=["shared.py", "beta.py"]),
            briefed("gamma"),
        ]
        with isolated() as (home, store), scratch() as (root, elsewhere):
            code, out, err = items_run(root, os.path.join(elsewhere, "run"), items, "--plan-only")
            self.assertEqual(code, 0, out + err)
            lines = events_of(store)
            summary = summary_of(store)
        self.assertEqual([line["event"] for line in lines], ["run.start", "plan", "run.end"])
        self.assertEqual(summary["mode"], "plan-only")
        self.assertEqual(summary["steps"], 3)
        self.assertEqual(summary["lanes"], 3)
        self.assertEqual(summary["msps"], 2)
        self.assertEqual(summary["lane_width"], 2)

    def test_spec_plan_only_writes_the_structure_stage(self):
        with isolated() as (home, store), scratch() as (root, elsewhere):
            write(root, "docs/spec.md", "# One\n\nbody\n\n# Two\n\nmore\n")
            reply = {"items": [bare("alpha"), bare("beta")], "assumptions": [], "constraints": []}
            write(elsewhere, "worker/structure-reply.txt", json.dumps(reply))
            code, out, err = invoke(
                root,
                [
                    "--spec",
                    "docs/spec.md",
                    "--run-dir",
                    os.path.join(elsewhere, "run"),
                    "--decompose-command",
                    command(elsewhere, "structurer.py", STRUCTURER, "{document}"),
                    "--timeout",
                    "30",
                    "--plan-only",
                ],
            )
            self.assertEqual(code, 0, out + err)
            lines = events_of(store)
            summary = summary_of(store)
        self.assertEqual([line["event"] for line in lines], ["run.start", "stage.end", "run.end"])
        self.assertEqual(named(lines, "stage.end")[0]["stage"], "structure")
        self.assertEqual(summary["mode"], "plan-only")
        self.assertEqual(summary["steps"], 2)
        for key in ("lanes", "msps", "clusters", "lane_width"):
            self.assertIsNone(summary[key], key)


class Summary(unittest.TestCase):
    def objectives_run(self):
        with isolated() as (home, store), scratch() as (root, elsewhere):
            code, out, err = build(root, elsewhere, two_lane_items(), failing_second(elsewhere), "--no-push")
            return code, out, err, summary_of(store)

    def test_summary_counts_the_objectives(self):
        import mitosis

        code, out, err, summary = self.objectives_run()
        self.assertEqual(code, 10, out + err)
        self.assertEqual(summary["steps"], 2)
        self.assertEqual(summary["lanes"], 2)
        self.assertEqual(summary["msps"], 2)
        self.assertEqual(summary["lane_states"]["ok"], 1)
        self.assertEqual(summary["lane_states"]["failed"], 1)
        self.assertEqual(summary["msp_states"]["committed"], 1)
        self.assertEqual(summary["msp_states"]["blocked"], 1)
        self.assertEqual(summary["exit"], 10)
        self.assertEqual(summary["meaning"], last_json(out)["meaning"])
        self.assertEqual(summary["meaning"], mitosis.EXIT_MEANING[10])

    def test_summary_keys_are_field_names(self):
        code, out, err, summary = self.objectives_run()
        self.assertEqual(code, 10, out + err)
        for key in walk_keys(summary):
            self.assertRegex(key, FIELD_NAME)
        for key in ("tier_models", "lane_runs", "msp_runs", "spend", "children"):
            self.assertIsInstance(summary[key], list, key)
            for entry in summary[key]:
                self.assertIsInstance(entry, dict, key)

    def test_summary_spend_from_worker_transcript(self):
        config = {"all": {"transcript": {"model": "claude-sonnet-5-5", "output": 1000000}}}
        with isolated() as (home, store), scratch() as (root, elsewhere):
            code, out, err = build(
                root, elsewhere, [briefed("alpha")], worker(elsewhere, config, "{session_id}"), "--no-push"
            )
            self.assertEqual(code, 0, out + err)
            summary = summary_of(store)
        self.assertEqual(summary["usd"], 10.0)
        self.assertEqual(
            [(row["source"], row["model"], row["usd"]) for row in summary["spend"]],
            [("worker", "claude-sonnet-5-5", 10.0)],
        )
        self.assertEqual(summary["prices_date"], "2026-10-06")
        self.assertEqual(summary["sessions_missing"], 0)
        self.assertIs(summary["spend_complete"], True)

    def test_summary_counts_missing_sessions(self):
        with isolated() as (home, store), scratch() as (root, elsewhere):
            code, out, err = build(root, elsewhere, [briefed("alpha")], worker(elsewhere), "--no-push")
            self.assertEqual(code, 0, out + err)
            summary = summary_of(store)
        self.assertEqual(summary["sessions_missing"], 1)
        self.assertIs(summary["spend_complete"], False)
        self.assertEqual(summary["usd"], 0)

    def test_summary_written_when_dispatch_ends(self):
        with isolated() as (home, store), scratch() as (root, elsewhere):
            copy = os.path.join(elsewhere, "copied-summary.json")
            code, out, err = build(
                root,
                elsewhere,
                [gated_alpha()],
                worker(elsewhere),
                "--no-push",
                accept=acceptance(elsewhere, {"copy_summary": copy}),
            )
            self.assertEqual(code, 0, out + err)
            self.assertTrue(os.path.isfile(copy))
            copied = read_lines(copy)[0]
        self.assertEqual(copied["lane_states"]["ok"], 1)

    def test_crash_still_writes_summary(self):
        import run

        with isolated() as (home, store), scratch() as (root, elsewhere):
            with unittest.mock.patch.object(run, "execute", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    build(root, elsewhere, [briefed("alpha")], worker(elsewhere), "--no-push")
            ended = only(named(events_of(store), "run.end"))
            summary = summary_of(store)
        self.assertIsNone(ended["exit"])
        self.assertIsNone(summary["exit"])

    def test_refused_run_records_its_exit(self):
        import mitosis

        with isolated() as (home, store), scratch() as (root, elsewhere):
            code, out, err = items_run(
                root,
                os.path.join(elsewhere, "run"),
                [briefed("alpha")],
                "--plan-only",
                "--proven-head",
                "nosuch=abcdef1",
            )
            ended = only(named(events_of(store), "run.end"))
            summary = summary_of(store)
        self.assertEqual(code, 2, out + err)
        self.assertEqual((ended["exit"], ended["meaning"]), (2, mitosis.EXIT_MEANING[2]))
        self.assertEqual((summary["exit"], summary["meaning"]), (2, mitosis.EXIT_MEANING[2]))

    def test_resume_counts_only_its_own_lanes(self):
        config = {"all": {"transcript": {"model": "claude-sonnet-5-5", "output": 1000000}}}
        with isolated() as (home, store), scratch() as (root, elsewhere):
            template = worker(elsewhere, config, "{session_id}")
            code, out, err = build(root, elsewhere, [briefed("alpha")], template, "--no-push")
            self.assertEqual(code, 0, out + err)
            code, out, err = build(root, elsewhere, [briefed("alpha")], template, "--resume", "--no-push")
            self.assertEqual(code, 0, out + err)
            by_mode = {summary["mode"]: summary for summary in summaries_of(store)}
        first, second = by_mode["build"], by_mode["resume"]
        self.assertEqual(first["usd"], 10.0)
        self.assertEqual(len(first["lane_runs"]), 1)
        self.assertEqual(second["usd"], 0)
        self.assertEqual(second["lane_runs"], [])
        self.assertEqual(second["lane_seconds"], 0)
        self.assertEqual(set(second["lane_states"].values()), {0})

    def test_resume_does_not_count_a_lane_left_held(self):
        import run

        config = {"all": {"transcript": {"model": "claude-sonnet-5-5", "output": 1000000}}}
        with isolated() as (home, store), scratch() as (root, elsewhere):
            template = worker(elsewhere, config, "{session_id}")
            with unittest.mock.patch.object(run, "_landed", side_effect=RuntimeError("killed before landing")):
                with self.assertRaises(RuntimeError):
                    build(root, elsewhere, [briefed("alpha")], template, "--no-push")
            state = read_json(os.path.join(elsewhere, "run", "state.json"))
            self.assertEqual({lane: entry["state"] for lane, entry in state["lanes"].items()}, {"0": "held"})
            code, out, err = build(root, elsewhere, [briefed("alpha")], template, "--resume", "--no-push")
            self.assertEqual(code, 0, out + err)
            resumed = {summary["mode"]: summary for summary in summaries_of(store)}["resume"]
            resumed_events = [
                line["event"]
                for path in store_files(store, "event")
                if run_id_of(path) == resumed["run"]
                for line in read_lines(path)
            ]
        self.assertNotIn("lane.end", resumed_events)
        self.assertEqual(resumed["usd"], 0)
        self.assertEqual(resumed["lane_runs"], [])
        self.assertEqual(resumed["lane_seconds"], 0)
        self.assertEqual(set(resumed["lane_states"].values()), {0})


class Children(unittest.TestCase):
    def test_commands_get_the_run_as_parent(self):
        given = "pipeline/feat-a.20261026T120000Z"
        with isolated(PLUGIN_METRICS_PARENT=given) as (home, store), scratch() as (root, elsewhere):
            seen = os.path.join(elsewhere, "parent.txt")
            code, out, err = build(
                root, elsewhere, [briefed("alpha")], worker(elsewhere, {"all": {"parent_file": seen}}), "--no-push"
            )
            self.assertEqual(code, 0, out + err)
            path = only(store_files(store, "event"))
            with open(seen, encoding="utf-8") as handle:
                received = handle.read()
            lines = read_lines(path)
        self.assertEqual(received, "mitosis/" + run_id_of(path))
        self.assertEqual({line["parent"] for line in lines}, {given})

    def test_children_listed_as_missing(self):
        with isolated() as (home, store), scratch() as (root, elsewhere):
            child = {"repo": os.path.basename(root), "run": "child-1.20261026T120000Z"}
            code, out, err = build(
                root, elsewhere, [briefed("alpha")], worker(elsewhere, {"all": {"child": child}}), "--no-push"
            )
            self.assertEqual(code, 0, out + err)
            summary = summary_of(store)
        self.assertEqual(
            summary["children"],
            [{"plugin": "probe", "run": "child-1.20261026T120000Z", "usd": None, "missing": True}],
        )
        self.assertEqual(summary["usd"], 0)

    def test_caller_window_counted(self):
        before = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        earlier = {
            "type": "assistant",
            "timestamp": before,
            "message": {"id": "msg-caller-before", "model": "claude-opus-5-5", "usage": {"output_tokens": 5000}},
        }
        config = {"all": {"caller": {"session": "caller-1", "model": "claude-opus-5-5", "output": 1000}}}
        with isolated(CLAUDE_CODE_SESSION_ID="caller-1") as (home, store), scratch() as (root, elsewhere):
            write(home, ".claude/projects/scratch/caller-1.jsonl", json.dumps(earlier) + "\n")
            code, out, err = build(root, elsewhere, [briefed("alpha")], worker(elsewhere, config), "--no-push")
            self.assertEqual(code, 0, out + err)
            summary = summary_of(store)
        callers = [row for row in summary["spend"] if row["source"] == "caller"]
        self.assertEqual(len(callers), 1)
        self.assertEqual(callers[0]["model"], "claude-opus-5-5")
        self.assertEqual(callers[0]["tokens"]["output"], 1000)


class Catalogue(unittest.TestCase):
    def test_catalogue_copied_to_the_store(self):
        with isolated() as (home, store), scratch() as (root, elsewhere):
            code, out, err = items_run(root, os.path.join(elsewhere, "run"), [briefed("alpha")], "--plan-only")
            self.assertEqual(code, 0, out + err)
            copied = read_json(os.path.join(store, "plugin=mitosis", "catalogue.json"))
        self.assertEqual(copied, catalogue())

    def test_every_written_field_described(self):
        import metrics_writer

        with isolated() as (home, store), scratch() as (root, elsewhere):
            code, out, err = build(root, elsewhere, two_lane_items(), failing_second(elsewhere), "--no-push")
            self.assertEqual(code, 10, out + err)
            code, out, err = items_run(root, os.path.join(elsewhere, "plan"), [briefed("alpha")], "--plan-only")
            self.assertEqual(code, 0, out + err)
            lines = [line for path in store_files(store, "event") for line in read_lines(path)]
            summaries = summaries_of(store)
        described = catalogue()
        missing = []
        for line in lines:
            entry = described["events"].get(line["event"])
            if entry is None:
                missing.append(line["event"])
                continue
            fields = {key: value for key, value in line.items() if key not in metrics_writer.ENVELOPE}
            missing.extend(undescribed(fields, {**dict.fromkeys(FIELD_KEYS), "fields": entry["fields"]}, line["event"]))
        for summary in summaries:
            fields = {key: value for key, value in summary.items() if key not in metrics_writer.ENVELOPE}
            missing.extend(
                undescribed(fields, {**dict.fromkeys(FIELD_KEYS), "fields": described["summary"]["fields"]}, "summary")
            )
        self.assertEqual(sorted(set(missing)), [])

    def test_objectives_name_described_fields(self):
        described = catalogue()
        objectives = described["objectives"]
        self.assertIsInstance(objectives, list)
        self.assertEqual(
            [objective["id"] for objective in objectives], ["split", "parallel", "gate", "pull_requests", "spend"]
        )
        names = described_fields(described)
        for objective in objectives:
            self.assertTrue(objective["text"])
            self.assertTrue(objective["measured_by"])
            for entry in objective["measured_by"]:
                self.assertIn(entry, names)

    def test_catalogue_holds_schema_keys(self):
        schema = read_json(os.path.join(ROOT, "tests", "fixtures", "metrics", "catalogue.schema.json"))
        described = catalogue()
        for key in schema["required"]:
            self.assertIn(key, described)
        required = schema["$defs"]["field"]["required"]
        entries = list(every_field_entry(described["summary"]["fields"], "summary"))
        for event, entry in described["events"].items():
            entries.extend(every_field_entry(entry["fields"], event))
        self.assertTrue(entries)
        for trail, entry in entries:
            for key in required:
                self.assertIn(key, entry, trail)


class MetricsFailure(unittest.TestCase):
    def test_unwritable_store_keeps_the_run(self):
        def outcome(out):
            found = last_json(out)
            return (
                found["exit"],
                found["meaning"],
                [lane["state"] for lane in found["lanes"]],
                [msp["state"] for msp in found["msps"]],
            )

        with isolated() as (home, store), scratch() as (root, elsewhere):
            code, out, err = build(root, elsewhere, [briefed("alpha")], worker(elsewhere), "--no-push")
            self.assertEqual(code, 0, out + err)
        with isolated() as (home, store), scratch() as (root, elsewhere):
            blocked = write(elsewhere, "not-a-folder", "")
            with unittest.mock.patch.dict(os.environ, {"PLUGIN_METRICS_DIR": blocked}):
                second_code, second_out, second_err = build(
                    root, elsewhere, [briefed("alpha")], worker(elsewhere), "--no-push"
                )
        self.assertEqual(second_code, 0, second_out + second_err)
        self.assertEqual(outcome(out), outcome(second_out))
        self.assertTrue(
            any(line.startswith("metrics not written: ") for line in second_err.splitlines()), second_err
        )

    def test_metrics_errors_never_stop_the_run(self):
        with isolated() as (home, store), scratch() as (root, elsewhere):
            write(store, "plugin=probe/kind=event/repo=elsewhere/broken-1.20261026T120000Z.jsonl", "not json\n")
            code, out, err = build(
                root,
                elsewhere,
                [briefed("alpha")],
                worker(elsewhere, {"all": {"transcript_dir": True}}, "{session_id}"),
                "--no-push",
            )
        self.assertEqual(code, 0, out + err)
        found = last_json(out)
        self.assertEqual(found["exit"], 0)
        self.assertEqual([lane["state"] for lane in found["lanes"]], ["ok"])
        self.assertTrue(any(line.startswith("metrics not written: ") for line in err.splitlines()), err)


class RunIdentity(unittest.TestCase):
    def test_run_id_from_run_dir(self):
        with isolated() as (home, store), scratch() as (root, elsewhere):
            code, out, err = items_run(root, os.path.join(elsewhere, "My_Run"), [briefed("alpha")], "--plan-only")
            self.assertEqual(code, 0, out + err)
            name = os.path.basename(only(store_files(store, "event")))
        prefix, suffix = "my-run.", ".jsonl"
        self.assertTrue(name.startswith(prefix), name)
        self.assertTrue(name.endswith(suffix), name)
        self.assertRegex(name[len(prefix) : -len(suffix)], STAMP)

    def test_resume_is_a_new_run(self):
        with isolated() as (home, store), scratch() as (root, elsewhere):
            template = worker(elsewhere)
            code, out, err = build(root, elsewhere, [briefed("alpha")], template, "--no-push")
            self.assertEqual(code, 0, out + err)
            code, out, err = build(root, elsewhere, [briefed("alpha")], template, "--resume", "--no-push")
            self.assertEqual(code, 0, out + err)
            paths = store_files(store, "summary")
            summaries = [read_lines(path)[0] for path in paths]
        self.assertEqual(len(paths), 2)
        self.assertEqual(len({run_id_of(path) for path in paths}), 2)
        self.assertEqual(len({summary["plan_id"] for summary in summaries}), 1)
        self.assertIsNotNone(summaries[0]["plan_id"])
        self.assertEqual(sorted(summary["mode"] for summary in summaries), ["build", "resume"])

    def test_taken_id_gets_a_suffix(self):
        import run

        with isolated() as (home, store), scratch() as (root, elsewhere):
            with unittest.mock.patch.object(run, "now", return_value="2026-10-26T12:00:00+00:00"):
                for _ in range(2):
                    code, out, err = items_run(
                        root, os.path.join(elsewhere, "My_Run"), [briefed("alpha")], "--plan-only"
                    )
                    self.assertEqual(code, 0, out + err)
            names = [os.path.basename(path) for path in store_files(store, "event")]
        self.assertEqual(names, ["my-run-2.20261026T120000Z.jsonl", "my-run.20261026T120000Z.jsonl"])

    def test_outside_a_repository_uses_the_folder_name(self):
        with isolated() as (home, store), tempfile.TemporaryDirectory() as parent:
            loose = os.path.join(parent, "loose-items")
            os.makedirs(loose)
            code, out, err = items_run(loose, os.path.join(parent, "run"), [briefed("alpha")], "--plan-only")
            self.assertEqual(code, 0, out + err)
            folders = [os.path.basename(os.path.dirname(path)) for path in store_files(store, "event")]
        self.assertEqual(folders, ["repo=loose-items"])


class NoWorkContent(unittest.TestCase):
    def assert_store_clean(self, store):
        self.assertTrue(os.listdir(store))
        self.assertNotIn(MARKER, every_file_text(store))

    def test_no_task_or_worker_output_stored(self):
        with isolated() as (home, store), scratch() as (root, elsewhere):
            code, out, err = build(
                root,
                elsewhere,
                [bare("alpha", task="do alpha " + MARKER)],
                worker(elsewhere, {"all": {"print": MARKER, "exit": 4}}),
                "--no-push",
            )
            self.assertEqual(code, 10, out + err)
            self.assert_store_clean(store)

    def test_no_return_notes_or_lane_reason_stored(self):
        with isolated() as (home, store), scratch() as (root, elsewhere):
            code, out, err = build(
                root,
                elsewhere,
                [briefed("alpha")],
                worker(elsewhere, {"all": {"status": "failed", "notes": MARKER}}),
                "--no-push",
            )
            self.assertEqual(code, 10, out + err)
            self.assertIn(MARKER, out)
            self.assert_store_clean(store)

    def test_no_gate_reason_stored(self):
        with isolated() as (home, store), scratch() as (root, elsewhere):
            code, out, err = build(
                root,
                elsewhere,
                [gated_alpha()],
                worker(elsewhere),
                "--no-push",
                accept=acceptance(elsewhere, {"print": MARKER, "exit": 3}),
            )
            self.assertEqual(code, 21, out + err)
            self.assert_store_clean(store)

    def test_no_pull_request_text_or_msp_reason_stored(self):
        with isolated() as (home, store), scratch() as (root, elsewhere):
            remote = os.path.join(elsewhere, "remote.git")
            git("git", "init", "-q", "--bare", remote)
            git("git", "-C", root, "remote", "add", "origin", remote)
            code, out, err = build(
                root,
                elsewhere,
                [briefed("alpha")],
                worker(elsewhere),
                "--pr-command",
                command(elsewhere, "opener.py", PULL_REQUEST, MARKER, "{branch}"),
                "--pr-type",
                "feat",
                "--pr-subject",
                "record the run",
                "--pr-why",
                "Callers need " + MARKER + ".",
                "--pr-impact",
                "Callers see " + MARKER + ".",
                "--render-command",
                renderer(elsewhere),
            )
            self.assertEqual(code, 22, out + err)
            self.assertIn(MARKER, out)
            self.assert_store_clean(store)


class Retention(unittest.TestCase):
    def test_summary_write_prunes_old_runs(self):
        start = datetime.datetime(2026, 1, 5, 9, 0, tzinfo=datetime.timezone.utc)
        with isolated() as (home, store), scratch() as (root, elsewhere):
            folder = os.path.join(store, "plugin=mitosis", "kind=event", "repo=" + os.path.basename(root))
            earlier = []
            for index in range(100):
                moment = start + datetime.timedelta(minutes=index)
                name = "old-%03d.%s" % (index, moment.strftime("%Y%m%dT%H%M%SZ"))
                line = {"v": 1, "ts": moment.strftime("%Y-%m-%dT%H:%M:%SZ"), "event": "run.start", "run": name}
                earlier.append(write(folder, name + ".jsonl", json.dumps(line) + "\n"))
            code, out, err = items_run(root, os.path.join(elsewhere, "fresh"), [briefed("alpha")], "--plan-only")
            self.assertEqual(code, 0, out + err)
            remaining = sorted(os.listdir(folder))
        self.assertEqual(len(remaining), 100)
        self.assertNotIn(os.path.basename(earlier[0]), remaining)
        self.assertTrue(any(name.startswith("fresh.") for name in remaining), remaining)


class SuiteIsolation(unittest.TestCase):
    def test_invoke_helper_isolates_the_store(self):
        import metrics_writer

        helpers = mitosis_helpers()
        written = []
        original_append, original_summary = metrics_writer.append_event, metrics_writer.write_summary

        def append_spy(plugin, plugin_version, repo, run, event, fields, environ=None, now=None):
            warning = original_append(plugin, plugin_version, repo, run, event, fields, environ, now)
            path = metrics_writer.event_path(metrics_writer.store_root(os.environ), plugin, repo, run)
            written.append(read_lines(path)[-1])
            return warning

        def summary_spy(plugin, plugin_version, repo, run, fields, environ=None, now=None):
            warning = original_summary(plugin, plugin_version, repo, run, fields, environ, now)
            path = metrics_writer.summary_path(metrics_writer.store_root(os.environ), plugin, repo, run)
            written.append(read_lines(path)[-1])
            return warning

        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as other, scratch() as (
            root,
            elsewhere,
        ):
            named_store = os.path.join(other, "named-store")
            chosen = {
                **ISOLATED_GIT_ENV,
                "HOME": home,
                "PLUGIN_METRICS_DIR": named_store,
                "PLUGIN_METRICS_PARENT": "pipeline/elsewhere.20261026T120000Z",
                "CLAUDE_CODE_SESSION_ID": "session-elsewhere",
            }
            with unittest.mock.patch.dict(os.environ, chosen), unittest.mock.patch.object(
                metrics_writer, "append_event", append_spy
            ), unittest.mock.patch.object(metrics_writer, "write_summary", summary_spy):
                write(root, "items.json", json.dumps([briefed("alpha")]))
                code, out, err = helpers.invoke(
                    root, ["--items", "items.json", "--run-dir", os.path.join(elsewhere, "run"), "--plan-only"]
                )
                self.assertEqual(os.environ["PLUGIN_METRICS_DIR"], named_store)
            self.assertEqual(code, 0, out + err)
            self.assertFalse(os.path.exists(os.path.join(home, ".plugin-metrics")))
            self.assertFalse(os.path.exists(named_store))
        self.assertTrue(written)
        self.assertEqual({line["parent"] for line in written}, {None})
        self.assertEqual({line["session"] for line in written}, {None})


if __name__ == "__main__":
    unittest.main()
