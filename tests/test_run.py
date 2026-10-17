import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core
import run

ISOLATED_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "mitosis test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "mitosis test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "HOME": tempfile.gettempdir(),
}


def sh(args, cwd):
    return subprocess.run(
        args, cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def step(name, files, **extra):
    base = {
        "name": name,
        "task": "do " + name,
        "files": list(files),
        "source": None,
        "acceptance": [],
    }
    return {**base, **extra}


def lane_named(plan, name):
    return next(i for i, lane in enumerate(plan["lanes"]) if name in lane["steps"])


WORKER_SCRIPT = r'''
import json
import os
import subprocess
import sys
import time

mode, marker_dir, lane, task = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
running = os.path.join(marker_dir, "running-%s" % lane)
open(running, "w").close()
if mode == "slow-" + lane:
    time.sleep(1.5)
prefix = "Write-set for this Lane, the only files you may edit: "
write_set = []
for line in task.splitlines():
    if line.startswith(prefix):
        write_set = [p.strip() for p in line[len(prefix):].split(",") if p.strip()]
with open(os.path.join(marker_dir, "lane-%s" % lane), "a") as handle:
    handle.write("ran; saw " + " ".join(sorted(os.listdir("."))) + "\n")
if mode == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    with open(os.path.join(marker_dir, "child-%s" % lane), "w") as handle:
        handle.write(str(child.pid))
    child.wait()
written = write_set[:1] if mode == "partial" else write_set
if mode == "tests-only":
    written = [p for p in write_set if p.startswith("tests/")]
if mode in ("nothing", "nothing-" + lane):
    written = []
for path in written:
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as handle:
        handle.write("work by lane %s\n" % lane)
reported = list(write_set)
if mode in ("foreign-" + lane, "sneak-" + lane):
    with open("iface.txt", "a") as handle:
        handle.write("shape changed by lane %s\n" % lane)
    if mode == "foreign-" + lane:
        reported = reported + ["iface.txt"]
if mode == "undeclared":
    with open("stray.txt", "w") as handle:
        handle.write("undeclared\n")
if mode == "cross":
    with open("b.txt", "a") as handle:
        handle.write("written across an MSP boundary by lane %s\n" % lane)
if mode == "noisy":
    for i in range(5000):
        print("prose line %d" % i)
status = "failed" if mode == "reports-failed" else "ok"
notes = "n" * 500 if mode == "noisy" else "done"
if mode != "silent":
    print(json.dumps({"item": "lane-%s" % lane, "status": status, "files_changed": reported, "notes": notes}))
os.remove(running)
sys.exit(3 if mode in ("crash", "crash-" + lane) else 0)
'''


ACCEPT_SCRIPT = r'''
import os
import sys

mode, marker_dir, file, test = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
has_work = os.path.exists("impl.txt") and "work" in open("impl.txt").read()
with open(os.path.join(marker_dir, "gate"), "a") as handle:
    handle.write("%s %s %s work=%s\n" % (mode, file, test, has_work))
if mode == "inert":
    sys.exit(0)
if mode == "real":
    sys.exit(0 if has_work else 1)
if mode == "brittle":
    sys.exit(0 if has_work else 2)
if mode == "failing":
    sys.exit(1)
sys.exit(7)
'''


PR_SCRIPT = r'''
import json
import os
import sys

marker_dir = sys.argv[1]
with open(os.path.join(marker_dir, "pr"), "a") as handle:
    handle.write(json.dumps(sys.argv[2:]) + "\n")
print("https://example.invalid/pull/%d" % len(open(os.path.join(marker_dir, "pr")).read().splitlines()))
'''


HEADER = {
    "type": "feat",
    "scope": "ship",
    "subject": "write pull requests in the house format",
    "why": ("Callers need it.",),
}

PR_STUB = object()


def sha256_of(path):
    import hashlib

    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.saved_env = {key: os.environ.get(key) for key in ISOLATED_GIT_ENV}
        os.environ.update(ISOLATED_GIT_ENV)
        self.tmp = tempfile.mkdtemp(prefix="mitosis-")
        self.repo = os.path.join(self.tmp, "repo")
        self.trees = os.path.join(self.tmp, "trees")
        self.run_dir = os.path.join(self.tmp, "run")
        self.markers = os.path.join(self.tmp, "markers")
        os.makedirs(self.repo)
        sh(["git", "init", "-q", "-b", "main"], self.repo)
        write(os.path.join(self.repo, "README.md"), "seed\n")
        sh(["git", "add", "-A"], self.repo)
        sh(["git", "commit", "-q", "-m", "seed"], self.repo)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def seed_existing(self, relative, content):
        write(os.path.join(self.repo, relative), content)
        sh(["git", "add", "-A"], self.repo)
        sh(["git", "commit", "-q", "-m", "seed existing implementation"], self.repo)

    def worker_command(self, mode="ok"):
        script = os.path.join(self.tmp, "worker.py")
        if not os.path.exists(script):
            write(script, WORKER_SCRIPT)
        os.makedirs(self.markers, exist_ok=True)
        return "%s %s %s %s {lane} {task}" % (
            shlex.quote(sys.executable),
            shlex.quote(script),
            mode,
            shlex.quote(self.markers),
        )

    def marker(self, lane):
        path = os.path.join(self.markers, "lane-%d" % lane)
        return read(path) if os.path.exists(path) else None

    def acceptance_command(self, mode="real"):
        script = os.path.join(self.tmp, "accept.py")
        if not os.path.exists(script):
            write(script, ACCEPT_SCRIPT)
        os.makedirs(self.markers, exist_ok=True)
        return "%s %s %s %s {file} {test}" % (
            shlex.quote(sys.executable),
            shlex.quote(script),
            mode,
            shlex.quote(self.markers),
        )

    def gate_runs(self):
        path = os.path.join(self.markers, "gate")
        return read(path).splitlines() if os.path.exists(path) else []

    def execute(self, plan, mode="ok", accept="real", pr=PR_STUB, timeout=60, **extra):
        return run.execute(
            plan,
            self.repo,
            "main",
            self.run_dir,
            self.worker_command(mode),
            self.acceptance_command(accept),
            self.pr_command() if pr is PR_STUB else pr,
            timeout,
            2,
            trees_root=self.trees,
            **{"header": HEADER, **extra},
        )

    def house_text(self, plan, state, msp):
        record = state["msps"][str(msp)]
        exception = next(
            (entry for entry in state["stacking_exceptions"] if entry["msp"] == msp), None
        )
        return (
            run.pull_request_title(HEADER, plan, msp),
            run.pull_request_body(
                HEADER,
                plan,
                msp,
                record["gate"],
                record.get("gate_error"),
                record["reconcile"],
                exception,
                run.msp_foreign_writes(plan, msp, state["lanes"]),
            ),
        )

    def state(self):
        return json.loads(read(os.path.join(self.run_dir, run.STATE_FILE)))

    def commit_file(self, tree, path, content, message="commit"):
        write(os.path.join(tree, path), content)
        sh(["git", "add", "-A", "--", path], tree)
        sh(["git", "commit", "-q", "-m", message], tree)
        return sh(["git", "rev-parse", "HEAD"], tree)

    def dispatch(self, plan, trees, mode="ok", timeout=60, prior=None, concurrency=2, **extra):
        return run.dispatch(
            plan,
            trees,
            self.worker_command(mode),
            timeout,
            concurrency,
            self.run_dir,
            "main",
            prior=prior,
            **extra,
        )

    def build(self, plan, mode="ok"):
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        records = self.dispatch(plan, trees, mode=mode)
        self.assertTrue(all(r["state"] == "ok" for r in records.values()))
        return trees

    def gate(self, plan, trees, accept="real", msp=0, command=None):
        return run.gate(
            plan,
            msp,
            trees[msp]["path"],
            trees[msp]["branch"],
            "main",
            command or self.acceptance_command(accept),
            os.path.join(self.run_dir, "gate"),
            60,
        )

    def pr_command(self):
        script = os.path.join(self.tmp, "pr.py")
        if not os.path.exists(script):
            write(script, PR_SCRIPT)
        os.makedirs(self.markers, exist_ok=True)
        return "%s %s %s {branch} {base} {title} {body}" % (
            shlex.quote(sys.executable),
            shlex.quote(script),
            shlex.quote(self.markers),
        )

    def pull_requests(self):
        path = os.path.join(self.markers, "pr")
        if not os.path.exists(path):
            return []
        return [json.loads(line) for line in read(path).splitlines()]

    def add_remote(self):
        remote = os.path.join(self.tmp, "remote.git")
        sh(["git", "init", "-q", "--bare", remote], self.tmp)
        sh(["git", "remote", "add", "origin", remote], self.repo)
        return remote

    def remote_heads(self):
        out = sh(["git", "ls-remote", "--heads", "origin"], self.repo)
        return sorted(line.split("refs/heads/", 1)[1] for line in out.splitlines() if "refs/heads/" in line)

    def branches(self):
        out = sh(["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"], self.repo)
        return sorted(out.splitlines())

    def worktree_paths(self):
        out = sh(["git", "worktree", "list", "--porcelain"], self.repo)
        return sorted(
            line[len("worktree "):]
            for line in out.splitlines()
            if line.startswith("worktree ")
        )


class Worktrees(RepoCase):
    def each_msp_gets_its_own_branch_and_worktree(self):
        msps = [{"label": "core", "steps": ["a"]}, {"label": "run", "steps": ["b"]}]
        trees = run.prepare_worktrees(msps, "main", self.trees, self.repo)
        self.assertEqual([t["msp"] for t in trees], [0, 1])
        self.assertEqual(len({t["branch"] for t in trees}), 2)
        self.assertEqual(len({t["path"] for t in trees}), 2)
        for tree in trees:
            self.assertTrue(os.path.isdir(tree["path"]))
            self.assertIn(tree["branch"], self.branches())
            self.assertEqual(sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], tree["path"]), tree["branch"])
            self.assertEqual(
                sh(["git", "rev-parse", "HEAD"], tree["path"]),
                sh(["git", "rev-parse", "main"], self.repo),
            )
        self.assertEqual(
            sorted(os.path.realpath(t["path"]) for t in trees),
            sorted(p for p in map(os.path.realpath, self.worktree_paths()) if p != os.path.realpath(self.repo)),
        )

    def a_rerun_reuses_an_existing_branch(self):
        msps = [{"label": "core", "steps": ["a"]}]
        first = run.prepare_worktrees(msps, "main", self.trees, self.repo)
        write(os.path.join(first[0]["path"], "work.txt"), "committed on the branch\n")
        sh(["git", "add", "-A"], first[0]["path"])
        sh(["git", "commit", "-q", "-m", "work"], first[0]["path"])
        head = sh(["git", "rev-parse", "HEAD"], first[0]["path"])
        second = run.prepare_worktrees(msps, "main", self.trees, self.repo)
        self.assertEqual(second[0]["branch"], first[0]["branch"])
        self.assertEqual(second[0]["path"], first[0]["path"])
        self.assertEqual(sh(["git", "rev-parse", "HEAD"], second[0]["path"]), head)
        self.assertEqual(self.branches().count(first[0]["branch"]), 1)
        shutil.rmtree(first[0]["path"])
        third = run.prepare_worktrees(msps, "main", self.trees, self.repo)
        self.assertEqual(third[0]["branch"], first[0]["branch"])
        self.assertTrue(os.path.isfile(os.path.join(third[0]["path"], "work.txt")))
        self.assertEqual(sh(["git", "rev-parse", "HEAD"], third[0]["path"]), head)

    def labels_that_slug_alike_get_separate_worktrees(self):
        msps = [{"label": "Core!", "steps": ["a"]}, {"label": "core", "steps": ["b"]}]
        trees = run.prepare_worktrees(msps, "main", self.trees, self.repo)
        self.assertNotEqual(trees[0]["path"], trees[1]["path"])
        self.assertNotEqual(trees[0]["branch"], trees[1]["branch"])
        self.assertTrue(all(os.path.isdir(t["path"]) for t in trees))
        again = run.prepare_worktrees(msps, "main", self.trees, self.repo)
        self.assertEqual([t["branch"] for t in again], [t["branch"] for t in trees])

    def a_git_failure_raises_with_its_stderr(self):
        with self.assertRaises(run.GitError) as caught:
            run.git(["checkout", "no-such-branch"], self.repo)
        self.assertIn("no-such-branch", str(caught.exception))


