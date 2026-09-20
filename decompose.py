import concurrent.futures
import copy
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess

import core
import shape

RETURN_KEYS = ("items", "assumptions", "constraints")

DISPATCH_ATTEMPTS = 2

PROMPT_PLACEHOLDER = "{prompt}"
MODEL_PLACEHOLDER = "{model}"
DOCUMENT_PLACEHOLDER = "{document}"
PLACEHOLDERS = (PROMPT_PLACEHOLDER, MODEL_PLACEHOLDER, DOCUMENT_PLACEHOLDER)

SKIPPED_DIRS = (".git", "node_modules", "__pycache__")

DOCUMENT_OPEN = "<<<DOCUMENT"
DOCUMENT_CLOSE = "DOCUMENT>>>"

OPTIONAL_ITEM_FIELDS = tuple(f for f in core.ITEM_FIELDS if f not in core.REQUIRED_ITEM_FIELDS)

RETURN_SHAPE = (
    '{"items":[{...},...],'
    '"assumptions":[{"step":"<name>","text":"<reading chosen>"},...],'
    '"constraints":["<global statement>",...]}'
)

REQUIRED_FIELD_LINES = {
    "name": "  name: kebab-case, unique within this return",
    "task": "  task: the Step's entire brief; the Worker that builds it receives nothing else",
    "files": "  files: the write-set, a non-empty list of paths relative to the codebase root",
    "source": "  source: null; the document path and hash are stamped in for you",
    "acceptance": '  acceptance: a list of {"%s": "<test file>", "%s": "<test identifier>"} objects,'
    % core.ACCEPTANCE_KEYS
    + " never prose; an empty list declares that the Step proves nothing mechanically",
}

OPTIONAL_FIELD_LINES = (
    "  after: names of Steps that must be built first; these edges join parts of the document"
    " that may be far apart, and they can only be seen from the whole of it",
    "  contract_group: one shared id for Steps that ship as ONE pull request because they"
    " are parts of one interface. It does not order them; after edges do that. Use it when"
    " two halves of an interface must be reviewed and merged together",
    "  type: one of " + ", ".join(core.STEP_TYPES),
    "  complexity: one of " + ", ".join(core.COMPLEXITY_VALUES),
    "  file_notes: {path: what changes there}",
    "  msp: a tag forcing Steps to ship as one pull request",
    "  spec_ref: the sections this Step came from, each as its number or its exact heading"
    " text, or as a line number or a range like 12-20 when the document has no headings",
    "  assumptions: the readings you chose where the document was underdetermined",
)

SCHEDULING_LINES = (
    "How a plan is built from what you return, so you can avoid returning one that cannot run:",
    "  Two Steps that list the same file in their write-sets are built by ONE agent, in one"
    " sitting, one after the other. That agent cannot pause in the middle.",
    "  It follows that no Step outside such a pair may sit between them in the after order."
    " If X and Z share a file, do not write X -> Y -> Z with Y owning different files: X and Z"
    " must both wait for Y while Y waits for X, nothing can start, and the plan is refused.",
    "  When two Steps must run at different times, give them different files. When two Steps"
    " genuinely edit the same file, put every Step between them in that same write-set, or"
    " drop the ordering that forces one between them.",
    "  A file that declares what a package exports - __init__.py, index.ts, mod.rs - is the"
    " package's public surface, not a formality. Give it to a Step whose job IS that surface,"
    " and give that Step an after edge reaching every Step whose modules it exports, directly"
    " or through another. A Step that merely needs the file to exist will create it empty, every"
    " test will still pass, and the package will export nothing.",
    "  When the codebase already holds an implementation, a Step may find the document asks"
    " for nothing its files do not already do. Say so: keep the Step, and return an empty"
    " acceptance list. Do NOT invent a property describing behaviour that already works, because"
    " a property that holds before your Step runs proves nothing about it and stops the run.",
)

TOP_LEVEL_LINES = (
    '"assumptions" at the top level lists every such reading with the "step" it belongs to;'
    " a Step with any assumption is never rated simple",
    '"constraints" lists the document\'s global statements that belong to no single Step,'
    " one string each; return an empty list when there are none",
)

TASK_FORBIDDEN = (
    "A Step must not carry a task; a return in which any Step carries one will be rejected."
)


