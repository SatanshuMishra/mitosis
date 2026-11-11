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

SHARED_LINE = (
    "%s, written by the check-author Step %s, is named by no Step's acceptance, "
    "so every Step that takes one of that Step's test files writes it too"
)


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


def _takes(item, author):
    authored = _written((author,))
    return any(referee.normalised(path) in authored for path in core._acceptance_files(item))


def _helpers(author, kept):
    spoken_for = _normalised(path for item in kept for path in core._acceptance_files(item)) | _written(kept)
    return tuple(
        path for path in dict.fromkeys(core._files(author)) if referee.normalised(path) not in spoken_for
    )


def _with_helpers(item, helpers):
    files = list(item.get("files") or [])
    held = _normalised(files)
    added = [path for path in dict.fromkeys(helpers) if referee.normalised(path) not in held]
    return {**item, "files": files + added} if added else item


def _takers(author, kept):
    return frozenset(index for index, item in enumerate(kept) if _takes(item, author))


def folded(items):
    authors = core.check_author_steps(items)
    if not authors:
        return list(items), ()
    authored = _written(authors)
    taking = [_with_authored_tests(item, authored) for item in core.without_check_author(items)]
    shares = [(author, _helpers(author, taking), _takers(author, taking)) for author in authors]
    kept = [
        _with_helpers(item, [path for _, helpers, takers in shares if index in takers for path in helpers])
        for index, item in enumerate(taking)
    ]
    folds = tuple(FOLDED_LINE % author.get("name") for author in authors)
    helper_lines = tuple(
        (SHARED_LINE if takers else DROPPED_LINE) % (path, author.get("name"))
        for author, helpers, takers in shares
        for path in helpers
    )
    return kept, folds + helper_lines
