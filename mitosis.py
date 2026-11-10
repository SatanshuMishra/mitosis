import argparse
import hashlib
import itertools
import json
import os
import re
import shlex
import signal
import sys
import tempfile
from datetime import datetime

import asks
import core
import decompose
import handoff
import isolation
import own_tests
import metrics
import referee
import pr_facts
import run
import shape

EXIT_SHIPPED = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_INCOMPLETE = 4
EXIT_NO_BRANCHES = 5
EXIT_RECONCILE = 6

EXIT_CROSSING = 7

EXIT_FOREIGN = 8

EXIT_REFEREE = 9

TIMEOUT_NOTICE = "mitosis: --timeout is deprecated and ignored; mitosis has no wall-clock limit"

SUITE_NOTICE = "mitosis: --suite-command is deprecated and ignored; a surviving mutant is reported and holds nothing"

BRIEF_NOTICE = "mitosis: --brief-command is deprecated and ignored; the split writes each Step's task, so no brief call runs"

EXIT_HANDOFF = 15

LANE_EXIT = {
    "ok": EXIT_SHIPPED,
    "failed": 10,
    "blocked": 11,
    "merge-blocked": 12,
    run.LANE_PARKED: 13,
}

MSP_EXIT = {
    "shipped": EXIT_SHIPPED,
    run.MSP_PUSHED: EXIT_SHIPPED,
    run.MSP_UNCHANGED: EXIT_SHIPPED,
    run.MSP_COMMITTED: EXIT_SHIPPED,
    run.MSP_BLOCKED: 11,
    "ship-failed": 22,
    run.MSP_REFEREE_REVERTED: EXIT_REFEREE,
    run.MSP_PARKED: 13,
}

EXIT_HELD = 21

EXIT_SEVERITY = (9, 7, 8, 21, 22, 10, 12, 11, 13, 6, 4)

EXIT_MEANING = {
    EXIT_SHIPPED: "every MSP reached shipped, pushed, unchanged or committed; anything reported "
    "along the way is named in the result and the pull request",
    EXIT_USAGE: "the flags did not parse",
    EXIT_REFUSED: "refused to start",
    EXIT_INCOMPLETE: "the run stopped before every Lane and MSP reached a terminal state",
    EXIT_NO_BRANCHES: "the run created no branches",
    EXIT_RECONCILE: "reconcile found an MSP that changed nothing though it declared files",
    EXIT_CROSSING: "an MSP branch, pushed or with its pull request open, writes files another MSP "
    "owns and also changes",
    EXIT_FOREIGN: "a Lane changed a file another Lane of its MSP owns and had already committed",
    EXIT_REFEREE: "a Worker wrote a referee path; mitosis restored it and held its MSP back",
    EXIT_HANDOFF: "a hand-off mitosis was about to write failed its owned schema; mitosis wrote "
    "nothing for it and stopped",
    LANE_EXIT["failed"]: "a Lane failed",
    LANE_EXIT["blocked"]: "a Lane or MSP was blocked, by a predecessor or by a write across an "
    "MSP boundary",
    LANE_EXIT["merge-blocked"]: "a producer branch would not merge",
    LANE_EXIT[run.LANE_PARKED]: "a Worker stopped at a usage limit; the run is parked: pass "
    "--resume after the limit resets",
    EXIT_HELD: "an MSP was held back instead of pushed: an acceptance test fails "
    "against the finished work, the repository's hook refused the whole change, or the work could "
    "not be committed",
    MSP_EXIT["ship-failed"]: "a commit, push or pull-request command failed",
}

DECOMPOSE_TIER = "top"

RUNS_DIR = ("~", ".mitosis", "runs")

ITEMS_FILE = "items.json"
NOTICES_FILE = "notices.json"

STRUCTURE_FILE = "structure.json"

BRIEFS_DIR = "briefs"

DECOMPOSE_FILE = "decompose.json"

DECOMPOSE_LOG = "decompose.log"

RECON_DIR = "recon"

REMOTE = "origin"

DEFAULT_CONCURRENCY = 4

REPORT_SECTIONS = (
    "Coverage map",
    "Assumptions",
    "Decisions",
    "Constraints count",
    "Drift",
    "Split shape",
    "Split quality",
    "Gate outcomes",
    "Reconcile findings",
    "Coupling",
    "Stacking exceptions",
    "Outcome",
)

DECOMPOSE_PLACEHOLDERS = ("prompt", "model", "document", "session_id", "schema")

TEMPLATE_PLACEHOLDERS = {"decompose": DECOMPOSE_PLACEHOLDERS}

PLACEHOLDER_HELP = {
    "dispatch": (
        "{task} {model} {tier} {worktree} {branch} {lane} {msp} {charter} {document} {run_dir} "
        "{session_id} {schema}"
    ),
    "reask": " ".join("{%s}" % key for key in run.TEMPLATE_PLACEHOLDERS["reask"]),
    "decompose": " ".join("{%s}" % key for key in DECOMPOSE_PLACEHOLDERS),
    "acceptance": "{file} {test} {worktree}",
    "gate-setup": "{worktree}",
    "pull-request": "{branch} {base} {title} {body} {worktree} {msp} {remote}",
}

STAGE_FLAG_NAMES = (
    "--asks",
    "--recon-prompt-template",
    "--emit-items",
    "--brief-command",
    "--decisions",
    "--structure-samples",
    "--pick",
    "--revise",
    "--proven-head",
    "--worker-sandbox",
    "--worker-write",
    "--worktree-root",
    "--no-run-dir",
    "--suite-command",
    "--gate-classes",
    "--pr-impact",
    "--render-command",
    "--no-trajectories",
    "--no-briefs",
    "--prompt-template",
    "--max-output-bytes",
    "--run-log",
    "--dry-run",
    "--plan-file",
)

SAVED_PLAN_KEYS = ("msps", "lanes", "briefs")

GATE_CLASS_KEYS = ("class", "reason", "test")

GATE_CLASS_REASON_LIMIT = 500

PROVEN_HEAD = re.compile(r"^(.+)=([0-9a-f]{7,40})$")

RUN_FLAGS = (
    ("--feature-branch", "feature_branch"),
    ("--dispatch-command", "dispatch_command"),
    ("--acceptance-command", "acceptance_command"),
)

PUSH_FLAGS = (
    ("--pr-type", "pr_type"),
    ("--pr-subject", "pr_subject"),
    ("--pr-why", "pr_why"),
)

