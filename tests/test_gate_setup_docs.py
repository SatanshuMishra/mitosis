import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import mitosis

BULLET_START = re.compile(r"\n(?=[ \t]*(?:[-*+]|\d+\.)[ \t])")
BLANK_LINE = re.compile(r"\n[ \t]*\n")
SENTENCE_END = re.compile(r"\.(?: |\n)|\n[ \t]*\n")
BULLET = re.compile(r"^(?:[-*+]|\d+\.)[ \t]")
RELEASE_HEADING = "## 0.8.0"


def read_text(name):
    with open(os.path.join(ROOT, *name.split("/")), encoding="utf-8", newline="") as handle:
        return handle.read()


def flat(text):
    return " ".join(text.split())


def sentences(text):
    return [flat(piece) for piece in SENTENCE_END.split(text) if piece.strip()]


def paragraphs(text):
    return [flat(piece) for piece in BLANK_LINE.split(text) if piece.strip()]


def blocks(text):
    return [
        flat(piece)
        for paragraph in BLANK_LINE.split(text)
        for piece in BULLET_START.split(paragraph)
        if piece.strip()
    ]


def release_section(text, heading):
    lines = text.split("\n")
    starts = [index for index, line in enumerate(lines) if line.startswith(heading)]
    if not starts:
        return ""
    following = [
        index for index, line in enumerate(lines) if index > starts[0] and line.startswith("## ")
    ]
    end = following[0] if following else len(lines)
    return "\n".join(lines[starts[0] + 1 : end])


def bullets(text):
    return [
        BULLET.sub("", piece.strip(), count=1)
        for piece in blocks(text)
        if BULLET.match(piece.strip())
    ]


def has_all(text, words):
    return all(word in text for word in words)


class GateSetupDocs(unittest.TestCase):
    def no_doc_offers_gate_setup_as_a_remedy_for_lfs_or_eol(self):
        documents = {
            name: read_text(name) for name in ("README.md", "skills/mitosis/SKILL.md", "CHANGELOG.md")
        }
        for name, text in sorted(documents.items()):
            with self.subTest(document=name):
                offering = [
                    sentence
                    for sentence in sentences(text)
                    if "--gate-setup" in sentence
                    and ("LFS" in sentence or "eol" in sentence)
                    and ("prepare" in sentence or "need" in sentence)
                ]
                self.assertEqual(offering, [], "%s offers --gate-setup for LFS or eol" % name)
        for name in ("README.md", "skills/mitosis/SKILL.md"):
            with self.subTest(document=name):
                self.assertTrue(
                    any(
                        has_all(block, ("LFS", "pointer", "--gate-setup", "top-level"))
                        for block in blocks(documents[name])
                    ),
                    "%s has no paragraph naming LFS, pointer, --gate-setup and top-level" % name,
                )
        with self.subTest(document="CHANGELOG.md"):
            section = release_section(documents["CHANGELOG.md"], RELEASE_HEADING)
            self.assertTrue(section, "CHANGELOG.md has no %s heading" % RELEASE_HEADING)
            self.assertTrue(
                any(has_all(item, ("--gate-setup", "top-level")) for item in bullets(section)),
                "CHANGELOG.md has no %s bullet naming --gate-setup and top-level" % RELEASE_HEADING,
            )

    def the_docs_name_the_submodule_hold_and_revert(self):
        for name in ("README.md", "skills/mitosis/SKILL.md"):
            found = paragraphs(read_text(name))
            with self.subTest(document=name):
                self.assertTrue(
                    any(has_all(paragraph, ("submodule", "cannot be judged", "exit 21")) for paragraph in found),
                    "%s has no paragraph naming submodule, cannot be judged and exit 21" % name,
                )
                self.assertTrue(
                    any(has_all(paragraph, ("submodule", "gitlink", "exit 9")) for paragraph in found),
                    "%s has no paragraph naming submodule, gitlink and exit 9" % name,
                )

    def the_help_text_matches_the_clean_gate(self):
        resume = flat(mitosis.FLAG_SPECS["--resume"]["help"])
        gate_setup = flat(mitosis.FLAG_SPECS["--gate-setup"]["help"])
        self.assertNotIn("uncommitted", resume)
        self.assertIn("pre-gate commit", resume)
        self.assertIn("top-level", gate_setup)


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            GateSetupDocs,
            (
                "no_doc_offers_gate_setup_as_a_remedy_for_lfs_or_eol",
                "the_docs_name_the_submodule_hold_and_revert",
                "the_help_text_matches_the_clean_gate",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