class Dispatch(RepoCase):
    def build_argv_cannot_be_reinterpreted_as_shell(self):
        task = 'echo "quoted"; rm -rf /\nsecond line && $(whoami) | tee {model}'
        template = "%s -c %s {task} {model} tail" % (
            shlex.quote(sys.executable),
            shlex.quote("import json, sys; print(json.dumps(sys.argv[1:]))"),
        )
        argv = run.build_argv(template, {"task": task, "model": "m1"})
        self.assertEqual(argv[3], task)
        self.assertEqual(argv[4], "m1")
        self.assertEqual(argv[5], "tail")
        self.assertEqual(len(argv), 6)
        seen = json.loads(subprocess.run(argv, capture_output=True, text=True, check=True).stdout)
        self.assertEqual(seen, [task, "m1", "tail"])
        self.assertEqual(run.build_argv("a {unknown} b", {}), ["a", "{unknown}", "b"])

    def an_unmapped_tier_refuses_to_start(self):
        plan = core.plan([step("a", ["a.txt"], complexity="complex")])
        with self.assertRaises(run.ConfigError) as caught:
            run.check_models(plan, "agent --model {model} {task}", {"cheap": "small"})
        self.assertIn("top", str(caught.exception))
        run.check_models(plan, "agent {task}", {})
        run.check_models(plan, "agent --model {model} {task}", {"top": "big"})

    def a_lane_is_ok_when_both_channels_agree(self):
        plan = core.plan([step("a", ["a.txt"])])
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        records = self.dispatch(plan, trees)
        self.assertEqual(records[0]["state"], "ok")
        self.assertEqual(records[0]["exit"], 0)
        self.assertEqual(records[0]["returned"]["status"], "ok")
        self.assertEqual(read(os.path.join(trees[0]["path"], "a.txt")), "work by lane 0\n")
        self.assertEqual(sh(["git", "status", "--porcelain"], trees[0]["path"]), "")
        self.assertEqual(records[0]["commit"], sh(["git", "rev-parse", "HEAD"], trees[0]["path"]))
        self.assertNotEqual(records[0]["commit"], sh(["git", "rev-parse", "main"], self.repo))

    def a_worker_that_reports_failure_and_exits_zero_is_failed(self):
        plan = core.plan([step("a", ["a.txt"])])
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        records = self.dispatch(plan, trees, mode="reports-failed")
        self.assertEqual(records[0]["state"], "failed")
        self.assertEqual(records[0]["exit"], 0)
        self.assertEqual(records[0]["returned"]["status"], "failed")
        self.assertIn("failed", records[0]["reason"])

    def a_worker_that_exits_nonzero_and_reports_ok_is_failed(self):
        plan = core.plan([step("a", ["a.txt"])])
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        records = self.dispatch(plan, trees, mode="crash")
        self.assertEqual(records[0]["state"], "failed")
        self.assertEqual(records[0]["exit"], 3)
        self.assertEqual(records[0]["returned"]["status"], "ok")
        self.assertIn("3", records[0]["reason"])

    def a_worker_with_no_return_line_is_failed(self):
        plan = core.plan([step("a", ["a.txt"])])
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        records = self.dispatch(plan, trees, mode="silent")
        self.assertEqual(records[0]["state"], "failed")
        self.assertEqual(records[0]["exit"], 0)
        self.assertIsNone(records[0]["returned"])

    def a_worker_that_never_exits_is_killed_and_failed(self):
        plan = core.plan([step("a", ["a.txt"])])
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        started = time.monotonic()
        records = self.dispatch(plan, trees, mode="hang", timeout=1)
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual(records[0]["state"], "failed")
        self.assertEqual(records[0]["reason"], "timeout")
        child = int(read(os.path.join(self.markers, "child-0")))
        time.sleep(0.5)
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)

    def a_failed_producer_merge_blocks_the_consumer(self):
        plan = core.plan([step("a", ["a.txt"]), step("b", ["b.txt"], after=["a"])])
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        producer = self.commit_file(trees[0]["path"], "shared.txt", "one\n")
        self.commit_file(trees[1]["path"], "shared.txt", "two\n")
        prior = {0: {"lane": 0, "msp": 0, "state": "ok", "commit": producer}}
        records = self.dispatch(plan, trees, prior=prior)
        self.assertEqual(records[0]["state"], "ok")
        self.assertEqual(records[1]["state"], "merge-blocked")
        self.assertIn(trees[0]["branch"], records[1]["reason"])
        self.assertIn(trees[1]["branch"], records[1]["reason"])
        self.assertIsNone(self.marker(1))
        self.assertEqual(sh(["git", "status", "--porcelain"], trees[1]["path"]), "")
        self.assertEqual(read(os.path.join(trees[1]["path"], "shared.txt")), "two\n")

    def a_producers_committed_work_is_merged_before_the_consumer_starts(self):
        plan = core.plan([step("a", ["a.txt"]), step("b", ["b.txt"], after=["a"])])
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        records = self.dispatch(plan, trees)
        self.assertEqual(records[0]["state"], "ok")
        self.assertEqual(records[1]["state"], "ok")
        self.assertIn("a.txt", self.marker(1))
        self.assertEqual(records[1]["merged"], [trees[0]["branch"]])
        self.assertTrue(os.path.isfile(os.path.join(trees[1]["path"], "a.txt")))

    def a_consumer_waits_for_a_producer_that_is_still_running(self):
        plan = core.plan(
            [step("a", ["a.txt"]), step("b", ["b.txt"]), step("c", ["c.txt"], after=["a", "b"])]
        )
        slow = lane_named(plan, "b")
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        records = self.dispatch(plan, trees, mode="slow-%d" % slow, concurrency=2)
        self.assertEqual({r["state"] for r in records.values()}, {"ok"})
        consumer = lane_named(plan, "c")
        self.assertEqual(len(records[consumer]["merged"]), 2)
        self.assertIn("a.txt", self.marker(consumer))
        self.assertIn("b.txt", self.marker(consumer))

    def a_failed_lane_blocks_only_its_dependents(self):
        plan = core.plan(
            [
                step("a", ["a.txt"]),
                step("b", ["b.txt"], after=["a"]),
                step("c", ["c.txt"], after=["b"]),
                step("d", ["d.txt"]),
            ]
        )
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        records = self.dispatch(plan, trees, mode="crash")
        by_step = {plan["lanes"][i]["steps"][0]: r for i, r in records.items()}
        self.assertEqual(by_step["a"]["state"], "failed")
        self.assertEqual(by_step["b"]["state"], "blocked")
        self.assertEqual(by_step["c"]["state"], "blocked")
        self.assertEqual(by_step["d"]["state"], "failed")
        self.assertIn(str(lane_named(plan, "a")), by_step["b"]["reason"])
        self.assertIsNone(self.marker(lane_named(plan, "b")))
        self.assertIsNone(self.marker(lane_named(plan, "c")))
        self.assertIsNotNone(self.marker(lane_named(plan, "d")))

    def _commit_refused_run(self, first_files):
        self.add_remote()
        plan = core.plan(
            [
                step("a", first_files),
                step("b", ["b.txt"], after=["a"]),
                step("d", ["d.txt"]),
            ]
        )
        final = self.execute(plan)
        by_step = {plan["lanes"][int(i)]["steps"][0]: r for i, r in final["lanes"].items()}
        self.assertEqual(by_step["a"]["state"], "failed")
        self.assertIn("the Lane's commit failed", by_step["a"]["reason"])
        self.assertEqual(by_step["b"]["state"], "blocked")
        self.assertEqual(by_step["d"]["state"], "ok")
        shipped = {
            plan["msps"][int(m)]["steps"][0]: r["state"] for m, r in final["msps"].items()
        }
        self.assertEqual(shipped["d"], "shipped")
        self.assertEqual(shipped["a"], run.MSP_BLOCKED)
        self.assertEqual(len(self.pull_requests()), 1)

    def a_refused_commit_fails_its_lane_and_the_run_goes_on(self):
        hook = os.path.join(self.repo, ".git", "hooks", "pre-commit")
        write(hook, "#!/bin/sh\ngit diff --cached --name-only | grep -qx refused.txt && exit 1\nexit 0\n")
        os.chmod(hook, 0o755)
        self._commit_refused_run(["refused.txt"])

    def a_write_set_path_git_ignores_fails_its_lane_and_the_run_goes_on(self):
        self.seed_existing(".gitignore", "build/\n")
        self._commit_refused_run(["build/out.txt"])

    def a_refused_commit_leaves_nothing_staged_to_block_a_siblings_merge(self):
        self.add_remote()
        hook = os.path.join(self.repo, ".git", "hooks", "pre-commit")
        write(hook, "#!/bin/sh\ngit diff --cached --name-only | grep -qx refused.txt && exit 1\nexit 0\n")
        os.chmod(hook, 0o755)
        plan = core.plan(
            [
                step("p", ["p.txt"]),
                step("a", ["refused.txt"], msp="m"),
                step("e", ["e.txt"], msp="m"),
                step("c", ["c.txt"], msp="m", after=["p"]),
            ]
        )
        final = self.execute(plan, mode="slow-%d" % lane_named(plan, "p"))
        by_step = {plan["lanes"][int(i)]["steps"][0]: r for i, r in final["lanes"].items()}
        self.assertEqual(by_step["a"]["state"], "failed")
        self.assertEqual(by_step["e"]["state"], "ok")
        self.assertEqual(by_step["c"]["state"], "ok", by_step["c"].get("reason"))
        m = next(int(i) for i, msp in enumerate(plan["msps"]) if "a" in msp["steps"])
        self.assertIn("is failed", final["msps"][str(m)]["reason"])

    def a_commit_msg_hook_that_refuses_every_landing_fails_each_lane_and_stops_nothing(self):
        hook = os.path.join(self.repo, ".git", "hooks", "commit-msg")
        write(hook, "#!/bin/sh\ngrep -q '^JIRA-' \"$1\" || { echo 'needs a ticket' >&2; exit 1; }\n")
        os.chmod(hook, 0o755)
        plan = core.plan([step(name, [name + ".txt"]) for name in "abc"])
        final = self.execute(plan)
        self.assertEqual({r["state"] for r in final["lanes"].values()}, {"failed"})
        self.assertTrue(all("needs a ticket" in r["reason"] for r in final["lanes"].values()))

    def a_repository_without_a_commit_identity_is_refused_before_anything_is_written(self):
        sh(["git", "config", "user.useConfigOnly", "true"], self.repo)
        for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            os.environ.pop(key, None)
        plan = core.plan([step("a", ["a.txt"])])
        with self.assertRaises(run.ConfigError) as caught:
            self.execute(plan)
        self.assertIn("no commit identity", str(caught.exception))
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, run.STATE_FILE)))
        self.assertEqual(self.branches(), ["main"])

    def a_commit_msg_hook_that_accepts_the_landing_message_lets_the_run_go(self):
        hook = os.path.join(self.repo, ".git", "hooks", "commit-msg")
        write(hook, "#!/bin/sh\ngrep -q '^chore(' \"$1\"\n")
        os.chmod(hook, 0o755)
        plan = core.plan([step("a", ["a.txt"]), step("b", ["b.txt"])])
        final = self.execute(plan)
        self.assertEqual({r["state"] for r in final["lanes"].values()}, {"ok"})

    def two_lane_specific_refusals_first_never_stop_the_lanes_after_them(self):
        hook = os.path.join(self.repo, ".git", "hooks", "pre-commit")
        write(hook, "#!/bin/sh\ngit diff --cached --name-only | grep -q '^refused' && exit 1\nexit 0\n")
        os.chmod(hook, 0o755)
        plan = core.plan(
            [step("refused-%d" % n, ["refused-%d.txt" % n]) for n in range(2)]
            + [step("fine-%d" % n, ["fine-%d.txt" % n]) for n in range(4)]
        )
        final = run.execute(
            plan, self.repo, "main", self.run_dir, self.worker_command(), self.acceptance_command(),
            self.pr_command(), 60, 1, trees_root=self.trees, no_push=True,
        )
        by_step = {plan["lanes"][int(i)]["steps"][0]: r["state"] for i, r in final["lanes"].items()}
        self.assertEqual(
            by_step,
            {**{"refused-%d" % n: "failed" for n in range(2)}, **{"fine-%d" % n: "ok" for n in range(4)}},
        )

    def a_lane_recorded_held_is_landed_on_resume_without_running_its_worker_again(self):
        plan = core.plan([step("a", ["a.txt"])])
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        write(os.path.join(trees[0]["path"], "a.txt"), "work left by a Worker before the run died\n")
        prior = {0: {"lane": 0, "msp": 0, "state": run.LANE_HELD, "commit": None}}
        records = self.dispatch(plan, trees, prior=prior)
        self.assertEqual(records[0]["state"], "ok")
        self.assertIsNotNone(records[0]["commit"])
        self.assertIsNone(self.marker(0))
        self.assertIn("a.txt", sh(["git", "show", "--name-only", "--format=", records[0]["commit"]], self.repo))

    def a_held_lane_whose_work_is_gone_is_run_again_not_recorded_ok(self):
        plan = core.plan([step("a", ["a.txt"])])
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        prior = {0: {"lane": 0, "msp": 0, "state": run.LANE_HELD, "commit": None}}
        records = self.dispatch(plan, trees, prior=prior)
        self.assertEqual(records[0]["state"], "ok")
        self.assertIsNotNone(self.marker(0))
        self.assertIn("a.txt", sh(["git", "show", "--name-only", "--format=", records[0]["commit"]], self.repo))

    def a_lane_waiting_to_land_is_recorded_held_before_it_lands(self):
        plan = core.plan([step("a", ["a.txt"], msp="m"), step("b", ["b.txt"], msp="m")])
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        seen = []
        self.dispatch(
            plan,
            trees,
            mode="slow-%d" % lane_named(plan, "b"),
            on_lane=lambda lane, record: seen.append((lane, record["state"])),
        )
        a = lane_named(plan, "a")
        self.assertEqual([state for lane, state in seen if lane == a], [run.LANE_HELD, "ok"])

    def a_producer_merge_skips_the_hooks_that_could_stash_a_siblings_work(self):
        plan = core.plan([step("p", ["p.txt"]), step("c", ["c.txt"], after=["p"])])
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        producer, consumer = trees[lane_named(plan, "p")], trees[lane_named(plan, "c")]
        self.commit_file(producer["path"], "p.txt", "produced\n")
        self.commit_file(consumer["path"], "early.txt", "diverged\n")
        for name in ("pre-merge-commit", "commit-msg", "prepare-commit-msg"):
            hook = os.path.join(self.repo, ".git", "hooks", name)
            write(hook, "#!/bin/sh\nexit 1\n")
            os.chmod(hook, 0o755)
        merged, error = run.merge_producers(
            consumer["path"], consumer["branch"], [{"branch": producer["branch"], "commit": None}], "main"
        )
        self.assertIsNone(error)
        self.assertEqual(merged, (producer["branch"],))

    def a_refused_lane_does_not_halt_a_run_whose_other_commits_land(self):
        hook = os.path.join(self.repo, ".git", "hooks", "pre-commit")
        write(hook, "#!/bin/sh\ngit diff --cached --name-only | grep -q '^refused' && exit 1\nexit 0\n")
        os.chmod(hook, 0o755)
        plan = core.plan(
            [step("fine", ["fine.txt"])]
            + [step("refused-%d" % n, ["refused-%d.txt" % n]) for n in range(3)]
            + [step("later-%d" % n, ["later-%d.txt" % n]) for n in range(3)]
        )
        final = self.execute(plan, mode="slow-%d" % lane_named(plan, "refused-0"))
        by_step = {plan["lanes"][int(i)]["steps"][0]: r for i, r in final["lanes"].items()}
        self.assertEqual(by_step["fine"]["state"], "ok")
        for n in range(3):
            self.assertEqual(by_step["later-%d" % n]["state"], "ok")
            self.assertEqual(by_step["refused-%d" % n]["state"], "failed")

    def a_finished_lane_lands_only_when_no_sibling_is_writing_in_its_worktree(self):
        overlap = os.path.join(self.markers, "overlap")
        hook = os.path.join(self.repo, ".git", "hooks", "pre-commit")
        write(
            hook,
            "#!/bin/sh\nfor f in %s/running-*; do [ -e \"$f\" ] && echo \"$f\" >> %s; done\nexit 0\n"
            % (self.markers, overlap),
        )
        os.chmod(hook, 0o755)
        plan = core.plan([step("a", ["a.txt"], msp="m"), step("b", ["b.txt"], msp="m")])
        final = self.execute(plan, mode="slow-%d" % lane_named(plan, "b"))
        self.assertEqual({r["state"] for r in final["lanes"].values()}, {"ok"})
        self.assertTrue(all(r["commit"] for r in final["lanes"].values()))
        self.assertFalse(os.path.exists(overlap), read(overlap) if os.path.exists(overlap) else "")

    def no_lane_starts_in_an_msp_while_one_of_its_lanes_waits_to_land(self):
        plan = core.plan(
            [step("a", ["a.txt"], msp="m"), step("c", ["c.txt"], msp="m"), step("e", ["e.txt"], msp="m")]
        )
        final = self.execute(plan, mode="slow-%d" % lane_named(plan, "c"))
        self.assertEqual({r["state"] for r in final["lanes"].values()}, {"ok"})
        branch = final["worktrees"][0]["branch"]
        landed = sh(["git", "log", "--reverse", "--format=%s", "main.." + branch], self.repo).splitlines()
        self.assertEqual([line.rsplit("(", 1)[1].rstrip(")") for line in landed], ["a", "c", "e"])

    def a_step_waiting_on_a_held_lane_in_its_own_msp_runs_after_it_lands(self):
        plan = core.plan(
            [
                step("a", ["a.txt"], msp="m"),
                step("b", ["b.txt"], msp="m", after=["a"]),
                step("d", ["d.txt"], msp="m", after=["a"]),
                step("c", ["c.txt"], msp="m"),
            ]
        )
        final = self.execute(plan, mode="slow-%d" % lane_named(plan, "c"))
        self.assertEqual({r["state"] for r in final["lanes"].values()}, {"ok"})
        self.assertIsNotNone(self.marker(lane_named(plan, "b")))
        self.assertIsNotNone(self.marker(lane_named(plan, "d")))

    def a_commit_waits_out_a_sibling_holding_the_worktree_lock(self):
        plan = core.plan([step("a", ["a.txt"])])
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        tree = trees[0]["path"]
        write(os.path.join(tree, "a.txt"), "work\n")
        lock = sh(["git", "rev-parse", "--git-path", "index.lock"], tree)
        lock = lock if os.path.isabs(lock) else os.path.join(tree, lock)
        write(lock, "")
        release = threading.Timer(0.5, os.remove, [lock])
        release.start()
        try:
            head = run.commit_paths(tree, ["a.txt"], "land a")
        finally:
            release.join()
        self.assertEqual(sh(["git", "log", "-1", "--format=%s", head], tree), "land a")

    def a_lock_is_recognised_in_any_language_and_nothing_else_is(self):
        def failed(stderr, code=128):
            return subprocess.CompletedProcess(["git"], code, "", stderr)

        for stderr in (
            "fatal: Unable to create '/r/.git/index.lock': File exists.",
            "fatal : Impossible de créer '/r/.git/worktrees/m/index.lock' : Le fichier existe.",
            "Schwerwiegend: Konnte '/r/.git/refs/heads/mitosis/a.lock' nicht erstellen",
        ):
            self.assertTrue(run._locked(failed(stderr)), stderr)
        for stderr in (
            "error: pathspec 'x' did not match any file(s) known to git",
            "hook: Cargo.lock is out of date",
        ):
            self.assertFalse(run._locked(failed(stderr)), stderr)
        self.assertFalse(run._locked(failed("fatal: Unable to create '/r/.git/index.lock'", 0)))

    def _interface_plan(self):
        return core.plan(
            [
                step("iface", ["iface.txt"], type="contract", contract_group="g"),
                step("server", ["server.txt"], contract_group="g", after=["iface"]),
                step("client", ["client.txt"], contract_group="g", after=["iface"]),
            ]
        )

    def a_lane_that_rewrites_a_landed_siblings_file_is_named_and_the_change_kept(self):
        self.add_remote()
        plan = self._interface_plan()
        server, client, iface = (lane_named(plan, name) for name in ("server", "client", "iface"))
        state = self.execute(plan, mode="foreign-%d" % server)
        lanes = state["lanes"]
        self.assertEqual({r["state"] for r in lanes.values()}, {"ok"})
        self.assertEqual(
            lanes[str(server)]["foreign_writes"],
            [{"path": "iface.txt", "owner": iface, "suspects": [server]}],
        )
        self.assertEqual(lanes[str(client)]["foreign_writes"], [])
        self.assertEqual(lanes[str(iface)]["foreign_writes"], [])
        branch = state["msps"]["0"]["ship"]["branch"]
        log = sh(["git", "log", "--format=%s", "main.." + branch], self.repo)
        self.assertIn("keep changes to files other Lanes own (iface.txt)", log)
        self.assertIn("shape changed by lane", sh(["git", "show", branch + ":iface.txt"], self.repo))
        body = self.pull_requests()[0][3]
        self.assertIn(
            "\n\n## Risk\nA Lane changed 1 file another Lane of this MSP owned and had already "
            "committed (iface.txt).\n\n",
            body,
        )

    def an_unclaimed_write_is_put_on_every_lane_that_finished_with_it(self):
        plan = self._interface_plan()
        server, client = lane_named(plan, "server"), lane_named(plan, "client")
        state = run.execute(
            plan, self.repo, "main", self.run_dir, self.worker_command("sneak-%d" % server),
            self.acceptance_command(), None, 60, 2, trees_root=self.trees, no_push=True,
        )
        suspects = {
            tuple(entry["suspects"])
            for record in state["lanes"].values()
            for entry in record.get("foreign_writes") or []
        }
        self.assertTrue(suspects)
        for found in suspects:
            self.assertIn(server, found)
        lines = [
            run.foreign_sentence(plan, entry)
            for entry in run.unique_foreign(run.msp_foreign_writes(plan, 0, state["lanes"]))
        ]
        self.assertEqual(len(lines), len(suspects))

    def a_lane_that_keeps_to_its_own_files_carries_no_foreign_write(self):
        plan = self._interface_plan()
        state = run.execute(
            plan, self.repo, "main", self.run_dir, self.worker_command(),
            self.acceptance_command(), None, 60, 2, trees_root=self.trees, no_push=True,
        )
        self.assertEqual(
            [record.get("foreign_writes") for record in state["lanes"].values()], [[], [], []]
        )

    def worker_output_goes_to_disk_and_the_record_stays_small(self):
        plan = core.plan([step("a", ["a.txt"])])
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        records = self.dispatch(plan, trees, mode="noisy")
        record = records[0]
        self.assertEqual(record["state"], "ok")
        self.assertLess(len(json.dumps(record)), 1500)
        self.assertLessEqual(len(record["returned"]["notes"]), core.NOTES_CAP)
        self.assertIn("prose line 4999", read(record["stdout"]))
        self.assertTrue(record["stdout"].startswith(self.run_dir))

    def a_resumed_ok_lane_is_not_dispatched_again(self):
        plan = core.plan([step("a", ["a.txt"]), step("b", ["b.txt"])])
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        prior = {0: {"lane": 0, "msp": 0, "state": "ok", "commit": None}}
        records = self.dispatch(plan, trees, prior=prior)
        self.assertEqual(records[0], prior[0])
        self.assertEqual(records[1]["state"], "ok")
        self.assertIsNone(self.marker(0))
        self.assertIsNotNone(self.marker(1))


