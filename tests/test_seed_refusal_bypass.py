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
GIT_IDENTITY = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "mitosis test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "mitosis test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}
VENV_FILES = {
    "pyvenv.cfg": "home = /usr/bin\n",
    "bin/python": "#!/usr/bin/python3\n",
    "bin/pip": "#!/checkout/.venv/bin/python\n",
    "lib/site.py": "site\n",
    "include/header.h": "header\n",
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


def venv(prefix):
    return {prefix + path: content for path, content in VENV_FILES.items()}


class SeedRefusalBypass(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="mitosis-seed-bypass-"))
        self.repo = os.path.join(self.tmp, "repo")
        home = os.path.join(self.tmp, "home")
        os.makedirs(self.repo)
        os.makedirs(home)
        self.env = {**os.environ, **GIT_IDENTITY, "HOME": home}

    def tearDown(self):
        opened_tree_removed(self.tmp)

    def git(self, *args):
        completed = subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=CALL_LIMIT
        )
        if completed.returncode != 0:
            raise RuntimeError("scaffold git %s failed: %s" % (" ".join(args), completed.stderr))

    def plan(self, ignores, listed, loose):
        self.git("init", "-q", "-b", "main", self.repo)
        put(os.path.join(self.repo, ".gitignore"), ignores)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "seed")
        put(os.path.join(self.repo, ".worktreeinclude"), listed)
        for path, content in sorted(loose.items()):
            put(os.path.join(self.repo, path), content)
        return importlib.import_module("isolation").seed_plan(self.repo)

    def skipped(self, plan):
        entries = plan.get("skipped") or []
        for entry in entries:
            self.assertTrue((entry.get("reason") or "").strip(), "a skipped entry carries no reason")
        return sorted(entry.get("path") for entry in entries)

    def test_children_of_a_virtual_environment_are_refused(self):
        plan = self.plan(".venv/\n", ".venv/*\n", venv(".venv/"))
        self.assertEqual(plan.get("paths"), [])
        self.assertTrue(self.skipped(plan), "nothing under .venv/ was recorded as refused")
        self.assertTrue(all(path.startswith(".venv/") for path in self.skipped(plan)))

    def test_a_folder_inside_a_virtual_environment_is_refused(self):
        plan = self.plan(".venv/\n", ".venv/bin/\n", venv(".venv/"))
        self.assertEqual(plan.get("paths"), [])
        self.assertEqual(self.skipped(plan), [".venv/bin/"])

    def test_a_folder_holding_a_virtual_environment_seeds_only_its_other_contents(self):
        plan = self.plan(
            "tools/\n", "tools/\n", {**venv("tools/env/"), "tools/cache/a.bin": "warm\n", "tools/run.sh": "run\n"}
        )
        self.assertEqual(plan.get("paths"), ["tools/cache/", "tools/run.sh"])
        self.assertEqual(self.skipped(plan), ["tools/env/"])

    def test_a_collapsed_folder_holding_a_listed_secret_is_not_copied(self):
        plan = self.plan("config/\n", "*.pem\n", {"config/server.pem": "secret\n"})
        self.assertEqual(plan.get("paths"), [])
        self.assertEqual(self.skipped(plan), ["config/server.pem"])

    def test_a_collapsed_folder_from_a_listed_secret_path_is_not_copied(self):
        plan = self.plan("config/\n", "config/server.pem\n", {"config/server.pem": "secret\n"})
        self.assertEqual(plan.get("paths"), [])
        self.assertEqual(self.skipped(plan), ["config/server.pem"])

    def test_a_nested_secret_in_a_collapsed_folder_is_not_copied(self):
        plan = self.plan("deploy/\n", "**/.env\n", {"deploy/.env": "TOKEN=secret\n"})
        self.assertEqual(plan.get("paths"), [])
        self.assertEqual(self.skipped(plan), ["deploy/.env"])

    def test_a_listed_folder_holding_a_secret_seeds_everything_but_the_secret(self):
        plan = self.plan(
            "node_modules/\n",
            "node_modules/\n",
            {
                "node_modules/left/index.js": "left\n",
                "node_modules/left/.npmrc": "//registry/:_authToken=secret\n",
                "node_modules/left/lib/a.js": "a\n",
                "node_modules/right/index.js": "right\n",
            },
        )
        self.assertEqual(
            plan.get("paths"), ["node_modules/left/index.js", "node_modules/left/lib/", "node_modules/right/"]
        )
        self.assertEqual(self.skipped(plan), ["node_modules/left/.npmrc"])

    def test_a_secret_name_in_another_letter_case_is_not_copied(self):
        plan = self.plan("config/\n", "config/\n", {"config/SERVER.PEM": "secret\n", "config/.Env": "TOKEN=secret\n"})
        self.assertEqual(plan.get("paths"), [])
        self.assertEqual(self.skipped(plan), ["config/.Env", "config/SERVER.PEM"])

    def test_a_folder_without_secrets_or_environments_stays_one_copy(self):
        plan = self.plan(
            "node_modules/\n",
            "node_modules/\n",
            {"node_modules/left/index.js": "left\n", "node_modules/right/lib/a.js": "a\n"},
        )
        self.assertEqual(plan.get("paths"), ["node_modules/"])
        self.assertEqual(self.skipped(plan), [])


if __name__ == "__main__":
    unittest.main()
