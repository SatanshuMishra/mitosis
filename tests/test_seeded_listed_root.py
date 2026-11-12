import importlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

CALL_LIMIT = 60
APFS_ONLY = "seeded copies are made with clonefile, which only this Mac's APFS carries"
GIT_IDENTITY = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "mitosis test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "mitosis test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}
WORKER_FILE = "worker-new.txt"
FIXTURES = {
    "a key beside a subfolder": (
        "cache/",
        {"cache/key.pem": "secret\n", "cache/sub/a.txt": "warm\n"},
    ),
    "a package holding an npmrc": (
        "node_modules/",
        {
            "node_modules/left/index.js": "left\n",
            "node_modules/left/.npmrc": "//registry/:_authToken=secret\n",
            "node_modules/right/index.js": "right\n",
        },
    ),
    "a folder holding a virtual environment": (
        "tools/",
        {
            "tools/venv/pyvenv.cfg": "home = /usr/bin\n",
            "tools/venv/bin/python": "#!/usr/bin/python3\n",
            "tools/run.sh": "run\n",
        },
    ),
    "a folder whose only child holds an npmrc": (
        "cache/",
        {"cache/sub/.npmrc": "//registry/:_authToken=secret\n", "cache/sub/a.txt": "warm\n"},
    ),
    "a folder with nothing refused": (
        "cache/",
        {"cache/sub/a.txt": "warm\n"},
    ),
}


def put(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content.encode("utf-8"))


def opened_tree_removed(top):
    for folder, _, _ in os.walk(top):
        if not os.path.islink(folder):
            os.chmod(folder, stat.S_IRWXU)
    shutil.rmtree(top)


class SeededListedRoot(unittest.TestCase):
    def setUp(self):
        if sys.platform != "darwin":
            self.skipTest(APFS_ONLY)
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="mitosis-seed-root-"))
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        self.env = {**os.environ, **GIT_IDENTITY, "HOME": home}
        self.isolation = importlib.import_module("isolation")
        self.run = importlib.import_module("run")

    def tearDown(self):
        opened_tree_removed(self.tmp)

    def git(self, folder, *args):
        completed = subprocess.run(
            ["git", *args], cwd=folder, env=self.env, capture_output=True, text=True, timeout=CALL_LIMIT
        )
        if completed.returncode != 0:
            raise RuntimeError("scaffold git %s failed: %s" % (" ".join(args), completed.stderr))
        return completed.stdout.strip()

    def landed(self, name, listed, loose):
        top = os.path.join(self.tmp, name.replace(" ", "-"))
        repo, tree, clone = (os.path.join(top, part) for part in ("repo", "tree", "clone"))
        os.makedirs(repo)
        self.git(repo, "init", "-q", "-b", "main", repo)
        put(os.path.join(repo, "README"), "readme\n")
        put(os.path.join(repo, ".gitignore"), listed + "\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "seed")
        put(os.path.join(repo, ".worktreeinclude"), listed + "\n")
        for path, content in sorted(loose.items()):
            put(os.path.join(repo, path), content)
        self.git(top, "clone", "-q", repo, tree)
        self.git(tree, "rm", "-q", ".gitignore")
        self.git(tree, "commit", "-q", "-m", "an earlier Lane dropped the ignore line")
        start = self.git(tree, "rev-parse", "HEAD")
        self.git(top, "clone", "-q", repo, clone)
        plan = self.isolation.seed_plan(repo)
        seeding = self.isolation.seed_clone(repo, plan, clone)
        put(os.path.join(clone, listed, WORKER_FILE), "a Worker's new file\n")
        outside = self.isolation.outside_writes(
            clone, tree, self.run._committed_entries(tree, start), (), seeding["seeded"]
        )
        under = lambda paths: sorted(path for path in paths if path.startswith(listed))
        return seeding["seeded"], under(outside["writes"]), under(outside["ignored"])

    def test_a_worker_file_under_a_split_listed_folder_never_lands(self):
        for name, (listed, loose) in sorted(FIXTURES.items()):
            with self.subTest(name):
                seeded, writes, ignored = self.landed(name, listed, loose)
                self.assertEqual(writes, [])
                self.assertEqual(ignored, [])
                self.assertEqual(seeded, [listed])

    def test_a_listed_root_with_no_copy_made_is_not_recorded_as_seeded(self):
        repo = os.path.join(self.tmp, "only-secret")
        os.makedirs(repo)
        self.git(repo, "init", "-q", "-b", "main", repo)
        put(os.path.join(repo, ".gitignore"), "cache/\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "seed")
        put(os.path.join(repo, ".worktreeinclude"), "cache/\n")
        put(os.path.join(repo, "cache/key.pem"), "secret\n")
        clone = os.path.join(self.tmp, "only-secret-clone")
        os.makedirs(clone)
        seeding = self.isolation.seed_clone(repo, self.isolation.seed_plan(repo), clone)
        self.assertEqual(seeding["seeded"], [])
        self.assertEqual([entry["path"] for entry in seeding["skipped"]], ["cache/key.pem"])

    def test_a_worker_file_beside_a_file_seeded_from_a_folder_ignored_by_pattern_is_seen(self):
        top = os.path.join(self.tmp, "ignored-by-pattern")
        repo, tree, clone = (os.path.join(top, part) for part in ("repo", "tree", "clone"))
        os.makedirs(repo)
        self.git(repo, "init", "-q", "-b", "main", repo)
        put(os.path.join(repo, "README"), "readme\n")
        put(os.path.join(repo, ".gitignore"), "objs/*.o\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "seed")
        put(os.path.join(repo, ".worktreeinclude"), "objs/\n")
        put(os.path.join(repo, "objs/a.o"), "warm\n")
        self.git(top, "clone", "-q", repo, tree)
        start = self.git(tree, "rev-parse", "HEAD")
        self.git(top, "clone", "-q", repo, clone)
        seeding = self.isolation.seed_clone(repo, self.isolation.seed_plan(repo), clone)
        put(os.path.join(clone, "objs", WORKER_FILE), "a Worker's new file\n")
        outside = self.isolation.outside_writes(
            clone, tree, self.run._committed_entries(tree, start), (), seeding["seeded"]
        )
        under = lambda paths: sorted(path for path in paths if path.startswith("objs/"))
        self.assertEqual(seeding["seeded"], ["objs/a.o"])
        self.assertEqual(under(outside["writes"]), ["objs/" + WORKER_FILE])
        self.assertEqual(under(outside["ignored"]), [])

    def test_a_plan_without_roots_records_each_copy_as_its_own_root(self):
        repo = os.path.join(self.tmp, "plain")
        put(os.path.join(repo, "cache/sub/a.txt"), "warm\n")
        clone = os.path.join(self.tmp, "plain-clone")
        os.makedirs(clone)
        seeding = self.isolation.seed_clone(repo, {"paths": ["cache/sub/"], "skipped": []}, clone)
        self.assertEqual(seeding["seeded"], ["cache/sub/"])


if __name__ == "__main__":
    unittest.main()