def gated_step(name="impl", **extra):
    files = ["impl.txt", "tests/t_impl.txt"]
    acceptance = [{"file": "tests/t_impl.txt", "test": "property"}]
    return step(name, files, acceptance=acceptance, **extra)


class Resume(RepoCase):
    def the_run_directory_holds_the_plan_and_an_incremental_state(self):
        plan = core.plan([step("a", ["a.txt"]), step("b", ["b.txt"])])
        seen = []
        original = run.write_state

        def spy(run_dir, state):
            seen.append(json.loads(json.dumps(state)))
            return original(run_dir, state)

        run.write_state = spy
        try:
            final = self.execute(plan)
        finally:
            run.write_state = original
        self.assertEqual(json.loads(read(os.path.join(self.run_dir, run.PLAN_FILE)))["plan_id"], plan["plan_id"])
        self.assertEqual(self.state(), final)
        self.assertEqual(final["plan_id"], plan["plan_id"])
        self.assertEqual({r["state"] for r in final["lanes"].values()}, {"ok"})
        self.assertEqual(sorted(final["lanes"]), ["0", "1"])
        lane_counts = [len(s["lanes"]) for s in seen]
        self.assertIn(1, lane_counts)
        self.assertIn(2, lane_counts)
        self.assertTrue(all(s["plan_id"] == plan["plan_id"] for s in seen))

    def resume_refuses_a_different_plan(self):
        plan = core.plan([step("a", ["a.txt"])])
        os.makedirs(self.run_dir)
        write(os.path.join(self.run_dir, run.STATE_FILE), json.dumps({"plan_id": "someoneelse", "lanes": {}, "msps": {}}))
        with self.assertRaises(run.ResumeError) as caught:
            self.execute(plan, resume=True)
        self.assertIn("someoneelse", str(caught.exception))
        self.assertIn(plan["plan_id"], str(caught.exception))
        self.assertIsNone(self.marker(0))

    def resume_skips_completed_lanes(self):
        plan = core.plan([step("a", ["a.txt"]), step("b", ["b.txt"])])
        first = self.execute(plan, mode="crash-1")
        self.assertEqual(first["lanes"]["0"]["state"], "ok")
        self.assertEqual(first["lanes"]["1"]["state"], "failed")
        self.assertEqual(self.marker(0).count("ran"), 1)
        self.assertEqual(self.marker(1).count("ran"), 1)
        second = self.execute(plan, mode="ok", resume=True)
        self.assertEqual(second["lanes"]["0"], first["lanes"]["0"])
        self.assertEqual(second["lanes"]["1"]["state"], "ok")
        self.assertEqual(self.marker(0).count("ran"), 1)
        self.assertEqual(self.marker(1).count("ran"), 2)

    def a_gate_failure_ships_and_a_resume_repeats_neither_the_lane_nor_the_gate(self):
        self.add_remote()
        plan = core.plan([gated_step()])
        first = self.execute(plan, accept="inert")
        self.assertEqual(first["lanes"]["0"]["state"], "ok")
        self.assertEqual(first["msps"]["0"]["state"], "shipped")
        self.assertEqual(first["msps"]["0"]["gate"]["outcome"], "inert")
        self.assertEqual(self.marker(0).count("ran"), 1)
        first_gate_runs = len(self.gate_runs())
        self.assertGreater(first_gate_runs, 0)
        second = self.execute(plan, accept="real", resume=True)
        self.assertEqual(self.marker(0).count("ran"), 1)
        self.assertEqual(len(self.gate_runs()), first_gate_runs)
        self.assertEqual(second["msps"]["0"], first["msps"]["0"])
        self.assertEqual(len(self.pull_requests()), 1)

    def a_pushed_msp_is_not_pushed_again_on_resume(self):
        self.add_remote()
        plan = core.plan([gated_step(), step("b", ["b.txt"], after=["impl"])])
        first = self.execute(plan, pr=None)
        self.assertEqual({m["state"] for m in first["msps"].values()}, {run.MSP_PUSHED})
        gate_runs = len(self.gate_runs())
        self.assertGreater(gate_runs, 0)
        heads = sh(["git", "ls-remote", "--heads", "origin"], self.repo)
        for tree in first["worktrees"]:
            write(os.path.join(tree["path"], "later.txt"), "written after the push\n")
        second = self.execute(plan, pr=None, resume=True)
        self.assertEqual(len(self.gate_runs()), gate_runs)
        self.assertEqual(sh(["git", "ls-remote", "--heads", "origin"], self.repo), heads)
        self.assertEqual(second["msps"], first["msps"])
        for record in second["msps"].values():
            self.assertEqual(record["state"], run.MSP_PUSHED)
            self.assertTrue(record["ship"]["title"].startswith("feat(ship): "))
            self.assertTrue(record["ship"]["body"].startswith("## What changed\n"))
        self.assertEqual(self.pull_requests(), [])

    def a_merged_predecessor_satisfies_its_dependents(self):
        plan = core.plan([step("a", ["a.txt"]), step("b", ["b.txt"], after=["a"])])
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        landed = self.commit_file(trees[0]["path"], "a.txt", "work by a human-merged lane\n")
        sh(["git", "worktree", "remove", "--force", trees[0]["path"]], self.repo)
        sh(["git", "merge", "-q", "--ff-only", trees[0]["branch"]], self.repo)
        sh(["git", "branch", "-D", trees[0]["branch"]], self.repo)
        self.assertNotIn(trees[0]["branch"], self.branches())
        prior = {0: {"lane": 0, "msp": 0, "state": "ok", "commit": landed}}
        records = self.dispatch(plan, trees, prior=prior)
        self.assertEqual(records[1]["state"], "ok")
        self.assertIn("a.txt", self.marker(1))
        self.assertTrue(os.path.isfile(os.path.join(trees[1]["path"], "a.txt")))

    def an_unreachable_deleted_predecessor_merge_blocks_its_dependents(self):
        plan = core.plan([step("a", ["a.txt"]), step("b", ["b.txt"], after=["a"])])
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        landed = self.commit_file(trees[0]["path"], "a.txt", "never merged\n")
        sh(["git", "worktree", "remove", "--force", trees[0]["path"]], self.repo)
        sh(["git", "branch", "-D", trees[0]["branch"]], self.repo)
        prior = {0: {"lane": 0, "msp": 0, "state": "ok", "commit": landed}}
        records = self.dispatch(plan, trees, prior=prior)
        self.assertEqual(records[1]["state"], "merge-blocked")
        self.assertIn(trees[0]["branch"], records[1]["reason"])
        self.assertIsNone(self.marker(1))

    def document_drift_reports_without_blocking_resume(self):
        doc = os.path.join(self.repo, "docs", "change.md")
        write(doc, "original\n")
        source = {"path": "docs/change.md", "sha256": sha256_of(doc)}
        plan = core.plan([step("a", ["a.txt"], source=source)])
        first = self.execute(plan, mode="crash")
        self.assertFalse(first["drift"]["drifted"])
        self.assertEqual(first["lanes"]["0"]["state"], "failed")
        write(doc, "changed after the plan was made\n")
        second = self.execute(plan, mode="ok", resume=True)
        self.assertTrue(second["drift"]["drifted"])
        self.assertEqual(second["drift"]["path"], "docs/change.md")
        self.assertEqual(second["lanes"]["0"]["state"], "ok")
        self.assertEqual(self.marker(0).count("ran"), 2)