FLAG_SPECS = {
    "--items": {
        "metavar": "PATH",
        "help": "a JSON array of Steps; the plan is built from it directly",
    },
    "--spec": {
        "metavar": "PATH",
        "help": "a document in any format; the structure stage turns it into Steps, each with "
        "its task, which needs --decompose-command",
    },
    "--asks": {
        "metavar": "PATH",
        "help": "an asks file, one ask per line; each ask is planned by its own call through "
        "--decompose-command, at most --concurrency at once",
    },
    "--recon-prompt-template": {
        "metavar": "TEMPLATE",
        "help": "the planning prompt for each ask and a spec's structure call, with {ask}, "
        "{graph}, {source} and {contract} replaced",
    },
    "--emit-items": {
        "metavar": "PATH",
        "help": "write the Steps the asks or structure stage derived to PATH as JSON indented by 2",
    },
    "--charter": {
        "metavar": "PATH",
        "help": "the project's binding constraints; every Worker receives its path unchanged",
    },
    "--feature-branch": {
        "metavar": "BRANCH",
        "help": "the branch every MSP branches from and an unstacked pull request targets; "
        "required for a run",
    },
    "--run-dir": {
        "metavar": "PATH",
        "help": "where the plan, the state and every log land "
        "(default: ~/.mitosis/runs/<repository>/<input hash>)",
    },
    "--resume": {
        "action": "store_true",
        "help": "continue the run in --run-dir: with --spec, load the persisted structure and "
        "plan it; Lanes already ok are skipped, "
        "a gate that blocked is re-run, and a passing gate is reused when it was recorded at "
        "the commit mitosis gates now, the MSP branch's commit after its pre-gate commit of the "
        "worktree, so a leftover edit that commit takes forces a new gate, and a worktree HEAD "
        "off that commit is fatal with exit 9; the plan id must match",
    },
    "--proven-head": {
        "metavar": "LABEL=COMMIT",
        "action": "append",
        "default": [],
        "help": "ship the MSP named LABEL without re-gating when its HEAD equals COMMIT and a "
        "gate result that did not block is recorded for it; otherwise it is gated as without "
        "this flag; repeatable",
    },
    "--plan-only": {
        "action": "store_true",
        "help": "stop before the build: with --spec and no --resume, run the structure stage "
        "and its check and write the structure; otherwise validate, schedule, write the "
        "plan and print the plan-stage report; exit zero when the stage is valid",
    },
    "--dispatch-command": {
        "metavar": "TEMPLATE",
        "help": "one Worker per Lane; placeholders %s; split into argv before substitution; "
        "a run needs {schema}, the call schema the reply meets as structured output, and "
        "{session_id}" % PLACEHOLDER_HELP["dispatch"],
    },
    "--reask-command": {
        "metavar": "TEMPLATE",
        "help": "asks a Worker, check-author or decompose Worker whose reply fails its "
        "owned schema once more, in the same session; placeholders %s; required for a run "
        "that calls a model, whose command also needs {schema} and {session_id}; replies are "
        "read from structured_output" % PLACEHOLDER_HELP["reask"],
    },
    "--decompose-command": {
        "metavar": "TEMPLATE",
        "help": "the structure Worker; placeholders %s; the prompt also arrives on stdin; "
        "needs {schema} and {session_id}, and its reply meets %s as structured output"
        % (PLACEHOLDER_HELP["decompose"], decompose.REPLY_SCHEMA),
    },
    "--brief-command": {
        "metavar": "TEMPLATE",
        "help": "deprecated and ignored; the split writes each Step's task, so no brief call runs",
    },
    "--decisions": {
        "metavar": "PATH",
        "help": "the settled-questions file, injected verbatim into every structure prompt "
        "(default: <document>%s when it exists)" % decompose.DECISIONS_SUFFIX,
    },
    "--structure-samples": {
        "metavar": "N",
        "type": int,
        "default": 1,
        "help": "concurrent structure dispatches against one prompt; every sample is ranked "
        "by its scalars and the best is persisted (default: 1)",
    },
    "--pick": {
        "metavar": "K",
        "type": int,
        "help": "persist the Kth structure sample instead of the best-scoring one",
    },
    "--revise": {
        "action": "store_true",
        "help": "the structure in --run-dir is the base: the structure Worker returns a delta "
        "against it and every kept Step keeps its task",
    },
    "--acceptance-command": {
        "metavar": "TEMPLATE",
        "help": "runs one acceptance property; placeholders %s; exit 0 passes, exit %d fails, "
        "anything else is inconclusive"
        % (PLACEHOLDER_HELP["acceptance"], run.ACCEPTANCE_FAILURE_EXIT),
    },
    "--gate-setup": {
        "metavar": "TEMPLATE",
        "help": "prepares the gate's clean checkout once per gate before any acceptance run; "
        "placeholders %s; when given, the gate links no dependency "
        "folder from the repository root; only what it writes at new top-level names survives "
        "each run's reset; a non-zero exit holds the MSP back "
        "gate-failed" % PLACEHOLDER_HELP["gate-setup"],
    },
    "--suite-command": {
        "metavar": "TEMPLATE",
        "help": "deprecated and ignored: a test no mutant fails is reported and holds nothing, "
        "so nothing needs the suite run against each survivor",
    },
    "--gate-classes": {
        "metavar": "PATH",
        "help": "a JSON list of {test, class, reason} objects, test naming one acceptance "
        "property as <file>::<name>, class one of %s and reason one non-empty line of at most "
        "%d characters; a classed property is judged by its class without a run, the plan id "
        "stays unchanged, and with --resume every other property keeps the verdict recorded at "
        "the MSP's unchanged commit" % (", ".join(run.GATE_CLASSES), GATE_CLASS_REASON_LIMIT),
    },
    "--pr-command": {
        "metavar": "TEMPLATE",
        "help": "opens one pull request with the title and body pull-request render --fields "
        "printed; placeholders %s; its last stdout line is recorded; without it, a run allowed to "
        "push pushes each branch, ends its MSP pushed and records the title and body for the "
        "caller" % PLACEHOLDER_HELP["pull-request"],
    },
    "--render-command": {
        "metavar": "COMMAND",
        "default": "pull-request",
        "help": "the pull-request command whose render --fields turns each MSP's facts file into "
        "its title and body; needs pull-request 0.3.0 or later and is probed on every MSP before "
        "any Worker runs when --pr-type and --pr-subject are given (default: pull-request)",
    },
    "--pr-type": {
        "metavar": "TYPE",
        "help": "the type of every pull-request title, one of %s, handed to pull-request render "
        "--fields as a fact; required for a run allowed to push" % ", ".join(run.PR_TYPES),
    },
    "--pr-scope": {
        "metavar": "SCOPE",
        "help": "the optional scope of every pull-request title, handed to pull-request render "
        "--fields as a fact",
    },
    "--pr-subject": {
        "metavar": "TEXT",
        "help": "the summary of every pull-request title, handed to pull-request render --fields "
        "as a fact; a plan of several MSPs appends each MSP label in parentheses; required for a "
        "run allowed to push",
    },
    "--pr-why": {
        "metavar": "SENTENCE",
        "action": "append",
        "default": [],
        "help": "one line of the problem every pull request solves, handed to pull-request render "
        "--fields as a fact; more than four lines are joined into one paragraph; give it 1 to %d "
        "times; required for a run allowed to push" % run.SECTION_LIMIT,
    },
    "--pr-door": {
        "metavar": "DOOR",
        "help": "one of %s, accepted so existing callers keep working; it no longer reaches the "
        "pull-request body; given together with --pr-blast-radius or not at all"
        % ", ".join(run.PR_DOORS),
    },
    "--pr-blast-radius": {
        "metavar": "SENTENCE",
        "help": "what callers or users notice, handed to pull-request render --fields as the "
        "impact when no --pr-impact is given; given together with --pr-door or not at all",
    },
    "--pr-impact": {
        "metavar": "SENTENCE",
        "action": "append",
        "default": [],
        "help": "one line of what callers or users notice, handed to pull-request render --fields "
        "as a fact; repeatable; a run given --pr-type and --pr-subject needs it or "
        "--pr-blast-radius",
    },
    "--no-push": {
        "action": "store_true",
        "help": "commit, gate and reconcile every MSP on its local branch, then stop: nothing is "
        "pushed and no pull request opens, so neither --pr-command nor the pull-request text "
        "flags are needed; a later --resume without this flag ships what was held",
    },
    "--tier-model": {
        "metavar": "TIER=MODEL",
        "action": "append",
        "default": [],
        "help": "the model {model} substitutes for a tier (%s); every tier the plan uses must "
        "be mapped when the dispatch template uses {model}; decompose uses the %s mapping"
        % (", ".join(core.TIERS), DECOMPOSE_TIER),
    },
    "--timeout": {
        "metavar": "SECONDS",
        "type": float,
        "help": "deprecated and ignored; mitosis has no wall-clock limit, and a command's exit "
        "ends every wait",
    },
    "--concurrency": {
        "metavar": "N",
        "type": int,
        "default": DEFAULT_CONCURRENCY,
        "help": "Workers running at once, for the asks stage and the build "
        "(default: %d)" % DEFAULT_CONCURRENCY,
    },
    "--context-hops": {
        "metavar": "N",
        "type": int,
        "default": 1,
        "help": "how far along the graph each Step's read-set reaches (default: 1)",
    },
    "--context-cap": {
        "metavar": "N",
        "type": int,
        "default": core.CONTEXT_CAP,
        "help": "most graph neighbours kept per Step; the overflow is counted "
        "(default: %d)" % core.CONTEXT_CAP,
    },
    "--coalesce-below": {
        "metavar": "COST",
        "type": float,
        "default": 2.0,
        "help": "cheap-tier Steps at or below this cost may be grouped into one Worker "
        "(0 disables; default: 2)",
    },
    "--coalesce-max": {
        "metavar": "N",
        "type": int,
        "default": 4,
        "help": "most Steps in one coalesced group (default: 4)",
    },
    "--graph": {
        "metavar": "PATH",
        "help": "a JSON object mapping each path to the paths it imports, or a node-link "
        "graph such as graphify's graph.json, read as that mapping from its links between "
        "code in different files",
    },
    "--shared-files": {
        "metavar": "LIST",
        "default": "",
        "help": "comma-separated files that MSPs may each edit without being fused: an entry "
        "matches a path equal to it, a path ending in a slash and the entry, or a path the "
        "entry matches as a case-sensitive glob; an overlap only on such a file makes no MSP, "
        "Lane edge or scheduling conflict, is listed in the coupling review, and the plan "
        "records the order to integrate each file two MSPs edit (default: none)",
    },
    "--risk-markers": {
        "metavar": "MARKERS",
        "default": "",
        "help": "comma-separated path markers that force the top tier and count as a coupling "
        "signal; a one-word marker matches a word of the path, any other marker a "
        "case-insensitive substring; a repeated flag keeps the last value",
    },
    "--serial-verify-markers": {
        "metavar": "MARKERS",
        "dest": "serial_markers",
        "default": "",
        "help": "comma-separated path substrings; a Step with a file containing one verifies "
        "serially, and such properties are counted not-applicable",
    },
    "--serial-markers": {
        "metavar": "MARKERS",
        "dest": "serial_markers",
        "default": "",
        "help": "the same as --serial-verify-markers",
    },
    "--trajectory": {
        "metavar": "PATH",
        "help": "a JSONL file of prior outcomes to read history from and append gate outcomes "
        "to; without it, history is read from MITOSIS_TRAJECTORY_STORE or "
        "~/.claude/mcp-servers/trajectory-kb/data/trajectories.jsonl and nothing is appended; "
        "a missing file reads as no history, and a recorded revert, speculative ship, caused "
        "regression or two prior traps on a surface raises its tier and never lowers one",
    },
    "--no-trajectories": {
        "action": "store_true",
        "help": "plan from no history, reading neither --trajectory's file nor the default store",
    },
    "--no-briefs": {
        "action": "store_true",
        "help": "write plan.json without briefs or check-author briefs, a shape-only plan; "
        "accepted only with --plan-only, since a run dispatches every Lane from its brief",
    },
    "--prompt-template": {
        "metavar": "TEXT",
        "help": "replaces each build Lane's brief with TEXT after its 'Lane N of MSP L' line; "
        "placeholders {cluster} (the Lane's Steps), {spec} (its JSON leaves), {tier} and "
        "{contract} (the Worker return contract); check-author Lanes keep their brief, and "
        "the plan and its id never see the template",
    },
    "--max-output-bytes": {
        "metavar": "N",
        "type": int,
        "default": run.MAX_OUTPUT,
        "help": "keep the last N characters of a Lane's stdout as its record's output_tail "
        "when the Worker and its re-ask return no valid structured reply (default: %d)"
        % run.MAX_OUTPUT,
    },
    "--run-log": {
        "metavar": "PATH",
        "help": "when the run's build ends, including a stopped run, write PATH fresh as JSON "
        "lines, one per Lane record in state.json ordered by its finished time, each carrying "
        "the plan's plan_id",
    },
    "--dry-run": {
        "action": "store_true",
        "help": "print each Lane's dispatch argv in dispatch order and build nothing: no "
        "Worker, branch, worktree, clone, state.json or plan.json; needs --dispatch-command",
    },
    "--plan-file": {
        "metavar": "PATH",
        "help": "build the saved plan at PATH as written, its briefs and plan_id included, "
        "instead of computing one, with --plan-only as with a run; every lock --charter derives "
        "and every check-author Step's files are merged back into its locked list",
    },
    "--worker-sandbox": {
        "action": "store_true",
        "help": "run every Worker under sandbox-exec with a profile that refuses file writes "
        "outside its clone, a private temp folder passed as TMPDIR, its Claude Code transcript "
        "folder, /dev/null, /dev/tty, /dev/fd and each --worker-write path, and run the gate's "
        "setup, acceptance and mutant runs and the lock stage's checks under sandbox-exec "
        "too, writing only their checkout, a private temp folder and each --worker-write path; "
        "a host without sandbox-exec refuses the run before any Worker starts",
    },
    "--worker-write": {
        "metavar": "PATH",
        "action": "append",
        "default": [],
        "help": "one more path a sandboxed Worker, gate run or lock-stage check may write "
        "under, such as one a --gate-setup needs; repeatable; a path that is, holds or lies "
        "inside the run directory, the repository or its git directory refuses the run with "
        "exit 3",
    },
    "--worktree-root": {
        "metavar": "PATH",
        "default": "",
        "help": "the folder each MSP's worktree is placed in, as PATH/<slug>, read against the "
        "repository root; it is created before any Worker starts and a PATH that cannot be "
        "created refuses the run with exit 3; a --worker-write path that is, holds or lies "
        "inside it refuses the run with exit 3 (default: the run directory's trees folder)",
    },
    "--no-run-dir": {
        "action": "store_true",
        "help": "keep the run's plan, state, logs and clones in a private temp folder removed "
        "before mitosis exits, whether the run shipped, failed or was refused; --run-dir is "
        "ignored and --resume starts from nothing; the MSP branches and worktrees stay, at "
        "--worktree-root or where a run of the same input keeps them; each Worker's standard "
        "output is printed before the report, and the result line carries no run_dir",
    },
    "--version": {"action": "version", "version": core.__version__},
    "--help": {"action": "help", "help": "show this message and exit"},
}


class Refusal(Exception):
    pass


class UnknownProvenHead(Exception):
    pass


def flag_names():
    anchor = core.FLAG_NAMES.index("--decompose-command") + 1
    return core.FLAG_NAMES[:anchor] + STAGE_FLAG_NAMES + core.FLAG_NAMES[anchor:]


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mitosis",
        add_help=False,
        description="Split a document or an items file into MSPs, build each on its own "
        "branch with each Lane one Worker session, gate the work, hand each MSP's "
        "facts to pull-request render --fields, and push each branch with the title and body "
        "render printed, opening one pull request per MSP when --pr-command is given. "
        "mitosis never merges.",
    )
    for name in flag_names():
        parser.add_argument(name, **FLAG_SPECS[name])
    return parser


def declared_flags(parser):
    return tuple(
        option for action in parser._actions for option in action.option_strings
    )


def tier_models(pairs):
    models = {}
    for pair in pairs or ():
        tier, separator, model = str(pair).partition("=")
        if not separator or not tier or not model:
            raise Refusal("--tier-model takes TIER=MODEL, got %r" % pair)
        if tier not in core.TIERS:
            raise Refusal(
                "--tier-model names tier %r; the tiers are %s" % (tier, ", ".join(core.TIERS))
            )
        models = {**models, tier: model}
    return models


def unmapped_tiers(plan, template, models):
    if not template or "{model}" not in template:
        return ()
    return tuple(tier for tier in core.plan_tiers(plan) if tier not in (models or {}))


