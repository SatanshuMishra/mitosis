import os
import re

FLAGS = re.ASCII | re.MULTILINE

JS_EXTS = ("js", "jsx", "ts", "tsx", "mjs", "cjs")

SHELL_EXTS = ("sh", "bash")

NUMBER = r"(?<![\w.])\d+(?![\w.])"

ARITHMETIC = r"\$\(\(([^)]*)\)\)"


def _text(replacement):
    return lambda match: replacement


def _shifted(delta):
    return lambda match: str(int(match.group(0)) + delta)


def _arithmetic(sign):
    return lambda match: "$((%s%s1))" % (match.group(1), sign)


def _operator(name, pattern, replacement, extensions=None):
    return (name, re.compile(pattern, FLAGS), replacement, extensions)


OPERATORS = (
    _operator("=== -> !==", r"===", _text("!==")),
    _operator("!== -> ===", r"!==", _text("===")),
    _operator("== -> !=", r"(?<![=!<>])==(?!=)", _text("!=")),
    _operator("!= -> ==", r"(?<![!<>=])!=(?!=)", _text("==")),
    _operator("<= -> <", r"<=", _text("<")),
    _operator("< -> <=", r"(?<![<=!-])<(?![=<])", _text("<=")),
    _operator(">= -> >", r">=", _text(">")),
    _operator("> -> >=", r"(?<![>=-])>(?![=>])", _text(">=")),
    _operator("&& -> ||", r"&&", _text("||")),
    _operator("|| -> &&", r"\|\|", _text("&&")),
    _operator("and -> or", r"\band\b", _text("or")),
    _operator("or -> and", r"\bor\b", _text("and")),
    _operator("+ -> -", r"(?<![+\w\"'`])\s\+\s(?![+=])", _text(" - ")),
    _operator("- -> +", r"(?<![-\w\"'`])\s-\s(?![-=>])", _text(" + ")),
    _operator("/ -> *", r"(?<![/*])\s/\s(?![/*=])", _text(" * ")),
    _operator("* -> /", r"(?<![/*])\s\*\s(?![/*=])", _text(" / ")),
    _operator("true -> false", r"\btrue\b", _text("false")),
    _operator("false -> true", r"\bfalse\b", _text("true")),
    _operator("True -> False", r"\bTrue\b", _text("False")),
    _operator("False -> True", r"\bFalse\b", _text("True")),
    _operator("number +1", NUMBER, _shifted(1)),
    _operator("number -1", NUMBER, _shifted(-1)),
    _operator(
        "return -> None", r"(?<=\breturn)[ \t]+(?!None\b)[^#\n]+$", _text(" None"), ("py",)
    ),
    _operator(
        "return -> undefined",
        r"(?<=\breturn)[ \t]+(?!undefined\b)[^;\n]+;?$",
        _text(" undefined;"),
        JS_EXTS,
    ),
    _operator("$((x)) -> $((x+1))", ARITHMETIC, _arithmetic("+"), SHELL_EXTS),
    _operator("$((x)) -> $((x-1))", ARITHMETIC, _arithmetic("-"), SHELL_EXTS),
)

QUOTED = re.compile(r"([\"'`])((?:\\.|(?!\1).)*)\1")

NON_SPACE = re.compile(r"\S")

COMMENT_LINE = re.compile(r"^\s*(//|#|\*|/\*)")

CODE_EXTS = frozenset(
    (
        "js", "jsx", "ts", "tsx", "mjs", "cjs", "py", "rb", "go", "rs", "java", "kt", "kts",
        "cs", "php", "swift", "scala", "c", "h", "cc", "cpp", "hpp", "m", "mm", "sh", "bash",
        "zsh", "pl", "lua", "ex", "exs", "dart", "groovy", "vue", "svelte",
    )
)

FILE_HEADER = re.compile(r"^\+\+\+ (?:b/)?(.*)$")

HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

DROP_ADDED = "drop added lines"


def mask_strings(line):
    return QUOTED.sub(
        lambda match: match.group(1) + NON_SPACE.sub(" ", match.group(2)) + match.group(1), line
    )


def _extension(path):
    return os.path.splitext(path)[1][1:].lower()


def mutants_for_line(line, ext):
    if COMMENT_LINE.search(line):
        return []
    masked = mask_strings(line)
    lowered = (ext or "").lower()
    found = ()
    for name, pattern, replacement, extensions in OPERATORS:
        if extensions is not None and lowered not in extensions:
            continue
        match = pattern.search(masked)
        if match is None:
            continue
        after = line[: match.start()] + replacement(match) + line[match.end():]
        if after != line:
            found = found + ({"op": name, "before": line, "after": after, "col": match.start()},)
    return list(found)


def is_mutable(path):
    return _extension(path) in CODE_EXTS


def added_lines(diff_text):
    added = {}
    current = None
    previous = ""
    for line in diff_text.splitlines():
        header = FILE_HEADER.match(line) if previous.startswith("--- ") else None
        previous = line
        if header is not None:
            current = None if header.group(1) == "/dev/null" else header.group(1)
            continue
        hunk = HUNK_HEADER.match(line)
        if hunk is None or current is None:
            continue
        start = int(hunk.group(1))
        count = 1 if hunk.group(2) is None else int(hunk.group(2))
        added = {**added, current: added.get(current, ()) + tuple(range(start, start + count))}
    return {path: list(lines) for path, lines in added.items() if lines}


def _lines_of(text):
    return text.split("\n")


def compute(added, read):
    found = ()
    for path in sorted(added):
        if not is_mutable(path):
            continue
        lines = _lines_of(read(path))
        for number in sorted(set(added[path])):
            if number < 1 or number > len(lines):
                continue
            found = found + tuple(
                {
                    "file": path,
                    "line": number,
                    "op": mutant["op"],
                    "before": mutant["before"],
                    "after": mutant["after"],
                }
                for mutant in mutants_for_line(lines[number - 1], _extension(path))
            )
    return list(found)


def select(mutants, cap):
    queues = {}
    for mutant in mutants:
        queues = {**queues, mutant["file"]: queues.get(mutant["file"], ()) + (mutant,)}
    chosen = ()
    depth = 0
    while len(chosen) < cap and any(depth < len(queue) for queue in queues.values()):
        for path in sorted(queues):
            if len(chosen) >= cap:
                break
            if depth < len(queues[path]):
                chosen = chosen + (queues[path][depth],)
        depth = depth + 1
    return list(chosen)


def apply(text, mutant):
    if "text" in mutant:
        return mutant["text"]
    lines = _lines_of(text)
    index = mutant["line"] - 1
    if index < 0 or index >= len(lines) or lines[index] != mutant["before"]:
        return None
    return "\n".join(lines[:index] + [mutant["after"]] + lines[index + 1:])


def fallback(added, read):
    found = ()
    for path in sorted(added):
        numbers = sorted(set(added[path]))
        if not numbers:
            continue
        dropped = set(numbers)
        kept = [
            line for index, line in enumerate(read(path).splitlines(True), 1) if index not in dropped
        ]
        found = found + (
            {"file": path, "line": numbers[0], "op": DROP_ADDED, "text": "".join(kept)},
        )
    return list(found)
