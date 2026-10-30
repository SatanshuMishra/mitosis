import json
import re

ANNOTATIONS = frozenset(("$schema", "$id", "title", "description", "$comment", "$defs"))

TYPE_CHECKS = {
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "boolean": lambda value: isinstance(value, bool),
    "null": lambda value: value is None,
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "integer": lambda value: (
        isinstance(value, int) and not isinstance(value, bool)
    ) or (isinstance(value, float) and value.is_integer()),
}


class UnsupportedKeyword(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _same(left, right):
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return _canonical(left) == _canonical(right)


def _resolve(root, reference):
    if not reference.startswith("#"):
        raise UnsupportedKeyword("only local $ref is supported, got %r" % reference)
    target = root
    for part in [p for p in reference[1:].split("/") if p]:
        key = part.replace("~1", "/").replace("~0", "~")
        target = target[int(key)] if isinstance(target, list) else target[key]
    return target


def _type(instance, expected, where):
    names = expected if isinstance(expected, list) else [expected]
    if any(TYPE_CHECKS[name](instance) for name in names):
        return []
    return ["%s: %r is not of type %s" % (where, instance, "/".join(names))]


def _properties(instance, schema, root, where):
    if not isinstance(instance, dict):
        return []
    declared = schema.get("properties") or {}
    found = []
    for key, sub in declared.items():
        if key in instance:
            found = found + _check(instance[key], sub, root, "%s/%s" % (where, key))
    extra = schema.get("additionalProperties", True)
    for key in sorted(k for k in instance if k not in declared):
        if extra is False:
            found = found + ["%s: additional property %r is not allowed" % (where, key)]
        elif isinstance(extra, dict):
            found = found + _check(instance[key], extra, root, "%s/%s" % (where, key))
    return found


def _required(instance, names, where):
    if not isinstance(instance, dict):
        return []
    return ["%s: required property %r is missing" % (where, name) for name in names if name not in instance]


def _array(instance, schema, root, where):
    if not isinstance(instance, list):
        return []
    found = []
    if "items" in schema:
        for index, item in enumerate(instance):
            found = found + _check(item, schema["items"], root, "%s/%d" % (where, index))
    if "minItems" in schema and len(instance) < schema["minItems"]:
        found = found + ["%s: %d items, fewer than %d" % (where, len(instance), schema["minItems"])]
    if "maxItems" in schema and len(instance) > schema["maxItems"]:
        found = found + ["%s: %d items, more than %d" % (where, len(instance), schema["maxItems"])]
    if schema.get("uniqueItems") is True:
        keys = [_canonical(item) for item in instance]
        if len(set(keys)) != len(keys):
            found = found + ["%s: items are not unique" % where]
    if "contains" in schema:
        matched = sum(1 for item in instance if not _check(item, schema["contains"], root, where))
        low = schema.get("minContains", 1)
        high = schema.get("maxContains")
        if matched < low:
            found = found + ["%s: %d items match contains, fewer than %d" % (where, matched, low)]
        if high is not None and matched > high:
            found = found + ["%s: %d items match contains, more than %d" % (where, matched, high)]
    return found


def _string(instance, schema, where):
    if not isinstance(instance, str):
        return []
    found = []
    if "minLength" in schema and len(instance) < schema["minLength"]:
        found = found + ["%s: %r is shorter than %d" % (where, instance, schema["minLength"])]
    if "maxLength" in schema and len(instance) > schema["maxLength"]:
        found = found + ["%s: %r is longer than %d" % (where, instance, schema["maxLength"])]
    if "pattern" in schema and re.search(schema["pattern"], instance) is None:
        found = found + ["%s: %r does not match %r" % (where, instance, schema["pattern"])]
    return found


HANDLED = frozenset(
    (
        "type",
        "properties",
        "additionalProperties",
        "required",
        "enum",
        "const",
        "items",
        "minItems",
        "maxItems",
        "uniqueItems",
        "contains",
        "minContains",
        "maxContains",
        "minLength",
        "maxLength",
        "pattern",
        "allOf",
        "not",
        "$ref",
    )
)


def _check(instance, schema, root, where):
    if schema is True:
        return []
    if schema is False:
        return ["%s: no value is allowed" % where]
    unknown = sorted(key for key in schema if key not in HANDLED and key not in ANNOTATIONS)
    if unknown:
        raise UnsupportedKeyword("the validator does not implement %s" % ", ".join(unknown))
    found = []
    if "$ref" in schema:
        found = found + _check(instance, _resolve(root, schema["$ref"]), root, where)
    if "type" in schema:
        found = found + _type(instance, schema["type"], where)
    if "enum" in schema and not any(_same(instance, option) for option in schema["enum"]):
        found = found + ["%s: %r is not one of %r" % (where, instance, schema["enum"])]
    if "const" in schema and not _same(instance, schema["const"]):
        found = found + ["%s: %r is not %r" % (where, instance, schema["const"])]
    if "required" in schema:
        found = found + _required(instance, schema["required"], where)
    found = found + _properties(instance, schema, root, where)
    found = found + _array(instance, schema, root, where)
    found = found + _string(instance, schema, where)
    for sub in schema.get("allOf") or []:
        found = found + _check(instance, sub, root, where)
    if "not" in schema and not _check(instance, schema["not"], root, where):
        found = found + ["%s: %r matches a schema it must not match" % (where, instance)]
    return found


def errors(instance, schema):
    return _check(instance, schema, schema, "$")


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)
