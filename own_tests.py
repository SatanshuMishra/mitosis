import collections

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

KEPT_LINE = (
    "%s, written by the check-author Step %s, is named by no Step's acceptance "
    "and stays with Step %s, which already writes it"
)

REFEREE_LINE = (
    "%s, written by the check-author Step %s, is named by no Step's acceptance and is dropped, "
    "because it %s and no Step may write the referee"
)

WAITING_LINE = (
    "%s, written by the check-author Step %s, is named by no Step's acceptance and is dropped, "
    "because the Steps that would write it would wait on each other"
)

Helper = collections.namedtuple("Helper", ("path", "author", "takers", "writer", "barred"))


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


def _takers(author, kept):
    return frozenset(index for index, item in enumerate(kept) if _takes(item, author))


def _first_spellings(paths):
    listed = tuple(paths)
    first = {referee.normalised(path): path for path in reversed(listed)}
    return tuple(first[key] for key in dict.fromkeys(referee.normalised(path) for path in listed))


def _writer(path, kept):
    target = referee.normalised(path)
    return next((item.get("name") for item in kept if target in _written((item,))), None)


def _helpers(author, kept, locked):
    named = _normalised(path for item in kept for path in core._acceptance_files(item))
    takers = _takers(author, kept)
    return tuple(
        Helper(path, author.get("name"), takers, _writer(path, kept), referee.referee_reason(path, locked))
        for path in _first_spellings(core._files(author))
        if referee.normalised(path) not in named
    )


def _shareable(helper):
    return helper.writer is None and helper.barred is None and bool(helper.takers)


def _with_helpers(item, paths):
    added = list(_first_spellings(paths))
    return {**item, "files": list(item.get("files") or []) + added} if added else item


def _sharing(kept, helpers):
    return [
        _with_helpers(item, [helper.path for helper in helpers if _shareable(helper) and index in helper.takers])
        for index, item in enumerate(kept)
    ]


def _lanes_wait(items, shared_files):
    return bool(core.lane_cycles(items, core.lane_items(items, shared_files)))


def _barred_words(reason):
    return "is " + reason if reason == referee.LOCKED_REASON else reason


def _helper_line(helper, given):
    if helper.writer is not None:
        return KEPT_LINE % (helper.path, helper.author, helper.writer)
    if helper.barred is not None:
        return REFEREE_LINE % (helper.path, helper.author, _barred_words(helper.barred))
    if referee.normalised(helper.path) in given:
        return SHARED_LINE % (helper.path, helper.author) if helper.takers else None
    return (WAITING_LINE if helper.takers else DROPPED_LINE) % (helper.path, helper.author)


def folded(items, locked=(), shared_files=()):
    authors = core.check_author_steps(items)
    if not authors:
        return list(items), ()
    authored = _written(authors)
    taking = [_with_authored_tests(item, authored) for item in core.without_check_author(items)]
    helpers = tuple(helper for author in authors for helper in _helpers(author, taking, locked))
    sharing = _sharing(taking, helpers)
    apart = _lanes_wait(sharing, shared_files) and not _lanes_wait(taking, shared_files)
    given = frozenset() if apart else _normalised(helper.path for helper in helpers if _shareable(helper))
    folds = tuple(FOLDED_LINE % author.get("name") for author in authors)
    lines = tuple(line for line in (_helper_line(helper, given) for helper in helpers) if line is not None)
    return (taking if apart else sharing), folds + lines
