import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone

import core

BRANCH_PREFIX = "mitosis"

PROBE_PREFIX = "probe"

PLAN_FILE = "plan.json"

STATE_FILE = "state.json"

MSP_BLOCKED = "blocked"

LANE_COMMIT_FAILED = "the Lane's commit failed"

LANE_HELD = "held"

MESSAGE_CHECK_FILE = "landing-message.txt"

LOCK_MARKERS = (".lock': File exists", "Another git process seems to be running")

LOCK_PATH = re.compile(r"[\\/]\.git[\\/][^\s'\"]*\.lock\b")

LOCK_RETRY_DELAYS = (0.1, 0.2, 0.4, 0.8, 1.6, 3.2)

MSP_UNCHANGED = "unchanged"

MSP_COMMITTED = "committed"

PUBLISHED_STATES = ("shipped", MSP_UNCHANGED)

ACCEPTANCE_FAILURE_EXIT = 1

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
)


class GitError(RuntimeError):
    def __init__(self, args, code, stderr):
        self.args_run = tuple(args)
        self.code = code
        self.stderr = stderr
        super().__init__(
            "git %s exited %d: %s" % (" ".join(args), code, stderr.strip() or "<no stderr>")
        )


class ConfigError(ValueError):
    pass


class ResumeError(RuntimeError):
    pass


class ShipError(RuntimeError):
    pass


def _git_once(args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL
    )


def _locked(completed):
    stderr = completed.stderr or ""
    return completed.returncode != 0 and (
        any(marker in stderr for marker in LOCK_MARKERS) or bool(LOCK_PATH.search(stderr))
    )


def git_run(args, cwd):
    completed = _git_once(args, cwd)
    for delay in LOCK_RETRY_DELAYS:
        if not _locked(completed):
            return completed
        time.sleep(delay)
        completed = _git_once(args, cwd)
    return completed


def git(args, cwd, check=True):
    completed = git_run(args, cwd)
    if check and completed.returncode != 0:
        raise GitError(args, completed.returncode, completed.stderr)
    return completed.stdout.strip()


def git_ok(args, cwd):
    return git_run(args, cwd).returncode == 0


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    ),
    "decompose": ("prompt", "model", "document"),
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


def last_line(path):
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    lines = [line for line in data.decode("utf-8", "replace").splitlines() if line.strip()]
    return lines[-1] if lines else None


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


def run_command(argv, cwd, timeout, stdout_path, stderr_path):
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=out,
            stderr=err,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            return proc.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            return proc.wait(), True


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
        result = git_run(["merge", "--no-edit", "--no-verify", target], tree)
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
    tracked = git(["ls-files", "--", *paths], tree).splitlines() if paths else []
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


def _worker_values(plan, lane, tree, models, run_dir):
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
    }


def _landing_message(plan, lane, tree):
    return "chore(%s): land Lane %d (%s)" % (
        tree.get("label") or tree["msp"],
        lane,
        ", ".join(plan["lanes"][lane]["steps"]),
    )


