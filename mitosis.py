import argparse
import os
import sys
from datetime import datetime

import core
import decompose
import run

EXIT_SHIPPED = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_INCOMPLETE = 4
EXIT_NO_BRANCHES = 5
EXIT_RECONCILE = 6

LANE_EXIT = {
    "ok": EXIT_SHIPPED,
    "failed": 10,
    "blocked": 11,
    "merge-blocked": 12,
}

MSP_EXIT = {
    "shipped": EXIT_SHIPPED,
    run.MSP_BLOCKED: 11,
    "gate-failed": 20,
    "gate-inconclusive": 21,
    "ship-failed": 22,
}

EXIT_MEANING = {
    EXIT_SHIPPED: "every MSP shipped and reconcile found nothing",
    EXIT_USAGE: "the flags did not parse",
    EXIT_REFUSED: "refused to start",
    EXIT_INCOMPLETE: "the run stopped before every Lane and MSP reached a terminal state",
    EXIT_NO_BRANCHES: "the run created no branches",
    EXIT_RECONCILE: "reconcile found a write outside the declaration or a declared path never written",
    LANE_EXIT["failed"]: "a Lane failed",
    LANE_EXIT["blocked"]: "a Lane or MSP was blocked by a predecessor",
    LANE_EXIT["merge-blocked"]: "a producer branch would not merge",
    MSP_EXIT["gate-failed"]: "a gate found an inert acceptance property",
    MSP_EXIT["gate-inconclusive"]: "a gate could not reach a verdict",
    MSP_EXIT["ship-failed"]: "a commit, push or pull-request command failed",
}

DECOMPOSE_TIER = "top"

RUNS_DIR = ("~", ".mitosis", "runs")

ITEMS_FILE = "items.json"

DECOMPOSE_FILE = "decompose.json"

DECOMPOSE_LOG = "decompose.log"

REMOTE = "origin"

REPORT_SECTIONS = (
    "Coverage map",
    "Assumptions",
    "Constraints count",
    "Drift",
    "Split quality",
    "Gate outcomes",
    "Reconcile findings",
    "Unreviewed coupling pairs",
    "Stacking exceptions",
    "Outcome",
)

PLACEHOLDER_HELP = {
    "dispatch": "{task} {model} {tier} {worktree} {branch} {lane} {msp} {charter} {document} {run_dir}",
    "decompose": "{prompt} {model} {document}",
    "acceptance": "{file} {test} {worktree}",
    "pull-request": "{branch} {base} {title} {body} {worktree} {msp} {remote}",
}

FLAG_SPECS = {
    "--items": {
        "metavar": "PATH",
        "help": "a JSON array of Steps; the plan is built from it directly",
    },
    "--spec": {
        "metavar": "PATH",
        "help": "a document in any format; decompose turns it into Steps first, "
        "which needs --decompose-command",
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
        "help": "continue the run in --run-dir: Lanes already ok are skipped and a failed "
        "gate is re-run; the plan id must match",
    },
    "--plan-only": {
        "action": "store_true",
        "help": "validate, schedule, write the plan and print the plan-stage report; "
        "spawn nothing and exit zero when the plan is valid",
    },
    "--dispatch-command": {
        "metavar": "TEMPLATE",
        "help": "one Worker per Lane; placeholders %s; split into argv before substitution"
        % PLACEHOLDER_HELP["dispatch"],
    },
    "--decompose-command": {
        "metavar": "TEMPLATE",
        "help": "the decompose Worker; placeholders %s; the prompt also arrives on stdin"
        % PLACEHOLDER_HELP["decompose"],
    },
    "--acceptance-command": {
        "metavar": "TEMPLATE",
        "help": "runs one acceptance property; placeholders %s; exit 0 passes, exit %d fails, "
        "anything else is inconclusive"
        % (PLACEHOLDER_HELP["acceptance"], run.ACCEPTANCE_FAILURE_EXIT),
    },
    "--pr-command": {
        "metavar": "TEMPLATE",
        "help": "opens one draft pull request; placeholders %s; its last stdout line is recorded"
        % PLACEHOLDER_HELP["pull-request"],
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
        "default": 1,
        "help": "Workers running at once (default: 1)",
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
        "help": "most graph neighbours kept per Step; the overflow is counted (default: no cap)",
    },
    "--graph": {
        "metavar": "PATH",
        "help": "a JSON object mapping each path to the paths it imports",
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
    "--version": {"action": "version", "version": core.__version__},
    "--help": {"action": "help", "help": "show this message and exit"},
}


class Refusal(Exception):
    pass


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mitosis",
        add_help=False,
        description="Split a document or an items file into MSPs, build each on its own "
        "branch, gate the work, and open one draft pull request per MSP. "
        "mitosis never merges.",
    )
    for name in core.FLAG_NAMES:
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
    used = sorted({lane.get("tier") for lane in plan.get("lanes") or ()}, key=str)
    return tuple(tier for tier in used if tier not in (models or {}))


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


