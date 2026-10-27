import datetime
import glob
import json
import os
import re
import sys

import core
import metrics_writer
import run
import spend

PLUGIN = "mitosis"

CATALOGUE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalogue.json")

UNSAFE_CHARACTER = re.compile(r"[^a-z0-9.-]")

LEADING_UNSAFE = re.compile(r"^[^a-z0-9]+")

FALLBACK_NAME = "run"

STAMP_LENGTH = len(".YYYYMMDDTHHMMSSZ")

NAME_LIMIT = metrics_writer.NAME_LIMIT - STAMP_LENGTH

PARTITION_SEPARATOR = "="

SPEND_KEYS = ("usd", "tokens", "spend", "prices_date", "unpriced_models", "sessions_missing")

RECONCILE_KEYS = ("undeclared", "unwritten", "crossing", "fatal")


def field_name(value):
    return str(value).replace("-", "_")


def zero_counts(values):
    return {field_name(value): 0 for value in values}


def counted(values, keys):
    names = tuple(field_name(value) for value in values if value in keys)
    return {**zero_counts(keys), **{name: names.count(name) for name in set(names)}}


def mode_of(args):
    if args.plan_only:
        return "plan-only"
    if args.resume:
        return "resume" if args.no_push else "ship"
    return "build"


def run_name(run_dir):
    folder = os.path.basename(os.path.normpath(run_dir)).lower()
    cleaned = LEADING_UNSAFE.sub("", UNSAFE_CHARACTER.sub("-", folder))
    return cleaned[:NAME_LIMIT] or FALLBACK_NAME


def numbered_name(name, number):
    if number == 1:
        return name
    suffix = "-%d" % number
    return name[: NAME_LIMIT - len(suffix)] + suffix


def taken(root, repo, candidate):
    return os.path.lexists(
        metrics_writer.event_path(root, PLUGIN, repo, candidate)
    ) or os.path.lexists(metrics_writer.summary_path(root, PLUGIN, repo, candidate))


def free_run_id(root, repo, name, start):
    number = 1
    while taken(root, repo, metrics_writer.run_id(numbered_name(name, number), start)):
        number = number + 1
    return metrics_writer.run_id(numbered_name(name, number), start)


def moment(text):
    return datetime.datetime.fromisoformat(text)


def warn(text):
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def warn_failure(what, error):
    warn(metrics_writer.WARNING_PREFIX + what + ": " + (str(error) or type(error).__name__))


def tier_model_records(models):
    return [{"tier": tier, "model": models[tier]} for tier in core.TIERS if tier in (models or {})]


def partition(folder):
    return os.path.basename(folder).partition(PARTITION_SEPARATOR)[2]


def read_lines(path):
    with open(path, encoding="utf-8") as handle:
        return tuple(json.loads(line) for line in handle if line.strip())


def store_files(root, kind, suffix):
    pattern = os.path.join(glob.escape(root), "plugin=*", "kind=" + kind, "repo=*", "*" + suffix)
    return sorted(glob.glob(pattern))


def run_of(path, suffix):
    repo_folder = os.path.dirname(path)
    plugin_folder = os.path.dirname(os.path.dirname(repo_folder))
    return partition(plugin_folder), partition(repo_folder), os.path.basename(path)[: -len(suffix)]


def child_records(root, parent, kind, suffix):
    found = ()
    for path in store_files(root, kind, suffix):
        try:
            lines = read_lines(path)
        except (OSError, ValueError) as error:
            warn_failure("reading child " + kind + " " + path, error)
            continue
        if not lines or lines[0].get("parent") != parent:
            continue
        plugin, repo, name = run_of(path, suffix)
        found = found + (({"plugin": plugin, "repo": repo, "run": name}, lines),)
    return found


def child_event_runs(root, parent):
    return tuple(
        {**place, "session": lines[0].get("session"), "start": lines[0].get("ts"), "end": lines[-1].get("ts")}
        for place, lines in child_records(root, parent, "event", metrics_writer.EVENT_SUFFIX)
    )


def child_summary_runs(root, parent):
    return tuple(
        {**place, "usd": lines[0].get("usd")}
        for place, lines in child_records(root, parent, "summary", metrics_writer.SUMMARY_SUFFIX)
    )


