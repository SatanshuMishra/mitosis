import ast
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from datetime import datetime, timezone
from functools import lru_cache

import core
import handoff
import isolation
import mutants
import outcomes
import pr_facts
import referee

BRANCH_PREFIX = "mitosis"

PLAN_FILE = "plan.json"

STATE_FILE = "state.json"
RUNNER_FILE = "runner.pid"

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

QUIET_PROMPTS = {
    "GIT_TERMINAL_PROMPT": "0",
    "GH_PROMPT_DISABLED": "1",
    "GIT_PAGER": "cat",
    "PAGER": "cat",
}

SSH_OPTIONS = "-o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4"

DEFAULT_SSH = "ssh"

HTTP_SETTINGS = (("http.lowSpeedLimit", "1"), ("http.lowSpeedTime", "60"))

CONFIG_UNSET = 1

CONFIGURED_SSH_CACHE = 256

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

SAMPLED_CODE = "code"

SAMPLED_DATA = "data"

SAMPLED_UNLOADED = "unloaded"

MSP_GATE_FAILED = "gate-failed"

UNPROVEN_OUTCOMES = ("weak", "inert", "inconclusive")

PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")

WORKER_RETURN = core.RETURN_SCHEMA

REASK_STEM = "reask"

MODEL_TEMPLATE_NEEDS = ("schema", "session_id")

REASK_TEMPLATE_NEEDS = ("prompt", "session_id", "schema")

LANE_RECORD_KEYS = (
    "lane",
    "msp",
    "state",
    "reason",
    "exit",
    "returned",
    "stdout",
    "stderr",
    "merged",
    "commit",
    "started",
    "finished",
    "session_id",
    "seeded",
    "seed_skipped",
    "seed_seconds",
    "output_tail",
)

MAX_OUTPUT = 4000


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
            "git %s exited %d: %s"
            % (" ".join(" ".join(str(arg).split()) for arg in args), code, stderr.strip() or "<no stderr>")
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
    def __init__(self, message, shipped=None):
        super().__init__(message)
        self.shipped = shipped


def _decoded(data):
    return data.decode("utf-8", "surrogateescape")


def _git_kept_environ(source=None):
    return {
        name: value
        for name, value in (os.environ if source is None else source).items()
        if name not in UNPINNED_GIT_VARIABLES
    }


@lru_cache(maxsize=CONFIGURED_SSH_CACHE)
def configured_ssh(repo):
    if repo is None:
        return None
    argv = ["git", "config", "--get", "core.sshCommand"]
    completed = subprocess.run(
        argv,
        cwd=repo,
        env={**_git_kept_environ(), **QUIET_PROMPTS},
        stdin=subprocess.DEVNULL,
        capture_output=True,
    )
    if completed.returncode == CONFIG_UNSET:
        return None
    if completed.returncode != 0:
        raise GitError(argv[1:], completed.returncode, _decoded(completed.stderr))
    found = _decoded(completed.stdout).strip()
    return found or None


def ssh_command(base, repo=None):
    chosen = (
        base.get("GIT_SSH_COMMAND")
        or configured_ssh(repo)
        or (shlex.quote(base["GIT_SSH"]) if base.get("GIT_SSH") else DEFAULT_SSH)
    )
    if chosen.endswith(" " + SSH_OPTIONS):
        return chosen
    return chosen + " " + SSH_OPTIONS


def _config_count(base):
    raw = base.get("GIT_CONFIG_COUNT") or "0"
    if not raw.isdigit():
        raise ConfigError("GIT_CONFIG_COUNT is %r, not a count of git config entries" % raw)
    return int(raw)


def _config_added(base, settings):
    count = _config_count(base)
    added = {
        name: value
        for offset, (key, setting) in enumerate(settings)
        for name, value in (
            ("GIT_CONFIG_KEY_%d" % (count + offset), key),
            ("GIT_CONFIG_VALUE_%d" % (count + offset), setting),
        )
    }
    return {**added, "GIT_CONFIG_COUNT": str(count + len(settings))}


def _config_entries(base):
    count = _config_count(base)
    held = tuple(
        (base.get("GIT_CONFIG_KEY_%d" % position), base.get("GIT_CONFIG_VALUE_%d" % position))
        for position in range(count)
    )
    if count >= len(HTTP_SETTINGS) and held[count - len(HTTP_SETTINGS):] == HTTP_SETTINGS:
        return {}
    return _config_added(base, HTTP_SETTINGS)


def process_environ(env=None, repo=None):
    base = dict(os.environ if env is None else env)
    return {
        **base,
        **QUIET_PROMPTS,
        "GIT_SSH_COMMAND": ssh_command(base, repo),
        **_config_entries(base),
    }


REPOSITORY_PROGRAMS_OFF = (("core.hooksPath", os.devnull), ("core.fsmonitor", "false"))

_programs_off_for_unconfined_git = ()


def _keep_repository_programs_confined(sandbox):
    global _programs_off_for_unconfined_git
    _programs_off_for_unconfined_git = () if sandbox is None else REPOSITORY_PROGRAMS_OFF


def git_environ(env=None, cwd=None):
    merged = {**process_environ(_git_kept_environ(), cwd), **(env or {})}
    if not _programs_off_for_unconfined_git:
        return merged
    return {**merged, **_config_added(merged, _programs_off_for_unconfined_git)}


def _git_once(args, cwd, raw=False, data=None, env=None):
    argv = ["git", *args]
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=git_environ(env, cwd),
        stdin=subprocess.DEVNULL if data is None else subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    stdout, stderr = proc.communicate(input=data)
    return subprocess.CompletedProcess(
        argv, proc.returncode, stdout if raw else _decoded(stdout), _decoded(stderr)
    )


def _locked(completed):
    stderr = completed.stderr or ""
    return completed.returncode != 0 and (
        any(marker in stderr for marker in LOCK_MARKERS) or bool(LOCK_PATH.search(stderr))
    )


def git_run(args, cwd, raw=False, data=None, env=None):
    completed = _git_once(args, cwd, raw, data, env)
    for delay in LOCK_RETRY_DELAYS:
        if not _locked(completed):
            return completed
        time.sleep(delay)
        completed = _git_once(args, cwd, raw, data, env)
    return completed


def git(args, cwd, check=True, env=None):
    completed = git_run(args, cwd, env=env)
    if check and completed.returncode != 0:
        raise GitError(args, completed.returncode, completed.stderr)
    return completed.stdout.strip()


def git_paths(args, cwd, env=None):
    completed = git_run(args, cwd, raw=True, env=env)
    if completed.returncode != 0:
        raise GitError(args, completed.returncode, completed.stderr)
    return [os.fsdecode(name) for name in completed.stdout.split(b"\0") if name]


def git_ok(args, cwd, env=None):
    return git_run(args, cwd, env=env).returncode == 0


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


def _run_is_live(run_dir):
    try:
        os.kill(read_json(os.path.join(run_dir, RUNNER_FILE)), 0)
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError, OverflowError):
        return False
    return True


def _files_built_in(tree):
    run_dir = os.path.dirname(os.path.dirname(tree))
    try:
        worktrees = read_json(os.path.join(run_dir, STATE_FILE)).get("worktrees") or ()
        msps = read_json(os.path.join(run_dir, PLAN_FILE)).get("msps") or ()
    except (OSError, ValueError):
        return frozenset()
    return frozenset(
        _norm(built)
        for entry in worktrees
        if os.path.realpath(entry["path"]) == tree
        for built in msps[entry["msp"]].get("files") or ()
    )


BRANCH_HOLDS = "mitosis-branch-holds"
BRANCH_IN_USE = "branch %s is in use by another running mitosis"

_held_branches = {}


def _branch_hold_path(repo, branch, suffix):
    common = os.path.realpath(os.path.join(repo, git(["rev-parse", "--git-common-dir"], repo)))
    return os.path.join(common, BRANCH_HOLDS, hashlib.sha256(branch.encode("utf-8")).hexdigest() + suffix)


def _hold_branch(repo, branch):
    lock = _branch_hold_path(repo, branch, ".lock")
    if lock in _held_branches:
        return
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    handle = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        os.close(handle)
        raise GitError(["worktree", "add", branch], 1, BRANCH_IN_USE % branch) from error
    _held_branches[lock] = handle


def _branch_builder(repo, branch):
    try:
        return read_json(_branch_hold_path(repo, branch, ".json"))
    except (OSError, ValueError):
        return {}


def _retried_by(repo, branch, holder, files):
    wanted = frozenset(_norm(path) for path in files)
    run_dir = os.path.dirname(os.path.dirname(holder))
    if os.path.isfile(os.path.join(run_dir, STATE_FILE)):
        return not _run_is_live(run_dir) and bool(_files_built_in(holder) & wanted)
    builder = _branch_builder(repo, branch)
    return builder.get("tree") == holder and bool(frozenset(builder.get("files") or ()) & wanted)


def prepare_worktree(repo, base, branch, path, files=()):
    _hold_branch(repo, branch)
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
        holders = sorted(tree for tree, held in registered.items() if held == branch and tree != real)
        for holder in holders:
            if _retried_by(repo, branch, holder, files):
                git(["switch", "--detach"], holder)
        git(["worktree", "add", path, branch], repo)
    else:
        git(["worktree", "add", "-b", branch, path, base], repo)
    write_json(
        _branch_hold_path(repo, branch, ".json"),
        {"tree": real, "files": sorted(_norm(built) for built in files)},
    )


def prepare_worktrees(msps, base, root, repo, prefix=BRANCH_PREFIX, bases=None, skipped=()):
    slugs = msp_slugs(msps)
    trees = ()
    for index, msp in enumerate(msps):
        branch = "%s/%s" % (prefix, slugs[index])
        path = os.path.join(os.path.abspath(root), slugs[index])
        if index not in skipped:
            files = msp.get("files") or () if isinstance(msp, dict) else ()
            prepare_worktree(repo, (bases or {}).get(index, base), branch, path, files)
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
        "schema",
    ),
    "reask": ("prompt", "session_id", "schema", "model"),
    "decompose": ("prompt", "model", "document", "session_id"),
    "acceptance": ("file", "test", "worktree"),
    "gate-setup": ("worktree",),
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


def _missing_placeholders(template, needed):
    used = {match.group(1) for match in PLACEHOLDER.finditer(template or "")}
    return tuple(key for key in needed if key not in used)


def check_reply_templates(worker_command, reask_command):
    missing = _missing_placeholders(worker_command, MODEL_TEMPLATE_NEEDS)
    if missing:
        raise ConfigError(
            "--dispatch-command lacks %s; a model command must carry {schema}, the call schema "
            "its reply meets as structured output, and {session_id}, the session a re-ask resumes"
            % ", ".join("{%s}" % key for key in missing)
        )
    if reask_command is None:
        raise ConfigError(
            "--reask-command is required: a Worker reply that fails %s is asked again in the "
            "same session through it, offered %s"
            % (
                WORKER_RETURN,
                ", ".join("{%s}" % key for key in TEMPLATE_PLACEHOLDERS[REASK_STEM]),
            )
        )
    try:
        check_template(REASK_STEM, reask_command)
    except ConfigError as error:
        raise ConfigError("--reask-command: %s" % error) from error
    missing = _missing_placeholders(reask_command, REASK_TEMPLATE_NEEDS)
    if missing:
        raise ConfigError(
            "--reask-command lacks %s; a re-ask must carry {prompt}, the errors to answer, "
            "{session_id}, the session to resume, and {schema}, the call schema to meet"
            % ", ".join("{%s}" % key for key in missing)
        )


