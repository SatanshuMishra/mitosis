import argparse
import hashlib
import itertools
import json
import os
import re
import sys
from datetime import datetime

import briefs
import core
import decompose
import isolation
import metrics
import referee
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

EXIT_MANIFEST = 23

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

GATE_EXIT = {"gate-inert": 20, "gate-inconclusive": 21, "gate-weak": 24}

EXIT_SEVERITY = (9, 7, 8, 24, 20, 21, 23, 22, 10, 12, 11, 13, 6, 4)

EXIT_MEANING = {
    EXIT_SHIPPED: "every MSP reached shipped, pushed, unchanged or committed, and reconcile found "
    "nothing",
    EXIT_USAGE: "the flags did not parse",
    EXIT_REFUSED: "refused to start",
    EXIT_INCOMPLETE: "the run stopped before every Lane and MSP reached a terminal state",
    EXIT_NO_BRANCHES: "the run created no branches",
    EXIT_RECONCILE: "reconcile found a write outside the declaration or a declared path never written",
    EXIT_CROSSING: "an MSP branch, pushed or with its pull request open, writes files another MSP "
    "owns and also changes",
    EXIT_FOREIGN: "a Lane changed a file another Lane of its MSP owns and had already committed",
    EXIT_REFEREE: "a Worker wrote a referee path; mitosis restored it and held its MSP back",
    EXIT_MANIFEST: "a branch is pushed or a pull request open with a package manifest that "
    "exports nothing",
    LANE_EXIT["failed"]: "a Lane failed",
    LANE_EXIT["blocked"]: "a Lane or MSP was blocked, by a predecessor or by a write across an "
    "MSP boundary",
    LANE_EXIT["merge-blocked"]: "a producer branch would not merge",
    LANE_EXIT[run.LANE_PARKED]: "a Worker stopped at a usage limit; the run is parked: pass "
    "--resume after the limit resets",
    GATE_EXIT["gate-inert"]: "an MSP was held back instead of pushed: an acceptance property "
    "proves nothing, because it passes with the implementation reverted",
    GATE_EXIT["gate-inconclusive"]: "an MSP was held back instead of pushed: the gate could not "
    "judge an acceptance property, or could not run",
    GATE_EXIT["gate-weak"]: "an acceptance property passes against a mutant of the work, so it "
    "cannot tell working code from broken code",
    MSP_EXIT["ship-failed"]: "a commit, push or pull-request command failed",
}

DECOMPOSE_TIER = "top"

RUNS_DIR = ("~", ".mitosis", "runs")

ITEMS_FILE = "items.json"

STRUCTURE_FILE = "structure.json"

BRIEFS_DIR = "briefs"

DECOMPOSE_FILE = "decompose.json"

DECOMPOSE_LOG = "decompose.log"

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

NO_PLAN = (
    "unavailable: the Steps are not briefed yet, so no plan exists; "
    "--resume writes the briefs and builds the plan"
)

BRIEF_PLACEHOLDERS = ("prompt", "model", "step", "document", "session_id")

TEMPLATE_PLACEHOLDERS = {"brief": BRIEF_PLACEHOLDERS}

PLACEHOLDER_HELP = {
    "dispatch": (
        "{task} {model} {tier} {worktree} {branch} {lane} {msp} {charter} {document} {run_dir} "
        "{session_id}"
    ),
    "decompose": "{prompt} {model} {document} {session_id}",
    "brief": " ".join("{%s}" % key for key in BRIEF_PLACEHOLDERS),
    "acceptance": "{file} {test} {worktree}",
    "gate-setup": "{worktree}",
    "suite": "{worktree} {report}",
    "pull-request": "{branch} {base} {title} {body} {worktree} {msp} {remote}",
}

COMPOSED_HELP = (
    "refused when it holds $, a backslash, <, @, ~, *, an _ outside a word, # or GH- before a "
    "digit, links or github.com/, two backticks in a row or an odd number of backticks, "
    "character references, emoji shortcodes or a run of 40 hexadecimal digits such as a full "
    "commit hash"
)

STAGE_FLAG_NAMES = (
    "--brief-command",
    "--decisions",
    "--structure-samples",
    "--pick",
    "--revise",
    "--proven-head",
    "--worker-sandbox",
    "--worker-write",
    "--suite-command",
    "--gate-classes",
)

GATE_CLASS_KEYS = ("class", "reason", "test")

GATE_CLASS_REASON_LIMIT = 500

PROVEN_HEAD = re.compile(r"^(.+)=([0-9a-f]{7,40})$")

