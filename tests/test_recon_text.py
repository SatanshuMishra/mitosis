import hashlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import recon_text

N = (
    "No graphify graph is available, so fall back to the codebase: grep for "
    "the surface the request names, then follow imports; for net-new work "
    "read the directory structure and place it beside its neighbours."
)


class ReconText(unittest.TestCase):
    def contract_is_byte_exact(self):
        text = recon_text.RECON_CONTRACT
        self.assertEqual(
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "53c4a2066e659794a94d2f66dd3039e2d3180eb5950a3ca2111c0781dfa2e0cc",
        )
        self.assertEqual(len(text.splitlines()), 56)
        self.assertEqual(len(text), 3202)

    def spec_lines_hold_the_registry_rules_and_the_finding_section(self):
        lines = recon_text.SPEC_LINES
        self.assertEqual(len(lines), 29)
        self.assertEqual(
            lines[0],
            "  files           every file the work will EDIT. Include whatever this project",
        )
        self.assertEqual(
            hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest(),
            "7cee3ef14e50a643d280738f92ac3ed8b84877a19efeca162f27c27f57893189",
        )

    def source_line_names_the_graph_only_when_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            present = os.path.join(tmp, "graph.json")
            with open(present, "w") as handle:
                handle.write("{}")
            self.assertEqual(recon_text.source_line(None), N)
            self.assertEqual(
                recon_text.source_line(os.path.join(tmp, "missing.json")), N
            )
            self.assertEqual(
                recon_text.source_line(present),
                "A graphify graph IS available at "
                + present
                + " - query it FIRST, before grepping. It is the difference "
                "between a predicted file list and a guessed one.",
            )

    def default_prompt_is_contract_source_and_request(self):
        self.assertEqual(
            recon_text.prompt("add a reader"),
            recon_text.RECON_CONTRACT
            + "\n\n"
            + N
            + "\n\nRequest:\nadd a reader",
        )

    def template_prompt_fills_every_placeholder(self):
        template = "A<{ask}>G<{graph}>S<{source}>C<{contract}>"
        self.assertEqual(
            recon_text.prompt("add a reader", None, template, "K"),
            "A<add a reader>G<>S<" + N + ">C<K>",
        )
        self.assertTrue(
            recon_text.prompt("add a reader", None, template, None).endswith("C<>")
        )

    def environment_carries_the_ask_and_graph(self):
        self.assertEqual(
            recon_text.environment("add a reader"),
            {
                "MITOSIS_RECON": "1",
                "MITOSIS_RECON_ASK": "add a reader",
                "MITOSIS_RECON_GRAPH": "",
            },
        )
        self.assertEqual(
            recon_text.environment("x", "g.json")["MITOSIS_RECON_GRAPH"], "g.json"
        )


def load_tests(loader, tests, pattern):
    class Loader(unittest.TestLoader):
        def getTestCaseNames(self, case):
            names = [
                name
                for name, value in vars(case).items()
                if callable(value) and not name.startswith("_")
            ]
            return sorted(names)

    suite = unittest.TestSuite()
    suite.addTests(Loader().loadTestsFromTestCase(ReconText))
    return suite


if __name__ == "__main__":
    unittest.main()
