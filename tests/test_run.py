import json
import os
import shlex
import shutil
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
    print(json.dumps({"item": "lane-%s" % lane, "status": status, "files_changed": write_set, "notes": notes}))
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

    def execute(self, plan, mode="ok", accept="real", pr=None, timeout=60, **extra):
        return run.execute(
            plan,
            self.repo,
            "main",
            self.run_dir,
            self.worker_command(mode),
            self.acceptance_command(accept),
            pr or self.pr_command(),
            timeout,
            2,
            trees_root=self.trees,
            **extra,
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
        self.assertIn("Gate: inert", body)
        self.assertIn("passes with the implementation reverted", body)
        self.assertIn("unproven acceptance property", body)

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
        self.assertIn("tests/t_impl.txt::property (impl): inert", body)


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
        state = self.execute(plan, mode="cross", pr=self.pr_command())
        self.assertTrue(state["msps"]["0"]["reconcile"]["fatal"])
        self.assertNotEqual(state["msps"]["0"]["state"], "shipped")
        self.assertIn("b.txt", state["msps"]["0"]["reason"])
        self.assertNotIn(trees[0]["branch"], [pr[0] for pr in self.pull_requests()])

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

    def the_pull_request_body_carries_the_acceptance_properties(self):
        self.add_remote()
        plan = core.plan([gated_step()])
        state = self.execute(plan, pr=self.pr_command())
        self.assertEqual(state["msps"]["0"]["state"], "shipped")
        branch, base, title, body = self.pull_requests()[0]
        self.assertEqual(base, "main")
        self.assertIn("tests/t_impl.txt::property", body)
        self.assertIn("impl", title)
        self.assertIn(self.acceptance_command("real"), body)
        self.assertIn("draft", body.lower())
        self.assertIn("pass", body)

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
    for case in (Worktrees, Dispatch, Resume, Gate, Reconcile, Ship):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