def refuse_unmapped(plan, template, models):
    missing = unmapped_tiers(plan, template, models)
    if missing:
        raise Refusal(
            "the dispatch command uses {model} but no model is mapped for tier %s; "
            "map every tier with --tier-model or the Lane would run on the agent's default"
            % ", ".join(str(tier) for tier in missing)
        )


def repository_root(cwd):
    try:
        return run.git(["rev-parse", "--show-toplevel"], cwd)
    except (run.GitError, OSError):
        return None


def read_json(path, what):
    try:
        return run.read_json(path)
    except OSError as error:
        raise Refusal("%s %s is not readable: %s" % (what, path, error))
    except ValueError as error:
        raise Refusal("%s %s is not valid JSON: %s" % (what, path, error))


ITEM_POINTER = re.compile(r"^#/(\d+)(?![0-9])")


def item_label(items, error):
    found = ITEM_POINTER.match(error)
    if found is None:
        return None
    index = int(found.group(1))
    name = items[index].get("name") if index < len(items) and isinstance(items[index], dict) else None
    return name if isinstance(name, str) and name else "item #%d" % index


def schema_error_line(items, error):
    label = item_label(items, error)
    return error if label is None else "Step %s: %s" % (label, error)


def with_item_defaults(item):
    acceptance = item.get("acceptance", [])
    observable = {"observable": acceptance} if isinstance(acceptance, str) else {}
    return {
        **item,
        "source": item.get("source"),
        **observable,
        "acceptance": [] if isinstance(acceptance, str) else acceptance,
    }


def read_items(path):
    items = read_json(path, "--items")
    found = handoff.errors(ITEMS_SCHEMA, items)
    if found:
        raise Refusal(
            "\n".join(
                ("the --items file %s fails the owned schema %s:" % (path, handoff.file_name(ITEMS_SCHEMA)),)
                + tuple(schema_error_line(items, error) for error in found)
            )
        )
    return [with_item_defaults(item) for item in items]


def file_digest(path):
    try:
        return run.sha256_file(path)
    except OSError as error:
        raise Refusal("%s is not readable: %s" % (path, error))


def default_run_dir(root, kind, digest):
    return os.path.join(
        os.path.expanduser(os.path.join(*RUNS_DIR)),
        os.path.basename(os.path.abspath(root)),
        "%s-%s" % (kind, digest[:12]),
    )


def document_path(spec, root):
    absolute = os.path.abspath(spec)
    relative = os.path.relpath(absolute, root)
    return absolute if relative.startswith("..") else relative


def check_charter(charter):
    if charter is None:
        return None
    absolute = os.path.abspath(charter)
    if not os.path.isfile(absolute):
        raise Refusal("--charter %s is not a file" % charter)
    return absolute


GRAPH_SHAPE = "--graph %s must be a node-link graph with nodes and links"

GRAPH_KEY_TYPES = (str, int, float, bool, type(None))


def _graph_node(node):
    return (
        isinstance(node, dict)
        and isinstance(node.get("id"), GRAPH_KEY_TYPES)
        and isinstance(node.get("source_file"), (str, type(None)))
    )


def _graph_link(link):
    return (
        isinstance(link, dict)
        and isinstance(link.get("source"), GRAPH_KEY_TYPES)
        and isinstance(link.get("target"), GRAPH_KEY_TYPES)
    )


def node_link_graph(graph):
    return (
        isinstance(graph, dict)
        and isinstance(graph.get("nodes"), list)
        and isinstance(graph.get("links"), list)
        and all(_graph_node(node) for node in graph["nodes"])
        and all(_graph_link(link) for link in graph["links"])
    )


def load_graph(path):
    if path is None:
        return None
    graph = read_json(path, "--graph")
    if not node_link_graph(graph):
        raise Refusal(GRAPH_SHAPE % path)
    nodes_by_id = {node["id"]: node for node in graph["nodes"]}
    return {
        file: sorted(neighbours)
        for file, neighbours in core.build_file_coupling(nodes_by_id, graph["links"]).items()
    }


def unoffered_placeholders(name, template):
    offered = TEMPLATE_PLACEHOLDERS.get(name)
    if offered is None:
        return ()
    used = {match.group(1) for match in run.PLACEHOLDER.finditer(template)}
    return tuple(sorted(used - set(offered)))


TEMPLATE_FLAGS = {
    "dispatch": "--dispatch-command",
    "reask": "--reask-command",
    "decompose": "--decompose-command",
    "acceptance": "--acceptance-command",
    "gate-setup": "--gate-setup",
    "pull-request": "--pr-command",
}


def check_split(name, template):
    if not isinstance(template, str) or not template.strip():
        raise run.ConfigError("the %s command is empty" % name)
    try:
        shlex.split(template)
    except ValueError as error:
        raise run.ConfigError("the %s command does not split into argv: %s" % (name, error))


def check_template(name, template):
    flag = TEMPLATE_FLAGS[name]
    try:
        if name in TEMPLATE_PLACEHOLDERS:
            check_split(name, template)
        else:
            run.check_template(name, template)
    except run.ConfigError as error:
        raise Refusal("%s: %s" % (flag, error))
    unknown = unoffered_placeholders(name, template)
    if unknown:
        raise Refusal(
            "%s: the %s command uses %s, which it is never given; it offers %s"
            % (
                flag,
                name,
                ", ".join("{%s}" % key for key in unknown),
                ", ".join("{%s}" % key for key in TEMPLATE_PLACEHOLDERS[name]),
            )
        )


def check_templates(args):
    for name, template in (
        ("dispatch", args.dispatch_command),
        ("reask", args.reask_command),
        ("decompose", args.decompose_command),
        ("acceptance", args.acceptance_command),
        ("gate-setup", args.gate_setup),
        ("pull-request", args.pr_command),
    ):
        if template is None:
            continue
        check_template(name, template)
    if args.dispatch_command is not None:
        try:
            run.check_reply_templates(args.dispatch_command, args.reask_command)
        except run.ConfigError as error:
            raise Refusal(str(error))
    called = called_model_templates(args)
    for flag, template in called:
        check_model_template(flag, template)
    if called:
        check_reask_template(args.reask_command, tuple(flag for flag, _ in called))


def called_model_templates(args):
    from_document = not args.items
    decomposes = from_document and not args.resume and args.decompose_command is not None
    return (("--decompose-command", args.decompose_command),) if decomposes else ()


def missing_placeholders(template, needed):
    used = {match.group(1) for match in run.PLACEHOLDER.finditer(template or "")}
    return tuple("{%s}" % key for key in needed if key not in used)


def check_model_template(flag, template):
    missing = missing_placeholders(template, run.MODEL_TEMPLATE_NEEDS)
    if missing:
        raise Refusal(
            "%s lacks %s; a model command must carry {schema}, the call schema its reply meets "
            "as structured output, and {session_id}, the session a re-ask resumes"
            % (flag, ", ".join(missing))
        )


def check_reask_template(reask, flags):
    if reask is None:
        raise Refusal(
            "--reask-command is required: a reply from %s that fails its owned schema is asked "
            "again in the same session through it, offered %s"
            % (
                " or ".join(flags),
                ", ".join("{%s}" % key for key in run.TEMPLATE_PLACEHOLDERS[run.REASK_STEM]),
            )
        )
    missing = missing_placeholders(reask, run.REASK_TEMPLATE_NEEDS)
    if missing:
        raise Refusal(
            "--reask-command lacks %s; a re-ask must carry {prompt}, the errors to answer, "
            "{session_id}, the session to resume, and {schema}, the call schema to meet"
            % ", ".join(missing)
        )


def check_worker_writes(args, run_dir, repo, trees_root=None):
    try:
        isolation.refuse_overlapping_writes(run_dir, args.worker_write, repo, trees_root)
    except run.ConfigError as error:
        raise Refusal(str(error))


def trees_root_of(args, root):
    if args.worktree_root:
        return os.path.abspath(os.path.join(root, args.worktree_root))
    if args.no_run_dir:
        return os.path.join(default_input_run_dir(args, root), "trees")
    return None


def made_trees_root(path):
    if path is None:
        return None
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as error:
        raise Refusal("the worktree folder %s cannot be created: %s" % (path, error))
    return path


def check_document(args, root):
    path = document_path(args.spec, root)
    if not os.path.isfile(os.path.join(root, path)):
        raise Refusal("--spec %s is not a file" % args.spec)
    return path


def load_decisions(args, root, document):
    try:
        return decompose.load_decisions(
            args.working_directory, args.decisions, os.path.join(root, document)
        )
    except ValueError as error:
        raise Refusal(str(error))
    except OSError as error:
        raise Refusal("the decisions file is not readable: %s" % error)


def load_decomposed(run_dir):
    path = os.path.join(run_dir, DECOMPOSE_FILE)
    if not os.path.isfile(path):
        return None
    record = read_json(path, "the decompose record")
    return record if isinstance(record, dict) else None


def load_structure(run_dir, root, purpose):
    path = os.path.join(run_dir, STRUCTURE_FILE)
    if not os.path.isfile(path):
        raise Refusal(
            "%s: %s holds no %s; --plan-only writes the structure first"
            % (purpose, run_dir, STRUCTURE_FILE)
        )
    items = read_json(path, "the persisted structure")
    checked = core.validate(items, root=root, required=core.REQUIRED_ITEM_FIELDS)
    if checked["errors"]:
        raise Refusal(
            "the persisted structure %s did not validate:\n" % path
            + "\n".join("  " + line for line in checked["errors"])
        )
    return items


def _number(value):
    return "%.2f" % value if isinstance(value, float) else "%d" % value


def scalar_text(scored):
    return ", ".join("%s %s" % (key, _number(value)) for key, value in scored.items())


def sample_lines(results, order, chosen):
    lines = (
        "%d structure samples ranked by their scalars; sample %d is persisted"
        % (len(results), chosen + 1),
    )
    for place, index in enumerate(order, 1):
        result = results[index]
        errors = result.get("errors") or []
        detail = (
            "%s, so it is not scored" % _n(len(errors), "contract error")
            if errors
            else scalar_text(shape.scalars(result["items"]))
        )
        lines = lines + ("%d. sample %d: %s" % (place, index + 1, detail),)
    return lines + tuple(decompose.disagreements(results))


def chosen_sample(order, pick):
    return order[0] if pick is None else pick - 1


def recorder_of(args):
    return getattr(args, "recorder", metrics.UNRECORDED)


def refuse_unmapped_decompose(template, models):
    if "{model}" in template and models.get(DECOMPOSE_TIER) is None:
        raise Refusal(
            "the decompose command uses {model} but no model is mapped for tier %s; "
            "map it with --tier-model %s=<model>" % (DECOMPOSE_TIER, DECOMPOSE_TIER)
        )


def emit_items(path, steps):
    if path is None:
        return
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(steps, handle, indent=2)
    except OSError as error:
        raise Refusal("--emit-items %s is not writable: %s" % (path, error))


def structure_document(args, root, run_dir, models, charter, graph, decisions, prior):
    refuse_unmapped_decompose(args.decompose_command, models)
    model = models.get(DECOMPOSE_TIER)
    path = check_document(args, root)
    recorder = recorder_of(args)
    began = run.now()
    results = decompose.sample_structures(
        args.structure_samples,
        path,
        args.decompose_command,
        root,
        model=model,
        graph=graph,
        charter=charter,
        log=os.path.join(run_dir, DECOMPOSE_LOG),
        decisions=decisions,
        prior=prior,
        env=run.process_environ(recorder.env, root),
        on_sessions=lambda sessions: recorder.stage_ended("structure", began, sessions),
        reask=args.reask_command,
        graph_path=args.graph,
        prompt_template=args.recon_prompt_template,
    )
    order = decompose.rank(results)
    chosen = chosen_sample(order, args.pick)
    if len(results) > 1:
        _print(sample_lines(results, order, chosen))
    result = results[chosen]
    emit_items(args.emit_items, result.get("items") or [])
    _print(decompose.report(result))
    if result["errors"]:
        _print(("Coverage map:",) + tuple("  " + line for line in coverage_lines({}, result, root)))
        raise Refusal(
            "the structure stage returned %s; the log is %s:\n" % (
                _n(len(result["errors"]), "contract error"), result["log"]
            )
            + "\n".join("  " + line for line in result["errors"])
        )
    record = {key: result[key] for key in result if key != "items"}
    run.write_json(os.path.join(run_dir, DECOMPOSE_FILE), record)
    run.write_json(os.path.join(run_dir, STRUCTURE_FILE), result["items"])
    return result["items"], record


