import core
import referee

TASK_LEAD = (
    "Write the locked checks for MSP %s before any of its Steps is built. Write each test below "
    "so it checks only the observable named after it, at a public surface of the work (its command "
    "line, an exported function or a file it writes), and fails on the current code:"
)

UNWRITTEN_LINE = (
    "%s, named by Step %s, is written by no Step or check-author, is not locked and is not on the base"
)

CHECK_AUTHOR_PREFIX = "check-author-"


def _entries(item):
    acceptance = item.get("acceptance")
    if not isinstance(acceptance, list):
        return ()
    return tuple(
        entry for entry in acceptance if isinstance(entry, dict) and isinstance(entry.get("file"), str)
    )


def _written(items):
    return frozenset(referee.normalised(path) for item in items for path in core._files(item))


def _normalised(paths):
    return frozenset(referee.normalised(path) for path in paths)


def _observable(item):
    return item.get("observable") or item.get("task") or item["name"]


def _msp_tests(items, msp, excluded, held):
    return tuple(
        (entry["file"], entry.get("test"), items[i]["name"], _observable(items[i]))
        for i in msp
        for entry in _entries(items[i])
        if referee.normalised(entry["file"]) not in excluded and not held(entry["file"])
    )


def _author(label, tests, source):
    lines = "".join(
        "\n- %s::%s for Step %s: %s" % (path, test, step, observable)
        for path, test, step, observable in tests
    )
    return {
        "name": CHECK_AUTHOR_PREFIX + label,
        "role": core.CHECK_AUTHOR,
        "msp": label,
        "task": TASK_LEAD % label + lines,
        "files": list(dict.fromkeys(path for path, _, _, _ in tests)),
        "source": source,
        "acceptance": [],
    }


def _ordered_step(item, label, author):
    after = list(item.get("after") or [])
    unlabelled = item.get("msp") in (None, "")
    return {
        **item,
        **({"msp": label} if unlabelled else {}),
        "after": after if author in after else after + [author],
    }


def check_authors(items, held=lambda path: False, locked=()):
    if any(item.get("role") == core.CHECK_AUTHOR for item in items):
        return [dict(item) for item in items]
    base = _written(items) | _normalised(locked)
    claimed = frozenset()
    authors = ()
    changed = {}
    for msp in core.msp_items(items):
        tests = _msp_tests(items, msp, base | claimed, held)
        claimed = claimed | _normalised(path for path, _, _, _ in tests)
        if not tests:
            continue
        label = core._msp_label(items, msp)
        author = _author(label, tests, items[msp[0]].get("source"))
        authors = authors + (author,)
        changed = {**changed, **{i: _ordered_step(items[i], label, author["name"]) for i in msp}}
    return list(authors) + [changed.get(i, dict(item)) for i, item in enumerate(items)]


def unwritten(plan, held):
    items = plan.get("items") or ()
    excluded = (
        _written(items)
        | _normalised(path for entry in plan.get("check_authors") or () for path in entry.get("slice") or ())
        | _normalised(plan.get("locked") or ())
    )
    found = (
        (path, item["name"])
        for item in items
        for path in core._acceptance_files(item)
        if referee.normalised(path) not in excluded and not held(path)
    )
    return tuple(UNWRITTEN_LINE % pair for pair in dict.fromkeys(found))
