import json
import os

import decompose

RETURN_KEYS = ("name", "task")

STEP_PLACEHOLDER = "{step}"

RETURN_SHAPE = '{"name":"<step>","task":"<the brief>"}'

BRIEF_CONTRACT = "\n".join(
    (
        "When finished, print one line of JSON and nothing after it:",
        RETURN_SHAPE,
        "The object carries exactly the keys " + ", ".join('"%s"' % key for key in RETURN_KEYS) + ".",
        "  name: the Step you were asked to brief, exactly as named above; a return naming any"
        " other Step is rejected",
        "  task: the Step's entire brief as one string; the Worker that builds it receives"
        " nothing else, and an empty task is rejected",
        "The last line of your output that parses as a JSON object is taken as the return, so"
        " narration or a code fence around it is tolerated.",
    )
)

COMPACT_FIELDS = ("name", "files", "after", "contract_group", "type", "complexity", "acceptance")

SUBJECT_OMITTED = ("task", "source")

STRUCTURE_HEADING = (
    "The approved structure follows, one Step per line as JSON. Every Step is briefed against"
    " this same structure, so name any interface a sibling Step builds or consumes by its"
    " signature; that is how the Steps meet."
)

SUBJECT_LEAD = (
    "The Step to brief is %s. It follows in full as JSON; the task you return is its entire"
    " brief, and the Worker that builds it receives nothing else."
)

READ_SET_LEAD = "Read-set for this Step, context only, never edit: "

CHEAP_COMPLEXITY = "simple"


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _briefed(item):
    return isinstance(item, dict) and bool(_text(item.get("task")))


def pending(items):
    return [item for item in items if not _briefed(item)]


def tier_for(item):
    return "cheap" if item.get("complexity") == CHEAP_COMPLEXITY else "top"


def _dumps(value):
    return json.dumps(value, separators=(",", ":"))


def compact(item):
    return {key: item[key] for key in COMPACT_FIELDS if key in item}


def _header_lines(document, charter):
    header = [
        "Write the brief for one Step of an approved structure: the task prose that the Worker"
        " building it receives as its entire instruction.",
    ]
    if isinstance(document, dict):
        header.append("Document: %s" % document.get("path"))
        header.append("sha256: %s" % document.get("sha256"))
    if charter:
        header.append(
            "Charter: %s (binding on every Step; the Worker receives it unchanged, so the brief"
            " must not restate it)" % charter
        )
    return header


def _decisions_lines(decisions):
    text = decisions.get("text") if isinstance(decisions, dict) else decisions
    if not _text(text):
        return []
    return [decompose.DECISIONS_HEADING, text.rstrip("\n")]


def _structure_lines(structure):
    steps = [compact(item) for item in structure or () if isinstance(item, dict)]
    if not steps:
        return []
    return [STRUCTURE_HEADING] + [_dumps(step) for step in steps]


def _subject_lines(step):
    subject = {key: value for key, value in step.items() if key not in SUBJECT_OMITTED}
    return [SUBJECT_LEAD % step.get("name"), _dumps(subject)]


def _norm(path):
    return os.path.normpath(path).replace(os.sep, "/")


def _write_set(step):
    files = step.get("files")
    if not isinstance(files, list):
        return frozenset()
    return frozenset(_norm(path) for path in files if isinstance(path, str))


def _read_set(step, pack):
    paths = pack.get("paths") if isinstance(pack, dict) else None
    if not isinstance(paths, list):
        return []
    own = _write_set(step)
    return list(
        dict.fromkeys(path for path in paths if isinstance(path, str) and _norm(path) not in own)
    )


def _read_lines(step, pack):
    read = _read_set(step, pack)
    if not read:
        return []
    overflow = pack.get("overflow") or 0
    line = READ_SET_LEAD + ", ".join(read)
    if overflow > 0:
        line += " (%d more not shown)" % overflow
    return [line]


def _document_lines(document):
    if not isinstance(document, dict):
        return []
    text = document.get("text")
    if text is None:
        return ["The document is not decodable as UTF-8 text; open it from its path."]
    return [
        "The document follows, between the markers.",
        decompose.DOCUMENT_OPEN,
        text.rstrip("\n"),
        decompose.DOCUMENT_CLOSE,
    ]


