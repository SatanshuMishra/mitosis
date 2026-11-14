import collections
import os
import shutil
import tempfile

import run

OURS_MARK = "<<<<<<< the other Lane's, already on the branch"
SPLIT_MARK = "======="
THEIRS_MARK = ">>>>>>> yours"

MARK_SIZE = 48
OURS_LABEL, BASE_LABEL, THEIRS_LABEL = "landed", "start", "lane"
OPENING = b"<" * MARK_SIZE + b" " + OURS_LABEL.encode()
MIDDLE = b"|" * MARK_SIZE + b" " + BASE_LABEL.encode()
DIVIDER = b"=" * MARK_SIZE
CLOSING = b">" * MARK_SIZE + b" " + THEIRS_LABEL.encode()
BINARY_SIGN = b"\0"

Joined = collections.namedtuple("Joined", ("text", "conflicts"))

Meeting = collections.namedtuple("Meeting", ("ours", "base", "theirs"))


def _marker(line):
    return line.rstrip(b"\r\n")


def _is_text(*sides):
    return not any(BINARY_SIGN in side for side in sides)


def _collides(*sides):
    marks = (OPENING, MIDDLE, DIVIDER, CLOSING)
    return any(_marker(line) in marks for side in sides for line in side.splitlines(True))


def _ended(text):
    return text if not text or text.endswith(b"\n") else text + b"\n"


def _parts(merged):
    parts, kept, meeting, side = [], [], None, None
    for line in merged.splitlines(True):
        mark = _marker(line)
        if meeting is None and mark == OPENING:
            parts, kept, meeting, side = parts + [b"".join(kept)], [], {"ours": [], "base": [], "theirs": []}, "ours"
        elif meeting is not None and mark == MIDDLE:
            side = "base"
        elif meeting is not None and mark == DIVIDER:
            side = "theirs"
        elif meeting is not None and mark == CLOSING:
            parts = parts + [Meeting(*(b"".join(meeting[name]) for name in ("ours", "base", "theirs")))]
            meeting, side = None, None
        elif meeting is not None:
            meeting = {**meeting, side: meeting[side] + [line]}
        else:
            kept = kept + [line]
    return parts + [b"".join(kept)]


def _shown(part):
    return b"".join(
        (
            OURS_MARK.encode() + b"\n",
            _ended(part.ours),
            SPLIT_MARK.encode() + b"\n",
            _ended(part.theirs),
            THEIRS_MARK.encode() + b"\n",
        )
    )


def _both(part):
    return _ended(part.ours) + part.theirs


def _settled(part):
    if not isinstance(part, Meeting):
        return part, 0
    if part.base:
        return _shown(part), 1
    return _both(part), 0


def _three_way(base, ours, theirs):
    folder = tempfile.mkdtemp(prefix="mitosis-joint-")
    try:
        paths = {}
        for name, data in (("ours", ours), ("base", base), ("theirs", theirs)):
            paths = {**paths, name: os.path.join(folder, name)}
            with open(paths[name], "wb") as handle:
                handle.write(data)
        done = run.git_run(
            [
                "merge-file",
                "-p",
                "--diff3",
                "--marker-size=%d" % MARK_SIZE,
                "-L",
                OURS_LABEL,
                "-L",
                BASE_LABEL,
                "-L",
                THEIRS_LABEL,
                paths["ours"],
                paths["base"],
                paths["theirs"],
            ],
            folder,
            raw=True,
        )
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if done.returncode < 0 or done.returncode > 127:
        raise run.GitError(["merge-file"], done.returncode, done.stderr)
    return done.stdout


def _whole(ours, theirs):
    return Joined(_shown(Meeting(ours, b"", theirs)), 1)


def joined(base, ours, theirs):
    if base is None or not _is_text(base, ours, theirs) or _collides(base, ours, theirs):
        return _whole(ours, theirs)
    settled = tuple(_settled(part) for part in _parts(_three_way(base, ours, theirs)))
    return Joined(b"".join(text for text, _ in settled), sum(count for _, count in settled))


def marked(text):
    marks = (OURS_MARK.encode(), THEIRS_MARK.encode())
    return any(_marker(line) in marks for line in text.splitlines(True))
