import fnmatch
import json
import os
import re

__version__ = "0.1.0"

PLAN_KEYS = (
    "version",
    "plan_id",
    "source",
    "items",
    "msps",
    "lanes",
    "lane_after",
    "clusters",
    "tiers",
    "verify_modes",
    "context_packs",
    "lane_order",
    "coalesce",
    "coupling_review",
    "counts",
    "briefs",
)

ITEM_FIELDS = (
    "name",
    "task",
    "files",
    "source",
    "acceptance",
    "after",
    "contract_group",
    "type",
    "complexity",
    "file_notes",
    "msp",
    "spec_ref",
    "assumptions",
)

REQUIRED_ITEM_FIELDS = (
    "name",
    "task",
    "files",
    "source",
    "acceptance",
)

ACCEPTANCE_KEYS = ("file", "test")

SOURCE_KEYS = ("path", "sha256")

LANE_STATES = ("ok", "failed", "blocked", "merge-blocked")

MSP_STATES = ("shipped", "gate-failed", "gate-inconclusive", "ship-failed")

GATE_OUTCOMES = ("pass", "inert", "inconclusive", "not-applicable")

STEP_TYPES = ("fix", "feature", "port", "design", "sweep", "contract")

COMPLEXITY_VALUES = ("simple", "complex")

TIERS = ("top", "cheap")

VERIFY_MODES = ("serial", "offload")

COUPLING_SIGNALS = (
    "import-adjacency",
    "shared-risk-marker",
    "recorded-regression",
    "same-migration-directory",
)

COUNT_KEYS = ("missing_paths", "no_acceptance", "assumptions")

RETURN_KEYS = ("item", "status", "files_changed", "notes")

NOTES_CAP = 200

RETURN_CONTRACT = (
    '{"item":"<name>","status":"ok"|"failed",'
    '"files_changed":["path",...],"notes":"<=200 chars"}'
)

FLAG_NAMES = (
    "--items",
    "--spec",
    "--charter",
    "--feature-branch",
    "--run-dir",
    "--resume",
    "--plan-only",
    "--dispatch-command",
    "--decompose-command",
    "--acceptance-command",
    "--pr-command",
    "--tier-model",
    "--timeout",
    "--concurrency",
    "--context-hops",
    "--context-cap",
    "--graph",
    "--risk-markers",
    "--serial-markers",
    "--trajectory",
    "--version",
    "--help",
)


def _norm(path):
    return os.path.normpath(str(path)).replace(os.sep, "/")


def _files(item):
    files = item.get("files")
    if not isinstance(files, list):
        return ()
    return tuple(_norm(f) for f in files if isinstance(f, str))


def _after(item):
    after = item.get("after")
    if not isinstance(after, list):
        return ()
    seen = []
    for name in after:
        if isinstance(name, str) and name not in seen:
            seen = seen + [name]
    return tuple(seen)


def _by_name(items):
    return {item.get("name"): i for i, item in enumerate(items) if "name" in item}


def _owners(groups, count):
    owner = {}
    for group_index, group in enumerate(groups):
        for member in group:
            owner[member] = group_index
    return tuple(owner.get(i) for i in range(count))


def _shared(items, keys_of):
    first = {}
    pairs = []
    for i, item in enumerate(items):
        for key in keys_of(item):
            if key in first:
                pairs.append((first[key], i))
            else:
                first[key] = i
    return tuple(pairs)


