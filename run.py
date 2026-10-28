import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import time
import unicodedata
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone

import core
import mutants
import outcomes
import referee
import shape

BRANCH_PREFIX = "mitosis"

PROBE_PREFIX = "probe"

GATE_MARKER = "mitosis-gate.json"

PLAN_FILE = "plan.json"

STATE_FILE = "state.json"

MSP_BLOCKED = "blocked"

LANE_COMMIT_FAILED = "the Lane's commit failed"

LANE_HELD = "held"

LANE_PARKED = "parked-limit"

MSP_PARKED = "parked"

USAGE_LIMIT = re.compile(
    r"you've hit your.*limit|usage limit reached|you've reached your.*limit"
    r"|out of usage credits|limit will reset",
    re.IGNORECASE,
)

USAGE_LIMIT_TAIL = 20

LOCK_MARKERS = (".lock': File exists", "Another git process seems to be running")

LOCK_PATH = re.compile(r"[\\/]\.git[\\/][^\s'\"]*\.lock\b")

LOCK_RETRY_DELAYS = (0.1, 0.2, 0.4, 0.8, 1.6, 3.2)

GIT_TIMEOUT = 600

KILL_GRACE = 30

MSP_UNCHANGED = "unchanged"

MSP_COMMITTED = "committed"

MSP_PUSHED = "pushed"

MSP_REFEREE_REVERTED = "referee-reverted"

PUBLISHED_STATES = ("shipped", MSP_PUSHED, MSP_UNCHANGED)

ACCEPTANCE_FAILURE_EXIT = 1

MUTANT_CAP = 12

MSP_GATE_FAILED = "gate-failed"

BLOCKING_OUTCOMES = ("weak", "inert", "inconclusive")

PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")

LANE_RECORD_KEYS = (
    "lane",
    "msp",
    "state",
    "reason",
    "exit",
    "timed_out",
    "returned",
    "stdout",
    "stderr",
    "merged",
    "commit",
    "started",
    "finished",
    "session_id",
)


INSTALLED_FOLDERS = frozenset(("node_modules",))

VENV_MARKER = "pyvenv.cfg"


class GitError(RuntimeError):
    def __init__(self, args, code, stderr):
        self.args_run = tuple(args)
        self.code = code
        self.stderr = stderr
        super().__init__(
            "git %s exited %d: %s" % (" ".join(args), code, stderr.strip() or "<no stderr>")
        )


class GitTimeout(GitError):
    def __init__(self, args, timeout):
        self.args_run = tuple(args)
        self.code = None
        self.stderr = ""
        RuntimeError.__init__(
            self,
            "git %s timed out after %s seconds; a git hook or a lock may be hanging"
            % (" ".join(args), timeout),
        )


class RestoreError(GitError):
    def __init__(self, branch, commit, left):
        self.args_run = ("reset", "--hard", commit)
        self.code = None
        self.stderr = ""
        self.left = tuple(left)
        RuntimeError.__init__(
            self,
            "the gate could not restore %s to the gated commit %s: %s still differ"
            % (branch, commit[:12], ", ".join(self.left)),
        )


class GateRecoveryError(GitError):
    def __init__(self, tree, marker, cause):
        self.args_run = ()
        self.code = None
        self.stderr = ""
        self.marker = marker
        RuntimeError.__init__(
            self,
            "could not recover the interrupted gate in %s: %s; %s is kept, so the next run "
            "tries the recovery again" % (tree, cause, marker),
        )


class ConfigError(ValueError):
    pass


class ResumeError(RuntimeError):
    pass


class ShipError(RuntimeError):
    pass


def _index_lock(cwd):
    dot_git = os.path.join(cwd or os.curdir, ".git")
    if not os.path.isfile(dot_git):
        return os.path.join(dot_git, "index.lock")
    try:
        with open(dot_git, encoding="utf-8") as handle:
            pointer = handle.read().strip()
    except OSError:
        return None
    if not pointer.startswith("gitdir:"):
        return None
    gitdir = os.path.join(cwd or os.curdir, pointer[len("gitdir:"):].strip())
    return os.path.join(gitdir, "index.lock")


def _left_lock(index_lock, locked_before):
    if index_lock is None or locked_before or not os.path.exists(index_lock):
        return
    try:
        os.remove(index_lock)
    except FileNotFoundError:
        pass


def _decoded(data):
    return data.decode("utf-8", "surrogateescape")


def _git_once(args, cwd, timeout, raw=False):
    index_lock = _index_lock(cwd)
    locked_before = index_lock is not None and os.path.exists(index_lock)
    argv = ["git", *args]
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        try:
            proc.communicate(timeout=KILL_GRACE)
        except subprocess.TimeoutExpired:
            pass
        _left_lock(index_lock, locked_before)
        raise GitTimeout(args, timeout)
    return subprocess.CompletedProcess(
        argv, proc.returncode, stdout if raw else _decoded(stdout), _decoded(stderr)
    )


def _locked(completed):
    stderr = completed.stderr or ""
    return completed.returncode != 0 and (
        any(marker in stderr for marker in LOCK_MARKERS) or bool(LOCK_PATH.search(stderr))
    )


def git_run(args, cwd, timeout=GIT_TIMEOUT, raw=False):
    completed = _git_once(args, cwd, timeout, raw)
    for delay in LOCK_RETRY_DELAYS:
        if not _locked(completed):
            return completed
        time.sleep(delay)
        completed = _git_once(args, cwd, timeout, raw)
    return completed


def git(args, cwd, check=True, timeout=GIT_TIMEOUT):
    completed = git_run(args, cwd, timeout)
    if check and completed.returncode != 0:
        raise GitError(args, completed.returncode, completed.stderr)
    return completed.stdout.strip()


def git_paths(args, cwd, timeout=GIT_TIMEOUT):
    completed = git_run(args, cwd, timeout, raw=True)
    if completed.returncode != 0:
        raise GitError(args, completed.returncode, completed.stderr)
    return [os.fsdecode(name) for name in completed.stdout.split(b"\0") if name]


def git_ok(args, cwd, timeout=GIT_TIMEOUT):
    return git_run(args, cwd, timeout).returncode == 0


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def seconds_between(start, end):
    try:
        return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
    except (TypeError, ValueError):
        return None


def notify(observer, boundary, record):
    if observer is not None:
        observer(boundary, record)


def branch_exists(repo, branch):
    return git_ok(["rev-parse", "--verify", "--quiet", "refs/heads/" + branch], repo)


def slug(label):
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(label).lower()).strip("-")
    return cleaned or "msp"


def msp_slugs(msps):
    taken = ()
    for index, msp in enumerate(msps):
        label = msp.get("label") if isinstance(msp, dict) else None
        candidate = slug(label if label not in (None, "") else "msp-%d" % index)
        suffix = 2
        chosen = candidate
        while chosen in taken:
            chosen = "%s-%d" % (candidate, suffix)
            suffix += 1
        taken = taken + (chosen,)
    return taken


def registered_worktrees(repo):
    out = git(["worktree", "list", "--porcelain"], repo)
    trees = {}
    current = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            current = os.path.realpath(line[len("worktree "):])
            trees = {**trees, current: None}
        elif line.startswith("branch ") and current is not None:
            trees = {**trees, current: line[len("branch "):].replace("refs/heads/", "", 1)}
    return trees


def prepare_worktree(repo, base, branch, path):
    git(["worktree", "prune"], repo)
    registered = registered_worktrees(repo)
    real = os.path.realpath(path)
    if real in registered and recover_gate(path):
        registered = registered_worktrees(repo)
    if real in registered:
        if registered[real] != branch:
            raise GitError(
                ["worktree", "add", path, branch],
                1,
                "%s already holds branch %s, not %s" % (path, registered[real], branch),
            )
        return
    os.makedirs(os.path.dirname(real), exist_ok=True)
    if branch_exists(repo, branch):
        git(["worktree", "add", path, branch], repo)
    else:
        git(["worktree", "add", "-b", branch, path, base], repo)


def prepare_worktrees(msps, base, root, repo, prefix=BRANCH_PREFIX):
    slugs = msp_slugs(msps)
    trees = ()
    for index, msp in enumerate(msps):
        branch = "%s/%s" % (prefix, slugs[index])
        path = os.path.join(os.path.abspath(root), slugs[index])
        prepare_worktree(repo, base, branch, path)
        trees = trees + (
            {
                "msp": index,
                "label": msp.get("label") if isinstance(msp, dict) else None,
                "branch": branch,
                "path": path,
            },
        )
    return list(trees)


def build_argv(template, values):
    def fill(match):
        key = match.group(1)
        if key not in values:
            return match.group(0)
        value = values[key]
        return "" if value is None else str(value)

    return [PLACEHOLDER.sub(fill, argument) for argument in shlex.split(template)]


TEMPLATE_PLACEHOLDERS = {
    "dispatch": (
        "task",
        "model",
        "tier",
        "worktree",
        "branch",
        "lane",
        "msp",
        "charter",
        "document",
        "run_dir",
        "session_id",
    ),
    "decompose": ("prompt", "model", "document", "session_id"),
    "acceptance": ("file", "test", "worktree"),
    "pull-request": ("branch", "base", "title", "body", "worktree", "msp", "remote"),
}


def check_template(name, template):
    if not isinstance(template, str) or not template.strip():
        raise ConfigError("the %s command is empty" % name)
    try:
        shlex.split(template)
    except ValueError as error:
        raise ConfigError("the %s command does not split into argv: %s" % (name, error))
    offered = TEMPLATE_PLACEHOLDERS.get(name)
    if offered is None:
        return
    unknown = sorted({m.group(1) for m in PLACEHOLDER.finditer(template)} - set(offered))
    if unknown:
        raise ConfigError(
            "the %s command uses %s, which it is never given; it offers %s"
            % (
                name,
                ", ".join("{%s}" % key for key in unknown),
                ", ".join("{%s}" % key for key in offered),
            )
        )


def check_models(plan, template, models):
    if "{model}" not in template:
        return
    tiers = sorted({lane.get("tier") for lane in plan.get("lanes", ())})
    missing = [tier for tier in tiers if tier not in (models or {})]
    if missing:
        raise ConfigError(
            "the dispatch command uses {model} but no model is mapped for tier %s"
            % ", ".join(str(tier) for tier in missing)
        )