class Gate(RepoCase):
    def a_load_bearing_property_passes_the_gate(self):
        plan = core.plan([gated_step()])
        trees = self.build(plan)
        result = self.gate(plan, trees, accept="real")
        self.assertEqual(result["outcome"], "pass")
        self.assertFalse(result["blocks"])
        self.assertEqual(result["counts"]["pass"], 1)
        self.assertEqual(result["reverted"], ["impl.txt"])
        self.assertEqual([p["outcome"] for p in result["properties"]], ["pass"])
        runs = self.gate_runs()
        self.assertEqual(len(runs), 2)
        self.assertIn("work=True", runs[0])
        self.assertIn("work=False", runs[1])
        tree = trees[0]["path"]
        self.assertEqual(sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], tree), trees[0]["branch"])
        self.assertEqual(sh(["git", "status", "--porcelain"], tree), "")
        self.assertEqual(read(os.path.join(tree, "impl.txt")), "work by lane 0\n")
        self.assertFalse(any(b.startswith(run.PROBE_PREFIX + "/") for b in self.branches()))

    def an_inert_acceptance_property_is_reported_in_the_pull_request(self):
        plan = core.plan([gated_step()])
        trees = self.build(plan)
        result = self.gate(plan, trees, accept="inert")
        self.assertEqual(result["outcome"], "inert")
        self.assertTrue(result["blocks"])
        self.assertEqual(run.gate_state(result), "gate-failed")
        shutil.rmtree(self.run_dir, ignore_errors=True)
        self.add_remote()
        state = self.execute(core.plan([gated_step()]), accept="inert", pr=self.pr_command())
        self.assertEqual(state["msps"]["0"]["state"], "shipped")
        self.assertEqual(state["msps"]["0"]["gate"]["outcome"], "inert")
        opened = self.pull_requests()
        self.assertEqual(len(opened), 1)
        body = opened[0][3]
        self.assertIn("\nNot verified: tests/t_impl.txt::property (impl) is inert", body)
        self.assertNotIn("passes with the implementation reverted", body)
        self.assertNotIn("Gate:", body)

    def a_step_that_changed_no_implementation_does_not_block(self):
        self.seed_existing("impl.txt", "already works\n")
        plan = core.plan([gated_step()])
        trees = self.build(plan, mode="tests-only")
        result = self.gate(plan, trees, accept="inert")
        self.assertEqual(result["outcome"], "not-applicable")
        self.assertFalse(result["blocks"])
        self.assertIsNone(run.gate_state(result))
        self.assertEqual(result["counts"]["inert"], 0)
        self.assertIn("changed no implementation", result["properties"][0]["reason"])

    def a_step_that_is_only_a_test_file_is_never_excused(self):
        self.seed_existing("impl.txt", "already works\n")
        only_test = step(
            "impl",
            ["tests/t_impl.txt"],
            acceptance=[{"file": "tests/t_impl.txt", "test": "property"}],
        )
        plan = core.plan([only_test])
        self.assertEqual(run.step_implementation_paths(plan, 0, "impl"), [])
        trees = self.build(plan)
        result = self.gate(plan, trees, accept="inert")
        self.assertEqual(result["outcome"], "inert")
        self.assertTrue(result["blocks"])

    def a_step_with_nothing_to_change_and_nothing_to_implement_still_blocks(self):
        self.seed_existing("tests/t_impl.txt", "a property that already holds\n")
        only_test = step(
            "impl",
            ["tests/t_impl.txt"],
            acceptance=[{"file": "tests/t_impl.txt", "test": "property"}],
        )
        plan = core.plan([only_test])
        trees = self.build(plan, mode="nothing")
        result = self.gate(plan, trees, accept="inert")
        self.assertEqual(result["outcome"], "inert")
        self.assertTrue(result["blocks"])

    def a_step_that_did_change_implementation_is_still_called_inert(self):
        self.seed_existing("impl.txt", "already works\n")
        plan = core.plan([gated_step()])
        trees = self.build(plan, mode="ok")
        result = self.gate(plan, trees, accept="inert")
        self.assertEqual(result["outcome"], "inert")
        self.assertTrue(result["blocks"])

    def a_reverted_build_error_is_inconclusive_not_a_pass(self):
        plan = core.plan([gated_step()])
        trees = self.build(plan)
        result = self.gate(plan, trees, accept="brittle")
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertTrue(result["blocks"])
        self.assertEqual(run.gate_state(result), "gate-inconclusive")
        self.assertEqual(result["properties"][0]["reverted"], 2)
        self.assertEqual(result["counts"]["pass"], 0)

    def a_property_failing_with_the_work_present_is_inconclusive(self):
        plan = core.plan([gated_step()])
        trees = self.build(plan)
        result = self.gate(plan, trees, accept="failing")
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertTrue(result["blocks"])
        self.assertIn("work present", result["properties"][0]["reason"])
        self.assertEqual(len(self.gate_runs()), 1)

    def a_step_whose_test_file_is_not_named_is_counted_not_passed(self):
        plan = core.plan([step("impl", ["impl.txt", "tests/t_impl.txt"])])
        self.assertEqual(run.implementation_paths(plan, 0), ["impl.txt", "tests/t_impl.txt"])
        trees = self.build(plan)
        result = self.gate(plan, trees, accept="real")
        self.assertEqual(result["outcome"], "not-applicable")
        self.assertFalse(result["blocks"])
        self.assertEqual(result["counts"], {"pass": 0, "inert": 0, "inconclusive": 0, "not-applicable": 1})
        self.assertEqual(self.gate_runs(), [])
        self.assertIsNone(run.gate_state(result))

    def the_implementation_set_is_the_write_set_minus_named_acceptance_files(self):
        plan = core.plan(
            [
                gated_step("one"),
                step("two", ["other.txt", "tests/t_other.txt"], msp="m", acceptance=[{"file": "tests/t_other.txt", "test": "p"}]),
                step("three", ["tests/t_impl.txt", "impl.txt"], msp="m"),
            ]
        )
        self.assertEqual(len(plan["msps"]), 1)
        self.assertEqual(run.implementation_paths(plan, 0), ["impl.txt", "other.txt"])

    def a_serial_acceptance_property_is_counted_not_run(self):
        plan = core.plan([gated_step()], serial_markers=["impl.txt"])
        self.assertEqual(plan["verify_modes"]["impl"], "serial")
        trees = self.build(plan)
        result = self.gate(plan, trees, accept="inert")
        self.assertEqual(result["outcome"], "not-applicable")
        self.assertFalse(result["blocks"])
        self.assertEqual(result["counts"]["not-applicable"], 1)
        self.assertIn("serial", result["properties"][0]["reason"])
        self.assertEqual(self.gate_runs(), [])

    def an_empty_acceptance_list_is_counted_and_does_not_block(self):
        plan = core.plan([step("a", ["a.txt"])])
        trees = self.build(plan)
        result = self.gate(plan, trees, accept="inert")
        self.assertEqual(result["outcome"], "not-applicable")
        self.assertFalse(result["blocks"])
        self.assertEqual(result["counts"]["not-applicable"], 1)
        self.assertEqual(result["properties"][0]["step"], "a")
        self.assertEqual(self.gate_runs(), [])
        shutil.rmtree(self.run_dir, ignore_errors=True)
        state = self.execute(core.plan([step("a", ["a.txt"])]), accept="inert")
        self.assertNotIn(state["msps"]["0"]["state"], ("gate-failed", "gate-inconclusive", run.MSP_BLOCKED))
        self.assertEqual(state["msps"]["0"]["gate"]["counts"]["not-applicable"], 1)

    def the_probe_branch_is_deleted_even_when_the_gate_throws(self):
        plan = core.plan([gated_step()])
        trees = self.build(plan)
        tree = trees[0]["path"]
        with self.assertRaises(FileNotFoundError):
            self.gate(plan, trees, command=os.path.join(self.tmp, "no-such-runner") + " {file} {test}")
        self.assertFalse(any(b.startswith(run.PROBE_PREFIX + "/") for b in self.branches()))
        self.assertEqual(sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], tree), trees[0]["branch"])
        self.assertEqual(sh(["git", "status", "--porcelain"], tree), "")
        self.assertEqual(read(os.path.join(tree, "impl.txt")), "work by lane 0\n")

    def a_gate_that_throws_records_the_error_and_still_ships(self):
        self.add_remote()
        plan = core.plan([gated_step()])
        state = run.execute(
            plan,
            self.repo,
            "main",
            self.run_dir,
            self.worker_command("ok"),
            os.path.join(self.tmp, "no-such-runner") + " {file} {test}",
            self.pr_command(),
            60,
            2,
            trees_root=self.trees,
            header=HEADER,
        )
        self.assertEqual(state["msps"]["0"]["state"], "shipped")
        self.assertIsNone(state["msps"]["0"]["gate"])
        self.assertIn("no-such-runner", state["msps"]["0"]["gate_error"])
        self.assertEqual(len(self.pull_requests()), 1)

    def an_unproven_property_is_named_in_the_body_with_its_step(self):
        self.add_remote()
        plan = core.plan([gated_step()])
        state = self.execute(plan, accept="inert", pr=self.pr_command())
        self.assertEqual(state["msps"]["0"]["state"], "shipped")
        self.assertEqual(self.remote_heads(), ["mitosis/impl"])
        body = self.pull_requests()[0][3]
        self.assertIn("tests/t_impl.txt::property (impl) is inert", body)


class GateReuse(RepoCase):
    def _held_run(self, accept="real"):
        self.add_remote()
        plan = core.plan([gated_step()])
        held = self.execute(plan, accept=accept, no_push=True)
        self.assertEqual(held["msps"]["0"]["state"], run.MSP_COMMITTED)
        gate_runs = len(self.gate_runs())
        self.assertGreater(gate_runs, 0)
        return plan, held, gate_runs

    def test_resume_at_the_gated_head_runs_no_acceptance(self):
        plan, held, gate_runs = self._held_run()
        resumed = self.execute(plan, resume=True)
        self.assertEqual(len(self.gate_runs()), gate_runs)
        self.assertEqual(resumed["msps"]["0"]["state"], "shipped")
        self.assertEqual(resumed["msps"]["0"]["gate"], held["msps"]["0"]["gate"])

    def test_resume_after_a_new_commit_gates_again(self):
        plan, held, gate_runs = self._held_run()
        tree = held["worktrees"][0]["path"]
        new_head = self.commit_file(tree, "impl.txt", "work by lane 0\nmore work\n")
        resumed = self.execute(plan, resume=True)
        self.assertGreater(len(self.gate_runs()), gate_runs)
        self.assertEqual(resumed["msps"]["0"]["gate"]["commit"], new_head)

    def test_resume_with_an_uncommitted_change_gates_again(self):
        plan, held, gate_runs = self._held_run()
        tree = held["worktrees"][0]["path"]
        write(os.path.join(tree, "impl.txt"), "work by lane 0\nuncommitted work\n")
        self.execute(plan, resume=True)
        self.assertGreater(len(self.gate_runs()), gate_runs)

    def test_resume_after_a_blocking_gate_gates_again(self):
        plan, held, gate_runs = self._held_run(accept="exit7")
        self.assertEqual(held["msps"]["0"]["gate"]["outcome"], "inconclusive")
        resumed = self.execute(plan, accept="real", resume=True)
        self.assertGreater(len(self.gate_runs()), gate_runs)
        self.assertEqual(resumed["msps"]["0"]["gate"]["outcome"], "pass")

    def test_resume_with_a_file_git_status_hides_gates_again(self):
        plan, held, gate_runs = self._held_run()
        tree = held["worktrees"][0]["path"]
        sh(["git", "config", "status.showUntrackedFiles", "no"], tree)
        write(os.path.join(tree, "ungated.txt"), "never gated\n")
        resumed = self.execute(plan, resume=True)
        record = resumed["msps"]["0"]
        self.assertGreater(len(self.gate_runs()), gate_runs)
        self.assertEqual(record["state"], "shipped")
        self.assertEqual(record["gate"]["commit"], record["ship"]["pushed"])
        self.assertIn(
            "ungated.txt",
            sh(["git", "ls-tree", "--name-only", record["gate"]["commit"]], tree).splitlines(),
        )

    def test_resume_with_a_submodule_move_git_status_hides_gates_again(self):
        source = os.path.join(self.tmp, "dependency")
        sh(["git", "init", "-q", "-b", "main", source], self.tmp)
        self.commit_file(source, "dep.txt", "1\n")
        sh(
            ["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", source, "dep"],
            self.repo,
        )
        sh(["git", "commit", "-q", "-m", "add the dependency"], self.repo)
        plan, held, gate_runs = self._held_run()
        tree = held["worktrees"][0]["path"]
        sh(["git", "-c", "protocol.file.allow=always", "submodule", "update", "-q", "--init"], tree)
        sh(["git", "config", "diff.ignoreSubmodules", "all"], tree)
        self.commit_file(os.path.join(tree, "dep"), "dep.txt", "2\n")
        resumed = self.execute(plan, resume=True)
        record = resumed["msps"]["0"]
        self.assertGreater(len(self.gate_runs()), gate_runs)
        self.assertEqual(record["gate"]["commit"], record["ship"]["pushed"])