RUN_FLAGS = (
    ("--feature-branch", "feature_branch"),
    ("--dispatch-command", "dispatch_command"),
    ("--acceptance-command", "acceptance_command"),
    ("--timeout", "timeout"),
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
        "help": "a document in any format; the structure stage turns it into unbriefed Steps, "
        "which needs --decompose-command, and the brief stage writes each Step's task, "
        "which needs --brief-command",
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
        "brief every Step still unbriefed before planning; Lanes already ok are skipped, "
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
        "help": "one Worker per Lane; placeholders %s; split into argv before substitution"
        % PLACEHOLDER_HELP["dispatch"],
    },
    "--decompose-command": {
        "metavar": "TEMPLATE",
        "help": "the structure Worker; placeholders %s; the prompt also arrives on stdin"
        % PLACEHOLDER_HELP["decompose"],
    },
    "--brief-command": {
        "metavar": "TEMPLATE",
        "help": "one brief Worker per unbriefed Step; placeholders %s; the prompt also "
        "arrives on stdin; required for the brief stage" % PLACEHOLDER_HELP["brief"],
    },
    "--decisions": {
        "metavar": "PATH",
        "help": "the settled-questions file, injected verbatim into every structure and "
        "brief prompt (default: <document>%s when it exists)" % decompose.DECISIONS_SUFFIX,
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
        "against it and every kept Step keeps its brief",
    },
    "--acceptance-command": {
        "metavar": "TEMPLATE",
        "help": "runs one acceptance property; placeholders %s; exit 0 passes, exit %d fails, "
        "anything else is inconclusive"
        % (PLACEHOLDER_HELP["acceptance"], run.ACCEPTANCE_FAILURE_EXIT),
    },
    "--gate-setup": {
        "metavar": "TEMPLATE",
        "help": "prepares the gate's clean checkout once per gate before any acceptance run, "
        "with the run's --timeout; placeholders %s; when given, the gate links no dependency "
        "folder from the repository root; only what it writes at new top-level names survives "
        "each run's reset; a non-zero exit or a timeout holds the MSP back "
        "gate-failed" % PLACEHOLDER_HELP["gate-setup"],
    },
    "--suite-command": {
        "metavar": "TEMPLATE",
        "help": "the project's full test suite, run in the gate's checkout once for each "
        "mutant that every acceptance test of a property survives, with the run's --timeout; "
        "placeholders %s, where {report} is a fresh path for an optional JUnit XML report; "
        "exit 1 kills the mutant and makes the property covered-elsewhere when every survivor "
        "is killed; any other exit keeps the property weak" % PLACEHOLDER_HELP["suite"],
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
        "help": "opens one pull request with the house title and body; placeholders %s; "
        "its last stdout line is recorded; without it, a run allowed to push pushes each branch, "
        "ends its MSP pushed and records the title and body for the caller"
        % PLACEHOLDER_HELP["pull-request"],
    },
    "--pr-type": {
        "metavar": "TYPE",
        "help": "the type that opens every pull-request title, one of %s; required for a run "
        "allowed to push" % ", ".join(run.PR_TYPES),
    },
    "--pr-scope": {
        "metavar": "SCOPE",
        "help": "the optional scope in parentheses after the type: a lowercase letter or digit, "
        "then up to 15 lowercase letters, digits or hyphens",
    },
    "--pr-subject": {
        "metavar": "TEXT",
        "help": "the title's summary: a lowercase first letter, printable ASCII, no trailing full "
        "stop; %s; a plan of several MSPs appends each MSP label in parentheses; required for a "
        "run allowed to push" % COMPOSED_HELP,
    },
    "--pr-why": {
        "metavar": "SENTENCE",
        "action": "append",
        "default": [],
        "help": "one line of every body's Why section in printable ASCII, beginning with an "
        "uppercase letter and ending with . ? or !; %s; give it 1 to %d times; required for a run "
        "allowed to push" % (COMPOSED_HELP, run.SECTION_LIMIT),
    },
    "--pr-door": {
        "metavar": "DOOR",
        "help": "the merge-danger call that opens every body's Risk section, one of %s: a "
        "two-way door is cheap to roll back, a one-way door is destructive or hard to reverse; "
        "given together with --pr-blast-radius or not at all" % ", ".join(run.PR_DOORS),
    },
    "--pr-blast-radius": {
        "metavar": "SENTENCE",
        "help": "what a bad merge would reach, the Risk line after the door call, in printable "
        "ASCII, beginning with an uppercase letter and ending with . ? or !; %s; given together "
        "with --pr-door or not at all" % COMPOSED_HELP,
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
        "help": "per Worker, per acceptance run and per pull-request command; required for a run",
    },
    "--concurrency": {
        "metavar": "N",
        "type": int,
        "default": DEFAULT_CONCURRENCY,
        "help": "Workers running at once, for the brief stage and the build "
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
        "help": "most graph neighbours kept per Step and per Lane; the overflow is counted "
        "(default: %d)" % core.CONTEXT_CAP,
    },
    "--session-tokens": {
        "metavar": "N",
        "type": int,
        "default": core.SESSION_TOKENS,
        "help": "the estimated tokens one Worker session may hold; a Lane estimated above it is "
        "built as several Lanes in its Step order, one fresh session each, in a row on its MSP's "
        "branch (default: %d)" % core.SESSION_TOKENS,
    },
    "--graph": {
        "metavar": "PATH",
        "help": "a JSON object mapping each path to the paths it imports, or a node-link "
        "graph such as graphify's graph.json, read as that mapping from its links between "
        "code in different files",
    },
    "--risk-markers": {
        "metavar": "MARKER",
        "nargs": "+",
        "action": "extend",
        "default": [],
        "help": "path globs or substrings that force the top tier and count as a coupling signal",
    },
    "--serial-markers": {
        "metavar": "MARKER",
        "nargs": "+",
        "action": "extend",
        "default": [],
        "help": "path globs or substrings whose acceptance the probe cannot run headlessly; "
        "such properties are counted not-applicable",
    },
    "--trajectory": {
        "metavar": "PATH",
        "help": "a JSONL file of prior outcomes; a recorded regression on a surface raises "
        "its tier and never lowers one",
    },
    "--worker-sandbox": {
        "action": "store_true",
        "help": "run every Worker under sandbox-exec with a profile that refuses file writes "
        "outside its clone, a private temp folder passed as TMPDIR, its Claude Code transcript "
        "folder, /dev/null, /dev/tty, /dev/fd and each --worker-write path; a host without "
        "sandbox-exec refuses the run before any Worker starts",
    },
    "--worker-write": {
        "metavar": "PATH",
        "action": "append",
        "default": [],
        "help": "one more path a sandboxed Worker may write under; repeatable",
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
        "branch, gate the work, and push each branch with a pull-request title and body in "
        "the house format, opening one pull request per MSP when --pr-command is given. "
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


def load_graph(path, root):
    if path is None:
        return None
    graph = read_json(path, "--graph")
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    if isinstance(nodes, list) and any(isinstance(node, dict) for node in nodes):
        if core.node_link_edges(graph) is None:
            raise Refusal(
                "--graph %s has a list of nodes but no links or edges list, so it cannot say "
                "which files are linked" % path
            )
    if core.node_link_edges(graph) is not None:
        if not any(core.node_files(graph, root).values()):
            raise Refusal(
                "--graph %s is a node-link graph, but none of its code nodes names a "
                "source_file under %s, so it cannot say which files are linked" % (path, root)
            )
        return core.adjacency_from_node_links(graph, root)
    if not core.is_adjacency(graph):
        raise Refusal(
            "--graph %s is neither a JSON object mapping each path to a list of the paths it "
            "imports nor a node-link graph with nodes and links" % path
        )
    return core.clean_adjacency(graph)


def unoffered_placeholders(name, template):
    offered = TEMPLATE_PLACEHOLDERS.get(name)
    if offered is None:
        return ()
    used = {match.group(1) for match in run.PLACEHOLDER.finditer(template)}
    return tuple(sorted(used - set(offered)))


def check_template(name, template):
    try:
        run.check_template(name, template)
    except run.ConfigError as error:
        raise Refusal(str(error))
    unknown = unoffered_placeholders(name, template)
    if unknown:
        raise Refusal(
            "the %s command uses %s, which it is never given; it offers %s"
            % (
                name,
                ", ".join("{%s}" % key for key in unknown),
                ", ".join("{%s}" % key for key in TEMPLATE_PLACEHOLDERS[name]),
            )
        )


def check_templates(args):
    for name, template in (
        ("dispatch", args.dispatch_command),
        ("decompose", args.decompose_command),
        ("brief", args.brief_command),
        ("acceptance", args.acceptance_command),
        ("gate-setup", args.gate_setup),
        ("suite", args.suite_command),
        ("pull-request", args.pr_command),
    ):
        if template is None:
            continue
        check_template(name, template)


def check_document(args, root):
    path = document_path(args.spec, root)
    if not os.path.isfile(os.path.join(root, path)):
        raise Refusal("--spec %s is not a file" % args.spec)
    return path


def load_decisions(args, root, document):
    try:
        return decompose.load_decisions(root, args.decisions, document)
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
    checked = core.validate(items, root=root, required=core.STRUCTURE_ITEM_FIELDS)
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


def structure_document(args, root, run_dir, models, charter, graph, decisions, prior):
    model = models.get(DECOMPOSE_TIER)
    if "{model}" in args.decompose_command and model is None:
        raise Refusal(
            "the decompose command uses {model} but no model is mapped for tier %s; "
            "map it with --tier-model %s=<model>" % (DECOMPOSE_TIER, DECOMPOSE_TIER)
        )
    if args.timeout is None:
        raise Refusal("--timeout is required to run the structure stage; mitosis has no default")
    path = check_document(args, root)
    recorder = recorder_of(args)
    began = run.now()
    results = decompose.sample_structures(
        args.structure_samples,
        path,
        args.decompose_command,
        root,
        args.timeout,
        model=model,
        graph=graph,
        charter=charter,
        log=os.path.join(run_dir, DECOMPOSE_LOG),
        decisions=decisions,
        prior=prior,
        env=recorder.env,
        on_sessions=lambda sessions: recorder.stage_ended("structure", began, sessions),
    )
    order = decompose.rank(results)
    chosen = chosen_sample(order, args.pick)
    if len(results) > 1:
        _print(sample_lines(results, order, chosen))
    result = results[chosen]
    _print(decompose.report(result))
    if result["errors"]:
        _print(("Coverage map:",) + tuple("  " + line for line in coverage_lines({}, result, root)))
        raise Refusal(
            "the structure stage returned %s; the log is %s"
            % (_n(len(result["errors"]), "contract error"), result["log"])
        )
    record = {key: result[key] for key in result if key != "items"}
    run.write_json(os.path.join(run_dir, DECOMPOSE_FILE), record)
    run.write_json(os.path.join(run_dir, STRUCTURE_FILE), result["items"])
    return result["items"], record


def refuse_unmapped_briefs(template, pending, models):
    if "{model}" not in template:
        return
    missing = sorted({briefs.tier_for(step) for step in pending} - set(models or {}))
    if missing:
        raise Refusal(
            "the brief command uses {model} but no model is mapped for tier %s; "
            "map every tier with --tier-model or the brief would run on the agent's default"
            % ", ".join(missing)
        )


def brief_lines(written):
    retried = written.get("retried") or []
    lines = (
        "brief stage: %s written, %s reused"
        % (_n(len(written["written"]), "brief"), _n(len(written["reused"]), "brief")),
    )
    if retried:
        lines = lines + (
            "%s re-dispatched after a return that broke the contract: %s"
            % (_n(len(retried), "brief"), ", ".join(retried)),
        )
    return lines + tuple(written["errors"])


def refuse_unbriefable(args, pending):
    if args.brief_command is None:
        raise Refusal(
            "the brief stage needs --brief-command for %s; pass --plan-only to stop at "
            "the structure" % _n(len(pending), "unbriefed Step")
        )


def brief_structure(args, items, root, run_dir, models, charter, graph, decisions, document):
    pending = briefs.pending(items)
    if not pending:
        _print(brief_lines({"written": [], "reused": [step["name"] for step in items], "errors": []}))
        return items
    refuse_unbriefable(args, pending)
    if args.timeout is None:
        raise Refusal("--timeout is required to run the brief stage; mitosis has no default")
    refuse_unmapped_briefs(args.brief_command, pending, models)
    log_dir = os.path.join(run_dir, BRIEFS_DIR)
    os.makedirs(log_dir, exist_ok=True)
    recorder = recorder_of(args)
    began = run.now()
    try:
        written = briefs.write(
            items,
            args.brief_command,
            root,
            args.timeout,
            models=models,
            concurrency=args.concurrency,
            log_dir=log_dir,
            document=document,
            packs=core.context_packs(items, graph, args.context_hops, args.context_cap),
            charter=charter,
            decisions=decisions,
            env=recorder.env,
            on_sessions=lambda sessions: recorder.stage_ended("brief", began, sessions),
        )
    except (ValueError, OSError) as error:
        raise Refusal("the brief stage could not run: %s" % error)
    _print(brief_lines(written))
    run.write_json(os.path.join(run_dir, STRUCTURE_FILE), written["items"])
    unbriefed = briefs.pending(written["items"])
    if unbriefed:
        raise Refusal(
            "%s still unbriefed after the brief stage: %s; the logs are in %s"
            % (_n(len(unbriefed), "Step"), ", ".join(step["name"] for step in unbriefed), log_dir)
        )
    return written["items"]


def staged(items, decomposed, run_dir, decisions, briefed):
    return {
        "items": items,
        "decomposed": decomposed,
        "run_dir": run_dir,
        "decisions": decisions,
        "briefed": briefed,
    }


def input_run_dir(args, root):
    if args.run_dir:
        return os.path.abspath(args.run_dir)
    kind, path = ("items", args.items) if args.items else ("spec", args.spec)
    return os.path.abspath(default_run_dir(root, kind, file_digest(os.path.abspath(path))))


def resolve_input(args, root, repo, models, charter, graph, run_dir):
    if args.items:
        items = read_json(os.path.abspath(args.items), "--items")
        return staged(items, load_decomposed(run_dir), run_dir, None, True)
    document = check_document(args, root)
    decisions = load_decisions(args, root, document)
    if not args.plan_only:
        refuse_unbuildable(args, repo)
    if args.resume:
        items = load_structure(run_dir, root, "nothing to resume")
        decomposed = load_decomposed(run_dir)
    else:
        if os.path.isfile(os.path.join(run_dir, run.STATE_FILE)):
            raise Refusal(
                "%s already holds a run; pass --resume to continue it or --run-dir to start elsewhere"
                % run_dir
            )
        prior = load_structure(run_dir, root, "nothing to revise") if args.revise else None
        if not args.plan_only:
            refuse_unbriefable(args, briefs.pending(prior or [{}]))
        items, decomposed = structure_document(
            args, root, run_dir, models, charter, graph, decisions, prior
        )
        if args.plan_only:
            return staged(items, decomposed, run_dir, decisions, False)
    items = brief_structure(args, items, root, run_dir, models, charter, graph, decisions, document)
    run.write_json(os.path.join(run_dir, ITEMS_FILE), items)
    return staged(items, decomposed, run_dir, decisions, True)


def recorded_sizes(run_dir):
    try:
        recorded = run.read_json(os.path.join(run_dir, run.PLAN_FILE))
    except (OSError, ValueError):
        return None
    sizes = recorded.get("sizes") if isinstance(recorded, dict) else None
    return sizes if isinstance(sizes, dict) else None


def build_plan(args, items, root, charter, graph, run_dir=None):
    try:
        return core.plan(
            items,
            charter=charter,
            risk_markers=tuple(args.risk_markers or ()),
            serial_markers=tuple(args.serial_markers or ()),
            graph=graph,
            hops=args.context_hops,
            cap=args.context_cap,
            history=core.trajectory_store(args.trajectory),
            root=root,
            session_tokens=args.session_tokens,
            sizes=recorded_sizes(run_dir) if args.resume and run_dir else None,
        )
    except core.ValidationError as error:
        raise Refusal(
            "the Steps did not validate:\n" + "\n".join("  " + line for line in error.errors)
        )


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
    unwritten = missing_push_flags(args)
    if unwritten:
        raise Refusal(
            "a run allowed to push needs %s to title and describe each pull request; pass "
            "--no-push to hold the branches instead" % ", ".join(unwritten)
        )
    if not run.branch_exists(repo, args.feature_branch):
        raise Refusal("--feature-branch %s is not a branch of %s" % (args.feature_branch, repo))


def subject_fault(subject):
    if not "a" <= subject[:1] <= "z":
        return "does not begin with a lowercase ASCII letter"
    if not all(" " <= character <= "~" for character in subject):
        return "holds a character outside printable ASCII"
    if subject.endswith((".", " ")):
        return "ends with a full stop or a space"
    return run.composed_fault(subject)


def why_fault(why):
    fault = run.composed_fault(why)
    if fault is not None:
        return fault
    if not run.receipt_token(why) and not run.sentence_shaped(why):
        return "does not begin with an uppercase letter and end with . ? or !"
    return None


def blast_radius_fault(blast_radius):
    fault = run.composed_fault(blast_radius)
    if fault is not None:
        return fault
    if not run.sentence_shaped(blast_radius):
        return "does not begin with an uppercase letter and end with . ? or !"
    return None


def check_pull_request_flags(args):
    if args.pr_type is not None and args.pr_type not in run.PR_TYPES:
        raise Refusal(
            "--pr-type %r is not one of %s" % (args.pr_type, ", ".join(run.PR_TYPES))
        )
    if args.pr_scope is not None and run.SCOPE_PATTERN.fullmatch(args.pr_scope) is None:
        raise Refusal(
            "--pr-scope %r does not match ^[a-z0-9][a-z0-9-]{0,15}$" % args.pr_scope
        )
    if args.pr_scope is not None:
        fault = run.composed_fault(args.pr_scope)
        if fault is not None:
            raise Refusal("--pr-scope %r %s" % (args.pr_scope, fault))
    if args.pr_subject is not None:
        fault = subject_fault(args.pr_subject)
        if fault is not None:
            raise Refusal("--pr-subject %r %s" % (args.pr_subject, fault))
    whys = args.pr_why or ()
    if len(whys) > run.SECTION_LIMIT:
        raise Refusal(
            "--pr-why is given %d times; a pull request's Why section holds at most %d lines"
            % (len(whys), run.SECTION_LIMIT)
        )
    for index, why in enumerate(whys, 1):
        fault = why_fault(why)
        if fault is not None:
            raise Refusal("--pr-why number %d %s" % (index, fault))
    if (args.pr_door is None) != (args.pr_blast_radius is None):
        raise Refusal(
            "--pr-door and --pr-blast-radius go together or not at all; a door call without its "
            "blast radius, or a blast radius without its door call, tells a reviewer half the "
            "merge danger"
        )
    if args.pr_door is not None and args.pr_door not in run.PR_DOORS:
        raise Refusal("--pr-door %r is not one of %s" % (args.pr_door, ", ".join(run.PR_DOORS)))
    if args.pr_blast_radius is not None:
        fault = blast_radius_fault(args.pr_blast_radius)
        if fault is not None:
            raise Refusal("--pr-blast-radius %r %s" % (args.pr_blast_radius, fault))


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
    }