def load_graph(path):
    if path is None:
        return None
    graph = read_json(path, "--graph")
    if not isinstance(graph, dict):
        raise Refusal("--graph %s must be a JSON object mapping each path to its neighbours" % path)
    return graph


def check_templates(args):
    for name, template in (
        ("dispatch", args.dispatch_command),
        ("decompose", args.decompose_command),
        ("acceptance", args.acceptance_command),
        ("pull-request", args.pr_command),
    ):
        if template is None:
            continue
        try:
            run.check_template(name, template)
        except run.ConfigError as error:
            raise Refusal(str(error))


def decompose_document(args, root, run_dir, models, charter, graph):
    model = models.get(DECOMPOSE_TIER)
    if "{model}" in args.decompose_command and model is None:
        raise Refusal(
            "the decompose command uses {model} but no model is mapped for tier %s; "
            "map it with --tier-model %s=<model>" % (DECOMPOSE_TIER, DECOMPOSE_TIER)
        )
    if args.timeout is None:
        raise Refusal("--timeout is required to run decompose; mitosis has no default")
    path = document_path(args.spec, root)
    if not os.path.isfile(os.path.join(root, path)):
        raise Refusal("--spec %s is not a file" % args.spec)
    result = decompose.decompose(
        path,
        args.decompose_command,
        root,
        args.timeout,
        model=model,
        graph=graph,
        charter=charter,
        log=os.path.join(run_dir, DECOMPOSE_LOG),
    )
    record = {key: result[key] for key in result if key != "items"}
    run.write_json(os.path.join(run_dir, DECOMPOSE_FILE), record)
    run.write_json(os.path.join(run_dir, ITEMS_FILE), result["items"])
    _print(decompose.report(result))
    if result["errors"]:
        raise Refusal(
            "decompose returned %d contract errors; the log is %s"
            % (len(result["errors"]), result["log"])
        )
    return result["items"], record


def load_decomposed(run_dir):
    path = os.path.join(run_dir, DECOMPOSE_FILE)
    if not os.path.isfile(path):
        return None
    record = read_json(path, "the decompose record")
    return record if isinstance(record, dict) else None


def resolve_input(args, root, models, charter, graph):
    if args.items:
        items_path = os.path.abspath(args.items)
        run_dir = args.run_dir or default_run_dir(root, "items", file_digest(items_path))
        run_dir = os.path.abspath(run_dir)
        items = read_json(items_path, "--items")
        return items, load_decomposed(run_dir), run_dir
    spec_path = os.path.abspath(args.spec)
    run_dir = args.run_dir or default_run_dir(root, "spec", file_digest(spec_path))
    run_dir = os.path.abspath(run_dir)
    if args.resume:
        persisted = os.path.join(run_dir, ITEMS_FILE)
        if not os.path.isfile(persisted):
            raise Refusal(
                "nothing to resume: %s holds no %s; a --spec resume reads the Steps "
                "decomposed by the first run rather than decomposing again" % (run_dir, ITEMS_FILE)
            )
        return read_json(persisted, "the persisted items file"), load_decomposed(run_dir), run_dir
    if os.path.isfile(os.path.join(run_dir, run.STATE_FILE)):
        raise Refusal(
            "%s already holds a run; pass --resume to continue it or --run-dir to start elsewhere"
            % run_dir
        )
    items, record = decompose_document(args, root, run_dir, models, charter, graph)
    return items, record, run_dir