class Reconcile(RepoCase):
    def an_undeclared_file_is_reported_from_the_git_diff(self):
        plan = core.plan([step("a", ["a.txt"])])
        trees = self.build(plan, mode="undeclared")
        run.commit_all(trees[0]["path"], "everything")
        findings = run.reconcile(plan, 0, trees[0]["path"], trees[0]["branch"], "main")
        self.assertEqual(findings["undeclared"], ["stray.txt"])
        self.assertEqual(findings["unwritten"], [])
        self.assertEqual(findings["crossing"], [])
        self.assertFalse(findings["fatal"])
        self.assertEqual(sorted(findings["changed"]), ["a.txt", "stray.txt"])

    def a_declared_file_never_written_is_reported(self):
        plan = core.plan([step("a", ["a.txt", "never.txt"])])
        trees = self.build(plan, mode="partial")
        run.commit_all(trees[0]["path"], "everything")
        findings = run.reconcile(plan, 0, trees[0]["path"], trees[0]["branch"], "main")
        self.assertEqual(findings["unwritten"], ["never.txt"])
        self.assertEqual(findings["undeclared"], [])
        self.assertFalse(findings["fatal"])

    def an_existing_file_a_step_was_never_asked_to_change_is_not_a_finding(self):
        self.seed_existing("existing.txt", "already correct\n")
        plan = core.plan([step("a", ["a.txt", "existing.txt"])])
        trees = self.build(plan, mode="partial")
        run.commit_all(trees[0]["path"], "everything")
        findings = run.reconcile(plan, 0, trees[0]["path"], trees[0]["branch"], "main")
        self.assertEqual(findings["unwritten"], [])
        self.assertEqual(findings["untouched"], ["existing.txt"])
        self.assertFalse(findings["fatal"])

    def an_existing_file_a_step_did_promise_to_prove_is_still_a_finding(self):
        self.seed_existing("existing.txt", "already correct\n")
        gated = step(
            "a",
            ["a.txt", "existing.txt"],
            acceptance=[{"file": "a.txt", "test": "property"}],
        )
        plan = core.plan([gated])
        trees = self.build(plan, mode="partial")
        run.commit_all(trees[0]["path"], "everything")
        findings = run.reconcile(plan, 0, trees[0]["path"], trees[0]["branch"], "main")
        self.assertEqual(findings["unwritten"], ["existing.txt"])
        self.assertEqual(findings["untouched"], [])

    def a_declared_file_that_never_existed_is_still_a_finding(self):
        plan = core.plan([step("a", ["a.txt", "never.txt"])])
        trees = self.build(plan, mode="partial")
        run.commit_all(trees[0]["path"], "everything")
        findings = run.reconcile(plan, 0, trees[0]["path"], trees[0]["branch"], "main")
        self.assertEqual(findings["unwritten"], ["never.txt"])
        self.assertEqual(findings["untouched"], [])

    def a_file_crossing_an_msp_boundary_is_fatal(self):
        plan = core.plan([step("a", ["a.txt"]), step("b", ["b.txt"], msp="beta")])
        trees = self.build(plan, mode="cross")
        run.commit_all(trees[0]["path"], "everything")
        findings = run.reconcile(plan, 0, trees[0]["path"], trees[0]["branch"], "main")
        self.assertTrue(findings["fatal"])
        self.assertEqual([c["path"] for c in findings["crossing"]], ["b.txt"])
        self.assertEqual(findings["crossing"][0]["msp"], 1)
        self.assertEqual(findings["crossing"][0]["label"], "beta")
        self.assertEqual(findings["undeclared"], ["b.txt"])
        shutil.rmtree(self.run_dir, ignore_errors=True)
        self.add_remote()
        state = self.execute(plan, mode="cross", pr=self.pr_command())
        self.assertTrue(state["msps"]["0"]["reconcile"]["fatal"])
        self.assertEqual(state["msps"]["0"]["state"], "shipped")
        opened = {pr[0]: pr[3] for pr in self.pull_requests()}
        self.assertIn(trees[0]["branch"], opened)
        self.assertIn(
            "\n\n## Risk\nReconcile found 1 undeclared write (b.txt); another MSP also changes 1 "
            "file this branch writes (b.txt).\n\n",
            opened[trees[0]["branch"]],
        )

    def reconcile_ignores_the_worker_self_report(self):
        plan = core.plan([step("a", ["a.txt", "never.txt"])])
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        records = self.dispatch(plan, trees, mode="partial")
        self.assertEqual(records[0]["returned"]["files_changed"], ["a.txt", "never.txt"])
        write(os.path.join(trees[0]["path"], "stray.txt"), "not in any report\n")
        run.commit_all(trees[0]["path"], "everything")
        findings = run.reconcile(plan, 0, trees[0]["path"], trees[0]["branch"], "main")
        self.assertEqual(findings["unwritten"], ["never.txt"])
        self.assertEqual(findings["undeclared"], ["stray.txt"])
        self.assertEqual(sorted(findings["changed"]), ["a.txt", "stray.txt"])

    def a_producers_merged_files_are_not_this_msps_writes(self):
        plan = core.plan([step("a", ["a.txt"]), step("b", ["b.txt"], after=["a"])])
        trees = self.build(plan)
        run.commit_all(trees[1]["path"], "everything")
        self.assertTrue(os.path.isfile(os.path.join(trees[1]["path"], "a.txt")))
        findings = run.reconcile(plan, 1, trees[1]["path"], trees[1]["branch"], "main", (trees[0]["branch"],))
        self.assertEqual(findings["changed"], ["b.txt"])
        self.assertEqual(findings["undeclared"], [])
        self.assertEqual(findings["crossing"], [])
        self.assertFalse(findings["fatal"])


class Ship(RepoCase):
    def a_dependent_pull_request_targets_its_predecessor(self):
        self.add_remote()
        plan = core.plan([step("a", ["a.txt"]), step("b", ["b.txt"], after=["a"])])
        state = self.execute(plan, pr=self.pr_command())
        self.assertEqual(state["msps"]["0"]["state"], "shipped")
        self.assertEqual(state["msps"]["1"]["state"], "shipped")
        trees = state["worktrees"]
        opened = {pr[0]: pr for pr in self.pull_requests()}
        self.assertEqual(opened[trees[0]["branch"]][1], "main")
        self.assertEqual(opened[trees[1]["branch"]][1], trees[0]["branch"])
        self.assertEqual(state["msps"]["1"]["ship"]["base"], trees[0]["branch"])
        self.assertEqual(self.remote_heads(), sorted(t["branch"] for t in trees))
        self.assertEqual(state["stacking_exceptions"], [])
        self.assertTrue(state["msps"]["0"]["ship"]["pull_request"].startswith("https://example.invalid/pull/"))

    def an_msp_with_two_producers_targets_the_feature_branch_and_is_reported(self):
        self.add_remote()
        plan = core.plan(
            [step("a", ["a.txt"]), step("b", ["b.txt"]), step("c", ["c.txt"], after=["a", "b"])]
        )
        state = self.execute(plan, pr=self.pr_command())
        self.assertEqual({m["state"] for m in state["msps"].values()}, {"shipped"})
        consumer = lane_named(plan, "c")
        msp = plan["lanes"][consumer]["msp"]
        trees = state["worktrees"]
        opened = {pr[0]: pr for pr in self.pull_requests()}
        self.assertEqual(opened[trees[msp]["branch"]][1], "main")
        self.assertEqual(len(state["stacking_exceptions"]), 1)
        exception = state["stacking_exceptions"][0]
        self.assertEqual(exception["msp"], msp)
        self.assertEqual(sorted(exception["producers"]), sorted(p for p in range(3) if p != msp))
        self.assertEqual(sorted(state["lanes"][str(consumer)]["merged"]), sorted(trees[p]["branch"] for p in exception["producers"]))
        self.assertTrue(os.path.isfile(os.path.join(trees[msp]["path"], "a.txt")))
        self.assertTrue(os.path.isfile(os.path.join(trees[msp]["path"], "b.txt")))
        self.assertIn("producers", opened[trees[msp]["branch"]][3])

    def a_pull_request_command_receives_the_house_title_and_body(self):
        self.add_remote()
        plan = core.plan([gated_step()])
        state = self.execute(plan, pr=self.pr_command())
        record = state["msps"]["0"]
        self.assertEqual(record["state"], "shipped")
        self.assertEqual(record["ship"]["pull_request"], "https://example.invalid/pull/1")
        title, body = self.house_text(plan, state, 0)
        self.assertEqual(self.pull_requests(), [[record["ship"]["branch"], "main", title, body]])
        self.assertEqual(title, "feat(ship): write pull requests in the house format")
        self.assertIn(
            "\nVerified: tests/t_impl.txt::property (impl) fails with the work reverted and "
            "passes with it",
            body,
        )
        self.assertNotIn("draft", body.lower())
        self.assertNotIn(self.acceptance_command("real"), body)
        self.assertEqual((record["ship"]["title"], record["ship"]["body"]), (title, body))

    def without_a_pull_request_command_each_msp_is_pushed_and_its_text_recorded(self):
        self.add_remote()
        self.seed_existing("a.txt", "already done\n")
        plan = core.plan([step("a", ["a.txt"]), step("b", ["b.txt"], after=["a"]), gated_step("c")])
        index = {plan["msps"][m]["steps"][0]: m for m in range(len(plan["msps"]))}
        state = self.execute(plan, mode="nothing-%d" % lane_named(plan, "a"), pr=None)
        self.assertEqual(state["msps"][str(index["a"])]["state"], run.MSP_UNCHANGED)
        self.assertEqual(self.remote_heads(), ["mitosis/b", "mitosis/c"])
        self.assertEqual(self.pull_requests(), [])
        for name in ("b", "c"):
            record = state["msps"][str(index[name])]
            self.assertEqual(record["state"], run.MSP_PUSHED)
            self.assertIn("no pull-request command was given", record["reason"])
            self.assertEqual(record["ship"]["pull_request"], "")
            self.assertEqual(
                (record["ship"]["title"], record["ship"]["body"]),
                self.house_text(plan, state, index[name]),
            )
        self.assertIn(
            "\nVerified: tests/t_impl.txt::property (c) fails with the work reverted and passes "
            "with it",
            state["msps"][str(index["c"])]["ship"]["body"],
        )
        unchanged = state["msps"][str(index["a"])]["ship"]
        self.assertNotIn("title", unchanged)
        self.assertNotIn("body", unchanged)
        self.assertTrue(run.succeeded(state))
        self.assertEqual(self.state(), state)
        with self.assertRaises(run.ConfigError):
            self.execute(plan, pr=None, header=None, resume=True)

    def a_resume_keeps_the_door_call_the_run_recorded(self):
        self.add_remote()
        plan = core.plan([step("a", ["a.txt"])])
        guarded = {**HEADER, "door": "two-way", "blast_radius": "Docs only."}
        held = self.execute(plan, pr=None, header=guarded, no_push=True)
        self.assertEqual(held["merge_danger"], {"door": "two-way", "blast_radius": "Docs only."})
        shipped = self.execute(plan, pr=None, header=HEADER, resume=True)
        self.assertEqual(shipped["merge_danger"], held["merge_danger"])
        self.assertEqual(
            section_of(shipped["msps"]["0"]["ship"]["body"], "## Risk")[:2],
            ["Door: two-way", "Blast radius: Docs only."],
        )
        with self.assertRaises(run.ResumeError):
            self.execute(
                plan,
                pr=None,
                header={**HEADER, "door": "one-way", "blast_radius": "Docs only."},
                resume=True,
            )

    def a_gate_failure_no_longer_blocks_the_cluster_below_it(self):
        self.add_remote()
        plan = core.plan([gated_step(), step("b", ["b.txt"], after=["impl"])])
        state = self.execute(plan, accept="inert", pr=self.pr_command())
        self.assertEqual({m["state"] for m in state["msps"].values()}, {"shipped"})
        self.assertEqual(state["msps"]["0"]["gate"]["outcome"], "inert")
        self.assertEqual(len(self.pull_requests()), 2)
        self.assertEqual(sorted(self.remote_heads()), ["mitosis/b", "mitosis/impl"])

    def an_msp_that_changed_nothing_is_unchanged_and_never_pushed(self):
        self.add_remote()
        self.seed_existing("a.txt", "already done\n")
        plan = core.plan([step("a", ["a.txt"])])
        state = self.execute(plan, mode="nothing")
        self.assertEqual(state["msps"]["0"]["state"], run.MSP_UNCHANGED)
        self.assertIn("main", state["msps"]["0"]["reason"])
        self.assertIsNone(state["msps"]["0"]["ship"]["pushed"])
        self.assertEqual(self.remote_heads(), [])
        self.assertEqual(self.pull_requests(), [])
        self.assertTrue(run.succeeded(state))

    def a_branch_with_nothing_new_is_unchanged_after_its_base_moves_on(self):
        self.add_remote()
        plan = core.plan([step("a", ["a.txt"])])
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        self.commit_file(self.repo, "later.txt", "landed on main after the branch was cut\n")
        shipped = run.ship(
            plan,
            0,
            trees[0]["path"],
            trees[0]["branch"],
            "main",
            self.pr_command(),
            "origin",
            os.path.join(self.run_dir, "ship"),
            header=HEADER,
            timeout=60,
        )
        self.assertTrue(shipped["unchanged"])
        self.assertEqual(self.remote_heads(), [])
        self.assertEqual(self.pull_requests(), [])

    def a_held_unchanged_msp_is_rechecked_when_its_producer_fails_to_ship(self):
        self.add_remote()
        self.seed_existing("b.txt", "already done\n")
        plan = core.plan(
            [step("a", ["a.txt"]), step("b", ["b.txt"], after=["a"]), step("c", ["c.txt"], after=["b"])]
        )
        held = run.execute(
            plan,
            self.repo,
            "main",
            self.run_dir,
            self.worker_command("nothing-%d" % lane_named(plan, "b")),
            self.acceptance_command(),
            None,
            60,
            2,
            trees_root=self.trees,
            no_push=True,
        )
        by_step = lambda state: {plan["msps"][int(m)]["steps"][0]: r["state"] for m, r in state["msps"].items()}
        self.assertEqual(
            by_step(held), {"a": run.MSP_COMMITTED, "b": run.MSP_UNCHANGED, "c": run.MSP_COMMITTED}
        )
        refusing = os.path.join(self.tmp, "refuse-a.py")
        write(refusing, "import sys\nsys.exit(1 if sys.argv[1] == 'mitosis/a' else 0)\n")
        resumed = self.execute(plan, resume=True, pr="%s %s {branch}" % (shlex.quote(sys.executable), shlex.quote(refusing)))
        self.assertEqual(
            by_step(resumed), {"a": "ship-failed", "b": run.MSP_BLOCKED, "c": run.MSP_BLOCKED}
        )
        self.assertNotIn("mitosis/c", self.remote_heads())

    def a_rerun_msp_keeps_nothing_from_its_earlier_pass(self):
        self.add_remote()
        plan = core.plan([step("a", ["a.txt"]), step("b", ["b.txt"], after=["a"])])
        held = run.execute(
            plan, self.repo, "main", self.run_dir, self.worker_command(), self.acceptance_command(),
            None, 60, 2, trees_root=self.trees, no_push=True,
        )
        self.assertEqual({m["state"] for m in held["msps"].values()}, {run.MSP_COMMITTED})
        self.assertIn("ship", held["msps"]["1"])
        refusing = os.path.join(self.tmp, "refuse-a.py")
        write(refusing, "import sys\nsys.exit(1 if sys.argv[1] == 'mitosis/a' else 0)\n")
        again = self.execute(
            plan, resume=True, pr="%s %s {branch}" % (shlex.quote(sys.executable), shlex.quote(refusing))
        )
        self.assertEqual(again["msps"]["0"]["state"], "ship-failed")
        self.assertEqual(again["msps"]["1"]["state"], run.MSP_BLOCKED)
        self.assertIsNone(again["msps"]["1"].get("ship"))

    def a_stacking_exception_from_an_earlier_pass_is_dropped_when_the_msp_stacks(self):
        self.add_remote()
        plan = core.plan([step("a", ["a.txt"]), step("b", ["b.txt"]), step("c", ["c.txt"], after=["a", "b"])])
        index = {plan["msps"][m]["steps"][0]: m for m in range(len(plan["msps"]))}
        held = run.execute(
            plan, self.repo, "main", self.run_dir, self.worker_command(), self.acceptance_command(),
            None, 60, 2, trees_root=self.trees, no_push=True,
        )
        self.assertEqual([e["msp"] for e in held["stacking_exceptions"]], [index["c"]])
        sh(["git", "merge", "-q", "--no-edit", held["msps"][str(index["b"])]["ship"]["branch"]], self.repo)
        again = self.execute(plan, resume=True)
        self.assertEqual(again["msps"][str(index["b"])]["state"], run.MSP_UNCHANGED)
        self.assertEqual(again["stacking_exceptions"], [])
        self.assertEqual(again["msps"][str(index["c"])]["ship"]["base"], "mitosis/a")

    def a_chain_of_unchanged_producers_is_followed_to_a_branch_that_ships(self):
        plan = core.plan(
            [step("a", ["a.txt"]), step("b", ["b.txt"], after=["a"]), step("c", ["c.txt"], after=["b"])]
        )
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        index = {plan["msps"][m]["steps"][0]: m for m in range(len(plan["msps"]))}
        settled = {
            str(index["a"]): {"state": run.MSP_UNCHANGED, "ship": {"base": "main"}},
            str(index["b"]): {"state": run.MSP_UNCHANGED, "ship": {"base": trees[index["a"]]["branch"]}},
        }
        self.assertEqual(
            run.pr_base(plan, index["c"], "main", trees, self.repo, settled), ("main", None)
        )
        shipped = {**settled, str(index["a"]): {"state": "shipped"}}
        self.assertEqual(
            run.pr_base(plan, index["c"], "main", trees, self.repo, shipped),
            (trees[index["a"]]["branch"], None),
        )

    def a_dependent_of_an_unchanged_msp_targets_that_msps_base(self):
        self.add_remote()
        self.seed_existing("a.txt", "already done\n")
        plan = core.plan([step("a", ["a.txt"]), step("b", ["b.txt"], after=["a"])])
        state = self.execute(plan, mode="nothing-%d" % lane_named(plan, "a"))
        by_step = {plan["msps"][int(m)]["steps"][0]: r for m, r in state["msps"].items()}
        self.assertEqual(by_step["a"]["state"], run.MSP_UNCHANGED)
        self.assertEqual(by_step["b"]["state"], "shipped")
        opened = self.pull_requests()
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0][:2], [by_step["b"]["ship"]["branch"], "main"])
        self.assertEqual(self.remote_heads(), [by_step["b"]["ship"]["branch"]])
        self.assertTrue(run.succeeded(state))

    def a_held_run_commits_every_branch_and_pushes_nothing_until_resumed(self):
        self.add_remote()
        plan = core.plan([step("a", ["a.txt"]), step("b", ["b.txt"], after=["a"])])
        held = run.execute(
            plan,
            self.repo,
            "main",
            self.run_dir,
            self.worker_command(),
            self.acceptance_command(),
            None,
            60,
            2,
            trees_root=self.trees,
            no_push=True,
        )
        self.assertEqual({m["state"] for m in held["msps"].values()}, {run.MSP_COMMITTED})
        self.assertEqual(self.remote_heads(), [])
        self.assertEqual(self.pull_requests(), [])
        self.assertTrue(run.succeeded(held))
        for record in held["msps"].values():
            log = sh(["git", "log", "--format=%s", "main.." + record["ship"]["branch"]], self.repo)
            self.assertIn("land Lane", log)
            self.assertNotIn("check that a Lane can land", log)
        shipped = self.execute(plan, resume=True)
        self.assertEqual({m["state"] for m in shipped["msps"].values()}, {"shipped"})
        self.assertEqual(len(self.remote_heads()), 2)
        self.assertEqual(len(self.pull_requests()), 2)

    def mitosis_never_merges(self):
        self.add_remote()
        plan = core.plan([step("a", ["a.txt"]), step("b", ["b.txt"], after=["a"])])
        before = sh(["git", "rev-parse", "main"], self.repo)
        state = self.execute(plan, pr=self.pr_command())
        self.assertEqual({m["state"] for m in state["msps"].values()}, {"shipped"})
        self.assertEqual(sh(["git", "rev-parse", "main"], self.repo), before)
        self.assertNotIn("main", self.remote_heads())
        for opened in self.pull_requests():
            self.assertFalse(any("merge" in arg.lower() for arg in opened[:3]))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "a.txt")))

    def a_ship_failure_is_recorded_not_raised(self):
        plan = core.plan([step("a", ["a.txt"])])
        state = self.execute(plan, pr=self.pr_command())
        self.assertEqual(state["msps"]["0"]["state"], "ship-failed")
        self.assertIn("origin", state["msps"]["0"]["reason"])
        self.assertEqual(self.pull_requests(), [])

    def a_failing_pull_request_command_is_ship_failed(self):
        self.add_remote()
        plan = core.plan([step("a", ["a.txt"])])
        state = self.execute(plan, pr=os.path.join(self.tmp, "no-such-pr-tool") + " {branch} {base}")
        self.assertEqual(state["msps"]["0"]["state"], "ship-failed")
        self.assertIn("no-such-pr-tool", state["msps"]["0"]["reason"])

    def every_terminal_status_maps_to_an_exit_code(self):
        def state_with(msp_state, lane_state="ok", findings=None):
            return {
                "lanes": {"0": {"state": lane_state}},
                "msps": {"0": {"state": msp_state, "reconcile": findings}},
            }

        clean = {"undeclared": [], "unwritten": [], "crossing": [], "fatal": False}
        self.assertTrue(run.succeeded(state_with("shipped", findings=clean)))
        self.assertTrue(run.succeeded(state_with(run.MSP_UNCHANGED, findings=clean)))
        for msp_state in core.MSP_STATES:
            if msp_state not in core.DELIVERED_STATES:
                self.assertFalse(run.succeeded(state_with(msp_state, findings=clean)), msp_state)
        self.assertFalse(run.succeeded(state_with(run.MSP_BLOCKED, findings=clean)))
        self.assertFalse(run.succeeded(state_with(None, findings=clean)))
        for lane_state in core.LANE_STATES:
            if lane_state != "ok":
                self.assertFalse(run.succeeded(state_with("shipped", lane_state, clean)), lane_state)
        self.assertFalse(run.succeeded(state_with("shipped", findings={**clean, "undeclared": ["x"]})))
        self.assertFalse(run.succeeded(state_with("shipped", findings={**clean, "unwritten": ["x"]})))
        self.assertFalse(run.succeeded(state_with("shipped", findings=None)))
        self.assertFalse(run.succeeded({"lanes": {}, "msps": {}}))