DELTA_KEYS = ("keep", "change", "add", "remove", "assumptions", "constraints")

DELTA_SHAPE = (
    '{"keep":["<name>",...],"change":[{...},...],"add":[{...},...],"remove":["<name>",...],'
    '"assumptions":[{"step":"<name>","text":"<reading chosen>"},...],'
    '"constraints":["<global statement>",...]}'
)

DELTA_RULES = (
    '"keep" and "remove" list names of Steps in the structure being revised; "change" and'
    ' "add" list whole Steps',
    'every Step in the structure being revised must appear in exactly one of "keep", "change"'
    ' or "remove"; a kept Step is carried over unchanged, a changed Step replaces the one of'
    " that name, and an added Step's name must not already exist",
    "a return breaking any of these rules is rejected whole",
)

ITEMS_LEAD = 'Every entry of "items" is one Step, and every Step must carry each of these fields: '

DELTA_LEAD = (
    'Every entry of "change" and "add" is one whole Step, and every Step must carry each of'
    " these fields: "
)


def _contract(shape, lead, required, tail=()):
    return "\n".join(
        (
            "When finished, print one line of JSON and nothing after it:",
            shape,
            lead + ", ".join(required) + ".",
        )
        + tuple(REQUIRED_FIELD_LINES[field] for field in required)
        + ("A Step may also carry: " + ", ".join(OPTIONAL_ITEM_FIELDS) + ".",)
        + OPTIONAL_FIELD_LINES
        + SCHEDULING_LINES
        + TOP_LEVEL_LINES
        + tuple(tail)
    )


CONTRACT = _contract(RETURN_SHAPE, ITEMS_LEAD, core.REQUIRED_ITEM_FIELDS)

STRUCTURE_CONTRACT = _contract(
    RETURN_SHAPE, ITEMS_LEAD, core.STRUCTURE_ITEM_FIELDS, (TASK_FORBIDDEN,)
)

DELTA_CONTRACT = _contract(
    DELTA_SHAPE, DELTA_LEAD, core.STRUCTURE_ITEM_FIELDS, (TASK_FORBIDDEN,) + DELTA_RULES
)

DECISIONS_HEADING = (
    "The following questions are already settled. They are binding on every Step and"
    " must not be re-opened; do not restate them as assumptions."
)

PRIOR_HEADING = (
    "The structure being revised follows, as JSON. Return a delta against it, never a fresh"
    " structure."
)


COVERAGE_MODES = ("headings", "lines", "none")

