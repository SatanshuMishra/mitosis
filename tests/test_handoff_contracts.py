import copy
import importlib
import importlib.util
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONTRACTS = os.path.join(ROOT, "contracts")
PROGRAM_CONTRACTS = os.path.join(os.path.expanduser("~"), ".mitosis", "harness-redesign", "contracts")

VENDORED = (
    "validator.py",
    "h8-mitosis-result.schema.json",
    "h9-mitosis-state.schema.json",
    "h10-worker-return.schema.json",
    "h10b-decompose-return.schema.json",
    "h11-interphase-record.schema.json",
    "h12-interphase-items.schema.json",
    "h15-pr-fields.schema.json",
    "h16-pr-render.schema.json",
    "k1-acceptance.schema.json",
    "k2-locked.schema.json",
    "k6-outcome.schema.json",
)

VENDORED_SCHEMAS = tuple(name for name in VENDORED if name.endswith(".schema.json"))

OLD_COPIES = (
    os.path.join("tests", "contract_schema.py"),
    os.path.join("tests", "fixtures", "contracts", "k2-locked.schema.json"),
    os.path.join("tests", "fixtures", "contracts", "k6-outcome.schema.json"),
)

OLD_IMPORT = re.compile(r"^\s*(?:import|from)\s+contract_schema\b", re.MULTILINE)

COMBINATORS = ("oneOf", "anyOf", "allOf")

VALID_RETURN = {
    "item": "add-numbers",
    "status": "ok",
    "files_changed": ["src/calc.py"],
    "notes": "done",
}

STRUCTURE = {"items": [{"name": "alpha"}], "assumptions": [], "constraints": []}


def module(name):
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    return importlib.import_module(name)