def children_of(root, parent):
    events = child_event_runs(root, parent)
    summaries = {(entry["plugin"], entry["repo"], entry["run"]): entry for entry in child_summary_runs(root, parent)}
    keys = tuple(dict.fromkeys(
        tuple((entry["plugin"], entry["repo"], entry["run"]) for entry in events) + tuple(summaries)
    ))
    listed = [
        {
            "plugin": key[0],
            "run": key[2],
            "usd": summaries[key]["usd"] if key in summaries else None,
            "missing": key not in summaries,
        }
        for key in keys
    ]
    spans = [
        {"session": entry["session"], "start": entry["start"], "end": entry["end"]}
        for entry in events
        if entry["session"] is not None
    ]
    return listed, spans


def unknown_spend():
    return {
        "usd": None,
        "tokens": None,
        "spend": [],
        "prices_date": None,
        "unpriced_models": [],
        "sessions_missing": None,
    }


def reconcile_counts(findings):
    found = findings if isinstance(findings, dict) else {}
    return {
        "undeclared": len(found.get("undeclared") or ()),
        "unwritten": len(found.get("unwritten") or ()),
        "crossing": len(found.get("crossing") or ()),
        "fatal": 1 if found.get("fatal") else 0,
    }


def lane_seconds(record):
    seconds = run.seconds_between(record.get("started"), record.get("finished"))
    return seconds if seconds is not None else 0