def refuse_unfit_titles(header, plan):
    if header is None:
        return
    for index in range(len(plan.get("msps") or [])):
        title = run.pull_request_title(header, plan, index)
        fault = run.title_fault(title)
        if fault is not None:
            raise Refusal(
                "MSP %s would be titled %r, which %s; shorten --pr-subject or relabel the MSP"
                % (_label(plan, index), title, fault)
            )


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
            "unavailable: decisions reach the structure and brief prompts, and this run took an items file",
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


def _depths(count, producers_of):
    depth = {}
    for index in range(count):
        depth = {**depth, index: _depth(index, producers_of, depth, ())}
    return depth


def _depth(index, producers_of, known, trail):
    if index in known:
        return known[index]
    if index in trail:
        return 0
    producers = [p for p in producers_of.get(index, ()) if p != index]
    if not producers:
        return 0
    return 1 + max(_depth(p, producers_of, known, trail + (index,)) for p in producers)


def lane_width(plan):
    return shape.scalars(plan.get("items") or [])["parallelism"]


def shape_lines(items):
    return tuple(
        "%s: %s" % (finding["kind"], finding["detail"]) for finding in shape.findings(items)
    ) + (scalar_text(shape.scalars(items)),)


def empty_manifests(items):
    return [gap for gap in shape.manifest_gaps(items) if gap["reached"] == 0]