def staged(items, decomposed, run_dir, decisions, troubles=()):
    return {
        "items": items,
        "decomposed": decomposed,
        "run_dir": run_dir,
        "decisions": decisions,
        "troubles": tuple(troubles),
    }


def base_holds(args, repo):
    ref = args.feature_branch or "HEAD"
    return lambda path: repo is not None and run.git_ok(["cat-file", "-e", "%s:%s" % (ref, path)], repo)


def planning_shared_files(args):
    if args.plan_file is None:
        return shared_list(args.shared_files)
    loaded = read_json(args.plan_file, "--plan-file")
    return tuple(loaded.get("shared_files") or ()) if isinstance(loaded, dict) else ()


def owned_items(args, items, root, charter, run_dir):
    path = os.path.join(run_dir, ITEMS_FILE)
    if args.resume and os.path.isfile(path):
        return run.read_json(path)
    owned = own_tests.owned(items, core.charter_locks(charter, root)[0])
    run.write_json(path, owned)
    emit_items(args.emit_items, owned)
    return owned


def load_notices(run_dir):
    path = os.path.join(run_dir, NOTICES_FILE)
    return tuple(run.read_json(path)) if os.path.isfile(path) else ()


def refuse_held_run(run_dir):
    if os.path.isfile(os.path.join(run_dir, run.STATE_FILE)):
        raise Refusal(
            "%s already holds a run; pass --resume to continue it or --run-dir to start elsewhere"
            % run_dir
        )


def read_ask_lines(path):
    try:
        return asks.read_asks(path)
    except (OSError, ValueError) as error:
        raise Refusal("--asks %s is not readable: %s" % (path, error))


def planned_asks(args, root, models, run_dir):
    refuse_unmapped_decompose(args.decompose_command, models)
    path = os.path.abspath(args.asks)
    lines = read_ask_lines(path)
    recorder = recorder_of(args)
    began = run.now()
    result = asks.plan_asks(
        lines,
        args.decompose_command,
        root,
        model=models.get(DECOMPOSE_TIER),
        graph_path=args.graph,
        concurrency=args.concurrency,
        reask=args.reask_command,
        env=run.process_environ(recorder.env, root),
        log_dir=os.path.join(run_dir, RECON_DIR),
        prompt_template=args.recon_prompt_template,
        document=path,
    )
    recorder.stage_ended("structure", began, result["sessions"])
    if result["errors"]:
        raise Refusal(
            "the asks stage returned %s:\n" % _n(len(result["errors"]), "error")
            + "\n".join("  " + line for line in result["errors"])
        )
    run.write_json(os.path.join(run_dir, STRUCTURE_FILE), result["items"])
    notices = tuple(asks.notices(result))
    run.write_json(os.path.join(run_dir, NOTICES_FILE), list(notices))
    _print(notices, sys.stderr)
    return result["items"], notices


def asks_input(args, root, repo, models, charter, run_dir):
    if args.dry_run and not args.plan_only:
        refuse_undispatchable(args)
    elif not args.plan_only:
        refuse_unbuildable(args, repo)
    if args.resume:
        items, notices = load_structure(run_dir, root, "nothing to resume"), load_notices(run_dir)
    else:
        refuse_held_run(run_dir)
        items, notices = planned_asks(args, root, models, run_dir)
    owned = owned_items(args, items, root, charter, run_dir)
    return staged(owned, load_decomposed(run_dir), run_dir, None, notices)


def planned_with_lock(path):
    loaded = read_json(path, "--plan-file")
    return not isinstance(loaded, dict) or bool(loaded.get("check_authors"))


def folded_items(args, items):
    if args.plan_file is not None and planned_with_lock(args.plan_file):
        return items, ()
    return own_tests.folded(items)


def items_input(args, run_dir):
    items, notices = folded_items(args, read_items(os.path.abspath(args.items)))
    _print(tuple("mitosis: " + notice for notice in notices), sys.stderr)
    return staged(items, load_decomposed(run_dir), run_dir, None, notices)


def input_run_dir(args, root):
    if args.run_dir:
        return os.path.abspath(os.path.join(root, args.run_dir))
    return default_input_run_dir(args, root)


def private_run_dir():
    return os.path.realpath(tempfile.mkdtemp(prefix="mitosis-run-"))


def removed_private_run_dir(path):
    try:
        isolation.remove_tree(path)
    except OSError as error:
        _print(("mitosis: the private run folder %s stays: %s" % (path, error),), sys.stderr)


def default_input_run_dir(args, root):
    kind, path = next(
        (kind, path)
        for kind, path in (("items", args.items), ("asks", args.asks), ("spec", args.spec))
        if path
    )
    return os.path.abspath(default_run_dir(root, kind, file_digest(os.path.abspath(path))))


def resolve_input(args, root, repo, models, charter, graph, run_dir):
    if args.items:
        return items_input(args, run_dir)
    if args.asks:
        return asks_input(args, root, repo, models, charter, run_dir)
    document = check_document(args, root)
    decisions = load_decisions(args, root, document)
    if args.dry_run and not args.plan_only:
        refuse_undispatchable(args)
    elif not args.plan_only:
        refuse_unbuildable(args, repo)
    if args.resume:
        items = load_structure(run_dir, root, "nothing to resume")
        decomposed = load_decomposed(run_dir)
    else:
        refuse_held_run(run_dir)
        prior = load_structure(run_dir, root, "nothing to revise") if args.revise else None
        items, decomposed = structure_document(
            args, root, run_dir, models, charter, graph, decisions, prior
        )
    owned = owned_items(args, items, root, charter, run_dir)
    return staged(owned, decomposed, run_dir, decisions)


def marker_list(text):
    return tuple(part for part in text.split(",") if part)


def shared_list(text):
    return tuple(entry for entry in (part.strip() for part in (text or "").split(",")) if entry)


def planning_history(args):
    if args.no_trajectories:
        return ()
    return core.trajectory_store(args.trajectory or core.DEFAULT_TRAJECTORY_STORE)


def refused_steps(error):
    return Refusal(
        "the Steps did not validate:\n" + "\n".join("  " + line for line in error.errors)
    )


def build_plan(args, items, root, charter, graph):
    try:
        return core.plan(
            items,
            charter=charter,
            risk_markers=marker_list(args.risk_markers),
            serial_markers=marker_list(args.serial_markers),
            graph=graph,
            hops=args.context_hops,
            cap=args.context_cap,
            history=planning_history(args),
            root=root,
            coalesce_below=args.coalesce_below,
            coalesce_max=args.coalesce_max,
            briefs=not args.no_briefs,
            shared_files=shared_list(args.shared_files),
        )
    except core.ValidationError as error:
        raise refused_steps(error)


def saved_plan(path, items, charter, root):
    loaded = read_json(path, "--plan-file")
    if not isinstance(loaded, dict):
        raise Refusal("--plan-file %s does not hold a JSON object" % path)
    missing = tuple(key for key in SAVED_PLAN_KEYS if key not in loaded)
    if missing:
        raise Refusal("--plan-file %s lacks %s" % (path, ", ".join(missing)))
    try:
        steps = core.locked_steps(items, charter, root, loaded.get("shared_files") or ())
    except core.ValidationError as error:
        raise refused_steps(error)
    if steps.authors and not loaded.get("check_authors"):
        raise Refusal(
            "--plan-file %s has no check_authors, but the items hold the check-author Step %s"
            % (path, steps.authors[0].get("name"))
        )
    claimed = core.saved_slice_errors(loaded.get("check_authors") or (), steps)
    if claimed:
        raise Refusal("--plan-file %s: %s" % (path, "; ".join(claimed)))
    locked = core._merged_locks(loaded.get("locked") or (), steps.locked)
    if not locked and "locked" not in loaded:
        return loaded
    return {**loaded, "locked": list(locked)}


def planned(args, items, root, charter, graph):
    if args.plan_file is None:
        return build_plan(args, items, root, charter, graph)
    return saved_plan(args.plan_file, items, charter, root)


def refuse_undispatchable(args):
    if args.dispatch_command is None:
        raise Refusal(
            "--dry-run needs --dispatch-command to print the argv each Lane would be "
            "dispatched with"
        )


def dispatch_order(plan):
    lanes = plan.get("lanes") or []
    edges = run.lane_edges(plan)
    listed = [int(lane) for lane in plan.get("lane_order") or range(len(lanes))]
    pending = tuple(listed + [lane for lane in range(len(lanes)) if lane not in listed])
    order = ()
    while pending:
        ready = next(
            (lane for lane in pending if all(producer in order for producer in edges.get(lane, ()))),
            None,
        )
        if ready is None:
            raise Refusal(
                "Lanes %s wait on producers that never run"
                % ", ".join(str(lane) for lane in pending)
            )
        order = order + (ready,)
        pending = tuple(lane for lane in pending if lane != ready)
    return order


def dry_run_lines(args, plan, models, run_dir):
    slugs = run.msp_slugs(plan["msps"])
    lines = ()
    for lane in dispatch_order(plan):
        msp = plan["lanes"][lane]["msp"]
        entry = plan["msps"][msp]
        tree = {
            "msp": msp,
            "label": entry.get("label") if isinstance(entry, dict) else None,
            "branch": "%s/%s" % (run.BRANCH_PREFIX, slugs[msp]),
            "path": isolation.lane_folders(run_dir, lane)["clone"],
        }
        values = run._worker_values(
            plan, lane, tree, models, run_dir, core.new_session_id(), args.prompt_template
        )
        lines = lines + (
            "dry run: Lane %d (%s): %s"
            % (
                lane,
                ", ".join(plan["lanes"][lane]["steps"]),
                json.dumps(run.build_argv(args.dispatch_command, values)),
            ),
        )
    return lines


def missing_run_flags(args):
    return tuple(flag for flag, attribute in RUN_FLAGS if getattr(args, attribute) is None)


def missing_push_flags(args):
    if args.no_push:
        return ()
    return tuple(flag for flag, attribute in PUSH_FLAGS if not getattr(args, attribute))


def refuse_unbuildable(args, repo):
    if repo is None:
        raise Refusal("the working directory is not inside a git repository")
    if args.no_push and args.pr_command is not None:
        raise Refusal(
            "--no-push opens no pull request, so --pr-command would never run; pass one of them"
        )
    missing = missing_run_flags(args)
    if missing:
        raise Refusal("a run needs %s; pass --plan-only to stop at the plan" % ", ".join(missing))
    refuse_untitled(args)
    if not run.branch_exists(repo, args.feature_branch):
        raise Refusal("--feature-branch %s is not a branch of %s" % (args.feature_branch, repo))


def refuse_untitled(args):
    unwritten = missing_push_flags(args)
    if unwritten:
        raise Refusal(
            "a run allowed to push needs %s to title and describe each pull request; pass "
            "--no-push to hold the branches instead" % ", ".join(unwritten)
        )