def build_plan(args, items, root, charter, graph):
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
        )
    except core.ValidationError as error:
        raise Refusal(
            "the Steps did not validate:\n" + "\n".join("  " + line for line in error.errors)
        )


def refuse_run(args, plan, models, run_dir, repo):
    if repo is None:
        raise Refusal("the working directory is not inside a git repository")
    missing = [
        flag
        for flag, value in (
            ("--feature-branch", args.feature_branch),
            ("--dispatch-command", args.dispatch_command),
            ("--acceptance-command", args.acceptance_command),
            ("--pr-command", args.pr_command),
            ("--timeout", args.timeout),
        )
        if value is None
    ]
    if missing:
        raise Refusal("a run needs %s; pass --plan-only to stop at the plan" % ", ".join(missing))
    if not run.branch_exists(repo, args.feature_branch):
        raise Refusal("--feature-branch %s is not a branch of %s" % (args.feature_branch, repo))
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
    source = plan.get("source")
    if not isinstance(source, dict) or not source.get("path"):
        return ("unavailable: the Steps declare no source document, so there are no sections to claim",)
    covered = (decomposed or {}).get("coverage")
    if not isinstance(covered, dict):
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
    depths = _depths(len(plan.get("lanes") or []), run.lane_edges(plan))
    levels = {}
    for depth in depths.values():
        levels = {**levels, depth: levels.get(depth, 0) + 1}
    return max(levels.values()) if levels else 0


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
        [(a, 1) for a, _ in spans] + [(b, -1) for _, b in spans], key=lambda e: (e[0], -e[1])
    )
    peak = 0
    live = 0
    for _, delta in events:
        live = live + delta
        peak = max(peak, live)
    return peak, len(spans)


