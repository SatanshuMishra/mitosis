import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DOCS = ("README.md", os.path.join("skills", "mitosis", "SKILL.md"))
TERMS = (
    "--worker-sandbox",
    "--worker-write",
    "--suite-command",
    "--gate-classes",
    "check-author",
    "not-applicable",
    "covered-elsewhere",
    "untraced",
)


def text_of(relative):
    with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
        return handle.read()


class IsolationDocs(unittest.TestCase):
    def docs_describe_isolation_and_gate_classes(self):
        for relative in DOCS:
            with self.subTest(doc=relative):
                text = text_of(relative)
                missing = [term for term in TERMS if term not in text]
                self.assertEqual(missing, [], "%s does not mention %s" % (relative, missing))


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            IsolationDocs,
            ("docs_describe_isolation_and_gate_classes",),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
