import hashlib
import json
import os
import re
import shlex
import signal
import subprocess

import core

RETURN_KEYS = ("items", "assumptions", "constraints")

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

CONTRACT = "\n".join(
    (
        "When finished, print one line of JSON and nothing after it:",
        RETURN_SHAPE,
        "Every entry of \"items\" is one Step, and every Step must carry each of these fields: "
        + ", ".join(core.REQUIRED_ITEM_FIELDS)
        + ".",
        "  name: kebab-case, unique within this return",
        "  task: the Step's entire brief; the Worker that builds it receives nothing else",
        "  files: the write-set, a non-empty list of paths relative to the codebase root",
        "  source: null; the document path and hash are stamped in for you",
        '  acceptance: a list of {"%s": "<test file>", "%s": "<test identifier>"} objects,'
        % core.ACCEPTANCE_KEYS
        + " never prose; an empty list declares that the Step proves nothing mechanically",
        "A Step may also carry: " + ", ".join(OPTIONAL_ITEM_FIELDS) + ".",
        "  after: names of Steps that must be built first; these edges join parts of the document"
        " that may be far apart, and they can only be seen from the whole of it",
        "  contract_group: one shared id for the halves of one interface",
        "  type: one of " + ", ".join(core.STEP_TYPES),
        "  complexity: one of " + ", ".join(core.COMPLEXITY_VALUES),
        "  file_notes: {path: what changes there}",
        "  msp: a tag forcing Steps to ship as one pull request",
        "  spec_ref: the sections this Step came from, each as its number or its exact heading"
        " text, or as a line number or a range like 12-20 when the document has no headings",
        "  assumptions: the readings you chose where the document was underdetermined",
        '"assumptions" at the top level lists every such reading with the "step" it belongs to;'
        " a Step with any assumption is never rated simple",
        '"constraints" lists the document\'s global statements that belong to no single Step,'
        " one string each; return an empty list when there are none",
    )
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


def sections(text):
    if text is None:
        return {
            "mode": "none",
            "sections": [],
            "reason": "the document is not decodable as UTF-8 text",
        }
    lines = text.splitlines()
    found = _headings(lines)
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
            "The document has no headings; claim spec_ref by line number or range"
            " across its %d non-blank lines." % len(found["sections"])
        ]
    return ["Coverage is not computable for this document: %s." % found["reason"]]


def render_prompt(document, codebase, graph=None, charter=None):
    header = [
        "Decompose the document into Steps: one logical pass over the whole of it.",
        "Document: %s" % document["path"],
        "sha256: %s" % document["sha256"],
        "Read all of it before drawing any Step boundary. after and contract_group relate parts"
        " of the document that may be far apart, and only the whole document shows them.",
    ]
    if charter:
        header.append(
            "Charter: %s (binding on every Step; every Worker receives it unchanged)" % charter
        )
    parts = [
        header,
        _codebase_lines(codebase),
        _graph_lines(graph),
        _document_lines(document),
        _claimable_lines(document),
        ["Return contract:", CONTRACT],
    ]
    return "\n\n".join("\n".join(part) for part in parts if part) + "\n"


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


def check_items(items, source, root=None):
    stamped = [_stamp(item, source) for item in items]
    checked = core.validate(stamped, root=root)
    return {"items": stamped, "errors": checked["errors"], "counts": checked["counts"]}


def _kill_tree(process):
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()


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
    lines = [line for line in out.decode("utf-8", "replace").splitlines() if line.strip()]
    return {
        "exit": process.returncode,
        "line": lines[-1] if lines and reason is None else None,
        "reason": reason,
    }


def _clip(text, limit=200):
    return text if len(text) <= limit else text[:limit] + "..."


def _empty_return(errors):
    return {**dict.fromkeys(RETURN_KEYS, []), "errors": list(errors)}


def parse_return(line):
    if line is None:
        return _empty_return(["the decompose Worker printed no return line"])
    try:
        value = json.loads(line)
    except ValueError:
        return _empty_return(["the decompose Worker's last line is not JSON: " + _clip(line)])
    if not isinstance(value, dict):
        return _empty_return(
            ["the return must be a JSON object with keys %s" % ", ".join(RETURN_KEYS)]
        )
    parsed = {}
    errors = []
    for key in RETURN_KEYS:
        if key not in value:
            errors.append("the return is missing '%s'" % key)
            parsed = {**parsed, key: []}
        elif not isinstance(value[key], list):
            errors.append("'%s' must be a list, got %s" % (key, type(value[key]).__name__))
            parsed = {**parsed, key: []}
        else:
            parsed = {**parsed, key: value[key]}
    return {**parsed, "errors": errors}


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


def collect(parsed, source, root=None):
    folded, fold_errors = fold_assumptions(parsed["items"], parsed["assumptions"])
    lifted = [_raise_vague(item) for item in folded]
    raised = [after["name"] for before, after in zip(folded, lifted) if after is not before]
    checked = check_items(lifted, source, root)
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
    source = source_of(frozen)
    prompt = render_prompt(frozen, inventory(root, cap), graph, charter)
    substitutions = {PROMPT_PLACEHOLDER: prompt, DOCUMENT_PLACEHOLDER: frozen["path"]}
    if model is not None:
        substitutions = {**substitutions, MODEL_PLACEHOLDER: model}
    argv = build_argv(template, substitutions)
    spawned = spawn(argv, prompt, timeout, cwd=root, log=log)
    spawn_errors = _spawn_errors(spawned)
    parsed = (
        parse_return(spawned["line"])
        if spawned["line"] is not None or not spawn_errors
        else _empty_return([])
    )
    collected = collect(parsed, source, root)
    return {
        "source": source,
        "items": collected["items"],
        "constraints": collected["constraints"],
        "errors": spawn_errors + collected["errors"],
        "counts": collected["counts"],
        "raised": collected["raised"],
        "coverage": coverage(frozen["text"], collected["items"]),
        "exit": spawned["exit"],
        "log": log,
    }


def _coverage_lines(covered):
    if not covered:
        return []
    if covered["mode"] == "none":
        return ["coverage not computable: %s" % covered["reason"]]
    lines = [
        "coverage by %s: %d of %d sections unclaimed"
        % (covered["mode"], len(covered["uncovered"]), len(covered["sections"]))
    ]
    lines.extend("  unclaimed: " + _section_label(section) for section in covered["uncovered"])
    if covered["unmatched_claims"]:
        lines.append(
            "spec_ref claims matching no section: " + "; ".join(covered["unmatched_claims"])
        )
    return lines


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
    lines.extend(_coverage_lines(result.get("coverage")))
    errors = result.get("errors") or []
    if errors:
        lines.append("%d contract errors:" % len(errors))
        lines.extend(errors)
    return lines