class Run:
    def __init__(self, identity, parallelism):
        self.identity = identity
        self.parallelism = parallelism
        self.seen = {
            "plan": None,
            "steps": None,
            "calls": (),
            "dispatched": {},
            "settled": {},
            "gated": {},
            "reconciled": {},
            "finished": {},
        }

    @property
    def env(self):
        return None if self.identity is None else self.identity["env"]

    def guarded(self, what, action):
        if self.identity is None:
            return
        try:
            action()
        except Exception as error:
            warn_failure(what, error)

    def remember(self, **changes):
        self.seen = {**self.seen, **changes}

    def append(self, event, fields):
        identity = self.identity
        warning = metrics_writer.append_event(
            PLUGIN,
            core.__version__,
            identity["repo"],
            identity["run"],
            event,
            fields,
            now=moment(run.now()),
        )
        if warning is not None:
            warn(warning)

    def label(self, msp):
        plan = self.seen["plan"]
        return run._label(plan["plan"], msp) if plan is not None else str(msp)

    def model_of(self, tier):
        return (self.identity["models"] or {}).get(tier)

    def tier_of(self, lane):
        plan = self.seen["plan"]
        lanes = (plan["plan"].get("lanes") or []) if plan is not None else []
        return lanes[lane].get("tier") if 0 <= lane < len(lanes) else None

    def started(self):
        identity = self.identity
        self.append(
            "run.start",
            {
                "mode": identity["mode"],
                "input": identity["input"],
                "concurrency": identity["concurrency"],
                "plan_id": None,
                "run_dir": identity["run_dir"],
                "tier_models": tier_model_records(identity["models"]),
            },
        )

    def stage_ended(self, stage, began, session_ids):
        def record():
            calls = tuple((stage, session) for session in session_ids)
            self.remember(calls=self.seen["calls"] + calls)
            self.append(
                "stage.end",
                {
                    "stage": stage,
                    "seconds": run.seconds_between(began, run.now()),
                    "calls": len(calls),
                },
            )

        self.guarded("the " + stage + " stage", record)

    def structured(self, items):
        steps = len(items) if isinstance(items, list) else None
        self.guarded("the Step count", lambda: self.remember(steps=steps))

    def planned(self, plan, width, fatal):
        def record():
            figures = {
                "plan": plan,
                "plan_id": plan.get("plan_id"),
                "steps": len(plan.get("items") or []),
                "lanes": len(plan.get("lanes") or []),
                "msps": len(plan.get("msps") or []),
                "clusters": len(plan.get("clusters") or []),
                "lane_width": width,
            }
            self.remember(plan=figures)
            self.append(
                "plan",
                {
                    **{key: value for key, value in figures.items() if key != "plan"},
                    "fatal": fatal,
                },
            )

        self.guarded("the plan", record)

    def observe(self, boundary, record):
        handlers = {
            "lane-dispatched": self.lane_dispatched,
            "dispatch-ended": self.dispatch_ended,
            "gated": self.gated,
            "reconciled": self.reconciled,
            "msp-finished": self.msp_finished,
        }
        handler = handlers.get(boundary)
        if handler is not None:
            self.guarded("the " + boundary + " boundary", lambda: handler(record))

    def lane_fields(self, lane, msp, tier, session_id):
        return {
            "lane": lane,
            "msp": self.label(msp),
            "tier": tier,
            "model": self.model_of(tier),
            "session_id": session_id,
        }

    def lane_dispatched(self, record):
        lane = record["lane"]
        fields = self.lane_fields(lane, record["msp"], record.get("tier"), record.get("session_id"))
        self.remember(
            dispatched={**self.seen["dispatched"], lane: fields},
            calls=self.seen["calls"] + (("worker", record.get("session_id")),),
        )
        self.append("lane.start", fields)

    def lane_end_fields(self, lane, record, **ending):
        return {
            **self.lane_fields(lane, record.get("msp"), self.tier_of(lane), record.get("session_id")),
            "state": record.get("state"),
            **ending,
            "seconds": lane_seconds(record),
        }

    def lane_runs(self):
        return [
            self.lane_end_fields(lane, record)
            for lane, record in sorted(self.seen["settled"].items())
            if record.get("started") is not None
        ]

    def dispatch_ended(self, record):
        dispatched = self.seen["dispatched"]
        settled = {
            int(lane): entry
            for lane, entry in (record.get("lanes") or {}).items()
            if int(lane) in dispatched or entry.get("started") is None
        }
        self.remember(settled=settled)
        for lane, entry in sorted(settled.items()):
            if lane not in dispatched:
                continue
            self.append(
                "lane.end",
                self.lane_end_fields(
                    lane, entry, exit=entry.get("exit"), timed_out=bool(entry.get("timed_out"))
                ),
            )
        self.write_summary(None, None)

    def gated(self, record):
        msp = record["msp"]
        outcome = (record.get("gate") or {}).get("outcome")
        fields = {
            "msp": self.label(msp),
            "outcome": outcome,
            "reused": bool(record.get("reused")),
            "seconds": record.get("seconds"),
        }
        self.remember(gated={**self.seen["gated"], msp: fields})
        self.append("gate", fields)

    def reconciled(self, record):
        msp = record["msp"]
        counts = reconcile_counts(record.get("findings"))
        self.remember(reconciled={**self.seen["reconciled"], msp: counts})
        self.append("reconcile", {"msp": self.label(msp), **counts})

    def msp_finished(self, record):
        msp = record["msp"]
        finished = record.get("record") or {}
        fields = {"msp": self.label(msp), "state": finished.get("state")}
        stacked = bool((finished.get("ship") or {}).get("stacking_exception"))
        self.remember(finished={**self.seen["finished"], msp: {**fields, "stacked": stacked}})
        self.append("msp.end", fields)

    def foreign_writes(self):
        entries = [
            {**entry, "lane": lane}
            for lane, record in self.seen["settled"].items()
            for entry in record.get("foreign_writes") or ()
        ]
        return len(run.unique_foreign(entries))

    def msp_runs(self):
        return [
            {
                "msp": finished["msp"],
                "state": finished["state"],
                "gate": (self.seen["gated"].get(msp) or {}).get("outcome"),
                "reconcile_clean": None
                if msp not in self.seen["reconciled"]
                else not any(self.seen["reconciled"][msp].values()),
            }
            for msp, finished in sorted(self.seen["finished"].items())
        ]

    def own_spend(self, start, end, spans):
        try:
            found = spend.as_dict(
                spend.run_spend(
                    os.environ.get("HOME") or os.path.expanduser("~"),
                    [{"source": source, "session": session} for source, session in self.seen["calls"]],
                    os.environ.get(metrics_writer.SESSION_VARIABLE) or None,
                    (spend.instant(start), spend.instant(end)),
                    spans,
                    spend.load_prices(),
                )
            )
        except Exception as error:
            warn_failure("reading spend", error)
            return unknown_spend()
        return {key: found[key] for key in SPEND_KEYS}

    def summary_fields(self, code, meaning, end):
        identity = self.identity
        plan = self.seen["plan"]
        runs = self.lane_runs()
        settled = self.seen["settled"]
        gated = tuple(self.seen["gated"].values())
        try:
            listed, spans = children_of(identity["root"], identity["parent"])
        except Exception as error:
            warn_failure("scanning the store for child runs", error)
            listed, spans = [], []
        spent = self.own_spend(identity["start"], end, spans)
        return {
            "mode": identity["mode"],
            "input": identity["input"],
            "exit": code,
            "meaning": meaning,
            "plan_id": plan["plan_id"] if plan is not None else None,
            "run_dir": identity["run_dir"],
            "steps": plan["steps"] if plan is not None else self.seen["steps"],
            "lanes": plan["lanes"] if plan is not None else None,
            "msps": plan["msps"] if plan is not None else None,
            "clusters": plan["clusters"] if plan is not None else None,
            "lane_width": plan["lane_width"] if plan is not None else None,
            "peak_parallel": self.parallelism(
                {str(lane): record for lane, record in settled.items() if record.get("started")}
            )[0],
            "concurrency": identity["concurrency"],
            "lane_states": counted(
                tuple(record.get("state") for record in settled.values()), core.LANE_STATES
            ),
            "timed_out": sum(1 for record in settled.values() if record.get("timed_out")),
            "lane_seconds": sum(entry["seconds"] for entry in runs),
            "wall_seconds": run.seconds_between(identity["start"], end),
            "msp_states": counted(
                tuple(entry["state"] for entry in self.seen["finished"].values()), core.MSP_STATES
            ),
            "gates": counted(tuple(entry["outcome"] for entry in gated), core.GATE_OUTCOMES),
            "gates_reused": sum(1 for entry in gated if entry["reused"]),
            "reconcile": {
                key: sum(counts[key] for counts in self.seen["reconciled"].values())
                for key in RECONCILE_KEYS
            },
            "foreign_writes": self.foreign_writes(),
            "stacking_exceptions": sum(
                1 for entry in self.seen["finished"].values() if entry["stacked"]
            ),
            "tier_models": tier_model_records(identity["models"]),
            "lane_runs": runs,
            "msp_runs": self.msp_runs(),
            **spent,
            "spend_complete": spent["sessions_missing"] == 0 and not spent["unpriced_models"],
            "children": listed,
            "spend_scope": "own",
        }

    def write_summary(self, code, meaning):
        identity = self.identity
        end = run.now()
        warning = metrics_writer.write_summary(
            PLUGIN,
            core.__version__,
            identity["repo"],
            identity["run"],
            self.summary_fields(code, meaning, end),
            now=moment(end),
        )
        if warning is not None:
            warn(warning)

    def ended(self, code, meaning):
        def record():
            self.append("run.end", {"exit": code, "meaning": meaning})
            self.write_summary(code, meaning)

        self.guarded("the run's end", record)


