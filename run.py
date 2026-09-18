import os
import re
import subprocess

import core

BRANCH_PREFIX = "mitosis"


class GitError(RuntimeError):
    def __init__(self, args, code, stderr):
        self.args_run = tuple(args)
        self.code = code
        self.stderr = stderr
        super().__init__(
            "git %s exited %d: %s" % (" ".join(args), code, stderr.strip() or "<no stderr>")
        )


def git(args, cwd, check=True):
    completed = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    if check and completed.returncode != 0:
        raise GitError(args, completed.returncode, completed.stderr)
    return completed.stdout.strip()


def git_ok(args, cwd):
    return (
        subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL
        ).returncode
        == 0
    )


def branch_exists(repo, branch):
    return git_ok(["rev-parse", "--verify", "--quiet", "refs/heads/" + branch], repo)


def slug(label):
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(label).lower()).strip("-")
    return cleaned or "msp"


def msp_slugs(msps):
    taken = ()
    for index, msp in enumerate(msps):
        label = msp.get("label") if isinstance(msp, dict) else None
        candidate = slug(label if label not in (None, "") else "msp-%d" % index)
        suffix = 2
        chosen = candidate
        while chosen in taken:
            chosen = "%s-%d" % (candidate, suffix)
            suffix += 1
        taken = taken + (chosen,)
    return taken


def registered_worktrees(repo):
    out = git(["worktree", "list", "--porcelain"], repo)
    trees = {}
    current = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            current = os.path.realpath(line[len("worktree "):])
            trees = {**trees, current: None}
        elif line.startswith("branch ") and current is not None:
            trees = {**trees, current: line[len("branch "):].replace("refs/heads/", "", 1)}
    return trees


def prepare_worktree(repo, base, branch, path):
    git(["worktree", "prune"], repo)
    registered = registered_worktrees(repo)
    real = os.path.realpath(path)
    if real in registered:
        if registered[real] != branch:
            raise GitError(
                ["worktree", "add", path, branch],
                1,
                "%s already holds branch %s, not %s" % (path, registered[real], branch),
            )
        return
    os.makedirs(os.path.dirname(real), exist_ok=True)
    if branch_exists(repo, branch):
        git(["worktree", "add", path, branch], repo)
    else:
        git(["worktree", "add", "-b", branch, path, base], repo)


def prepare_worktrees(msps, base, root, repo, prefix=BRANCH_PREFIX):
    slugs = msp_slugs(msps)
    trees = ()
    for index, msp in enumerate(msps):
        branch = "%s/%s" % (prefix, slugs[index])
        path = os.path.join(os.path.abspath(root), slugs[index])
        prepare_worktree(repo, base, branch, path)
        trees = trees + (
            {
                "msp": index,
                "label": msp.get("label") if isinstance(msp, dict) else None,
                "branch": branch,
                "path": path,
            },
        )
    return list(trees)