def planned_code(items):
    return EXIT_MANIFEST if empty_manifests(items) else EXIT_SHIPPED


def manifest_lines(items):
    gaps = empty_manifests(items)
    if not gaps:
        return ()
    return tuple(
        "%s would ship with nothing exported: %s owns it and is built before all %d Steps whose "
        "modules it should export"
        % (gap["manifest"], items[gap["owner"]].get("name"), gap["siblings"])
        for gap in gaps
    )


def cluster_depths(plan):
    producers = run.msp_producers(plan)
    depths = _depths(len(plan.get("msps") or []), producers)
    return tuple(
        max((depths[msp] for msp in cluster), default=0) + 1 for cluster in plan.get("clusters") or []
    )


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


def session_runs(plan):
    runs = ()
    for index, lane in enumerate(plan.get("lanes") or []):
        session = lane.get("session") or ()
        if len(session) == 2 and session[0] == 1:
            runs = runs + ((index, session[1]),)
    return runs


def oversized_sessions(plan):
    limit = plan.get("session_tokens")
    if limit is None:
        return ()
    return tuple(
        (index, lane["steps"][0], lane["tokens"])
        for index, lane in enumerate(plan.get("lanes") or [])
        if len(lane.get("steps") or ()) == 1 and (lane.get("tokens") or 0) > limit
    )