UNRECORDED = Run(None, None)


def write_catalogue():
    try:
        with open(CATALOGUE_PATH, encoding="utf-8") as handle:
            catalogue = json.load(handle)
    except (OSError, ValueError) as error:
        warn_failure("loading " + CATALOGUE_PATH, error)
        return
    warning = metrics_writer.write_catalogue(PLUGIN, catalogue)
    if warning is not None:
        warn(warning)


def identity_for(args, repo, cwd, run_dir, models):
    start = run.now()
    root = metrics_writer.store_root(os.environ)
    repo_name = os.path.basename(os.path.normpath(repo or cwd))
    run_id = free_run_id(root, repo_name, run_name(run_dir), moment(start))
    return {
        "root": root,
        "repo": repo_name,
        "run": run_id,
        "parent": PLUGIN + "/" + run_id,
        "start": start,
        "mode": mode_of(args),
        "input": "items" if args.items else "spec",
        "concurrency": args.concurrency,
        "run_dir": run_dir,
        "models": dict(models or {}),
        "env": {**os.environ, metrics_writer.PARENT_VARIABLE: PLUGIN + "/" + run_id},
    }


def opened(args, repo, cwd, run_dir, models, parallelism):
    try:
        identity = identity_for(args, repo, cwd, run_dir, models)
    except Exception as error:
        warn_failure("opening the run", error)
        return UNRECORDED
    recorder = Run(identity, parallelism)
    recorder.guarded("the catalogue", write_catalogue)
    recorder.guarded("the run's start", recorder.started)
    return recorder