def render_brief_prompt(document, structure, step, pack=None, charter=None, decisions=None):
    parts = [
        _header_lines(document, charter),
        _decisions_lines(decisions),
        _structure_lines(structure),
        _subject_lines(step),
        _read_lines(step, pack),
        _document_lines(document),
        ["Return contract:", BRIEF_CONTRACT],
    ]
    return "\n\n".join("\n".join(part) for part in parts if part) + "\n"


def _clip(text, limit=200):
    return text if len(text) <= limit else text[:limit] + "..."


def _parsed(name, task, errors):
    return {"name": name, "task": task, "errors": list(errors)}


def parse_brief(line, expected):
    if line is None:
        return _parsed(None, None, ["the brief Worker for %s printed no return line" % expected])
    try:
        value = json.loads(line)
    except ValueError:
        value = None
    if not isinstance(value, dict):
        return _parsed(
            None,
            None,
            [
                "the brief Worker for %s: the last line is not a JSON object: %s"
                % (expected, _clip(line))
            ],
        )
    name = value.get("name")
    task = value.get("task")
    errors = []
    if name != expected:
        errors.append("the return names %r but the Step briefed was %r" % (name, expected))
    if not _text(task):
        errors.append("the return for %s carries no task" % expected)
    return _parsed(name, task, errors)


def _frozen(document):
    if document is None or isinstance(document, dict):
        return document
    return decompose.freeze(document)


def _argv(template, prompt, name, frozen, model):
    substitutions = {decompose.PROMPT_PLACEHOLDER: prompt, STEP_PLACEHOLDER: name}
    if frozen is not None:
        substitutions = {**substitutions, decompose.DOCUMENT_PLACEHOLDER: frozen["path"]}
    if model is not None:
        substitutions = {**substitutions, decompose.MODEL_PLACEHOLDER: model}
    return decompose.build_argv(template, substitutions)


def _job(step, template, frozen, structure, models, packs, charter, decisions, log_dir):
    name = step["name"]
    prompt = render_brief_prompt(
        frozen,
        structure,
        step,
        pack=(packs or {}).get(name),
        charter=charter,
        decisions=decisions,
    )
    model = (models or {}).get(tier_for(step))
    return {
        "argv": _argv(template, prompt, name, frozen, model),
        "prompt": prompt,
        "log": os.path.join(log_dir, name + ".log") if log_dir else None,
    }


def _spawn_errors(name, spawned):
    errors = []
    if spawned["reason"]:
        errors.append("the brief Worker for %s failed: %s" % (name, spawned["reason"]))
    if spawned["exit"] not in (None, 0):
        errors.append("the brief Worker for %s exited %d" % (name, spawned["exit"]))
    return errors


def _outcome(name, spawned):
    spawn_errors = _spawn_errors(name, spawned)
    if spawned["line"] is None and spawn_errors:
        return _parsed(None, None, spawn_errors)
    parsed = parse_brief(spawned["line"], name)
    return {**parsed, "errors": spawn_errors + parsed["errors"]}


def _rebuild(item, outcomes):
    outcome = outcomes.get(item.get("name")) if isinstance(item, dict) else None
    if outcome is None or outcome["errors"]:
        return {**item} if isinstance(item, dict) else item
    return {**item, "task": outcome["task"]}


def write(
    items,
    template,
    root,
    timeout,
    models=None,
    concurrency=1,
    log_dir=None,
    document=None,
    structure=None,
    packs=None,
    charter=None,
    decisions=None,
):
    frozen = _frozen(document)
    whole = items if structure is None else structure
    todo = pending(items)
    jobs = [
        _job(step, template, frozen, whole, models, packs, charter, decisions, log_dir)
        for step in todo
    ]
    spawned = decompose.spawn_until(
        jobs,
        timeout,
        concurrency,
        root,
        lambda index, one: not _outcome(todo[index]["name"], one)["errors"],
    )
    outcomes = {step["name"]: _outcome(step["name"], one) for step, one in zip(todo, spawned)}
    return {
        "items": [_rebuild(item, outcomes) for item in items],
        "written": [step["name"] for step in todo if not outcomes[step["name"]]["errors"]],
        "reused": [item["name"] for item in items if _briefed(item)],
        "retried": [step["name"] for step, one in zip(todo, spawned) if one.get("attempts", 1) > 1],
        "errors": [error for step in todo for error in outcomes[step["name"]]["errors"]],
    }