def oversized_lines(plan):
    return tuple(
        "Lane %d holds Step %s alone at about %d estimated tokens, above the %d one session "
        "should hold, and a session holds at least one Step"
        % (index, name, tokens, plan["session_tokens"])
        for index, name, tokens in oversized_sessions(plan)
    )


def session_lines(plan):
    limit = plan.get("session_tokens")
    if limit is None:
        return ()
    runs = session_runs(plan)
    if not runs:
        if oversized_sessions(plan):
            return oversized_lines(plan)
        return ("every Lane fits one Worker session of %d estimated tokens" % limit,)
    return tuple(
        "Lanes %d to %d are one Lane of MSP %s estimated above %d tokens, built as %d fresh "
        "Worker sessions in a row on its branch, each continuing from the work committed before it"
        % (start, start + count - 1, _label(plan, plan["lanes"][start]["msp"]), limit, count)
        for start, count in runs
    ) + oversized_lines(plan)


def lock_stage_lines(plan):
    author = plan.get("check_author")
    if not author:
        return ()
    return (
        "lock stage: the %s Step %s writes and locks %s before every Lane, and every Lane "
        "builds with them read-only"
        % (core.CHECK_AUTHOR, author["name"], ", ".join(plan.get("locked") or ())),
    )


def split_lines(plan, state):
    if plan is None:
        return (NO_PLAN,)
    lanes = plan.get("lanes") or []
    msps = plan.get("msps") or []
    clusters = plan.get("clusters") or []
    counts = plan.get("counts") or {}
    total = len(plan.get("items") or [])
    lines = (
        "%s in %s across %s in %s"
        % (_n(total, "Step"), _n(len(lanes), "Lane"), _n(len(msps), "MSP"), _n(len(clusters), "Cluster")),
        "%d of %s declare no acceptance property; %s do not exist yet"
        % (counts.get("no_acceptance", 0), _n(total, "Step"), _n(counts.get("missing_paths", 0), "write-set path")),
    )
    lines = lines + session_lines(plan) + lock_stage_lines(plan)
    lines = lines + tuple(
        "Lane %d (MSP %s, %s): %s, cost %s, about %s tokens%s: %s"
        % (
            index,
            _label(plan, lane["msp"]),
            lane.get("tier"),
            _n(len(lane.get("steps") or []), "Step"),
            lane.get("cost"),
            lane.get("tokens"),
            ", session %d of %d" % tuple(lane["session"]) if lane.get("session") else "",
            ", ".join(lane.get("steps") or []),
        )
        for index, lane in enumerate(lanes)
    )
    lines = lines + tuple(
        "Cluster %d: depth %d, MSPs %s"
        % (index, depth, ", ".join(_label(plan, m) for m in cluster))
        for index, (cluster, depth) in enumerate(zip(clusters, cluster_depths(plan)))
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


def _mutant_line(record, mutation=None, killers=()):
    if not isinstance(record, dict):
        return None
    killed = record.get("killed")
    tried = _n(record.get("tried") or 0, "mutant") + " tried"
    if mutation == "not-applicable" and not record.get("tried"):
        return "no mutant tried: the test loaded none of the Step's Python files"
    if mutation == "not-applicable":
        return tried + ", none killed it; the Step added no code, so mutation does not apply"
    if mutation == "covered-elsewhere":
        return tried + ", none killed it; the suite killed each, failing %s" % (
            ", ".join(killers) or "tests it did not name"
        )
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
            tried = _mutant_line(
                entry.get("mutants"), entry.get("mutation"), tuple(entry.get("killers") or ())
            )
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
    return ("%s from the git log" % _n(findings, "finding"),) + lines


def coupling_lines(plan, state):
    if plan is None:
        return (NO_PLAN,)
    ordered = plan.get("coupling_order") or []
    pairs = plan.get("coupling_review") or []
    if not ordered and not pairs:
        return ("none: no write-set-disjoint pair in different Lanes shares a coupling signal",)
    verb = "ran" if state is not None else "run"
    return tuple(
        "%s then %s: put in order because of %s"
        % (entry["first"], entry["then"], ", ".join(entry["signals"]))
        for entry in ordered
    ) + tuple(
        "%s and %s (Lanes %s) %s in parallel with only %s between them; add an after edge if "
        "one needs the other's output"
        % (
            pair["steps"][0],
            pair["steps"][1],
            ", ".join(str(lane) for lane in pair.get("lanes") or []),
            verb,
            " and ".join(pair.get("signals") or []),
        )
        for pair in pairs
    )


def stacking_lines(plan, state):
    if plan is None:
        return (NO_PLAN,)
    if state is None:
        producers = run.msp_producers(plan)
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
    return bool(
        findings.get("undeclared")
        or findings.get("unwritten")
        or findings.get("crossing")
        or findings.get("fatal")
    )


def gate_exit(record):
    if record.get("proven_head"):
        return None
    if record.get("gate_error"):
        return GATE_EXIT["gate-inconclusive"]
    flag = run.gate_state(record.get("gate") or {})
    return GATE_EXIT.get(flag)


def _finding(msp, lane, kind, code, detail):
    return {"msp": msp, "lane": lane, "kind": kind, "exit": code, "detail": _one_line(detail)}


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
    if record.get("gate_error"):
        return record["gate_error"]
    gate = record.get("gate") or {}
    named = [
        "%s::%s: %s" % (entry.get("file"), entry.get("test"), _one_line(entry.get("reason")))
        for entry in gate.get("properties") or []
        if entry.get("outcome") in run.GATE_STATES
    ]
    return "; ".join(named) or record.get("reason") or ""


def _gate_findings(label, record):
    flag = gate_exit(record)
    if flag is None and record.get("state") == run.MSP_GATE_FAILED:
        flag = GATE_EXIT["gate-inconclusive"]
    if flag is None:
        return []
    kind = next(name for name, code in GATE_EXIT.items() if code == flag)
    return [_finding(label, None, kind, flag, _gate_detail(record))]


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
        + [_finding(label, None, "unwritten", EXIT_RECONCILE, path) for path in found.get("unwritten") or []]
    )