def check_committable(tree, message, run_dir):
    for ident in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
        found = git_run(["var", ident], tree["path"])
        if found.returncode != 0:
            raise ConfigError(
                "git has no commit identity in %s, so no Lane could land: %s"
                % (tree["path"], (found.stderr or "").strip() or "git var %s failed" % ident)
            )
    path = os.path.join(run_dir, MESSAGE_CHECK_FILE)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(message + "\n")
    hooked = git_run(["hook", "run", "--ignore-missing", "commit-msg", "--", path], tree["path"])
    detail = ((hooked.stderr or "") + (hooked.stdout or "")).strip()
    if hooked.returncode != 0 and "'hook' is not a git command" not in detail:
        raise ConfigError(
            "the repository's commit-msg hook refuses mitosis's landing message %r, so no Lane "
            "could land: %s" % (message, detail or "exit %d" % hooked.returncode)
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


def _verdict(lane, tree, started, merged, out, err, code, timed_out):
    returned = last_return(out)
    state, reason = lane_verdict(code, returned, timed_out)
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
    )


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
        if isinstance(record, dict) and record.get("state") == LANE_HELD
    }
    ordered = [int(i) for i in plan.get("lane_order") or range(len(lanes))]
    every = ordered + [i for i in range(len(lanes)) if i not in ordered]
    pending = [i for i in every if i not in records and i not in held]
    running = {}
    cap = max(1, int(concurrency))
    if pending:
        first = tree_of[lanes[pending[0]]["msp"]]
        check_committable(first, _landing_message(plan, pending[0], first), run_dir)

    def settle(known, lane, record):
        if on_lane is not None:
            on_lane(lane, record)
        return {**known, lane: record}

    with ThreadPoolExecutor(max_workers=cap) as pool:
        while pending or running or held:
            progressed = False
            busy = {lanes[entry[0]]["msp"] for entry in running.values()}
            for lane in [lane for lane in held if lanes[lane]["msp"] not in busy]:
                landed = _landed(plan, lane, tree_of[lanes[lane]["msp"]], held[lane])
                held = {key: value for key, value in held.items() if key != lane}
                records = settle(records, lane, landed)
                progressed = True
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
                argv = build_argv(command, _worker_values(plan, lane, tree, models, run_dir))
                future = pool.submit(run_command, argv, tree["path"], timeout, out, err)
                running = {**running, future: (lane, now(), merged, out, err)}
                progressed = True
            pending = list(waiting)
            if running:
                done, _ = wait(list(running), return_when=FIRST_COMPLETED)
                for future in done:
                    lane, started, merged, out, err = running[future]
                    running = {f: v for f, v in running.items() if f is not future}
                    code, timed_out = future.result()
                    tree = tree_of[lanes[lane]["msp"]]
                    judged = _verdict(lane, tree, started, merged, out, err, code, timed_out)
                    if judged["state"] == "ok":
                        held = {**held, lane: judged}
                        if on_lane is not None:
                            on_lane(lane, {**judged, "state": LANE_HELD})
                    else:
                        records = settle(records, lane, judged)
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
            found = found + (
                {
                    "step": name,
                    "file": entry["file"],
                    "test": entry["test"],
                    "outcome": outcome,
                    "reason": reason,
                },
            )
    return found


def commit_all(tree, message):
    git(["add", "-A"], tree)
    if git(["status", "--porcelain"], tree):
        git(["commit", "-q", "-m", message], tree)
    return git(["rev-parse", "HEAD"], tree)


def revert_implementation(tree, base, paths):
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
    return list(reverted)


def run_acceptance(command, tree, entry, log_path, timeout):
    argv = build_argv(command, {"file": entry["file"], "test": entry["test"], "worktree": tree})
    return run_command(argv, tree, timeout, log_path, log_path + ".err")


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
    for candidate in ("inert", "inconclusive", "pass"):
        if candidate in outcomes:
            return candidate
    return "not-applicable"


def gate_state(result):
    if result["outcome"] == "inert":
        return "gate-failed"
    if result["outcome"] == "inconclusive":
        return "gate-inconclusive"
    return None


def _gate_result(properties, implementation, commit, reverted):
    resolved = [
        {**entry, "outcome": entry["outcome"] or "inconclusive"}
        for entry in properties
    ]
    outcome = gate_outcome(resolved)
    return {
        "outcome": outcome,
        "blocks": outcome in ("inert", "inconclusive"),
        "properties": resolved,
        "implementation": list(implementation),
        "reverted": list(reverted),
        "counts": {
            key: sum(1 for e in resolved if e["outcome"] == key) for key in core.GATE_OUTCOMES
        },
        "commit": commit,
    }


def gate(plan, msp, tree, branch, base, command, log_dir, timeout=None):
    commit = commit_all(
        tree, "chore(%s): commit the MSP's work before the gate" % _label(plan, msp)
    )
    properties = msp_properties(
        plan, msp, steps_without_implementation_change(plan, msp, tree, base)
    )
    implementation = implementation_paths(plan, msp)
    runnable = [index for index, entry in enumerate(properties) if entry["outcome"] is None]
    if not runnable:
        return _gate_result(properties, implementation, commit, ())
    os.makedirs(log_dir, exist_ok=True)
    with_work = {
        index: run_acceptance(
            command,
            tree,
            properties[index],
            os.path.join(log_dir, "%d-with-work.log" % index),
            timeout,
        )
        for index in runnable
    }
    probe = "%s/%s" % (PROBE_PREFIX, branch)
    git(["checkout", "-q", "-B", probe], tree)
    try:
        reverted = revert_implementation(tree, base, implementation)
        without = {
            index: run_acceptance(
                command,
                tree,
                properties[index],
                os.path.join(log_dir, "%d-reverted.log" % index),
                timeout,
            )
            for index in runnable
            if with_work[index] == (0, False)
        }
    finally:
        git(["checkout", "-q", "-f", branch], tree)
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
            },
        )
    return _gate_result(judged, implementation, commit, reverted)