def check_pull_request_flags(args):
    if args.pr_type is not None and args.pr_type not in run.PR_TYPES:
        raise Refusal(
            "--pr-type %r is not one of %s" % (args.pr_type, ", ".join(run.PR_TYPES))
        )
    whys = args.pr_why or ()
    if len(whys) > run.SECTION_LIMIT:
        raise Refusal(
            "--pr-why is given %d times; a pull request's problem takes at most %d lines"
            % (len(whys), run.SECTION_LIMIT)
        )
    if (args.pr_door is None) != (args.pr_blast_radius is None):
        raise Refusal(
            "--pr-door and --pr-blast-radius go together or not at all; a door call without its "
            "blast radius, or a blast radius without its door call, tells a reviewer half the "
            "merge danger"
        )
    if args.pr_door is not None and args.pr_door not in run.PR_DOORS:
        raise Refusal("--pr-door %r is not one of %s" % (args.pr_door, ", ".join(run.PR_DOORS)))


def pull_request_header(args):
    if args.pr_type is None or args.pr_subject is None:
        return None
    return {
        "type": args.pr_type,
        "scope": args.pr_scope,
        "subject": args.pr_subject,
        "why": tuple(args.pr_why or ()),
        "door": args.pr_door,
        "blast_radius": args.pr_blast_radius,
        "impact": tuple(args.pr_impact or ()),
    }


def run_header(args, plan, run_dir):
    try:
        return run.carried_header(
            pull_request_header(args), run.prior_state(run_dir, plan, args.resume)
        )
    except (run.ResumeError, handoff.HandoffError) as error:
        raise Refusal(str(error))


def refuse_unrenderable(args, plan, run_dir, repo=None):
    header = run_header(args, plan, run_dir)
    if header is None:
        return
    if not header["why"]:
        refuse_untitled(args)
    if not header["impact"] and header["blast_radius"] is None:
        raise Refusal(
            "a run that opens pull requests needs --pr-impact, what callers or users notice, "
            "or --pr-blast-radius for render's impact"
        )
    env = run.process_environ(recorder_of(args).env, repo)
    for index in range(len(plan.get("msps") or [])):
        try:
            pr_facts.render(
                args.render_command,
                pr_facts.probe_facts(header, plan, index),
                os.path.join(run_dir, "probe-%d.json" % index),
                env,
            )
        except pr_facts.RenderError as error:
            raise Refusal("MSP %s: %s" % (_label(plan, index), error))


def proven_heads(values, plan):
    labels = {run._label(plan, index): index for index in range(len(plan.get("msps") or []))}
    pairs = tuple(PROVEN_HEAD.fullmatch(value).groups() for value in values)
    unknown = next((label for label, _ in pairs if label not in labels), None)
    if unknown is not None:
        raise UnknownProvenHead(
            "--proven-head names MSP %s, which the plan does not hold" % unknown
        )
    return {labels[label]: commit for label, commit in pairs}


def _class_label(position, entry):
    test = entry.get("test") if isinstance(entry, dict) else None
    if isinstance(test, str):
        return "entry %d (%s)" % (position, json.dumps(test[:200]))
    return "entry %d" % position


def plan_properties(plan):
    return frozenset(
        run.property_key(entry["file"], entry["test"])
        for item in plan.get("items") or []
        for entry in item.get("acceptance") or []
    )


def _class_fault(entry, properties, seen):
    if not isinstance(entry, dict) or sorted(entry) != sorted(GATE_CLASS_KEYS):
        return "is not an object holding exactly the keys %s" % ", ".join(GATE_CLASS_KEYS)
    test, name, reason = entry["test"], entry["class"], entry["reason"]
    if not isinstance(test, str) or test not in properties:
        return "names no acceptance property of the plan"
    if test in seen:
        return "names a property an earlier entry already classes"
    if name not in run.GATE_CLASSES:
        return "has class %s, which is none of %s" % (
            json.dumps(name)[:80],
            ", ".join(run.GATE_CLASSES),
        )
    if not isinstance(reason, str) or not reason.strip():
        return "has no reason"
    if len(reason.splitlines()) != 1:
        return "has a reason of more than one line"
    if len(reason) > GATE_CLASS_REASON_LIMIT:
        return "has a reason longer than %d characters" % GATE_CLASS_REASON_LIMIT
    return None


def gate_classes(path, plan):
    if path is None:
        return None, None
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as error:
        raise Refusal("--gate-classes %s could not be read: %s" % (path, error.strerror))
    try:
        loaded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Refusal("--gate-classes %s is not UTF-8 JSON" % path)
    if not isinstance(loaded, list):
        raise Refusal("--gate-classes %s does not hold a JSON list" % path)
    properties = plan_properties(plan)
    seen = frozenset()
    for position, entry in enumerate(loaded, 1):
        fault = _class_fault(entry, properties, seen)
        if fault is not None:
            raise Refusal(
                "--gate-classes %s: %s %s" % (path, _class_label(position, entry), fault)
            )
        seen = seen | {entry["test"]}
    classes = tuple(
        {"test": entry["test"], "class": entry["class"], "reason": entry["reason"]}
        for entry in loaded
    )
    return classes, hashlib.sha256(raw).hexdigest()


def refuse_run(args, plan, models, run_dir, repo):
    refuse_unbuildable(args, repo)
    refuse_unmapped(plan, args.dispatch_command, models)
    if os.path.isfile(os.path.join(run_dir, run.STATE_FILE)) and not args.resume:
        raise Refusal(
            "%s already holds a run; pass --resume to continue it or --run-dir to start elsewhere"
            % run_dir
        )


def _n(count, noun):
    return "%d %s" % (count, noun if count == 1 else noun + "s")


def _one_line(text):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return lines[0] if lines else ""


def _whole(text):
    return "" if text is None else str(text)


def read_document(path):
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as error:
        return False, str(error)
    try:
        return True, raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return True, None


def section_label(section):
    if section["id"] == section["title"]:
        return section["title"]
    return "%s %s" % (section["id"], section["title"])


def coverage_lines(plan, decomposed, root):
    covered = (decomposed or {}).get("coverage")
    if not isinstance(covered, dict):
        source = plan.get("source")
        if not isinstance(source, dict) or not source.get("path"):
            return ("unavailable: the Steps declare no source document, so there are no sections to claim",)
        readable, text = read_document(os.path.join(root, source["path"]))
        if not readable:
            return ("unavailable: %s could not be read (%s)" % (source["path"], text),)
        covered = decompose.coverage(text, plan.get("items") or [])
    if covered.get("mode") == "none":
        return ("unavailable: %s" % covered.get("reason"),)
    sections = covered.get("sections") or []
    uncovered = covered.get("uncovered") or []
    lines = (
        "by %s: %d of %s claimed, %d unclaimed"
        % (covered.get("mode"), len(sections) - len(uncovered), _n(len(sections), "section"), len(uncovered)),
    )
    lines = lines + tuple("unclaimed: " + section_label(section) for section in uncovered)
    unmatched = covered.get("unmatched_claims") or []
    if unmatched:
        lines = lines + ("%s match no section:" % _n(len(unmatched), "spec_ref claim"),)
        lines = lines + tuple("  " + claim for claim in unmatched)
    return lines


def assumption_lines(plan):
    found = tuple(
        (item["name"], text)
        for item in plan.get("items") or []
        for text in (item.get("assumptions") or [])
    )
    if not found:
        return ("none declared on any Step",)
    steps = len({name for name, _ in found})
    return ("%s chosen across %s" % (_n(len(found), "reading"), _n(steps, "Step")),) + tuple(
        "%s: %s" % pair for pair in found
    )


def decision_lines(decisions, from_items, root):
    if from_items:
        return (
            "unavailable: decisions reach the structure prompt, and this run took an items file",
        )
    if decisions is None:
        return ("none: no decisions file was supplied",)
    shown = document_path(decisions.get("path"), root)
    return ("%s: %s" % (shown, _n(decisions.get("count") or 0, "settled question")),)


def constraint_lines(decomposed, from_items):
    if decomposed is None:
        if from_items:
            return ("unavailable: constraints are extracted by decompose, and this run took an items file",)
        return ("unavailable: the run directory holds no decompose record",)
    constraints = decomposed.get("constraints") or []
    return ("%s extracted" % _n(len(constraints), "global statement"),) + tuple(
        str(constraint) for constraint in constraints
    )


def drift_lines(plan, root):
    found = run.document_drift(plan, root)
    if found is None:
        return ("unavailable: the Steps declare no source document to compare against",)
    if found["actual"] is None:
        return (
            "%s is missing; it hashed to %s when the Steps were written"
            % (found["path"], found["expected"]),
        )
    if found["drifted"]:
        return (
            "%s has changed since the Steps were written (sha256 %s then, %s now)"
            % (found["path"], found["expected"], found["actual"]),
        )
    return ("none: %s still hashes to %s" % (found["path"], found["actual"]),)


def lane_width(plan):
    return shape.scalars(plan.get("items") or [], plan.get("shared_files"))["parallelism"]


def shape_lines(items, shared_files=()):
    return tuple(
        "%s: %s" % (finding["kind"], finding["detail"])
        for finding in shape.findings(items, shared_files)
    ) + (scalar_text(shape.scalars(items, shared_files)),)


def empty_manifests(items):
    return [gap for gap in shape.manifest_gaps(items) if gap["reached"] == 0]