def lane_edges(plan):
    return {
        int(consumer): tuple(int(producer) for producer in producers)
        for consumer, producers in (plan.get("lane_after") or {}).items()
    }


def nonempty_lines(path):
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return []
    return [line for line in data.decode("utf-8", "replace").splitlines() if line.strip()]


def last_line(path):
    lines = nonempty_lines(path)
    return lines[-1] if lines else None


def usage_limit_line(stdout_path, stderr_path):
    for path in (stdout_path, stderr_path):
        for line in nonempty_lines(path)[-USAGE_LIMIT_TAIL:]:
            if USAGE_LIMIT.search(line):
                return line.strip()
    return None


def last_return(path):
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    for line in reversed(data.decode("utf-8", "replace").splitlines()):
        if not line.strip():
            continue
        returned = parse_return(line.strip())
        if returned is not None:
            return returned
    return None


def parse_return(line):
    if line is None:
        return None
    try:
        parsed = json.loads(line)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    files = parsed.get("files_changed")
    notes = parsed.get("notes")
    bounded = {
        "item": parsed.get("item") if isinstance(parsed.get("item"), str) else None,
        "status": parsed.get("status") if isinstance(parsed.get("status"), str) else None,
        "files_changed": (
            [f for f in files if isinstance(f, str)] if isinstance(files, list) else []
        ),
        "notes": (notes if isinstance(notes, str) else "")[: core.NOTES_CAP],
    }
    return {key: bounded[key] for key in core.RETURN_KEYS}


def lane_verdict(exit_code, returned, timed_out):
    if timed_out:
        return "failed", "timeout"
    status = returned.get("status") if returned else None
    if exit_code == 0 and status == "ok":
        return "ok", None
    if returned is None:
        return "failed", "exit %d with no return line" % exit_code
    if exit_code == 0:
        return "failed", "exit 0 but reported %s" % status
    if status == "ok":
        return "failed", "exit %d but reported ok" % exit_code
    return "failed", "exit %d and reported %s" % (exit_code, status)