def lock_of(state):
    lock = (state or {}).get("lock")
    return lock if isinstance(lock, dict) else None


def lock_stopped(state):
    lock = lock_of(state)
    return lock is not None and lock.get("state") != "ok"


def _lock_findings(state):
    lock = lock_of(state)
    if lock is None:
        return []
    stated = (
        []
        if lock.get("state") == "ok"
        else [
            _finding(
                core.LOCK_MSP,
                None,
                lock.get("state") or "incomplete",
                LANE_EXIT.get(lock.get("state"), EXIT_INCOMPLETE),
                lock.get("reason") or "",
            )
        ]
    )
    return stated + [
        _finding(core.LOCK_MSP, None, "undeclared", EXIT_RECONCILE, path)
        for path in lock.get("undeclared") or []
    ]


def findings(state, plan):
    manifests = [
        _finding(None, None, "manifest", EXIT_MANIFEST, line)
        for line in manifest_lines(plan.get("items") or [])
    ]
    if state is None:
        return manifests
    lane_records = state.get("lanes") or {}
    msp_records = state.get("msps") or {}
    msps = [
        entry
        for index in range(len(plan.get("msps") or []))
        for entry in _msp_findings(plan, index, msp_records.get(str(index)), lane_records)
    ]
    return _lock_findings(state) + _lane_findings(plan, lane_records) + msps + manifests


def exit_code(state, plan):
    if not plan.get("msps") or not ((state or {}).get("worktrees") or lock_stopped(state)):
        return EXIT_NO_BRANCHES
    carried = {entry["exit"] for entry in findings(state, plan)}
    worst = next((code for code in EXIT_SEVERITY if code in carried), None)
    if worst is not None:
        return worst
    return EXIT_SHIPPED if run.succeeded(state) else EXIT_INCOMPLETE