def changed_files(tree, branch, base, exclude=()):
    excluded = ["^" + ref for ref in exclude if branch_exists(tree, ref)]
    out = git(
        ["log", "--format=", "--name-only", "--no-renames", branch, "^" + base, *excluded], tree
    )
    found = ()
    for line in out.splitlines():
        path = line.strip()
        if path and path not in found:
            found = found + (path,)
    return sorted(found)


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
    return {
        "changed": changed,
        "declared": declared,
        "undeclared": undeclared,
        "unwritten": unwritten,
        "untouched": untouched,
        "crossing": list(crossing),
        "fatal": bool(crossing),
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


def _property_lines(plan, msp):
    steps = _steps_by_name(plan)
    lines = ()
    for name in plan["msps"][msp]["steps"]:
        for entry in steps[name].get("acceptance") or []:
            lines = lines + ("- %s::%s (%s)" % (entry["file"], entry["test"], name),)
    return lines


def _first_line(text):
    stripped = (text or "").strip()
    return stripped.splitlines()[0] if stripped else ""


def pull_request_title(plan, msp):
    label = _label(plan, msp)
    steps = plan["msps"][msp]["steps"]
    if steps == [label]:
        return label
    return "%s: %s" % (label, ", ".join(steps))


def pull_request_body(plan, msp, base, acceptance_command, gate_result, findings, exception):
    steps = _steps_by_name(plan)
    names = plan["msps"][msp]["steps"]
    lines = [
        "Draft pull request opened by mitosis. mitosis never merges; a human does.",
        "",
        "MSP: %s" % _label(plan, msp),
        "Base: %s" % base,
        "Plan: %s" % plan.get("plan_id"),
        "",
        "Steps:",
    ]
    lines = lines + [
        "- %s: %s" % (name, _first_line(steps[name].get("task")) or name) for name in names
    ]
    properties = _property_lines(plan, msp)
    lines = lines + ["", "Acceptance properties, re-runnable by a reviewer:"]
    lines = lines + (list(properties) if properties else ["- none declared"])
    lines = lines + ["", "Run each property with: %s" % acceptance_command]
    silent = sum(1 for name in names if not steps[name].get("acceptance"))
    lines = lines + ["", "Steps with no acceptance property: %d of %d" % (silent, len(names))]
    if gate_result:
        counts = gate_result.get("counts") or {}
        tally = ", ".join("%d %s" % (counts.get(key, 0), key) for key in core.GATE_OUTCOMES)
        lines = lines + ["Gate: %s (%s)" % (gate_result.get("outcome"), tally)]
    if findings:
        undeclared = ", ".join(findings.get("undeclared") or []) or "none"
        unwritten = ", ".join(findings.get("unwritten") or []) or "none"
        lines = lines + ["Reconcile: undeclared %s; unwritten %s" % (undeclared, unwritten)]
    if exception:
        producers = ", ".join(_label(plan, p) for p in exception["producers"])
        lines = lines + [
            "",
            "Stacking exception: this MSP has %d producers (%s), so its base is the feature "
            "branch and its diff includes their work." % (len(exception["producers"]), producers),
        ]
    return "\n".join(lines) + "\n"


def ship(
    plan,
    msp,
    tree,
    branch,
    base,
    pr_command,
    remote,
    log_dir,
    acceptance_command="",
    gate_result=None,
    findings=None,
    exception=None,
    timeout=None,
    push=True,
):
    label = _label(plan, msp)
    head = commit_all(tree, "chore(%s): commit the MSP's work before shipping" % label)
    held = {
        "branch": branch,
        "base": base,
        "remote": remote,
        "pushed": None,
        "title": pull_request_title(plan, msp),
        "pull_request": "",
        "stacking_exception": exception is not None,
    }
    if git_ok(["diff", "--quiet", "%s...%s" % (base, head)], tree):
        return {**held, "unchanged": True}
    if not push:
        return {**held, "unchanged": False}
    git(["push", "-q", "-u", remote, branch], tree)
    title = pull_request_title(plan, msp)
    body = pull_request_body(plan, msp, base, acceptance_command, gate_result, findings, exception)
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
    code, timed_out = run_command(argv, tree, timeout, out, err)
    if timed_out:
        raise ShipError("the pull-request command timed out for %s" % branch)
    if code != 0:
        detail = last_line(err) or last_line(out) or ""
        raise ShipError("the pull-request command exited %d for %s: %s" % (code, branch, detail))
    return {
        "branch": branch,
        "base": base,
        "remote": remote,
        "pushed": head,
        "title": title,
        "pull_request": last_line(out) or "",
        "stacking_exception": exception is not None,
        "unchanged": False,
    }


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
        if lane_state != "ok":
            return {"state": MSP_BLOCKED, "reason": "Lane %d is %s" % (index, lane_state)}
    return None


def _producer_block(plan, msp, state, producers):
    for producer in producers:
        producer_state = (state["msps"].get(str(producer)) or {}).get("state")
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
        )
    except (OSError, GitError, subprocess.SubprocessError) as error:
        return {"state": "gate-inconclusive", "reason": str(error), "gate": None}
    return {"state": gate_state(result), "reason": None, "gate": result}


