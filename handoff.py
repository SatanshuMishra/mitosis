import importlib.util
import json
import os
import re

CONTRACTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "contracts")
SCHEMA_SUFFIX = ".schema.json"
SCHEMA_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}\Z")
COMBINATORS = ("oneOf", "anyOf", "allOf")
REPLY = "reply"
STRUCTURED = "structured_output"


class HandoffError(Exception):
    pass


def _vendored_validator():
    path = os.path.join(CONTRACTS, "validator.py")
    spec = importlib.util.spec_from_file_location("mitosis_vendored_contract_validator", path)
    if spec is None or spec.loader is None:
        raise HandoffError("the vendored validator %s cannot be loaded" % path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


validator = _vendored_validator()


def file_name(schema):
    if not isinstance(schema, str):
        raise HandoffError("a schema is named by a string, not %s" % type(schema).__name__)
    stem = schema[: -len(SCHEMA_SUFFIX)] if schema.endswith(SCHEMA_SUFFIX) else schema
    if not SCHEMA_NAME.match(stem):
        raise HandoffError("%r is not the name of an owned schema" % schema)
    name = stem + SCHEMA_SUFFIX
    if not os.path.isfile(os.path.join(CONTRACTS, name)):
        raise HandoffError("contracts/ holds no owned schema %s" % name)
    return name


def load(schema):
    return validator.read_schema(os.path.join(CONTRACTS, file_name(schema)))


def errors(schema, value):
    return tuple(validator.errors_against(CONTRACTS, file_name(schema), value))


def require(schema, value, what, producer):
    found = errors(schema, value)
    if not found:
        return value
    heading = "%s, written by %s, fails the owned schema %s:" % (what, producer, file_name(schema))
    raise HandoffError("\n".join((heading,) + found))


def _wrapped(document):
    return document.get("type") != "object" or any(key in document for key in COMBINATORS)


def call_schema(schema):
    document = load(schema)
    if not _wrapped(document):
        return {key: value for key, value in document.items() if key != "$schema"}
    reply = {key: value for key, value in document.items() if key not in ("$schema", "$id", "$defs")}
    defs = {"$defs": document["$defs"]} if "$defs" in document else {}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [REPLY],
        "properties": {REPLY: reply},
        **defs,
    }


def _unique_members(pairs):
    keys = tuple(key for key, _ in pairs)
    repeated = sorted(key for key in set(keys) if keys.count(key) > 1)
    if repeated:
        raise ValueError("member %s appears more than once in one object" % json.dumps(repeated[0]))
    return dict(pairs)


def _refusal(name, reason):
    return ("the reply cannot be read against %s: %s" % (name, reason),)


def _structured(stdout, name, wrapped):
    if not isinstance(stdout, str):
        return None, _refusal(name, "stdout is %s, not text" % type(stdout).__name__)
    try:
        envelope = json.loads(stdout.strip(), object_pairs_hook=_unique_members)
    except ValueError as error:
        return None, _refusal(name, "stdout is not one JSON object (%s)" % error)
    if not isinstance(envelope, dict):
        return None, _refusal(name, "stdout holds a JSON %s, not one JSON object" % type(envelope).__name__)
    if STRUCTURED not in envelope:
        return None, _refusal(name, "stdout's JSON object has no %s member" % STRUCTURED)
    reply = envelope[STRUCTURED]
    if reply is None:
        return None, _refusal(name, "%s is null" % STRUCTURED)
    if not wrapped:
        return reply, ()
    if not isinstance(reply, dict) or REPLY not in reply:
        return None, _refusal(name, "%s holds no %s member" % (STRUCTURED, REPLY))
    extra = sorted(key for key in reply if key != REPLY)
    if extra:
        return None, _refusal(name, "%s holds member %s beside %s" % (STRUCTURED, json.dumps(extra[0]), REPLY))
    return reply[REPLY], ()


def read_reply(stdout, schema):
    name = file_name(schema)
    reply, refused = _structured(stdout, name, _wrapped(load(schema)))
    if refused:
        return None, refused
    found = errors(name, reply)
    if found:
        return None, ("%s does not meet the owned schema %s:" % (STRUCTURED, name),) + found
    return reply, ()


def errors_at(schema, pointer, value):
    name = file_name(schema)
    if not isinstance(pointer, str) or not pointer.startswith("#"):
        raise HandoffError("%r is not a JSON pointer inside %s" % (pointer, name))
    scope = validator.Scope(CONTRACTS, load(name))
    try:
        part_scope, part = validator.resolve(scope, pointer)
    except (KeyError, IndexError, ValueError, TypeError) as error:
        raise HandoffError("%s holds nothing at %s (%s)" % (name, pointer, error)) from error
    validator.supported(part)
    return tuple(validator.check(part, value, "", part_scope))


def reask_prompt(schema, errors):
    name = file_name(schema)
    listed = tuple(errors)
    if not listed or not all(isinstance(error, str) and error for error in listed):
        raise HandoffError("a re-ask against %s needs one or more error strings" % name)
    return "\n".join(
        ("Your previous reply failed the owned schema %s, with these errors:" % name,)
        + listed
        + ("Return your reply again now, as structured output that meets %s." % name,)
    )