def vendored_validator():
    path = os.path.join(CONTRACTS, "validator.py")
    spec = importlib.util.spec_from_file_location("vendored_contract_validator", path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def read_schema(name):
    with open(os.path.join(CONTRACTS, name + ".schema.json"), encoding="utf-8") as handle:
        return json.load(handle)


def references(value):
    if isinstance(value, dict):
        own = (value["$ref"],) if isinstance(value.get("$ref"), str) else ()
        return own + tuple(found for member in value.values() for found in references(member))
    if isinstance(value, list):
        return tuple(found for member in value for found in references(member))
    return ()


def envelope(**members):
    return json.dumps({"type": "result", "session_id": "s", "result": "done", **members})


def joined(errors):
    return "\n".join(errors)


class VendoredContracts(unittest.TestCase):
    def every_vendored_contract_matches_its_program_copy(self):
        for name in VENDORED:
            self.assertTrue(
                os.path.isfile(os.path.join(CONTRACTS, name)), "contracts/%s is missing" % name
            )
        present = sorted(name for name in os.listdir(CONTRACTS) if name.endswith(".schema.json"))
        self.assertEqual(present, sorted(VENDORED_SCHEMAS))
        if not os.path.isdir(PROGRAM_CONTRACTS):
            return
        for name in VENDORED:
            self.assertEqual(
                read_bytes(os.path.join(CONTRACTS, name)),
                read_bytes(os.path.join(PROGRAM_CONTRACTS, name)),
                "contracts/%s differs from the program copy" % name,
            )

    def every_vendored_schema_loads_and_every_reference_resolves(self):
        validator = vendored_validator()
        names = sorted(name for name in os.listdir(CONTRACTS) if name.endswith(".schema.json"))
        self.assertEqual(names, sorted(VENDORED_SCHEMAS))
        for name in names:
            with open(os.path.join(CONTRACTS, name), encoding="utf-8") as handle:
                schema = json.load(handle)
            validator.supported(schema)
            scope = validator.Scope(CONTRACTS, schema)
            for reference in references(schema):
                validator.resolve(scope, reference)

    def the_old_test_validator_and_its_fixture_copies_are_gone(self):
        for relative in OLD_COPIES:
            self.assertFalse(os.path.exists(os.path.join(ROOT, relative)), relative)
        importers = tuple(
            os.path.relpath(os.path.join(folder, name), ROOT)
            for folder, _, names in sorted(os.walk(HERE))
            for name in sorted(names)
            if name.endswith(".py") and OLD_IMPORT.search(read_bytes(os.path.join(folder, name)).decode("utf-8", "replace"))
        )
        self.assertEqual(importers, ())


class Refusals(unittest.TestCase):
    def a_refusal_names_the_value_the_producer_the_schema_and_every_error(self):
        handoff = module("handoff")
        refused = {"locked": [], "receipt_lock": "a" * 64, "commit": "abc"}
        with self.assertRaises(handoff.HandoffError) as caught:
            handoff.require("k2-locked", refused, "the lock record", "mitosis")
        message = str(caught.exception)
        for part in ("the lock record", "mitosis", "k2-locked", "#/locked minItems", "#/commit pattern"):
            self.assertIn(part, message)
        lines = message.splitlines()
        locked_lines = [index for index, line in enumerate(lines) if "#/locked minItems" in line]
        commit_lines = [index for index, line in enumerate(lines) if "#/commit pattern" in line]
        self.assertTrue(locked_lines and commit_lines)
        self.assertTrue(set(locked_lines).isdisjoint(commit_lines))
        accepted = {"locked": ["tests/test_a.py"], "receipt_lock": "a" * 64, "commit": "0" * 40}
        kept = copy.deepcopy(accepted)
        returned = handoff.require("k2-locked", accepted, "the lock record", "mitosis")
        self.assertIs(returned, accepted)
        self.assertEqual(returned, kept)

    def the_call_schema_has_an_object_root_and_no_meta_schema(self):
        handoff = module("handoff")
        validator = vendored_validator()
        names = ("h10-worker-return", "h10b-decompose-return")
        before = {name: read_bytes(os.path.join(CONTRACTS, name + ".schema.json")) for name in names}
        called = {name: handoff.call_schema(name) for name in names}
        for name, schema in called.items():
            self.assertNotIn("$schema", schema, name)
            self.assertEqual(schema.get("type"), "object", name)
            for key in COMBINATORS:
                self.assertNotIn(key, schema, name)
        vendored = read_schema("h10-worker-return")
        self.assertEqual(
            called["h10-worker-return"], {key: value for key, value in vendored.items() if key != "$schema"}
        )
        wrapped = called["h10b-decompose-return"]
        vendored = read_schema("h10b-decompose-return")
        self.assertEqual(wrapped["required"], ["reply"])
        self.assertEqual(wrapped["properties"]["reply"]["oneOf"], vendored["oneOf"])
        self.assertEqual(wrapped["$defs"], vendored["$defs"])
        found = references(wrapped)
        self.assertTrue(found)
        for reference in found:
            self.assertTrue(reference.startswith("#"), reference)
            validator.at_pointer(wrapped, reference[1:])
        for name in names:
            self.assertEqual(read_bytes(os.path.join(CONTRACTS, name + ".schema.json")), before[name])

    def a_value_is_checked_against_a_part_of_an_owned_schema(self):
        handoff = module("handoff")
        pointer = "#/$defs/ship/properties/pull_request"
        for value in ("created", "https://example.invalid/pull/7 extra"):
            errors = handoff.errors_at("h9-mitosis-state", pointer, value)
            self.assertIsInstance(errors, tuple)
            self.assertTrue(errors, value)
            for error in errors:
                self.assertTrue(error.startswith("#"), error)
        for value in ("https://example.invalid/pull/7", ""):
            self.assertEqual(handoff.errors_at("h9-mitosis-state", pointer, value), (), value)


class ReplyReading(unittest.TestCase):
    def a_valid_structured_output_is_returned_unchanged(self):
        handoff = module("handoff")
        stdout = (
            '{"type": "result", "session_id": "s", "result": "done", "structured_output": '
            '{"item": "add-numbers", "status": "ok", "files_changed": ["src/calc.py"], "notes": "done"}}'
        )
        value, errors = handoff.read_reply(stdout, "h10-worker-return")
        self.assertEqual(value, VALID_RETURN)
        self.assertEqual(errors, ())
        value, errors = handoff.read_reply(
            envelope(structured_output={"reply": STRUCTURE}), "h10b-decompose-return"
        )
        self.assertEqual(value, STRUCTURE)
        self.assertEqual(errors, ())

    def a_null_structured_output_is_an_error_not_a_reply(self):
        handoff = module("handoff")
        value, errors = handoff.read_reply(envelope(structured_output=None), "h10-worker-return")
        self.assertIsNone(value)
        self.assertTrue(errors)
        self.assertTrue(any("structured_output" in error for error in errors), errors)

    def a_return_line_in_the_result_text_is_never_read(self):
        handoff = module("handoff")
        prose = 'Done.\n{"item": "add-numbers", "status": "ok", "files_changed": [], "notes": "done"}'
        stdout = json.dumps({"type": "result", "session_id": "s", "result": prose})
        value, errors = handoff.read_reply(stdout, "h10-worker-return")
        self.assertIsNone(value)
        self.assertIn("structured_output", joined(errors))
        self.assertNotEqual(
            value, {"item": "add-numbers", "status": "ok", "files_changed": [], "notes": "done"}
        )

    def output_that_is_not_one_json_object_is_an_error(self):
        handoff = module("handoff")
        valid = envelope(structured_output=VALID_RETURN)
        for stdout in (valid + "\n{}", "all done", "", "[]"):
            value, errors = handoff.read_reply(stdout, "h10-worker-return")
            self.assertIsNone(value, repr(stdout))
            self.assertTrue(errors, repr(stdout))
            self.assertNotEqual(value, VALID_RETURN)

    def notes_over_the_cap_are_an_error_and_never_cut(self):
        handoff = module("handoff")
        long_notes = {**VALID_RETURN, "notes": "x" * 201}
        value, errors = handoff.read_reply(
            envelope(structured_output=long_notes), "h10-worker-return"
        )
        self.assertIsNone(value)
        self.assertIn("h10-worker-return", joined(errors))
        self.assertIn("#/notes maxLength", joined(errors))
        self.assertNotIn("x" * 200, json.dumps(value))

    def an_extra_key_or_unknown_status_is_an_error(self):
        handoff = module("handoff")
        for reply, expected in (
            ({**VALID_RETURN, "extra": 1}, "#/extra additionalProperties"),
            ({**VALID_RETURN, "status": "done"}, "#/status enum"),
            ("ok", "# type"),
        ):
            value, errors = handoff.read_reply(
                envelope(structured_output=reply), "h10-worker-return"
            )
            self.assertIsNone(value, expected)
            self.assertIn(expected, joined(errors))


class Loader(unittest.TestLoader):
    def getTestCaseNames(self, case):
        return sorted(
            name
            for name in vars(case)
            if not name.startswith("_") and callable(getattr(case, name))
        )


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case in (VendoredContracts, Refusals, ReplyReading):
        suite.addTests(Loader().loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
