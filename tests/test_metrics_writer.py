import datetime
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "metrics")
WARNING_PREFIX = "metrics not written: "
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
STANDIN = ("<root>", "<home>")
INHERITED_ONLY_WHEN_SET = ("PLUGIN_METRICS_DIR", "PLUGIN_METRICS_PARENT", "CLAUDE_CODE_SESSION_ID")
SOURCE_REFS = ("origin/main", "origin/feat/unified-metrics")
COPIED_FILES = (
    "metrics_writer.py",
    "tests/fixtures/metrics/cases.json",
    "tests/fixtures/metrics/expected.json",
    "tests/fixtures/metrics/line.schema.json",
    "tests/fixtures/metrics/catalogue.schema.json",
)
ENVELOPE = ["v", "ts", "plugin", "plugin_version", "kind", "event", "run", "parent", "session", "repo"]


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


def moment(text):
    return datetime.datetime.strptime(text, TIMESTAMP_FORMAT).replace(tzinfo=datetime.timezone.utc)


def substituted(value, root, home):
    return dict(zip(STANDIN, (root, home))).get(value, value) if isinstance(value, str) else value


def relative(path, root):
    return os.path.relpath(path, root).replace(os.sep, "/")


def reported(result, root):
    if isinstance(result, tuple):
        return [relative(path, root) for path in result]
    if isinstance(result, str) and result.startswith(WARNING_PREFIX):
        rest = result[len(WARNING_PREFIX):]
        return WARNING_PREFIX + rest[: rest.index(": ") + 2]
    return result


def call_result(writer, call, environ, root, home):
    args = {name: substituted(value, root, home) for name, value in call["args"].items()}
    if call["fn"] in ("append_event", "write_summary"):
        return getattr(writer, call["fn"])(environ=environ, now=moment(call["now"]), **args)
    if call["fn"] == "write_catalogue":
        return writer.write_catalogue(environ=environ, **args)
    if call["fn"] == "prune":
        return writer.prune(**args)
    return writer.run_id(args["name"], moment(args["start"]))


def written_files(root):
    found = {}
    for folder, _, names in os.walk(root):
        for name in names:
            path = os.path.join(folder, name)
            with open(path, "rb") as handle:
                found = dict(found, **{relative(path, root): handle.read().decode("utf-8")})
    return found


def case_environment(case, root, home):
    inherited = {name: value for name, value in os.environ.items() if name not in INHERITED_ONLY_WHEN_SET}
    chosen = {name: substituted(value, root, home) for name, value in case["environ"].items()}
    return dict(inherited, **chosen)


def run_case(writer, case, folder):
    root = os.path.join(folder, "root")
    home = os.path.join(folder, "home")
    os.makedirs(root)
    os.makedirs(home)
    environ = case_environment(case, root, home)
    with unittest.mock.patch.dict(os.environ, environ, clear=True):
        returns = [reported(call_result(writer, call, environ, root, home), root) for call in case["calls"]]
    return {"files": written_files(root), "returns": returns}


def sha256_of(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class WriterConformance(unittest.TestCase):
    def setUp(self):
        self.folder = os.path.realpath(tempfile.mkdtemp(prefix="metrics-writer-"))
        self.addCleanup(shutil.rmtree, self.folder, True)

    def test_writer_matches_fixtures(self):
        import metrics_writer
        cases = fixture("cases.json")
        expected = fixture("expected.json")
        self.assertEqual(sorted(case["name"] for case in cases), sorted(expected))
        for case in cases:
            with self.subTest(case=case["name"]):
                outcome = run_case(metrics_writer, case, os.path.join(self.folder, case["name"]))
                self.assertEqual(outcome, expected[case["name"]])


class CopiedFiles(unittest.TestCase):
    def test_copies_match_their_source(self):
        source = fixture("source.json")
        self.assertIn(source["ref"], SOURCE_REFS)
        self.assertRegex(source["commit"], "^[0-9a-f]{40}$")
        recorded = {record["path"]: record["sha256"] for record in source["files"]}
        for path in COPIED_FILES:
            with self.subTest(path=path):
                self.assertRegex(recorded.get(path, ""), "^[0-9a-f]{64}$")
                self.assertEqual(sha256_of(os.path.join(ROOT, path)), recorded[path])

    def test_line_schema_requires_the_envelope(self):
        self.assertEqual(fixture("line.schema.json")["required"], ENVELOPE)
