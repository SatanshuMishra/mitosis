import contextlib
import csv
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, ROOT)

ISOLATED_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "mitosis test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "mitosis test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}

UNSET_FOR_RUNS = ("PLUGIN_METRICS_DIR", "PLUGIN_METRICS_PARENT", "CLAUDE_CODE_SESSION_ID")

UNSET_FOR_DUCKDB = UNSET_FOR_RUNS + ("AI_AGENT",)

SKILL_PATH = os.path.join(ROOT, "skills", "metrics", "SKILL.md")

CATALOGUE_PATH = os.path.join(ROOT, "catalogue.json")

STORE_IN_SKILL = "~/.plugin-metrics"

SQL_BLOCK = re.compile(r"^```sql\n(.*?)^```", re.S | re.M)

QUERY_COLUMNS = {
    "recent_runs": ["run", "mode", "exit", "steps", "msps", "wall_seconds", "usd"],
    "lane_states": ["state", "lanes"],
    "gate_outcomes": ["outcome", "gates"],
    "msp_outcomes": ["state", "msps"],
    "parallelism": ["run", "lane_width", "peak_parallel", "concurrency"],
    "spend_by_model": ["source", "model", "usd"],
}

WORKER = r'''
import datetime
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
config = json.load(open(os.path.join(here, "config.json")))
lane = sys.argv[2]
session = sys.argv[3]
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
if "model" in config:
    projects = os.path.join(os.environ["HOME"], ".claude", "projects", "scratch")
    os.makedirs(projects, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    message = {
        "type": "assistant",
        "timestamp": stamp,
        "message": {"id": "msg-" + session, "model": config["model"], "usage": {"output_tokens": 1000}},
    }
    with open(os.path.join(projects, session + ".jsonl"), "a") as handle:
        handle.write(json.dumps(message) + "\n")
if lane in config.get("failing_lanes", []):
    sys.exit(4)
print(json.dumps({"item": "lane", "status": "ok", "files_changed": paths, "notes": "done"}))
'''

ACCEPTANCE = r'''
import os
import sys

sys.exit(0 if os.path.exists("alpha.py") and "work" in open("alpha.py").read() else 1)
'''


def write(root, relative, content):
    path = os.path.join(root, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def command(root, script_name, script, *placeholders):
    path = write(root, os.path.join("worker", script_name), script)
    return " ".join((shlex.quote(sys.executable), shlex.quote(path)) + placeholders)


def briefed(name, **extra):
    return {"name": name, "task": "do " + name, "files": [name + ".py"], "source": None, "acceptance": [], **extra}


def gated_alpha():
    return briefed("alpha", files=["alpha.py", "alpha_check.txt"], acceptance=[{"file": "alpha_check.txt", "test": "property"}])


def git_repo(root):
    env = {**os.environ, **ISOLATED_GIT_ENV}
    for args in (
        ["git", "init", "-q", root],
        ["git", "-C", root, "commit", "-q", "--allow-empty", "-m", "root"],
        ["git", "-C", root, "branch", "feature"],
    ):
        subprocess.run(args, check=True, capture_output=True, env=env)


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


def scratch_run(items, *flags, worker_config=None):
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as elsewhere:
        git_repo(root)
        write(root, "items.json", json.dumps(items))
        write(elsewhere, os.path.join("worker", "config.json"), json.dumps(worker_config or {}))
        build_flags = (
            (
                "--feature-branch",
                "feature",
                "--dispatch-command",
                command(elsewhere, "worker.py", WORKER, "{task}", "{lane}", "{session_id}"),
                "--acceptance-command",
                command(elsewhere, "accept.py", ACCEPTANCE, "{file}", "{test}"),
                "--timeout",
                "30",
            )
            if worker_config is not None
            else ()
        )
        return invoke(root, ["--items", "items.json", "--run-dir", os.path.join(elsewhere, "run"), *build_flags, *flags])


@contextlib.contextmanager
def written_store():
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as store:
        os.makedirs(os.path.join(home, ".claude", "projects"))
        with unittest.mock.patch.dict(os.environ, {**ISOLATED_GIT_ENV, "HOME": home}):
            for name in UNSET_FOR_RUNS:
                os.environ.pop(name, None)
            os.environ["PLUGIN_METRICS_DIR"] = store
            runs = (
                scratch_run([briefed("alpha"), briefed("beta")], "--no-push", worker_config={"failing_lanes": ["1"]}),
                scratch_run([gated_alpha()], "--no-push", worker_config={"model": "claude-sonnet-5-5"}),
                scratch_run([briefed("alpha"), briefed("beta")], "--plan-only"),
            )
        yield home, store, runs


def skill_blocks():
    return [block.strip() for block in SQL_BLOCK.findall(read_text(SKILL_PATH))]


def view_blocks(blocks):
    return [block for block in blocks if block.upper().startswith(("SET ", "CREATE OR REPLACE VIEW"))]


def substituted(sql, store):
    return sql.replace(STORE_IN_SKILL, store)


def duckdb_command(case):
    found = shutil.which("duckdb")
    if found is None:
        case.fail("the duckdb command is not on PATH; install it with brew install duckdb")
    return found


def run_duckdb(case, home, script):
    environment = {name: value for name, value in os.environ.items() if name not in UNSET_FOR_DUCKDB}
    return subprocess.run(
        [duckdb_command(case), "-csv", "-bail"],
        input=script,
        capture_output=True,
        text=True,
        env={**environment, "HOME": home},
        timeout=120,
    )


def csv_rows(text):
    return list(csv.reader(io.StringIO(text)))


class NamedQueries(unittest.TestCase):
    def test_named_queries_run(self):
        with open(CATALOGUE_PATH, encoding="utf-8") as handle:
            queries = json.load(handle)["queries"]
        self.assertEqual(sorted(query["name"] for query in queries), sorted(QUERY_COLUMNS))
        with written_store() as (home, store, runs):
            self.assertEqual([code for code, _, _ in runs], [10, 0, 0], [out + err for _, out, err in runs])
            setup = "\n".join(substituted(block, store) for block in view_blocks(skill_blocks()))
            for query in queries:
                completed = run_duckdb(self, home, setup + "\n" + query["sql"] + ";\n")
                self.assertEqual(completed.returncode, 0, query["name"] + ": " + completed.stderr)
                rows = csv_rows(completed.stdout)
                self.assertEqual(rows[0], QUERY_COLUMNS[query["name"]], query["name"])
                self.assertGreaterEqual(len(rows), 2, query["name"])


class SkillSql(unittest.TestCase):
    def test_skill_sql_runs(self):
        blocks = skill_blocks()
        views = view_blocks(blocks)
        self.assertEqual(len(views), 3)
        self.assertGreater(len(blocks), len(views))
        with written_store() as (home, store, runs):
            self.assertEqual([code for code, _, _ in runs], [10, 0, 0], [out + err for _, out, err in runs])
            setup = "\n".join(substituted(block, store) for block in views)
            for block in blocks:
                completed = run_duckdb(self, home, setup + "\n" + substituted(block, store) + "\n")
                self.assertEqual(completed.returncode, 0, block + "\n" + completed.stderr)
            failing = run_duckdb(self, home, setup + "\nSELECT no_such_column FROM summaries;\n")
            self.assertNotEqual(failing.returncode, 0)


if __name__ == "__main__":
    unittest.main()
