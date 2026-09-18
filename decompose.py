import hashlib
import os
import re
import shlex

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
        '  acceptance: a list of {"%s": "<test file>", "%s": "<test identifier>"} objects, never prose;'
        % core.ACCEPTANCE_KEYS
        + " an empty list declares that the Step proves nothing mechanically",
        "A Step may also carry: " + ", ".join(OPTIONAL_ITEM_FIELDS) + ".",
        "  after: names of Steps that must be built first; these edges join parts of the document"
        " that may be far apart, and they can only be seen from the whole of it",
        "  contract_group: one shared id for the halves of one interface",
        "  type: one of " + ", ".join(core.STEP_TYPES),
        "  complexity: one of " + ", ".join(core.COMPLEXITY_VALUES),
        "  file_notes: {path: what changes there}",
        "  msp: a tag forcing Steps to ship as one pull request",
        "  spec_ref: the sections this Step came from, each as its number or its exact heading text,"
        " or as a line number or a range like 12-20 when the document has no headings",
        "  assumptions: the readings you chose where the document was underdetermined",
        '"assumptions" at the top level lists every such reading with the "step" it belongs to;'
        " a Step with any assumption is never rated simple",
        '"constraints" lists the document\'s global statements that belong to no single Step,'
        " one string each; return an empty list when there are none",
    )
)


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
    shown = "%d shown, %d more not shown" % (len(paths), overflow) if overflow else "%d" % len(paths)
    lines.append("Files in the codebase (%s):" % shown)
    lines.extend("  " + path for path in paths)
    return lines


def _document_lines(document):
    text = document.get("text")
    if text is None:
        return [
            "The document is not decodable as UTF-8 text; open it from its path.",
        ]
    return ["The document follows, between the markers.", DOCUMENT_OPEN, text.rstrip("\n"), DOCUMENT_CLOSE]


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
    parts = [header, _codebase_lines(codebase), _graph_lines(graph), _document_lines(document)]
    parts.append(["Return contract:", CONTRACT])
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