@lru_cache(maxsize=1)
def worker_schema():
    return json.dumps(handoff.call_schema(WORKER_RETURN), sort_keys=True)


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


def read_return(path):
    try:
        with open(path, "rb") as handle:
            text = handle.read().decode("utf-8", "replace")
    except OSError as error:
        return None, (
            "the reply cannot be read against %s: its output file could not be opened (%s)"
            % (WORKER_RETURN, error.strerror or type(error).__name__),
        )
    return handoff.read_reply(text, WORKER_RETURN)


def reply_errors(errors):
    listed = tuple(errors or ())
    if not listed:
        return "no reply"
    if len(listed) == 1:
        return listed[0]
    return "%s %s" % (listed[0], "; ".join(listed[1:]))


def call_files(folder, stem):
    return {
        "out": os.path.join(folder, "%s.out" % stem),
        "err": os.path.join(folder, "%s.err" % stem),
        "reask_out": os.path.join(folder, "%s.%s.out" % (stem, REASK_STEM)),
        "reask_err": os.path.join(folder, "%s.%s.err" % (stem, REASK_STEM)),
    }


def _called(code, out, err, first=None):
    returned, errors = read_return(out)
    return {
        "exit": code,
        "returned": returned,
        "errors": errors,
        "stdout": out,
        "stderr": err,
        "first_exit": first,
    }


def worker_call(argv, reask, cwd, files, env=None, sandbox=None):
    code = run_command(argv, cwd, files["out"], files["err"], env, sandbox)
    first = _called(code, files["out"], files["err"])
    if first["returned"] is not None or usage_limit_line(files["out"], files["err"]) is not None:
        return first
    again = build_argv(
        reask["template"],
        {**reask["values"], "prompt": handoff.reask_prompt(WORKER_RETURN, first["errors"])},
    )
    code = run_command(again, cwd, files["reask_out"], files["reask_err"], env, sandbox)
    return _called(code, files["reask_out"], files["reask_err"], first["exit"])


def reask_values(values):
    return {
        "session_id": values["session_id"],
        "schema": values["schema"],
        "model": values.get("model"),
    }


def lane_verdict(exit_code, returned, errors=()):
    status = returned.get("status") if returned else None
    if exit_code == 0 and status == "ok":
        return "ok", None
    if returned is None:
        return "failed", "exit %d with no valid reply: %s" % (exit_code, reply_errors(errors))
    if exit_code == 0:
        return "failed", "exit 0 but reported %s" % status
    if status == "ok":
        return "failed", "exit %d but reported ok" % exit_code
    return "failed", "exit %d and reported %s" % (exit_code, status)


def run_command(argv, cwd, stdout_path, stderr_path, env=None, sandbox=None):
    launched, launch_env = isolation.confined(argv, process_environ(env, cwd), sandbox)
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
            code = proc.wait()
        finally:
            isolation.end_run(proc, sandbox)
    return code


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


HOOKS_OFF = ("-c", "core.hooksPath=" + os.devnull)


def _ignored_of(tree, paths):
    listed = git_run(["check-ignore", "-z", "--stdin"], tree, raw=True, data=b"\0".join(map(os.fsencode, paths)) + b"\0")
    if listed.returncode not in (0, 1):
        raise GitError(["check-ignore"], listed.returncode, listed.stderr)
    return frozenset(os.fsdecode(name) for name in listed.stdout.split(b"\0") if name)


def _stage(tree, paths, force):
    if paths:
        git(["add", "-A", *force, "--", *paths], tree)


def commit_paths(tree, paths, message, branch=None):
    msp_branch = branch or _attached_branch(tree)
    present = [p for p in paths if os.path.lexists(os.path.join(tree, p))]
    tracked = git_paths(["ls-files", "-z", "--", *paths], tree) if paths else []
    wanted = sorted(set(present) | set(tracked))
    ignored = _ignored_of(tree, wanted) if wanted else frozenset()
    targets = [path for path in wanted if path not in ignored]
    if targets:
        known = frozenset(tracked)
        _stage(tree, [path for path in targets if path not in known], ())
        _stage(tree, [path for path in targets if path in known], ("-f",))
        if git(["status", "--porcelain", "--", *targets], tree):
            git([*HOOKS_OFF, "commit", "-q", "--no-verify", "-m", message, "--", *targets], tree)
    return branch_commit(tree, msp_branch, required=True)


GIT_HELP_EXITS = (0, 129)

PRE_COMMIT = ("git", "hook", "run", "--ignore-missing", "pre-commit")

HOOK_COULD_NOT_RUN = "mitosis could not run the pre-commit hook over the whole change: %s"


def _hook_quarantine(log_dir):
    return os.path.join(log_dir, "quarantine")