HOUSE_TITLE = re.compile(
    r"^(?=.{1,72}$)(feat|fix|refactor|docs|test|chore|perf|ci)(\([a-z0-9][a-z0-9-]{0,15}\))?: "
    r"[a-z][\x20-\x7E]*[\x21-\x2D\x2F-\x7E]$"
)

JS_TRIM = frozenset(
    chr(code)
    for code in (
        *range(0x09, 0x0E),
        0x20,
        0xA0,
        0x1680,
        *range(0x2000, 0x200B),
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
        0xFEFF,
    )
)

HIDDEN_RANGES = (
    (0x80, 0x9F),
    (0xAD, 0xAD),
    (0x61C, 0x61C),
    (0x180E, 0x180E),
    (0x200B, 0x200F),
    (0x2028, 0x202F),
    (0x2060, 0x206F),
    (0xFEFF, 0xFEFF),
    (0xFFF9, 0xFFFB),
)

RECEIPT_LINE_TOKENS = ("work-type:", "test-removal:", "receipt:", "receipt-cmd:", "receipt-lock:")

CLEAN_FINDINGS = {"undeclared": [], "unwritten": [], "crossing": [], "fatal": False}


def broken_rule(value, cap=500):
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        return "control character"
    if not value:
        return "empty"
    if value[0] in JS_TRIM or value[-1] in JS_TRIM:
        return "edge whitespace"
    if len(value.encode("utf-16-le")) // 2 > cap:
        return "over the cap"
    if value.startswith("@"):
        return "leading @"
    if any(low <= ord(c) <= high for c in value for low, high in HIDDEN_RANGES):
        return "invisible character"
    if re.search(r"<[!/?A-Za-z]", value):
        return "tag opener"
    if re.search(r"&(?:#[0-9]+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);", value):
        return "character reference"
    if "\\" in value:
        return "backslash"
    if any(construct in value for construct in ("](", "![", "]:", "$")):
        return "inline construct"
    if value[0] in "`~#>|":
        return "block opener"
    if set(value) <= set("=-"):
        return "setext underline"
    if value.lower().startswith(("verified:", "not verified:", "door:", "blast radius:")):
        return "reserved field prefix"
    if value.startswith("Supersedes "):
        return "reserved structure prefix"
    return None


def reads_as_sentence(value):
    first = value[0]
    return first.lower() != first and first.upper() == first and value[-1] in ".?!"


def body_problems(body):
    found = ()
    blocks = body.split("\n\n")
    headings = [block.split("\n")[0] for block in blocks]
    if headings not in (
        ["## What changed", "## Why", "## Verification"],
        ["## What changed", "## Why", "## Risk", "## Verification"],
    ):
        found = found + (("layout", headings),)
    for block in blocks:
        heading, *lines = block.split("\n")
        if heading == "## What changed":
            values = [line[2:] for line in lines]
            found = found + tuple(("bullet", line) for line in lines if not line.startswith("- "))
            found = found + tuple(
                ("what sentence", value)
                for value in values
                if not reads_as_sentence(value) or value.startswith(RECEIPT_LINE_TOKENS)
            )
        elif heading == "## Why":
            values = lines
            found = found + tuple(
                ("why sentence", value)
                for value in values
                if not value.startswith(RECEIPT_LINE_TOKENS) and not reads_as_sentence(value)
            )
        elif heading == "## Risk":
            called = lines[:1] != [] and lines[0].startswith("Door: ")
            door, radius = (lines[0], lines[1:2]) if called else (None, [])
            rest = lines[2:] if called else lines
            found = found + (
                (("door call", lines[:2]),)
                if called
                and (
                    door[len("Door: "):] not in ("one-way", "two-way")
                    or not radius
                    or not radius[0].startswith("Blast radius: ")
                )
                else ()
            )
            values = [line[len("Blast radius: "):] for line in radius] + rest
            found = found + ((("risk lines", lines),) if not lines or len(rest) > 1 else ())
            found = found + tuple(
                ("posing merge-danger line", line)
                for line in rest
                if re.match(r"(?i)(door|blast radius):", line)
            )
            found = found + tuple(
                ("risk sentence", value) for value in values if not reads_as_sentence(value)
            )
        else:
            verified = [line[len("Verified: "):] for line in lines if line.startswith("Verified: ")]
            unproven = [
                line[len("Not verified: "):] for line in lines if line.startswith("Not verified: ")
            ]
            ordered = ["Verified: " + v for v in verified] + ["Not verified: " + v for v in unproven]
            found = found + ((("verification order", lines),) if lines != ordered else ())
            found = found + (
                (("verification limit", len(verified), len(unproven)),)
                if len(verified) > 8 or len(unproven) > 8
                else ()
            )
            values = verified + unproven
        found = found + ((("limit", heading, len(values)),) if heading != "## Verification" and len(values) > 8 else ())
        found = found + tuple((broken_rule(value), value) for value in values if broken_rule(value))
    return found


def section_of(body, heading):
    blocks = [block.split("\n") for block in body.split("\n\n")]
    found = [lines[1:] for lines in blocks if lines[0] == heading]
    return found[0] if found else None


