#!/usr/bin/env python3
import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VERSION_LINE = re.compile(r'^__version__ = "(\d+)\.(\d+)\.(\d+)"$', re.M)

SHIPPED = re.compile(r"^(?:[^/]+\.py|skills/.+|\.claude-plugin/.+)$")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_MISCONFIGURED = 2


def git(root, *args):
    return subprocess.run(("git", "-C", root) + args, capture_output=True, check=True).stdout.decode(
        "utf-8", "replace"
    )


def version_at(root, ref):
    match = VERSION_LINE.search(git(root, "show", "%s:core.py" % ref))
    return tuple(int(part) for part in match.groups()) if match else None


def shipped_changes(root, base):
    names = git(root, "diff", "--name-only", "-z", "%s...HEAD" % base).split("\0")
    return tuple(name for name in names if name and SHIPPED.match(name))


def _dotted(version):
    return ".".join(str(part) for part in version) if version else "no version"


def bump_problems(root, base):
    changed = shipped_changes(root, base)
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


def _file_hits(root, name, terms):
    path = os.path.join(root, name)
    if not os.path.isfile(path):
        return ()
    with open(path, "rb") as handle:
        lines = handle.read().decode("utf-8", "replace").splitlines()
    return tuple("%s:%d" % (name, number) for number, line in enumerate(lines, 1) if _hit(line, terms))


def tracked_hits(root, terms):
    names = tuple(name for name in git(root, "ls-files", "-z").split("\0") if name)
    return tuple("%s (path)" % name for name in names if _hit(name, terms)) + tuple(
        hit for name in names for hit in _file_hits(root, name, terms)
    )


def commit_hits(root, base, terms):
    entries = git(root, "log", "-z", "--format=%h%n%B", "%s..HEAD" % base).split("\0")
    return tuple(
        "commit %s" % entry.split("\n", 1)[0]
        for entry in entries
        if entry.strip() and _hit(entry.split("\n", 1)[-1], terms)
    )


def denied(root, base, terms, extra_text=""):
    found = tracked_hits(root, terms) + commit_hits(root, base, terms)
    found = found + (("pull request title or body",) if _hit(extra_text, terms) else ())
    return tuple("denied term in %s" % place for place in found)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Refuse a release whose shipped files changed without a version bump, or whose "
        "published text contains a denied term. Terms come from DENY_TERMS, one per line or comma "
        "separated; extra text to scan, such as a pull request's title and body, comes from PR_TEXT."
    )
    parser.add_argument("--base", default="origin/main", help="the ref this release is compared with")
    parser.add_argument("--root", default=ROOT, help="the repository to check")
    return parser


def main(argv=None, environ=None):
    args = build_parser().parse_args(argv)
    env = os.environ if environ is None else environ
    terms = terms_from(env.get("DENY_TERMS"))
    if not terms:
        print("release check: DENY_TERMS is empty, so the denied-term scan cannot run", file=sys.stderr)
        return EXIT_MISCONFIGURED
    try:
        problems = bump_problems(args.root, args.base) + denied(
            args.root, args.base, terms, env.get("PR_TEXT", "")
        )
    except subprocess.CalledProcessError as error:
        print("release check: git failed: %s" % error.stderr.decode("utf-8", "replace").strip(), file=sys.stderr)
        return EXIT_MISCONFIGURED
    for problem in problems:
        print(problem)
    return EXIT_FAILED if problems else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