def _hook_path(tree):
    named = subprocess.run(
        ["git", "rev-parse", "--git-path", "hooks/pre-commit"],
        cwd=tree,
        env=_git_kept_environ(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    return os.path.join(tree, named.stdout.strip()) if named.returncode == 0 else None


def _pre_commit_installed(tree):
    hook = _hook_path(tree)
    return (
        hook is not None
        and os.access(hook, os.X_OK)
        and git_run(["hook", "run", "-h"], tree).returncode in GIT_HELP_EXITS
    )


def _pre_commit_exit(folder, base, head, tree, log_dir, env, sandbox, out, err):
    git([*HOOKS_OFF, "worktree", "add", "-q", "--detach", folder, head], tree)
    git([*HOOKS_OFF, "reset", "-q", "--soft", base], folder)
    admin = git(["rev-parse", "--absolute-git-dir"], folder)
    stored = os.path.join(folder, git(["rev-parse", "--git-path", "objects"], folder))
    quarantine = _hook_quarantine(log_dir)
    objects = os.path.join(quarantine, "objects")
    os.makedirs(objects)
    index = shutil.copyfile(os.path.join(admin, "index"), os.path.join(quarantine, "index"))
    confinement = isolation.gate_confinement(sandbox, log_dir, folder, quarantine)
    quarantined = {
        **_git_kept_environ(env),
        "GIT_INDEX_FILE": index,
        "GIT_OBJECT_DIRECTORY": objects,
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": stored,
    }
    return run_command(list(PRE_COMMIT), folder, out, err, quarantined, confinement)


def msp_hook_refusal(tree, base, head, log_dir, env=None, sandbox=None):
    if head == base or not _pre_commit_installed(tree):
        return None
    os.makedirs(log_dir, exist_ok=True)
    _remove_gate_folders(log_dir)
    shutil.rmtree(_hook_quarantine(log_dir), ignore_errors=True)
    folder = tempfile.mkdtemp(prefix=GATE_FOLDER_PREFIX, dir=log_dir)
    out, err = os.path.join(log_dir, "pre-commit.out"), os.path.join(log_dir, "pre-commit.err")
    try:
        code = _pre_commit_exit(folder, base, head, tree, log_dir, env, sandbox, out, err)
    except (GitError, OSError) as error:
        return HOOK_COULD_NOT_RUN % error
    finally:
        git_run(["worktree", "remove", "--force", folder], tree)
        shutil.rmtree(folder, ignore_errors=True)
        shutil.rmtree(_hook_quarantine(log_dir), ignore_errors=True)
        isolation.release_gate(isolation.gate_folders(log_dir))
    if code == 0:
        return None
    return (_read_tail(err) or _read_tail(out)).strip() or "exit %d" % code


def cluster_label(steps, index):
    return steps[0] if len(steps) == 1 else "cluster-%d" % index


def worker_environ(env, steps, files, label, plan_id, tier, branch):
    return {
        **(os.environ if env is None else env),
        "MITOSIS_BRANCH": branch or "",
        "MITOSIS_CLUSTER": label,
        "MITOSIS_ITEM": steps[0],
        "MITOSIS_ITEMS": ",".join(steps),
        "MITOSIS_PLAN_ID": plan_id,
        "MITOSIS_TIER": tier or "",
        "MITOSIS_FILES": ",".join(sorted(set(files))),
    }


def _lane_environ(plan, lane, branch, env):
    steps = list(plan["lanes"][lane]["steps"])
    by_name = _steps_by_name(plan)
    return worker_environ(
        env,
        steps,
        [path for name in steps for path in (by_name.get(name) or {}).get("files") or ()],
        cluster_label(steps, lane),
        plan["plan_id"],
        plan["lanes"][lane].get("tier"),
        branch,
    )


def output_tail(called, max_output):
    if called["returned"] is not None:
        return None
    with open(called["stdout"], encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    return text[-max_output:] if text else None


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


def _producer_msps(plan, lane, producers):
    lanes = plan["lanes"]
    own = lanes[lane]["msp"]
    return tuple(sorted({lanes[producer]["msp"] for producer in producers} - {own}))


def _msp_lanes(plan, msp):
    return tuple(index for index, entry in enumerate(plan["lanes"]) if entry["msp"] == msp)


def _msp_head(tree, plan, msp, records):
    commits = [
        records[index]["commit"]
        for index in _msp_lanes(plan, msp)
        if records.get(index, {}).get("commit")
    ]
    head = None
    for commit in commits:
        if head is None or git_ok(["merge-base", "--is-ancestor", head, commit], tree):
            head = commit
    return head


def _cross_producers(plan, lane, producers, trees, records):
    tree = trees[plan["lanes"][lane]["msp"]]["path"]
    return tuple(
        {
            "msp": msp,
            "branch": trees[msp]["branch"],
            "commit": _msp_head(tree, plan, msp, records),
        }
        for msp in _producer_msps(plan, lane, producers)
    )


def templated_task(plan, lane, label, template):
    brief = plan["briefs"][lane]
    rendered = (
        template.replace("{cluster}", ", ".join(plan["lanes"][lane]["steps"]))
        .replace("{spec}", core.whole_spec(brief["steps"]))
        .replace("{tier}", str(plan["lanes"][lane].get("tier") or ""))
        .replace("{contract}", core.RETURN_CONTRACT)
    )
    return "Lane %d of MSP %s\n%s" % (lane, label, rendered)


def _worker_values(plan, lane, tree, models, run_dir, session_id, template=None):
    brief = plan["briefs"][lane]
    tier = plan["lanes"][lane].get("tier")
    label = tree.get("label") or tree["msp"]
    return {
        "task": brief["text"] if template is None else templated_task(plan, lane, label, template),
        "model": (models or {}).get(tier, ""),
        "tier": tier,
        "worktree": tree["path"],
        "branch": tree["branch"],
        "lane": lane,
        "msp": label,
        "charter": brief.get("charter"),
        "document": brief.get("document"),
        "run_dir": run_dir,
        "session_id": session_id,
        "schema": worker_schema(),
    }


RETRY_NOTE = "\n\nAn earlier attempt at this work failed: %s. Start again from this clone."

OUTSIDE_WRITE_NOTE = (
    " If the obstacle was a file outside your write-set, change that file as well and name it in "
    "your notes: mitosis keeps the write, shows it to the gate and reports it."
)

RETRY_STEM = "retry"

REPAIR_STEM = "repair"


def _attempt_stem(lane, retrying):
    return "%s.%s" % (lane, RETRY_STEM) if retrying else str(lane)


def _retry_note(judged):
    told = (judged.get("returned") or {}).get("notes")
    return (
        RETRY_NOTE % ("%s (%s)" % (judged["reason"], told) if told else judged["reason"])
        + OUTSIDE_WRITE_NOTE
    )


def _with_note(values, note):
    return values if note is None else {**values, "task": values["task"] + note}


def _noting_retry(judged, note):
    if note is None:
        return judged
    earlier = "retried once: " + note.strip().split("\n")[0]
    return {**judged, "reason": "%s; %s" % (judged["reason"], earlier) if judged["reason"] else earlier}


def _landing_message(plan, lane, header):
    steps = plan["lanes"][lane]["steps"]
    tasks = {item.get("name"): item.get("task") for item in plan.get("items") or []}
    subject = "%s: %s" % (
        pr_facts.conventional_prefix(
            (header or {}).get("type") or "chore", (header or {}).get("scope")
        ),
        ", ".join(steps),
    )
    return "%s\n\n%s" % (
        subject,
        "\n".join(pr_facts.first_sentence(tasks.get(name)) for name in steps),
    )


def check_identity(repo):
    for ident in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
        found = git_run(["var", ident], repo)
        if found.returncode != 0:
            raise ConfigError(
                "git has no commit identity in %s, so no Lane could land: %s"
                % (repo, (found.stderr or "").strip() or "git var %s failed" % ident)
            )


def _seeding(seeds, clone):
    if seeds is None:
        return {"seeded": [], "skipped": [], "seconds": 0}
    return isolation.seed_clone(seeds["repo"], seeds["plan"], clone)


def _seed_fields(seeding):
    return {
        "seeded": list(seeding["seeded"]),
        "seed_skipped": list(seeding["skipped"]),
        "seed_seconds": seeding["seconds"],
    }


def _land(plan, lane, tree, record, run_dir, header):
    write_set = plan["briefs"][lane]["write_set"]
    clone = isolation.lane_folders(run_dir, lane)["clone"]
    try:
        landing = isolation.read_lane(
            clone, tree["path"], record["start"], write_set, record.get("seeded") or ()
        )
    except (GitError, OSError) as error:
        return None, "mitosis could not land the Lane from its clone %s: %s" % (clone, error), None
    try:
        isolation.place_entries(tree["path"], landing["entries"])
        commit = commit_paths(
            tree["path"], write_set, _landing_message(plan, lane, header), tree["branch"]
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


def called_verdict(called):
    judged, why = lane_verdict(called["exit"], called["returned"], called["errors"])
    if judged == "ok" or called["first_exit"] is None:
        return judged, why
    return judged, "exit %d with no valid reply; re-asked once: %s" % (called["first_exit"], why)


def _lane_outcome(called):
    judged, why = called_verdict(called)
    limit = None if judged != "failed" else usage_limit_line(called["stdout"], called["stderr"])
    return (judged, why) if limit is None else (LANE_PARKED, "usage limit: %s" % limit)


def _verdict(lane, tree, started, merged, called, session_id, max_output=MAX_OUTPUT):
    state, reason = _lane_outcome(called)
    return lane_record(
        lane,
        tree["msp"],
        state,
        reason=reason,
        exit=called["exit"],
        returned=called["returned"],
        stdout=called["stdout"],
        stderr=called["stderr"],
        merged=merged,
        commit=None,
        started=started,
        finished=now(),
        session_id=session_id,
        output_tail=output_tail(called, max_output),
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


def _landed(plan, lane, tree, record, run_dir, header):
    commit, reason, outside = _land(plan, lane, tree, record, run_dir, header)
    if reason is not None:
        return {**record, "state": "failed", "reason": reason}
    return {**record, **outside, "commit": commit}


def _open_to_the_gate(record):
    return {**record, "replayed": {}}


def _land_batch(plan, msp, tree, batch, held, records, run_dir, settle, header):
    lanes = plan["lanes"]
    landed = {}
    for lane in sorted(batch):
        done = _landed(plan, lane, tree, held[lane], run_dir, header)
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
    concurrency,
    run_dir,
    base,
    models=None,
    prior=None,
    on_lane=None,
    observer=None,
    env=None,
    sandbox=None,
    header=None,
    seeds=None,
    reask=None,
    stopped=None,
    prompt_template=None,
    max_output=MAX_OUTPUT,
):
    check_reply_templates(command, reask)
    check_models(plan, command, models)
    lanes = plan.get("lanes") or []
    edges = lane_edges(plan)
    tree_of = {tree["msp"]: tree for tree in trees}
    lane_dir = os.path.join(run_dir, "lanes")
    os.makedirs(lane_dir, exist_ok=True)
    records = {
        **{
            int(index): record
            for index, record in (prior or {}).items()
            if isinstance(record, dict) and record.get("state") == "ok"
        },
        **dict(stopped or {}),
    }
    held = {
        int(index): {**record, "state": "ok"}
        for index, record in (prior or {}).items()
        if isinstance(record, dict)
        and int(index) not in records
        and record.get("state") == LANE_HELD
        and isolation.clone_ready(isolation.lane_folders(run_dir, int(index)), record)
    }
    ordered = [int(i) for i in plan.get("lane_order") or range(len(lanes))]
    every = ordered + [i for i in range(len(lanes)) if i not in ordered]
    pending = [i for i in every if i not in records and i not in held]
    running = {}
    parked_by = None
    retried = {}
    cap = max(1, int(concurrency))

    def settle(known, lane, record):
        if on_lane is not None:
            on_lane(lane, record)
        return {**known, lane: record}

    with ThreadPoolExecutor(max_workers=cap) as pool:
        while pending or running or held:
            progressed = False
            for msp in sorted({lanes[lane]["msp"] for lane in held}):
                batch = [lane for lane in held if lanes[lane]["msp"] == msp]
                landed, records = _land_batch(
                    plan, msp, tree_of[msp], batch, held, records, run_dir, settle, header
                )
                foreign = _foreign_writes(plan, msp, tree_of[msp], batch, records)
                for lane in batch:
                    records = settle(
                        records,
                        lane,
                        {
                            **(_open_to_the_gate(landed[lane]) if lane in retried else landed[lane]),
                            "foreign_writes": [
                                {**entry, "suspects": suspects}
                                for entry in foreign
                                for suspects in (_suspects(batch, held, entry["path"]),)
                                if lane in suspects
                            ],
                        },
                    )
                    if landed[lane]["state"] == "ok":
                        isolation.discard_landed_folders(isolation.lane_folders(run_dir, lane))
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
                if len(running) >= cap:
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
                awaited = [
                    member
                    for producer_msp in _producer_msps(plan, lane, producers)
                    for member in _msp_lanes(plan, producer_msp)
                ]
                if any(member not in records for member in awaited):
                    waiting = waiting + (lane,)
                    continue
                if any(records[member].get("state") != "ok" for member in awaited):
                    records = settle(
                        records,
                        lane,
                        lane_record(
                            lane,
                            msp,
                            "blocked",
                            reason="dependencies can no longer be satisfied",
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
                files = call_files(lane_dir, _attempt_stem(lane, lane in retried))
                session = core.new_session_id()
                folders = isolation.lane_folders(run_dir, lane)
                values = _worker_values(
                    plan,
                    lane,
                    {**tree, "path": folders["clone"]},
                    models,
                    run_dir,
                    session,
                    prompt_template,
                )
                argv = build_argv(command, _with_note(values, retried.get(lane)))
                asked = {"template": reask, "values": reask_values(values)}
                carried = core.carried_session(argv, session)
                try:
                    start = isolation.cut_clone(tree, folders)
                    seeding = _seeding(seeds, folders["clone"])
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
                    worker_call,
                    argv,
                    asked,
                    folders["clone"],
                    files,
                    _lane_environ(plan, lane, tree["branch"], env),
                    confinement,
                )
                running = {
                    **running,
                    future: (
                        lane,
                        now(),
                        merged,
                        carried,
                        folders["clone"],
                        start,
                        seeding,
                    ),
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
                    lane, started, merged, session, clone, start, seeding = running[future]
                    running = {f: v for f, v in running.items() if f is not future}
                    called = future.result()
                    tree = tree_of[lanes[lane]["msp"]]
                    judged = {
                        **_verdict(lane, tree, started, merged, called, session, max_output),
                        "clone": clone,
                        "start": start,
                        **_seed_fields(seeding),
                    }
                    if judged["state"] == "failed" and lane not in retried:
                        retried = {**retried, lane: _retry_note(judged)}
                        pending = [lane] + pending
                        continue
                    judged = _noting_retry(judged, retried.get(lane))
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


STATE_SCHEMA = "h9-mitosis-state"


def write_state(run_dir, state):
    path = os.path.join(run_dir, STATE_FILE)
    write_json(path, handoff.require(STATE_SCHEMA, state, path, "mitosis"))
    return state


def _logged_order(entry):
    index, record = entry
    finished = record.get("finished")
    return (finished is None, finished or "", index)


def run_log_lines(plan, state):
    entries = sorted(
        ((int(index), record) for index, record in (state.get("lanes") or {}).items()),
        key=_logged_order,
    )
    return tuple(json.dumps({**record, "plan_id": plan["plan_id"]}) for _, record in entries)


def write_run_log(path, plan, state):
    with open(path, "w", encoding="utf-8") as handle:
        handle.writelines(line + "\n" for line in run_log_lines(plan, state))


def load_state(run_dir):
    path = os.path.join(run_dir, STATE_FILE)
    if not os.path.isfile(path):
        return None
    try:
        loaded = read_json(path)
    except ValueError as error:
        raise handoff.HandoffError(
            "%s, written by mitosis, is not JSON, so it cannot meet the owned schema %s: %s"
            % (path, handoff.file_name(STATE_SCHEMA), error)
        ) from error
    return handoff.require(STATE_SCHEMA, loaded, path, "mitosis")


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


def msp_order(plan):
    producers = core.msp_producers(plan)
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


def step_mutable_paths(plan, msp, name):
    return tuple(
        path
        for path in step_implementation_paths(plan, msp, name)
        if not referee.in_test_location(path)
    )


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


def _property_verdict(name, unchanged):
    if name in unchanged:
        return "not-applicable", UNCHANGED_REASON
    return None, None


def msp_properties(plan, msp, unchanged=frozenset()):
    steps = _steps_by_name(plan)
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
        outcome, reason = _property_verdict(name, unchanged)
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
        git([*HOOKS_OFF, "commit", "-q", "--no-verify", "-m", message], tree)
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


def revert_implementation(
    tree, branch, head, base, paths, message=RESTORE_MESSAGE, *, locked_entries
):
    written = tuple(sorted({_norm(path) for path in paths}))
    ref = "refs/heads/" + branch
    moved_from = branch_commit(tree, branch, required=True)
    held = {**_committed_entries(tree, base), **locked_entries}
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


def run_acceptance(command, tree, entry, log_path, env=None, sandbox=None):
    argv = build_argv(command, {"file": entry["file"], "test": entry["test"], "worktree": tree})
    return run_command(argv, tree, log_path, log_path + ".err", env, sandbox)


FAILS_WITH_THE_WORK = "fails against the finished work"


def probe_outcome(with_work, reverted):
    if with_work == ACCEPTANCE_FAILURE_EXIT:
        return "inconclusive", FAILS_WITH_THE_WORK
    if with_work != 0:
        return "inconclusive", "exit %d with the work present, so nothing judged it" % with_work
    if reverted == 0:
        return "inert", "passes with the implementation reverted"
    if reverted == ACCEPTANCE_FAILURE_EXIT:
        return "pass", None
    return (
        "inconclusive",
        "exit %d with the implementation reverted; a test that never ran is not a test that failed"
        % reverted,
    )


def with_guards(judged):
    proven = frozenset(entry["step"] for entry in judged if entry["outcome"] == "pass")
    return tuple(
        {**entry, "outcome": "not-applicable", "reason": core.GUARD_REASON}
        if entry["outcome"] == "inert" and entry["step"] in proven
        else entry
        for entry in judged
    )


def gate_outcome(properties):
    outcomes = [entry["outcome"] for entry in properties]
    for candidate in ("weak", "inert", "inconclusive", "pass"):
        if candidate in outcomes:
            return candidate
    return "not-applicable"


GATE_HELD = "gate-inconclusive"


def fails_with_the_work(entry):
    return entry.get("with_work") == ACCEPTANCE_FAILURE_EXIT


def failing_properties(result):
    return tuple(entry for entry in (result or {}).get("properties") or () if fails_with_the_work(entry))


def cannot_run_with_the_work(entry):
    code = entry.get("with_work")
    return isinstance(code, int) and not isinstance(code, bool) and code not in (0, ACCEPTANCE_FAILURE_EXIT)


def needs_repair(entry):
    return fails_with_the_work(entry) or cannot_run_with_the_work(entry)


def unrunnable_properties(result):
    return tuple(
        entry for entry in (result or {}).get("properties") or () if cannot_run_with_the_work(entry)
    )


NEVER_JUDGED = "the gate never judged this property, so it proves nothing yet"


def _resolved_property(entry):
    timed = {**entry, "seconds": 0 if entry.get("seconds") is None else entry["seconds"]}
    if entry["outcome"] is not None:
        return timed
    return {**timed, "outcome": "inconclusive", "reason": entry.get("reason") or NEVER_JUDGED}


def _gate_result(properties, implementation, commit, reverted):
    resolved = [_resolved_property(entry) for entry in properties]
    outcome = gate_outcome(resolved)
    return {
        "outcome": outcome,
        "blocks": any(fails_with_the_work(entry) for entry in resolved),
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
    paths = step_mutable_paths(plan, msp, entry["step"])
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


def structural_mutants(plan, msp, tree, base, commit, entry):
    paths = step_mutable_paths(plan, msp, entry["step"])
    if not paths:
        return []
    pairs, texts = _step_added(tree, base, commit, paths)
    held = dict(texts)

    def read(path):
        return _decoded(held[path])

    return mutants.structural(dict(pairs), read)


def _run_mutant(command, folder, entry, mutant, log_path, env, reset, sandbox=None):
    written = reset()
    path = mutant["file"]
    mutated = mutants.apply(_read_text(folder, path), mutant)
    if mutated is None:
        return None, written
    clear_caches_beside(folder, (path,))
    _write_text(folder, path, mutated)
    return run_acceptance(command, folder, entry, log_path, env, sandbox), written


def _mutant_place(mutant):
    return {"file": mutant["file"], "line": mutant["line"], "op": mutant["op"]}


def _timed(call):
    started = time.monotonic()
    result = call()
    return result, time.monotonic() - started


def _first_kill(candidates, attempt, log_name):
    tried = ()
    written = ()
    seconds = 0
    for mutant in candidates:
        (ran, left), spent = _timed(
            lambda mutant=mutant: attempt(mutant, log_name(len(tried)))
        )
        seconds = seconds + spent
        written = written + left
        if ran is None:
            continue
        tried = tried + (mutant,)
        if ran != 0:
            return mutant, tried, written, seconds
    return None, tried, written, seconds


WEAK_REASON = "no mutant of the work fails it: it still passes when %s:%d changes by %s"

WEAK_PLACE = re.compile(
    "^" + re.escape(WEAK_REASON.split("%s", 1)[0]) + r"(?P<file>.+):(?P<line>\d+) changes by (?P<op>.+)$"
)


def _weak(entry, mutant, record, coverage, seconds):
    return {
        **entry,
        "seconds": seconds,
        "outcome": "weak",
        "reason": WEAK_REASON % (mutant["file"], mutant["line"], mutant["op"]),
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
    env,
    folder,
    reset,
    trace=None,
    seen=None,
    sandbox=None,
):
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
    def attempt(mutant, name):
        return _run_mutant(
            command, folder, entry, mutant, os.path.join(log_dir, name), env, reset, sandbox
        )

    killer, tried, left, spent = _first_kill(
        sampled, attempt, lambda count: "%d-mutant-%d.log" % (index, count)
    )
    seconds = seconds + spent
    written = written + left
    attempts = tried
    if killer is None and tried:
        killer, extra, left, spent = _first_kill(
            structural_mutants(plan, msp, tree, base, commit, entry),
            attempt,
            lambda count: "%d-structural-%d.log" % (index, count),
        )
        seconds = seconds + spent
        written = written + left
        attempts = tried + extra
    if killer is not None:
        return (
            {
                **entry,
                "mutants": {"tried": len(attempts), "killed": _mutant_place(killer)},
                "mutation": "killed",
                "coverage": coverage,
                "seconds": seconds,
            },
            written,
            seen,
        )
    record = {"tried": len(attempts), "killed": None}
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
    return _weak(entry, tried[0], record, coverage, seconds), written, seen


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
    env=None,
    repo=None,
    setup=None,
    head=None,
    replayed=None,
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
            env,
            commit,
            properties,
            folder,
            repo,
            setup,
            replayed or {},
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


MUTATED_OUTCOMES = ("pass", "inert")


def guarded_by_mutation(inert, mutated):
    if mutated.get("mutation") != "killed":
        return {**inert, "mutants": mutated["mutants"]}
    place = mutated["mutants"]["killed"]
    return {
        **mutated,
        "outcome": "pass",
        "reason": core.GUARD_KILLED_REASON % (place["file"], place["line"], place["op"]),
    }


def _gate_runs(
    plan,
    msp,
    tree,
    base,
    command,
    log_dir,
    env,
    commit,
    properties,
    folder,
    repo,
    setup,
    replayed,
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
        else _gate_setup(setup, folder, top, log_dir, env, sandbox)
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
        if with_work[index] == 0
    }
    without = {index: ran for index, (_, (ran, _)) in without_runs.items()}
    judged = ()
    for index, entry in enumerate(properties):
        if index not in with_work:
            judged = judged + (entry,)
            continue
        outcome, reason = probe_outcome(with_work[index], without.get(index))
        judged = judged + (
            {
                **entry,
                "outcome": outcome,
                "reason": reason,
                "with_work": with_work[index],
                "reverted": without.get(index),
                "seconds": with_work_runs[index][1][1]
                + (without_runs[index][1][1] if index in without_runs else 0),
            },
        )
    verdicts = ()
    seen = {}
    for index, entry in enumerate(with_guards(judged)):
        if index not in with_work or entry["outcome"] not in MUTATED_OUTCOMES:
            verdicts = verdicts + ((entry, ()),)
            continue
        mutated, left, seen = mutant_verdict(
            plan,
            msp,
            tree,
            base,
            commit,
            command,
            index,
            entry,
            log_dir,
            env,
            folder,
            lambda: reset(with_work_tree),
            traces[index],
            seen,
            sandbox,
        )
        toothed = guarded_by_mutation(entry, mutated) if entry["outcome"] == "inert" else mutated
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


def locked_record(repo, branch, commit, locked, holders=None):
    files = ()
    for path in locked:
        held = (holders or {}).get(path, commit)
        shown = git_run(
            ["cat-file", "blob", "%s:%s" % (held, path)], repo, raw=True, env=PINNED_GIT
        )
        if shown.returncode != 0:
            raise ConfigError(
                "--charter locks %s, which %s does not hold; commit the locked checks on it "
                "before the build" % (path, branch)
            )
        files = files + ((path, shown.stdout),)
    record = {
        "locked": list(locked),
        "receipt_lock": referee.receipt_lock(files),
        "commit": commit,
    }
    return handoff.require("k2-locked", record, "the lock record", "mitosis")


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


def _gate_setup(setup, folder, top, log_dir, env, sandbox=None):
    out = os.path.join(log_dir, "gate-setup.log")
    try:
        code = run_command(
            build_argv(setup, {"worktree": folder}),
            folder,
            out,
            out + ".err",
            env,
            sandbox,
        )
    except OSError as error:
        raise GateSetupError("the --gate-setup command could not start in %s: %s" % (folder, error))
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


def referee_writes(plan, tree, head, base, held=None):
    locked = tuple(plan.get("locked") or ())
    marked = _referee_marked(_tree_changes(tree, base, head), locked)
    if not held:
        return sorted(marked)
    current = _committed_entries(tree, head)
    judged = frozenset(
        path for path in marked if _norm(path) not in held or current.get(_norm(path)) != held[_norm(path)]
    )
    judged_names = frozenset(_norm(path) for path in judged)
    moved = frozenset(
        path for path, entry in held.items() if path not in judged_names and current.get(path) != entry
    )
    return sorted(judged | moved)


def _declaring_msps(plan, path, msp):
    normalized = _norm(path)
    return tuple(
        index
        for index, entry in enumerate(plan.get("msps") or [])
        if index != msp and normalized in {_norm(f) for f in entry.get("files") or ()}
    )


def _plan_msp_rank(plan):
    items = plan.get("items") or []
    index_of = {item.get("name"): index for index, item in enumerate(items)}
    msps = tuple(
        tuple(index_of[name] for name in entry.get("steps") or () if name in index_of)
        for entry in plan.get("msps") or []
    )
    return core.msp_rank(items, msps)


def shared_overlaps(plan, msp, undeclared):
    shared = core.shared_matcher(plan.get("shared_files") or ())
    found = tuple(
        (path, others)
        for path in undeclared
        if shared(_norm(path))
        for others in (_declaring_msps(plan, path, msp),)
        if others
    )
    if not found:
        return ()
    rank = _plan_msp_rank(plan)
    return tuple(
        {
            "path": path,
            "also_in": [_label(plan, index) for index in others],
            "integration_order": [
                _label(plan, index) for index in rank if index in others or index == msp
            ],
        }
        for path, others in found
    )


def reconcile(plan, msp, tree, head, base, producer_heads=(), held=None):
    changed = changed_files(tree, head, base, producer_heads)
    declared = sorted({_norm(f) for f in plan["msps"][msp].get("files") or ()})
    outside = [path for path in changed if _norm(path) not in declared]
    overlaps = shared_overlaps(plan, msp, outside)
    overlapping = frozenset(entry["path"] for entry in overlaps)
    undeclared = [path for path in outside if path not in overlapping]
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
    written = referee_writes(plan, tree, head, base, held)
    return {
        "changed": changed,
        "declared": declared,
        "undeclared": undeclared,
        "unwritten": unwritten,
        "untouched": untouched,
        "crossing": list(crossing),
        "referee": written,
        "fatal": bool(crossing or written),
        **({"shared_overlaps": list(overlaps)} if overlaps else {}),
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
        for producer in core.msp_producers(plan).get(msp, ())
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


PR_TYPES = ("feat", "fix", "refactor", "docs", "test", "chore", "perf", "ci")

PR_DOORS = ("one-way", "two-way")

SECTION_LIMIT = 8


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
    push=True,
    foreign=(),
    proven_head=None,
    env=None,
    render_command="pull-request",
    pushed_before=None,
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
    earlier = (pushed_before or {}).get("pushed")
    if earlier is not None and not git_ok(["merge-base", "--is-ancestor", earlier, head], tree):
        raise ShipError(
            "%s is at %s, which does not descend from %s that mitosis pushed; mitosis never "
            "force-pushes, so it refused to push it" % (branch, head[:12], earlier[:12])
        )
    os.makedirs(log_dir, exist_ok=True)
    facts = pr_facts.facts(
        header, plan, msp, gate_result, gate_error, findings, exception, foreign, proven_head
    )
    try:
        title, body = pr_facts.render(
            render_command, facts, os.path.join(log_dir, "fields.json"), process_environ(env, tree)
        )
    except pr_facts.RenderError as error:
        raise ShipError(str(error))
    git(["push", "-q", "-u", remote, branch], tree)
    pushed = {**held, "pushed": head, "title": title, "body": body, "unchanged": False}
    if pr_command is None:
        return pushed
    if (pushed_before or {}).get("pull_request"):
        return {**pushed, "pull_request": pushed_before["pull_request"]}
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
    code = run_command(argv, tree, out, err, env)
    if code != 0:
        detail = last_line(err) or last_line(out) or ""
        raise ShipError(
            "the pull-request command exited %d for %s: %s" % (code, branch, detail), pushed
        )
    return {**pushed, "pull_request": pull_request_url(out, branch, pushed)}


def pull_request_url(out, branch, pushed):
    printed = last_line(out)
    if printed is None:
        raise ShipError(
            "the pull-request command exited 0 for %s but printed nothing, so no pull request "
            "URL names what it opened" % branch,
            pushed,
        )
    found = handoff.errors_at(STATE_SCHEMA, "#/$defs/ship/properties/pull_request", printed)
    if found:
        raise ShipError(
            "the pull-request command exited 0 for %s but its last line %s is not a pull request "
            "URL: %s" % (branch, json.dumps(printed), "; ".join(found)),
            pushed,
        )
    return printed


def _reconcile_clean(findings, state=None):
    return isinstance(findings, dict) and not (
        (findings.get("unwritten") and state == MSP_UNCHANGED)
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
        and _reconcile_clean(record.get("reconcile"), record.get("state"))
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
            settings.get("env"),
            settings.get("repo"),
            settings.get("gate_setup"),
            head,
            replayed,
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
        "gate_setup": settings.get("gate_setup"),
        "replayed": replayed,
    }
    return hashlib.sha256(json.dumps(named, sort_keys=True).encode("utf-8")).hexdigest()


REPAIR_NOTE = (
    "\n\nThe finished build fails %s against this Lane's work. Change the work until each passes, "
    "and leave the test as it is. If the failure comes from the environment and no change to the "
    "code can fix it, say so in your notes and report failed.\n\nOutput of the failing run:\n%s"
)

UNRUNNABLE_NOTE = (
    "\n\nThe acceptance command could not run %s against this Lane's work, so nothing judged it. "
    "The command is given the test file and the test's name separately; change the test file so "
    "each test runs under exactly the name given, and leave the name as it is. If the failure "
    "comes from the environment and no change to the code can fix it, say so in your notes and "
    "report failed.\n\nOutput of the run:\n%s"
)


WEAK_NOTE = (
    "\n\nThe gate changed %s in this Lane's work and %s still passed, so the test cannot tell "
    "working code from broken code. Change the test so it fails on that change and passes on the "
    "work, and keep the work as it is unless it is wrong.\n\nOutput of the run:\n%s"
)

INERT_NOTE = (
    "\n\n%s passes with this Lane's work reverted and no change to the work makes it fail, so it "
    "cannot tell the work from its absence. Change it so it fails without the work and passes with "
    "it.\n\nOutput of the run:\n%s"
)

RETURNED_OUTCOMES = ("weak", "inert")


def _gate_log_tail(settings, msp, entry):
    log = os.path.join(settings["run_dir"], "gate", "msp-%d" % msp, "%s-with-work.log" % entry)
    found = tuple(
        text
        for path in (log, log + ".err")
        if os.path.isfile(path)
        for text in (_read_tail(path),)
        if text
    )
    return "\n".join(found)


def _read_tail(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()[-MAX_OUTPUT:]


def _owned_unproven(plan, lane, entry):
    if entry.get("outcome") not in RETURNED_OUTCOMES:
        return False
    target = _norm(entry["file"])
    written = {_norm(path) for path in plan["briefs"][lane]["write_set"]}
    return target in written and target not in {_norm(path) for path in plan.get("locked") or ()}


def _failing_by_lane(plan, gate_result):
    owners = {step: lane for lane, entry in enumerate(plan["lanes"]) for step in entry["steps"]}
    every = tuple((gate_result or {}).get("properties") or ())
    grouped = {}
    for position, entry in enumerate(every):
        lane = owners.get(entry.get("step"))
        if lane is not None and (needs_repair(entry) or _owned_unproven(plan, lane, entry)):
            grouped = {**grouped, lane: grouped.get(lane, ()) + ((position, entry),)}
    return grouped


def _survivor(entry):
    matched = WEAK_PLACE.match(entry.get("reason") or "")
    if matched is None:
        return "the work"
    return "%s:%s by %s" % (matched.group("file"), matched.group("line"), matched.group("op"))


def _unproven_note(entry, log):
    key = property_key(entry["file"], entry["test"])
    if entry["outcome"] == "weak":
        return WEAK_NOTE % (_survivor(entry), key, log)
    return INERT_NOTE % (key, log)


def _repair_note(settings, msp, failing):
    notes = ()
    for note, wanted in ((REPAIR_NOTE, fails_with_the_work), (UNRUNNABLE_NOTE, cannot_run_with_the_work)):
        chosen = tuple((position, entry) for position, entry in failing if wanted(entry))
        if chosen:
            names = ", ".join(property_key(entry["file"], entry["test"]) for _, entry in chosen)
            logs = "\n".join(_gate_log_tail(settings, msp, position) for position, _ in chosen)
            notes = notes + (note % (names, logs[-MAX_OUTPUT:]),)
    unproven = tuple(
        _unproven_note(entry, _gate_log_tail(settings, msp, position)[-MAX_OUTPUT:])
        for position, entry in failing
        if entry.get("outcome") in RETURNED_OUTCOMES and not needs_repair(entry)
    )
    return "".join(notes + unproven)


def _repaired_lane(plan, lane, tree, settings, note):
    run_dir, worker = settings["run_dir"], settings["worker"]
    folders = isolation.lane_folders(run_dir, lane)
    session = core.new_session_id()
    started = now()
    values = _worker_values(
        plan, lane, {**tree, "path": folders["clone"]}, worker["models"], run_dir, session, worker["template"]
    )
    argv = build_argv(worker["command"], _with_note(values, note))
    try:
        start = isolation.cut_clone(tree, folders)
        seeding = _seeding(settings.get("seeds"), folders["clone"])
        confinement = isolation.worker_confinement(folders, settings.get("sandbox"), settings.get("env"))
    except (GitError, OSError) as error:
        return lane_record(lane, tree["msp"], "failed", reason="mitosis could not repair the Lane: %s" % error)
    called = worker_call(
        argv,
        {"template": settings["reask_command"], "values": reask_values(values)},
        folders["clone"],
        call_files(os.path.join(run_dir, "lanes"), "%d.%s" % (lane, REPAIR_STEM)),
        _lane_environ(plan, lane, tree["branch"], settings.get("env")),
        confinement,
    )
    judged = {
        **_verdict(lane, tree, started, [], called, core.carried_session(argv, session), worker["max_output"]),
        "clone": folders["clone"],
        "start": start,
        **_seed_fields(seeding),
    }
    if judged["state"] != "ok":
        return judged
    landed = _landed(plan, lane, tree, judged, run_dir, worker["header"])
    if landed["state"] == "ok":
        isolation.discard_landed_folders(folders)
    return landed


OUTSIDE_PATH_KEYS = ("unreplayed", "ignored")

NO_REPAIR = {"state": None, "landed": False, "parked": None, "failed": ()}


def _keeping_outside_writes(earlier, repaired):
    return {
        **repaired,
        "replayed": {**(repaired.get("replayed") or {}), **(earlier.get("replayed") or {})},
        **{
            key: sorted(set(earlier.get(key) or ()) | set(repaired.get(key) or ()))
            for key in OUTSIDE_PATH_KEYS
        },
    }


def _carried_foreign(earlier, found):
    new = frozenset(entry["path"] for entry in found)
    return [entry for entry in earlier if entry["path"] not in new] + list(found)


def _with_repair(plan, msp, tree, lanes, lane, repaired):
    claimed = plan["briefs"][lane]["write_set"]
    freed = {
        key: record
        if int(key) == lane or plan["lanes"][int(key)]["msp"] != msp
        else isolation.released(record, claimed)
        for key, record in lanes.items()
    }
    foreign = _foreign_writes(plan, msp, tree, [lane], {int(key): record for key, record in freed.items()})
    earlier = freed[str(lane)]
    return {
        **freed,
        str(lane): {
            **_keeping_outside_writes(earlier, repaired),
            "foreign_writes": _carried_foreign(
                earlier.get("foreign_writes") or (),
                [{**entry, "suspects": [lane]} for entry in foreign],
            ),
        },
    }


def _repair_lanes(plan, msp, tree, state, settings, gated):
    lanes = state["lanes"]
    outcomes = ()
    for lane, failing in sorted(_failing_by_lane(plan, gated["gate"]).items()):
        record = _repaired_lane(plan, lane, tree, settings, _repair_note(settings, msp, failing))
        outcomes = outcomes + ((lane, record),)
        if record["state"] == "ok":
            lanes = _with_repair(plan, msp, tree, lanes, lane, record)
    return {
        "state": {**state, "lanes": lanes},
        "landed": any(record["state"] == "ok" for _, record in outcomes),
        "parked": next(
            (record["reason"] for _, record in outcomes if record["state"] == LANE_PARKED), None
        ),
        "failed": tuple(
            "the repair of Lane %d failed: %s" % (lane, record["reason"])
            for lane, record in outcomes
            if record["state"] == "failed"
        ),
    }


REPAIR_PARKED = "the repair of a failing test stopped (%s); pass --resume to finish it"

PRODUCER_REPAIR_CONFLICT = "a producer's repair would not merge into this MSP: %s"


def _merged_producer_repairs(tree, producers, trees, base):
    tips = tuple(
        {"branch": trees[producer]["branch"]}
        for producer in producers
        if branch_exists(tree["path"], trees[producer]["branch"])
    )
    return merge_producers(tree["path"], tree["branch"], tips, base)[1]


def _head_with_producers(plan, msp, tree, producers, trees, base):
    head, commit_error = _committed_head(plan, msp, tree)
    if commit_error is not None:
        return None, commit_error, None
    conflict = _merged_producer_repairs(tree, producers, trees, base)
    if conflict is not None:
        return None, None, {"state": MSP_BLOCKED, "reason": PRODUCER_REPAIR_CONFLICT % conflict}
    return branch_commit(tree["path"], tree["branch"], required=True), None, None


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
            reached_referee_held(tree["path"], _judged_head(tree, head), settings),
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
            plan,
            tree["path"],
            _judged_head(tree, head),
            settings["base_commit"],
            reached_referee_held(tree["path"], _judged_head(tree, head), settings),
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
            judged = _judged_head(tree, head)
            revert_implementation(
                tree["path"],
                tree["branch"],
                judged,
                settings["base_commit"],
                landed,
                "revert(%s): restore the referee paths a Worker wrote" % _label(plan, msp),
                locked_entries=reached_referee_held(tree["path"], judged, settings) or {},
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


def _ship_stage(plan, msp, tree, base, exception, record, settings, foreign=(), pushed_before=None):
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
            push=settings["push"],
            foreign=foreign,
            proven_head=record.get("proven_head"),
            env=settings.get("env"),
            render_command=settings["render_command"],
            pushed_before=pushed_before,
        )
    except ShipError as error:
        return {"state": "ship-failed", "reason": str(error), "ship": error.shipped or pushed_before}
    except (OSError, GitError, subprocess.SubprocessError, ValueError) as error:
        return {"state": "ship-failed", "reason": str(error), "ship": pushed_before}
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
        if prop.get("file") and prop.get("test") and not core.is_guard(prop)
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
    except (OSError, handoff.HandoffError) as error:
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


def _held_back(gated, branch, hook_refusal=None):
    if hook_refusal is not None:
        return {
            "state": MSP_GATE_FAILED,
            "reason": "the repository's pre-commit hook refused the MSP's whole change, so %s was "
            "committed and not pushed: %s" % (branch, hook_refusal),
        }
    failing = failing_properties(gated["gate"])
    if not failing:
        return None
    return {
        "state": MSP_GATE_FAILED,
        "reason": "%s failed against the finished work, so %s was committed and not pushed"
        % (", ".join(property_key(entry["file"], entry["test"]) for entry in failing), branch),
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


def _pushed_ship(record):
    shipped = record.get("ship") or {}
    return shipped if shipped.get("pushed") else None


def _finish_msp(plan, msp, trees, producers, state, settings, repairable=True):
    run_dir = settings["run_dir"]
    tree = trees[msp]
    prior = state["msps"].get(str(msp)) or {}
    state = {
        **state,
        "msps": {
            **state["msps"],
            str(msp): {key: prior[key] for key in LOCK_KEYS if key in prior},
        },
        "stacking_exceptions": [
            entry for entry in state["stacking_exceptions"] if entry.get("msp") != msp
        ],
    }
    block = _lane_block(plan, msp, state) or _producer_block(plan, msp, state, producers)
    replayed = isolation.msp_replayed(plan, msp, state["lanes"])
    unreplayed = isolation.msp_unreplayed(plan, msp, state["lanes"])
    state = write_state(run_dir, _msp_record(state, msp, replayed=replayed))
    head, commit_error, conflict = (
        (None, None, None)
        if block is not None
        else _head_with_producers(plan, msp, tree, producers, trees, settings["base_commit"])
    )
    block = block or conflict
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
    refused = (
        msp_hook_refusal(
            tree["path"],
            settings["base_commit"],
            head,
            os.path.join(run_dir, "gate", "hook-msp-%d" % msp),
            settings.get("env"),
            settings.get("sandbox"),
        )
        if judged_now
        else None
    )
    held_back = _held_back(gated, tree["branch"], refused) if judged_now else None
    unrunnable = judged_now and refused is None and bool(unrunnable_properties(gated["gate"]))
    returned = judged_now and refused is None and bool(_failing_by_lane(plan, gated["gate"]))
    if held_back is not None or unrunnable or returned:
        owed = recorded_gate is None or prior.get("state") != MSP_GATE_FAILED
        repair = (
            _repair_lanes(plan, msp, tree, state, settings, gated)
            if repairable and refused is None and owed
            else NO_REPAIR
        )
        if repair["parked"] is not None:
            return write_state(
                run_dir,
                _msp_record(
                    repair["state"],
                    msp,
                    state=MSP_PARKED,
                    reason=REPAIR_PARKED % repair["parked"],
                ),
            )
        if repair["landed"]:
            return _finish_msp(plan, msp, trees, producers, repair["state"], settings, repairable=False)
    if held_back is not None:
        return write_state(
            run_dir,
            _msp_record(
                state,
                msp,
                **{
                    **held_back,
                    "reason": "; ".join((held_back["reason"], *repair["failed"])),
                },
            ),
        )
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
        _pushed_ship(prior),
    )
    return write_state(run_dir, _msp_record(state, msp, **shipped))


LOCK_MESSAGE = "test: lock the acceptance checks"

LOCK_KEYS = ("lock", "referee")

LOCK_DIR = "lock"

LOCK_INDEX = "index"

ALREADY_LOCKED = (
    "the locked checks %s are already on the feature branch at %s, so no check-author ran"
)

LOCKED_BY_AUTHOR = (
    "the %s Step %s wrote %s, each locked test passes with its probe, each Step holds one that "
    "fails on the feature base without the build, and %s locks them as the first commit of "
    "MSP %s's branch"
)

GUARDS_AT_THE_LOCK = (
    "; %s pass on the feature base without the build and guard behaviour that must stay the same"
)

PASSES_WITHOUT_BUILD = (
    "%s passes on the feature base without the build, so it cannot tell the work from its absence"
)

FAILS_WITH_PROBE = (
    "%s fails with the check-author's probe, so no build inside the probe files can be shown "
    "to pass it"
)

CANNOT_RUN = "%s could not run on the feature base with its locked slice: exit %d"


def lock_record(state, reason, commit=None, session_id=None, **fields):
    return {
        "state": state,
        "reason": reason,
        "commit": commit,
        "session_id": session_id,
        "probes": [],
        "undeclared": [],
        **fields,
    }


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


def _lock_values(entry, folders, feature_branch, models, run_dir, session_id):
    brief = entry["brief"]
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
        "schema": worker_schema(),
    }


def _lock_name(entry):
    return "%s-%d" % (LOCK_DIR, entry["msp"])


def _check_author_environ(entry, plan_id, branch, env):
    steps = list(entry["steps"])
    return worker_environ(
        env,
        steps,
        entry["slice"],
        cluster_label(steps, entry["msp"]),
        plan_id,
        core.LOCK_TIER,
        branch,
    )


def _dispatch_check_author(entry, settings, command, models, sandbox, base, note=None):
    run_dir = settings["run_dir"]
    folders = isolation.lock_folders(run_dir, entry["msp"])
    lane_dir = os.path.join(run_dir, "lanes")
    os.makedirs(lane_dir, exist_ok=True)
    files = call_files(lane_dir, _attempt_stem(_lock_name(entry), note is not None))
    session = core.new_session_id()
    values = _lock_values(entry, folders, settings["feature_branch"], models, run_dir, session)
    argv = build_argv(command, _with_note(values, note))
    asked = {"template": settings["reask_command"], "values": reask_values(values)}
    carried = core.carried_session(argv, session)
    seeded = {}
    try:
        start = isolation.cut_clone(
            {"path": settings["repo"], "branch": settings["feature_branch"]}, folders
        )
        if start != base:
            git(["reset", "-q", "--hard", base], folders["clone"])
        seeded = _seed_fields(_seeding(settings.get("seeds"), folders["clone"]))
        confinement = isolation.worker_confinement(folders, sandbox, settings["env"])
    except (GitError, OSError) as error:
        return lock_record(
            "failed",
            "mitosis could not prepare the clone %s of the %s Step %s: %s"
            % (folders["clone"], core.CHECK_AUTHOR, entry["name"], error),
            session_id=carried,
            **seeded,
        )
    called = worker_call(
        argv,
        asked,
        folders["clone"],
        files,
        _check_author_environ(
            entry, settings["plan_id"], settings["feature_branch"], settings["env"]
        ),
        confinement,
    )
    return _check_author_outcome(entry, called, carried, folders, seeded)


def _check_author_outcome(entry, called, carried, folders, seeded):
    judged, why = called_verdict(called)
    ran = {
        "stdout": called["stdout"],
        "stderr": called["stderr"],
        "returned": called["returned"],
        "clone": folders["clone"],
        **seeded,
    }
    if judged == "ok":
        return lock_record("ok", None, session_id=carried, **ran)
    limit = usage_limit_line(called["stdout"], called["stderr"])
    if limit is not None:
        return lock_record(
            MSP_PARKED,
            "the %s Step %s stopped at a usage limit: %s" % (core.CHECK_AUTHOR, entry["name"], limit),
            session_id=carried,
            **ran,
        )
    return lock_record(
        "failed",
        "the %s Step %s failed: %s" % (core.CHECK_AUTHOR, entry["name"], why),
        session_id=carried,
        **ran,
    )


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


def _step_checks(plan, paths):
    locked = frozenset(referee.normalised(path) for path in paths)
    return tuple(
        tuple(
            {"file": entry["file"], "test": entry["test"]}
            for entry in item.get("acceptance") or ()
            if referee.normalised(entry["file"]) in locked
        )
        for item in plan.get("items") or ()
    )


def _distinct(checks):
    return tuple(check for position, check in enumerate(checks) if check not in checks[:position])


def _locked_checks(plan, paths):
    return _distinct(tuple(check for checks in _step_checks(plan, paths) for check in checks))


def _passing_alone(steps, passing):
    return _distinct(
        tuple(
            check
            for checks in steps
            if all(check in passing for check in checks)
            for check in checks
        )
    )


def _named_check(check):
    return "%s::%s" % (check["file"], check["test"])


def _without_probe_fault(check, code):
    if code in (0, ACCEPTANCE_FAILURE_EXIT):
        return None
    return CANNOT_RUN % (_named_check(check), code)


def _with_probe_fault(check, code):
    if code == 0:
        return None
    return FAILS_WITH_PROBE % _named_check(check)


def _probe_target(without, probes):
    kept = {path: entry for path, entry in without.items() if path not in probes}
    return {
        **kept,
        **{path: (mode, oid, None) for path, (mode, oid) in probes.items() if oid is not None},
    }


def _lock_folder(settings, entry):
    return os.path.join(settings["run_dir"], LOCK_DIR, "msp-%d" % entry["msp"])


def _lock_judgement(plan, entry, settings, base, stored, probes):
    repo = settings["repo"]
    locked = tuple(plan.get("locked") or ())
    env = settings["env"]
    without = {
        **_expanded(repo, base, base),
        **{path: (mode, oid, None) for path, (mode, oid) in stored.items()},
    }
    with_probe = _probe_target(without, probes)
    log_dir = _lock_folder(settings, entry)
    os.makedirs(log_dir, exist_ok=True)
    _remove_gate_folders(log_dir)
    folder = tempfile.mkdtemp(prefix=GATE_FOLDER_PREFIX, dir=log_dir)
    try:
        confinement = isolation.gate_confinement(settings.get("sandbox"), log_dir, folder)
        reset_gate_folder(repo, folder, without, (), locked)
        top = frozenset(_parts(path)[0] for path in with_probe)
        setup = settings["gate_setup"]
        kept = (
            _link_dependencies(repo, folder, top)
            if setup is None
            else _gate_setup(setup, folder, top, log_dir, env, confinement)
        )

        def accepted(position, check, target, name):
            reset_gate_folder(repo, folder, target, kept, locked)
            return run_acceptance(
                settings["acceptance_command"],
                folder,
                check,
                os.path.join(log_dir, "%d-%s.log" % (position, name)),
                env,
                confinement,
            )

        faults = ()
        passing = ()
        for position, check in enumerate(_locked_checks(plan, entry["slice"])):
            without_build = accepted(position, check, without, "without-probe")
            fault = _without_probe_fault(check, without_build)
            if fault is None:
                fault = _with_probe_fault(check, accepted(position, check, with_probe, "with-probe"))
            faults = faults if fault is None else faults + (fault,)
            passing = passing + (check,) if without_build == 0 else passing
        unproven = _passing_alone(_step_checks(plan, entry["slice"]), passing)
        return (
            faults + tuple(PASSES_WITHOUT_BUILD % _named_check(check) for check in unproven),
            tuple(check for check in passing if check not in unproven),
        )
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        isolation.release_gate(isolation.gate_folders(log_dir))


def _removed(path):
    if os.path.lexists(path):
        os.remove(path)


def _lock_commit(settings, entry, base, stored):
    repo = settings["repo"]
    index = os.path.join(settings["run_dir"], LOCK_DIR, "%s-%d" % (LOCK_INDEX, entry["msp"]))
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
    return git(["commit-tree", tree, "-p", base, "-m", LOCK_MESSAGE], repo)


def _guards_note(guards):
    return GUARDS_AT_THE_LOCK % ", ".join(_named_check(check) for check in guards) if guards else ""


def _failed_lock(record, reason):
    return {**record, "state": "failed", "reason": reason}


def _probe_writes(clone, repo, base, probe, seeded):
    held = _committed_entries(repo, base)
    found = isolation.write_set_entries(clone, probe, seeded)
    return {
        path: entry
        for path, entry in found.items()
        if (entry is None and path in held)
        or (
            entry is not None
            and (
                path not in held
                or held[path][0] != entry[0]
                or _object_id(entry[1], held[path][1]) != held[path][1]
            )
        )
    }


def _stored_probes(repo, writes):
    stored = _stored_entries(repo, {path: entry for path, entry in writes.items() if entry is not None})
    return {
        path: stored[_norm(path)] if entry is not None else (None, None)
        for path, entry in writes.items()
    }


def _landed_lock(plan, entry, settings, record, base):
    repo = settings["repo"]
    slice_paths = tuple(entry["slice"])
    probe = tuple(path for path in core.probe_files(plan, entry["msp"]) if path not in slice_paths)
    clone = record["clone"]
    try:
        seeded = record.get("seeded") or ()
        entries = isolation.write_set_entries(clone, slice_paths, seeded)
        writes = _probe_writes(clone, repo, base, probe, seeded)
        outside = isolation.outside_writes(
            clone, repo, _committed_entries(repo, base), slice_paths + probe, seeded
        )
    except (GitError, OSError) as error:
        return _failed_lock(
            record,
            "mitosis could not read the clone %s of the %s Step %s: %s"
            % (clone, core.CHECK_AUTHOR, entry["name"], error),
        )
    noted = {
        **record,
        "probes": sorted(writes),
        "undeclared": sorted(
            set(outside["writes"]) | set(outside["deletes"]) | set(outside["unreplayed"])
        ),
        "ignored": list(outside["ignored"]),
    }
    unwritten = [path for path, found in sorted(entries.items()) if found is None]
    if unwritten:
        return _failed_lock(
            noted,
            "the %s Step %s left %s unwritten"
            % (core.CHECK_AUTHOR, entry["name"], ", ".join(unwritten)),
        )
    try:
        stored = _stored_entries(repo, entries)
        probes = _stored_probes(repo, writes)
        faults, guards = _lock_judgement(plan, entry, settings, base, stored, probes)
    except GateSetupError as error:
        return _failed_lock(noted, str(error))
    except (GitError, OSError) as error:
        return _failed_lock(noted, "mitosis could not run the locked checks: %s" % error)
    if faults:
        return _failed_lock(noted, "; ".join(faults))
    try:
        commit = _lock_commit(settings, entry, base, stored)
    except (GitError, OSError) as error:
        return _failed_lock(
            noted, "mitosis could not commit the locked checks of MSP %s: %s" % (entry["label"], error)
        )
    return {
        **noted,
        "state": "ok",
        "reason": LOCKED_BY_AUTHOR
        % (core.CHECK_AUTHOR, entry["name"], ", ".join(slice_paths), commit[:12], entry["label"])
        + _guards_note(guards),
        "commit": commit,
    }


def _judged_lock(plan, entry, settings, record, base):
    landed = _landed_lock(plan, entry, settings, record, base)
    if landed["state"] == "ok":
        isolation.discard_landed_folders(isolation.lock_folders(settings["run_dir"], entry["msp"]))
    return landed


def _lock_attempt(plan, entry, settings, command, models, sandbox, base, note=None):
    record = _dispatch_check_author(entry, settings, command, models, sandbox, base, note)
    return record if record["state"] != "ok" else _judged_lock(plan, entry, settings, record, base)


LOCK_REFUSED_NOTE = (
    "mitosis refused the checks you wrote: %s. Correct them in this clone, keeping the rest of "
    "your work, and reply again."
)

SEED_FIELDS = ("seeded", "seed_skipped", "seed_seconds")


def _refused_after_reply(record):
    return bool(record.get("session_id")) and (record.get("returned") or {}).get("status") == "ok"


def _continued_lock(plan, entry, settings, models, sandbox, base, first):
    run_dir = settings["run_dir"]
    folders = isolation.lock_folders(run_dir, entry["msp"])
    session = first["session_id"]
    values = _lock_values(entry, folders, settings["feature_branch"], models, run_dir, session)
    asked = {"template": settings["reask_command"], "values": reask_values(values)}
    argv = build_argv(asked["template"], {**asked["values"], "prompt": LOCK_REFUSED_NOTE % first["reason"]})
    try:
        confinement = isolation.worker_confinement(folders, sandbox, settings["env"])
    except (GitError, OSError) as error:
        return lock_record("failed", "mitosis could not confine the continued lock: %s" % error)
    called = worker_call(
        argv,
        asked,
        folders["clone"],
        call_files(os.path.join(run_dir, "lanes"), _attempt_stem(_lock_name(entry), True)),
        _check_author_environ(entry, settings["plan_id"], settings["feature_branch"], settings["env"]),
        confinement,
    )
    seeded = {key: first[key] for key in SEED_FIELDS if key in first}
    record = _check_author_outcome(entry, called, session, folders, seeded)
    return record if record["state"] != "ok" else _judged_lock(plan, entry, settings, record, base)


def _msp_lock(plan, entry, settings, command, models, sandbox, base):
    slice_paths = tuple(entry["slice"])
    if not missing_paths(settings["repo"], base, slice_paths):
        return lock_record("ok", ALREADY_LOCKED % (", ".join(slice_paths), base[:12]), commit=base)
    first = _lock_attempt(plan, entry, settings, command, models, sandbox, base)
    if first["state"] != "failed":
        return first
    if settings.get("reask_command") and _refused_after_reply(first):
        continued = _continued_lock(plan, entry, settings, models, sandbox, base, first)
        if continued["state"] != "failed" or _refused_after_reply(continued):
            return continued
    return _lock_attempt(
        plan, entry, settings, command, models, sandbox, base, RETRY_NOTE % first["reason"]
    )


def commit_exists(repo, commit):
    return bool(commit) and git_ok(["cat-file", "-e", "%s^{commit}" % commit], repo, env=PINNED_GIT)


def kept_lock(repo, record):
    return (
        isinstance(record, dict)
        and record.get("state") == "ok"
        and commit_exists(repo, record.get("commit"))
    )


def settled_lock(repo, record):
    return kept_lock(repo, record) or (record or {}).get("state") == "failed"


def lock_of(state, msp):
    record = (((state or {}).get("msps") or {}).get(str(msp)) or {}).get("lock")
    return record if isinstance(record, dict) else None


def _with_lock(state, msp, record):
    current = state["msps"].get(str(msp)) or {}
    return {**state, "msps": {**state["msps"], str(msp): {**current, "lock": record}}}


def lock_stage(plan, state, settings, command, models, sandbox, concurrency=1):
    repo, base = settings["repo"], state["base_commit"]
    pending = [
        entry
        for entry in plan.get("check_authors") or ()
        if not settled_lock(repo, lock_of(state, entry["msp"]))
    ]
    if not pending:
        return state
    run_dir = settings["run_dir"]
    with ThreadPoolExecutor(max_workers=max(1, int(concurrency))) as pool:
        futures = {
            pool.submit(_msp_lock, plan, entry, settings, command, models, sandbox, base): entry
            for entry in pending
        }
        for future in as_completed(futures):
            state = write_state(run_dir, _with_lock(state, futures[future]["msp"], future.result()))
    return state


def _lock_entries(plan):
    return {entry["msp"]: entry for entry in plan.get("check_authors") or ()}


def _own_stop(plan, entry, record):
    return {
        "state": MSP_PARKED if record.get("state") == MSP_PARKED else MSP_BLOCKED,
        "reason": "the %s Step %s did not lock %s for MSP %s (%s): %s"
        % (
            core.CHECK_AUTHOR,
            entry["name"],
            ", ".join(entry["slice"]),
            _label(plan, entry["msp"]),
            record.get("state") or "never ran",
            record.get("reason"),
        ),
    }


def _carried_stop(plan, entry, record, consumer, own):
    return {
        "state": own["state"],
        "reason": "MSP %s merges MSP %s, whose lock by the %s Step %s is %s: %s"
        % (
            _label(plan, consumer),
            _label(plan, entry["msp"]),
            core.CHECK_AUTHOR,
            entry["name"],
            record.get("state") or "never ran",
            record.get("reason"),
        ),
        "blocked_by": [entry["msp"]],
    }


LOCK_FALLS_BACK = ("ok", "failed")


def lock_stops(plan, state):
    entries = _lock_entries(plan)
    failing = {
        msp: lock_of(state, msp) or {}
        for msp in sorted(entries)
        if (lock_of(state, msp) or {}).get("state") not in LOCK_FALLS_BACK
    }
    own = {msp: _own_stop(plan, entries[msp], record) for msp, record in failing.items()}
    mergers = core.msp_mergers(plan)
    carried = {}
    for msp, record in failing.items():
        for consumer in mergers.get(msp, ()):
            if consumer not in own:
                found = _carried_stop(plan, entries[msp], record, consumer, own[msp])
                carried = {**carried, consumer: _worse(carried.get(consumer), found)}
    stops = {**carried, **own}
    return {msp: stops[msp] for msp in sorted(stops)}


def _worse(known, found):
    if known is None:
        return found
    if known["state"] == MSP_BLOCKED or found["state"] != MSP_BLOCKED:
        return known
    return found


def lock_stopped_lanes(plan, stops):
    return {
        index: lane_record(
            index,
            lane["msp"],
            LANE_PARKED if stops[lane["msp"]]["state"] == MSP_PARKED else "blocked",
            reason=stops[lane["msp"]]["reason"],
        )
        for index, lane in enumerate(plan.get("lanes") or [])
        if lane["msp"] in stops
    }


def msp_base(plan, state, msp, base):
    record = lock_of(state, msp)
    if msp in _lock_entries(plan) and record is not None and record.get("state") == "ok":
        return record["commit"]
    return base


def referee_holders(plan, state, msp, base):
    entries = _lock_entries(plan)
    own = msp_base(plan, state, msp, base)
    every = frozenset(_norm(path) for entry in entries.values() for path in entry["slice"])
    sliced = {
        _norm(path): msp_base(plan, state, holder, base)
        for holder in (msp,) + core.msp_merges(plan).get(msp, ())
        if holder in entries and (lock_of(state, holder) or {}).get("state") == "ok"
        for path in entries[holder]["slice"]
    }
    return {
        path: sliced.get(_norm(path), own)
        for path in plan.get("locked") or ()
        if _norm(path) in sliced or _norm(path) not in every
    }


def msp_referee(plan, state, settings, msp):
    holders = referee_holders(plan, state, msp, settings["base_commit"])
    if not holders:
        return None
    return locked_record(
        settings["repo"],
        settings["feature_branch"],
        msp_base(plan, state, msp, settings["base_commit"]),
        list(holders),
        holders,
    )


def reached_referee_held(tree, head, settings):
    held = settings.get("referee_held")
    if not held:
        return held
    holders = settings.get("referee_holders") or {}
    reached = {
        commit: git_ok(["merge-base", "--is-ancestor", commit, head], tree, env=PINNED_GIT)
        for commit in frozenset(holders.values())
    }
    return {path: entry for path, entry in held.items() if reached.get(holders.get(path), True)}


def referee_entries(repo, holders):
    commits = sorted(set(holders.values()))
    listed = {commit: _committed_entries(repo, commit) for commit in commits}
    return {
        _norm(path): listed[commit][_norm(path)]
        for path, commit in holders.items()
        if _norm(path) in listed[commit]
    }


def _settled(state, msp, producers, finished, proven=None):
    record = state["msps"].get(str(msp)) or {}
    current = record.get("state")
    if current in ("shipped", MSP_PUSHED):
        pushed = (_pushed_ship(record) or {}).get("pushed")
        return proven is None or pushed is None or pushed.startswith(proven)
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
    render_command="pull-request",
    landing=None,
    trajectory=None,
    gate_setup=None,
    base_commit=None,
    worker_sandbox=False,
    worker_writes=(),
    gate_classes=None,
    gate_classes_sha256=None,
    reask_command=None,
    prompt_template=None,
    max_output=MAX_OUTPUT,
):
    prior = prior_state(run_dir, plan, resume)
    header = carried_header(header, prior)
    check_template("dispatch", worker_command)
    check_reply_templates(worker_command, reask_command)
    check_template("acceptance", acceptance_command)
    if not no_push and pr_command is not None:
        check_template("pull-request", pr_command)
    if gate_setup is not None:
        check_template("gate-setup", gate_setup)
    checked_writes = isolation.refuse_overlapping_writes(run_dir, worker_writes, repo, trees_root)
    if not no_push and header is None:
        raise ConfigError(
            "a run allowed to push needs a pull-request header holding its type, subject and "
            "why lines, so it can title and describe each pull request"
        )
    check_models(plan, worker_command, models)
    env = process_environ(env, repo)
    program = isolation.sandbox_program(worker_sandbox)
    if program is not None:
        isolation.refuse_unquotable(
            (os.path.abspath(run_dir), isolation.worker_home(env)) + checked_writes
        )
    sandbox = None if program is None else {"program": program, "writes": checked_writes}
    _keep_repository_programs_confined(sandbox)
    recorded = (prior or {}).get("lanes") or {}
    if any(
        (recorded.get(str(index)) or {}).get("state") != "ok"
        for index in range(len(plan.get("lanes") or []))
    ):
        check_identity(repo)
    base = base_commit or pinned_base(repo, feature_branch, prior)
    seeds = {"repo": repo, "plan": isolation.seed_plan(repo)}
    settings = {
        "repo": repo,
        "feature_branch": feature_branch,
        "base_commit": base,
        "run_dir": run_dir,
        "acceptance_command": acceptance_command,
        "pr_command": pr_command,
        "render_command": render_command,
        "header": header,
        "remote": remote,
        "push": not no_push,
        "proven_heads": dict(proven_heads or {}),
        "observer": observer,
        "env": env,
        "trajectory": trajectory,
        "gate_setup": gate_setup,
        "gate_classes": tuple(gate_classes or ()),
        "sandbox": sandbox,
        "seeds": seeds,
        "reask_command": reask_command,
        "plan_id": plan["plan_id"],
        "worker": {
            "command": worker_command,
            "models": models,
            "template": prompt_template,
            "max_output": max_output,
            "header": landing or header,
        },
    }
    os.makedirs(run_dir, exist_ok=True)
    write_json(os.path.join(run_dir, PLAN_FILE), plan)
    write_json(os.path.join(run_dir, RUNNER_FILE), os.getpid())
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
        },
    )
    if plan.get("check_authors"):
        state = write_state(
            run_dir,
            lock_stage(plan, state, settings, worker_command, models, sandbox, concurrency),
        )
    stops = lock_stops(plan, state)
    bases = {msp: msp_base(plan, state, msp, base) for msp in range(len(plan["msps"]))}
    trees = prepare_worktrees(
        plan["msps"],
        base,
        trees_root or os.path.join(run_dir, "trees"),
        repo,
        prefix,
        bases,
        frozenset(stops),
    )
    stopped = lock_stopped_lanes(plan, stops)
    state = write_state(
        run_dir,
        {
            **state,
            "worktrees": trees,
            "lanes": {**state["lanes"], **{str(lane): record for lane, record in stopped.items()}},
            "msps": {
                **state["msps"],
                **{
                    str(msp): {
                        **(state["msps"].get(str(msp)) or {}),
                        "referee": None if msp in stops else msp_referee(plan, state, settings, msp),
                    }
                    for msp in range(len(plan["msps"]))
                    if plan.get("check_authors")
                },
            },
        },
    )

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
        concurrency,
        run_dir,
        base,
        models=models,
        prior=state["lanes"],
        on_lane=on_lane,
        observer=observer,
        env=env,
        sandbox=sandbox,
        header=landing or header,
        seeds=seeds,
        reask=reask_command,
        stopped=stopped,
        prompt_template=prompt_template,
        max_output=max_output,
    )
    notify(
        observer,
        "dispatch-ended",
        {"lanes": {str(lane): record for lane, record in records.items() if lane not in already}},
    )
    producers = core.msp_producers(plan)
    finished = core.DELIVERED_STATES if no_push else PUBLISHED_STATES
    for msp in msp_order(plan):
        if msp in stops:
            state = write_state(run_dir, _lock_stopped_record(state, msp, stops[msp]))
            notify(observer, "msp-finished", {"msp": msp, "record": state["msps"][str(msp)]})
            continue
        proven = settings["proven_heads"].get(msp) if settings["push"] else None
        if _settled(state, msp, producers[msp], finished, proven):
            continue
        state = _finish_msp(
            plan, msp, trees, producers[msp], state, msp_settings(plan, state, settings, msp)
        )
        notify(observer, "msp-finished", {"msp": msp, "record": state["msps"][str(msp)]})
    return state


UNLOCKED_REASON = "its check author could not lock it (%s), so nothing built it to judge"


def unlocked_classes(plan, state, msp):
    entry = _lock_entries(plan).get(msp)
    record = lock_of(state, msp) or {}
    if entry is None or record.get("state") != "failed":
        return ()
    held = frozenset(_norm(path) for path in entry["slice"])
    return tuple(
        {
            "test": property_key(check["file"], check["test"]),
            "class": "not-applicable",
            "reason": UNLOCKED_REASON % record.get("reason"),
        }
        for item in plan.get("items") or ()
        for check in item.get("acceptance") or ()
        if _norm(check["file"]) in held
    )


def msp_settings(plan, state, settings, msp):
    if not plan.get("check_authors"):
        return settings
    holders = referee_holders(plan, state, msp, settings["base_commit"])
    return {
        **settings,
        "gate_classes": tuple(settings.get("gate_classes") or ()) + unlocked_classes(plan, state, msp),
        "base_commit": msp_base(plan, state, msp, settings["base_commit"]),
        "referee_held": referee_entries(settings["repo"], holders) if holders else None,
        "referee_holders": {_norm(path): commit for path, commit in holders.items()} or None,
    }


def _lock_stopped_record(state, msp, stop):
    current = state["msps"].get(str(msp)) or {}
    kept = {key: current[key] for key in LOCK_KEYS if key in current}
    return {
        **state,
        "msps": {
            **state["msps"],
            str(msp): {**kept, **stop, "referee": None, "reconcile": NO_REFEREE_WRITE},
        },
    }