def split_lines(plan, state):
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
    lines = lines + tuple(
        "Lane %d (MSP %s, %s): %s, cost %s: %s"
        % (
            index,
            _label(plan, lane["msp"]),
            lane.get("tier"),
            _n(len(lane.get("steps") or []), "Step"),
            lane.get("cost"),
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
            if entry.get("outcome") == "pass":
                continue
            where = "%s::%s" % (entry.get("file"), entry.get("test")) if entry.get("file") else entry.get("step")
            lines = lines + ("  %s: %s (%s)" % (where, entry.get("outcome"), _one_line(entry.get("reason"))),)
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
        crossing = found.get("crossing") or []
        findings = findings + len(undeclared) + len(unwritten)
        lines = lines + (
            "MSP %s: %d undeclared, %d unwritten, %d crossing an MSP boundary"
            % (_label(plan, index), len(undeclared), len(unwritten), len(crossing)),
        )
        lines = lines + tuple("  undeclared: " + path for path in undeclared)
        lines = lines + tuple("  unwritten: " + path for path in unwritten)
        lines = lines + tuple(
            "  crossing: %s belongs to MSP %s" % (entry.get("path"), entry.get("label"))
            for entry in crossing
        )
    return ("%s from the git log" % _n(findings, "finding"),) + lines


def coupling_lines(plan, state):
    pairs = plan.get("coupling_review") or []
    if not pairs:
        return ("none: no write-set-disjoint pair in different Lanes shares a coupling signal",)
    verb = "ran" if state is not None else "will run"
    lines = (
        "%s share a signal and carry no checkpoint verdict; they %s unreviewed"
        % (_n(len(pairs), "pair"), verb),
    )
    return lines + tuple(
        "%s and %s (Lanes %s): %s"
        % (
            pair["steps"][0],
            pair["steps"][1],
            ", ".join(str(lane) for lane in pair.get("lanes") or []),
            ", ".join(pair.get("signals") or []),
        )
        for pair in pairs
    )


def stacking_lines(plan, state):
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


def exit_code(state, plan):
    lanes = plan.get("lanes") or []
    msps = plan.get("msps") or []
    if not msps or not (state or {}).get("worktrees"):
        return EXIT_NO_BRANCHES
    lane_records = (state or {}).get("lanes") or {}
    for index in range(len(lanes)):
        record = lane_records.get(str(index))
        if not isinstance(record, dict):
            return EXIT_INCOMPLETE
        code = LANE_EXIT.get(record.get("state"), EXIT_INCOMPLETE)
        if code != EXIT_SHIPPED:
            return code
    msp_records = (state or {}).get("msps") or {}
    for index in range(len(msps)):
        record = msp_records.get(str(index))
        if not isinstance(record, dict):
            return EXIT_INCOMPLETE
        code = MSP_EXIT.get(record.get("state"), EXIT_INCOMPLETE)
        if code != EXIT_SHIPPED:
            return code
        if reconcile_dirty(record.get("reconcile")):
            return EXIT_RECONCILE
    return EXIT_SHIPPED if run.succeeded(state) else EXIT_INCOMPLETE


def outcome_lines(plan, state, run_dir, code):
    lines = ("run directory: %s" % run_dir, "plan id: %s" % plan.get("plan_id"))
    if state is None:
        return lines + ("exit %d: the plan was written and nothing was spawned" % code,)
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


def report(plan, state, decomposed, root, run_dir, code, from_items):
    sections = (
        coverage_lines(plan, decomposed, root),
        assumption_lines(plan),
        constraint_lines(decomposed, from_items),
        drift_lines(plan, root),
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


def execute(args, plan, models, run_dir, repo, root):
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
    charter = check_charter(args.charter)
    graph = load_graph(args.graph)
    items, decomposed, run_dir = resolve_input(args, root, models, charter, graph)
    plan = build_plan(args, items, root, charter, graph)
    from_items = bool(args.items)
    if args.plan_only:
        run.write_json(os.path.join(run_dir, run.PLAN_FILE), plan)
        run.write_json(os.path.join(run_dir, ITEMS_FILE), items)
        _print(report(plan, None, decomposed, root, run_dir, EXIT_SHIPPED, from_items))
        return EXIT_SHIPPED
    refuse_run(args, plan, models, run_dir, repo)
    run.write_json(os.path.join(run_dir, ITEMS_FILE), items)
    if plan.get("msps"):
        state, failure = execute(args, plan, models, run_dir, repo, root)
    else:
        run.write_json(os.path.join(run_dir, run.PLAN_FILE), plan)
        state, failure = {}, None
    code = exit_code(state, plan)
    if failure is not None and code == EXIT_SHIPPED:
        code = EXIT_INCOMPLETE
    _print(report(plan, state, decomposed, root, run_dir, code, from_items))
    if failure is not None:
        _print(("the run stopped: %s" % failure,), sys.stderr)
    return code


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.items and not args.spec:
        parser.error("one of --items or --spec is required; mitosis has no input without them")
    if args.items and args.spec:
        parser.error("--items and --spec are two front ends to one pipeline; pass one, not both")
    if args.spec and not args.decompose_command and not args.resume:
        parser.error("--spec needs --decompose-command; a document becomes Steps only through decompose")
    try:
        return run_pipeline(args)
    except Refusal as refusal:
        _print(("mitosis: %s" % refusal, "exit %d: %s" % (EXIT_REFUSED, EXIT_MEANING[EXIT_REFUSED])), sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