def outcome_lines(plan, state, run_dir, code):
    if plan is None:
        return (
            "run directory: %s" % run_dir,
            "exit %d: %s"
            % (
                code,
                EXIT_MEANING[code]
                if code != EXIT_SHIPPED
                else "the structure was written and no brief was bought",
            ),
        )
    lines = ("run directory: %s" % run_dir, "plan id: %s" % plan.get("plan_id"))
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
    lock = lock_of(state)
    if lock is not None:
        reason = _one_line(lock.get("reason"))
        lines = lines + (
            "lock stage: %s%s" % (lock.get("state"), " (%s)" % reason if reason else ""),
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


def outline(items):
    first = items[0] if items and isinstance(items[0], dict) else {}
    return {"items": items, "source": first.get("source")}


def _lane_summary(plan, state):
    records = (state or {}).get("lanes") or {}
    return [
        {
            "lane": index,
            "msp": _label(plan, lane["msp"]),
            "steps": list(lane.get("steps") or ()),
            "session": list(lane["session"]) if lane.get("session") else None,
            "state": (records.get(str(index)) or {}).get("state"),
            "reason": _one_line((records.get(str(index)) or {}).get("reason")),
            "notes": ((records.get(str(index)) or {}).get("returned") or {}).get("notes"),
            "foreign_writes": list((records.get(str(index)) or {}).get("foreign_writes") or ()),
            "stdout": (records.get(str(index)) or {}).get("stdout"),
            "stderr": (records.get(str(index)) or {}).get("stderr"),
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
                "reason": _one_line(record.get("reason")),
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
                                for key in ("mutation", "coverage", "killers")
                                if key in entry
                            },
                        }
                        for entry in gate.get("properties") or []
                    ],
                    "unproven": [
                        "%s::%s %s" % (entry.get("file"), entry.get("test"), entry.get("outcome"))
                        for entry in gate.get("properties") or []
                        if entry.get("outcome") in run.BLOCKING_OUTCOMES
                    ],
                },
                "reconcile": {
                    **{
                        key: list(findings.get(key) or ())
                        for key in ("undeclared", "unwritten", "untouched")
                    },
                    **_referee_verdict(record.get("reconcile")),
                },
                "crossing": [entry.get("path") for entry in findings.get("crossing") or ()],
                "ignored": list(record.get("ignored") or ()),
                "trajectory_error": record.get("trajectory_error"),
            }
        )
    return summaries


def _lock_summary(state):
    lock = lock_of(state)
    if lock is None:
        return None
    return {key: lock.get(key) for key in ("state", "reason", "commit", "session_id")}


def _lock_attention(state):
    lock = lock_of(state) or {}
    stopped = (
        ["the lock stage ended %s: %s" % (lock.get("state"), _one_line(lock.get("reason")))]
        if lock and lock.get("state") != "ok"
        else []
    )
    undeclared = (
        [
            "the check-author wrote outside the locked paths, and mitosis landed none of it: %s"
            % ", ".join(lock["undeclared"])
        ]
        if lock.get("undeclared")
        else []
    )
    checked_out = [
        "%s has the feature branch checked out; mitosis moved the branch to the lock commit "
        "%s and left that checkout's files untouched, so update it before working there"
        % (path, lock.get("commit"))
        for path in lock.get("checked_out") or []
    ]
    return stopped + undeclared + checked_out


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
        "ordered": [dict(entry) for entry in plan.get("coupling_order") or []],
        "parallel": [
            {"steps": list(pair["steps"]), "signals": list(pair["signals"])}
            for pair in plan.get("coupling_review") or []
            if pair.get("default") == "parallel"
        ],
    }


def _coupling_attention(coupling):
    keyed = sorted((tuple(pair["signals"]), "+".join(pair["steps"])) for pair in coupling["parallel"])
    return [
        "%s run in parallel with only %s between them: %s%s; add an after edge where one needs "
        "the other's output"
        % (
            _n(len(names), "pair") + " of Steps",
            " and ".join(signals),
            ", ".join(names[:COUPLING_NAMED]),
            " and %d more" % (len(names) - COUPLING_NAMED) if len(names) > COUPLING_NAMED else "",
        )
        for signals, names in (
            (signals, [name for _, name in group])
            for signals, group in itertools.groupby(keyed, key=lambda entry: entry[0])
        )
    ]


def sandbox_of(state):
    return (state or {}).get("sandbox") or isolation.SANDBOX_OFF


def summary(plan, state, run_dir, code, referee=None, troubles=()):
    lanes = _lane_summary(plan or {}, state)
    msps = _msp_summary(plan or {}, state)
    coupling = _coupling_summary(plan or {})
    return {
        "version": core.__version__,
        "plan_id": (plan or {}).get("plan_id"),
        "run_dir": run_dir,
        "exit": code,
        "meaning": EXIT_MEANING.get(code, "not shipped"),
        "lanes": lanes,
        "msps": msps,
        "coupling": coupling,
        "referee": referee,
        "lock": _lock_summary(state),
        "findings": findings(state, plan or {}),
        "sandbox": sandbox_of(state),
        "attention": _lock_attention(state)
        + _attention(plan or {}, lanes, msps)
        + list(troubles)
        + list(oversized_lines(plan or {}))
        + list(manifest_lines((plan or {}).get("items") or []))
        + _coupling_attention(coupling),
    }


def _summarised(plan, state, run_dir, code, referee=None, troubles=()):
    _print(
        (
            json.dumps(
                summary(plan, state, run_dir, code, referee, troubles), separators=(",", ":")
            ),
        )
    )
    return code


def referee_record(args, plan, repo, base):
    locked = plan.get("locked") or ()
    if not locked or base is None:
        return None
    return run.locked_record(repo, args.feature_branch, base, locked)


def lock_stage_referee(args, plan, repo, base):
    author = plan.get("check_author")
    if not author or base is None:
        return referee_record(args, plan, repo, base)
    missing = run.missing_paths(repo, base, plan.get("locked") or ())
    owned = frozenset(referee.normalised(path) for path in author.get("files") or ())
    unowned = [path for path in missing if referee.normalised(path) not in owned]
    if unowned:
        raise run.ConfigError(
            "--charter locks %s, which %s does not hold and the %s Step %s does not write; "
            "commit the locked checks on it before the build"
            % (unowned[0], args.feature_branch, core.CHECK_AUTHOR, author["name"])
        )
    return None if missing else referee_record(args, plan, repo, base)


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
    except (run.ResumeError, run.GitError) as error:
        raise Refusal(str(error))