def union_find(count, pairs):
    parent = list(range(count))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in pairs:
        ra, rb = root(a), root(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    groups = {}
    for i in range(count):
        groups.setdefault(root(i), []).append(i)
    return tuple(tuple(members) for _, members in sorted(groups.items()))


def _tag(item, field):
    value = item.get(field)
    if value is None or value == "":
        return ()
    return ((field, str(value)),)


def msp_items(items):
    pairs = (
        _shared(items, _files)
        + _shared(items, lambda item: _tag(item, "contract_group"))
        + _shared(items, lambda item: _tag(item, "msp"))
    )
    return union_find(len(items), pairs)


def _pinned_groups(items):
    return frozenset(
        str(item.get("contract_group"))
        for item in items
        if item.get("type") == "contract" and item.get("contract_group") not in (None, "")
    )


def _walk_order(items, members, by_name):
    member_set = frozenset(members)
    indegree = {i: 0 for i in members}
    successors = {i: [] for i in members}
    for consumer in members:
        for name in _after(items[consumer]):
            producer = by_name.get(name)
            if producer in member_set and producer != consumer:
                indegree[consumer] += 1
                successors[producer].append(consumer)
    ready = sorted(i for i in members if indegree[i] == 0)
    order = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for consumer in successors[current]:
            indegree[consumer] -= 1
            if indegree[consumer] == 0:
                ready = sorted(ready + [consumer])
    remaining = sorted(i for i in members if i not in order)
    return tuple(order + remaining)


def lane_items(items):
    msps = msp_items(items)
    owner = _owners(msps, len(items))
    by_name = _by_name(items)
    pinned = _pinned_groups(items)
    consumers = {}
    for consumer, item in enumerate(items):
        for name in _after(item):
            producer = by_name.get(name)
            if producer is not None and producer != consumer:
                consumers[producer] = consumers.get(producer, 0) + 1
    chain_pairs = []
    for consumer, item in enumerate(items):
        producers = tuple(
            by_name[name]
            for name in _after(item)
            if name in by_name and by_name[name] != consumer
        )
        if len(producers) != 1:
            continue
        producer = producers[0]
        if owner[producer] == owner[consumer] and consumers.get(producer) == 1:
            chain_pairs.append((producer, consumer))
    pairs = (
        _shared(items, _files)
        + _shared(
            items,
            lambda item: tuple(
                key for key in _tag(item, "contract_group") if key[1] not in pinned
            ),
        )
        + tuple(chain_pairs)
    )
    groups = union_find(len(items), pairs)
    lanes = tuple(_walk_order(items, group, by_name) for group in groups)
    return tuple(sorted(lanes, key=lambda lane: (owner[lane[0]], min(lane))))


def lane_msps(items, lanes):
    owner = _owners(msp_items(items), len(items))
    return tuple(owner[lane[0]] for lane in lanes)


def lane_after(items, lanes):
    by_name = _by_name(items)
    lane_of = _owners(lanes, len(items))
    edges = {}
    for consumer_lane, lane in enumerate(lanes):
        producers = set()
        for consumer in lane:
            for name in _after(items[consumer]):
                producer = by_name.get(name)
                if producer is None:
                    continue
                if lane_of[producer] != consumer_lane:
                    producers.add(lane_of[producer])
        if producers:
            edges[consumer_lane] = tuple(sorted(producers))
    return edges


def clusters(items, lanes):
    msp_count = len(msp_items(items))
    lane_owner = lane_msps(items, lanes)
    pairs = tuple(
        (lane_owner[producer], lane_owner[consumer])
        for consumer, producers in lane_after(items, lanes).items()
        for producer in producers
        if lane_owner[producer] != lane_owner[consumer]
    )
    return union_find(msp_count, pairs)


def _label(item, index):
    name = item.get("name") if isinstance(item, dict) else None
    return name if isinstance(name, str) and name else "item #%d" % index


def _shape_errors(items):
    errors = []
    for index, item in enumerate(items):
        label = _label(item, index)
        if not isinstance(item, dict):
            errors.append("%s: a Step must be an object, got %s" % (label, type(item).__name__))
            continue
        for field in REQUIRED_ITEM_FIELDS:
            if field not in item:
                errors.append("%s: missing required field '%s'" % (label, field))
        if "name" in item and not (isinstance(item["name"], str) and item["name"]):
            errors.append("%s: 'name' must be a non-empty string" % label)
        if "task" in item and not isinstance(item["task"], str):
            errors.append("%s: 'task' must be a string" % label)
        if "files" in item:
            files = item["files"]
            if not isinstance(files, list) or not files:
                errors.append("%s: 'files' must be a non-empty list of paths" % label)
            elif not all(isinstance(f, str) and f for f in files):
                errors.append("%s: every entry of 'files' must be a path string" % label)
        if "after" in item and not (
            isinstance(item["after"], list) and all(isinstance(n, str) for n in item["after"])
        ):
            errors.append("%s: 'after' must be a list of Step names" % label)
        if "assumptions" in item and not isinstance(item["assumptions"], list):
            errors.append("%s: 'assumptions' must be a list" % label)
        if "acceptance" in item:
            errors.extend(_acceptance_errors(item["acceptance"], label))
    return errors


def _acceptance_errors(acceptance, label):
    if not isinstance(acceptance, list):
        return [
            "%s: 'acceptance' must be a list of {file, test} objects, not prose; got %s"
            % (label, type(acceptance).__name__)
        ]
    errors = []
    for position, entry in enumerate(acceptance):
        if not isinstance(entry, dict):
            errors.append(
                "%s: acceptance[%d] must be a {file, test} object, not prose" % (label, position)
            )
            continue
        for key in ACCEPTANCE_KEYS:
            if not (isinstance(entry.get(key), str) and entry.get(key)):
                errors.append(
                    "%s: acceptance[%d] must name a '%s' string" % (label, position, key)
                )
    return errors


def _duplicate_errors(items):
    seen = {}
    errors = []
    for index, item in enumerate(items):
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        if name in seen:
            errors.append(
                "duplicate name '%s' at items %d and %d" % (name, seen[name], index)
            )
        else:
            seen = {**seen, name: index}
    return errors


def _edge_errors(items, by_name):
    errors = []
    for index, item in enumerate(items):
        for name in _after(item):
            if name not in by_name:
                errors.append(
                    "%s: after names '%s', which is not a Step in this file"
                    % (_label(item, index), name)
                )
    return errors


def _reachable(start, successors):
    seen = set()
    frontier = list(successors.get(start, ()))
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        frontier.extend(successors.get(current, ()))
    return frozenset(seen)


def cycles(items):
    by_name = _by_name(items)
    successors = {}
    for consumer, item in enumerate(items):
        for name in _after(item):
            producer = by_name.get(name)
            if producer is not None:
                successors[producer] = successors.get(producer, ()) + (consumer,)
    reach = {i: _reachable(i, successors) for i in range(len(items))}
    on_cycle = sorted(i for i in range(len(items)) if i in reach[i])
    groups = []
    placed = set()
    for member in on_cycle:
        if member in placed:
            continue
        component = tuple(
            sorted(j for j in on_cycle if j == member or (j in reach[member] and member in reach[j]))
        )
        placed.update(component)
        groups.append(tuple(items[j]["name"] for j in component))
    return tuple(groups)


def _source_errors(items):
    normalized = {}
    for index, item in enumerate(items):
        key = json.dumps(item.get("source"), sort_keys=True)
        normalized.setdefault(key, []).append(_label(item, index))
    if len(normalized) <= 1:
        return []
    described = "; ".join(
        "%s declared by %s" % (key, ", ".join(labels)) for key, labels in normalized.items()
    )
    return ["every Step must declare the same source: " + described]


def _counts(items, root):
    base = root if root is not None else os.getcwd()
    missing = {
        path
        for item in items
        for path in _files(item)
        if not os.path.exists(os.path.join(base, path))
    }
    return {
        "missing_paths": len(missing),
        "no_acceptance": sum(1 for item in items if item.get("acceptance") == []),
        "assumptions": sum(1 for item in items if item.get("assumptions")),
    }


def validate(items, root=None):
    if not isinstance(items, list):
        return {
            "errors": ["the items file must be a JSON array at the top level"],
            "counts": dict.fromkeys(COUNT_KEYS, 0),
        }
    errors = _shape_errors(items)
    if errors:
        return {"errors": errors, "counts": dict.fromkeys(COUNT_KEYS, 0)}
    errors = _duplicate_errors(items)
    by_name = _by_name(items)
    errors = errors + _edge_errors(items, by_name)
    errors = errors + [
        "dependency cycle: " + " -> ".join(members + (members[0],)) for members in cycles(items)
    ]
    errors = errors + _source_errors(items)
    return {"errors": errors, "counts": _counts(items, root)}


def marker_matches(path, marker):
    if not isinstance(marker, str) or not marker:
        return False
    normalized = _norm(path)
    return fnmatch.fnmatch(normalized, marker) or marker in normalized or marker in path


def _marker_hits(paths, markers):
    return tuple(
        marker
        for marker in (markers or ())
        if any(marker_matches(path, marker) for path in paths)
    )


def _touches(record_path, path):
    a = _norm(record_path).rstrip("/")
    b = _norm(path).rstrip("/")
    return a == b or b.startswith(a + "/") or a.startswith(b + "/")


def _record_files(record):
    if not isinstance(record, dict):
        return ()
    files = record.get("files")
    if not isinstance(files, list):
        return ()
    return tuple(f for f in files if isinstance(f, str))


def _is_regression(record):
    return isinstance(record, dict) and record.get("outcome") != "ok"


def surface_history(history, paths):
    return tuple(
        record
        for record in (history or ())
        if _is_regression(record)
        and any(_touches(recorded, path) for recorded in _record_files(record) for path in paths)
    )


def trajectory_store(path):
    if not path or not os.path.isfile(path):
        return ()
    records = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
    return tuple(records)


def tier_for(write_set, risk_markers, complexity, assumptions, history=()):
    paths = tuple(write_set or ())
    mechanical = complexity == "simple" and not assumptions
    low_blast = not _marker_hits(paths, risk_markers) and not surface_history(history, paths)
    return "cheap" if mechanical and low_blast else "top"


def _numbered(path):
    return bool(re.match(r"\d+", os.path.basename(_norm(path))))


def _adjacent(graph, left, right):
    if not graph:
        return False
    for a in left:
        neighbours = graph.get(a) or graph.get(_norm(a)) or ()
        if any(_norm(n) in right for n in neighbours):
            return True
    for b in right:
        neighbours = graph.get(b) or graph.get(_norm(b)) or ()
        if any(_norm(n) in left for n in neighbours):
            return True
    return False


def _pair_signals(left, right, graph, risk_markers, history):
    signals = []
    if _adjacent(graph, left, right):
        signals.append("import-adjacency")
    if set(_marker_hits(left, risk_markers)) & set(_marker_hits(right, risk_markers)):
        signals.append("shared-risk-marker")
    if any(
        any(_touches(f, a) for f in _record_files(record) for a in left)
        and any(_touches(f, b) for f in _record_files(record) for b in right)
        for record in (history or ())
        if _is_regression(record)
    ):
        signals.append("recorded-regression")
    left_dirs = {os.path.dirname(p) for p in left if _numbered(p)}
    right_dirs = {os.path.dirname(p) for p in right if _numbered(p)}
    if left_dirs & right_dirs:
        signals.append("same-migration-directory")
    return signals


def coupling_review(items, lanes, graph, risk_markers=(), history=()):
    lane_of = _owners(lanes, len(items))
    review = []
    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            if lane_of[a] == lane_of[b]:
                continue
            left = frozenset(_files(items[a]))
            right = frozenset(_files(items[b]))
            if left & right:
                continue
            signals = _pair_signals(left, right, graph, risk_markers, history)
            if signals:
                review.append(
                    {
                        "steps": [items[a]["name"], items[b]["name"]],
                        "lanes": [lane_of[a], lane_of[b]],
                        "signals": signals,
                    }
                )
    return review


def _acceptance_files(item):
    acceptance = item.get("acceptance")
    if not isinstance(acceptance, list):
        return ()
    return tuple(
        entry["file"]
        for entry in acceptance
        if isinstance(entry, dict) and isinstance(entry.get("file"), str)
    )


def verify_modes(items, serial_markers):
    modes = {}
    for item in items:
        surface = _files(item) + _acceptance_files(item)
        serial = bool(_marker_hits(surface, serial_markers))
        modes = {**modes, item["name"]: "serial" if serial else "offload"}
    return modes
