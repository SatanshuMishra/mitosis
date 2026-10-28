import json
import os
import re
import subprocess

import shape

SENTENCE_END = re.compile(r"[.?!](?= |$)")

NAMEABLE = re.compile(r"[A-Za-z0-9._/-]{1,100}")

UNNAMEABLE_WORDS = ("reverted", "speculative", "unverified")

NAMED_LIMIT = 3

TEST_ROW_LIMIT = 10

PROBE_COMMIT = "0" * 40

INSTALL = "install pull-request 0.3.0 or later"

NO_FIELDS_FLAG = 'unknown flag "--fields"'

RESULTS = {
    "pass": "passes with the work and fails without it",
    "inert": "passes with and without the work",
    "inconclusive": "Not run: the acceptance command was inconclusive",
    "not-applicable": "Not run: the gate does not apply to it",
}

UNJUDGED = "Not run: the gate did not judge it"

NO_ACCEPTANCE = "Not run: the Step names no acceptance test"

GATE_FAILED = {"check": "Acceptance gate", "result": "Not run: the gate could not run"}


class RenderError(RuntimeError):
    pass


def first_sentence(task):
    text = " ".join(str(task or "").split())
    end = SENTENCE_END.search(text)
    return text[: end.end()] if end else text


def _steps(plan, msp):
    by_name = {item.get("name"): item for item in plan.get("items") or []}
    return tuple((name, by_name.get(name) or {}) for name in plan["msps"][msp]["steps"])


def msp_manifest_gaps(plan, msp):
    items = plan.get("items") or []
    owned = set(plan["msps"][msp]["steps"])
    return list(
        dict.fromkeys(
            gap["manifest"]
            for gap in shape.manifest_gaps(items)
            if gap["reached"] == 0
            and 0 <= gap["owner"] < len(items)
            and items[gap["owner"]].get("name") in owned
        )
    )


def _title(header, plan, msp):
    scope = header.get("scope")
    subject = header["subject"]
    labelled = (
        "%s (%s)" % (subject, plan["msps"][msp]["label"])
        if len(plan.get("msps") or []) > 1
        else subject
    )
    return "%s%s: %s" % (header["type"], "(%s)" % scope if scope else "", labelled)


def _problem(header):
    why = list(header.get("why") or ())
    return why if len(why) <= 4 else [" ".join(why)]


def _impact(header):
    impact = list(header.get("impact") or ())
    return impact if impact else [header.get("blast_radius")]


def _folded(passed, total):
    if passed == total:
        return (
            "1 acceptance test passes with the work and fails without it"
            if total == 1
            else "%d acceptance tests pass with the work and fail without it" % total
        )
    return (
        "%d of %d acceptance tests pass with the work and fail without it; the rest are not proven"
        % (passed, total)
    )


def _probe_folded(passed, total):
    return (
        "%d of %d acceptance tests pass with the work and fail without it; the rest are not proven"
        % (total, total)
    )


def _testing(plan, msp, outcome_of, fold, closing):
    steps = _steps(plan, msp)
    tests = tuple(
        (name, entry) for name, item in steps for entry in item.get("acceptance") or ()
    )
    if len(tests) <= TEST_ROW_LIMIT:
        proven = [
            {
                "check": "%s::%s" % (entry.get("file"), entry.get("test")),
                "result": RESULTS.get(outcome_of(name, entry), UNJUDGED),
            }
            for name, entry in tests
        ]
    else:
        proven = [
            {
                "check": name,
                "result": fold(
                    sum(1 for entry in item["acceptance"] if outcome_of(name, entry) == "pass"),
                    len(item["acceptance"]),
                ),
            }
            for name, item in steps
            if item.get("acceptance")
        ]
    untested = [
        {"check": name, "result": NO_ACCEPTANCE} for name, item in steps if not item.get("acceptance")
    ]
    return proven + untested + list(closing)


def _proven_head_row(proven_head, gated):
    return {
        "check": "Proven head",
        "result": "Not run: the caller proved %s after mitosis gated %s"
        % (proven_head[:12], gated[:12]),
    }


def _closing(gate_result, gate_error, proven_head):
    gated = (gate_result or {}).get("commit") or ""
    return ([GATE_FAILED] if gate_error else []) + (
        [_proven_head_row(proven_head, gated)]
        if proven_head is not None and proven_head != gated
        else []
    )


def _outcomes(gate_result):
    judged = {
        (entry.get("step"), entry.get("file"), entry.get("test")): entry.get("outcome")
        for entry in (gate_result or {}).get("properties") or []
    }
    return lambda name, entry: judged.get((name, entry.get("file"), entry.get("test")))


def _nameable(path):
    return (
        isinstance(path, str)
        and NAMEABLE.fullmatch(path) is not None
        and not any(word in path.lower() for word in UNNAMEABLE_WORDS)
    )