def _reconcile_stage(plan, msp, tree, producers, trees, settings):
    findings = reconcile(
        plan,
        msp,
        tree["path"],
        tree["branch"],
        settings["feature_branch"],
        tuple(trees[p]["branch"] for p in producers),
    )
    if not findings["fatal"]:
        return {"reconcile": findings}
    crossed = ", ".join(
        "%s belongs to MSP %s" % (entry["path"], entry["label"]) for entry in findings["crossing"]
    )
    return {
        "reconcile": findings,
        "state": MSP_BLOCKED,
        "reason": "a write crossed an MSP boundary: " + crossed,
    }


def _ship_stage(plan, msp, tree, base, exception, record, settings):
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
            settings["acceptance_command"],
            record.get("gate"),
            record.get("reconcile"),
            exception,
            settings["timeout"],
            settings["push"],
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
    return {"state": "shipped", "reason": None, "ship": shipped}


def _finish_msp(plan, msp, trees, producers, state, settings):
    run_dir = settings["run_dir"]
    tree = trees[msp]
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
    gated = _gate_stage(plan, msp, tree, settings)
    state = write_state(run_dir, _msp_record(state, msp, **gated))
    if gated["gate"] is None or gated["gate"]["blocks"]:
        return state
    reconciled = _reconcile_stage(plan, msp, tree, producers, trees, settings)
    state = write_state(run_dir, _msp_record(state, msp, **reconciled))
    if reconciled.get("state") == MSP_BLOCKED:
        return state
    base, exception = pr_base(
        plan, msp, settings["feature_branch"], trees, settings["repo"], state["msps"]
    )
    if exception is not None:
        state = write_state(
            run_dir, {**state, "stacking_exceptions": state["stacking_exceptions"] + [exception]}
        )
    shipped = _ship_stage(plan, msp, tree, base, exception, state["msps"][str(msp)], settings)
    return write_state(run_dir, _msp_record(state, msp, **shipped))


def _settled(state, msp, producers, finished):
    current = (state["msps"].get(str(msp)) or {}).get("state")
    if current == "shipped":
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
):
    prior = prior_state(run_dir, plan, resume)
    check_template("dispatch", worker_command)
    check_template("acceptance", acceptance_command)
    if not no_push:
        check_template("pull-request", pr_command)
    check_models(plan, worker_command, models)
    if timeout is None:
        raise ConfigError("a per-Lane timeout is required; mitosis has no default")
    settings = {
        "repo": repo,
        "feature_branch": feature_branch,
        "run_dir": run_dir,
        "acceptance_command": acceptance_command,
        "pr_command": pr_command,
        "timeout": timeout,
        "remote": remote,
        "push": not no_push,
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
        },
    )
    trees = prepare_worktrees(
        plan["msps"], feature_branch, trees_root or os.path.join(run_dir, "trees"), repo, prefix
    )
    state = write_state(run_dir, {**state, "worktrees": trees})

    def on_lane(lane, record):
        nonlocal state
        state = write_state(run_dir, {**state, "lanes": {**state["lanes"], str(lane): record}})

    dispatch(
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
    )
    producers = msp_producers(plan)
    finished = core.DELIVERED_STATES if no_push else PUBLISHED_STATES
    for msp in msp_order(plan):
        if _settled(state, msp, producers[msp], finished):
            continue
        state = _finish_msp(plan, msp, trees, producers[msp], state, settings)
    return state