_ATX = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(```|~~~)")
_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\.?[ \t]+(.+)$")
_RANGE = re.compile(r"^(\d+)\s*-\s*(\d+)$")


def _heading(raw, level, line):
    numbered = _NUMBERED.match(raw)
    if numbered:
        return {
            "id": numbered.group(1),
            "title": numbered.group(2).strip(),
            "heading": raw,
            "line": line,
            "level": level,
        }
    return {"id": raw, "title": raw, "heading": raw, "line": line, "level": level}


def _headings(lines):
    found = ()
    fenced = False
    previous = None
    for number, line in enumerate(lines, 1):
        if _FENCE.match(line):
            fenced = not fenced
            previous = None
            continue
        if fenced:
            continue
        atx = _ATX.match(line)
        if atx:
            title = atx.group(2).strip()
            if title:
                found = found + (_heading(title, len(atx.group(1)), number),)
            previous = None
            continue
        if previous is not None and _SETEXT.match(line):
            level = 1 if line.strip().startswith("=") else 2
            found = found + (_heading(previous.strip(), level, number - 1),)
            previous = None
            continue
        previous = line if line.strip() else None
    return found


_PLAIN_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\.[ \t]+(\S.*?)[ \t]*$")

PLAIN_HEADING_MAX = 80
PLAIN_HEADING_MINIMUM = 3


def _ordinal(identifier):
    return tuple(int(part) for part in identifier.split("."))


def _plain_numbered_candidates(lines):
    found = ()
    fenced = False
    for number, line in enumerate(lines, 1):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced or line[:1] in (" ", "\t") or not line.strip():
            continue
        match = _PLAIN_NUMBERED.match(line)
        if not match or len(match.group(2)) > PLAIN_HEADING_MAX:
            continue
        found = found + ((_ordinal(match.group(1)), line.strip(), number),)
    return found


def _plain_numbered_headings(lines):
    found = _plain_numbered_candidates(lines)
    if len(found) < PLAIN_HEADING_MINIMUM:
        return ()
    if found[0][0] != (1,):
        return ()
    ordinals = [entry[0] for entry in found]
    if ordinals != sorted(ordinals):
        return ()
    return tuple(
        _heading(text, len(ordinal), number) for ordinal, text, number in found
    )


def sections(text):
    if text is None:
        return {
            "mode": "none",
            "sections": [],
            "reason": "the document is not decodable as UTF-8 text",
        }
    lines = text.splitlines()
    found = _headings(lines) or _plain_numbered_headings(lines)
    if found:
        return {"mode": "headings", "sections": list(found), "reason": None}
    found = tuple(
        {"id": str(number), "title": line.strip(), "heading": line.strip(), "line": number}
        for number, line in enumerate(lines, 1)
        if line.strip()
    )
    if found:
        return {"mode": "lines", "sections": list(found), "reason": None}
    return {
        "mode": "none",
        "sections": [],
        "reason": "the document has no headings and no non-blank lines",
    }


def _spec_refs(item):
    if not isinstance(item, dict):
        return ()
    raw = item.get("spec_ref")
    if raw is None:
        return ()
    if isinstance(raw, (str, int)):
        raw = [raw]
    if not isinstance(raw, list):
        return ()
    return tuple(str(claim) for claim in raw if isinstance(claim, (str, int)))


def _normalized(claim):
    return claim.strip().lstrip("#").strip().rstrip(".").strip().lower()


def _claims(claim, section):
    wanted = _normalized(claim)
    if not wanted:
        return False
    candidates = {
        section["id"].lower(),
        section["title"].lower(),
        section["heading"].lower(),
        ("%s %s" % (section["id"], section["title"])).lower(),
        ("%s. %s" % (section["id"], section["title"])).lower(),
    }
    if wanted in candidates:
        return True
    span = _RANGE.match(wanted)
    if span and section["id"].isdigit():
        return int(span.group(1)) <= int(section["id"]) <= int(span.group(2))
    return False


def _step_label(item, index):
    name = item.get("name") if isinstance(item, dict) else None
    return name if isinstance(name, str) and name else "item #%d" % index


def coverage(text, items):
    found = sections(text)
    if found["mode"] == "none":
        return {**found, "claimed": [], "uncovered": [], "unmatched_claims": []}
    claims = tuple(
        (_step_label(item, index), claim)
        for index, item in enumerate(items)
        for claim in _spec_refs(item)
    )
    matched = {
        section["line"]
        for section in found["sections"]
        if any(_claims(claim, section) for _, claim in claims)
    }
    return {
        **found,
        "claimed": [s["id"] for s in found["sections"] if s["line"] in matched],
        "uncovered": [s for s in found["sections"] if s["line"] not in matched],
        "unmatched_claims": [
            "%s: %s" % (label, claim)
            for label, claim in claims
            if not any(_claims(claim, section) for section in found["sections"])
        ],
    }


def _section_label(section):
    if section["id"] == section["title"]:
        return section["title"]
    return "%s %s" % (section["id"], section["title"])


def freeze(path):
    with open(path, "rb") as handle:
        raw = handle.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = None
    return {"path": path, "sha256": hashlib.sha256(raw).hexdigest(), "text": text}


def source_of(document):
    return {key: document[key] for key in core.SOURCE_KEYS}


DECISIONS_SUFFIX = ".decisions.md"


def default_decisions_path(document):
    return os.path.splitext(document)[0] + DECISIONS_SUFFIX


def _decision_count(text):
    return sum(1 for line in text.splitlines() if line.lstrip().startswith("- "))


def load_decisions(root, path=None, document=None):
    if path is not None:
        resolved = os.path.join(root, path)
        if not os.path.isfile(resolved):
            raise ValueError("the decisions file %s does not exist" % resolved)
    elif document is not None:
        resolved = os.path.join(root, default_decisions_path(document))
        if not os.path.isfile(resolved):
            return None
    else:
        return None
    with open(resolved, "rb") as handle:
        text = handle.read().decode("utf-8-sig")
    return {"path": resolved, "text": text, "count": _decision_count(text)}


def _walk(base, relative=""):
    try:
        entries = sorted(os.scandir(os.path.join(base, relative)), key=lambda e: e.name)
    except OSError:
        return
    for entry in entries:
        if entry.name in SKIPPED_DIRS:
            continue
        found = entry.name if not relative else relative + "/" + entry.name
        if entry.is_dir(follow_symlinks=False):
            yield from _walk(base, found)
        elif entry.is_file(follow_symlinks=False):
            yield found


def inventory(root, cap=None):
    paths = tuple(_walk(os.path.abspath(root)))
    limit = len(paths) if cap is None else max(0, int(cap))
    kept = paths[:limit]
    return {"root": root, "paths": list(kept), "overflow": len(paths) - len(kept)}


def _graph_lines(graph):
    if isinstance(graph, str):
        return ["Import graph: %s (adjacency from each path to its neighbours)" % graph]
    if isinstance(graph, dict):
        return ["Import graph, each path to its neighbours:"] + [
            "  %s: %s" % (path, ", ".join(str(n) for n in neighbours))
            for path, neighbours in graph.items()
            if isinstance(neighbours, (list, tuple))
        ]
    return []


def _codebase_lines(codebase):
    if not isinstance(codebase, dict):
        return []
    lines = ["Codebase root: %s" % codebase.get("root")]
    paths = codebase.get("paths") or []
    overflow = codebase.get("overflow") or 0
    shown = "%d" % len(paths)
    if overflow:
        shown = "%d shown, %d more not shown" % (len(paths), overflow)
    lines.append("Files in the codebase (%s):" % shown)
    lines.extend("  " + path for path in paths)
    return lines


def _document_lines(document):
    text = document.get("text")
    if text is None:
        return [
            "The document is not decodable as UTF-8 text; open it from its path.",
        ]
    return [
        "The document follows, between the markers.",
        DOCUMENT_OPEN,
        text.rstrip("\n"),
        DOCUMENT_CLOSE,
    ]


def _claimable_lines(document):
    found = sections(document.get("text"))
    if found["mode"] == "headings":
        return [
            "Sections you may claim in spec_ref, by number or exact heading:"
        ] + ["  " + _section_label(section) for section in found["sections"]]
    if found["mode"] == "lines":
        return [
            "The document has no headings. Claim spec_ref by the document's own line"
            " number, counting every line including blank ones from 1, or by a range"
            " like 12-20. The document has %d lines." % len(document.get("text", "").splitlines())
        ]
    return ["Coverage is not computable for this document: %s." % found["reason"]]


def _header_lines(document, charter):
    header = [
        "Decompose the document into Steps: one logical pass over the whole of it.",
        "Document: %s" % document["path"],
        "sha256: %s" % document["sha256"],
        "Read all of it before drawing any Step boundary. after and contract_group relate parts"
        " of the document that may be far apart, and only the whole document shows them.",
    ]
    if charter:
        return header + [
            "Charter: %s (binding on every Step; every Worker receives it unchanged)" % charter
        ]
    return header


def _decisions_lines(decisions):
    text = decisions.get("text") if isinstance(decisions, dict) else decisions
    if not isinstance(text, str) or not text.strip():
        return []
    return [DECISIONS_HEADING, text.rstrip("\n")]


def _prior_lines(prior):
    if not prior:
        return []
    return [PRIOR_HEADING, json.dumps(list(prior), separators=(",", ":"))]


def _render(document, codebase, graph, charter, decisions, prior, contract):
    parts = [
        _header_lines(document, charter),
        _codebase_lines(codebase),
        _graph_lines(graph),
        _decisions_lines(decisions),
        _prior_lines(prior),
        _document_lines(document),
        _claimable_lines(document),
        ["Return contract:", contract],
    ]
    return "\n\n".join("\n".join(part) for part in parts if part) + "\n"


def render_prompt(document, codebase, graph=None, charter=None):
    return _render(document, codebase, graph, charter, None, None, CONTRACT)


def render_structure_prompt(
    document, codebase, graph=None, charter=None, decisions=None, prior=None
):
    contract = DELTA_CONTRACT if prior else STRUCTURE_CONTRACT
    return _render(document, codebase, graph, charter, decisions, prior, contract)


def build_argv(template, substitutions):
    words = shlex.split(template)
    if not words:
        raise ValueError("the decompose command template is empty")
    unmapped = sorted(
        placeholder
        for placeholder in PLACEHOLDERS
        if placeholder not in substitutions and any(placeholder in word for word in words)
    )
    if unmapped:
        raise ValueError(
            "the decompose command template uses %s but no value was supplied"
            % ", ".join(unmapped)
        )
    if not substitutions:
        return list(words)
    pattern = re.compile("|".join(re.escape(key) for key in substitutions))
    return [pattern.sub(lambda match: substitutions[match.group(0)], word) for word in words]


def _stamp(item, source):
    if not isinstance(item, dict):
        return item
    return {**item, "source": source}


def check_items(items, source, root=None, required=None):
    stamped = [_stamp(item, source) for item in items]
    checked = core.validate(stamped, root=root, required=required)
    return {"items": stamped, "errors": checked["errors"], "counts": checked["counts"]}


def _kill_tree(process):
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()


def _is_object(line):
    try:
        value = json.loads(line)
    except ValueError:
        return False
    return isinstance(value, dict)


def last_return(lines):
    for line in reversed(lines):
        if _is_object(line):
            return line
    return lines[-1] if lines else None


def _write_log(log, out, err):
    if not log:
        return
    parent = os.path.dirname(log)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(log, "wb") as handle:
        handle.write(b"--- stdout ---\n" + out + b"\n--- stderr ---\n" + err)


def spawn(argv, prompt, timeout, cwd=None, log=None):
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            start_new_session=True,
        )
    except OSError as failure:
        return {"exit": None, "line": None, "reason": "could not start %s: %s" % (argv[0], failure)}
    reason = None
    try:
        out, err = process.communicate(prompt.encode("utf-8"), timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        out, err = process.communicate()
        reason = "timeout after %s seconds" % timeout
    _write_log(log, out, err)
    lines = [line.strip() for line in out.decode("utf-8", "replace").splitlines() if line.strip()]
    return {
        "exit": process.returncode,
        "line": last_return(lines) if reason is None else None,
        "reason": reason,
    }


def spawn_many(jobs, timeout, concurrency, cwd=None):
    workers = concurrency if isinstance(concurrency, int) and concurrency > 0 else 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        pending = [
            pool.submit(spawn, job["argv"], job["prompt"], timeout, cwd=cwd, log=job.get("log"))
            for job in jobs
        ]
        return [future.result() for future in pending]


def spawn_until(jobs, timeout, concurrency, cwd, accepts, attempts=None):
    rounds = DISPATCH_ATTEMPTS if attempts is None else max(1, attempts)
    results = {}
    outstanding = tuple(range(len(jobs)))
    for _ in range(rounds):
        if not outstanding:
            break
        spawned = spawn_many([jobs[index] for index in outstanding], timeout, concurrency, cwd=cwd)
        results = {
            **results,
            **{
                index: {**one, "attempts": results.get(index, {}).get("attempts", 0) + 1}
                for index, one in zip(outstanding, spawned)
            },
        }
        outstanding = tuple(
            index for index in outstanding if not accepts(index, results[index])
        )
    return tuple(results[index] for index in range(len(jobs)))


def _clip(text, limit=200):
    return text if len(text) <= limit else text[:limit] + "..."


def _empty_lists(keys, errors):
    return {**dict.fromkeys(keys, []), "errors": list(errors)}


def _empty_return(errors):
    return _empty_lists(RETURN_KEYS, errors)


def _parse_lists(line, keys):
    if line is None:
        return _empty_lists(keys, ["the decompose Worker printed no return line"])
    try:
        value = json.loads(line)
    except ValueError:
        return _empty_lists(keys, ["the decompose Worker's last line is not JSON: " + _clip(line)])
    if not isinstance(value, dict):
        return _empty_lists(
            keys, ["the return must be a JSON object with keys %s" % ", ".join(keys)]
        )
    parsed = {}
    errors = []
    for key in keys:
        if key not in value:
            errors.append("the return is missing '%s'" % key)
            parsed = {**parsed, key: []}
        elif not isinstance(value[key], list):
            errors.append("'%s' must be a list, got %s" % (key, type(value[key]).__name__))
            parsed = {**parsed, key: []}
        else:
            parsed = {**parsed, key: value[key]}
    return {**parsed, "errors": errors}


def parse_return(line):
    return _parse_lists(line, RETURN_KEYS)


def _returned_a_structure(index, spawned):
    return not parse_return(spawned["line"])["errors"]


def parse_delta(line):
    return _parse_lists(line, DELTA_KEYS)


def _step_name(step):
    name = step.get("name") if isinstance(step, dict) else None
    return name if isinstance(name, str) and name else None


def _named_entries(delta, key, wanted):
    entries = delta.get(key) or []
    if wanted == "name":
        errors = [
            "%s[%d] must be a Step name, got %s" % (key, position, type(entry).__name__)
            for position, entry in enumerate(entries)
            if not isinstance(entry, str)
        ]
        names = tuple(entry for entry in entries if isinstance(entry, str))
        return names, errors
    errors = [
        "%s[%d] must be a whole Step object with a name" % (key, position)
        for position, entry in enumerate(entries)
        if _step_name(entry) is None
    ]
    steps = tuple(entry for entry in entries if _step_name(entry) is not None)
    return steps, errors


def _membership_errors(key, names, prior_names, present):
    verb = "carries" if key in ("change", "add") else "names"
    if present:
        return [
            "%s %s '%s', which is not in the structure being revised" % (key, verb, name)
            for name in names
            if name not in prior_names
        ]
    return [
        "%s %s '%s', which already exists in the structure being revised" % (key, verb, name)
        for name in names
        if name in prior_names
    ]


def _partition_errors(prior_names, mentioned):
    twice = [name for name in prior_names if mentioned.count(name) > 1]
    absent = [name for name in prior_names if mentioned.count(name) == 0]
    if not twice and not absent:
        return []
    parts = []
    if twice:
        parts.append("%s appears more than once" % ", ".join("'%s'" % n for n in twice))
    if absent:
        parts.append("%s does not appear" % ", ".join("'%s'" % n for n in absent))
    return ["keep, change and remove must name every prior Step exactly once: " + "; ".join(parts)]


def _duplicate_errors(names):
    seen = ()
    found = ()
    for name in names:
        if name in seen and name not in found:
            found = found + (name,)
        seen = seen + (name,)
    return ["duplicate name in the result: '%s'" % name for name in found]


def apply_delta(prior, delta):
    prior_names = tuple(_step_name(step) or "item #%d" % i for i, step in enumerate(prior))
    keep, errors = _named_entries(delta, "keep", "name")
    change, change_errors = _named_entries(delta, "change", "step")
    add, add_errors = _named_entries(delta, "add", "step")
    remove, remove_errors = _named_entries(delta, "remove", "name")
    change_names = tuple(_step_name(step) for step in change)
    add_names = tuple(_step_name(step) for step in add)
    errors = (
        errors
        + change_errors
        + add_errors
        + remove_errors
        + _membership_errors("keep", keep, prior_names, True)
        + _membership_errors("change", change_names, prior_names, True)
        + _membership_errors("remove", remove, prior_names, True)
        + _membership_errors("add", add_names, prior_names, False)
        + _partition_errors(prior_names, list(keep + change_names + remove))
    )
    if errors:
        return [], errors
    replacements = {_step_name(step): step for step in change}
    carried = [
        replacements[name] if name in replacements else step
        for name, step in zip(prior_names, prior)
        if name not in remove
    ]
    duplicates = _duplicate_errors([_step_name(s) for s in carried] + list(add_names))
    if duplicates:
        return [], duplicates
    return [copy.deepcopy(step) for step in carried + list(add)], []


def _own_assumptions(item):
    own = item.get("assumptions")
    if not isinstance(own, list):
        return ()
    return tuple(text for text in own if isinstance(text, str))


def fold_assumptions(items, assumptions):
    names = {
        item["name"]
        for item in items
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    extra = {}
    errors = []
    for position, entry in enumerate(assumptions):
        if not (
            isinstance(entry, dict)
            and isinstance(entry.get("step"), str)
            and isinstance(entry.get("text"), str)
        ):
            errors.append("assumptions[%d] must be a {step, text} object" % position)
            continue
        if entry["step"] not in names:
            errors.append(
                "assumptions[%d] names '%s', which is not a Step in this return"
                % (position, entry["step"])
            )
            continue
        extra = {**extra, entry["step"]: extra.get(entry["step"], ()) + (entry["text"],)}
    folded = []
    for item in items:
        if not isinstance(item, dict):
            folded.append(item)
            continue
        merged = tuple(dict.fromkeys(_own_assumptions(item) + extra.get(item.get("name"), ())))
        if merged:
            folded.append({**item, "assumptions": list(merged)})
        else:
            folded.append(item)
    return folded, errors


def _raise_vague(item):
    if isinstance(item, dict) and item.get("assumptions") and item.get("complexity") == "simple":
        return {**item, "complexity": "complex"}
    return item


def collect(parsed, source, root=None, required=None):
    folded, fold_errors = fold_assumptions(parsed["items"], parsed["assumptions"])
    lifted = [_raise_vague(item) for item in folded]
    raised = [after["name"] for before, after in zip(folded, lifted) if after is not before]
    checked = check_items(lifted, source, root, required)
    return {
        "items": checked["items"],
        "constraints": parsed["constraints"],
        "errors": parsed["errors"] + fold_errors + checked["errors"],
        "counts": checked["counts"],
        "raised": raised,
    }


def _spawn_errors(spawned):
    errors = []
    if spawned["reason"]:
        errors.append("the decompose Worker failed: " + spawned["reason"])
    if spawned["exit"] not in (None, 0):
        errors.append("the decompose Worker exited %d" % spawned["exit"])
    return errors


def _empty_errors(frozen, items):
    if items or not (frozen.get("text") or "").strip():
        return []
    return ["the decompose Worker returned no Steps for a document that has content"]


def _argv_for(frozen, template, prompt, model):
    substitutions = {PROMPT_PLACEHOLDER: prompt, DOCUMENT_PLACEHOLDER: frozen["path"]}
    if model is not None:
        substitutions = {**substitutions, MODEL_PLACEHOLDER: model}
    return build_argv(template, substitutions)


def _assemble(frozen, spawned, log, root, required=None, parse=parse_return):
    source = source_of(frozen)
    spawn_errors = _spawn_errors(spawned)
    parsed = (
        parse(spawned["line"])
        if spawned["line"] is not None or not spawn_errors
        else _empty_return([])
    )
    collected = collect(parsed, source, root, required)
    return {
        "source": source,
        "items": collected["items"],
        "constraints": collected["constraints"],
        "errors": spawn_errors + collected["errors"] + _empty_errors(frozen, collected["items"]),
        "counts": collected["counts"],
        "raised": collected["raised"],
        "coverage": coverage(frozen["text"], collected["items"]),
        "exit": spawned["exit"],
        "log": log,
    }


def decompose(
    document,
    template,
    root,
    timeout,
    model=None,
    graph=None,
    charter=None,
    cap=None,
    log=None,
):
    frozen = freeze(document)
    prompt = render_prompt(frozen, inventory(root, cap), graph, charter)
    argv = _argv_for(frozen, template, prompt, model)
    spawned = spawn(argv, prompt, timeout, cwd=root, log=log)
    return _assemble(frozen, spawned, log, root)


def _task_errors(items):
    named = [
        _step_label(item, index)
        for index, item in enumerate(items)
        if isinstance(item, dict) and item.get("task") not in (None, "")
    ]
    if not named:
        return []
    return [
        "%d Steps carry a task, which a structure return must not: %s"
        % (len(named), ", ".join(named))
    ]


def _delta_return(prior, line):
    parsed = parse_delta(line)
    carried = {key: parsed[key] for key in ("assumptions", "constraints")}
    if parsed["errors"]:
        return {"items": [], **carried, "errors": parsed["errors"]}
    task_errors = _task_errors(list(parsed["change"]) + list(parsed["add"]))
    items, errors = apply_delta(prior, parsed)
    if task_errors:
        return {"items": [], **carried, "errors": task_errors + errors}
    return {"items": items, **carried, "errors": errors}


def _assemble_structure(frozen, spawned, log, root, prior=None):
    if prior:
        return _assemble(
            frozen,
            spawned,
            log,
            root,
            core.STRUCTURE_ITEM_FIELDS,
            parse=lambda line: _delta_return(prior, line),
        )
    assembled = _assemble(frozen, spawned, log, root, core.STRUCTURE_ITEM_FIELDS)
    return {**assembled, "errors": assembled["errors"] + _task_errors(assembled["items"])}


def _sample_logs(log, count):
    if log is None or count == 1:
        return [log] * count
    stem, extension = os.path.splitext(log)
    return ["%s-%d%s" % (stem, number, extension) for number in range(1, count + 1)]


def sample_structures(
    samples,
    document,
    template,
    root,
    timeout,
    model=None,
    graph=None,
    charter=None,
    cap=None,
    log=None,
    decisions=None,
    prior=None,
):
    count = samples if isinstance(samples, int) and samples > 0 else 1
    frozen = freeze(document)
    prompt = render_structure_prompt(
        frozen, inventory(root, cap), graph, charter, decisions, prior
    )
    argv = _argv_for(frozen, template, prompt, model)
    logs = _sample_logs(log, count)
    jobs = [{"argv": argv, "prompt": prompt, "log": sample_log} for sample_log in logs]
    spawned = spawn_until(jobs, timeout, count, root, _returned_a_structure)
    return [
        _assemble_structure(frozen, one, sample_log, root, prior)
        for one, sample_log in zip(spawned, logs)
    ]


def structure(
    document,
    template,
    root,
    timeout,
    model=None,
    graph=None,
    charter=None,
    cap=None,
    log=None,
    decisions=None,
    prior=None,
):
    return sample_structures(
        1,
        document,
        template,
        root,
        timeout,
        model=model,
        graph=graph,
        charter=charter,
        cap=cap,
        log=log,
        decisions=decisions,
        prior=prior,
    )[0]


def _scorable(result):
    return [item for item in result.get("items") or [] if isinstance(item, dict)]


def _rank_key(result, index):
    scored = shape.scalars(_scorable(result))
    return (
        1 if result.get("errors") else 0,
        1 if scored["lane_cycles"] else 0,
        1 if shape.manifest_gaps(_scorable(result)) else 0,
        -scored["parallelism"],
        scored["fused_without_overlap"],
        -scored["msps_per_step"],
        scored["largest_lane"],
        index,
    )


def rank(results):
    return sorted(range(len(results)), key=lambda index: _rank_key(results[index], index))


def _owner_counts(items):
    counts = {}
    for item in items:
        files = item.get("files")
        paths = {path for path in files if isinstance(path, str)} if isinstance(files, list) else ()
        for path in paths:
            counts = {**counts, path: counts.get(path, 0) + 1}
    return counts


def _per_sample(values):
    return ", ".join("sample %d has %d" % (number, value) for number, value in enumerate(values, 1))


def disagreements(results):
    if len(results) < 2:
        return []
    structures = [_scorable(result) for result in results]
    owners = [_owner_counts(items) for items in structures]
    paths = sorted(set().union(*owners))
    found = []
    for path in paths:
        counts = [owned.get(path, 0) for owned in owners]
        if len(set(counts)) > 1:
            found.append(
                "The samples do not agree on how many Steps own %s: %s." % (path, _per_sample(counts))
            )
    scored = [shape.scalars(items) for items in structures]
    for key, label in (("msps", "the number of MSPs"), ("parallelism", "the parallelism")):
        values = [one[key] for one in scored]
        if len(set(values)) > 1:
            found.append("The samples do not agree on %s: %s." % (label, _per_sample(values)))
    return found


def report(result):
    source = result.get("source") or {}
    counts = result.get("counts") or {}
    lines = [
        "%d Steps decomposed from %s (sha256 %s)"
        % (len(result.get("items", [])), source.get("path"), source.get("sha256")),
        "%d global constraints extracted" % len(result.get("constraints", [])),
        "%d Steps carry assumptions" % counts.get("assumptions", 0),
        "%d Steps declare no acceptance" % counts.get("no_acceptance", 0),
        "%d write-set paths do not exist yet" % counts.get("missing_paths", 0),
    ]
    raised = result.get("raised") or []
    if raised:
        lines.append(
            "%d Steps raised from simple for carrying assumptions: %s"
            % (len(raised), ", ".join(raised))
        )
    covered = result.get("coverage") or {}
    if covered.get("mode") == "none":
        lines.append("coverage not computable: %s" % covered.get("reason"))
    errors = result.get("errors") or []
    if errors:
        lines.append("%d contract errors:" % len(errors))
        lines.extend(errors)
    return lines
