#!/usr/bin/env python3
import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VERSION_LINE = re.compile(r'^__version__ = "(\d+)\.(\d+)\.(\d+)"$', re.M)

UNSHIPPED = re.compile(
    r"^(?:tests/|docs/|scripts/|\.github/|\.receipts/"
    r"|(?:README\.md|CHANGELOG\.md|LICENSE|NOTICE|\.gitignore|AGENTS\.md|receipts\.config\.json)$)"
)

RELEASE_TAGS = "mitosis--v*"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_MISCONFIGURED = 2


def git(root, *args):
    return subprocess.run(("git", "-C", root) + args, capture_output=True, check=True).stdout


def _text(raw):
    return raw.decode("utf-8", "replace")


def _names(raw):
    return tuple(os.fsdecode(name) for name in raw.split(b"\0") if name)


def version_at(root, ref):
    match = VERSION_LINE.search(_text(git(root, "show", "%s:core.py" % ref)))
    return tuple(int(part) for part in match.groups()) if match else None


def is_ancestor(root, ref):
    probe = ("git", "-C", root, "merge-base", "--is-ancestor", ref, "HEAD")
    return subprocess.run(probe, capture_output=True).returncode == 0


def last_release(root):
    tags = _text(git(root, "tag", "--merged", "HEAD", "--list", RELEASE_TAGS, "--sort=-v:refname")).split()
    return tags[0] if tags else None


def shipped_changes(root, base):
    names = _names(git(root, "diff", "--name-only", "--no-renames", "-z", "%s...HEAD" % base))
    return tuple(name for name in names if not UNSHIPPED.match(name))


def _dotted(version):
    return ".".join(str(part) for part in version) if version else "no version"


def bump_problems(root, base):
    changed = () if base is None else shipped_changes(root, base)
    if not changed:
        return ()
    before, after = version_at(root, base), version_at(root, "HEAD")
    if after is not None and (before is None or after > before):
        return ()
    return (
        "%d shipped file(s) changed since %s, starting with %s, but the version went from %s to %s; "
        "raise it in core.py and .claude-plugin/plugin.json and add its CHANGELOG.md heading"
        % (len(changed), base, changed[0], _dotted(before), _dotted(after)),
    )


def terms_from(raw):
    return tuple(sorted({term.strip().lower() for term in re.split(r"[\n,]", raw or "") if term.strip()}))


def _hit(text, terms):
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _occurrences(text, term):
    return tuple(start for start in range(len(text) - len(term) + 1) if text.startswith(term, start))


def masked(text, terms):
    lowered = text.lower()
    if len(lowered) != len(text):
        return "*" * len(text) if _hit(text, terms) else text
    covered = frozenset(
        index
        for term in terms
        for start in _occurrences(lowered, term)
        for index in range(start, start + len(term))
    )
    return "".join("*" if index in covered else char for index, char in enumerate(text))


def _shown(name, terms):
    return masked(name.encode("utf-8", "surrogateescape").decode("utf-8", "replace"), terms)


def _content(root, name):
    path = os.path.join(root, name)
    if os.path.islink(path):
        return os.fsencode(os.readlink(path))
    if not os.path.isfile(path):
        return b""
    with open(path, "rb") as handle:
        return handle.read()


def _file_hits(root, name, terms):
    raw = _content(root, name)
    shown = _shown(name, terms)
    found = tuple(
        "%s:%d" % (shown, number) for number, line in enumerate(_text(raw).splitlines(), 1) if _hit(line, terms)
    )
    if found or not _hit(raw.replace(b"\0", b"").decode("latin-1"), terms):
        return found
    return ("%s (in content that is not UTF-8 text)" % shown,)


def tracked_hits(root, terms):
    names = _names(git(root, "ls-files", "-z"))
    return tuple("%s (path)" % _shown(name, terms) for name in names if _hit(name, terms)) + tuple(
        hit for name in names for hit in _file_hits(root, name, terms)
    )


def commit_hits(root, base, terms):
    span = "HEAD" if base is None else "%s..HEAD" % base
    entries = _text(git(root, "log", "-z", "--format=%h%n%B", span)).split("\0")
    return tuple(
        "commit %s" % entry.split("\n", 1)[0]
        for entry in entries
        if entry.strip() and _hit(entry.split("\n", 1)[-1], terms)
    )


def text_hits(text, terms):
    return ("pull request title or body",) if _hit(text, terms) else ()


def denied(root, base, terms):
    return tuple("denied term in %s" % place for place in tracked_hits(root, terms) + commit_hits(root, base, terms))


def build_parser():
    parser = argparse.ArgumentParser(
        description="Refuse a release whose shipped files changed without a version bump, or whose "
        "published text contains a denied term. Terms come from DENY_TERMS, one per line or comma "
        "separated; a pull request's title and body come from PR_TEXT."
    )
    parser.add_argument("--base", default="origin/main", help="the ref this release is compared with")
    parser.add_argument(
        "--since-release",
        action="store_true",
        help="compare with the latest release tag merged into HEAD instead of --base",
    )
    parser.add_argument(
        "--or-last-release",
        action="store_true",
        help="compare with the latest release tag when --base is not an ancestor of HEAD",
    )
    parser.add_argument("--text-only", action="store_true", help="scan only PR_TEXT")
    parser.add_argument("--root", default=ROOT, help="the repository to check")
    return parser


def _problems(args, terms, env):
    if args.text_only:
        return tuple("denied term in %s" % place for place in text_hits(env.get("PR_TEXT", ""), terms))
    fallback = args.or_last_release and not is_ancestor(args.root, args.base)
    base = last_release(args.root) if args.since_release or fallback else args.base
    return bump_problems(args.root, base) + denied(args.root, base, terms)


def main(argv=None, environ=None):
    args = build_parser().parse_args(argv)
    env = os.environ if environ is None else environ
    terms = terms_from(env.get("DENY_TERMS"))
    if not terms:
        print("release check: DENY_TERMS is empty, so the denied-term scan cannot run", file=sys.stderr)
        return EXIT_MISCONFIGURED
    try:
        problems = _problems(args, terms, env)
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode("utf-8", "replace").strip() if error.stderr else str(error)
        print("release check: git failed: %s" % masked(detail, terms), file=sys.stderr)
        return EXIT_MISCONFIGURED
    except OSError as error:
        print("release check: %s" % masked(str(error), terms), file=sys.stderr)
        return EXIT_MISCONFIGURED
    for problem in problems:
        print(masked(problem, terms))
    return EXIT_FAILED if problems else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