def _moment(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def achieved_parallelism(records):
    spans = tuple(
        (_moment(record.get("started")), _moment(record.get("finished")))
        for record in (records or {}).values()
        if isinstance(record, dict)
    )
    spans = tuple((a, b) for a, b in spans if a is not None and b is not None)
    events = sorted(
        [(a, 1) for a, _ in spans] + [(b, -1) for _, b in spans], key=lambda e: (e[0], e[1])
    )
    peak = 0
    live = 0
    for _, delta in events:
        live = live + delta
        peak = max(peak, live)
    return peak, len(spans)


def lock_stage_lines(plan):
    return tuple(
        "lock stage: the %s Step %s writes and locks %s for MSP %s as the first commit of its "
        "branch before its Lanes, and they build with them read-only"
        % (core.CHECK_AUTHOR, entry["name"], ", ".join(entry["slice"]), entry["label"])
        for entry in plan.get("check_authors") or ()
    )


def split_lines(plan, state):
    lanes = plan.get("lanes") or []
    msps = plan.get("msps") or []
    counts = plan.get("counts") or {}
    total = len(plan.get("items") or [])
    lines = (
        "%s in %s across %s"
        % (_n(total, "Step"), _n(len(lanes), "Lane"), _n(len(msps), "MSP")),
        "%d of %s declare no acceptance property; %s do not exist yet"
        % (counts.get("no_acceptance", 0), _n(total, "Step"), _n(counts.get("missing_paths", 0), "write-set path")),
    )
    lines = lines + lock_stage_lines(plan)
    lines = lines + tuple(
        "Lane %d (MSP %s, %s): %s, cost %s%s: %s"
        % (
            index,
            _label(plan, lane["msp"]),
            lane.get("tier"),
            _n(len(lane.get("steps") or []), "Step"),
            lane.get("cost"),
            ", session %d of %d" % tuple(lane["session"]) if lane.get("session") else "",
            ", ".join(lane.get("steps") or []),
        )
        for index, lane in enumerate(lanes)
    )
    lines = lines + ("parallelism available: up to %s at once" % _n(lane_width(plan), "Lane"),)
    if state is None:
        return lines + ("parallelism achieved: unavailable; no run has happened",)
    peak, ran = achieved_parallelism(state.get("lanes"))
    return lines + (
        "parallelism achieved: peak of %s at once; %d of %s ran"
        % (_n(peak, "Lane"), ran, _n(len(lanes), "Lane")),
    )


def _label(plan, index):
    msps = plan.get("msps") or []
    if index < len(msps) and msps[index].get("label"):
        return msps[index]["label"]
    return str(index)


def _tally(counts):
    return ", ".join("%d %s" % (counts.get(key, 0), key) for key in core.GATE_OUTCOMES)


def _mutant_line(record, mutation=None):
    if not isinstance(record, dict):
        return None
    killed = record.get("killed")
    tried = _n(record.get("tried") or 0, "mutant") + " tried"
    if mutation == "not-applicable" and not record.get("tried"):
        return "no mutant tried: the test loaded none of the Step's Python files"
    if mutation == "not-applicable":
        return tried + ", none killed it; the Step added no code, so mutation does not apply"
    if mutation == "covered-elsewhere":
        return tried + ", none killed it; the caller classed it as covered elsewhere"
    if not isinstance(killed, dict):
        return tried + ", none killed it"
    return tried + ", killed by %s:%s changed by %s" % (
        killed.get("file"),
        killed.get("line"),
        killed.get("op"),
    )


def gate_lines(plan, state):
    if state is None:
        return ("unavailable: no run has happened",)
    totals = dict.fromkeys(core.GATE_OUTCOMES, 0)
    lines = ()
    gated = 0
    for index in range(len(plan.get("msps") or [])):
        record = (state.get("msps") or {}).get(str(index)) or {}
        gate = record.get("gate")
        if not isinstance(gate, dict):
            lines = lines + (
                "MSP %s: not gated (%s)"
                % (_label(plan, index), _one_line(record.get("reason")) or record.get("state") or "never reached"),
            )
            continue
        gated = gated + 1
        counts = gate.get("counts") or {}
        totals = {key: totals[key] + counts.get(key, 0) for key in totals}
        lines = lines + ("MSP %s: %s (%s)" % (_label(plan, index), gate.get("outcome"), _tally(counts)),)
        for entry in gate.get("properties") or []:
            tried = _mutant_line(entry.get("mutants"), entry.get("mutation"))
            if entry.get("outcome") == "pass" and tried is None:
                continue
            where = "%s::%s" % (entry.get("file"), entry.get("test")) if entry.get("file") else entry.get("step")
            detail = "; ".join(
                part for part in (_one_line(entry.get("reason")), tried) if part
            )
            lines = lines + ("  %s: %s (%s)" % (where, entry.get("outcome"), detail),)
    return ("%s across %s" % (_tally(totals), _n(gated, "gated MSP")),) + lines


def reconcile_lines(plan, state):
    if state is None:
        return ("unavailable: no run has happened",)
    lines = ()
    findings = 0
    for index in range(len(plan.get("msps") or [])):
        record = (state.get("msps") or {}).get(str(index)) or {}
        found = record.get("reconcile")
        if not isinstance(found, dict):
            lines = lines + (
                "MSP %s: not reconciled (%s)"
                % (_label(plan, index), _one_line(record.get("reason")) or record.get("state") or "never reached"),
            )
            continue
        undeclared = found.get("undeclared") or []
        unwritten = found.get("unwritten") or []
        untouched = found.get("untouched") or []
        crossing = found.get("crossing") or []
        written = found.get("referee") or []
        findings = findings + len(undeclared) + len(unwritten)
        lines = lines + (
            "MSP %s: %d undeclared, %d unwritten, %d crossing an MSP boundary,"
            " %d left unchanged on purpose"
            % (
                _label(plan, index),
                len(undeclared),
                len(unwritten),
                len(crossing),
                len(untouched),
            ),
        )
        lines = lines + tuple("  undeclared: " + path for path in undeclared)
        lines = lines + tuple("  unwritten: " + path for path in unwritten)
        lines = lines + tuple("  untouched: " + path for path in untouched)
        lines = lines + tuple(
            "  crossing: %s belongs to MSP %s" % (entry.get("path"), entry.get("label"))
            for entry in crossing
        )
        lines = lines + tuple("  referee: " + path for path in written)
        lines = lines + tuple(
            "  shared: %s is also changed by MSP %s; integrate in the order %s"
            % (
                entry.get("path"),
                ", ".join(entry.get("also_in") or ()),
                ", ".join(entry.get("integration_order") or ()),
            )
            for entry in found.get("shared_overlaps") or ()
        )
    return ("%s from the git log" % _n(findings, "finding"),) + lines


def coupling_lines(plan, state):
    pairs = plan.get("coupling_review") or []
    if not pairs:
        return ("none: no write-set-disjoint pair in different Lanes shares a coupling signal",)
    verb = "ran" if state is not None else "run"
    return tuple(_coupling_pair_line(pair, verb) for pair in pairs)


def _coupling_pair_line(pair, verb):
    named = "%s and %s (Lanes %s) %s in parallel" % (
        pair["steps"][0],
        pair["steps"][1],
        ", ".join(str(lane) for lane in pair.get("lanes") or []),
        verb,
    )
    return COUPLING_LINE[_coupling_form(pair)] % (named, " and ".join(pair.get("signals") or []))


COUPLING_LINE = {
    "parallel": "%s with only %s between them; add an after edge if one needs the other's output",
    "serialize": "%s awaiting the orchestrator's verdict on %s, default serialize; add an after edge "
    "to serialize them",
}


def stacking_lines(plan, state):
    if state is None:
        producers = core.msp_producers(plan)
        many = tuple((msp, found) for msp, found in sorted(producers.items()) if len(found) > 1)
        if not many:
            return ("none predicted: every MSP has at most one producer",)
        return ("%d predicted from the plan" % len(many),) + tuple(
            "MSP %s has %s (%s), so its pull request will target the feature branch"
            % (_label(plan, msp), _n(len(found), "producer"), ", ".join(_label(plan, p) for p in found))
            for msp, found in many
        )
    exceptions = state.get("stacking_exceptions") or []
    if not exceptions:
        return ("none recorded",)
    return ("%d recorded" % len(exceptions),) + tuple(
        "MSP %s: %s (producers %s)"
        % (
            entry.get("label") or entry.get("msp"),
            _one_line(entry.get("reason")),
            ", ".join(_label(plan, p) for p in entry.get("producers") or []),
        )
        for entry in exceptions
    )


def reconcile_dirty(findings):
    if not isinstance(findings, dict):
        return True
    return bool(findings.get("crossing") or findings.get("fatal"))


def _finding(msp, lane, kind, code, detail):
    return {"msp": msp, "lane": lane, "kind": kind, "exit": code, "detail": _whole(detail)}


def _lane_findings(plan, lane_records):
    found = []
    for index, lane in enumerate(plan.get("lanes") or []):
        label = _label(plan, lane["msp"])
        record = lane_records.get(str(index))
        if not isinstance(record, dict):
            found = found + [_finding(label, index, "incomplete", EXIT_INCOMPLETE, "Lane %d has no record" % index)]
        elif record.get("state") != "ok":
            found = found + [
                _finding(
                    label,
                    index,
                    record.get("state") or "incomplete",
                    LANE_EXIT.get(record.get("state"), EXIT_INCOMPLETE),
                    record.get("reason") or "",
                )
            ]
    return found


def _gate_detail(record):
    named = [
        "%s::%s: %s" % (entry.get("file"), entry.get("test"), _whole(entry.get("reason")))
        for entry in run.failing_properties(record.get("gate"))
    ]
    return "; ".join(named) or record.get("reason") or record.get("gate_error") or ""


def _gate_findings(label, record):
    if record.get("state") != run.MSP_GATE_FAILED:
        return []
    return [_finding(label, None, run.GATE_HELD, EXIT_HELD, _gate_detail(record))]


def _reconcile_findings(label, found):
    return (
        [_finding(label, None, "referee", EXIT_REFEREE, path) for path in found.get("referee") or []]
        + [
            _finding(
                label,
                None,
                "crossing",
                EXIT_CROSSING,
                "%s belongs to MSP %s" % (entry.get("path"), entry.get("label")),
            )
            for entry in found.get("crossing") or []
        ]
    )


def _msp_findings(plan, index, record, lane_records):
    label = _label(plan, index)
    if not isinstance(record, dict):
        return [_finding(label, None, "incomplete", EXIT_INCOMPLETE, "MSP %s has no record" % label)]
    state = record.get("state")
    stated = (
        []
        if state in core.DELIVERED_STATES or state == run.MSP_GATE_FAILED
        else [
            _finding(
                label, None, state or "incomplete", MSP_EXIT.get(state, EXIT_INCOMPLETE), record.get("reason") or ""
            )
        ]
    )
    found = record.get("reconcile") if isinstance(record.get("reconcile"), dict) else {}
    foreign = [
        _finding(label, entry["lane"], "foreign", EXIT_FOREIGN, entry.get("path"))
        for entry in run.msp_foreign_writes(plan, index, lane_records)
    ]
    return (
        stated
        + _gate_findings(label, record)
        + _reconcile_findings(label, found)
        + foreign
        + [_finding(label, None, "undeclared", EXIT_RECONCILE, path) for path in found.get("undeclared") or []]
        + (
            [_finding(label, None, "unwritten", EXIT_RECONCILE, path) for path in found.get("unwritten") or []]
            if state == run.MSP_UNCHANGED
            else []
        )
    )


LOCK_EXIT = {run.MSP_PARKED: MSP_EXIT[run.MSP_PARKED]}


def locks(state, plan):
    return tuple(
        (msp, run.lock_of(state, msp))
        for msp in range(len(plan.get("msps") or []))
        if run.lock_of(state, msp) is not None
    )


def lock_stopped(state, plan):
    return any(lock.get("state") not in run.LOCK_FALLS_BACK for _, lock in locks(state, plan))


def _lock_findings(state, plan):
    found = []
    for msp, lock in locks(state, plan):
        label = _label(plan, msp)
        if lock.get("state") not in run.LOCK_FALLS_BACK:
            found = found + [
                _finding(
                    label,
                    None,
                    "lock-" + (lock.get("state") or "incomplete"),
                    LOCK_EXIT.get(lock.get("state"), EXIT_INCOMPLETE),
                    lock.get("reason") or "",
                )
            ]
    return found


def findings(state, plan):
    if state is None:
        return []
    lane_records = state.get("lanes") or {}
    msp_records = state.get("msps") or {}
    msps = [
        entry
        for index in range(len(plan.get("msps") or []))
        for entry in _msp_findings(plan, index, msp_records.get(str(index)), lane_records)
    ]
    return _lock_findings(state, plan) + _lane_findings(plan, lane_records) + msps


NOISE_KINDS = ("undeclared",)


def exit_code(state, plan):
    if not plan.get("msps") or not ((state or {}).get("worktrees") or lock_stopped(state, plan)):
        return EXIT_NO_BRANCHES
    carried = {entry["exit"] for entry in findings(state, plan) if entry["kind"] not in NOISE_KINDS}
    worst = next((code for code in EXIT_SEVERITY if code in carried), None)
    if worst is not None:
        return worst
    return EXIT_SHIPPED if run.succeeded(state) else EXIT_INCOMPLETE


def outcome_lines(plan, state, run_dir, code):
    lines = ("run directory: %s" % (run_dir or "none"), "plan id: %s" % plan.get("plan_id"))
    if state is None:
        return lines + (
            "exit %d: %s"
            % (
                code,
                EXIT_MEANING[code]
                if code != EXIT_SHIPPED
                else "the plan was written and nothing was spawned",
            ),
        )
    lines = lines + ("sandbox: %s" % sandbox_of(state),)
    for msp, lock in locks(state, plan):
        reason = _one_line(lock.get("reason"))
        lines = lines + (
            "lock stage of MSP %s: %s%s"
            % (_label(plan, msp), lock.get("state"), " (%s)" % reason if reason else ""),
        )
    lane_records = state.get("lanes") or {}
    for index in range(len(plan.get("lanes") or [])):
        record = lane_records.get(str(index)) or {}
        reason = _one_line(record.get("reason"))
        lines = lines + (
            "Lane %d: %s%s" % (index, record.get("state") or "never ran", " (%s)" % reason if reason else ""),
        )
    msp_records = state.get("msps") or {}
    for index in range(len(plan.get("msps") or [])):
        record = msp_records.get(str(index)) or {}
        shipped = record.get("ship") or {}
        detail = _one_line(record.get("reason")) or (
            "%s -> %s" % (shipped.get("pull_request") or shipped.get("branch"), shipped.get("base"))
            if shipped
            else None
        )
        lines = lines + (
            "MSP %s: %s%s"
            % (_label(plan, index), record.get("state") or "never reached", " (%s)" % detail if detail else ""),
        )
    return lines + ("exit %d: %s" % (code, EXIT_MEANING.get(code, "not shipped")),)


def _lane_summary(plan, state, outputs_kept=True):
    records = (state or {}).get("lanes") or {}
    return [
        {
            "lane": index,
            "msp": _label(plan, lane["msp"]),
            "steps": list(lane.get("steps") or ()),
            "session": None,
            "state": (records.get(str(index)) or {}).get("state"),
            "reason": _whole((records.get(str(index)) or {}).get("reason")),
            "notes": ((records.get(str(index)) or {}).get("returned") or {}).get("notes"),
            "foreign_writes": list((records.get(str(index)) or {}).get("foreign_writes") or ()),
            "stdout": (records.get(str(index)) or {}).get("stdout") if outputs_kept else None,
            "stderr": (records.get(str(index)) or {}).get("stderr") if outputs_kept else None,
        }
        for index, lane in enumerate(plan.get("lanes") or [])
    ]


def _referee_verdict(findings):
    if not isinstance(findings, dict):
        return {"referee": None, "fatal": None}
    return {"referee": list(findings.get("referee") or ()), "fatal": bool(findings.get("fatal"))}


def _msp_summary(plan, state):
    records = (state or {}).get("msps") or {}
    summaries = []
    for index in range(len(plan.get("msps") or [])):
        record = records.get(str(index)) or {}
        shipped = record.get("ship") or {}
        gate = record.get("gate") or {}
        findings = record.get("reconcile") or {}
        summaries.append(
            {
                "msp": _label(plan, index),
                "state": record.get("state"),
                "reason": _whole(record.get("reason")),
                "branch": shipped.get("branch"),
                "base": shipped.get("base"),
                "pull_request": shipped.get("pull_request") or None,
                "title": shipped.get("title"),
                "body": shipped.get("body"),
                "gate": {
                    "outcome": gate.get("outcome"),
                    "counts": gate.get("counts") or {},
                    "error": record.get("gate_error"),
                    "commit": gate.get("commit"),
                    "properties": [
                        {
                            **{
                                key: entry.get(key)
                                for key in ("file", "test", "step", "outcome", "reason", "mutants")
                            },
                            **{
                                key: entry[key]
                                for key in ("mutation", "coverage")
                                if key in entry
                            },
                        }
                        for entry in gate.get("properties") or []
                    ],
                    "unproven": [
                        "%s::%s %s" % (entry.get("file"), entry.get("test"), entry.get("outcome"))
                        for entry in gate.get("properties") or []
                        if entry.get("outcome") in run.UNPROVEN_OUTCOMES
                    ],
                },
                "reconcile": {
                    **{
                        key: list(findings.get(key) or ())
                        for key in ("undeclared", "unwritten", "untouched")
                    },
                    **_referee_verdict(record.get("reconcile")),
                    **(
                        {"shared_overlaps": list(findings["shared_overlaps"])}
                        if findings.get("shared_overlaps")
                        else {}
                    ),
                },
                "crossing": [entry.get("path") for entry in findings.get("crossing") or ()],
                "ignored": list(record.get("ignored") or ()),
                "trajectory_error": record.get("trajectory_error"),
                "lock": _lock_summary(record.get("lock")),
                "referee": _referee_summary(record.get("referee")),
            }
        )
    return summaries


LOCK_SUMMARY_KEYS = ("state", "reason", "commit", "session_id", "probes", "undeclared")

REFEREE_KEYS = ("locked", "receipt_lock", "commit")


def _lock_summary(lock):
    if not isinstance(lock, dict):
        return None
    return {
        key: list(lock.get(key) or ()) if key in ("probes", "undeclared") else lock.get(key)
        for key in LOCK_SUMMARY_KEYS
    }


def _referee_summary(record):
    if not isinstance(record, dict):
        return None
    return {key: record.get(key) for key in REFEREE_KEYS}


def _lock_attention(state, plan):
    lines = []
    for msp, lock in locks(state, plan):
        label = _label(plan, msp)
        if lock.get("state") != "ok":
            lines = lines + [
                "the lock of MSP %s ended %s: %s"
                % (label, lock.get("state"), _one_line(lock.get("reason")))
            ]
        if lock.get("undeclared"):
            lines = lines + [
                "the check-author of MSP %s wrote outside its locked slice and probe files, and "
                "mitosis landed none of it: %s" % (label, ", ".join(lock["undeclared"]))
            ]
    return lines


def _attention(plan, lanes, msps):
    lines = [
        "Lane %d (%s) is %s: %s"
        % (lane["lane"], ", ".join(lane["steps"]), lane["state"], lane["reason"] or "")
        for lane in lanes
        if lane["state"] not in ("ok", None)
    ] + [
        run.foreign_sentence(plan, entry)
        for entry in run.unique_foreign(
            [{**entry, "lane": lane["lane"]} for lane in lanes for entry in lane["foreign_writes"]]
        )
    ]
    for msp in msps:
        if msp["state"] not in (
            "shipped",
            run.MSP_PUSHED,
            run.MSP_UNCHANGED,
            run.MSP_COMMITTED,
            None,
        ):
            lines.append("MSP %s is %s: %s" % (msp["msp"], msp["state"], msp["reason"] or ""))
        if msp["gate"]["unproven"]:
            lines.append(
                "MSP %s carries unproven acceptance on its branch: %s"
                % (msp["msp"], ", ".join(msp["gate"]["unproven"]))
            )
        if msp["gate"]["error"]:
            lines.append("MSP %s could not be gated: %s" % (msp["msp"], msp["gate"]["error"]))
        lines.extend(
            "MSP %s: %s::%s passed on red and green alone; its Step added no line to mutate"
            % (msp["msp"], entry["file"], entry["test"])
            for entry in msp["gate"]["properties"]
            if entry["outcome"] == "pass"
            and (entry["mutants"] or {}).get("tried") == 0
            and entry.get("mutation") != "not-applicable"
        )
        lines.extend(
            "MSP %s: %s::%s passed without a mutant; its test loaded none of the Step's Python "
            "files" % (msp["msp"], entry["file"], entry["test"])
            for entry in msp["gate"]["properties"]
            if entry["outcome"] == "pass"
            and (entry["mutants"] or {}).get("tried") == 0
            and entry.get("mutation") == "not-applicable"
        )
        if msp["crossing"]:
            lines.append(
                "MSP %s wrote files another MSP owns: %s" % (msp["msp"], ", ".join(msp["crossing"]))
            )
        if msp.get("trajectory_error"):
            lines.append(
                "MSP %s's outcome entries were not appended to the trajectory: %s"
                % (msp["msp"], msp["trajectory_error"])
            )
    return lines


COUPLING_NAMED = 8


def _coupling_summary(plan):
    return {
        "ordered": [],
        "parallel": [
            {"steps": list(pair["steps"]), "signals": list(pair["signals"])}
            for pair in plan.get("coupling_review") or []
        ],
    }


COUPLING_ATTENTION = {
    "parallel": "%s run in parallel with only %s between them: %s%s; add an after edge where one "
    "needs the other's output",
    "serialize": "%s await the orchestrator's verdict on %s, default serialize: %s%s; add an after "
    "edge to serialize them",
}


def _coupling_form(pair):
    if pair.get("default") not in COUPLING_ATTENTION:
        raise ValueError(
            "coupling pair %s has an unknown default %r" % ("+".join(pair["steps"]), pair.get("default"))
        )
    return pair["default"]


def _coupling_attention(review):
    keyed = sorted(((_coupling_form(pair), tuple(pair["signals"])), "+".join(pair["steps"])) for pair in review)
    return [
        COUPLING_ATTENTION[default]
        % (
            _n(len(names), "pair") + " of Steps",
            " and ".join(signals),
            ", ".join(names[:COUPLING_NAMED]),
            " and %d more" % (len(names) - COUPLING_NAMED) if len(names) > COUPLING_NAMED else "",
        )
        for (default, signals), names in (
            (key, [name for _, name in group])
            for key, group in itertools.groupby(keyed, key=lambda entry: entry[0])
        )
    ]


def sandbox_of(state):
    return (state or {}).get("sandbox") or isolation.SANDBOX_OFF


def summary(plan, state, run_dir, code, referee=None, troubles=()):
    lanes = _lane_summary(plan or {}, state, run_dir is not None)
    msps = _msp_summary(plan or {}, state)
    coupling = _coupling_summary(plan or {})
    return {
        "version": core.__version__,
        "plan_id": (plan or {}).get("plan_id"),
        **({} if run_dir is None else {"run_dir": run_dir}),
        "exit": code,
        "meaning": EXIT_MEANING.get(code, "not shipped"),
        "lanes": lanes,
        "msps": msps,
        "coupling": coupling,
        "referee": referee,
        "findings": findings(state, plan or {}),
        "sandbox": sandbox_of(state),
        "attention": _lock_attention(state, plan or {})
        + _attention(plan or {}, lanes, msps)
        + list(troubles)
        + _coupling_attention((plan or {}).get("coupling_review") or []),
    }


RESULT_SCHEMA = "h8-mitosis-result"
ITEMS_SCHEMA = "mitosis-items"


def _summarised(plan, state, run_dir, code, referee=None, troubles=()):
    built = summary(plan, state, run_dir, code, referee, troubles)
    try:
        handoff.require(RESULT_SCHEMA, built, "the result line", "mitosis")
    except handoff.HandoffError as error:
        _print(_handoff_lines(error), sys.stderr)
        return EXIT_HANDOFF
    _print((json.dumps(built, separators=(",", ":")),))
    return code


def _handoff_lines(error):
    return ("mitosis: %s" % error, "exit %d: %s" % (EXIT_HANDOFF, EXIT_MEANING[EXIT_HANDOFF]))


def referee_record(args, plan, repo, base, locked=None):
    held = plan.get("locked") or () if locked is None else locked
    if not held or base is None:
        return None
    return run.locked_record(repo, args.feature_branch, base, list(held))


def sliced_paths(plan):
    return frozenset(
        referee.normalised(path)
        for entry in plan.get("check_authors") or ()
        for path in entry.get("slice") or ()
    )


def lock_stage_referee(args, plan, repo, base):
    if not plan.get("check_authors") or base is None:
        return referee_record(args, plan, repo, base)
    owned = sliced_paths(plan)
    charter = [path for path in plan.get("locked") or () if referee.normalised(path) not in owned]
    missing = run.missing_paths(repo, base, charter)
    if missing:
        raise run.ConfigError(
            "--charter locks %s, which %s does not hold and no %s Step writes; "
            "commit the locked checks on it before the build"
            % (missing[0], args.feature_branch, core.CHECK_AUTHOR)
        )
    return referee_record(args, plan, repo, base, charter)


def planned_referee(args, plan, repo):
    try:
        base = (
            run.branch_commit(repo, args.feature_branch)
            if args.feature_branch and repo is not None
            else None
        )
        return lock_stage_referee(args, plan, repo, base), ()
    except (run.ConfigError, run.GitError) as error:
        return None, (str(error),)


def pinned_base(args, plan, run_dir, repo):
    try:
        prior = run.prior_state(run_dir, plan, args.resume)
        return run.pinned_base(repo, args.feature_branch, prior)
    except (run.ResumeError, run.GitError, handoff.HandoffError) as error:
        raise Refusal(str(error))


def locked_referee(args, plan, repo, base):
    try:
        return lock_stage_referee(args, plan, repo, base)
    except (run.ConfigError, run.GitError) as error:
        raise Refusal(str(error))


def report(plan, state, decomposed, root, run_dir, code, from_items, decisions=None):
    steps = plan
    sections = (
        coverage_lines(steps, decomposed, root),
        assumption_lines(steps),
        decision_lines(decisions, from_items, root),
        constraint_lines(decomposed, from_items),
        drift_lines(steps, root),
        shape_lines(steps.get("items") or [], steps.get("shared_files")),
        split_lines(plan, state),
        gate_lines(plan, state),
        reconcile_lines(plan, state),
        coupling_lines(plan, state),
        stacking_lines(plan, state),
        outcome_lines(plan, state, run_dir, code),
    )
    lines = ()
    for title, body in zip(REPORT_SECTIONS, sections):
        lines = lines + (title + ":",) + tuple("  " + line for line in body) + ("",)
    return lines


def loaded_gate_classes(args):
    return getattr(args, "loaded_gate_classes", (None, None))


def execute(args, plan, models, run_dir, repo, root, base=None):
    try:
        return run.execute(
            plan,
            repo,
            args.feature_branch,
            run_dir,
            args.dispatch_command,
            args.acceptance_command,
            args.pr_command,
            args.concurrency,
            models=models,
            resume=args.resume,
            root=root,
            trees_root=trees_root_of(args, root),
            remote=REMOTE,
            no_push=args.no_push,
            header=pull_request_header(args),
            render_command=args.render_command,
            landing={"type": args.pr_type, "scope": args.pr_scope},
            proven_heads=proven_heads(args.proven_head, plan),
            observer=recorder_of(args).observe,
            env=recorder_of(args).env,
            trajectory=args.trajectory,
            gate_setup=args.gate_setup,
            base_commit=base,
            worker_sandbox=args.worker_sandbox,
            worker_writes=tuple(args.worker_write),
            gate_classes=loaded_gate_classes(args)[0],
            gate_classes_sha256=loaded_gate_classes(args)[1],
            reask_command=args.reask_command,
            prompt_template=args.prompt_template,
            max_output=args.max_output_bytes,
        ), None
    except (run.ConfigError, run.ResumeError) as error:
        raise Refusal(str(error))
    except (run.GitError, OSError) as error:
        return run.load_state(run_dir) or {}, str(error)


def lane_output_paths(run_dir):
    folder = os.path.join(run_dir, "lanes")
    if not os.path.isdir(folder):
        return ()
    return tuple(os.path.join(folder, name) for name in sorted(os.listdir(folder)) if name.endswith(".out"))


def _write_lane_outputs(run_dir):
    for path in lane_output_paths(run_dir):
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        if text:
            sys.stdout.write(text if text.endswith("\n") else text + "\n")
    sys.stdout.flush()


def _print(lines, stream=None):
    out = stream or sys.stdout
    for line in lines:
        out.write(line + "\n")
    out.flush()


def run_pipeline(args):
    cwd = os.getcwd()
    repo = repository_root(cwd)
    root = cwd if repo is None else repo
    models = tier_models(args.tier_model)
    check_templates(args)
    check_pull_request_flags(args)
    charter = check_charter(args.charter)
    graph = load_graph(args.graph)
    if not args.no_run_dir:
        return recorded_pipeline(args, cwd, repo, root, models, charter, graph, input_run_dir(args, root))
    private = private_run_dir()
    _removed_when_terminated(private)
    try:
        return recorded_pipeline(args, cwd, repo, root, models, charter, graph, private)
    finally:
        removed_private_run_dir(private)


def _removed_when_terminated(private):
    def terminated(number, frame):
        removed_private_run_dir(private)
        signal.signal(number, signal.SIG_DFL)
        os.kill(os.getpid(), number)

    signal.signal(signal.SIGTERM, terminated)


def recorded_pipeline(args, cwd, repo, root, models, charter, graph, run_dir):
    check_worker_writes(args, run_dir, repo, trees_root_of(args, root))
    recorder = metrics.opened(args, repo, root, run_dir, models, achieved_parallelism)
    recorded = argparse.Namespace(
        **{**vars(args), "recorder": recorder, "working_directory": cwd}
    )
    try:
        code = run_stages(recorded, repo, root, models, charter, graph, run_dir)
    except UnknownProvenHead:
        recorder.ended(EXIT_USAGE, EXIT_MEANING[EXIT_USAGE])
        raise
    except Refusal:
        recorder.ended(EXIT_REFUSED, EXIT_MEANING[EXIT_REFUSED])
        raise
    except handoff.HandoffError:
        recorder.ended(EXIT_HANDOFF, EXIT_MEANING[EXIT_HANDOFF])
        raise
    except BaseException:
        recorder.ended(None, None)
        raise
    recorder.ended(code, EXIT_MEANING.get(code))
    return code


def run_stages(args, repo, root, models, charter, graph, run_dir):
    recorder = recorder_of(args)
    resolved = resolve_input(args, root, repo, models, charter, graph, run_dir)
    recorder.structured(resolved["items"])
    items, decomposed, run_dir, decisions = (
        resolved["items"],
        resolved["decomposed"],
        resolved["run_dir"],
        resolved["decisions"],
    )
    from_items = bool(args.items)
    notices = resolved["troubles"]
    shown = None if args.no_run_dir else run_dir
    plan = planned(args, items, root, charter, graph)
    if args.plan_file is None:
        refuse_unwritten(plan, base_holds(args, repo))
    recorder.planned(plan, lane_width(plan), len(empty_manifests(plan.get("items") or [])))
    proven_heads(args.proven_head, plan)
    args = argparse.Namespace(
        **{**vars(args), "loaded_gate_classes": gate_classes(args.gate_classes, plan)}
    )
    refuse_unrenderable(args, plan, run_dir, repo)
    if args.plan_only:
        run.write_json(os.path.join(run_dir, run.PLAN_FILE), plan)
        run.write_json(os.path.join(run_dir, ITEMS_FILE), items)
        _print(
            report(plan, None, decomposed, root, shown, EXIT_SHIPPED, from_items, decisions)
        )
        locked, troubles = planned_referee(args, plan, repo)
        return _summarised(plan, None, shown, EXIT_SHIPPED, locked, tuple(troubles) + notices)
    if args.dry_run:
        refuse_undispatchable(args)
        refuse_unmapped(plan, args.dispatch_command, models)
        _print(dry_run_lines(args, plan, models, run_dir))
        _print(
            report(plan, None, decomposed, root, shown, EXIT_SHIPPED, from_items, decisions)
        )
        locked, troubles = planned_referee(args, plan, repo)
        return _summarised(plan, None, shown, EXIT_SHIPPED, locked, tuple(troubles) + notices)
    refuse_run(args, plan, models, run_dir, repo)
    base = pinned_base(args, plan, run_dir, repo)
    locked = locked_referee(args, plan, repo, base)
    run.write_json(os.path.join(run_dir, ITEMS_FILE), items)
    if plan.get("msps"):
        made_trees_root(trees_root_of(args, root))
        state, failure = execute(args, plan, models, run_dir, repo, root, base)
    else:
        run.write_json(os.path.join(run_dir, run.PLAN_FILE), plan)
        state, failure = {}, None
    if args.run_log:
        run.write_run_log(args.run_log, plan, state)
    code = exit_code(state, plan)
    if failure is not None and code == EXIT_SHIPPED:
        code = EXIT_INCOMPLETE
    if args.no_run_dir:
        _write_lane_outputs(run_dir)
    _print(report(plan, state, decomposed, root, shown, code, from_items, decisions))
    if failure is not None:
        _print(("the run stopped: %s" % failure,), sys.stderr)
    return _summarised(plan, state, shown, code, locked, notices)


def refuse_unwritten(plan, held):
    lines = own_tests.unwritten(plan, held)
    if lines:
        raise Refusal("the plan names tests nothing writes:\n" + "\n".join("  " + line for line in lines))


def main(argv=None):
    parser = build_parser()
    parsed = parser.parse_args(argv)
    args = argparse.Namespace(**{**vars(parsed), "resume": False}) if parsed.no_run_dir else parsed
    if not args.items and not args.spec and not args.asks:
        parser.error("one of --items, --spec or --asks is required; mitosis has no input without them")
    paired = next((flag for flag, given in (("--items", args.items), ("--spec", args.spec)) if given), None)
    if args.asks and paired:
        parser.error("--asks and %s are two front ends to one pipeline; pass one, not both" % paired)
    if args.items and args.spec:
        parser.error("--items and --spec are two front ends to one pipeline; pass one, not both")
    if args.spec and not args.decompose_command and not args.resume:
        parser.error("--spec needs --decompose-command; a document becomes Steps only through decompose")
    if args.asks and not args.decompose_command and not args.resume:
        parser.error("--asks needs --decompose-command; an ask becomes Steps only through a planning call")
    if args.items and args.revise:
        parser.error("--revise revises the structure in the run directory, which --items never writes")
    if args.asks and args.revise:
        parser.error("--revise revises a spec's structure in the run directory, which --asks never revises")
    if args.resume and args.revise:
        parser.error("--revise runs the structure stage, which --resume skips; run --plan-only --revise first")
    if args.no_briefs and not args.plan_only:
        parser.error("--no-briefs omits the briefs a run dispatches from; use it with --plan-only")
    if args.coalesce_max < 1:
        parser.error("--coalesce-max must be at least 1")
    if args.structure_samples < 1:
        parser.error("--structure-samples must be at least 1")
    if args.pick is not None and not 1 <= args.pick <= args.structure_samples:
        parser.error(
            "--pick %d names a sample that will not exist; --structure-samples is %d"
            % (args.pick, args.structure_samples)
        )
    if args.timeout is not None:
        _print((TIMEOUT_NOTICE,), sys.stderr)
    if args.suite_command is not None:
        _print((SUITE_NOTICE,), sys.stderr)
    if args.brief_command is not None:
        _print((BRIEF_NOTICE,), sys.stderr)
    malformed = next(
        (value for value in args.proven_head if PROVEN_HEAD.fullmatch(value) is None), None
    )
    if malformed is not None:
        parser.error(
            "--proven-head %r is not LABEL=COMMIT with a commit of 7 to 40 lowercase "
            "hexadecimal digits" % malformed
        )
    try:
        return run_pipeline(args)
    except UnknownProvenHead as unknown:
        _print(("mitosis: %s" % unknown, "exit %d: %s" % (EXIT_USAGE, EXIT_MEANING[EXIT_USAGE])), sys.stderr)
        return EXIT_USAGE
    except Refusal as refusal:
        _print(("mitosis: %s" % refusal, "exit %d: %s" % (EXIT_REFUSED, EXIT_MEANING[EXIT_REFUSED])), sys.stderr)
        return EXIT_REFUSED
    except handoff.HandoffError as error:
        _print(_handoff_lines(error), sys.stderr)
        return EXIT_HANDOFF


if __name__ == "__main__":
    sys.exit(main())