def locked_referee(args, plan, repo, base):
    try:
        return lock_stage_referee(args, plan, repo, base)
    except (run.ConfigError, run.GitError) as error:
        raise Refusal(str(error))


def report(plan, state, decomposed, root, run_dir, code, from_items, decisions=None, items=None):
    steps = plan if plan is not None else outline(items or [])
    sections = (
        coverage_lines(steps, decomposed, root),
        assumption_lines(steps),
        decision_lines(decisions, from_items, root),
        constraint_lines(decomposed, from_items),
        drift_lines(steps, root),
        shape_lines(steps.get("items") or []),
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
            args.timeout,
            args.concurrency,
            models=models,
            resume=args.resume,
            root=root,
            remote=REMOTE,
            no_push=args.no_push,
            header=pull_request_header(args),
            proven_heads=proven_heads(args.proven_head, plan),
            observer=recorder_of(args).observe,
            env=recorder_of(args).env,
            trajectory=args.trajectory,
            gate_setup=args.gate_setup,
            suite_command=args.suite_command,
            base_commit=base,
            worker_sandbox=args.worker_sandbox,
            worker_writes=tuple(args.worker_write),
            gate_classes=loaded_gate_classes(args)[0],
            gate_classes_sha256=loaded_gate_classes(args)[1],
        ), None
    except (run.ConfigError, run.ResumeError) as error:
        raise Refusal(str(error))
    except (run.GitError, OSError) as error:
        return run.load_state(run_dir) or {}, str(error)


def _print(lines, stream=None):
    out = stream or sys.stdout
    for line in lines:
        out.write(line + "\n")
    out.flush()


def check_root(cwd, repo):
    if repo is not None and os.path.realpath(repo) != os.path.realpath(cwd):
        raise Refusal(
            "run mitosis from the repository root %s; every Step path and the document path "
            "are relative to it" % repo
        )


def run_pipeline(args):
    cwd = os.getcwd()
    repo = repository_root(cwd)
    check_root(cwd, repo)
    root = cwd
    models = tier_models(args.tier_model)
    check_templates(args)
    check_pull_request_flags(args)
    charter = check_charter(args.charter)
    graph = load_graph(args.graph, root)
    run_dir = input_run_dir(args, root)
    recorder = metrics.opened(args, repo, cwd, run_dir, models, achieved_parallelism)
    recorded = argparse.Namespace(**{**vars(args), "recorder": recorder})
    try:
        code = run_stages(recorded, repo, root, models, charter, graph, run_dir)
    except UnknownProvenHead:
        recorder.ended(EXIT_USAGE, EXIT_MEANING[EXIT_USAGE])
        raise
    except Refusal:
        recorder.ended(EXIT_REFUSED, EXIT_MEANING[EXIT_REFUSED])
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
    if not resolved["briefed"]:
        _print(
            report(
                None, None, decomposed, root, run_dir, planned_code(items), from_items,
                decisions, items,
            )
        )
        return _summarised({"items": items}, None, run_dir, planned_code(items))
    plan = build_plan(args, items, root, charter, graph, run_dir)
    recorder.planned(plan, lane_width(plan), len(empty_manifests(plan.get("items") or [])))
    proven_heads(args.proven_head, plan)
    refuse_unfit_titles(pull_request_header(args), plan)
    args = argparse.Namespace(
        **{**vars(args), "loaded_gate_classes": gate_classes(args.gate_classes, plan)}
    )
    if args.plan_only:
        run.write_json(os.path.join(run_dir, run.PLAN_FILE), plan)
        run.write_json(os.path.join(run_dir, ITEMS_FILE), items)
        _print(
            report(plan, None, decomposed, root, run_dir, planned_code(items), from_items, decisions)
        )
        locked, troubles = planned_referee(args, plan, repo)
        return _summarised(plan, None, run_dir, planned_code(items), locked, troubles)
    refuse_run(args, plan, models, run_dir, repo)
    base = pinned_base(args, plan, run_dir, repo)
    locked = locked_referee(args, plan, repo, base)
    run.write_json(os.path.join(run_dir, ITEMS_FILE), items)
    if plan.get("msps"):
        state, failure = execute(args, plan, models, run_dir, repo, root, base)
    else:
        run.write_json(os.path.join(run_dir, run.PLAN_FILE), plan)
        state, failure = {}, None
    if plan.get("check_author"):
        locked = (state or {}).get("referee")
    code = exit_code(state, plan)
    if failure is not None and code == EXIT_SHIPPED:
        code = EXIT_INCOMPLETE
    _print(report(plan, state, decomposed, root, run_dir, code, from_items, decisions))
    if failure is not None:
        _print(("the run stopped: %s" % failure,), sys.stderr)
    return _summarised(plan, state, run_dir, code, locked)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.items and not args.spec:
        parser.error("one of --items or --spec is required; mitosis has no input without them")
    if args.items and args.spec:
        parser.error("--items and --spec are two front ends to one pipeline; pass one, not both")
    if args.spec and not args.decompose_command and not args.resume:
        parser.error("--spec needs --decompose-command; a document becomes Steps only through decompose")
    if args.items and args.revise:
        parser.error("--revise revises the structure in the run directory, which --items never writes")
    if args.resume and args.revise:
        parser.error("--revise runs the structure stage, which --resume skips; run --plan-only --revise first")
    if args.session_tokens < 1:
        parser.error("--session-tokens must be at least 1")
    if args.structure_samples < 1:
        parser.error("--structure-samples must be at least 1")
    if args.pick is not None and not 1 <= args.pick <= args.structure_samples:
        parser.error(
            "--pick %d names a sample that will not exist; --structure-samples is %d"
            % (args.pick, args.structure_samples)
        )
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


if __name__ == "__main__":
    sys.exit(main())
