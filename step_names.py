SUFFIX = "-2"


def unique_names(names):
    seen = set()
    result = []
    for raw in names:
        name = raw
        while name in seen:
            name += SUFFIX
        seen.add(name)
        result.append(name)
    return tuple(result)


def _has_name(step):
    return isinstance(step, dict) and isinstance(step.get("name"), str) and step["name"] != ""


def renamed(steps):
    named = [step["name"] for step in steps if _has_name(step)]
    names = iter(unique_names(named))
    result = []
    for step in steps:
        if _has_name(step):
            result.append({**step, "name": next(names)})
        else:
            result.append(step)
    return result
