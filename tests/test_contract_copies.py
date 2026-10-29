import hashlib
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import contract_schema

CONTRACTS = os.path.join(HERE, "fixtures", "contracts")
K2 = os.path.join(CONTRACTS, "k2-locked.schema.json")
K6 = os.path.join(CONTRACTS, "k6-outcome.schema.json")
K2_SHA256 = "4a9d4ddc1af766f694399f6ecba699ce93344fa323e108e9197c4a68c40b0e26"
K6_SHA256 = "dec9f762cc12389c487d41f6a64a847e330403c623a88337222f539f0cf9175b"
COMMIT = "0123456789abcdef0123456789abcdef01234567"

GOOD_K2 = {"locked": ["tests/test_locked.py"], "receipt_lock": "a" * 64, "commit": COMMIT}

GOOD_K6 = {
    "repo": "mitosis",
    "surface": "tests/test_calc.py::Calc.adds",
    "surface_key": "run-one/demo",
    "outcome": "fixed",
    "files": ["tests/test_calc.py"],
    "tags": [
        "kind:gate",
        "run:run-one",
        "msp:demo",
        "commit:" + COMMIT,
        "check:C1.1=pass",
        "usd:0",
        "minutes:0.25",
    ],
}

KEYWORD_CASES = (
    ("type", {"type": "string"}, "text", 3),
    ("type integer", {"type": "integer"}, 3, True),
    ("properties", {"properties": {"a": {"type": "string"}}}, {"a": "x"}, {"a": 1}),
    ("required", {"required": ["a"]}, {"a": 1}, {"b": 1}),
    (
        "additionalProperties",
        {"properties": {"a": {}}, "additionalProperties": False},
        {"a": 1},
        {"a": 1, "b": 2},
    ),
    ("enum", {"enum": ["fixed", "reverted"]}, "fixed", "broken"),
    ("const", {"const": "gate"}, "gate", "verdict"),
    ("pattern", {"pattern": "^[0-9a-f]{4}$"}, "beef", "BEEF"),
    ("items", {"items": {"type": "string"}}, ["a", "b"], ["a", 2]),
    ("uniqueItems", {"uniqueItems": True}, ["a", "b"], ["a", "a"]),
    ("minItems", {"minItems": 1}, ["a"], []),
    ("maxItems", {"maxItems": 1}, ["a"], ["a", "b"]),
    ("minLength", {"minLength": 1}, "a", ""),
    (
        "contains with minContains",
        {"contains": {"pattern": "^kind:"}, "minContains": 1},
        ["kind:gate"],
        ["run:x"],
    ),
    (
        "contains with maxContains",
        {"contains": {"pattern": "^kind:"}, "minContains": 1, "maxContains": 1},
        ["kind:gate", "run:x"],
        ["kind:gate", "kind:verdict"],
    ),
    ("allOf", {"allOf": [{"type": "string"}, {"minLength": 2}]}, "ab", "a"),
    ("not", {"not": {"const": "x"}}, "y", "x"),
    (
        "$defs and $ref",
        {"$defs": {"word": {"type": "string"}}, "$ref": "#/$defs/word"},
        "word",
        7,
    ),
)


def sha256_of(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class ContractCopies(unittest.TestCase):
    def the_k2_copy_is_byte_identical_to_the_frozen_contract(self):
        self.assertEqual(sha256_of(K2), K2_SHA256)

    def the_k6_copy_is_byte_identical_to_the_frozen_contract(self):
        self.assertEqual(sha256_of(K6), K6_SHA256)

    def the_validator_rejects_one_wrong_sample_per_keyword(self):
        for keyword, schema, good, bad in KEYWORD_CASES:
            with self.subTest(keyword=keyword):
                self.assertEqual(contract_schema.errors(good, schema), [])
                self.assertNotEqual(contract_schema.errors(bad, schema), [])

    def the_validator_refuses_a_keyword_it_does_not_implement(self):
        with self.assertRaises(contract_schema.UnsupportedKeyword):
            contract_schema.errors(1, {"multipleOf": 2})

    def a_good_k2_record_validates_and_wrong_ones_do_not(self):
        schema = contract_schema.load(K2)
        self.assertEqual(contract_schema.errors(GOOD_K2, schema), [])
        for name, wrong in (
            ("extra key", {**GOOD_K2, "extra": 1}),
            ("missing commit", {k: v for k, v in GOOD_K2.items() if k != "commit"}),
            ("empty locked", {**GOOD_K2, "locked": []}),
            ("duplicate locked", {**GOOD_K2, "locked": ["a.py", "a.py"]}),
            ("absolute path", {**GOOD_K2, "locked": ["/etc/passwd"]}),
            ("parent path", {**GOOD_K2, "locked": ["tests/../run.py"]}),
            ("short lock", {**GOOD_K2, "receipt_lock": "a" * 63}),
            ("short commit", {**GOOD_K2, "commit": COMMIT[:12]}),
            ("not an object", ["tests/test_locked.py"]),
        ):
            with self.subTest(sample=name):
                self.assertNotEqual(contract_schema.errors(wrong, schema), [])

    def a_good_k6_entry_validates_and_wrong_ones_do_not(self):
        schema = contract_schema.load(K6)
        self.assertEqual(contract_schema.errors(GOOD_K6, schema), [])
        tags = GOOD_K6["tags"]
        for name, wrong in (
            ("extra key", {**GOOD_K6, "id": "x"}),
            ("missing tags", {k: v for k, v in GOOD_K6.items() if k != "tags"}),
            ("empty repo", {**GOOD_K6, "repo": ""}),
            ("unknown outcome", {**GOOD_K6, "outcome": "passed"}),
            ("flat surface key", {**GOOD_K6, "surface_key": "run-one"}),
            ("two kind tags", {**GOOD_K6, "tags": tags + ["kind:verdict"]}),
            ("no minutes tag", {**GOOD_K6, "tags": [t for t in tags if not t.startswith("minutes:")]}),
            ("repeated tag", {**GOOD_K6, "tags": tags + ["usd:0"]}),
            ("unknown tag", {**GOOD_K6, "tags": tags + ["note:x"]}),
            ("bad check status", {**GOOD_K6, "tags": tags[:4] + ["check:C1.1=ok"] + tags[5:]}),
            ("files not strings", {**GOOD_K6, "files": [1]}),
            ("unknown tier", {**GOOD_K6, "tier": "huge"}),
        ):
            with self.subTest(sample=name):
                self.assertNotEqual(contract_schema.errors(wrong, schema), [])


def load_tests(loader, standard, pattern):
    suite = unittest.TestSuite()
    for case, names in (
        (
            ContractCopies,
            (
                "the_k2_copy_is_byte_identical_to_the_frozen_contract",
                "the_k6_copy_is_byte_identical_to_the_frozen_contract",
                "the_validator_rejects_one_wrong_sample_per_keyword",
                "the_validator_refuses_a_keyword_it_does_not_implement",
                "a_good_k2_record_validates_and_wrong_ones_do_not",
                "a_good_k6_entry_validates_and_wrong_ones_do_not",
            ),
        ),
    ):
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