def kill_tree(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (AttributeError, OSError):
        proc.kill()


def run_command(argv, cwd, timeout, stdout_path, stderr_path, env=None):
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=out,
            stderr=err,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            return proc.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            try:
                return proc.wait(timeout=KILL_GRACE), True
            except subprocess.TimeoutExpired:
                return -signal.SIGKILL, True


def merge_target(tree, producer, base):
    if branch_exists(tree, producer["branch"]):
        return producer["branch"]
    commit = producer.get("commit")
    if commit and git_ok(["merge-base", "--is-ancestor", commit, base], tree):
        return commit
    return None


def merge_producers(tree, consumer_branch, producers, base):
    merged = ()
    for producer in producers:
        target = merge_target(tree, producer, base)
        if target is None:
            return merged, (
                "producer branch %s is missing and its commits are not reachable from %s"
                % (producer["branch"], base)
            )
        result = git_run(
            ["-c", "core.hooksPath=" + os.devnull, "merge", "--no-edit", "--no-verify", target],
            tree,
        )
        if result.returncode != 0:
            git_run(["merge", "--abort"], tree)
            detail = (result.stderr.strip() or result.stdout.strip()).splitlines()
            return merged, "merging %s into %s failed: %s" % (
                producer["branch"],
                consumer_branch,
                detail[0] if detail else "exit %d" % result.returncode,
            )
        merged = merged + (producer["branch"],)
    return merged, None


def commit_paths(tree, paths, message):
    present = [p for p in paths if os.path.lexists(os.path.join(tree, p))]
    tracked = git_paths(["ls-files", "-z", "--", *paths], tree) if paths else []
    targets = sorted(set(present) | set(tracked))
    if targets:
        git(["add", "-A", "--", *targets], tree)
        if git(["status", "--porcelain", "--", *targets], tree):
            git(["commit", "-q", "-m", message, "--", *targets], tree)
    return git(["rev-parse", "HEAD"], tree)


def lane_record(lane, msp, state, **fields):
    base = dict.fromkeys(LANE_RECORD_KEYS)
    return {
        **base,
        **fields,
        "lane": lane,
        "msp": msp,
        "state": state,
        "merged": list(fields.get("merged") or ()),
    }


def _cross_producers(plan, lane, producers, trees, records):
    lanes = plan["lanes"]
    own = lanes[lane]["msp"]
    seen = ()
    for producer in producers:
        msp = lanes[producer]["msp"]
        if msp == own or any(entry["msp"] == msp for entry in seen):
            continue
        seen = seen + (
            {
                "msp": msp,
                "branch": trees[msp]["branch"],
                "commit": records.get(producer, {}).get("commit"),
            },
        )
    return seen


def _worker_values(plan, lane, tree, models, run_dir, session_id):
    brief = plan["briefs"][lane]
    tier = plan["lanes"][lane].get("tier")
    return {
        "task": brief["text"],
        "model": (models or {}).get(tier, ""),
        "tier": tier,
        "worktree": tree["path"],
        "branch": tree["branch"],
        "lane": lane,
        "msp": tree.get("label") or tree["msp"],
        "charter": brief.get("charter"),
        "document": brief.get("document"),
        "run_dir": run_dir,
        "session_id": session_id,
    }


def _landing_message(plan, lane, tree):
    return "chore(%s): land Lane %d (%s)" % (
        tree.get("label") or tree["msp"],
        lane,
        ", ".join(plan["lanes"][lane]["steps"]),
    )


def check_identity(repo):
    for ident in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
        found = git_run(["var", ident], repo)
        if found.returncode != 0:
            raise ConfigError(
                "git has no commit identity in %s, so no Lane could land: %s"
                % (repo, (found.stderr or "").strip() or "git var %s failed" % ident)
            )


def _still_written(plan, lane, tree):
    write_set = plan["briefs"][lane]["write_set"]
    return bool(write_set) and bool(
        git_run(["status", "--porcelain", "--", *write_set], tree["path"]).stdout.strip()
    )


def _land(plan, lane, tree):
    try:
        commit = commit_paths(
            tree["path"],
            plan["briefs"][lane]["write_set"],
            _landing_message(plan, lane, tree),
        )
    except GitError as error:
        write_set = plan["briefs"][lane]["write_set"]
        if write_set:
            git_run(["reset", "-q", "--", *write_set], tree["path"])
        return None, "%s: %s" % (LANE_COMMIT_FAILED, error)
    return commit, None


def _verdict(lane, tree, started, merged, out, err, code, timed_out, session_id):
    returned = last_return(out)
    judged, why = lane_verdict(code, returned, timed_out)
    limit = None if timed_out or judged != "failed" else usage_limit_line(out, err)
    state, reason = (judged, why) if limit is None else (LANE_PARKED, "usage limit: %s" % limit)
    return lane_record(
        lane,
        tree["msp"],
        state,
        reason=reason,
        exit=code,
        timed_out=timed_out,
        returned=returned,
        stdout=out,
        stderr=err,
        merged=merged,
        commit=None,
        started=started,
        finished=now(),
        session_id=session_id,
    )


def _status_paths(entries):
    skip = False
    for entry in entries:
        if skip:
            skip = False
        elif len(entry) > 3:
            skip = entry[0] in "RC"
            yield entry[3:]


def _dirty_paths(tree):
    entries = git_run(
        ["status", "--porcelain", "-z", "--untracked-files=all"], tree["path"]
    ).stdout.split("\0")
    return tuple(dict.fromkeys(_status_paths(entries)))


def _foreign_writes(plan, msp, tree, batch, records):
    siblings = [
        index
        for index, lane in enumerate(plan["lanes"])
        if lane["msp"] == msp and index not in batch
    ]
    unsettled = frozenset(
        _norm(path)
        for index in siblings
        if (records.get(index) or {}).get("state") != "ok"
        for path in plan["briefs"][index]["write_set"]
    )
    owners = {}
    for index in sorted(siblings, reverse=True):
        if (records.get(index) or {}).get("state") == "ok":
            for path in plan["briefs"][index]["write_set"]:
                owners = {**owners, _norm(path): index}
    touched = sorted(
        path
        for path in _dirty_paths(tree)
        if _norm(path) in owners and _norm(path) not in unsettled
    )
    if touched:
        try:
            commit_paths(
                tree["path"],
                touched,
                "chore(%s): keep changes to files other Lanes own (%s)"
                % (tree.get("label") or tree["msp"], ", ".join(touched)),
            )
        except GitTimeout:
            raise
        except GitError:
            pass
    return [{"path": path, "owner": owners[_norm(path)]} for path in touched]


def _suspects(batch, held, path):
    claimants = [
        lane
        for lane in batch
        if path in (((held[lane].get("returned") or {}).get("files_changed")) or [])
    ]
    return claimants or list(batch)


def _landed(plan, lane, tree, record):
    commit, reason = _land(plan, lane, tree)
    if reason is not None:
        return {**record, "state": "failed", "reason": reason}
    return {**record, "commit": commit}


def dispatch(
    plan,
    trees,
    command,
    timeout,
    concurrency,
    run_dir,
    base,
    models=None,
    prior=None,
    on_lane=None,
    observer=None,
    env=None,
):
    if timeout is None:
        raise ConfigError("a per-Lane timeout is required; mitosis has no default")
    check_models(plan, command, models)
    lanes = plan.get("lanes") or []
    edges = lane_edges(plan)
    tree_of = {tree["msp"]: tree for tree in trees}
    lane_dir = os.path.join(run_dir, "lanes")
    os.makedirs(lane_dir, exist_ok=True)
    records = {
        int(index): record
        for index, record in (prior or {}).items()
        if isinstance(record, dict) and record.get("state") == "ok"
    }
    held = {
        int(index): {**record, "state": "ok"}
        for index, record in (prior or {}).items()
        if isinstance(record, dict)
        and record.get("state") == LANE_HELD
        and _still_written(plan, int(index), tree_of[lanes[int(index)]["msp"]])
    }
    ordered = [int(i) for i in plan.get("lane_order") or range(len(lanes))]
    every = ordered + [i for i in range(len(lanes)) if i not in ordered]
    pending = [i for i in every if i not in records and i not in held]
    running = {}
    parked_by = None
    cap = max(1, int(concurrency))

    def settle(known, lane, record):
        if on_lane is not None:
            on_lane(lane, record)
        return {**known, lane: record}

    with ThreadPoolExecutor(max_workers=cap) as pool:
        while pending or running or held:
            progressed = False
            busy = {lanes[entry[0]]["msp"] for entry in running.values()}
            for msp in sorted({lanes[lane]["msp"] for lane in held} - busy):
                batch = [lane for lane in held if lanes[lane]["msp"] == msp]
                landed = {lane: _landed(plan, lane, tree_of[msp], held[lane]) for lane in batch}
                foreign = _foreign_writes(plan, msp, tree_of[msp], batch, records)
                for lane in batch:
                    records = settle(
                        records,
                        lane,
                        {
                            **landed[lane],
                            "foreign_writes": [
                                {**entry, "suspects": suspects}
                                for entry in foreign
                                for suspects in (_suspects(batch, held, entry["path"]),)
                                if lane in suspects
                            ],
                        },
                    )
                held = {key: value for key, value in held.items() if key not in batch}
                progressed = True
            if parked_by is not None:
                for lane in pending:
                    records = settle(
                        records,
                        lane,
                        lane_record(
                            lane,
                            lanes[lane]["msp"],
                            LANE_PARKED,
                            reason="not dispatched: Lane %d stopped at a usage limit" % parked_by,
                        ),
                    )
                pending = []
            waiting = ()
            holding = {lanes[lane]["msp"] for lane in held}
            for lane in pending:
                msp = lanes[lane]["msp"]
                producers = edges.get(lane, ())
                unfinished = [p for p in producers if p not in records]
                if unfinished:
                    active = {entry[0] for entry in running.values()} | set(held)
                    live = [p for p in unfinished if p in pending or p in active]
                    if live:
                        waiting = waiting + (lane,)
                        continue
                    records = settle(
                        records,
                        lane,
                        lane_record(
                            lane,
                            msp,
                            "blocked",
                            reason="producer Lane %d never ran" % unfinished[0],
                        ),
                    )
                    progressed = True
                    continue
                if len(running) >= cap or msp in holding:
                    waiting = waiting + (lane,)
                    continue
                bad = [p for p in producers if records[p].get("state") != "ok"]
                if bad:
                    records = settle(
                        records,
                        lane,
                        lane_record(
                            lane,
                            msp,
                            "blocked",
                            reason="producer Lane %d is %s"
                            % (bad[0], records[bad[0]].get("state")),
                        ),
                    )
                    progressed = True
                    continue
                tree = tree_of[msp]
                cross = _cross_producers(plan, lane, producers, tree_of, records)
                merged, error = merge_producers(tree["path"], tree["branch"], cross, base)
                if error is not None:
                    records = settle(
                        records,
                        lane,
                        lane_record(lane, msp, "merge-blocked", reason=error, merged=merged),
                    )
                    progressed = True
                    continue
                out = os.path.join(lane_dir, "%d.out" % lane)
                err = os.path.join(lane_dir, "%d.err" % lane)
                session = core.new_session_id()
                argv = build_argv(
                    command, _worker_values(plan, lane, tree, models, run_dir, session)
                )
                carried = core.carried_session(argv, session)
                future = pool.submit(run_command, argv, tree["path"], timeout, out, err, env)
                running = {**running, future: (lane, now(), merged, out, err, carried)}
                notify(
                    observer,
                    "lane-dispatched",
                    {"lane": lane, "msp": msp, "tier": lanes[lane].get("tier"), "session_id": carried},
                )
                progressed = True
            pending = list(waiting)
            if running:
                done, _ = wait(list(running), return_when=FIRST_COMPLETED)
                for future in done:
                    lane, started, merged, out, err, session = running[future]
                    running = {f: v for f, v in running.items() if f is not future}
                    code, timed_out = future.result()
                    tree = tree_of[lanes[lane]["msp"]]
                    judged = _verdict(
                        lane, tree, started, merged, out, err, code, timed_out, session
                    )
                    if judged["state"] == "ok":
                        held = {**held, lane: judged}
                        if on_lane is not None:
                            on_lane(lane, {**judged, "state": LANE_HELD})
                    else:
                        records = settle(records, lane, judged)
                        if judged["state"] == LANE_PARKED and parked_by is None:
                            parked_by = lane
            elif pending and not progressed and not held:
                for lane in pending:
                    records = settle(
                        records,
                        lane,
                        lane_record(
                            lane, lanes[lane]["msp"], "blocked", reason="no producer can finish"
                        ),
                    )
                pending = []
    return records


def write_json(path, payload):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    partial = path + ".partial"
    with open(partial, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(partial, path)


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_state(run_dir, state):
    write_json(os.path.join(run_dir, STATE_FILE), state)
    return state


def load_state(run_dir):
    path = os.path.join(run_dir, STATE_FILE)
    if not os.path.isfile(path):
        return None
    loaded = read_json(path)
    return loaded if isinstance(loaded, dict) else None


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def document_drift(plan, root):
    source = plan.get("source")
    if not isinstance(source, dict) or not source.get("path"):
        return None
    path = os.path.join(root, source["path"]) if root else source["path"]
    actual = sha256_file(path) if os.path.isfile(path) else None
    expected = source.get("sha256")
    return {
        "path": source["path"],
        "expected": expected,
        "actual": actual,
        "drifted": actual != expected,
    }


def prior_state(run_dir, plan, resume):
    if not resume:
        return None
    state = load_state(run_dir)
    if state is None:
        return None
    if state.get("plan_id") != plan.get("plan_id"):
        raise ResumeError(
            "%s holds a run of plan %s, not plan %s; the work-set changed, "
            "so prior results do not apply" % (run_dir, state.get("plan_id"), plan.get("plan_id"))
        )
    return state


def merge_danger_pair(header):
    if header is None or header.get("door") is None:
        return None
    return {"door": header["door"], "blast_radius": header.get("blast_radius")}


def carried_header(header, prior):
    recorded = (prior or {}).get("merge_danger")
    given = merge_danger_pair(header)
    if header is None or recorded is None:
        return header
    if given is None:
        return {**header, **recorded}
    if given != recorded:
        raise ResumeError(
            "this run recorded --pr-door %s and --pr-blast-radius %r; a resume passes the same "
            "pair or neither, so every pull request of one run carries one door call"
            % (recorded["door"], recorded["blast_radius"])
        )
    return header


def msp_producers(plan):
    lanes = plan.get("lanes") or []
    producers = {index: () for index in range(len(plan.get("msps") or []))}
    for consumer, sources in lane_edges(plan).items():
        own = lanes[consumer]["msp"]
        for producer in sources:
            other = lanes[producer]["msp"]
            if other != own and other not in producers[own]:
                producers = {**producers, own: producers[own] + (other,)}
    return {msp: tuple(sorted(found)) for msp, found in producers.items()}


def msp_order(plan):
    producers = msp_producers(plan)
    placed = ()
    remaining = sorted(producers)
    while remaining:
        ready = [msp for msp in remaining if all(p in placed for p in producers[msp])]
        if not ready:
            ready = remaining[:1]
        placed = placed + tuple(ready)
        remaining = [msp for msp in remaining if msp not in ready]
    return placed


def _norm(path):
    return os.path.normpath(str(path)).replace(os.sep, "/")


def _steps_by_name(plan):
    return {item["name"]: item for item in plan.get("items") or []}


def _label(plan, msp):
    return plan["msps"][msp].get("label") or str(msp)


def acceptance_files(plan, msp):
    steps = _steps_by_name(plan)
    named = ()
    for name in plan["msps"][msp]["steps"]:
        for entry in steps[name].get("acceptance") or []:
            if isinstance(entry, dict) and isinstance(entry.get("file"), str):
                path = _norm(entry["file"])
                if path not in named:
                    named = named + (path,)
    return named


def implementation_paths(plan, msp):
    named = acceptance_files(plan, msp)
    return [path for path in plan["msps"][msp]["files"] if _norm(path) not in named]


def step_implementation_paths(plan, msp, name):
    named = acceptance_files(plan, msp)
    steps = _steps_by_name(plan)
    return [
        path for path in (steps[name].get("files") or []) if _norm(path) not in named
    ]


def steps_without_implementation_change(plan, msp, tree, base):
    unchanged = ()
    for name in plan["msps"][msp]["steps"]:
        paths = step_implementation_paths(plan, msp, name)
        if paths and not git(["diff", "--name-only", base, "--", *paths], tree):
            unchanged = unchanged + (name,)
    return frozenset(unchanged)


UNCHANGED_REASON = (
    "the Step changed no implementation, so no property can depend on it"
)


def _property_verdict(name, modes, unchanged):
    if name in unchanged:
        return "not-applicable", UNCHANGED_REASON
    if modes.get(name) == "serial":
        return "not-applicable", "serial surface; the probe cannot run it headlessly"
    return None, None


def msp_properties(plan, msp, unchanged=frozenset()):
    steps = _steps_by_name(plan)
    modes = plan.get("verify_modes") or {}
    found = ()
    for name in plan["msps"][msp]["steps"]:
        acceptance = steps[name].get("acceptance") or []
        if not acceptance:
            found = found + (
                {
                    "step": name,
                    "file": None,
                    "test": None,
                    "outcome": "not-applicable",
                    "reason": "no acceptance property declared",
                },
            )
            continue
        outcome, reason = _property_verdict(name, modes, unchanged)
        for entry in acceptance:
            check = {"check": entry["check"]} if "check" in entry else {}
            found = found + (
                {
                    "step": name,
                    "file": entry["file"],
                    "test": entry["test"],
                    "outcome": outcome,
                    "reason": reason,
                    **check,
                },
            )
    return found


def written_since(tree):
    entries = git_paths(
        ["status", "--porcelain", "-z", "--untracked-files=all", "--no-renames"], tree
    )
    return tuple(dict.fromkeys(entry[3:] for entry in entries if len(entry) > 3))


def commit_all(tree, message):
    git(["add", "-A"], tree)
    if not git_ok(["diff", "--cached", "--quiet", "--ignore-submodules=none"], tree):
        git(["commit", "-q", "-m", message], tree)
    return git(["rev-parse", "HEAD"], tree)


def _remove_cache(folder):
    if os.path.islink(folder):
        os.remove(folder)
    elif os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)


def clear_caches_beside(tree, paths):
    for folder in sorted({_pycache_of(tree, path) for path in paths}):
        _remove_cache(folder)


def _clear_index_flags(tree, paths):
    tracked = git_paths(["ls-files", "-z", "--", *paths], tree) if paths else []
    for flag in ("--no-skip-worktree", "--no-assume-unchanged") if tracked else ():
        git(["update-index", flag, "--", *tracked], tree)


def revert_implementation(tree, base, paths):
    _clear_index_flags(tree, paths)
    reverted = ()
    for path in paths:
        if git_ok(["cat-file", "-e", "%s:%s" % (base, path)], tree):
            git(["checkout", "-q", base, "--", path], tree)
        else:
            git(["rm", "-r", "-q", "--ignore-unmatch", "--", path], tree)
            full = os.path.join(tree, path)
            if os.path.isdir(full) and not os.path.islink(full):
                shutil.rmtree(full)
            elif os.path.lexists(full):
                os.remove(full)
        reverted = reverted + (path,)
    clear_caches_beside(tree, reverted)
    return list(reverted)


def run_acceptance(command, tree, entry, log_path, timeout, env=None):
    argv = build_argv(command, {"file": entry["file"], "test": entry["test"], "worktree": tree})
    return run_command(argv, tree, timeout, log_path, log_path + ".err", env)


def probe_outcome(with_work, reverted):
    if with_work[1]:
        return "inconclusive", "timed out with the work present"
    if with_work[0] != 0:
        return "inconclusive", "exit %d with the work present" % with_work[0]
    if reverted[1]:
        return "inconclusive", "timed out with the implementation reverted"
    if reverted[0] == 0:
        return "inert", "passes with the implementation reverted"
    if reverted[0] == ACCEPTANCE_FAILURE_EXIT:
        return "pass", None
    return (
        "inconclusive",
        "exit %d with the implementation reverted; a test that never ran is not a test that failed"
        % reverted[0],
    )


def gate_outcome(properties):
    outcomes = [entry["outcome"] for entry in properties]
    for candidate in ("weak", "inert", "inconclusive", "pass"):
        if candidate in outcomes:
            return candidate
    return "not-applicable"


GATE_STATES = {"weak": "gate-weak", "inert": "gate-inert", "inconclusive": "gate-inconclusive"}


def gate_state(result):
    return GATE_STATES.get(result.get("outcome"))


def _gate_result(properties, implementation, commit, reverted):
    resolved = [
        {**entry, "outcome": entry["outcome"] or "inconclusive", "seconds": entry.get("seconds", 0)}
        for entry in properties
    ]
    outcome = gate_outcome(resolved)
    return {
        "outcome": outcome,
        "blocks": outcome in BLOCKING_OUTCOMES,
        "properties": resolved,
        "implementation": list(implementation),
        "reverted": list(reverted),
        "counts": {
            key: sum(1 for e in resolved if e["outcome"] == key) for key in core.GATE_OUTCOMES
        },
        "commit": commit,
    }


def commit_before_gate(plan, msp, tree):
    recover_gate(tree)
    return commit_all(tree, "chore(%s): commit the MSP's work before the gate" % _label(plan, msp))


def _pycache_of(tree, path):
    return os.path.join(tree, os.path.dirname(path), "__pycache__")


def _read_text(tree, path):
    with open(os.path.join(tree, path), encoding="utf-8", errors="surrogateescape", newline="") as handle:
        return handle.read()


def _write_text(tree, path, text):
    with open(os.path.join(tree, path), "w", encoding="utf-8", errors="surrogateescape", newline="") as handle:
        handle.write(text)


def property_mutants(plan, msp, tree, base, commit, entry):
    paths = step_implementation_paths(plan, msp, entry["step"])
    if not paths:
        return ()
    names = git_paths(
        ["diff", "-z", "--name-only", "--no-renames", base, commit, "--", *paths], tree
    )
    added = mutants.added_lines(
        {
            name: git(["diff", "-U0", "--no-color", base, commit, "--", ":(literal)" + name], tree)
            for name in names
        }
    )

    def read(path):
        return _read_text(tree, path)

    found = mutants.compute(added, read) or mutants.fallback(added, read)
    return tuple(mutants.select(found, MUTANT_CAP))


def _run_mutant(command, tree, entry, mutant, log_path, timeout, env, restore, mark):
    path = mutant["file"]
    mutated = mutants.apply(_read_text(tree, path), mutant)
    if mutated is None:
        return None, ()
    mark((path,))
    shutil.rmtree(_pycache_of(tree, path), ignore_errors=True)
    try:
        _write_text(tree, path, mutated)
        ran = run_acceptance(command, tree, entry, log_path, timeout, env)
    finally:
        written = restore()
        shutil.rmtree(_pycache_of(tree, path), ignore_errors=True)
    return ran, written


def _mutant_place(mutant):
    return {"file": mutant["file"], "line": mutant["line"], "op": mutant["op"]}


def _timed(call):
    started = time.monotonic()
    result = call()
    return result, time.monotonic() - started


def mutant_verdict(
    plan, msp, tree, base, commit, command, index, entry, log_dir, timeout, env, restore, mark
):
    tried = ()
    written = ()
    seconds = entry.get("seconds", 0)
    for mutant in property_mutants(plan, msp, tree, base, commit, entry):
        (ran, left), spent = _timed(
            lambda mutant=mutant: _run_mutant(
                command,
                tree,
                entry,
                mutant,
                os.path.join(log_dir, "%d-mutant-%d.log" % (index, len(tried))),
                timeout,
                env,
                restore,
                mark,
            )
        )
        seconds = seconds + spent
        written = written + left
        if ran is None:
            continue
        tried = tried + (mutant,)
        if ran[1] or ran[0] != 0:
            return (
                {
                    **entry,
                    "mutants": {"tried": len(tried), "killed": _mutant_place(mutant)},
                    "seconds": seconds,
                },
                written,
            )
    record = {"tried": len(tried), "killed": None}
    if not tried:
        return {**entry, "mutants": record, "seconds": seconds}, written
    first = tried[0]
    return (
        {
            **entry,
            "seconds": seconds,
            "outcome": "weak",
            "reason": "no mutant of the work fails it: it still passes when %s:%d changes by %s"
            % (first["file"], first["line"], first["op"]),
            "mutants": record,
        },
        written,
    )


def gate(plan, msp, tree, branch, base, command, log_dir, timeout=None, env=None):
    commit = commit_before_gate(plan, msp, tree)
    locked = tuple(plan.get("locked") or ())

    def restore():
        return restore_gate_commit(tree, branch, commit, locked)

    def mark(paths):
        mark_gate(tree, branch, commit, paths, locked)

    mark(implementation_paths(plan, msp))
    try:
        result, written = _gate_runs(
            plan, msp, tree, branch, base, command, log_dir, timeout, env, commit, restore, mark
        )
    finally:
        last = restore()
        clear_gate_mark(tree)
    referee_written = sorted(set(written) | set(last))
    return {
        **result,
        "referee": referee_written,
        "blocks": result["blocks"] or bool(referee_written),
    }


def _gate_runs(
    plan, msp, tree, branch, base, command, log_dir, timeout, env, commit, restore, mark
):
    properties = msp_properties(
        plan, msp, steps_without_implementation_change(plan, msp, tree, base)
    )
    implementation = implementation_paths(plan, msp)
    runnable = [index for index, entry in enumerate(properties) if entry["outcome"] is None]
    if not runnable:
        return _gate_result(properties, implementation, commit, ()), ()
    os.makedirs(log_dir, exist_ok=True)
    with_work_runs = {
        index: _timed(
            lambda index=index: run_acceptance(
                command,
                tree,
                properties[index],
                os.path.join(log_dir, "%d-with-work.log" % index),
                timeout,
                env,
            )
        )
        for index in runnable
    }
    with_work = {index: ran for index, (ran, _) in with_work_runs.items()}
    after_work = restore()
    probe = "%s/%s" % (PROBE_PREFIX, branch)
    git(["checkout", "-q", "-B", probe], tree)
    try:
        reverted = revert_implementation(tree, base, implementation)
        without_runs = {
            index: _timed(
                lambda index=index: run_acceptance(
                    command,
                    tree,
                    properties[index],
                    os.path.join(log_dir, "%d-reverted.log" % index),
                    timeout,
                    env,
                )
            )
            for index in runnable
            if with_work[index] == (0, False)
        }
        without = {index: ran for index, (ran, _) in without_runs.items()}
    finally:
        after_probe = restore()
        git_run(["branch", "-D", probe], tree)
    judged = ()
    for index, entry in enumerate(properties):
        if index not in with_work:
            judged = judged + (entry,)
            continue
        outcome, reason = probe_outcome(with_work[index], without.get(index, (None, False)))
        judged = judged + (
            {
                **entry,
                "outcome": outcome,
                "reason": reason,
                "with_work": with_work[index][0],
                "reverted": without[index][0] if index in without else None,
                "seconds": with_work_runs[index][1]
                + (without_runs[index][1] if index in without_runs else 0),
            },
        )
    verdicts = tuple(
        mutant_verdict(
            plan, msp, tree, base, commit, command, index, entry, log_dir, timeout, env, restore, mark
        )
        if index in with_work and entry["outcome"] == "pass"
        else (entry, ())
        for index, entry in enumerate(judged)
    )
    toothed = tuple(entry for entry, _ in verdicts)
    written = after_work + after_probe + tuple(path for _, left in verdicts for path in left)
    return _gate_result(toothed, implementation, commit, reverted), written


def changed_files(tree, branch, base, exclude=()):
    excluded = ["^" + ref for ref in exclude if branch_exists(tree, ref)]
    names = git_paths(
        ["log", "-z", "--format=", "--name-only", "--no-renames", branch, "^" + base, *excluded], tree
    )
    return sorted(dict.fromkeys(names))


def msp_owner_of(plan, path):
    normalized = _norm(path)
    for index, msp in enumerate(plan.get("msps") or []):
        if normalized in {_norm(f) for f in msp.get("files") or ()}:
            return index
    return None


def _unproven_paths(plan, msp):
    steps = _steps_by_name(plan)
    return {
        _norm(path)
        for name in plan["msps"][msp]["steps"]
        if not (steps[name].get("acceptance") or [])
        for path in (steps[name].get("files") or [])
    }


def locked_record(repo, branch, locked):
    files = ()
    for path in locked:
        shown = git_run(["show", "%s:%s" % (branch, path)], repo, raw=True)
        if shown.returncode != 0:
            raise ConfigError(
                "--charter locks %s, which %s does not hold; commit the locked checks on it "
                "before the build" % (path, branch)
            )
        files = files + ((path, shown.stdout),)
    return {
        "locked": list(locked),
        "receipt_lock": referee.receipt_lock(files),
        "commit": git(["rev-parse", "--verify", branch + "^{commit}"], repo),
    }


def _installed_folder(entry):
    return entry.name in INSTALLED_FOLDERS or os.path.isfile(os.path.join(entry.path, VENV_MARKER))


def disk_paths(tree, folder=""):
    try:
        with os.scandir(os.path.join(tree, folder)) as listing:
            entries = sorted(listing, key=lambda entry: entry.name)
    except OSError:
        return
    for entry in entries:
        if entry.name == ".git":
            continue
        relative = folder + "/" + entry.name if folder else entry.name
        if entry.is_dir(follow_symlinks=False):
            if not _installed_folder(entry):
                yield from disk_paths(tree, relative)
        else:
            yield relative


def _disk_bytes(full):
    try:
        status = os.lstat(full)
        if stat.S_ISLNK(status.st_mode):
            return os.fsencode(os.readlink(full))
        if not stat.S_ISREG(status.st_mode):
            return None
        with open(full, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def _differs_from(tree, base, path):
    stored = git_run(["cat-file", "--filters", "%s:%s" % (base, path)], tree, raw=True)
    on_disk = _disk_bytes(os.path.join(tree, path))
    return stored.returncode != 0 or on_disk is None or on_disk != stored.stdout


def referee_disk_writes(tree, base, locked):
    locked = tuple(locked)
    present = tuple(
        path for path in disk_paths(tree) if referee.referee_reason(path, locked) is not None
    )
    held = tuple(
        path
        for path in git_paths(["ls-tree", "-r", "-z", "--full-tree", "--name-only", base], tree)
        if referee.referee_reason(path, locked) is not None
    )
    clear_caches_beside(tree, present + held + locked)
    changed = {
        path
        for path in present
        if os.path.lexists(os.path.join(tree, path)) and _differs_from(tree, base, path)
    }
    missing = {path for path in held if not os.path.lexists(os.path.join(tree, path))}
    return sorted(changed | missing)


def referee_writes(plan, tree, branch, base):
    locked = tuple(plan.get("locked") or ())
    changed = git_paths(["diff", "-z", "--name-only", "--no-renames", base, branch], tree)
    committed = {path for path in changed if referee.referee_reason(path, locked) is not None}
    return sorted(committed | set(referee_disk_writes(tree, base, locked)))


def restore_gate_commit(tree, branch, commit, locked):
    written = tuple(referee_disk_writes(tree, commit, locked))
    git(["checkout", "-q", "-f", branch], tree)
    git(["reset", "-q", "--hard", commit], tree)
    git(["clean", "-ffdq"], tree)
    revert_implementation(tree, commit, written)
    left = written_since(tree) + tuple(referee_disk_writes(tree, commit, locked))
    if left:
        raise RestoreError(branch, commit, left)
    return written


def gate_marker(tree):
    return os.path.join(git(["rev-parse", "--absolute-git-dir"], tree), GATE_MARKER)


def _marked_paths(marker, commit):
    if not os.path.isfile(marker):
        return ()
    recorded = read_json(marker)
    if not isinstance(recorded, dict) or recorded.get("commit") != commit:
        return ()
    return tuple(recorded.get("paths") or ())


def mark_gate(tree, branch, commit, paths, locked):
    marker = gate_marker(tree)
    write_json(
        marker,
        {
            "branch": branch,
            "commit": commit,
            "paths": sorted(set(_marked_paths(marker, commit)) | set(paths)),
            "locked": list(locked),
        },
    )


def clear_gate_mark(tree):
    try:
        os.remove(gate_marker(tree))
    except FileNotFoundError:
        pass


def _recover_from(tree, recorded):
    branch = recorded["branch"]
    restore_gate_commit(tree, branch, recorded["commit"], tuple(recorded["locked"]))
    probe = "%s/%s" % (PROBE_PREFIX, branch)
    git_run(["branch", "-D", probe], tree)
    if branch_exists(tree, probe):
        raise GitError(["branch", "-D", probe], 1, "the probe branch %s is still there" % probe)
    clear_caches_beside(tree, tuple(recorded["paths"]))


def recover_gate(tree):
    marker = gate_marker(tree)
    if not os.path.isfile(marker):
        return False
    try:
        _recover_from(tree, read_json(marker))
    except (GitError, OSError, ValueError, KeyError, TypeError) as error:
        raise GateRecoveryError(tree, marker, error)
    os.remove(marker)
    return True


def reconcile(plan, msp, tree, branch, base, producer_branches=()):
    changed = changed_files(tree, branch, base, producer_branches)
    declared = sorted({_norm(f) for f in plan["msps"][msp].get("files") or ()})
    undeclared = [path for path in changed if _norm(path) not in declared]
    missing = [path for path in declared if path not in {_norm(c) for c in changed}]
    unproven = _unproven_paths(plan, msp)
    untouched = [
        path
        for path in missing
        if path in unproven and git_ok(["cat-file", "-e", "%s:%s" % (base, path)], tree)
    ]
    unwritten = [path for path in missing if path not in untouched]
    crossing = ()
    for path in undeclared:
        owner = msp_owner_of(plan, path)
        if owner is not None and owner != msp:
            crossing = crossing + ({"path": path, "msp": owner, "label": _label(plan, owner)},)
    written = referee_writes(plan, tree, branch, base)
    return {
        "changed": changed,
        "declared": declared,
        "undeclared": undeclared,
        "unwritten": unwritten,
        "untouched": untouched,
        "crossing": list(crossing),
        "referee": written,
        "fatal": bool(crossing or written),
    }


def _stack_target(producer, trees, settled):
    owners = {tree["branch"]: index for index, tree in enumerate(trees)}
    seen = frozenset()
    current = producer
    while current not in seen:
        record = (settled or {}).get(str(current)) or {}
        if record.get("state") != MSP_UNCHANGED:
            return trees[current]["branch"]
        base = (record.get("ship") or {}).get("base")
        if base not in owners:
            return base
        seen = seen | {current}
        current = owners[base]
    return None


def pr_base(plan, msp, feature_branch, trees, repo, settled=None):
    targets = {
        producer: _stack_target(producer, trees, settled)
        for producer in msp_producers(plan).get(msp, ())
    }
    stacked = [p for p, target in targets.items() if target not in (None, feature_branch)]
    distinct = tuple(dict.fromkeys(targets[p] for p in stacked))
    if len(distinct) > 1:
        return feature_branch, {
            "msp": msp,
            "label": _label(plan, msp),
            "producers": stacked,
            "reason": "a pull request can stack on one predecessor; this MSP has %d"
            % len(stacked),
        }
    if distinct and branch_exists(repo, distinct[0]):
        return distinct[0], None
    return feature_branch, None


def _judged(gate_result):
    return {
        (entry.get("step"), entry.get("file"), entry.get("test")): entry
        for entry in (gate_result or {}).get("properties") or []
    }


def _name_of(plan, index):
    items = plan.get("items") or []
    return items[index].get("name") if 0 <= index < len(items) else "a Step"


def manifest_gaps(plan, msp):
    owned = set(plan["msps"][msp]["steps"])
    return [
        gap
        for gap in shape.manifest_gaps(plan.get("items") or [])
        if gap["reached"] == 0 and _name_of(plan, gap["owner"]) in owned
    ]


PR_TYPES = ("feat", "fix", "refactor", "docs", "test", "chore", "perf", "ci")

PR_DOORS = ("one-way", "two-way")

TITLE_PATTERN = re.compile(
    r"^(?=.{1,72}$)(feat|fix|refactor|docs|test|chore|perf|ci)(\([a-z0-9][a-z0-9-]{0,15}\))?: "
    r"[a-z][\x20-\x7E]*[\x21-\x2D\x2F-\x7E]$"
)

SCOPE_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,15}")

TITLE_CAP = 72

VALUE_CAP = 500

SECTION_LIMIT = 8

RECEIPT_TOKENS = ("work-type:", "test-removal:", "receipt:", "receipt-cmd:", "receipt-lock:")

TRIM_SET = "".join(
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

CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")

INVISIBLE_CHARACTER = re.compile(
    r"[\u0080-\u009f\u00ad\u061c\u180e\u200b-\u200f\u2028-\u202f\u2060-\u206f\ufeff\ufff9-\ufffb]"
)

TAG_OPENER = re.compile(r"<[!/?A-Za-z]")

CHARACTER_REFERENCE = re.compile(r"&(?:#[0-9]+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);")

OUTSIDE_PRINTABLE_ASCII = re.compile(r"[^\x20-\x7e]")

INLINE_CONSTRUCTS = (
    ("\\", "holds a backslash"),
    ("](", "holds ]("),
    ("![", "holds !["),
    ("]:", "holds ]:"),
    ("$", "holds $"),
)

URL_TEXT = re.compile(r"://|www\.|github\.com/", re.IGNORECASE)

BLANK_SYMBOLS = frozenset(chr(code) for code in (0x2800, 0x16FE4, 0x1D159))

COMPOSED_RULES = (
    (re.compile(r"<"), "holds <"),
    (re.compile(r"@"), "holds @"),
    (re.compile(r"#[0-9]"), "holds # followed by a digit"),
    (re.compile(r"\*"), "holds *"),
    (re.compile(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])"), "holds _ outside a word"),
    (re.compile(r"(?<![A-Za-z0-9])gh-[0-9]", re.IGNORECASE), "holds GH- followed by a digit"),
    (re.compile(r":[a-z0-9_+-]+:"), "holds an emoji shortcode"),
    (re.compile(r"[0-9A-Fa-f]{40}"), "holds a run of 40 hexadecimal digits"),
)

NAMEABLE_CAP = 100

BLOCK_OPENERS = ("`", "~", "#", ">", "|")

SETEXT_UNDERLINE = re.compile(r"[=-]+")

RESERVED_FIELD = re.compile(
    r"(?:verified|not verified|door|blast radius):", re.IGNORECASE | re.ASCII
)

RESERVED_STRUCTURE = "Supersedes "

SENTENCE_ENDS = ".?!"

SENTENCE_END = re.compile(r"[.?!](?= |$)")

WHAT_HEADING = "## What changed"

WHY_HEADING = "## Why"

RISK_HEADING = "## Risk"

DOOR_PREFIX = "Door: "

BLAST_RADIUS_PREFIX = "Blast radius: "

VERIFICATION_HEADING = "## Verification"

BULLET = "- "

VERIFIED_PREFIX = "Verified: "

NOT_VERIFIED_PREFIX = "Not verified: "

PROVEN = "fails with the work reverted and passes with it"

WHAT_FALLBACK = "Builds one Step of this change."


def code_units(value):
    return len(value.encode("utf-16-le", "surrogatepass")) // 2


def blank_or_overlay(character):
    category = unicodedata.category(character)
    return (
        (category == "Zs" and character != " ")
        or category == "Me"
        or unicodedata.combining(character) == 1
        or character in BLANK_SYMBOLS
    )


def value_fault(value, cap=VALUE_CAP):
    if not isinstance(value, str):
        return "is not text"
    if CONTROL_CHARACTER.search(value):
        return "holds a control character"
    if value != value.strip(TRIM_SET):
        return "has leading or trailing whitespace"
    if not value:
        return "is empty"
    if code_units(value) > cap:
        return "is %d UTF-16 code units long, over the %d-unit cap" % (code_units(value), cap)
    if value.startswith("@"):
        return "begins with @"
    if INVISIBLE_CHARACTER.search(value):
        return "holds an invisible or bidirectional character"
    if TAG_OPENER.search(value):
        return "holds < followed by !, /, ? or a letter"
    if CHARACTER_REFERENCE.search(value):
        return "holds a character reference"
    inline = next((rule for construct, rule in INLINE_CONSTRUCTS if construct in value), None)
    if inline is not None:
        return inline
    if URL_TEXT.search(value):
        return "holds ://, www. or github.com/"
    if "~" in value:
        return "holds ~"
    if "``" in value or value.count("`") % 2:
        return "holds two backticks in a row or an odd number of backticks"
    if any(blank_or_overlay(character) for character in value):
        return "holds a space other than the plain space, an enclosing or overlay mark, or a blank-looking symbol"
    if value.startswith(BLOCK_OPENERS):
        return "begins with a markdown block opener"
    if SETEXT_UNDERLINE.fullmatch(value):
        return "is made only of = and -"
    if RESERVED_FIELD.match(value):
        return "begins with verified:, not verified:, door: or blast radius:"
    if value.startswith(RESERVED_STRUCTURE):
        return "begins with Supersedes"
    return None


def house_value(value, cap=VALUE_CAP):
    return value_fault(value, cap) is None


def sentence_shaped(value):
    if not isinstance(value, str) or not value:
        return False
    first = value[0]
    return first.lower() != first and first.upper() == first and value[-1] in SENTENCE_ENDS


def receipt_token(value):
    return isinstance(value, str) and value.startswith(RECEIPT_TOKENS)


def composed_fault(value, cap=VALUE_CAP):
    if not isinstance(value, str):
        return "is not text"
    if OUTSIDE_PRINTABLE_ASCII.search(value):
        return "holds a character outside printable ASCII"
    fault = value_fault(value, cap)
    if fault is not None:
        return fault
    return next((rule for pattern, rule in COMPOSED_RULES if pattern.search(value)), None)


def composed_value(value, cap=VALUE_CAP):
    return composed_fault(value, cap) is None


def title_fault(title):
    fault = composed_fault(title, TITLE_CAP)
    if fault is not None:
        return fault
    if TITLE_PATTERN.fullmatch(title) is None:
        return "does not match the house title pattern <type>(<scope>): <subject>"
    return None


def pull_request_title(header, plan, msp):
    scope = header.get("scope")
    subject = header["subject"]
    labelled = (
        "%s (%s)" % (subject, _label(plan, msp)) if len(plan.get("msps") or []) > 1 else subject
    )
    return "%s%s: %s" % (header["type"], "(%s)" % scope if scope else "", labelled)


def first_sentence(task):
    text = " ".join(str(task or "").split())
    end = SENTENCE_END.search(text)
    return text[: end.end()] if end else text


def _stated(named, anonymous):
    return named if composed_value(named) else anonymous


def _and_list(names):
    if len(names) < 2:
        return "".join(names)
    return "%s and %s" % (", ".join(names[:-1]), names[-1])


def _what_entry(name, task):
    sentence = first_sentence(task)
    if (
        composed_value(sentence)
        and "A" <= sentence[0] <= "Z"
        and sentence[-1] in SENTENCE_ENDS
        and not receipt_token(sentence)
    ):
        return sentence
    return _stated("Builds the %s Step." % name, WHAT_FALLBACK)


def _what_changed(plan, msp):
    steps = _steps_by_name(plan)
    names = tuple(str(name) for name in plan["msps"][msp]["steps"])
    entries = tuple(_what_entry(name, steps[name].get("task")) for name in names)
    if len(entries) <= SECTION_LIMIT:
        return entries
    rest = names[SECTION_LIMIT - 1 :]
    return entries[: SECTION_LIMIT - 1] + (
        _stated(
            "Builds %d more Steps: %s." % (len(rest), _and_list(rest)),
            "Builds %d more Steps." % len(rest),
        ),
    )


def _capped(values, overflow, closing=()):
    room = SECTION_LIMIT - len(closing)
    if len(values) <= room:
        return values + closing
    return values[: room - 1] + (overflow % (len(values) - room + 1),) + closing


def _unproven(outcome):
    return "is %s" % outcome if outcome in core.GATE_OUTCOMES else "was not judged"


def _proof(gate_result, proven_head):
    gated = (gate_result or {}).get("commit") or ""
    if proven_head is None or proven_head == gated:
        return ()
    return (
        "mitosis gated commit %s; the caller proved commit %s without a new gate."
        % (gated[:12], proven_head[:12]),
    )


def _verification(plan, msp, gate_result, gate_error, proven_head=None):
    steps = _steps_by_name(plan)
    judged = _judged(gate_result)
    names = plan["msps"][msp]["steps"]
    properties = tuple(
        (
            "%s::%s (%s)" % (entry.get("file"), entry.get("test"), name),
            (judged.get((name, entry.get("file"), entry.get("test"))) or {}).get("outcome"),
        )
        for name in names
        for entry in steps[name].get("acceptance") or []
    )
    verified = tuple(
        _stated("%s %s" % (where, PROVEN), "an acceptance test %s" % PROVEN)
        for where, outcome in properties
        if outcome == "pass"
    )
    unproven = (
        tuple(
            _stated("%s %s" % (where, _unproven(outcome)), "an acceptance test %s" % _unproven(outcome))
            for where, outcome in properties
            if outcome != "pass"
        )
        + tuple(
            _stated("the %s Step has no acceptance test" % name, "a Step has no acceptance test")
            for name in names
            if not steps[name].get("acceptance")
        )
    )
    closing = (("the gate could not run",) if gate_error else ()) + _proof(gate_result, proven_head)
    return tuple(
        VERIFIED_PREFIX + value
        for value in _capped(
            verified, "%d more acceptance tests fail with the work reverted and pass with it"
        )
    ) + tuple(
        NOT_VERIFIED_PREFIX + value
        for value in _capped(unproven, "%d more acceptance checks were not proven", closing)
    )


def _plural(count, singular, plural):
    return "%d %s" % (count, singular if count == 1 else plural)


def _risk_kinds(plan, msp, findings, exception, foreign, gate_error):
    found = findings or {}
    undeclared = tuple(dict.fromkeys(found.get("undeclared") or ()))
    unwritten = tuple(dict.fromkeys(found.get("unwritten") or ()))
    crossing = tuple(dict.fromkeys(entry.get("path") for entry in found.get("crossing") or ()))
    foreign_paths = tuple(dict.fromkeys(entry.get("path") for entry in unique_foreign(foreign)))
    gaps = tuple(dict.fromkeys(gap["manifest"] for gap in manifest_gaps(plan, msp)))
    producers = len((exception or {}).get("producers") or ())
    kinds = (
        (
            bool(undeclared),
            "reconcile found %s" % _plural(len(undeclared), "undeclared write", "undeclared writes"),
            undeclared,
        ),
        (
            bool(unwritten),
            "reconcile found %s never written"
            % _plural(len(unwritten), "declared path", "declared paths"),
            unwritten,
        ),
        (
            bool(crossing),
            "another MSP also changes %s this branch writes" % _plural(len(crossing), "file", "files"),
            crossing,
        ),
        (
            bool(foreign_paths),
            "a Lane changed %s another Lane of this MSP owned and had already committed"
            % _plural(len(foreign_paths), "file", "files"),
            foreign_paths,
        ),
        (
            bool(gaps),
            "the build leaves %s exporting nothing"
            % _plural(len(gaps), "package manifest", "package manifests"),
            gaps,
        ),
        (
            exception is not None,
            "this MSP has %s, so its base is the feature branch"
            % _plural(producers, "producer", "producers"),
            (),
        ),
        (bool(gate_error), "the gate could not run", ()),
    )
    return tuple(
        (text, tuple(path for path in paths if composed_value(path, NAMEABLE_CAP)), len(paths))
        for present, text, paths in kinds
        if present
    )


def _risk_clause(text, nameable, total, limit):
    named = nameable[:limit]
    hidden = total - len(named)
    if not named:
        return text
    return "%s (%s)" % (text, _and_list(named + (("%d more" % hidden,) if hidden else ())))


def _risk_line(kinds, limit):
    sentence = "; ".join(
        _risk_clause(text, nameable, total, limit) for text, nameable, total in kinds
    )
    return sentence[0].upper() + sentence[1:] + "."


def _risk_fits(kinds, limit):
    return composed_value(_risk_line(kinds, limit))


def _largest_fitting(kinds, low, high):
    if low >= high:
        return low
    middle = (low + high + 1) // 2
    if _risk_fits(kinds, middle):
        return _largest_fitting(kinds, middle, high)
    return _largest_fitting(kinds, low, middle - 1)


def _risk_limit(kinds):
    edges = sorted({0} | {len(nameable) for _, nameable, _ in kinds})
    spans = tuple(zip(edges, tuple(edge - 1 for edge in edges[1:]) + (edges[-1],)))
    low, high = next(
        ((low, high) for low, high in reversed(spans) if _risk_fits(kinds, low)), (0, 0)
    )
    return _largest_fitting(kinds, low, high)


def _risk(plan, msp, findings, exception, foreign, gate_error):
    kinds = _risk_kinds(plan, msp, findings, exception, foreign, gate_error)
    if not kinds:
        return None
    return _risk_line(kinds, _risk_limit(kinds))


def _block(heading, lines):
    return "\n".join((heading,) + tuple(lines)) if lines else ""


def lane_name(plan, lane):
    return "Lane %d (%s)" % (lane, ", ".join((plan.get("lanes") or [])[lane]["steps"]))


def foreign_sentence(plan, entry):
    suspects = entry.get("suspects") or [entry["lane"]]
    who = " or ".join(lane_name(plan, lane) for lane in suspects)
    unsure = "; they finished together, so git cannot tell which" if len(suspects) > 1 else ""
    return "%s changed %s%s. %s owns that file and had already committed it" % (
        who,
        entry["path"],
        unsure,
        lane_name(plan, entry["owner"]),
    )


def _foreign_key(entry):
    return (entry["path"], entry["owner"], tuple(entry.get("suspects") or [entry["lane"]]))


def unique_foreign(foreign):
    keyed = tuple((_foreign_key(entry), entry) for entry in foreign)
    first = dict(reversed(keyed))
    return [first[key] for key in dict.fromkeys(key for key, _ in keyed)]


def msp_foreign_writes(plan, msp, lane_records):
    return [
        {**entry, "lane": index}
        for index, lane in enumerate(plan.get("lanes") or [])
        if lane["msp"] == msp
        for entry in (lane_records.get(str(index)) or {}).get("foreign_writes") or []
    ]


def merge_danger(header):
    door = header.get("door")
    blast_radius = header.get("blast_radius")
    if door is None or blast_radius is None:
        return ()
    return (DOOR_PREFIX + door, BLAST_RADIUS_PREFIX + blast_radius)


def pull_request_body(
    header,
    plan,
    msp,
    gate_result=None,
    gate_error=None,
    findings=None,
    exception=None,
    foreign=(),
    proven_head=None,
):
    risk = _risk(plan, msp, findings, exception, foreign, gate_error)
    blocks = (
        _block(WHAT_HEADING, tuple(BULLET + entry for entry in _what_changed(plan, msp))),
        _block(WHY_HEADING, tuple(header.get("why") or ())),
        _block(RISK_HEADING, merge_danger(header) + ((risk,) if risk else ())),
        _block(
            VERIFICATION_HEADING, _verification(plan, msp, gate_result, gate_error, proven_head)
        ),
    )
    return "\n\n".join(block for block in blocks if block)


def ship(
    plan,
    msp,
    tree,
    branch,
    base,
    pr_command,
    remote,
    log_dir,
    header=None,
    gate_result=None,
    gate_error=None,
    findings=None,
    exception=None,
    timeout=None,
    push=True,
    foreign=(),
    proven_head=None,
    env=None,
):
    label = _label(plan, msp)
    head = git(["rev-parse", "--verify", branch + "^{commit}"], tree)
    dirty = written_since(tree)
    if dirty:
        raise ShipError("%s holds writes no gate judged: %s" % (branch, ", ".join(dirty)))
    gated = (gate_result or {}).get("commit")
    if proven_head is None and gated is not None and head != gated:
        raise ShipError(
            "%s is at %s, not the gated commit %s" % (branch, head[:12], gated[:12])
        )
    held = {
        "branch": branch,
        "base": base,
        "remote": remote,
        "pushed": None,
        "pull_request": "",
        "stacking_exception": exception is not None,
    }
    if git_ok(["diff", "--quiet", "%s...%s" % (base, head)], tree):
        return {**held, "unchanged": True}
    if not push:
        return {**held, "unchanged": False}
    title = pull_request_title(header, plan, msp)
    body = pull_request_body(
        header, plan, msp, gate_result, gate_error, findings, exception, foreign, proven_head
    )
    git(["push", "-q", "-u", remote, branch], tree)
    pushed = {**held, "pushed": head, "title": title, "body": body, "unchanged": False}
    if pr_command is None:
        return pushed
    os.makedirs(log_dir, exist_ok=True)
    out = os.path.join(log_dir, "pull-request.out")
    err = os.path.join(log_dir, "pull-request.err")
    argv = build_argv(
        pr_command,
        {
            "branch": branch,
            "base": base,
            "title": title,
            "body": body,
            "worktree": tree,
            "msp": label,
            "remote": remote,
        },
    )
    code, timed_out = run_command(argv, tree, timeout, out, err, env)
    if timed_out:
        raise ShipError("the pull-request command timed out for %s" % branch)
    if code != 0:
        detail = last_line(err) or last_line(out) or ""
        raise ShipError("the pull-request command exited %d for %s: %s" % (code, branch, detail))
    return {**pushed, "pull_request": last_line(out) or ""}


def _reconcile_clean(findings):
    return isinstance(findings, dict) and not (
        findings.get("undeclared")
        or findings.get("unwritten")
        or findings.get("crossing")
        or findings.get("fatal")
    )


def succeeded(state):
    msps = state.get("msps") or {}
    lanes = state.get("lanes") or {}
    if not msps:
        return False
    return all(record.get("state") == "ok" for record in lanes.values()) and all(
        record.get("state") in core.DELIVERED_STATES
        and _reconcile_clean(record.get("reconcile"))
        for record in msps.values()
    )


def _lane_indexes(plan, msp):
    return [index for index, lane in enumerate(plan.get("lanes") or []) if lane["msp"] == msp]


def _msp_record(run_state, msp, **fields):
    current = run_state["msps"].get(str(msp)) or {}
    return {**run_state, "msps": {**run_state["msps"], str(msp): {**current, **fields}}}


def _lane_block(plan, msp, state):
    for index in _lane_indexes(plan, msp):
        lane_state = (state["lanes"].get(str(index)) or {}).get("state")
        if lane_state == LANE_PARKED:
            return {
                "state": MSP_PARKED,
                "reason": "Lane %d is %s; pass --resume after the usage limit resets"
                % (index, LANE_PARKED),
            }
        if lane_state != "ok":
            return {"state": MSP_BLOCKED, "reason": "Lane %d is %s" % (index, lane_state)}
    return None


def _producer_block(plan, msp, state, producers):
    for producer in producers:
        producer_state = (state["msps"].get(str(producer)) or {}).get("state")
        if producer_state == MSP_PARKED:
            return {
                "state": MSP_PARKED,
                "reason": "MSP %s is %s; pass --resume after the usage limit resets"
                % (_label(plan, producer), MSP_PARKED),
            }
        if producer_state not in core.DELIVERED_STATES:
            return {
                "state": MSP_BLOCKED,
                "reason": "MSP %s is %s" % (_label(plan, producer), producer_state),
                "blocked_by": [producer],
            }
    return None


def _gate_stage(plan, msp, tree, settings):
    try:
        result = gate(
            plan,
            msp,
            tree["path"],
            tree["branch"],
            settings["feature_branch"],
            settings["acceptance_command"],
            os.path.join(settings["run_dir"], "gate", "msp-%d" % msp),
            settings["timeout"],
            settings.get("env"),
        )
    except GateRecoveryError:
        raise
    except (OSError, GitError, subprocess.SubprocessError) as error:
        return {"gate": None, "gate_error": str(error)}
    return {"gate": result, "gate_error": None}


def _passing_gate(prior):
    recorded = prior.get("gate")
    if not isinstance(recorded, dict) or recorded.get("blocks") is not False:
        return None
    return recorded


def _committed_head(plan, msp, tree):
    try:
        return commit_before_gate(plan, msp, tree["path"])
    except (GitTimeout, GateRecoveryError):
        raise
    except GitError:
        return None


def _gate_head(plan, msp, tree):
    try:
        return _committed_head(plan, msp, tree), None
    except GitTimeout as error:
        return None, str(error)


def _reusable_gate(prior, head):
    recorded = _passing_gate(prior)
    if recorded is None or head is None or head != recorded.get("commit"):
        return None
    return recorded


def _proven_head(prior, head, proven):
    if proven is None or head is None or not isinstance(prior.get("gate"), dict):
        return None
    return head if head.startswith(proven) else None


def _reconcile_stage(plan, msp, tree, producers, trees, settings):
    findings = reconcile(
        plan,
        msp,
        tree["path"],
        tree["branch"],
        settings["feature_branch"],
        tuple(trees[p]["branch"] for p in producers),
    )
    return {"reconcile": findings}


def _referee_stage(plan, msp, tree, producers, trees, settings):
    reconciled = _reconcile_stage(plan, msp, tree, producers, trees, settings)
    written = reconciled["reconcile"]["referee"]
    if not written:
        return None
    try:
        revert_implementation(tree["path"], settings["feature_branch"], written)
        commit_all(
            tree["path"],
            "revert(%s): restore the referee paths a Worker wrote" % _label(plan, msp),
        )
    except GitError as error:
        return {
            **reconciled,
            "state": MSP_GATE_FAILED,
            "gate": None,
            "gate_error": "the referee restore on %s failed, so the gate did not run: %s"
            % (tree["branch"], error),
            "reason": "a Worker wrote %s, which the referee owns; mitosis could not restore them "
            "on %s, so the gate did not run and the MSP is held back"
            % (", ".join(written), tree["branch"]),
        }
    return {
        **reconciled,
        "state": MSP_REFEREE_REVERTED,
        "reason": "a Worker wrote %s, which the referee owns; mitosis restored them on %s and "
        "held the MSP back" % (", ".join(written), tree["branch"]),
    }


def _ship_stage(plan, msp, tree, base, exception, record, settings, foreign=()):
    try:
        shipped = ship(
            plan,
            msp,
            tree["path"],
            tree["branch"],
            base,
            settings["pr_command"],
            settings["remote"],
            os.path.join(settings["run_dir"], "ship", "msp-%d" % msp),
            header=settings["header"],
            gate_result=record.get("gate"),
            gate_error=record.get("gate_error"),
            findings=record.get("reconcile"),
            exception=exception,
            timeout=settings["timeout"],
            push=settings["push"],
            foreign=foreign,
            proven_head=record.get("proven_head"),
            env=settings.get("env"),
        )
    except (OSError, GitError, ShipError, subprocess.SubprocessError, ValueError) as error:
        return {"state": "ship-failed", "reason": str(error), "ship": None}
    if shipped["unchanged"]:
        return {
            "state": MSP_UNCHANGED,
            "reason": "the branch adds nothing to %s, so nothing was pushed and no pull request "
            "opened" % base,
            "ship": shipped,
        }
    if shipped["pushed"] is None:
        return {
            "state": MSP_COMMITTED,
            "reason": "committed on %s and held: this run pushes nothing and opens no pull request"
            % shipped["branch"],
            "ship": shipped,
        }
    if settings["pr_command"] is None:
        return {
            "state": MSP_PUSHED,
            "reason": "pushed %s; no pull-request command was given, so the caller opens the "
            "pull request" % shipped["branch"],
            "ship": shipped,
        }
    return {"state": "shipped", "reason": None, "ship": shipped}


def _gate_outcomes(plan, msp, result, settings):
    run_name = slug(os.path.basename(os.path.normpath(settings["run_dir"])))
    msp_name = msp_slugs(plan["msps"])[msp]
    repo_name = os.path.basename(os.path.normpath(settings["repo"]))
    return [
        outcomes.entry(
            repo_name,
            run_name,
            msp_name,
            result["commit"],
            prop,
            (prop.get("seconds") or 0) / 60,
        )
        for prop in result.get("properties") or ()
        if prop.get("file") and prop.get("test")
    ]


def _trajectory_stage(plan, msp, gated, settings):
    path = settings.get("trajectory")
    if not path or gated["gate"] is None:
        return {}
    try:
        outcomes.append(path, _gate_outcomes(plan, msp, gated["gate"], settings))
    except OSError as error:
        return {"trajectory_error": str(error)}
    return {}


def _gate_referee_writes(gated):
    return list((gated["gate"] or {}).get("referee") or [])


def _with_gate_referee(reconciled, tampered):
    if not tampered:
        return reconciled
    found = reconciled["reconcile"]
    return {
        **reconciled,
        "reconcile": {
            **found,
            "referee": sorted(set(found["referee"]) | set(tampered)),
            "fatal": True,
        },
    }


def _held_back(gated, branch):
    if gated["gate_error"] is not None:
        return {
            "state": MSP_GATE_FAILED,
            "reason": "the gate could not run, so %s was committed and not pushed: %s"
            % (branch, gated["gate_error"]),
        }
    if not (gated["gate"] or {}).get("blocks"):
        return None
    return {
        "state": MSP_GATE_FAILED,
        "reason": "the gate judged %s, so %s was committed and not pushed"
        % (gated["gate"]["outcome"], branch),
    }


def _finish_msp(plan, msp, trees, producers, state, settings):
    run_dir = settings["run_dir"]
    tree = trees[msp]
    prior = state["msps"].get(str(msp)) or {}
    state = {
        **state,
        "msps": {**state["msps"], str(msp): {}},
        "stacking_exceptions": [
            entry for entry in state["stacking_exceptions"] if entry.get("msp") != msp
        ],
    }
    block = _lane_block(plan, msp, state) or _producer_block(plan, msp, state, producers)
    if block is not None:
        return write_state(run_dir, _msp_record(state, msp, **block))
    head, commit_error = _gate_head(plan, msp, tree)
    held = (
        None
        if commit_error is not None
        else _referee_stage(plan, msp, tree, producers, trees, settings)
    )
    if held is not None:
        notify(
            settings.get("observer"),
            "reconciled",
            {"msp": msp, "findings": held["reconcile"]},
        )
        return write_state(run_dir, _msp_record(state, msp, **held))
    proven_head = _proven_head(prior, head, settings["proven_heads"].get(msp))
    reused = prior.get("gate") if proven_head is not None else _reusable_gate(prior, head)
    judged_now = commit_error is not None or (proven_head is None and reused is None)
    gate_started = now()
    gated = (
        {"gate": None, "gate_error": commit_error}
        if commit_error is not None
        else _gate_stage(plan, msp, tree, settings)
        if judged_now
        else {"gate": reused, "gate_error": None}
    )
    notify(
        settings.get("observer"),
        "gated",
        {
            "msp": msp,
            "gate": gated["gate"],
            "reused": reused is not None,
            "seconds": seconds_between(gate_started, now()),
        },
    )
    proof = {} if proven_head is None else {"proven_head": proven_head}
    logged = _trajectory_stage(plan, msp, gated, settings) if judged_now else {}
    state = write_state(run_dir, _msp_record(state, msp, **gated, **proof, **logged))
    tampered = _gate_referee_writes(gated) if judged_now else []
    reconciled = _with_gate_referee(
        _reconcile_stage(plan, msp, tree, producers, trees, settings), tampered
    )
    notify(
        settings.get("observer"),
        "reconciled",
        {"msp": msp, "findings": reconciled["reconcile"]},
    )
    state = write_state(run_dir, _msp_record(state, msp, **reconciled))
    if reconciled.get("state") == MSP_BLOCKED:
        return state
    if tampered:
        return write_state(
            run_dir,
            _msp_record(
                state,
                msp,
                state=MSP_REFEREE_REVERTED,
                reason="an acceptance run wrote %s, which the referee owns; mitosis restored %s "
                "to the gated commit and held the MSP back" % (", ".join(tampered), tree["branch"]),
            ),
        )
    held_back = _held_back(gated, tree["branch"]) if judged_now else None
    if held_back is not None:
        return write_state(run_dir, _msp_record(state, msp, **held_back))
    base, exception = pr_base(
        plan, msp, settings["feature_branch"], trees, settings["repo"], state["msps"]
    )
    if exception is not None:
        state = write_state(
            run_dir, {**state, "stacking_exceptions": state["stacking_exceptions"] + [exception]}
        )
    shipped = _ship_stage(
        plan,
        msp,
        tree,
        base,
        exception,
        state["msps"][str(msp)],
        settings,
        msp_foreign_writes(plan, msp, state["lanes"]),
    )
    return write_state(run_dir, _msp_record(state, msp, **shipped))


def _settled(state, msp, producers, finished):
    current = (state["msps"].get(str(msp)) or {}).get("state")
    if current in ("shipped", MSP_PUSHED):
        return True
    return current in finished and all(
        (state["msps"].get(str(producer)) or {}).get("state") in finished
        for producer in producers
    )


def execute(
    plan,
    repo,
    feature_branch,
    run_dir,
    worker_command,
    acceptance_command,
    pr_command,
    timeout,
    concurrency,
    models=None,
    resume=False,
    root=None,
    trees_root=None,
    remote="origin",
    prefix=BRANCH_PREFIX,
    no_push=False,
    header=None,
    proven_heads=None,
    observer=None,
    env=None,
    trajectory=None,
):
    prior = prior_state(run_dir, plan, resume)
    header = carried_header(header, prior)
    check_template("dispatch", worker_command)
    check_template("acceptance", acceptance_command)
    if not no_push and pr_command is not None:
        check_template("pull-request", pr_command)
    if not no_push and header is None:
        raise ConfigError(
            "a run allowed to push needs a pull-request header holding its type, subject and "
            "why lines, so it can title and describe each pull request"
        )
    check_models(plan, worker_command, models)
    if timeout is None:
        raise ConfigError("a per-Lane timeout is required; mitosis has no default")
    recorded = (prior or {}).get("lanes") or {}
    if any(
        (recorded.get(str(index)) or {}).get("state") != "ok"
        for index in range(len(plan.get("lanes") or []))
    ):
        check_identity(repo)
    settings = {
        "repo": repo,
        "feature_branch": feature_branch,
        "run_dir": run_dir,
        "acceptance_command": acceptance_command,
        "pr_command": pr_command,
        "header": header,
        "timeout": timeout,
        "remote": remote,
        "push": not no_push,
        "proven_heads": dict(proven_heads or {}),
        "observer": observer,
        "env": env,
        "trajectory": trajectory,
    }
    os.makedirs(run_dir, exist_ok=True)
    write_json(os.path.join(run_dir, PLAN_FILE), plan)
    state = write_state(
        run_dir,
        {
            "version": core.__version__,
            "plan_id": plan.get("plan_id"),
            "feature_branch": feature_branch,
            "started": now(),
            "drift": document_drift(plan, root or repo),
            "worktrees": [],
            "lanes": dict((prior or {}).get("lanes") or {}),
            "msps": dict((prior or {}).get("msps") or {}),
            "stacking_exceptions": list((prior or {}).get("stacking_exceptions") or []),
            "merge_danger": merge_danger_pair(header),
        },
    )
    trees = prepare_worktrees(
        plan["msps"], feature_branch, trees_root or os.path.join(run_dir, "trees"), repo, prefix
    )
    state = write_state(run_dir, {**state, "worktrees": trees})

    def on_lane(lane, record):
        nonlocal state
        state = write_state(run_dir, {**state, "lanes": {**state["lanes"], str(lane): record}})

    already = {
        int(index) for index, record in state["lanes"].items() if (record or {}).get("state") == "ok"
    }
    records = dispatch(
        plan,
        trees,
        worker_command,
        timeout,
        concurrency,
        run_dir,
        feature_branch,
        models=models,
        prior=state["lanes"],
        on_lane=on_lane,
        observer=observer,
        env=env,
    )
    notify(
        observer,
        "dispatch-ended",
        {"lanes": {str(lane): record for lane, record in records.items() if lane not in already}},
    )
    producers = msp_producers(plan)
    finished = core.DELIVERED_STATES if no_push else PUBLISHED_STATES
    for msp in msp_order(plan):
        if _settled(state, msp, producers[msp], finished):
            continue
        state = _finish_msp(plan, msp, trees, producers[msp], state, settings)
        notify(observer, "msp-finished", {"msp": msp, "record": state["msps"][str(msp)]})
    return state
