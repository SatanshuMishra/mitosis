import core
import referee

UNWRITTEN_LINE = (
    "%s, named by Step %s, is written by no Step or check-author, is not locked and is not on the base"
)


def _normalised(paths):
    return frozenset(referee.normalised(path) for path in paths)


def _written(items):
    return _normalised(path for item in items for path in core._files(item))


def _with_own_tests(item, locked):
    files = list(item.get("files") or [])
    held = _normalised(files) | locked
    added = tuple(
        dict.fromkeys(path for path in core._acceptance_files(item) if referee.normalised(path) not in held)
    )
    return {**item, "files": files + list(added)}


def owned(items, locked=()):
    held = _normalised(locked)
    return [_with_own_tests(item, held) for item in items]


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


FOLDED_LINE = (
    "the check-author Step %s is folded: each Step whose acceptance names its files writes them, "
    "so no lock stage runs"
)

DROPPED_LINE = "%s, written by the check-author Step %s, is named by no Step's acceptance and is dropped"


def _with_authored_tests(item, authored):
    files = list(item.get("files") or [])
    held = _normalised(files)
    added = tuple(
        dict.fromkeys(
            path
            for path in core._acceptance_files(item)
            if referee.normalised(path) in authored and referee.normalised(path) not in held
        )
    )
    return {**item, "files": files + list(added)} if added else item


def _dropped(authors, kept):
    named = _normalised(path for item in kept for path in core._acceptance_files(item))
    return tuple(
        DROPPED_LINE % (path, author.get("name"))
        for author in authors
        for path in dict.fromkeys(core._files(author))
        if referee.normalised(path) not in named
    )


def folded(items):
    authors = core.check_author_steps(items)
    if not authors:
        return list(items), ()
    authored = _written(authors)
    kept = [_with_authored_tests(item, authored) for item in core.without_check_author(items)]
    notices = tuple(FOLDED_LINE % author.get("name") for author in authors)
    return kept, notices + _dropped(authors, kept)