def _counted(count, singular, plural):
    return "%d %s" % (count, singular if count == 1 else plural)


def _names(paths):
    named = [path for path in paths if _nameable(path)][:NAMED_LIMIT]
    hidden = len(paths) - len(named)
    if not hidden:
        return named[0] if len(named) == 1 else "%s and %s" % (", ".join(named[:-1]), named[-1])
    return "%s and %d more" % (", ".join(named), hidden)


def _sentence(lead, paths, tail=""):
    if not any(_nameable(path) for path in paths):
        return "%s%s." % (lead, tail)
    return "%s%s: %s." % (lead, tail, _names(paths))


def _paths_of(entries):
    return list(dict.fromkeys(entry.get("path") for entry in entries or ()))


def _tradeoffs(plan, msp, findings, exception, foreign):
    found = findings or {}
    undeclared = list(dict.fromkeys(found.get("undeclared") or ()))
    unwritten = list(dict.fromkeys(found.get("unwritten") or ()))
    crossing = _paths_of(found.get("crossing"))
    foreign_paths = _paths_of(foreign)
    gaps = msp_manifest_gaps(plan, msp)
    sentences = (
        undeclared
        and _sentence(
            "Reconcile found %s"
            % _counted(len(undeclared), "undeclared write", "undeclared writes"),
            undeclared,
        ),
        unwritten
        and _sentence(
            "Reconcile found %s"
            % _counted(len(unwritten), "declared path", "declared paths"),
            unwritten,
            " never written",
        ),
        crossing
        and _sentence(
            "Another MSP also changes %s" % _counted(len(crossing), "file", "files"),
            crossing,
            " this branch writes",
        ),
        foreign_paths
        and _sentence(
            "A Lane changed %s" % _counted(len(foreign_paths), "file", "files"),
            foreign_paths,
            " another Lane of this MSP owned and had already committed",
        ),
        gaps
        and _sentence(
            "The build leaves %s"
            % _counted(len(gaps), "package manifest", "package manifests"),
            gaps,
            " exporting nothing",
        ),
        exception is not None
        and "This MSP has %s, so its base is the feature branch."
        % _counted(len(exception.get("producers") or ()), "producer", "producers"),
    )
    return [sentence for sentence in sentences if sentence]


def _base(header, plan, msp):
    return {
        "title": _title(header, plan, msp),
        "problem": _problem(header),
        "change": [first_sentence(item.get("task")) for _, item in _steps(plan, msp)],
        "impact": _impact(header),
    }


def facts(
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
    testing = _testing(
        plan, msp, _outcomes(gate_result), _folded, _closing(gate_result, gate_error, proven_head)
    )
    tradeoffs = _tradeoffs(plan, msp, findings, exception, foreign)
    return {
        **_base(header, plan, msp),
        "testing": testing,
        **({"tradeoffs": tradeoffs} if tradeoffs else {}),
    }


def probe_facts(header, plan, msp):
    closing = [GATE_FAILED, _proven_head_row(PROBE_COMMIT, PROBE_COMMIT)]
    return {
        **_base(header, plan, msp),
        "testing": _testing(plan, msp, lambda name, entry: "pass", _probe_folded, closing),
    }


def _refusal(command, path, completed):
    lines = [line.strip() for line in (completed.stderr or "").splitlines() if line.strip()]
    if any(NO_FIELDS_FLAG in line for line in lines):
        return (
            "the pull-request command %s is older than 0.3.0 and its render has no --fields; %s"
            % (command, INSTALL)
        )
    return "pull-request render exited %d on the facts file %s%s" % (
        completed.returncode,
        path,
        ": " + "; ".join(lines) if lines else "",
    )


def _printed(stdout):
    lines = [line for line in (stdout or "").splitlines() if line.strip()]
    if not lines:
        return None
    try:
        value = json.loads(lines[-1])
    except ValueError:
        return None
    if (
        isinstance(value, dict)
        and isinstance(value.get("title"), str)
        and isinstance(value.get("body"), str)
    ):
        return value["title"], value["body"]
    return None


def render(command, facts, path, timeout, env=None):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(facts, ensure_ascii=False) + "\n")
    try:
        completed = subprocess.run(
            [command, "render", "--fields", path],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise RenderError(
            "pull-request render timed out after %s seconds on the facts file %s" % (timeout, path)
        )
    except OSError as error:
        raise RenderError(
            "the pull-request command %s could not run (%s); %s" % (command, error.strerror, INSTALL)
        )
    if completed.returncode != 0:
        raise RenderError(_refusal(command, path, completed))
    printed = _printed(completed.stdout)
    if printed is None:
        raise RenderError(
            "pull-request render printed no title and body for the facts file %s" % path
        )
    return printed
