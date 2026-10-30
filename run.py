import ast
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import xml.etree.ElementTree as ElementTree
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from functools import lru_cache

import core
import isolation
import mutants
import outcomes
import referee
import shape

BRANCH_PREFIX = "mitosis"

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

NO_REFEREE_WRITE = {"referee": [], "fatal": False}

PUBLISHED_STATES = ("shipped", MSP_PUSHED, MSP_UNCHANGED)

ACCEPTANCE_FAILURE_EXIT = 1

MUTANT_CAP = 12

LINE_TRACER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linetrace")

TRACE_FOLDER_VARIABLE = "MITOSIS_LINE_TRACE"

TRACE_ROOT_VARIABLE = "MITOSIS_TRACE_ROOT"

SUITE_FAILURE_EXIT = 1

SUITE_BASELINE = "unmutated"

UNITTEST_FAILURE = re.compile(r"^(?:FAIL|ERROR): (\S+) \(([^)\s]+)\)", re.MULTILINE)

PYTEST_FAILURE = re.compile(r"^FAILED (\S+::\S+)", re.MULTILINE)

UNSAFE_XML = re.compile(r"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)

SAMPLED_CODE = "code"

SAMPLED_DATA = "data"

SAMPLED_UNLOADED = "unloaded"

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

GITLINK_MODE = "160000"

SYMLINK_MODE = "120000"

EXECUTABLE_MODE = "100755"

REGULAR_MODE = "100644"

UNPINNED_GIT_VARIABLES = frozenset(
    {
        "GIT_DIFF_OPTS",
        "GIT_EXTERNAL_DIFF",
        "GIT_DIFF_PATH_COUNTER",
        "GIT_DIFF_PATH_TOTAL",
        "GIT_LITERAL_PATHSPECS",
        "GIT_GLOB_PATHSPECS",
        "GIT_NOGLOB_PATHSPECS",
        "GIT_ICASE_PATHSPECS",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
        "GIT_REPLACE_REF_BASE",
        "GIT_ATTR_SOURCE",
    }
)

RAW_OBJECTS = {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_GRAFT_FILE": os.devnull}

PINNED_GIT = {
    **RAW_OBJECTS,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_CONFIG_PARAMETERS": "",
    "GIT_CONFIG_COUNT": "0",
}

STATUS_SETTINGS = (
    "-c",
    "status.showUntrackedFiles=all",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.untrackedCache=false",
)

HISTORY_SETTINGS = ("-c", "log.showSignature=false", "-c", "diff.relative=false")

GATE_FOLDER_PREFIX = "checkout-"

BINARY_PROBE = 8000

LINE_DIFF = (
    "diff",
    "--no-index",
    "-U0",
    "--no-ext-diff",
    "--no-textconv",
    "--no-color",
    "--text",
)

DIFF_SECTION = re.compile(
    rb"^(?:diff --git a/old/(\d+) b/new/\d+|@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@)",
    re.MULTILINE,
)

STEP_CACHE_SIZE = 8


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


class GateSetupError(RuntimeError):
    pass


class TraceError(RuntimeError):
    pass


class ConfigError(ValueError):
    pass


class OverlappingWrite(ConfigError):
    pass


class ResumeError(RuntimeError):
    pass


class ShipError(RuntimeError):
    pass


class LingeringProcess(RuntimeError):
    def __init__(self, folder, limit):
        self.folder = folder
        super().__init__(
            "the run in %s left a process mitosis could not end within %d seconds, so mitosis "
            "read nothing that run could change" % (folder, limit)
        )


def _index_lock(cwd, args=()):
    named = tuple(arg[len("--git-dir="):] for arg in args[:1] if arg.startswith("--git-dir="))
    if named:
        return os.path.join(cwd or os.curdir, named[0], "index.lock")
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


def git_environ(env=None):
    kept = {
        name: value for name, value in os.environ.items() if name not in UNPINNED_GIT_VARIABLES
    }
    return {**kept, **(env or {})}


def _git_once(args, cwd, timeout, raw=False, data=None, env=None):
    index_lock = _index_lock(cwd, args)
    locked_before = index_lock is not None and os.path.exists(index_lock)
    argv = ["git", *args]
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=git_environ(env),
        stdin=subprocess.DEVNULL if data is None else subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(input=data, timeout=timeout)
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


def git_run(args, cwd, timeout=GIT_TIMEOUT, raw=False, data=None, env=None):
    completed = _git_once(args, cwd, timeout, raw, data, env)
    for delay in LOCK_RETRY_DELAYS:
        if not _locked(completed):
            return completed
        time.sleep(delay)
        completed = _git_once(args, cwd, timeout, raw, data, env)
    return completed


def git(args, cwd, check=True, timeout=GIT_TIMEOUT, env=None):
    completed = git_run(args, cwd, timeout, env=env)
    if check and completed.returncode != 0:
        raise GitError(args, completed.returncode, completed.stderr)
    return completed.stdout.strip()


def git_paths(args, cwd, timeout=GIT_TIMEOUT, env=None):
    completed = git_run(args, cwd, timeout, raw=True, env=env)
    if completed.returncode != 0:
        raise GitError(args, completed.returncode, completed.stderr)
    return [os.fsdecode(name) for name in completed.stdout.split(b"\0") if name]


def git_ok(args, cwd, timeout=GIT_TIMEOUT, env=None):
    return git_run(args, cwd, timeout, env=env).returncode == 0


def commit_at(ref, cwd, required=False, git_dir=None):
    options, env = _object_reader(git_dir)
    quiet = () if required else ("--quiet",)
    args = [*options, "rev-parse", "--verify", *quiet, ref + "^{commit}"]
    found = git_run(args, cwd, env=env)
    if found.returncode == 0:
        return found.stdout.strip()
    if required:
        raise GitError(args, found.returncode, found.stderr)
    return None


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


def branch_commit(repo, branch, required=False):
    return commit_at("refs/heads/" + branch, repo, required=required)


def pinned_base(repo, feature_branch, prior):
    recorded = (prior or {}).get("base_commit")
    return recorded if recorded else branch_commit(repo, feature_branch, required=True)


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
    "gate-setup": ("worktree",),
    "suite": ("worktree", "report"),
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
    missing = [tier for tier in core.plan_tiers(plan) if tier not in (models or {})]
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


def run_command(argv, cwd, timeout, stdout_path, stderr_path, env=None, sandbox=None):
    launched, launch_env = isolation.confined(argv, env, sandbox)
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        proc = subprocess.Popen(
            launched,
            cwd=cwd,
            env=launch_env,
            stdout=out,
            stderr=err,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            ended = proc.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            try:
                ended = proc.wait(timeout=KILL_GRACE), True
            except subprocess.TimeoutExpired:
                ended = -signal.SIGKILL, True
    isolation.end_run(proc, sandbox, cwd)
    return ended


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


def _attached_branch(tree):
    return git(["symbolic-ref", "--short", "HEAD"], tree, env=PINNED_GIT)


def commit_paths(tree, paths, message, branch=None):
    msp_branch = branch or _attached_branch(tree)
    present = [p for p in paths if os.path.lexists(os.path.join(tree, p))]
    tracked = git_paths(["ls-files", "-z", "--", *paths], tree) if paths else []
    targets = sorted(set(present) | set(tracked))
    if targets:
        git(["add", "-A", "--", *targets], tree)
        if git(["status", "--porcelain", "--", *targets], tree):
            git(["commit", "-q", "-m", message, "--", *targets], tree)
    return branch_commit(tree, msp_branch, required=True)


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


def _land(plan, lane, tree, record, run_dir):
    write_set = plan["briefs"][lane]["write_set"]
    clone = isolation.lane_folders(run_dir, lane)["clone"]
    try:
        landing = isolation.read_lane(clone, tree["path"], record["start"], write_set)
    except (GitError, OSError) as error:
        return None, "mitosis could not land the Lane from its clone %s: %s" % (clone, error), None
    try:
        isolation.place_entries(tree["path"], landing["entries"])
        commit = commit_paths(
            tree["path"], write_set, _landing_message(plan, lane, tree), tree["branch"]
        )
    except GitError as error:
        if write_set:
            git_run(["reset", "-q", "--", *write_set], tree["path"])
        return None, "%s: %s" % (LANE_COMMIT_FAILED, error), None
    except OSError as error:
        return None, "%s: %s" % (LANE_COMMIT_FAILED, error), None
    try:
        replayed = isolation.replay(tree["path"], landing, commit)
    except (GitError, OSError) as error:
        return None, "mitosis landed %s but could not replay the Lane's other writes: %s" % (
            commit,
            error,
        ), None
    return commit, None, {
        "replayed": replayed,
        "unreplayed": landing["unreplayed"],
        "ignored": landing["ignored"],
    }


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


def _written_paths(path, *options):
    entries = git_paths(["status", "--porcelain", "-z", "--untracked-files=all", *options], path)
    return tuple(dict.fromkeys(_status_paths(entries)))


def _dirty_paths(tree):
    try:
        return _written_paths(tree["path"])
    except GitError:
        return ()


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
                tree["branch"],
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


def _landed(plan, lane, tree, record, run_dir):
    commit, reason, outside = _land(plan, lane, tree, record, run_dir)
    if reason is not None:
        return {**record, "state": "failed", "reason": reason}
    isolation.remove_lane_folders(isolation.lane_folders(run_dir, lane))
    return {**record, **outside, "commit": commit}


def _land_batch(plan, msp, tree, batch, held, records, run_dir, settle):
    lanes = plan["lanes"]
    landed = {}
    for lane in sorted(batch):
        done = _landed(plan, lane, tree, held[lane], run_dir)
        if done["state"] == "ok":
            claimed = plan["briefs"][lane]["write_set"]
            landed = {key: isolation.released(value, claimed) for key, value in landed.items()}
            for index, record in sorted(records.items()):
                kept = isolation.released(record, claimed)
                if lanes[index]["msp"] == msp and kept is not record:
                    records = settle(records, index, kept)
        landed = {**landed, lane: done}
    return landed, records


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
    sandbox=None,
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
        and isolation.clone_ready(isolation.lane_folders(run_dir, int(index)), record)
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
                landed, records = _land_batch(
                    plan, msp, tree_of[msp], batch, held, records, run_dir, settle
                )
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
                folders = isolation.lane_folders(run_dir, lane)
                argv = build_argv(
                    command,
                    _worker_values(
                        plan, lane, {**tree, "path": folders["clone"]}, models, run_dir, session
                    ),
                )
                carried = core.carried_session(argv, session)
                try:
                    start = isolation.cut_clone(tree, folders)
                    confinement = isolation.worker_confinement(folders, sandbox, env)
                except (GitError, OSError) as error:
                    records = settle(
                        records,
                        lane,
                        lane_record(
                            lane,
                            msp,
                            "failed",
                            reason="mitosis could not prepare the Lane's clone %s: %s"
                            % (folders["clone"], error),
                            merged=merged,
                        ),
                    )
                    progressed = True
                    continue
                future = pool.submit(
                    run_command, argv, folders["clone"], timeout, out, err, env, confinement
                )
                running = {
                    **running,
                    future: (lane, now(), merged, out, err, carried, folders["clone"], start),
                }
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
                    lane, started, merged, out, err, session, clone, start = running[future]
                    running = {f: v for f, v in running.items() if f is not future}
                    code, timed_out = future.result()
                    tree = tree_of[lanes[lane]["msp"]]
                    judged = {
                        **_verdict(lane, tree, started, merged, out, err, code, timed_out, session),
                        "clone": clone,
                        "start": start,
                    }
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


def steps_without_implementation_change(plan, msp, tree, base, commit):
    changed = _changed_paths(_committed_entries(tree, base), _committed_entries(tree, commit))
    unchanged = ()
    for name in plan["msps"][msp]["steps"]:
        paths = step_implementation_paths(plan, msp, name)
        if paths and not any(_under(path, paths) for path in changed):
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
    return _written_paths(tree, "--no-renames")


def commit_all(tree, message, branch=None):
    msp_branch = branch or _attached_branch(tree)
    git(["add", "-A"], tree)
    if not git_ok(["diff", "--cached", "--quiet", "--ignore-submodules=none"], tree):
        git(["commit", "-q", "-m", message], tree)
    return branch_commit(tree, msp_branch, required=True)


class LinkRefusal(OSError):
    def __init__(self, path, reason):
        self.path = path
        super().__init__("mitosis refused to act on %s: %s" % (path, reason))


def _parts(path):
    return tuple(part for part in str(path).replace(os.sep, "/").split("/") if part not in ("", "."))


def _prefixes(path):
    parts = _parts(path)
    return tuple("/".join(parts[:depth]) for depth in range(1, len(parts) + 1))


def tree_path(tree, path, last=True):
    parts = _parts(path)
    checked = parts if last else parts[:-1]
    for depth in range(1, len(checked) + 1):
        if os.path.islink(os.path.join(tree, *checked[:depth])):
            raise LinkRefusal(
                path,
                "%s is a symbolic link, so the action would reach outside the worktree"
                % "/".join(checked[:depth]),
            )
    full = os.path.join(tree, *parts)
    root = os.path.realpath(tree)
    resolved = os.path.realpath(full if last else os.path.dirname(full))
    if resolved != root and not resolved.startswith(root + os.sep):
        raise LinkRefusal(path, "it resolves to %s, outside the worktree %s" % (resolved, root))
    return full


def _behind_link(tree, path):
    try:
        tree_path(tree, path, last=False)
    except LinkRefusal:
        return True
    return False


def _cache_of(path):
    return "/".join(_parts(os.path.dirname(_norm(path))) + ("__pycache__",))


def _remove_cache(folder):
    if os.path.islink(folder):
        os.remove(folder)
    elif os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)


def clear_caches_beside(tree, paths):
    folders = tuple(
        tree_path(tree, cache, last=False) for cache in sorted({_cache_of(path) for path in paths})
    )
    for folder in folders:
        _remove_cache(folder)


def _remove_entry(tree, path):
    full = tree_path(tree, path, last=False)
    if os.path.isdir(full) and not os.path.islink(full):
        shutil.rmtree(full)
    elif os.path.lexists(full):
        os.remove(full)


def _held_under(names, path):
    return any(name == path or name.startswith(path + "/") for name in names)


def _under(path, paths):
    return any(_held_under((path,), _norm(named)) for named in paths)


def _checked_run(args, tree, data=None, env=None):
    completed = git_run(args, tree, raw=True, data=data, env=env)
    if completed.returncode != 0:
        raise GitError(args, completed.returncode, completed.stderr)
    return completed.stdout


RESTORE_MESSAGE = "revert: restore the referee paths a Worker wrote"


def _restored_targets(paths, held):
    links = tuple(path for path, (mode, _) in held.items() if mode == GITLINK_MODE)
    return tuple(
        sorted({next((link for link in links if _held_under((path,), link)), path) for path in paths})
    )


def _within(entries, target):
    return {path: entry for path, entry in entries.items() if _held_under((path,), target)}


def _restore_rows(current, held, targets):
    dropped = b"".join(
        b"0 %s\t%s\0" % (b"0" * len(oid), os.fsencode(path))
        for path, (_, oid) in sorted(current.items())
        if _under(path, targets)
    )
    return dropped + b"".join(
        b"%s %s\t%s\0" % (mode.encode("ascii"), oid.encode("ascii"), os.fsencode(path))
        for path, (mode, oid) in sorted(held.items())
        if _under(path, targets)
    )


def _restored_tree(tree, head, rows):
    folder = tempfile.mkdtemp(prefix="mitosis-restore-")
    try:
        env = {**PINNED_GIT, "GIT_INDEX_FILE": os.path.join(folder, "index")}
        _checked_run(["read-tree", head], tree, env=env)
        _checked_run(["update-index", "-z", "--replace", "--index-info"], tree, rows, env)
        return _decoded(_checked_run(["write-tree"], tree, env=env)).strip()
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _reset_submodule_heads(tree, base, held, targets):
    for path in targets:
        oid = _gitlink(held, path)
        store = _module_store(tree, base, path, None) if oid else None
        if store is not None and os.path.isdir(store):
            _checked_run(
                [
                    "--git-dir=" + store,
                    "-c",
                    "core.hooksPath=" + os.devnull,
                    "update-ref",
                    "--no-deref",
                    "HEAD",
                    oid,
                ],
                tree,
                env=RAW_OBJECTS,
            )


def _lstat_kind(tree, path):
    try:
        return _kind(os.lstat(os.path.join(tree, *_parts(path))).st_mode)
    except FileNotFoundError:
        return None


def _entry_kind(tree, path):
    if _behind_link(tree, path):
        return "linked"
    return _lstat_kind(tree, path)


def _walk_parents(tree, path, clear, kind_of):
    for prefix in _prefixes(path)[:-1]:
        full = os.path.join(tree, *_parts(prefix))
        kind = kind_of(tree, prefix)
        if kind not in (None, "dir"):
            if not clear(full):
                return False
            kind = None
        if kind is None:
            os.mkdir(full)
    return True


def _open_parents(tree, path):
    return _walk_parents(tree, path, lambda full: False, _entry_kind)


def _write_blob(full, mode, data):
    if mode == SYMLINK_MODE:
        os.symlink(os.fsdecode(data), full)
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(full, flags, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        os.fchmod(handle.fileno(), 0o755 if mode == EXECUTABLE_MODE else 0o644)
        handle.write(data)


def _write_entry(tree, path, mode, data):
    kind = _entry_kind(tree, path)
    if kind not in (None, "file", "link") or not _open_parents(tree, path):
        return False
    full = tree_path(tree, path, last=False)
    if kind is not None:
        os.remove(full)
    _write_blob(full, mode, data)
    return True


def _drop_entry(tree, path):
    if _entry_kind(tree, path) not in ("file", "link"):
        return False
    os.remove(tree_path(tree, path, last=False))
    return True


def _sync_worktree(tree, commit, base, paths):
    restored = _expanded(tree, commit, base)
    wanted = {
        path: restored[path]
        for path in paths
        if path in restored and restored[path][0] != GITLINK_MODE
    }
    blobs = _store_blobs(tree, wanted)
    placed = ()
    for path in paths:
        if path in wanted:
            mode, oid, store = wanted[path]
            synced = _write_entry(tree, path, mode, blobs[(store, oid)])
        else:
            synced = path not in restored and _drop_entry(tree, path)
        placed = placed + ((path,) if synced else ())
    return placed


def revert_implementation(tree, branch, head, base, paths, message=RESTORE_MESSAGE):
    written = tuple(sorted({_norm(path) for path in paths}))
    ref = "refs/heads/" + branch
    moved_from = branch_commit(tree, branch, required=True)
    held = _committed_entries(tree, base)
    current = _committed_entries(tree, head)
    targets = _restored_targets(written, held)
    rows = _restore_rows(current, held, targets)
    restored = _restored_tree(tree, head, rows)
    commit = git(["commit-tree", restored, "-p", head, "-m", message], tree, env=RAW_OBJECTS)
    git(["update-ref", "-m", message, ref, commit, moved_from], tree, env=RAW_OBJECTS)
    _checked_run(["update-index", "-z", "--replace", "--index-info"], tree, rows, PINNED_GIT)
    _reset_submodule_heads(tree, base, held, targets)
    placed = _sync_worktree(tree, commit, base, written)
    clear_caches_beside(tree, placed)
    after = _committed_entries(tree, branch_commit(tree, branch, required=True))
    left = tuple(target for target in targets if _within(after, target) != _within(held, target))
    if left:
        raise GitError(
            ["update-ref", ref, commit],
            1,
            "the referee restore left %s different from %s" % (", ".join(left), base),
        )
    return list(targets)


def run_acceptance(command, tree, entry, log_path, timeout, env=None, sandbox=None):
    argv = build_argv(command, {"file": entry["file"], "test": entry["test"], "worktree": tree})
    return run_command(argv, tree, timeout, log_path, log_path + ".err", env, sandbox)


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


def commit_before_gate(plan, msp, tree, branch=None):
    return commit_all(
        tree, "chore(%s): commit the MSP's work before the gate" % _label(plan, msp), branch
    )


def _open_regular(tree, path, flags):
    descriptor = os.open(tree_path(tree, path), flags | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        regular = stat.S_ISREG(os.fstat(descriptor).st_mode)
    except OSError:
        os.close(descriptor)
        raise
    if not regular:
        os.close(descriptor)
        raise LinkRefusal(path, "it is not a regular file, so the gate cannot mutate it")
    return descriptor


def _read_text(tree, path):
    descriptor = _open_regular(tree, path, os.O_RDONLY)
    with os.fdopen(descriptor, encoding="utf-8", errors="surrogateescape", newline="") as handle:
        return handle.read()


def _write_text(tree, path, text):
    descriptor = _open_regular(tree, path, os.O_WRONLY)
    with os.fdopen(descriptor, "w", encoding="utf-8", errors="surrogateescape", newline="") as handle:
        os.ftruncate(descriptor, 0)
        handle.write(text)


LINK_REASON = "the Step changed %s, a symbolic link the gate cannot mutate without writing through it"


def linked_paths(tree, commit, paths):
    wanted = sorted({prefix for path in paths for prefix in _prefixes(path)})
    if not wanted:
        return ()
    entries = git_paths(
        ["ls-tree", "-z", "--full-tree", commit, "--", *(":(literal)" + path for path in wanted)],
        tree,
        env=PINNED_GIT,
    )
    return tuple(
        sorted(entry.split("\t", 1)[1] for entry in entries if entry.startswith("120000 "))
    )


def _changed_paths(before, after):
    return tuple(
        sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    )


def _changes(tree, base, commit, paths):
    before = _committed_entries(tree, base)
    after = _committed_entries(tree, commit)
    names = tuple(path for path in _changed_paths(before, after) if _under(path, paths))
    return names, before, after


def _step_changes(plan, msp, tree, base, commit, entry):
    paths = tuple(step_implementation_paths(plan, msp, entry["step"]))
    if not paths:
        return (), (), {}, {}
    names, before, after = _changes(tree, base, commit, paths)
    return paths, names, before, after


def step_links(plan, msp, tree, base, commit, entry):
    paths, names, _, _ = _step_changes(plan, msp, tree, base, commit, entry)
    return linked_paths(tree, commit, paths + names)


def _binary(data):
    return b"\0" in data[:BINARY_PROBE]


def _line_file(folder, name, data):
    with open(os.path.join(folder, name), "wb") as handle:
        handle.write(data + b"\n")


def _added_lines_of_each(pairs):
    if not pairs:
        return ()
    with tempfile.TemporaryDirectory(prefix="mitosis-diff-") as folder:
        for side in ("old", "new"):
            os.mkdir(os.path.join(folder, side))
        for number, (old, new) in enumerate(pairs):
            _line_file(os.path.join(folder, "old"), str(number), old)
            _line_file(os.path.join(folder, "new"), str(number), new)
        args = [*LINE_DIFF, "old", "new"]
        env = {**PINNED_GIT, "GIT_CEILING_DIRECTORIES": os.path.dirname(folder)}
        completed = git_run(args, folder, raw=True, env=env)
    if completed.returncode not in (0, 1):
        raise GitError(args, completed.returncode, completed.stderr)
    per_file = [[] for _ in pairs]
    current = None
    for section in DIFF_SECTION.finditer(completed.stdout):
        if section.group(1) is not None:
            current = int(section.group(1))
            continue
        if current is None:
            raise GitError(args, completed.returncode, "a hunk header arrived before any file header")
        start = int(section.group(2))
        count = int(section.group(3) or b"1")
        per_file[current].extend(range(start, start + count))
    return tuple(tuple(numbers) for numbers in per_file)


def _added_lines(old, new):
    return list(_added_lines_of_each(((old, new),))[0])


def _text_blobs(tree, entries, names):
    regular = {
        name: entries[name][1]
        for name in names
        if entries.get(name, ("",))[0] in (REGULAR_MODE, EXECUTABLE_MODE)
    }
    blobs = _blobs(tree, regular.values())
    return {name: blobs[oid] for name, oid in regular.items()}


@lru_cache(maxsize=STEP_CACHE_SIZE)
def _step_added(tree, base, commit, paths):
    names, before, after = _changes(tree, base, commit, paths)
    old = _text_blobs(tree, before, names)
    new = _text_blobs(tree, after, names)
    measured = tuple(
        name
        for name in sorted(new)
        if not _binary(new[name]) and not _binary(old.get(name, b""))
    )
    lines = _added_lines_of_each(tuple((old.get(name, b""), new[name]) for name in measured))
    added = tuple((name, found) for name, found in zip(measured, lines) if found)
    return added, tuple((name, new[name]) for name, _ in added)


def traced_env(env, trace_folder, root):
    base = dict(os.environ if env is None else env)
    inherited = [base["PYTHONPATH"]] if base.get("PYTHONPATH") else []
    return {
        **base,
        TRACE_FOLDER_VARIABLE: trace_folder,
        TRACE_ROOT_VARIABLE: root,
        "PYTHONPATH": os.pathsep.join([LINE_TRACER] + inherited),
    }


def fresh_folder(path):
    if os.path.lexists(path):
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    os.makedirs(path)
    return path


def _trace_files(folder):
    with os.scandir(folder) as listing:
        return tuple(
            sorted(
                entry.path
                for entry in listing
                if entry.name.endswith(".json") and entry.is_file(follow_symlinks=False)
            )
        )


def _valid_trace_record(record):
    return (
        isinstance(record, dict)
        and isinstance(record.get("function"), bool)
        and isinstance(record.get("lines"), list)
        and all(isinstance(line, int) and not isinstance(line, bool) for line in record["lines"])
        and isinstance(record.get("names"), list)
        and all(isinstance(name, str) for name in record["names"])
    )


def _trace_records(path):
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle)
    except ValueError as error:
        raise TraceError("the line trace %s is not JSON: %s" % (os.path.basename(path), error))
    valid = isinstance(loaded, dict) and all(
        isinstance(name, str) and isinstance(records, list) and all(map(_valid_trace_record, records))
        for name, records in loaded.items()
    )
    if not valid:
        raise TraceError("the line trace %s does not hold line records" % os.path.basename(path))
    return loaded


def read_trace(folder):
    files = _trace_files(folder)
    if not files:
        return None
    loaded = frozenset()
    function_lines = {}
    names = frozenset()
    for path in files:
        for name, records in sorted(_trace_records(path).items()):
            loaded = loaded | {name}
            executed = frozenset(
                line for record in records if record["function"] for line in record["lines"]
            )
            function_lines = {**function_lines, name: function_lines.get(name, frozenset()) | executed}
            names = names | frozenset(
                read for record in records if record["function"] for read in record["names"]
            )
    return {"loaded": loaded, "function_lines": function_lines, "names": names}


ASSIGNMENTS = (ast.Assign, ast.AnnAssign, ast.AugAssign)

FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)

NESTED_BODIES = ("body", "orelse", "finalbody")


def _target_names(target):
    if isinstance(target, ast.Name):
        return (target.id,)
    if isinstance(target, ast.Attribute):
        return (target.attr,)
    if isinstance(target, (ast.Subscript, ast.Starred)):
        return _target_names(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        return tuple(name for element in target.elts for name in _target_names(element))
    return ()


def _assigned_names(statement):
    targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
    return frozenset(name for target in targets for name in _target_names(target))


def _scope_assignments(body):
    found = ()
    for statement in body:
        if isinstance(statement, FUNCTIONS):
            continue
        if isinstance(statement, ASSIGNMENTS):
            found = found + (
                (statement.lineno, statement.end_lineno or statement.lineno, _assigned_names(statement)),
            )
            continue
        nested = [
            child
            for field in NESTED_BODIES
            for child in getattr(statement, field, None) or ()
            if isinstance(child, ast.stmt)
        ] + [
            child
            for handler in getattr(statement, "handlers", None) or ()
            for child in handler.body
        ]
        found = found + _scope_assignments(nested)
    return found


def _read_assignment_lines(text, added, names):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return frozenset(added)
    return frozenset(
        line
        for start, end, assigned in _scope_assignments(tree.body)
        if assigned & names
        for line in added
        if start <= line <= end
    )


def _candidate_lines(path, added, read, trace):
    executed = trace["function_lines"].get(path, frozenset())
    in_functions = frozenset(line for line in added if line in executed)
    return tuple(sorted(in_functions | _read_assignment_lines(read(path), added, trace["names"])))


def _is_python(path):
    return os.path.splitext(path)[1].lower() == ".py"


def _sampled(found):
    return tuple(mutants.select(found, MUTANT_CAP))


def property_sample(plan, msp, tree, base, commit, entry, trace=None):
    paths = tuple(step_implementation_paths(plan, msp, entry["step"]))
    if not paths:
        return (), SAMPLED_CODE, False
    pairs, texts = _step_added(tree, base, commit, paths)
    added = dict(pairs)
    held = dict(texts)

    def read(path):
        return _decoded(held[path])

    code = {path: lines for path, lines in added.items() if mutants.is_mutable(path)}
    if not code:
        return _sampled(mutants.fallback(added, read)), SAMPLED_DATA, False
    every_line = _sampled(mutants.compute(added, read) or mutants.fallback(added, read))
    python = tuple(sorted(path for path in code if _is_python(path)))
    if trace is None or not python:
        return every_line, SAMPLED_CODE, False
    others = {path: lines for path, lines in code.items() if path not in python}
    if not any(path in trace["loaded"] for path in python):
        if not others:
            return (), SAMPLED_UNLOADED, True
        found = mutants.compute(others, read) or mutants.fallback(others, read)
        return _sampled(found), SAMPLED_CODE, False
    candidates = {path: _candidate_lines(path, code[path], read, trace) for path in python}
    executed = {path: lines for path, lines in candidates.items() if lines}
    if not executed:
        return every_line, SAMPLED_CODE, False
    found = mutants.compute({**executed, **others}, read) or mutants.fallback(added, read)
    return _sampled(found), SAMPLED_CODE, True


def property_mutants(plan, msp, tree, base, commit, entry, trace=None):
    return property_sample(plan, msp, tree, base, commit, entry, trace)[0]


def _run_mutant(command, folder, entry, mutant, log_path, timeout, env, reset, sandbox=None):
    written = reset()
    path = mutant["file"]
    mutated = mutants.apply(_read_text(folder, path), mutant)
    if mutated is None:
        return None, written
    clear_caches_beside(folder, (path,))
    _write_text(folder, path, mutated)
    return run_acceptance(command, folder, entry, log_path, timeout, env, sandbox), written


def _mutant_place(mutant):
    return {"file": mutant["file"], "line": mutant["line"], "op": mutant["op"]}


def _mutant_key(mutant):
    return (mutant["file"], mutant["line"], mutant["op"])


def _timed(call):
    started = time.monotonic()
    result = call()
    return result, time.monotonic() - started


def _read_log(path):
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _junit_failures(report):
    if not os.path.isfile(report):
        return None
    text = _read_log(report)
    if not text.strip() or UNSAFE_XML.search(text):
        return None
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return None
    return tuple(
        ".".join(part for part in (case.get("classname"), case.get("name")) if part)
        for case in root.iter("testcase")
        if any(child.tag in ("failure", "error") for child in case)
    )


def _unittest_name(name, dotted):
    return dotted if dotted.endswith("." + name) else "%s.%s" % (dotted, name)


def _logged_failures(text):
    return tuple(
        _unittest_name(match.group(1), match.group(2)) for match in UNITTEST_FAILURE.finditer(text)
    ) + tuple(match.group(1) for match in PYTEST_FAILURE.finditer(text))


def suite_killers(report, log_path):
    from_report = _junit_failures(report)
    if from_report is not None:
        return tuple(sorted(set(from_report)))
    text = _read_log(log_path) + "\n" + _read_log(log_path + ".err")
    return tuple(sorted(set(_logged_failures(text))))


def _run_suite_mutant(suite, folder, mutant, report, log_path, timeout, env, reset, sandbox=None):
    written = reset()
    path = mutant["file"]
    mutated = mutants.apply(_read_text(folder, path), mutant)
    if mutated is None:
        return None, written
    clear_caches_beside(folder, (path,))
    _write_text(folder, path, mutated)
    _removed(report)
    argv = build_argv(suite, {"worktree": folder, "report": report})
    return run_command(argv, folder, timeout, log_path, log_path + ".err", env, sandbox), written


def _run_suite_unmutated(suite, folder, log_dir, timeout, env, reset, sandbox=None):
    written = reset()
    report = os.path.join(isolation.gate_outputs(log_dir, sandbox), "suite-unmutated.xml")
    log_path = os.path.join(log_dir, "suite-unmutated.log")
    _removed(report)
    argv = build_argv(suite, {"worktree": folder, "report": report})
    return run_command(argv, folder, timeout, log_path, log_path + ".err", env, sandbox), written


def suite_baseline_fault(seen):
    ran = seen.get(SUITE_BASELINE)
    if ran is None or ran == (0, False):
        return None
    if ran[1]:
        return "the suite times out before any mutant is applied"
    return "the suite fails before any mutant is applied (exit %d)" % ran[0]


def _suite_verdicts(
    suite, survivors, index, log_dir, timeout, env, folder, reset, seen, sandbox=None
):
    written = ()
    seconds = 0
    verdicts = ()
    if SUITE_BASELINE not in seen:
        (ran, written), seconds = _timed(
            lambda: _run_suite_unmutated(suite, folder, log_dir, timeout, env, reset, sandbox)
        )
        seen = {**seen, SUITE_BASELINE: ran}
    if suite_baseline_fault(seen) is not None:
        return ((survivors[0], (False, ())),), written, seconds, seen
    for position, mutant in enumerate(survivors):
        key = _mutant_key(mutant)
        if key not in seen:
            report = os.path.join(
                isolation.gate_outputs(log_dir, sandbox), "%d-suite-%d.xml" % (index, position)
            )
            log_path = os.path.join(log_dir, "%d-suite-%d.log" % (index, position))
            (ran, left), spent = _timed(
                lambda mutant=mutant, report=report, log_path=log_path: _run_suite_mutant(
                    suite, folder, mutant, report, log_path, timeout, env, reset, sandbox
                )
            )
            seconds = seconds + spent
            written = written + left
            killed = ran is not None and not ran[1] and ran[0] == SUITE_FAILURE_EXIT
            seen = {**seen, key: (killed, suite_killers(report, log_path) if killed else ())}
        verdicts = verdicts + ((mutant, seen[key]),)
        if not seen[key][0]:
            break
    return verdicts, written, seconds, seen


def _weak(entry, mutant, record, coverage, seconds, fault=None):
    return {
        **entry,
        "seconds": seconds,
        "outcome": "weak",
        "reason": "no mutant of the work fails it: it still passes when %s:%d changes by %s%s"
        % (mutant["file"], mutant["line"], mutant["op"], "" if fault is None else ", and " + fault),
        "mutants": record,
        "mutation": "weak",
        "coverage": coverage,
    }


def mutant_verdict(
    plan,
    msp,
    tree,
    base,
    commit,
    command,
    index,
    entry,
    log_dir,
    timeout,
    env,
    folder,
    reset,
    trace=None,
    suite=None,
    seen=None,
    sandbox=None,
):
    tried = ()
    written = ()
    seen = dict(seen or {})
    seconds = entry.get("seconds", 0)
    coverage = "untraced" if trace is None else "traced"
    linked = step_links(plan, msp, tree, base, commit, entry)
    if linked:
        return (
            {
                **entry,
                "outcome": "inconclusive",
                "reason": LINK_REASON % linked[0],
                "mutants": {"tried": 0, "killed": None},
                "coverage": coverage,
            },
            written,
            seen,
        )
    sampled, kind, traced = property_sample(plan, msp, tree, base, commit, entry, trace)
    coverage = "traced" if traced else "untraced"
    if kind == SAMPLED_UNLOADED:
        return (
            {
                **entry,
                "mutants": {"tried": 0, "killed": None},
                "mutation": "not-applicable",
                "coverage": coverage,
            },
            written,
            seen,
        )
    for mutant in sampled:
        (ran, left), spent = _timed(
            lambda mutant=mutant: _run_mutant(
                command,
                folder,
                entry,
                mutant,
                os.path.join(log_dir, "%d-mutant-%d.log" % (index, len(tried))),
                timeout,
                env,
                reset,
                sandbox,
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
                    "mutation": "killed",
                    "coverage": coverage,
                    "seconds": seconds,
                },
                written,
                seen,
            )
    record = {"tried": len(tried), "killed": None}
    if not tried:
        return {**entry, "mutants": record, "coverage": coverage, "seconds": seconds}, written, seen
    if kind == SAMPLED_DATA:
        return (
            {
                **entry,
                "mutants": record,
                "mutation": "not-applicable",
                "coverage": coverage,
                "seconds": seconds,
            },
            written,
            seen,
        )
    if suite is None:
        return _weak(entry, tried[0], record, coverage, seconds), written, seen
    verdicts, left, spent, seen = _suite_verdicts(
        suite, tried, index, log_dir, timeout, env, folder, reset, seen, sandbox
    )
    written = written + left
    seconds = seconds + spent
    missed = [mutant for mutant, (killed, _) in verdicts if not killed]
    if missed:
        fault = suite_baseline_fault(seen)
        return _weak(entry, missed[0], record, coverage, seconds, fault), written, seen
    return (
        {
            **entry,
            "mutants": record,
            "mutation": "covered-elsewhere",
            "killers": sorted({name for _, (_, names) in verdicts for name in names}),
            "coverage": coverage,
            "seconds": seconds,
        },
        written,
        seen,
    )


def _remove_gate_folders(log_dir):
    with os.scandir(log_dir) as listing:
        left = sorted(
            entry.path
            for entry in listing
            if entry.name.startswith(GATE_FOLDER_PREFIX) and entry.is_dir(follow_symlinks=False)
        )
    for path in left:
        shutil.rmtree(path, ignore_errors=True)


GATE_CLASSES = ("not-applicable", "covered-elsewhere", "removed")

CLASS_REASONS = {
    "not-applicable": "not applicable to the gate: %s",
    "covered-elsewhere": "covered elsewhere: %s",
    "removed": "removed from the gate: %s",
}


def property_key(file, test):
    return "%s::%s" % (file, test)


def class_verdict(entry, gate_class):
    name = gate_class["class"]
    if name not in CLASS_REASONS:
        raise ConfigError(
            "gate class %r is none of %s" % (name, ", ".join(GATE_CLASSES))
        )
    judged = {**entry, "reason": CLASS_REASONS[name] % gate_class["reason"], "class": name, "seconds": 0}
    if name == "covered-elsewhere":
        return {**judged, "outcome": "pass", "mutation": "covered-elsewhere"}
    return {**judged, "outcome": "not-applicable"}


def _classed(properties, classes):
    by_test = {gate_class["test"]: gate_class for gate_class in classes or ()}
    return tuple(
        class_verdict(entry, by_test[property_key(entry["file"], entry["test"])])
        if entry["file"] is not None and property_key(entry["file"], entry["test"]) in by_test
        else entry
        for entry in properties
    )


def _verdict_key(entry):
    return (entry.get("step"), entry.get("file"), entry.get("test"))


def reusable_verdicts(recorded_gate):
    if not isinstance(recorded_gate, dict) or recorded_gate.get("referee"):
        return {}
    return {
        _verdict_key(entry): entry
        for entry in recorded_gate.get("properties") or ()
        if isinstance(entry, dict) and "with_work" in entry and "class" not in entry
    }


def _reused(properties, verdicts):
    return tuple(
        {**entry, **verdicts[_verdict_key(entry)]}
        if entry["outcome"] is None and _verdict_key(entry) in verdicts
        else entry
        for entry in properties
    )


def gate(
    plan,
    msp,
    tree,
    base,
    command,
    log_dir,
    timeout=None,
    env=None,
    repo=None,
    setup=None,
    head=None,
    replayed=None,
    suite=None,
    classes=None,
    recorded_gate=None,
    sandbox=None,
):
    commit = commit_before_gate(plan, msp, tree) if head is None else head
    properties = _reused(
        _classed(
            msp_properties(
                plan, msp, steps_without_implementation_change(plan, msp, tree, base, commit)
            ),
            classes,
        ),
        reusable_verdicts(recorded_gate),
    )
    implementation = implementation_paths(plan, msp)
    if not any(entry["outcome"] is None for entry in properties):
        reverted = implementation if any("with_work" in entry for entry in properties) else ()
        return {**_gate_result(properties, implementation, commit, reverted), "referee": []}
    os.makedirs(log_dir, exist_ok=True)
    _remove_gate_folders(log_dir)
    folder = tempfile.mkdtemp(prefix=GATE_FOLDER_PREFIX, dir=log_dir)
    try:
        confinement = isolation.gate_confinement(sandbox, log_dir, folder)
        result, written = _gate_runs(
            plan,
            msp,
            tree,
            base,
            command,
            log_dir,
            timeout,
            env,
            commit,
            properties,
            folder,
            repo,
            setup,
            replayed or {},
            suite,
            confinement,
        )
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        isolation.release_gate(isolation.gate_folders(log_dir))
    referee_written = sorted(set(written))
    return {
        **result,
        "referee": referee_written,
        "blocks": result["blocks"] or bool(referee_written),
    }


def _setup_unrun(properties, written):
    reason = (
        "the --gate-setup command wrote %s, which the referee owns, so no acceptance run was judged"
        % ", ".join(written)
    )
    return tuple(
        {**entry, "reason": reason} if entry["outcome"] is None else entry for entry in properties
    )


def _gate_runs(
    plan,
    msp,
    tree,
    base,
    command,
    log_dir,
    timeout,
    env,
    commit,
    properties,
    folder,
    repo,
    setup,
    replayed,
    suite=None,
    sandbox=None,
):
    locked = tuple(plan.get("locked") or ())
    implementation = implementation_paths(plan, msp)
    runnable = [index for index, entry in enumerate(properties) if entry["outcome"] is None]
    with_work_tree = isolation.held_tree(_expanded(tree, commit, base), replayed)
    without_work_tree = _without_work(with_work_tree, _expanded(tree, base, base), implementation)
    reset_gate_folder(tree, folder, with_work_tree, (), locked)
    top = frozenset(_parts(path)[0] for path in with_work_tree)
    kept = (
        _link_dependencies(repo, folder, top)
        if setup is None
        else _gate_setup(setup, folder, top, log_dir, timeout, env, sandbox)
    )
    setup_written = (
        ()
        if setup is None
        else tuple(name for name in kept if referee.top_level_reason(name, locked) is not None)
    )
    if setup_written:
        unrun = _setup_unrun(properties, setup_written)
        return _gate_result(unrun, implementation, commit, ()), setup_written

    def reset(target):
        return reset_gate_folder(tree, folder, target, kept, locked)

    def accepted(index, target, name, run_env):
        written = reset(target)
        return written, _timed(
            lambda: run_acceptance(
                command,
                folder,
                properties[index],
                os.path.join(log_dir, "%d-%s.log" % (index, name)),
                timeout,
                run_env,
                sandbox,
            )
        )

    outputs = isolation.gate_outputs(log_dir, sandbox)
    trace_folders = {
        index: fresh_folder(os.path.join(outputs, "%d-trace" % index)) for index in runnable
    }
    with_work_runs = {
        index: accepted(
            index, with_work_tree, "with-work", traced_env(env, trace_folders[index], folder)
        )
        for index in runnable
    }
    traces = {index: read_trace(trace_folders[index]) for index in runnable}
    with_work = {index: ran for index, (_, (ran, _)) in with_work_runs.items()}
    without_runs = {
        index: accepted(index, without_work_tree, "reverted", env)
        for index in runnable
        if with_work[index] == (0, False)
    }
    without = {index: ran for index, (_, (ran, _)) in without_runs.items()}
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
                "seconds": with_work_runs[index][1][1]
                + (without_runs[index][1][1] if index in without_runs else 0),
            },
        )
    verdicts = ()
    seen = {}
    for index, entry in enumerate(judged):
        if index not in with_work or entry["outcome"] != "pass":
            verdicts = verdicts + ((entry, ()),)
            continue
        toothed, left, seen = mutant_verdict(
            plan,
            msp,
            tree,
            base,
            commit,
            command,
            index,
            entry,
            log_dir,
            timeout,
            env,
            folder,
            lambda: reset(with_work_tree),
            traces[index],
            suite,
            seen,
            sandbox,
        )
        verdicts = verdicts + ((toothed, left),)
    toothed = tuple(entry for entry, _ in verdicts)
    written = (
        tuple(path for left, _ in with_work_runs.values() for path in left)
        + tuple(path for left, _ in without_runs.values() for path in left)
        + tuple(path for _, left in verdicts for path in left)
        + reset(with_work_tree)
    )
    return _gate_result(toothed, implementation, commit, implementation), written


def changed_files(tree, head, base, exclude=()):
    excluded = ["^" + commit for commit in exclude]
    names = git_paths(
        [
            *HISTORY_SETTINGS,
            "log",
            "-z",
            "--format=",
            "--name-only",
            "--no-renames",
            "--ignore-submodules=none",
            head,
            "^" + base,
            *excluded,
        ],
        tree,
        env=PINNED_GIT,
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


def locked_record(repo, branch, commit, locked):
    files = ()
    for path in locked:
        shown = git_run(
            ["cat-file", "blob", "%s:%s" % (commit, path)], repo, raw=True, env=PINNED_GIT
        )
        if shown.returncode != 0:
            raise ConfigError(
                "--charter locks %s, which %s does not hold; commit the locked checks on it "
                "before the build" % (path, branch)
            )
        files = files + ((path, shown.stdout),)
    return {
        "locked": list(locked),
        "receipt_lock": referee.receipt_lock(files),
        "commit": commit,
    }


def _object_id(data, oid):
    digest = hashlib.new("sha1" if len(oid) == 40 else "sha256")
    digest.update(b"blob %d\0" % len(data))
    digest.update(data)
    return digest.hexdigest()


def _object_reader(git_dir):
    return (() if git_dir is None else ("--git-dir=" + git_dir,)), PINNED_GIT


def _committed_entries(tree, commit, git_dir=None):
    options, env = _object_reader(git_dir)
    output = _checked_run([*options, "ls-tree", "-r", "-z", "--full-tree", commit], tree, env=env)
    rows = tuple(os.fsdecode(entry).split("\t", 1) for entry in output.split(b"\0") if entry)
    return {path: tuple(header.split(" ")[0::2]) for header, path in rows}


def _batch_records(output):
    records = []
    offset = 0
    while offset < len(output):
        end = output.index(b"\n", offset)
        header = output[offset:end].split(b" ")
        if len(header) != 3:
            raise GitError(["cat-file", "--batch"], 1, "git holds no object %s" % _decoded(header[0]))
        size = int(header[2])
        records.append((header[0].decode("ascii"), output[end + 1 : end + 1 + size]))
        offset = end + 2 + size
    return records


def _blobs(tree, oids, git_dir=None):
    wanted = tuple(dict.fromkeys(oids))
    if not wanted:
        return {}
    options, env = _object_reader(git_dir)
    requests = "".join(oid + "\n" for oid in wanted).encode("ascii")
    output = _checked_run([*options, "cat-file", "--batch"], tree, requests, env)
    return dict(_batch_records(output))


def _store_blobs(tree, entries):
    stores = sorted({store for _, _, store in entries.values()}, key=lambda store: store or "")
    return {
        (store, oid): data
        for store in stores
        for oid, data in _blobs(
            tree, [oid for _, oid, held in entries.values() if held == store], store
        ).items()
    }


def _kind(mode):
    if stat.S_ISLNK(mode):
        return "link"
    if stat.S_ISDIR(mode):
        return "dir"
    return "file" if stat.S_ISREG(mode) else "other"


def _read_bytes(tree, path):
    try:
        descriptor = os.open(tree_path(tree, path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read()


def _expected_kind(mode):
    return "link" if mode == SYMLINK_MODE else "file"


def _clear_obstruction(full):
    if os.path.islink(full) or (os.path.lexists(full) and not os.path.isdir(full)):
        os.remove(full)
    return True


def _make_parents(tree, path):
    _walk_parents(tree, path, _clear_obstruction, _lstat_kind)


def _place(tree, path, mode, data):
    _make_parents(tree, path)
    _remove_entry(tree, path)
    _write_blob(tree_path(tree, path, last=False), mode, data)


def _safe_module_name(name):
    parts = re.split(r"[/\\]", name)
    return bool(name) and not os.path.isabs(name) and parts[0] != "" and ".." not in parts


def _git_path(tree, options, name):
    found = os.fsdecode(
        _checked_run([*options, "rev-parse", "--git-path", name], tree, env=PINNED_GIT).strip()
    )
    return found if os.path.isabs(found) else os.path.join(tree, found)


def _module_dir(tree, holder, path, git_dir=None):
    options, env = _object_reader(git_dir)
    args = [*options, "config", "--blob", holder + ":.gitmodules", "-z", "--get-regexp"]
    rows = _checked_run([*args, r"^submodule\..*\.path$"], tree, env=env).split(b"\0")
    names = tuple(
        key[len("submodule.") : -len(".path")]
        for key, _, value in (os.fsdecode(row).partition("\n") for row in rows if row)
        if "/".join(_parts(value)) == path
    )
    if len(names) != 1:
        raise GitError(args, 1, "%s:.gitmodules names no single submodule at %s" % (holder, path))
    if not _safe_module_name(names[0]):
        raise GitError(
            args, 1, "%s:.gitmodules names %s with the unsafe name %r" % (holder, path, names[0])
        )
    found = _git_path(tree, options, "modules/" + names[0])
    modules = os.path.realpath(_git_path(tree, options, "modules"))
    if not os.path.realpath(found).startswith(modules + os.sep):
        raise GitError(args, 1, "the git folder of %s resolves outside %s" % (path, modules))
    return found


def _module_store(tree, holder, path, git_dir):
    try:
        return _module_dir(tree, holder, path, git_dir)
    except (GitError, OSError, ValueError):
        return None


def _gitlink(entries, path):
    entry = entries.get(path)
    return entry[1] if entry is not None and entry[0] == GITLINK_MODE else None


def _held_entries(tree, holder, git_dir):
    try:
        return _committed_entries(tree, holder, git_dir)
    except GitError:
        return {}


def _submodule_files(tree, path, oid, holder, held, git_dir):
    base_link = _gitlink(held, path)
    store = _module_store(tree, holder, path, git_dir) if base_link else None
    if store is None or not os.path.isdir(store):
        return {}
    try:
        return _expanded(tree, oid, base_link, store)
    except GitError:
        return {}


def _expanded(tree, commit, holder, git_dir=None):
    entries = _committed_entries(tree, commit, git_dir)
    held = _held_entries(tree, holder, git_dir)
    files = {
        path: (mode, oid, git_dir) for path, (mode, oid) in entries.items() if mode != GITLINK_MODE
    }
    linked = {
        path: _submodule_files(tree, path, oid, holder, held, git_dir)
        for path, (mode, oid) in sorted(entries.items())
        if mode == GITLINK_MODE
    }
    inside = {
        path + "/" + name: entry for path, found in linked.items() for name, entry in found.items()
    }
    empty = {path: (GITLINK_MODE, entries[path][1], None) for path, found in linked.items() if not found}
    return {**files, **inside, **empty}


def _without_work(with_work, held, implementation):
    return {
        **{path: entry for path, entry in with_work.items() if not _under(path, implementation)},
        **{path: entry for path, entry in held.items() if _under(path, implementation)},
    }


def _gate_listing(folder, kept):
    found = []
    pending = [""]
    while pending:
        current = pending.pop()
        full = os.path.join(folder, *_parts(current)) if current else folder
        mode = stat.S_IMODE(os.lstat(full).st_mode)
        if mode & stat.S_IRWXU != stat.S_IRWXU:
            os.chmod(full, mode | stat.S_IRWXU)
        with os.scandir(full) as listing:
            names = sorted(entry.name for entry in listing)
        for name in names:
            if not current and name in kept:
                continue
            path = current + "/" + name if current else name
            kind = _kind(os.lstat(os.path.join(full, name)).st_mode)
            found.append((path, kind))
            if kind == "dir":
                pending.append(path)
    return dict(found)


def _differs(folder, path, mode, oid, kind):
    if kind != _expected_kind(mode):
        return True
    full = os.path.join(folder, *_parts(path))
    if mode == SYMLINK_MODE:
        return _object_id(os.fsencode(os.readlink(full)), oid) != oid
    executable = bool(os.lstat(full).st_mode & stat.S_IXUSR)
    data = _read_bytes(folder, path)
    return data is None or executable != (mode == EXECUTABLE_MODE) or _object_id(data, oid) != oid


def reset_gate_folder(tree, folder, target, kept, locked):
    found = _gate_listing(folder, kept)
    files = {path: entry for path, entry in target.items() if entry[0] != GITLINK_MODE}
    folders = frozenset(prefix for path in target for prefix in _prefixes(path)[:-1]) | frozenset(
        path for path, entry in target.items() if entry[0] == GITLINK_MODE
    )
    dropped = frozenset(
        path
        for path, kind in found.items()
        if not (path in folders and kind == "dir")
        and not (path in files and kind == _expected_kind(files[path][0]))
    )
    removed = tuple(
        path for path in sorted(found) if any(prefix in dropped for prefix in _prefixes(path))
    )
    for path in sorted(dropped):
        if not any(prefix in dropped for prefix in _prefixes(path)[:-1]):
            _remove_entry(folder, path)
    stale = tuple(
        path
        for path, (mode, oid, _) in sorted(files.items())
        if path in removed or path not in found or _differs(folder, path, mode, oid, found[path])
    )
    blobs = _store_blobs(tree, {path: files[path] for path in stale})
    for path in stale:
        mode, oid, store = files[path]
        _place(folder, path, mode, blobs[(store, oid)])
    for path in sorted(path for path, entry in target.items() if entry[0] == GITLINK_MODE):
        if not os.path.isdir(tree_path(folder, path)):
            _make_parents(folder, path)
            os.mkdir(os.path.join(folder, *_parts(path)))
    return tuple(sorted(_referee_marked(set(removed) | set(stale), locked)))


def _dependency_folders(repo, top):
    if repo is None:
        return ()
    root = os.path.abspath(repo)
    with os.scandir(root) as listing:
        names = sorted(entry.name for entry in listing if entry.is_dir())
    return tuple(
        name
        for name in names
        if name not in top
        and (name in INSTALLED_FOLDERS or os.path.isfile(os.path.join(root, name, VENV_MARKER)))
    )


def _link_dependencies(repo, folder, top):
    names = _dependency_folders(repo, top)
    for name in names:
        os.symlink(os.path.join(os.path.abspath(repo), name), os.path.join(folder, name))
    return names


def _gate_setup(setup, folder, top, log_dir, timeout, env, sandbox=None):
    out = os.path.join(log_dir, "gate-setup.log")
    try:
        code, timed_out = run_command(
            build_argv(setup, {"worktree": folder}),
            folder,
            timeout,
            out,
            out + ".err",
            env,
            sandbox,
        )
    except OSError as error:
        raise GateSetupError("the --gate-setup command could not start in %s: %s" % (folder, error))
    if timed_out:
        raise GateSetupError(
            "the --gate-setup command timed out after %s seconds in %s" % (timeout, folder)
        )
    if code != 0:
        raise GateSetupError(
            "the --gate-setup command exited %d in %s: %s"
            % (code, folder, last_line(out + ".err") or last_line(out) or "<no output>")
        )
    with os.scandir(folder) as listing:
        return tuple(sorted(entry.name for entry in listing if entry.name not in top))


def ignored_paths(tree):
    args = [
        *STATUS_SETTINGS,
        "status",
        "--porcelain",
        "-z",
        "--untracked-files=all",
        "--ignored=matching",
        "--no-renames",
    ]
    completed = git_run(args, tree, raw=True, env=PINNED_GIT)
    if completed.returncode != 0:
        raise GitError(args, completed.returncode, completed.stderr)
    return sorted(os.fsdecode(entry[3:]) for entry in completed.stdout.split(b"\0") if len(entry) > 3)


def _referee_marked(paths, locked):
    return frozenset(
        path
        for path in paths
        if referee.referee_reason(path, locked) is not None
        or any(_held_under((path,), _norm(entry)) for entry in locked)
    )


def _submodule_changes(tree, holder, path, old, new, git_dir, prefix):
    try:
        store = _module_dir(tree, holder, path, git_dir)
        return _tree_changes(tree, old, new, store, prefix + path + "/")
    except (GitError, OSError, ValueError) as error:
        raise GitError(
            ["ls-tree", new],
            1,
            "%s%s/ cannot be judged, because its commits cannot be read: %s" % (prefix, path, error),
        )


def _tree_changes(tree, old, new, git_dir=None, prefix=""):
    before = _committed_entries(tree, old, git_dir)
    after = _committed_entries(tree, new, git_dir)
    changed = _changed_paths(before, after)
    inside = tuple(
        written
        for path in changed
        if _gitlink(before, path) and _gitlink(after, path)
        for written in _submodule_changes(
            tree, old, path, before[path][1], after[path][1], git_dir, prefix
        )
    )
    return tuple(prefix + path for path in changed) + inside


def referee_writes(plan, tree, head, base):
    locked = tuple(plan.get("locked") or ())
    return sorted(_referee_marked(_tree_changes(tree, base, head), locked))


def reconcile(plan, msp, tree, head, base, producer_heads=()):
    changed = changed_files(tree, head, base, producer_heads)
    declared = sorted({_norm(f) for f in plan["msps"][msp].get("files") or ()})
    undeclared = [path for path in changed if _norm(path) not in declared]
    missing = [path for path in declared if path not in {_norm(c) for c in changed}]
    unproven = _unproven_paths(plan, msp)
    untouched = [
        path
        for path in missing
        if path in unproven
        and git_ok(["cat-file", "-e", "%s:%s" % (base, path)], tree, env=PINNED_GIT)
    ]
    unwritten = [path for path in missing if path not in untouched]
    crossing = ()
    for path in undeclared:
        owner = msp_owner_of(plan, path)
        if owner is not None and owner != msp:
            crossing = crossing + ({"path": path, "msp": owner, "label": _label(plan, owner)},)
    written = referee_writes(plan, tree, head, base)
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


def _unproven(outcome, gate_class=None):
    if gate_class is not None:
        return "is classed %s, so no run judged it" % gate_class
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
            found.get("outcome"),
            found.get("class"),
        )
        for name in names
        for entry in steps[name].get("acceptance") or []
        for found in (judged.get((name, entry.get("file"), entry.get("test"))) or {},)
    )
    verified = tuple(
        _stated("%s %s" % (where, PROVEN), "an acceptance test %s" % PROVEN)
        for where, outcome, gate_class in properties
        if outcome == "pass" and gate_class is None
    )
    unproven = (
        tuple(
            _stated(
                "%s %s" % (where, _unproven(outcome, gate_class)),
                "an acceptance test %s" % _unproven(outcome, gate_class),
            )
            for where, outcome, gate_class in properties
            if outcome != "pass" or gate_class is not None
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
    head = commit_at(branch, tree, required=True)
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


def _gate_stage(plan, msp, tree, settings, head, replayed, recorded_gate=None):
    try:
        result = gate(
            plan,
            msp,
            tree["path"],
            settings["base_commit"],
            settings["acceptance_command"],
            os.path.join(settings["run_dir"], "gate", "msp-%d" % msp),
            settings["timeout"],
            settings.get("env"),
            settings.get("repo"),
            settings.get("gate_setup"),
            head,
            replayed,
            settings.get("suite_command"),
            settings.get("gate_classes"),
            recorded_gate,
            settings.get("sandbox"),
        )
    except (OSError, GitError, GateSetupError, TraceError, subprocess.SubprocessError) as error:
        return {"gate": None, "gate_error": str(error)}
    return {"gate": result, "gate_error": None}


def gate_inputs(settings, replayed):
    named = {
        "acceptance_command": settings["acceptance_command"],
        "suite_command": settings.get("suite_command"),
        "gate_setup": settings.get("gate_setup"),
        "timeout": settings["timeout"],
        "replayed": replayed,
    }
    return hashlib.sha256(json.dumps(named, sort_keys=True).encode("utf-8")).hexdigest()


def _committed_head(plan, msp, tree):
    try:
        return commit_before_gate(plan, msp, tree["path"], tree["branch"]), None
    except GitError as error:
        return None, str(error)


def _reusable_gate(prior, head, inputs):
    recorded = prior.get("gate")
    if not isinstance(recorded, dict) or head is None or head != recorded.get("commit"):
        return None
    if prior.get("gate_inputs") != inputs:
        return None
    return recorded


def _proven_head(prior, head, proven):
    if proven is None or head is None or not isinstance(prior.get("gate"), dict):
        return None
    return head if head.startswith(proven) else None


def _judged_head(tree, head):
    return head if head is not None else branch_commit(tree["path"], tree["branch"], required=True)


def _head_off_branch(tree, head):
    judged = head if head is not None else branch_commit(tree["path"], tree["branch"])
    attached = git_run(["symbolic-ref", "-q", "HEAD"], tree["path"], env=PINNED_GIT).stdout.strip()
    return (
        judged is None
        or attached != "refs/heads/" + tree["branch"]
        or commit_at("HEAD", tree["path"]) != judged
    )


def _producer_heads(tree, producers, trees):
    found = tuple(branch_commit(tree["path"], trees[p]["branch"]) for p in producers)
    return tuple(commit for commit in found if commit is not None)


def _with_unreplayed(plan, findings, unreplayed):
    if not unreplayed:
        return findings
    marked = _referee_marked(unreplayed, tuple(plan.get("locked") or ()))
    reported = (
        {"undeclared": sorted(set(findings["undeclared"]) | set(unreplayed))}
        if "undeclared" in findings
        else {}
    )
    return {
        **findings,
        **reported,
        "referee": sorted(set(findings["referee"]) | marked),
        "fatal": bool(findings["fatal"] or marked),
    }


def _reconcile_stage(plan, msp, tree, producers, trees, settings, head=None, unreplayed=()):
    try:
        findings = reconcile(
            plan,
            msp,
            tree["path"],
            _judged_head(tree, head),
            settings["base_commit"],
            _producer_heads(tree, producers, trees),
        )
    except GitError as error:
        return {
            "reconcile": None,
            "state": MSP_GATE_FAILED,
            "reason": "mitosis could not judge the referee paths in the commits of %s, so the gate "
            "did not run and the MSP is held back: %s" % (tree["branch"], error),
        }
    return {"reconcile": _with_unreplayed(plan, findings, unreplayed)}


def _committed_reconcile(plan, tree, settings, uncommitted, head=None, unreplayed=()):
    try:
        written = referee_writes(
            plan, tree["path"], _judged_head(tree, head), settings["base_commit"]
        )
    except GitError as error:
        return {
            "reconcile": None,
            "state": MSP_GATE_FAILED,
            "reason": "mitosis judged only the commits on %s, because %s, and could not judge the "
            "referee paths they hold, so the gate did not run and the MSP is held back: %s"
            % (tree["branch"], uncommitted, error),
        }
    return {
        "reconcile": _with_unreplayed(
            plan, {"referee": written, "fatal": bool(written)}, unreplayed
        )
    }


def _referee_stage(
    plan, msp, tree, producers, trees, settings, uncommitted=None, head=None, unreplayed=()
):
    reconciled = (
        _reconcile_stage(plan, msp, tree, producers, trees, settings, head, unreplayed)
        if uncommitted is None
        else _committed_reconcile(plan, tree, settings, uncommitted, head, unreplayed)
    )
    if reconciled.get("state") == MSP_GATE_FAILED:
        return {**reconciled, "gate": None, "gate_error": reconciled["reason"]}
    written = reconciled["reconcile"]["referee"]
    off_branch = _head_off_branch(tree, head)
    if not written and not off_branch:
        return None
    if not written:
        return {
            **reconciled,
            "reconcile": {**reconciled["reconcile"], "fatal": True},
            "state": MSP_REFEREE_REVERTED,
            "reason": "the worktree's HEAD is not %s at its commit, so the referee and the gate "
            "would not judge one commit; mitosis held the MSP back" % tree["branch"],
        }
    kept = (
        ""
        if uncommitted is None
        else "; the rest of the work stays uncommitted, because %s" % uncommitted
    )
    landed = [path for path in written if path not in unreplayed]
    try:
        if landed:
            revert_implementation(
                tree["path"],
                tree["branch"],
                _judged_head(tree, head),
                settings["base_commit"],
                landed,
                "revert(%s): restore the referee paths a Worker wrote" % _label(plan, msp),
            )
    except (GitError, OSError) as error:
        return {
            **reconciled,
            "state": MSP_GATE_FAILED,
            "gate": None,
            "gate_error": "the referee restore on %s failed, so the gate did not run: %s"
            % (tree["branch"], error),
            "reason": "a Worker wrote %s, which the referee owns; mitosis could not restore them "
            "on %s, so the gate did not run and the MSP is held back%s"
            % (", ".join(written), tree["branch"], kept),
        }
    return {
        **reconciled,
        "state": MSP_REFEREE_REVERTED,
        "reason": "a Worker wrote %s, which the referee owns; mitosis restored them on %s and "
        "held the MSP back%s" % (", ".join(written), tree["branch"], kept),
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


def _setup_refereed_without_runs(result):
    return bool(result.get("referee")) and not any(
        "with_work" in prop for prop in result.get("properties") or ()
    )


def _trajectory_stage(plan, msp, gated, settings):
    path = settings.get("trajectory")
    if not path or gated["gate"] is None or _setup_refereed_without_runs(gated["gate"]):
        return {}
    try:
        outcomes.append(path, _gate_outcomes(plan, msp, gated["gate"], settings))
    except OSError as error:
        return {"trajectory_error": str(error)}
    return {}


def _gate_referee_writes(gated):
    return list((gated["gate"] or {}).get("referee") or [])


def _with_gate_referee(reconciled, tampered):
    if not tampered or reconciled["reconcile"] is None:
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


def _uncommitted_reason(block, commit_error):
    if block is not None:
        return "mitosis made no commit before the gate: %s" % block["reason"]
    if commit_error is not None:
        return "the commit before the gate failed: %s" % commit_error
    return None


def _blocked_record(block, held):
    if held is None:
        return {**block, "reconcile": NO_REFEREE_WRITE}
    if held.get("state") == MSP_REFEREE_REVERTED:
        return held
    return {
        **block,
        "reconcile": held.get("reconcile"),
        "reason": "%s; %s" % (block["reason"], held["reason"]),
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
    replayed = isolation.msp_replayed(plan, msp, state["lanes"])
    unreplayed = isolation.msp_unreplayed(plan, msp, state["lanes"])
    state = write_state(run_dir, _msp_record(state, msp, replayed=replayed))
    head, commit_error = (None, None) if block is not None else _committed_head(plan, msp, tree)
    if block is None:
        ignored = set(ignored_paths(tree["path"])) | set(
            isolation.msp_ignored(plan, msp, state["lanes"])
        )
        state = write_state(run_dir, _msp_record(state, msp, ignored=sorted(ignored)))
    uncommitted = _uncommitted_reason(block, commit_error)
    held = _referee_stage(
        plan, msp, tree, producers, trees, settings, uncommitted, head, unreplayed
    )
    if held is not None:
        notify(
            settings.get("observer"),
            "reconciled",
            {"msp": msp, "findings": held["reconcile"]},
        )
    if block is not None:
        return write_state(run_dir, _msp_record(state, msp, **_blocked_record(block, held)))
    if held is not None:
        return write_state(run_dir, _msp_record(state, msp, **held))
    proven_head = _proven_head(prior, head, settings["proven_heads"].get(msp))
    inputs = gate_inputs(settings, replayed)
    proven_gate = prior.get("gate") if proven_head is not None else None
    recorded_gate = None if proven_head is not None else _reusable_gate(prior, head, inputs)
    judged_now = commit_error is not None or proven_head is None
    gate_started = now()
    gated = (
        {"gate": None, "gate_error": commit_error}
        if commit_error is not None
        else _gate_stage(plan, msp, tree, settings, head, replayed, recorded_gate)
        if judged_now
        else {"gate": proven_gate, "gate_error": None}
    )
    notify(
        settings.get("observer"),
        "gated",
        {
            "msp": msp,
            "gate": gated["gate"],
            "reused": proven_gate is not None or recorded_gate is not None,
            "seconds": seconds_between(gate_started, now()),
        },
    )
    proof = {} if proven_head is None else {"proven_head": proven_head}
    logged = _trajectory_stage(plan, msp, gated, settings) if judged_now else {}
    recorded_inputs = inputs if judged_now else prior.get("gate_inputs")
    state = write_state(
        run_dir,
        _msp_record(state, msp, **gated, **proof, **logged, gate_inputs=recorded_inputs),
    )
    if commit_error is not None:
        return write_state(
            run_dir,
            _msp_record(
                state,
                msp,
                reconcile=NO_REFEREE_WRITE,
                state=MSP_GATE_FAILED,
                reason="mitosis could not commit the work on %s before the gate, so it kept the "
                "work uncommitted in the worktree, did not run the gate and held the MSP back: %s"
                % (tree["branch"], commit_error),
            ),
        )
    tampered = _gate_referee_writes(gated) if judged_now else []
    reconciled = _with_gate_referee(
        _reconcile_stage(plan, msp, tree, producers, trees, settings, head, unreplayed), tampered
    )
    notify(
        settings.get("observer"),
        "reconciled",
        {"msp": msp, "findings": reconciled["reconcile"]},
    )
    state = write_state(run_dir, _msp_record(state, msp, **reconciled))
    if reconciled.get("state") in (MSP_BLOCKED, MSP_GATE_FAILED):
        return state
    if tampered:
        return write_state(
            run_dir,
            _msp_record(
                state,
                msp,
                state=MSP_REFEREE_REVERTED,
                reason="a gate run wrote %s in the gate's clean checkout, which the referee "
                "owns, so mitosis held %s back" % (", ".join(tampered), tree["branch"]),
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


LOCK_MESSAGE = "test: lock the acceptance checks"

LOCK_DIR = "lock"

LOCK_INDEX = "index"

ALREADY_LOCKED = (
    "the locked checks are already on the feature branch at %s, so no check-author ran"
)

LOCKED_BY_AUTHOR = (
    "the check-author wrote %s, each locked test fails on the feature branch without the "
    "build, and %s locks them"
)


def lock_record(state, reason, commit=None, session_id=None, **fields):
    return {"state": state, "reason": reason, "commit": commit, "session_id": session_id, **fields}


def _blob_paths(repo, commit, paths):
    args = ["--literal-pathspecs", "ls-tree", "-z", commit, "--", *paths]
    listed = git_run(args, repo, raw=True, env=PINNED_GIT)
    if listed.returncode != 0:
        return frozenset()
    rows = (os.fsdecode(entry).split("\t", 1) for entry in listed.stdout.split(b"\0") if entry)
    return frozenset(path for header, path in rows if header.split(" ")[1] == "blob")


def missing_paths(repo, commit, paths):
    if not paths:
        return ()
    present = _blob_paths(repo, commit, tuple(_norm(path) for path in paths))
    return tuple(path for path in paths if _norm(path) not in present)


def _lock_values(plan, folders, feature_branch, models, run_dir, session_id):
    brief = plan["check_author"]["brief"]
    return {
        "task": brief["text"],
        "model": (models or {}).get(core.LOCK_TIER, ""),
        "tier": core.LOCK_TIER,
        "worktree": folders["clone"],
        "branch": feature_branch,
        "lane": core.CHECK_AUTHOR_LANE,
        "msp": core.LOCK_MSP,
        "charter": brief.get("charter"),
        "document": brief.get("document"),
        "run_dir": run_dir,
        "session_id": session_id,
    }


def _dispatch_check_author(plan, settings, command, models, sandbox, base):
    run_dir = settings["run_dir"]
    folders = isolation.lock_folders(run_dir)
    lane_dir = os.path.join(run_dir, "lanes")
    os.makedirs(lane_dir, exist_ok=True)
    out = os.path.join(lane_dir, "%s.out" % LOCK_DIR)
    err = os.path.join(lane_dir, "%s.err" % LOCK_DIR)
    session = core.new_session_id()
    argv = build_argv(
        command, _lock_values(plan, folders, settings["feature_branch"], models, run_dir, session)
    )
    carried = core.carried_session(argv, session)
    try:
        start = isolation.cut_clone(
            {"path": settings["repo"], "branch": settings["feature_branch"]}, folders
        )
        if start != base:
            git(["reset", "-q", "--hard", base], folders["clone"])
        confinement = isolation.worker_confinement(folders, sandbox, settings["env"])
    except (GitError, OSError) as error:
        return lock_record(
            "failed",
            "mitosis could not prepare the check-author's clone %s: %s" % (folders["clone"], error),
            session_id=carried,
        )
    code, timed_out = run_command(
        argv, folders["clone"], settings["timeout"], out, err, settings["env"], confinement
    )
    returned = last_return(out)
    judged, why = lane_verdict(code, returned, timed_out)
    ran = {"stdout": out, "stderr": err, "returned": returned, "clone": folders["clone"]}
    if judged == "ok":
        return lock_record("ok", None, session_id=carried, **ran)
    limit = None if timed_out else usage_limit_line(out, err)
    if limit is not None:
        return lock_record(LANE_PARKED, "usage limit: %s" % limit, session_id=carried, **ran)
    return lock_record("failed", "the check-author failed: %s" % why, session_id=carried, **ran)


def _stored_entries(repo, entries):
    ordered = sorted(entries.items())
    if not ordered:
        return {}
    folder = tempfile.mkdtemp(prefix="mitosis-lock-blobs-")
    try:
        staged = tuple(os.path.join(folder, str(position)) for position in range(len(ordered)))
        for staged_path, (_, (_, data)) in zip(staged, ordered):
            with open(staged_path, "wb") as handle:
                handle.write(data)
        listing = "".join(staged_path + "\n" for staged_path in staged).encode("utf-8")
        output = _checked_run(
            ["hash-object", "-w", "--no-filters", "--stdin-paths"], repo, listing, PINNED_GIT
        )
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    oids = output.decode("ascii").split()
    if len(oids) != len(ordered):
        raise GitError(
            ["hash-object", "--stdin-paths"],
            1,
            "git stored %d of the %d locked files" % (len(oids), len(ordered)),
        )
    return {_norm(path): (mode, oid) for (path, (mode, _)), oid in zip(ordered, oids)}


def _locked_checks(plan):
    locked = frozenset(referee.normalised(path) for path in plan.get("locked") or ())
    found = ()
    for item in plan.get("items") or ():
        for entry in item.get("acceptance") or ():
            check = {"file": entry["file"], "test": entry["test"]}
            if referee.normalised(check["file"]) in locked and check not in found:
                found = found + (check,)
    return found


def _lock_fault(check, code, timed_out):
    named = "%s::%s" % (check["file"], check["test"])
    if timed_out:
        return "%s timed out at the locked tree, so it could not run" % named
    if code == ACCEPTANCE_FAILURE_EXIT:
        return None
    if code == 0:
        return (
            "%s passes on the feature branch without the build, so it cannot tell the work "
            "from its absence" % named
        )
    return "%s could not run at the locked tree: exit %d" % (named, code)


def _locked_tree_faults(plan, settings, base, stored):
    repo = settings["repo"]
    locked = tuple(plan.get("locked") or ())
    timeout, env = settings["timeout"], settings["env"]
    target = {
        **_expanded(repo, base, base),
        **{path: (mode, oid, None) for path, (mode, oid) in stored.items()},
    }
    log_dir = os.path.join(settings["run_dir"], LOCK_DIR)
    os.makedirs(log_dir, exist_ok=True)
    _remove_gate_folders(log_dir)
    folder = tempfile.mkdtemp(prefix=GATE_FOLDER_PREFIX, dir=log_dir)
    try:
        confinement = isolation.gate_confinement(settings.get("sandbox"), log_dir, folder)
        reset_gate_folder(repo, folder, target, (), locked)
        top = frozenset(_parts(path)[0] for path in target)
        setup = settings["gate_setup"]
        kept = (
            _link_dependencies(repo, folder, top)
            if setup is None
            else _gate_setup(setup, folder, top, log_dir, timeout, env, confinement)
        )
        faults = ()
        for position, check in enumerate(_locked_checks(plan)):
            reset_gate_folder(repo, folder, target, kept, locked)
            code, timed_out = run_acceptance(
                settings["acceptance_command"],
                folder,
                check,
                os.path.join(log_dir, "%d-locked.log" % position),
                timeout,
                env,
                confinement,
            )
            fault = _lock_fault(check, code, timed_out)
            faults = faults if fault is None else faults + (fault,)
        return faults
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        isolation.release_gate(isolation.gate_folders(log_dir))


def _removed(path):
    if os.path.lexists(path):
        os.remove(path)


def _lock_commit(settings, base, stored):
    repo, branch = settings["repo"], settings["feature_branch"]
    index = os.path.join(settings["run_dir"], LOCK_DIR, LOCK_INDEX)
    os.makedirs(os.path.dirname(index), exist_ok=True)
    _removed(index)
    indexed = {"GIT_INDEX_FILE": index}
    try:
        git(["read-tree", base], repo, env=indexed)
        rows = b"".join(
            b"%s %s\t%s\0" % (mode.encode("ascii"), oid.encode("ascii"), os.fsencode(path))
            for path, (mode, oid) in sorted(stored.items())
        )
        _checked_run(["update-index", "-z", "--add", "--index-info"], repo, rows, indexed)
        tree = git(["write-tree"], repo, env=indexed)
    finally:
        _removed(index)
    commit = git(["commit-tree", tree, "-p", base, "-m", LOCK_MESSAGE], repo)
    git(["update-ref", "refs/heads/" + branch, commit, base], repo)
    return commit


def _pushed_lock(settings, base, commit):
    if not settings["push"]:
        return None
    branch = settings["feature_branch"]
    ref = "refs/heads/" + branch
    pushed = git_run(["push", "-q", settings["remote"], "%s:%s" % (ref, ref)], settings["repo"])
    if pushed.returncode == 0:
        return None
    git_run(["update-ref", ref, base, commit], settings["repo"])
    detail = (pushed.stderr.strip() or pushed.stdout.strip()).splitlines()
    return "pushing %s to %s failed, so mitosis moved it back to %s: %s" % (
        branch,
        settings["remote"],
        base[:12],
        detail[0] if detail else "exit %d" % pushed.returncode,
    )


def _failed_lock(record, reason):
    return {**record, "state": "failed", "reason": reason}


def _landed_lock(plan, settings, record, base):
    repo, branch = settings["repo"], settings["feature_branch"]
    locked = tuple(plan.get("locked") or ())
    clone = record["clone"]
    try:
        entries = isolation.write_set_entries(clone, locked)
        outside = isolation.outside_writes(clone, repo, _committed_entries(repo, base), locked)
    except (GitError, OSError) as error:
        return _failed_lock(
            record, "mitosis could not read the check-author's clone %s: %s" % (clone, error)
        )
    noted = {
        **record,
        "undeclared": sorted(
            set(outside["writes"]) | set(outside["deletes"]) | set(outside["unreplayed"])
        ),
        "ignored": list(outside["ignored"]),
    }
    unwritten = [path for path, entry in sorted(entries.items()) if entry is None]
    if unwritten:
        return _failed_lock(noted, "the check-author left %s unwritten" % ", ".join(unwritten))
    try:
        stored = _stored_entries(repo, entries)
        faults = _locked_tree_faults(plan, settings, base, stored)
    except GateSetupError as error:
        return _failed_lock(noted, str(error))
    except (GitError, OSError) as error:
        return _failed_lock(noted, "mitosis could not run the locked checks: %s" % error)
    if faults:
        return _failed_lock(noted, "; ".join(faults))
    checked_out = sorted(
        path for path, held in registered_worktrees(repo).items() if held == branch
    )
    try:
        commit = _lock_commit(settings, base, stored)
    except (GitError, OSError) as error:
        return _failed_lock(
            noted, "mitosis could not commit the locked checks on %s: %s" % (branch, error)
        )
    unpushed = _pushed_lock(settings, base, commit)
    if unpushed is not None:
        return _failed_lock(noted, unpushed)
    return {
        **noted,
        "state": "ok",
        "reason": LOCKED_BY_AUTHOR % (", ".join(locked), commit[:12]),
        "commit": commit,
        "checked_out": checked_out,
    }


def lock_stage(plan, state, settings, command, models, sandbox):
    prior = state.get("lock")
    if isinstance(prior, dict) and prior.get("state") == "ok" and state.get("referee"):
        return state
    repo, branch, base = settings["repo"], settings["feature_branch"], state["base_commit"]
    locked = tuple(plan.get("locked") or ())
    if not missing_paths(repo, base, locked):
        return {
            **state,
            "lock": lock_record("ok", ALREADY_LOCKED % base[:12], commit=base),
            "referee": locked_record(repo, branch, base, locked),
        }
    record = _dispatch_check_author(plan, settings, command, models, sandbox, base)
    if record["state"] != "ok":
        return {**state, "lock": record, "referee": None}
    landed = _landed_lock(plan, settings, record, base)
    if landed["state"] != "ok":
        return {**state, "lock": landed, "referee": None}
    isolation.remove_lane_folders(isolation.lock_folders(settings["run_dir"]))
    return {
        **state,
        "lock": landed,
        "base_commit": landed["commit"],
        "referee": locked_record(repo, branch, landed["commit"], locked),
    }


def lock_blocked(plan, state):
    parked = state["lock"]["state"] == LANE_PARKED
    reason = "the %s Step %s did not lock the checks (%s): %s" % (
        core.CHECK_AUTHOR,
        plan["check_author"]["name"],
        state["lock"]["state"],
        state["lock"]["reason"],
    )
    lanes = {
        str(index): lane_record(index, lane["msp"], LANE_PARKED if parked else "blocked", reason=reason)
        for index, lane in enumerate(plan.get("lanes") or [])
    }
    msps = {
        str(index): {
            **(state["msps"].get(str(index)) or {}),
            "state": MSP_PARKED if parked else MSP_BLOCKED,
            "reason": reason,
        }
        for index in range(len(plan.get("msps") or []))
    }
    return {**state, "lanes": lanes, "msps": msps}


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
    gate_setup=None,
    base_commit=None,
    worker_sandbox=False,
    worker_writes=(),
    suite_command=None,
    gate_classes=None,
    gate_classes_sha256=None,
):
    prior = prior_state(run_dir, plan, resume)
    header = carried_header(header, prior)
    check_template("dispatch", worker_command)
    check_template("acceptance", acceptance_command)
    if not no_push and pr_command is not None:
        check_template("pull-request", pr_command)
    if gate_setup is not None:
        check_template("gate-setup", gate_setup)
    if suite_command is not None:
        check_template("suite", suite_command)
    checked_writes = isolation.refuse_overlapping_writes(run_dir, worker_writes)
    if not no_push and header is None:
        raise ConfigError(
            "a run allowed to push needs a pull-request header holding its type, subject and "
            "why lines, so it can title and describe each pull request"
        )
    check_models(plan, worker_command, models)
    if timeout is None:
        raise ConfigError("a per-Lane timeout is required; mitosis has no default")
    program = isolation.sandbox_program(worker_sandbox)
    if program is not None:
        isolation.refuse_unquotable(
            (os.path.abspath(run_dir), isolation.worker_home(env)) + checked_writes
        )
    sandbox = None if program is None else {"program": program, "writes": checked_writes}
    recorded = (prior or {}).get("lanes") or {}
    if any(
        (recorded.get(str(index)) or {}).get("state") != "ok"
        for index in range(len(plan.get("lanes") or []))
    ):
        check_identity(repo)
    base = base_commit or pinned_base(repo, feature_branch, prior)
    settings = {
        "repo": repo,
        "feature_branch": feature_branch,
        "base_commit": base,
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
        "gate_setup": gate_setup,
        "suite_command": suite_command,
        "gate_classes": tuple(gate_classes or ()),
        "sandbox": sandbox,
    }
    os.makedirs(run_dir, exist_ok=True)
    write_json(os.path.join(run_dir, PLAN_FILE), plan)
    state = write_state(
        run_dir,
        {
            "version": core.__version__,
            "plan_id": plan.get("plan_id"),
            "feature_branch": feature_branch,
            "base_commit": base,
            "started": now(),
            "drift": document_drift(plan, root or repo),
            "worktrees": [],
            "lanes": dict((prior or {}).get("lanes") or {}),
            "msps": dict((prior or {}).get("msps") or {}),
            "stacking_exceptions": list((prior or {}).get("stacking_exceptions") or []),
            "merge_danger": merge_danger_pair(header),
            "sandbox": isolation.sandbox_name(program),
            **(
                {"gate_classes": list(gate_classes), "gate_classes_sha256": gate_classes_sha256}
                if gate_classes is not None
                else {}
            ),
            **(
                {"lock": (prior or {}).get("lock"), "referee": (prior or {}).get("referee")}
                if plan.get("check_author")
                else {}
            ),
        },
    )
    if plan.get("check_author"):
        state = write_state(
            run_dir, lock_stage(plan, state, settings, worker_command, models, sandbox)
        )
        if state["lock"]["state"] != "ok":
            return write_state(run_dir, lock_blocked(plan, state))
        base = state["base_commit"]
        settings = {**settings, "base_commit": base}
    trees = prepare_worktrees(
        plan["msps"], base, trees_root or os.path.join(run_dir, "trees"), repo, prefix
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
        base,
        models=models,
        prior=state["lanes"],
        on_lane=on_lane,
        observer=observer,
        env=env,
        sandbox=sandbox,
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
