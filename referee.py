import fnmatch
import hashlib
import re

import core

ANYWHERE = (
    "conftest.py",
    "pytest.ini",
    "tox.ini",
    "noxfile.py",
    ".coveragerc",
    "*jest.config.*",
    "*vitest.config.*",
    "*vitest.workspace.*",
    "*karma.conf.*",
    "*.mocharc*",
    "*playwright.config.*",
    "*cypress.config.*",
    "*phpunit.xml*",
    "*.rspec",
    "receipts.config.json",
    "*.receipts/*",
    "*__snapshots__/*",
    "*.snap",
)

IN_TESTS = (
    "*baseline*",
    "*allowlist*",
    "*whitelist*",
    "*skiplist*",
    "*skip-list*",
    "*skip_list*",
    "*snapshots/*",
)

TEST_SEGMENTS = frozenset(("test", "tests", "__tests__", "spec", "specs", "e2e", "testdata"))

TEST_FILE_NAMES = ("test_*", "*_test.*", "*.test.*", "*.spec.*")

LOCKED_LINE = re.compile(r"^\s*- Locked:(.*)$")

COLLATION = "_-,;:!?.'\"()[]{}@*/\\&#%`^+<=>|~$0123456789abcdefghijklmnopqrstuvwxyz"

COLLATION_RANK = {character: rank for rank, character in enumerate(COLLATION)}

LOCKED_REASON = "locked by the charter"


def normalised(path):
    return core._norm(str(path).replace("\\", "/"))


def in_test_location(path):
    segments = normalised(path).lower().split("/")
    if any(segment in TEST_SEGMENTS for segment in segments):
        return True
    return any(fnmatch.fnmatchcase(segments[-1], pattern) for pattern in TEST_FILE_NAMES)


def _locked_path(line):
    path = line.strip().strip("`").strip().replace("\\", "/")
    if not path:
        raise ValueError("the charter line %r names no path" % line)
    if path.startswith("/") or re.match(r"^[A-Za-z]:/", path):
        raise ValueError("the charter line %r locks an absolute path" % line)
    if ".." in path.split("/"):
        raise ValueError("the charter line %r locks a path that climbs out with .." % line)
    return path


def locked_paths(charter_text):
    found = ()
    for line in charter_text.splitlines():
        matched = LOCKED_LINE.match(line)
        if matched is None:
            continue
        path = _locked_path(matched.group(1))
        if path not in found:
            found = found + (path,)
    return found


def referee_reason(path, locked):
    target = normalised(path)
    if target in {normalised(entry) for entry in locked}:
        return LOCKED_REASON
    markers = ANYWHERE + (IN_TESTS if in_test_location(target) else ())
    marker = next((marker for marker in markers if core.marker_matches(target, marker)), None)
    return None if marker is None else "matches the referee marker %s" % marker


def _folder_markers(markers):
    return tuple(marker for marker in markers if "/" in marker)


def top_level_reason(name, locked):
    reason = referee_reason(name, locked)
    if reason is not None:
        return reason
    folder = normalised(name)
    if any(normalised(entry).startswith(folder + "/") for entry in locked):
        return LOCKED_REASON
    markers = _folder_markers(ANYWHERE) + (_folder_markers(IN_TESTS) if in_test_location(folder) else ())
    candidate = folder.lower() + "/"
    marker = next((marker for marker in markers if fnmatch.fnmatchcase(candidate, marker)), None)
    return None if marker is None else "matches the referee marker %s" % marker


def _collation_key(path):
    return (
        tuple(
            COLLATION_RANK.get(character, len(COLLATION) + ord(character))
            for character in path.lower()
        ),
        tuple(1 if character.isupper() else 0 for character in path),
    )


def _bytes(data):
    return data if isinstance(data, bytes) else str(data).encode("utf-8")


def receipt_lock(files):
    digest = hashlib.sha256()
    for path, data in sorted(files, key=lambda pair: _collation_key(pair[0])):
        digest.update(("file:%s\n" % path).encode("utf-8"))
        digest.update(_bytes(data).replace(b"\r\n", b"\n"))
        digest.update(b"\n")
    return digest.hexdigest()