class HouseFormat(unittest.TestCase):
    def the_verification_section_ends_with_its_closing_lines_past_the_limit(self):
        gated_commit = "a" * 40
        proven_commit = "b" * 40
        plan = core.plan(
            [
                step(
                    "impl",
                    ["impl.txt", "tests/impl.py"],
                    msp="m",
                    acceptance=[{"file": "tests/impl.py", "test": "case"}],
                )
            ]
            + [step("bare%d" % n, ["bare%d.txt" % n], msp="m") for n in range(8)]
        )
        gate = {
            "commit": gated_commit,
            "blocks": False,
            "properties": [
                {"step": "impl", "file": "tests/impl.py", "test": "case", "outcome": "pass"}
            ]
            + [
                {"step": "bare%d" % n, "file": None, "test": None, "outcome": "not-applicable"}
                for n in range(8)
            ],
        }
        closings = (
            (
                run.pull_request_body(
                    HEADER, plan, 0, gate, None, CLEAN_FINDINGS, None, (), proven_commit
                ),
                "Not verified: mitosis gated commit %s; the caller proved commit %s without a new gate."
                % (gated_commit[:12], proven_commit[:12]),
            ),
            (
                run.pull_request_body(HEADER, plan, 0, gate, "boom", CLEAN_FINDINGS, None, ()),
                "Not verified: the gate could not run",
            ),
        )
        for body, closing in closings:
            lines = section_of(body, "## Verification")
            unproven = [line for line in lines if line.startswith("Not verified: ")]
            self.assertEqual(lines[-1], closing)
            self.assertEqual(len(unproven), 8)
            self.assertEqual(unproven[-2], "Not verified: 2 more acceptance checks were not proven")
            self.assertEqual(body_problems(body), ())

    def a_title_carries_the_caller_type_scope_and_subject(self):
        one = core.plan([step("impl", ["impl.txt"])])
        two = core.plan([step("a", ["a.txt"], msp="core"), step("b", ["b.txt"], msp="docs")])
        index = {msp["label"]: position for position, msp in enumerate(two["msps"])}
        titles = (
            (run.pull_request_title(HEADER, one, 0), "feat(ship): write pull requests in the house format"),
            (
                run.pull_request_title({**HEADER, "scope": None}, one, 0),
                "feat: write pull requests in the house format",
            ),
            (
                run.pull_request_title(HEADER, two, index["core"]),
                "feat(ship): write pull requests in the house format (core)",
            ),
            (
                run.pull_request_title(HEADER, two, index["docs"]),
                "feat(ship): write pull requests in the house format (docs)",
            ),
        )
        for title, expected in titles:
            self.assertEqual(title, expected)
            self.assertIsNotNone(HOUSE_TITLE.fullmatch(title), title)
            self.assertIsNone(broken_rule(title, 72), title)

    def the_body_follows_the_house_layout_byte_for_byte(self):
        plan = core.plan(
            [
                step(
                    "impl",
                    ["impl.txt", "tests/t.py"],
                    task="Add the impl file.",
                    acceptance=[{"file": "tests/t.py", "test": "case"}],
                )
            ]
        )
        gate = {"properties": [{"step": "impl", "file": "tests/t.py", "test": "case", "outcome": "pass"}]}
        header = {"type": "feat", "scope": None, "subject": "add the impl", "why": ("Callers need it.",)}
        self.assertEqual(
            run.pull_request_body(header, plan, 0, gate, None, CLEAN_FINDINGS, None, ()),
            "## What changed\n- Add the impl file.\n\n## Why\nCallers need it.\n\n## Verification\n"
            "Verified: tests/t.py::case (impl) fails with the work reverted and passes with it",
        )
        self.assertEqual(
            run.pull_request_body(header, plan, 0, gate, None, None, None, ()),
            run.pull_request_body(header, plan, 0, gate, None, CLEAN_FINDINGS, None, ()),
        )
        risky = run.pull_request_body(
            header, plan, 0, gate, None, {**CLEAN_FINDINGS, "undeclared": ["b.txt"]}, None, ()
        )
        self.assertEqual(
            risky,
            "## What changed\n- Add the impl file.\n\n## Why\nCallers need it.\n\n## Risk\n"
            "Reconcile found 1 undeclared write (b.txt).\n\n## Verification\n"
            "Verified: tests/t.py::case (impl) fails with the work reverted and passes with it",
        )
        self.assertEqual(
            [block.split("\n")[0] for block in risky.split("\n\n")],
            ["## What changed", "## Why", "## Risk", "## Verification"],
        )
        self.assertFalse(risky.endswith("\n"))

    def what_changed_takes_each_task_first_sentence_or_a_fallback(self):
        plan = core.plan(
            [
                step(
                    "plain",
                    ["plain.txt"],
                    msp="m",
                    task="Change run.py so titles follow the format. Then update the tests.",
                ),
                step("backtick", ["backtick.txt"], msp="m", task="`run.py` gets the format."),
                step("base", ["base.txt"], msp="m", task="Point the pull request at <base> instead."),
                step("dash", ["dash.txt"], msp="m", task="— a dash opens this task."),
                step("<img>", ["img.txt"], msp="m", task="`x` comes first."),
            ]
        )
        expected = {
            "plain": "Change run.py so titles follow the format.",
            "backtick": "Builds the backtick Step.",
            "base": "Builds the base Step.",
            "dash": "Builds the dash Step.",
            "<img>": "Builds one Step of this change.",
        }
        body = run.pull_request_body(HEADER, plan, 0, None, None, None, None, ())
        self.assertEqual(
            section_of(body, "## What changed"),
            ["- " + expected[name] for name in plan["msps"][0]["steps"]],
        )
        ten = core.plan(
            [step("s%d" % n, ["s%d.txt" % n], msp="m", task="Do step %d." % n) for n in range(10)]
        )
        names = ten["msps"][0]["steps"]
        entries = section_of(run.pull_request_body(HEADER, ten, 0, None, None, None, None, ()), "## What changed")
        self.assertEqual(len(entries), 8)
        self.assertEqual(entries[:7], ["- Do step %s." % name[1:] for name in names[:7]])
        self.assertEqual(entries[7], "- Builds 3 more Steps: %s, %s and %s." % tuple(names[7:]))

    def verification_lists_each_property_by_its_gate_outcome(self):
        def gated(name, outcome=None):
            return step(
                name,
                ["%s.txt" % name, "tests/%s.py" % name],
                msp="m",
                acceptance=[{"file": "tests/%s.py" % name, "test": "case"}],
            )

        plan = core.plan(
            [
                gated("q"),
                gated("p"),
                gated("r"),
                gated("s"),
                gated("u"),
                step("n", ["n.txt"], msp="m"),
            ]
        )
        outcomes = {"p": "pass", "q": "inert", "r": "inconclusive", "s": "not-applicable"}
        gate = {
            "properties": [
                {"step": name, "file": "tests/%s.py" % name, "test": "case", "outcome": outcome}
                for name, outcome in outcomes.items()
            ]
            + [{"step": "n", "file": None, "test": None, "outcome": "not-applicable"}]
        }
        stated = {
            "q": "Not verified: tests/q.py::case (q) is inert",
            "r": "Not verified: tests/r.py::case (r) is inconclusive",
            "s": "Not verified: tests/s.py::case (s) is not-applicable",
            "u": "Not verified: tests/u.py::case (u) was not judged",
        }
        names = plan["msps"][0]["steps"]
        body = run.pull_request_body(HEADER, plan, 0, gate, "the runner vanished", None, None, ())
        self.assertEqual(
            section_of(body, "## Verification"),
            ["Verified: tests/p.py::case (p) fails with the work reverted and passes with it"]
            + [stated[name] for name in names if name in stated]
            + ["Not verified: the n Step has no acceptance test", "Not verified: the gate could not run"],
        )
        self.assertNotIn("the runner vanished", body)
        quiet = run.pull_request_body(HEADER, plan, 0, gate, None, None, None, ())
        self.assertNotIn("Not verified: the gate could not run", quiet)
        self.assertNotIn("## Risk", quiet)
        wide = core.plan(
            [step("w", ["w.txt"], acceptance=[{"file": "f" * 300, "test": "t" * 300}])]
        )
        wide_gate = {"properties": [{"step": "w", "file": "f" * 300, "test": "t" * 300, "outcome": "pass"}]}
        self.assertEqual(
            section_of(run.pull_request_body(HEADER, wide, 0, wide_gate, None, None, None, ()), "## Verification"),
            ["Verified: an acceptance test fails with the work reverted and passes with it"],
        )
        many = core.plan(
            [
                step(
                    "x",
                    ["x.txt"],
                    acceptance=[{"file": "tests/x%d.py" % n, "test": "case"} for n in range(10)],
                )
            ]
        )
        for outcome, last in (
            ("pass", "Verified: 3 more acceptance tests fail with the work reverted and pass with it"),
            ("inert", "Not verified: 3 more acceptance checks were not proven"),
        ):
            many_gate = {
                "properties": [
                    {"step": "x", "file": "tests/x%d.py" % n, "test": "case", "outcome": outcome}
                    for n in range(10)
                ]
            }
            lines = section_of(
                run.pull_request_body(HEADER, many, 0, many_gate, None, None, None, ()), "## Verification"
            )
            self.assertEqual(len(lines), 8)
            self.assertEqual(lines[7], last)
            self.assertEqual(lines[0].split("::")[0].split(": ")[1], "tests/x0.py")

    def the_door_call_and_blast_radius_open_the_risk_section(self):
        plain = core.plan([step("a", ["a.txt"])])
        guarded = {**HEADER, "door": "two-way", "blast_radius": "Docs only."}
        quiet = run.pull_request_body(guarded, plain, 0, None, None, CLEAN_FINDINGS, None, ())
        self.assertEqual(section_of(quiet, "## Risk"), ["Door: two-way", "Blast radius: Docs only."])
        self.assertEqual(body_problems(quiet), ())
        risky = run.pull_request_body(
            guarded, plain, 0, None, None, {**CLEAN_FINDINGS, "undeclared": ["b.txt"]}, None, ()
        )
        self.assertEqual(
            section_of(risky, "## Risk"),
            ["Door: two-way", "Blast radius: Docs only.", "Reconcile found 1 undeclared write (b.txt)."],
        )
        self.assertEqual(body_problems(risky), ())
        half = run.pull_request_body(
            {**HEADER, "door": "two-way"}, plain, 0, None, None, CLEAN_FINDINGS, None, ()
        )
        self.assertNotIn("## Risk", half)

    def risk_names_every_finding_kind_or_is_absent(self):
        plain = core.plan([step("a", ["a.txt"])])
        self.assertNotIn("## Risk", run.pull_request_body(HEADER, plain, 0, None, None, CLEAN_FINDINGS, None, ()))
        package = core.plan(
            [
                step("surface", ["pkg/__init__.py", "pkg/g.py"], msp="m"),
                step("parse", ["pkg/parse.py"], msp="m", after=["surface"]),
            ]
        )
        findings = {
            "undeclared": ["stray.txt", "b.txt"],
            "unwritten": ["pkg/g.py"],
            "crossing": [{"path": "b.txt", "msp": 1, "label": "beta"}],
            "fatal": True,
        }
        foreign = [{"path": "pkg/parse.py", "owner": 1, "suspects": [0], "lane": 0}]
        body = run.pull_request_body(
            HEADER, package, 0, None, "boom", findings, {"producers": [1, 2]}, foreign
        )
        self.assertEqual(
            section_of(body, "## Risk"),
            [
                "Reconcile found 2 undeclared writes (stray.txt and b.txt); reconcile found 1 "
                "declared path never written (pkg/g.py); another MSP also changes 1 file this "
                "branch writes (b.txt); a Lane changed 1 file another Lane of this MSP owned and "
                "had already committed (pkg/parse.py); the build leaves 1 package manifest "
                "exporting nothing; this MSP has 2 producers, so its base is the "
                "feature branch; the gate could not run."
            ],
        )
        hidden = run.pull_request_body(
            HEADER, plain, 0, None, None, {**CLEAN_FINDINGS, "undeclared": ["<x"]}, None, ()
        )
        self.assertEqual(section_of(hidden, "## Risk"), ["Reconcile found 1 undeclared write."])
        self.assertNotIn("<x", hidden)
        crowded = run.pull_request_body(
            HEADER,
            plain,
            0,
            None,
            None,
            {**CLEAN_FINDINGS, "undeclared": ["dir/file-%03d.txt" % n for n in range(200)]},
            None,
            (),
        )
        risk = section_of(crowded, "## Risk")
        self.assertEqual(len(risk), 1)
        self.assertLessEqual(len(risk[0].encode("utf-16-le")) // 2, 500)
        self.assertTrue(risk[0].startswith("Reconcile found 200 undeclared writes (dir/file-000.txt, "))
        cut = re.search(r" and (\d+) more\)\.$", risk[0])
        self.assertIsNotNone(cut, risk[0])
        self.assertEqual(int(cut.group(1)) + risk[0].count("dir/file-"), 200)
        self.assertEqual(body_problems(crowded), ())

    def hostile_inputs_never_break_the_value_rules(self):
        self.assertNotEqual(
            body_problems("## What changed\n- lower\n\n## Why\nok\n\n## Verification\nVerified: <b"), ()
        )
        header = {**HEADER, "why": ("Callers need it.", "receipt: kept as written")}
        for text in (
            "<script>alert(1)</script>",
            "# A heading",
            "\u202eReversed text.",
            "Verified: x",
            "Builds a long task " + "x" * 600 + ".",
            "First line.\n\tSecond line\twith tabs.",
            "case<img src=x>",
            "Ship it \U0001F680 now.",
            "<img>",
        ):
            with self.subTest(text=text):
                plan = core.plan(
                    [
                        step("alpha", ["alpha.txt"], msp="m", task=text),
                        step(
                            text,
                            ["hostile.txt", "tests/hostile.py"],
                            msp="m",
                            task=text,
                            acceptance=[
                                {"file": text, "test": text},
                                {"file": "tests/hostile.py", "test": text},
                            ],
                        ),
                        step(
                            "gamma",
                            ["gamma.txt"],
                            msp="m",
                            task=text,
                            acceptance=[{"file": "tests/g.py", "test": text}],
                        ),
                    ]
                )
                gate = {
                    "properties": [
                        {"step": text, "file": text, "test": text, "outcome": "pass"},
                        {"step": text, "file": "tests/hostile.py", "test": text, "outcome": "inert"},
                        {"step": "gamma", "file": "tests/g.py", "test": text, "outcome": "inconclusive"},
                    ]
                }
                findings = {
                    "undeclared": [text, "stray.txt"],
                    "unwritten": [text],
                    "crossing": [{"path": text, "msp": 1, "label": text}],
                    "fatal": True,
                }
                foreign = [{"path": text, "owner": 1, "suspects": [0], "lane": 0}]
                body = run.pull_request_body(
                    header, plan, 0, gate, text, findings, {"producers": [1, 2]}, foreign
                )
                self.assertEqual(body_problems(body), ())
                self.assertIsNotNone(section_of(body, "## Risk"))


class HouseFormatRoundTwo(unittest.TestCase):
    def composed_values_refuse_hidden_text_mentions_references_and_links(self):
        import run

        carriers = (
            "Add the file <?x hidden?> safely.",
            "Add the file <b>now</b>.",
            "Ping @SatanshuMishra about this.",
            "Ping the team@example about this.",
            "Closes #1 by adding the file.",
            "This fixes SatanshuMishra/mitosis#2 too.",
            "Add [the setup guide](https://example.invalid/x) to the docs.",
            "Add ![status](https://example.invalid/b.png) to the docs.",
            "See https://example.invalid/login for details.",
            "See www.example.invalid for details.",
            "Add the file&#xE0061; now.",
            "Add the&ZeroWidthSpace;file now.",
        )
        for carrier in carriers:
            self.assertFalse(run.composed_value(carrier), carrier)
            self.assertEqual(run._what_entry("impl", carrier), "Builds the impl Step.", carrier)
        self.assertTrue(run.composed_value("Add the impl file to issue tracker 1."))
        self.assertTrue(run.composed_value("tests/test_run.py::Ship.case (impl) is inert"))
        self.assertTrue(run.composed_value("Add the file & its test."))

    def _limited(self, call, *args):
        def expired(signum, frame):
            self.fail("%s ran past its 5-second limit" % call.__name__)

        previous = signal.signal(signal.SIGALRM, expired)
        signal.alarm(5)
        try:
            return call(*args)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)

    def risk_line_is_built_in_linear_time(self):
        plan = core.plan([step("a", ["a.txt"])])
        paths = ["dir/f-%05d.txt" % n for n in range(20000)]
        findings = {**CLEAN_FINDINGS, "undeclared": paths}
        for _ in range(3):
            body = self._limited(
                run.pull_request_body, HEADER, plan, 0, None, None, findings, None, ()
            )
            risk = section_of(body, "## Risk")
            self.assertEqual(len(risk), 1)
            line = risk[0]
            self.assertLessEqual(len(line.encode("utf-16-le")) // 2, 500)
            self.assertTrue(line.startswith("Reconcile found 20000 undeclared writes ("), line)
            cut = re.search(r" and (\d+) more\)\.$", line)
            self.assertIsNotNone(cut, line)
            named = re.findall(r"dir/f-\d{5}\.txt", line)
            self.assertEqual(len(named), 20000 - int(cut.group(1)))
            self.assertEqual(named, paths[: len(named)])

    def risk_line_skips_an_unnameable_path_and_counts_it(self):
        plan = core.plan([step("a", ["a.txt"])])

        def risk_of(**found):
            body = run.pull_request_body(
                HEADER, plan, 0, None, None, {**CLEAN_FINDINGS, **found}, None, ()
            )
            return section_of(body, "## Risk")

        self.assertEqual(
            risk_of(undeclared=["d/" + "x" * 470 + ".txt", "b.txt"]),
            ["Reconcile found 2 undeclared writes (b.txt and 1 more)."],
        )
        undeclared = ["dir/u-%03d.txt" % n for n in range(200)]
        unwritten = ["dir/w-%03d.txt" % n for n in range(200)]
        risk = risk_of(undeclared=undeclared, unwritten=unwritten)
        self.assertEqual(len(risk), 1)
        line = risk[0]
        units = len(line.encode("utf-16-le")) // 2
        self.assertLessEqual(units, 500)
        first, second = line.split("; ")
        named_undeclared = re.findall(r"dir/u-\d{3}\.txt", first)
        named_unwritten = re.findall(r"dir/w-\d{3}\.txt", second)
        self.assertGreater(len(named_undeclared), 0)
        self.assertEqual(len(named_undeclared), len(named_unwritten))
        self.assertEqual(named_undeclared, undeclared[: len(named_undeclared)])
        self.assertEqual(named_unwritten, unwritten[: len(named_unwritten)])
        self.assertTrue(first.startswith("Reconcile found 200 undeclared writes ("), first)
        self.assertTrue(first.endswith(" and %d more)" % (200 - len(named_undeclared))), first)
        self.assertTrue(
            second.startswith("reconcile found 200 declared paths never written ("), second
        )
        self.assertTrue(second.endswith(" and %d more)." % (200 - len(named_unwritten))), second)
        self.assertGreater(units + 2 * len(", dir/u-000.txt"), 500)

    def Unchanged_composed_values_stay_printable_ascii(self):
        self.assertEqual(run._what_entry("impl", "Ship it \U0001F680 now."), "Builds the impl Step.")
        self.assertEqual(
            run._what_entry("caf\u00e9", "`code` first."), "Builds one Step of this change."
        )
        plan = core.plan(
            [
                step("impl", ["impl.txt"], msp="m", task="Ship it \U0001F680 now."),
                step("caf\u00e9", ["cafe.txt"], msp="m", task="`code` first."),
            ]
        )
        expected = {
            "impl": "- Builds the impl Step.",
            "caf\u00e9": "- Builds one Step of this change.",
        }
        body = run.pull_request_body(HEADER, plan, 0, None, None, None, None, ())
        self.assertEqual(
            section_of(body, "## What changed"),
            [expected[name] for name in plan["msps"][0]["steps"]],
        )

    def rule_sources_hold_no_raw_invisible_character(self):
        hidden = frozenset(
            chr(code) for low, high in HIDDEN_RANGES for code in range(low, high + 1)
        )
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("run.py", os.path.join("tests", "test_run.py")):
            found = [
                (number, "U+%04X" % ord(character))
                for number, line in enumerate(read(os.path.join(root, name)).split("\n"), 1)
                for character in line
                if character in hidden
            ]
            self.assertEqual(found, [], name)
        for character in ("\u00ad", "\u202e", "\ufeff"):
            self.assertIsNotNone(
                run.INVISIBLE_CHARACTER.search("a%sb" % character), ascii(character)
            )
        self.assertEqual(
            [code for code in range(0x10000) if run.INVISIBLE_CHARACTER.fullmatch(chr(code))],
            [code for code in range(0x10000) if chr(code) in hidden],
        )

    def title_fault_applies_the_composed_rules(self):
        for title, rule in (
            ("feat: add <?x?> parser", "holds < followed by !, /, ? or a letter"),
            ("fix: fix &amp; cleanup", "holds a character reference"),
            ("feat: ping @team", "holds @"),
            ("fix: close #12", "holds # followed by a digit"),
            ("feat(ship): write the parser (a&lt;b)", "holds a character reference"),
        ):
            self.assertEqual(run.title_fault(title), rule, title)
        self.assertIsNone(
            run.title_fault("feat(ship): write pull requests in the house format (core)")
        )
        for value, rule in (
            ("add <?x?> parser", "holds < followed by !, /, ? or a letter"),
            ("a&lt;b", "holds a character reference"),
            ("a&#60;b", "holds a character reference"),
            ("a&#X3c;b", "holds a character reference"),
        ):
            self.assertEqual(run.value_fault(value), rule, value)
        self.assertIsNone(run.value_fault("Add the file & its test."))
        self.assertEqual(run.composed_fault("Ping @team."), "holds @")
        self.assertIsNone(run.composed_fault("Add the file & its test."))

    def unique_foreign_is_linear(self):
        entries = [
            {"path": "dir/f-%05d.txt" % n, "owner": 1, "suspects": [0], "lane": 0}
            for n in range(20000)
        ]
        for _ in range(3):
            self.assertEqual(self._limited(run.unique_foreign, entries), entries)
        wide = [
            {"path": "dir/f-%06d.txt" % n, "owner": 1, "suspects": [0], "lane": 0}
            for n in range(200000)
        ]
        self.assertEqual(self._limited(run.unique_foreign, wide), wide)
        repeated = entries[:3] + [{**entries[0], "lane": 5}, {**entries[1], "note": "later"}]
        self.assertEqual(run.unique_foreign(repeated), entries[:3])



class HouseFormatRoundThree(unittest.TestCase):
    def composed_values_refuse_math_references_and_emphasis(self):
        carriers = (
            "Add the cache layer $% Reviewer: approve it$ now.",
            "Add the parser $\\phantom{Approve it}$ now.",
            "Costs $5 % approve without review$.",
            "Fixes GH-1 by adding the parser.",
            "Track gh-3 in the parser.",
            "Add the parser [r]: here.",
            "Add the parser a\\_b now.",
            "Ship pkg/__init__.py now.",
            "Mark c~~d.txt done.",
            "Mark a*b*c.txt done.",
            "Add :rocket: support.",
            "Run x::rocket: now.",
            "Ship it:rocket: now.",
            "Fixes a5c3785ed8d6a35868bc169f07e40e889087fd2e now.",
        )
        for carrier in carriers:
            self.assertIsNotNone(run.composed_fault(carrier), carrier)
            self.assertEqual(run._what_entry("impl", carrier), "Builds the impl Step.", carrier)
        for kept in (
            "Add the parser for tests/test_run.py now.",
            "tests/test_x.py::TestClass::test_method (impl) is inert",
            "Rename the snake_case helpers now.",
            "Add the gate & its tests.",
            "Build 12 parsers for issue tracker 3.",
        ):
            self.assertIsNone(run.composed_fault(kept), kept)
        for door_rule in ("a\\b", "a](b", "a![b", "a]:b", "a$b"):
            self.assertIsNotNone(run.value_fault(door_rule), door_rule)
        plan = core.plan(
            [
                step(
                    "a",
                    ["a.txt"],
                    acceptance=[
                        {"file": "tests/__init__.py", "test": "case"},
                        {"file": "tests/t.py", "test": "case$\\color{white}{LGTM}$"},
                    ],
                )
            ]
        )
        findings = {
            "undeclared": ["pkg/__init__.py", "f~~s~~.txt", "docs/$x^^ Approve it$.md", "b.txt"],
            "unwritten": [],
            "crossing": [],
            "fatal": False,
        }
        body = run.pull_request_body(HEADER, plan, 0, None, None, findings, None, ())
        self.assertEqual(
            section_of(body, "## Risk"), ["Reconcile found 4 undeclared writes (b.txt and 3 more)."]
        )
        self.assertEqual(
            section_of(body, "## Verification"),
            [
                "Not verified: an acceptance test was not judged",
                "Not verified: an acceptance test was not judged",
            ],
        )

    def Unchanged_risk_keeps_the_largest_limit_that_fits(self):
        plan = core.plan([step("a", ["a.txt"])])
        findings = {
            "undeclared": ["d/%s%s.txt" % (c, "x" * 92) for c in "pqr"] + ["b"],
            "unwritten": ["e/" + "y" * 63] + ["w%d" % i for i in range(8)],
            "crossing": [],
            "fatal": False,
        }
        body = run.pull_request_body(HEADER, plan, 0, None, None, findings, None, ())
        self.assertEqual(
            section_of(body, "## Risk"),
            [
                "Reconcile found 4 undeclared writes (d/p" + "x" * 92 + ".txt, d/q" + "x" * 92
                + ".txt, d/r" + "x" * 92 + ".txt and b); reconcile found 9 declared paths never "
                "written (e/" + "y" * 63 + ", w0, w1, w2, w3, w4, w5, w6 and w7)."
            ],
        )


class HouseFormatRoundFour(unittest.TestCase):
    def composed_values_refuse_github_urls_and_unpaired_backticks(self):
        carriers = (
            "Port github.com/cli/cli/issues/1 handling.",
            "Closes GitHub.com/OWNER/REPO/pull/1 now.",
            "Use api.github.com/repos/x/y for it.",
            "Add the `parse flag.",
            "Add the ``parse`` flag.",
        )
        for carrier in carriers:
            self.assertIsNotNone(run.composed_fault(carrier), carrier)
            self.assertEqual(run._what_entry("impl", carrier), "Builds the impl Step.", carrier)
        self.assertIsNone(run.composed_fault("Add the `parse` and `scan` flags."))
        door_rules = (
            "a://b",
            "see www.a",
            "github.com/a",
            "a~b",
            "a``b",
            "a`b",
            "a" + chr(0xA0) + "b",
            "a" + chr(0x20DD),
            "a" + chr(0x336),
            "a" + chr(0x2800),
        )
        for door_rule in door_rules:
            self.assertIsNotNone(run.value_fault(door_rule), ascii(door_rule))
        plan = core.plan(
            [
                step(
                    "a",
                    ["a.txt"],
                    acceptance=[
                        {"file": "tests/github.com/x.py", "test": "case"},
                        {"file": "tests/t.py", "test": "case`x"},
                    ],
                )
            ]
        )
        findings = {
            "undeclared": ["vendor/github.com/cli/cli/issues/1", "b.txt"],
            "unwritten": [],
            "crossing": [],
            "fatal": False,
        }
        body = run.pull_request_body(HEADER, plan, 0, None, None, findings, None, ())
        self.assertEqual(
            section_of(body, "## Risk"), ["Reconcile found 2 undeclared writes (b.txt and 1 more)."]
        )
        self.assertEqual(
            section_of(body, "## Verification"),
            [
                "Not verified: an acceptance test was not judged",
                "Not verified: an acceptance test was not judged",
            ],
        )


def session_items():
    return [
        step(
            "s0",
            ["impl.txt", "tests/t_impl.txt", "a0.txt"],
            acceptance=[{"file": "tests/t_impl.txt", "test": "property"}],
        ),
        step("s1", ["impl.txt", "a1.txt"]),
        step("s2", ["impl.txt", "a2.txt"]),
    ]


def one_step_sessions(items):
    return max(
        core.estimate_tokens(items, (index,), {}, {}, earlier=tuple(range(index)))
        for index in range(len(items))
    ) + 1


class Sessions(RepoCase):
    def _plan(self):
        items = session_items()
        plan = core.plan(items, session_tokens=one_step_sessions(items))
        self.assertEqual([lane["steps"] for lane in plan["lanes"]], [["s0"], ["s1"], ["s2"]])
        self.assertEqual([lane["session"] for lane in plan["lanes"]], [[1, 3], [2, 3], [3, 3]])
        self.assertEqual(plan["lane_after"], {"1": [0], "2": [1]})
        return plan

    def _landings(self, state):
        branch = state["worktrees"][0]["branch"]
        return sh(["git", "log", "--reverse", "--format=%s", "main.." + branch], self.repo).splitlines()

    def an_oversized_lane_builds_as_fresh_sessions_in_a_row_on_one_branch(self):
        plan = self._plan()
        state = self.execute(plan, accept="real", pr=None, no_push=True)
        lanes = state["lanes"]
        self.assertEqual([lanes[str(i)]["state"] for i in range(3)], ["ok", "ok", "ok"])
        self.assertEqual([self.marker(i).count("ran") for i in range(3)], [1, 1, 1])
        self.assertEqual(
            self._landings(state),
            ["chore(s0): land Lane %d (s%d)" % (i, i) for i in range(3)],
        )
        tree = state["worktrees"][0]["path"]
        for earlier, later in ((0, 1), (1, 2)):
            self.assertLessEqual(lanes[str(earlier)]["finished"], lanes[str(later)]["started"])
            sh(
                ["git", "merge-base", "--is-ancestor", lanes[str(earlier)]["commit"], lanes[str(later)]["commit"]],
                tree,
            )
        self.assertIn("a0.txt", self.marker(1))
        self.assertIn("a1.txt", self.marker(2))
        msp = state["msps"]["0"]
        self.assertEqual(msp["state"], run.MSP_COMMITTED)
        self.assertEqual(msp["gate"]["outcome"], "pass")
        self.assertEqual(msp["reconcile"]["undeclared"], [])
        self.assertEqual(msp["reconcile"]["unwritten"], [])
        self.assertEqual(msp["reconcile"]["crossing"], [])
        self.assertTrue(run.succeeded(state))

    def a_resume_mid_lane_continues_from_the_next_unbuilt_session(self):
        plan = self._plan()
        first = self.execute(plan, mode="crash-1", accept="real", pr=None, no_push=True)
        self.assertEqual(
            [first["lanes"][str(i)]["state"] for i in range(3)], ["ok", "failed", "blocked"]
        )
        self.assertIsNone(self.marker(2))
        self.assertEqual(first["msps"]["0"]["state"], run.MSP_BLOCKED)
        second = self.execute(plan, accept="real", pr=None, no_push=True, resume=True)
        self.assertEqual(second["lanes"]["0"], first["lanes"]["0"])
        self.assertEqual([self.marker(i).count("ran") for i in range(3)], [1, 2, 1])
        self.assertEqual([second["lanes"][str(i)]["state"] for i in range(3)], ["ok", "ok", "ok"])
        self.assertEqual(
            self._landings(second),
            ["chore(s0): land Lane %d (s%d)" % (i, i) for i in range(3)],
        )
        self.assertEqual(second["msps"]["0"]["state"], run.MSP_COMMITTED)
        self.assertEqual(second["msps"]["0"]["gate"]["outcome"], "pass")
        self.assertTrue(run.succeeded(second))

    def a_failed_session_leaves_its_edits_out_of_another_lanes_landing(self):
        items = [
            step("s0", ["impl.txt", "a0.txt"], msp="m"),
            step("s1", ["impl.txt", "a1.txt"], msp="m"),
            step("y", ["y.txt"], msp="m"),
        ]
        plan = core.plan(items, session_tokens=one_step_sessions(items[:2]))
        self.assertEqual([lane["steps"] for lane in plan["lanes"]], [["s0"], ["s1"], ["y"]])
        self.assertEqual(plan["lane_order"][:2], [0, 1])
        trees = run.prepare_worktrees(plan["msps"], "main", self.trees, self.repo)
        records = self.dispatch(plan, trees, mode="crash-1", concurrency=1)
        self.assertEqual([records[i]["state"] for i in range(3)], ["ok", "failed", "ok"])
        self.assertEqual(records[2]["foreign_writes"], [])
        tree = trees[0]["path"]
        subjects = sh(["git", "log", "--format=%s", "main.." + trees[0]["branch"]], tree)
        self.assertNotIn("keep changes", subjects)
        self.assertIn("impl.txt", sh(["git", "status", "--porcelain"], tree))
        self.assertEqual(run.msp_foreign_writes(plan, 0, {str(k): v for k, v in records.items()}), [])

    def a_resume_after_every_session_landed_builds_nothing_again(self):
        plan = self._plan()
        first = self.execute(plan, accept="real", pr=None, no_push=True)
        second = self.execute(plan, accept="real", pr=None, no_push=True, resume=True)
        self.assertEqual([self.marker(i).count("ran") for i in range(3)], [1, 1, 1])
        self.assertEqual(second["lanes"], first["lanes"])
        self.assertEqual(self._landings(second), self._landings(first))


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
    for case in (
        Worktrees,
        Dispatch,
        Resume,
        Gate,
        GateReuse,
        Reconcile,
        Ship,
        HouseFormat,
        HouseFormatRoundTwo,
        HouseFormatRoundThree,
        HouseFormatRoundFour,
        Sessions,
    ):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
