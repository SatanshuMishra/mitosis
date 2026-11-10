import collections
import fnmatch
import hashlib
import itertools
import json
import os
import re
import uuid

import referee

__version__ = "0.18.0"

PLAN_KEYS = (
    "plan_id",
    "source",
    "locked",
    "items",
    "msps",
    "lanes",
    "lane_after",
    "tiers",
    "tier_notes",
    "verify_modes",
    "context_packs",
    "shared_context",
    "lane_order",
    "coalesce",
    "coupling_review",
    "verdict_groups",
    "counts",
    "shared_files",
    "integration_order",
    "shared_file_candidates",
    "briefs",
    "check_authors",
)

BRIEF_KEYS = ("briefs", "check_authors")

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
    "role",
)

REQUIRED_ITEM_FIELDS = (
    "name",
    "task",
    "files",
    "source",
    "acceptance",
)


ACCEPTANCE_KEYS = ("file", "test")

CHECK_AUTHOR = "check-author"

ROLES = (CHECK_AUTHOR,)

CHECK_AUTHOR_LANE = "check-author"

LOCK_MSP = "lock"

SOURCE_KEYS = ("path", "sha256")

LANE_STATES = ("ok", "failed", "blocked", "merge-blocked", "held", "parked-limit")

MSP_STATES = (
    "shipped",
    "pushed",
    "unchanged",
    "committed",
    "ship-failed",
    "blocked",
    "referee-reverted",
    "gate-failed",
    "parked",
)

DELIVERED_STATES = ("shipped", "pushed", "unchanged", "committed")

GATE_OUTCOMES = ("pass", "weak", "inert", "inconclusive", "not-applicable")

GUARD_REASON = "passes with and without the work, so it guards behaviour that must stay the same"


def is_guard(judged):
    return judged.get("outcome") == "not-applicable" and judged.get("reverted") == 0

STEP_TYPES = ("fix", "feature", "port", "design", "sweep", "contract")

COMPLEXITY_VALUES = ("simple", "complex")

TIERS = ("top", "cheap")

VERIFY_MODES = ("serial", "offload")

COUNT_KEYS = ("missing_paths", "no_acceptance", "assumptions")

RETURN_KEYS = ("item", "status", "files_changed", "notes")

NOTES_CAP = 150

RETURN_SCHEMA = "h10-worker-return"

RETURN_CONTRACT = (
    "Return your reply as structured output meeting the Worker return schema %s, "
    'shaped {"item":"<name>","status":"ok"|"failed","files_changed":["path",...],'
    '"notes":"<=%d chars"}, with notes at most %d characters; it is read from '
    "structured_output, never from a last JSON line." % (RETURN_SCHEMA, NOTES_CAP, NOTES_CAP)
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
    "--reask-command",
    "--decompose-command",
    "--acceptance-command",
    "--gate-setup",
    "--pr-command",
    "--pr-type",
    "--pr-scope",
    "--pr-subject",
    "--pr-why",
    "--pr-door",
    "--pr-blast-radius",
    "--no-push",
    "--tier-model",
    "--timeout",
    "--concurrency",
    "--context-hops",
    "--context-cap",
    "--coalesce-below",
    "--coalesce-max",
    "--graph",
    "--risk-markers",
    "--shared-files",
    "--serial-verify-markers",
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


def shared_matcher(shared_files=()):
    entries = tuple(shared_files or ())

    def matches(path):
        return any(
            path == entry or path.endswith("/" + entry) or fnmatch.fnmatchcase(path, entry)
            for entry in entries
        )

    return matches


def _unshared_files(shared_files):
    shared = shared_matcher(shared_files)
    return lambda item: tuple(path for path in _files(item) if not shared(path))


def msp_items(items, shared_files=()):
    pairs = (
        _shared(items, _unshared_files(shared_files))
        + _shared(items, lambda item: _tag(item, "contract_group"))
        + _shared(items, lambda item: _tag(item, "msp"))
    )
    return union_find(len(items), pairs)


def _conflict_adjacency(items, shared_files=()):
    shared = shared_matcher(shared_files)
    names = [it["name"] for it in items]
    files = {it["name"]: {path for path in it["files"] if not shared(path)} for it in items}
    group = {it["name"]: it.get("contract_group") for it in items}
    adj = {n: set() for n in names}
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            if files[a] & files[b] or (group[a] and group[a] == group[b]):
                adj[a].add(b)
                adj[b].add(a)
    return adj


def downstream_cost(items, cost):
    after = {it["name"]: list(it.get("after") or []) for it in items}
    names = list(after)
    consumers, outdeg = collections.defaultdict(list), {n: 0 for n in names}
    for n, deps in after.items():
        for d in deps:
            consumers[d].append(n)
            outdeg[d] += 1
    dc = {n: cost.get(n, 1.0) for n in names}
    queue = [n for n in names if outdeg[n] == 0]
    qi = 0
    while qi < len(queue):
        n = queue[qi]
        qi += 1
        for p in after[n]:
            dc[p] = max(dc[p], cost.get(p, 1.0) + dc[n])
            outdeg[p] -= 1
            if outdeg[p] == 0:
                queue.append(p)
    return dc


def global_waves(items, adj, order_index, priority=None):
    after = {it["name"]: list(it.get("after") or []) for it in items}
    names = [it["name"] for it in items]
    consumers = collections.defaultdict(list)
    indeg = {n: len(after[n]) for n in names}
    for n, deps in after.items():
        for d in deps:
            consumers[d].append(n)
    floor = {n: 0 for n in names}
    queue = [n for n in names if indeg[n] == 0]
    qi = 0
    while qi < len(queue):
        n = queue[qi]
        qi += 1
        for c in consumers[n]:
            floor[c] = max(floor[c], floor[n] + 1)
            indeg[c] -= 1
            if indeg[c] == 0:
                queue.append(c)
    if qi != len(names):
        raise ValueError("`after` cycle involving: %s" % sorted(n for n in names if indeg[n] > 0))
    members = set(names)
    priority = priority or {}
    verts = sorted(
        names,
        key=lambda n: (floor[n], -priority.get(n, 0.0), -len(adj.get(n, set()) & members), order_index[n]),
    )
    wave_of = {}
    for v in verts:
        used = {wave_of[u] for u in adj.get(v, set()) if u in wave_of}
        w = max([floor[v]] + [wave_of[p] + 1 for p in after[v] if p in wave_of])
        while w in used:
            w += 1
        wave_of[v] = w
    waves = [[] for _ in range(max(wave_of.values()) + 1 if wave_of else 0)]
    for v in names:
        waves[wave_of[v]].append(v)
    for w in waves:
        w.sort(key=lambda n: (-priority.get(n, 0.0), -len(adj.get(n, set()) & members), order_index[n]))
    return [w for w in waves if w]


def ready_after(items, waves, shared_files=()):
    after = {it["name"]: list(it.get("after") or []) for it in items}
    adj = _conflict_adjacency(items, shared_files)
    wave_of = {n: i for i, w in enumerate(waves) for n in w}
    return {
        it["name"]: sorted(
            set(after[it["name"]])
            | {m for m in adj.get(it["name"], ()) if wave_of.get(m, -1) < wave_of.get(it["name"], 0)}
        )
        for it in items
    }


def coalesce_groups(items, waves, tier, cost, below=2.0, max_size=4, deps=None):
    deps = deps or {}
    out = {}
    for i, w in enumerate(waves):
        small = [n for n in w if tier.get(n) != "top" and cost.get(n, 1.0) <= below]
        by_deps = collections.defaultdict(list)
        for n in small:
            by_deps[tuple(deps.get(n, ()))].append(n)
        groups = []
        for key in sorted(by_deps):
            bucket = by_deps[key]
            groups += [g for g in (bucket[k:k + max_size] for k in range(0, len(bucket), max_size)) if len(g) > 1]
        if groups:
            out[str(i)] = groups
    return out


def _first_build_steps(items):
    first = {}
    for item in items:
        name = item.get("name")
        if isinstance(name, str) and name not in first and not _is_check_author(item):
            first = {**first, name: item}
    return first


def build_waves(items, shared_files=()):
    first = _first_build_steps(items)
    looped = frozenset(name for group in cycles(list(first.values())) for name in group)
    names = tuple(name for name in first if name not in looped)
    known = frozenset(names)
    steps = [
        {
            "name": name,
            "files": list(_files(first[name])),
            "after": [producer for producer in _after(first[name]) if producer in known and producer != name],
            "contract_group": first[name].get("contract_group"),
        }
        for name in names
    ]
    cost = {name: item_cost(first[name]) for name in names}
    order_index = {name: index for index, name in enumerate(names)}
    waves = global_waves(
        steps, _conflict_adjacency(steps, shared_files), order_index, downstream_cost(steps, cost)
    )
    return steps, cost, waves


def wave_sequence(items, shared_files=()):
    _, _, waves = build_waves(items, shared_files)
    return {n: i for i, n in enumerate(n for w in waves for n in w)}


def _in_sequence(items, members, seq):
    def key(index):
        name = items[index].get("name")
        return (seq.get(name, len(seq)) if isinstance(name, str) else len(seq), index)

    return tuple(sorted(members, key=key))


def _within_one_msp(component, lanes, owner):
    return len({owner[lanes[lane][0]] for lane in component}) == 1


def _contract_within_msp(items, lanes, owner, seq):
    while True:
        components = [
            component
            for component in lane_cycles(items, lanes)
            if len(component) > 1 and _within_one_msp(component, lanes, owner)
        ]
        if not components:
            return lanes
        merged = set()
        rebuilt = []
        for component in components:
            members = tuple(index for lane in component for index in lanes[lane])
            rebuilt.append(_in_sequence(items, members, seq))
            merged.update(component)
        kept = [lane for index, lane in enumerate(lanes) if index not in merged]
        lanes = tuple(kept + rebuilt)


def _group_members(items):
    members = {}
    for index, item in enumerate(items):
        for _, group in _tag(item, "contract_group"):
            members = {**members, group: members.get(group, ()) + (index,)}
    return members


def _waits_on(items, start, target, by_name):
    seen = frozenset()
    frontier = (start,)
    while frontier:
        reached = tuple(
            by_name[name]
            for index in frontier
            for name in _after(items[index])
            if name in by_name and by_name[name] not in seen
        )
        if target in reached:
            return True
        seen = seen | frozenset(reached)
        frontier = tuple(dict.fromkeys(reached))
    return False


def _pins(items, members):
    producers = frozenset(items[index].get("name") for index in members if items[index].get("type") == "contract")
    consumers = tuple(index for index in members if items[index].get("name") not in producers)
    return (
        bool(producers)
        and bool(consumers)
        and all(producers & frozenset(_after(items[index])) for index in consumers)
    )


def pinned_groups(items):
    return frozenset(group for group, members in _group_members(items).items() if _pins(items, members))


def _interface_pairs(items):
    pinned = pinned_groups(items)
    return _shared(
        items,
        lambda item: tuple(tag for tag in _tag(item, "contract_group") if tag[1] not in pinned),
    )


def _lane_file_pairs(items, owner, shared_files):
    shared = shared_matcher(shared_files)
    keys = tuple(
        tuple((path, owner[index]) if shared(path) else path for path in _files(item))
        for index, item in enumerate(items)
    )
    return _shared(tuple(range(len(items))), lambda index: keys[index])


def lane_pairs(items, shared_files=()):
    owner = _owners(msp_items(items, shared_files), len(items))
    by_name = _by_name(items)
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
    return _lane_file_pairs(items, owner, shared_files), tuple(chain_pairs), _interface_pairs(items)


def _uncontracted(items, owner, seq, shared_files=()):
    shared, chained, interfaced = lane_pairs(items, shared_files)
    groups = union_find(len(items), shared + chained + interfaced)
    lanes = tuple(_in_sequence(items, group, seq) for group in groups)
    return tuple(sorted(lanes, key=lambda lane: (owner[lane[0]], min(lane))))


def uncontracted_lanes(items, shared_files=()):
    return _uncontracted(
        items,
        _owners(msp_items(items, shared_files), len(items)),
        wave_sequence(items, shared_files),
        shared_files,
    )


def lane_items(items, shared_files=()):
    owner = _owners(msp_items(items, shared_files), len(items))
    seq = wave_sequence(items, shared_files)
    lanes = _contract_within_msp(items, _uncontracted(items, owner, seq, shared_files), owner, seq)
    return tuple(sorted(lanes, key=lambda lane: (owner[lane[0]], min(lane))))


def lane_msps(items, lanes, shared_files=()):
    owner = _owners(msp_items(items, shared_files), len(items))
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


def clusters(items, lanes, shared_files=()):
    msp_count = len(msp_items(items, shared_files))
    lane_owner = lane_msps(items, lanes, shared_files)
    pairs = tuple(
        (lane_owner[producer], lane_owner[consumer])
        for consumer, producers in lane_after(items, lanes).items()
        for producer in producers
        if lane_owner[producer] != lane_owner[consumer]
    )
    return union_find(msp_count, pairs)


def _msp_producers(items, msps):
    owner = _owners(msps, len(items))
    by_name = _by_name(items)
    return tuple(
        frozenset(
            owner[by_name[name]]
            for member in msp
            for name in _after(items[member])
            if name in by_name and owner[by_name[name]] not in (None, index)
        )
        for index, msp in enumerate(msps)
    )


def _msp_chains(items, msps):
    known = frozenset(_by_name(items))
    steps = [
        {
            "name": item["name"],
            "after": [name for name in _after(item) if name in known and name != item["name"]],
        }
        for item in items
    ]
    reach = downstream_cost(steps, {item["name"]: item_cost(item) for item in items})
    return tuple(max((reach[items[member]["name"]] for member in msp), default=0.0) for msp in msps)


def _msp_levels(producers):
    levels = [0] * len(producers)
    waiting = [len(needed) for needed in producers]
    finished = [index for index, count in enumerate(waiting) if not count]
    for producer in finished:
        for consumer, needed in enumerate(producers):
            if producer in needed:
                levels[consumer] = max(levels[consumer], levels[producer] + 1)
                waiting[consumer] -= 1
                if not waiting[consumer]:
                    finished.append(consumer)
    return tuple(levels)


def msp_rank(items, msps):
    levels = _msp_levels(_msp_producers(items, msps))
    chains = _msp_chains(items, msps)
    return tuple(sorted(range(len(msps)), key=lambda index: (levels[index], -chains[index], index)))


def integration_order(items, msps, shared_files=()):
    shared = shared_matcher(shared_files)
    editors = {}
    for index in msp_rank(items, msps):
        for path in sorted({path for member in msps[index] for path in _files(items[member]) if shared(path)}):
            editors = {**editors, path: editors.get(path, ()) + (index,)}
    return {path: list(found) for path, found in sorted(editors.items()) if len(found) > 1}


def shared_file_candidates(items, shared_files=(), min_items=3, cap=20):
    declared = tuple(shared_files or ())
    shared = shared_matcher(declared)
    counts = collections.Counter(path for item in items for path in frozenset(_files(item)))
    walk = sorted(
        ((path, count) for path, count in counts.items() if count >= min_items and not shared(path)),
        key=lambda pair: (-pair[1], pair[0]),
    )
    msps_now = len(msp_items(items, declared))
    trial = declared
    previous = msps_now
    files = ()
    reach = 0
    for path, count in walk:
        trial = trial + (path,)
        after = len(msp_items(items, trial))
        files = files + ({"file": path, "items": count, "msps_after": after},)
        reach = len(files) if after > previous else reach
        previous = after
    if not reach:
        return None
    return {"msps_now": msps_now, "files": list(files[:reach][:cap])}


def _label(item, index):
    name = item.get("name") if isinstance(item, dict) else None
    return name if isinstance(name, str) and name else "item #%d" % index


def _shape_errors(items, required):
    errors = []
    for index, item in enumerate(items):
        label = _label(item, index)
        if not isinstance(item, dict):
            errors.append("%s: a Step must be an object, got %s" % (label, type(item).__name__))
            continue
        for field in required:
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
            sorted(
                j
                for j in on_cycle
                if j == member or (j in reach[member] and member in reach[j])
            )
        )
        placed.update(component)
        groups.append(tuple(items[j]["name"] for j in component))
    return tuple(groups)


def lane_cycles(items, lanes):
    edges = lane_after(items, lanes)
    successors = {}
    for consumer, producers in edges.items():
        for producer in producers:
            successors[producer] = successors.get(producer, ()) + (consumer,)
    reach = {index: _reachable(index, successors) for index in range(len(lanes))}
    on_cycle = sorted(index for index in range(len(lanes)) if index in reach[index])
    found = []
    placed = set()
    for member in on_cycle:
        if member in placed:
            continue
        component = tuple(
            sorted(
                other
                for other in on_cycle
                if other == member or (other in reach[member] and member in reach[other])
            )
        )
        placed.update(component)
        found.append(component)
    return tuple(found)


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


def _is_check_author(item):
    return item.get("role") == CHECK_AUTHOR


def _check_author_label(item):
    value = item.get("msp")
    unlabelled = value is None or value == "" or item.get("name") == CHECK_AUTHOR
    return None if unlabelled else str(value)


def _role_errors(items):
    unknown = [
        "%s: role %r is not one of %s" % (_label(item, index), item["role"], ", ".join(ROLES))
        for index, item in enumerate(items)
        if "role" in item and item["role"] not in ROLES
    ]
    authors = [item for item in items if _is_check_author(item)]
    unlabelled = [item for item in authors if _check_author_label(item) is None]
    several = (
        [
            "more than one Step has role %s and no msp: %s"
            % (CHECK_AUTHOR, ", ".join(_label(item, index) for index, item in enumerate(unlabelled)))
        ]
        if len(unlabelled) > 1
        else []
    )
    labels = tuple(
        label
        for label in dict.fromkeys(_check_author_label(item) for item in authors)
        if label is not None
    )
    shared_label = [
        "%s: %d %s Steps carry msp %s, and one MSP's label holds one check-author"
        % (", ".join(names), len(names), CHECK_AUTHOR, label)
        for label in labels
        for names in (
            [
                _label(item, index)
                for index, item in enumerate(authors)
                if _check_author_label(item) == label
            ],
        )
        if len(names) > 1
    ]
    declaring = [
        "%s: a %s Step writes the locked checks and declares no acceptance entries, but it "
        "declares %d" % (_label(item, index), CHECK_AUTHOR, len(item.get("acceptance") or ()))
        for index, item in enumerate(authors)
        if item.get("acceptance")
    ]
    owner = {}
    twice = []
    for index, item in enumerate(authors):
        for path in _files(item):
            if path in owner and owner[path] != _label(item, index):
                twice = twice + [
                    "%s: lists %s in its files, which the %s Step %s also writes"
                    % (_label(item, index), path, CHECK_AUTHOR, owner[path])
                ]
            else:
                owner = {**owner, path: owner.get(path, _label(item, index))}
    shared = [
        "%s: lists %s in its files, which the %s Step %s owns as a locked check"
        % (_label(item, index), path, CHECK_AUTHOR, owner[path])
        for index, item in enumerate(items)
        if not _is_check_author(item)
        for path in _files(item)
        if path in owner
    ]
    return unknown + several + shared_label + declaring + twice + shared


def check_author_steps(items):
    return tuple(item for item in items if _is_check_author(item))


def without_check_author(items):
    authors = frozenset(item.get("name") for item in items if _is_check_author(item))
    kept = [item for item in items if not _is_check_author(item)]
    if not authors:
        return kept
    return [
        _without_after(item, authors) if any(name in authors for name in _after(item)) else item
        for item in kept
    ]


def _without_after(item, names):
    after = [name for name in item["after"] if name not in names]
    if after:
        return {**item, "after": after}
    return {key: value for key, value in item.items() if key != "after"}


def with_task(item):
    if isinstance(item, dict) and "text" in item:
        return {**item, "task": item.get("task") or item["text"]}
    return item


def validate(items, root=None, required=None):
    if not isinstance(items, list):
        return {
            "errors": ["the items file must be a JSON array at the top level"],
            "counts": dict.fromkeys(COUNT_KEYS, 0),
        }
    items = [with_task(item) for item in items]
    errors = _shape_errors(items, REQUIRED_ITEM_FIELDS if required is None else required)
    if errors:
        return {"errors": errors, "counts": dict.fromkeys(COUNT_KEYS, 0)}
    errors = _duplicate_errors(items) + _role_errors(items)
    built = without_check_author(items)
    by_name = _by_name(built)
    errors = errors + _edge_errors(built, by_name)
    errors = errors + [
        "dependency cycle: " + " -> ".join(members + (members[0],)) for members in cycles(built)
    ]
    errors = errors + _source_errors(items)
    return {"errors": errors, "counts": _counts(built, root)}


GLOB_CHARACTERS = "*?["

DEFAULT_TRAJECTORY_STORE = os.environ.get(
    "MITOSIS_TRAJECTORY_STORE",
    os.path.expanduser("~/.claude/mcp-servers/trajectory-kb/data/trajectories.jsonl"),
)

CONTEXT_CAP = 40

WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")


def path_words(path):
    return {
        word.lower()
        for part in re.split(r"[^A-Za-z0-9]+", path)
        if part
        for word in WORD.findall(part)
    }


def risk_marker_matches(path, marker):
    if not isinstance(marker, str) or not marker.strip():
        return False
    normalized = _norm(path)
    candidate = marker.strip().lower()
    if not candidate.isalnum():
        return candidate in normalized.lower()
    return candidate in path_words(normalized)


def marker_matches(path, marker):
    if isinstance(marker, str) and any(character in marker for character in GLOB_CHARACTERS):
        return fnmatch.fnmatchcase(_norm(path).lower(), marker.strip().lower())
    return risk_marker_matches(path, marker)


def _paths_match(a, b):
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def _basename(p):
    return p.rsplit("/", 1)[-1]


def _ref_matches_item(ref, item):
    if not ref:
        return False
    r = str(ref).lower()
    for f in item["files"]:
        if _paths_match(str(ref), f):
            return True
        bn = _basename(f).lower()
        if bn and bn in r:
            return True
        stem = bn.rsplit(".", 1)[0]
        if len(stem) >= 4 and stem in r:
            return True
    nm = item["name"].lower()
    return len(nm) >= 4 and nm in r


def _entry_matches_item(entry, item):
    for ef in (entry.get("files") or []):
        for itf in item["files"]:
            if _paths_match(str(ef), itf):
                return True
    hay = (entry.get("surface") or "") + " " + " ".join(entry.get("tags") or [])
    return _ref_matches_item(hay, item)


def _entry_regresses_item(entry, target_item):
    return any(_ref_matches_item(r, target_item) for r in (entry.get("regressed") or []))


def history_tier_bump(item, trajectories):
    matched = [e for e in trajectories if _entry_matches_item(e, item)]
    if not matched:
        return False, ""
    reverts = sum(1 for e in matched if e.get("outcome") == "reverted")
    spec = sum(1 for e in matched if e.get("outcome") == "speculative")
    regressed = sum(1 for e in matched if (e.get("regressed") or []))
    traps = sum(1 for e in matched if (e.get("what_failed") or []))
    parts = (
        (("%d reverted" % reverts,) if reverts else ())
        + (("%d speculative" % spec,) if spec else ())
        + (("%d caused-regression" % regressed,) if regressed else ())
        + (("%d prior traps" % traps,) if traps >= 2 else ())
    )
    return bool(parts), ", ".join(parts)


def _regression_links(left, right):
    return any(_entry_regresses_item(e, right["item"]) for e in left["matched"]) or any(
        _entry_regresses_item(e, left["item"]) for e in right["matched"]
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
    superseded = {record.get("supersedes") for record in records if record.get("supersedes")}
    return tuple(record for record in records if record.get("id") not in superseded)


def tier_for(file_paths, risk_markers, complexity=None):
    for f in file_paths:
        if any(risk_marker_matches(f, m) for m in risk_markers):
            return "top"
    if complexity is not None and str(complexity).lower() not in ("simple", "low", "mechanical"):
        return "top"
    return "cheap"


def _tiered(item, risk_markers, history):
    tier = tier_for(_files(item), risk_markers, item.get("complexity"))
    if tier == "top" or not history:
        return tier, None
    bump, reason = history_tier_bump(item, history)
    return ("top", "history: " + reason) if bump else (tier, None)


def _item_graph_files(file_paths, adj_keys):
    return {k for f in file_paths for k in adj_keys if _paths_match(f, k)}


def _migration_apps(file_paths):
    return {
        norm.split("migrations/")[0].rstrip("/")
        for norm in (f.replace("\\", "/") for f in file_paths)
        if "migrations/" in norm and norm.endswith(".py")
    }


def _file_coupling(graph):
    linked = sorted(
        {
            pair
            for path in (graph or {})
            for neighbour in _neighbours(graph, path)
            if neighbour != _norm(path)
            for pair in ((_norm(path), neighbour), (neighbour, _norm(path)))
        }
    )
    return {
        path: frozenset(neighbour for _, neighbour in group)
        for path, group in itertools.groupby(linked, key=lambda pair: pair[0])
    }


def _coupling_side(item, adj_keys, history):
    files = frozenset(_files(item))
    return {
        "item": item,
        "files": files,
        "graph_files": _item_graph_files(files, adj_keys),
        "matched": tuple(e for e in history if _entry_matches_item(e, item)),
    }


def _pair_signals(left, right, graph, risk_markers):
    adjacent = any(
        bf in graph.get(af, ()) for af in left["graph_files"] for bf in right["graph_files"]
    )
    shared = sorted(
        {
            m
            for m in risk_markers
            if any(risk_marker_matches(f, m) for f in left["files"])
            and any(risk_marker_matches(f, m) for f in right["files"])
        }
    )
    regression = _regression_links(left, right)
    mig = sorted(_migration_apps(left["files"]) & _migration_apps(right["files"]))
    signals = (
        (("import-adjacent",) if adjacent else ())
        + tuple("shared-risk-marker:" + m for m in shared)
        + (("regression-history",) if regression else ())
        + tuple("same-migration-app:" + (m or "<root>") for m in mig)
    )
    return list(signals), "serialize" if (shared or regression or mig) else "parallel"


def _ordered(items, a, b, by_name):
    return _waits_on(items, a, b, by_name) or _waits_on(items, b, a, by_name)


def _shared_file_signals(overlap, shared):
    return tuple("shared-file:" + path for path in sorted(overlap) if shared(path))


def coupling_review(items, lanes, graph, risk_markers=(), history=(), shared_files=()):
    shared = shared_matcher(shared_files)
    lane_of = _owners(lanes, len(items))
    by_name = _by_name(items)
    adjacency = _file_coupling(graph)
    adj_keys = list(adjacency)
    sides = [_coupling_side(item, adj_keys, history or ()) for item in items]
    review = []
    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            overlap = sides[a]["files"] & sides[b]["files"]
            if lane_of[a] == lane_of[b] or any(not shared(path) for path in overlap):
                continue
            found, default = _pair_signals(sides[a], sides[b], adjacency, risk_markers or ())
            signals = found + list(_shared_file_signals(overlap, shared))
            if not signals or _ordered(items, a, b, by_name):
                continue
            review.append(
                {
                    "steps": [items[a]["name"], items[b]["name"]],
                    "lanes": [lane_of[a], lane_of[b]],
                    "signals": signals,
                    "default": default,
                }
            )
    return review


def verdict_groups(review):
    buckets = {}
    for e in review:
        buckets.setdefault((tuple(e["signals"]), e["default"]), []).append(e["steps"])
    out = [
        {"signals": list(sig), "default": dflt, "count": len(pairs), "pairs": pairs}
        for (sig, dflt), pairs in buckets.items()
    ]
    return sorted(out, key=lambda g: (-g["count"], g["signals"]))


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
    return {
        it["name"]: ("serial" if any(m in f for m in serial_markers for f in it["files"]) else "offload")
        for it in items
    }


def item_cost(item):
    explicit = item.get("cost")
    if isinstance(explicit, (int, float)) and not isinstance(explicit, bool) and explicit > 0:
        return explicit
    return max(1, len(_files(item)))


def lane_cost(items, lane):
    return sum(item_cost(items[i]) for i in lane)


def build_file_coupling(nodes_by_id, links):
    adj = collections.defaultdict(set)
    for ln in links:
        s = nodes_by_id.get(ln.get("source"))
        t = nodes_by_id.get(ln.get("target"))
        if not s or not t:
            continue
        sf, tf = s.get("source_file"), t.get("source_file")
        if sf and tf and sf != tf:
            adj[sf].add(tf)
            adj[tf].add(sf)
    return dict(adj)


def _neighbours(adjacency, path):
    if not adjacency:
        return ()
    raw = adjacency.get(path)
    if raw is None:
        raw = adjacency.get(_norm(path))
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(_norm(n) for n in raw if isinstance(n, str))


def context_packs(items, adj, hops=1, cap=CONTEXT_CAP):
    packs = {}
    adj_keys = list(adj or {})
    for it in items:
        own = list(dict.fromkeys(it["files"]))
        anchors = _item_graph_files(set(own), adj_keys) if adj else set()
        seen = set(own) | anchors
        frontier = anchors
        for _ in range(max(0, hops)):
            nxt = set()
            for f in frontier:
                nxt |= set(adj.get(f, ()))
            frontier = nxt - seen
            if not frontier:
                break
            seen |= frontier
        pack = {"edit": own}
        neighbours = sorted(seen - set(own) - anchors)
        if neighbours:
            pack["read"] = neighbours[:cap]
            if len(neighbours) > cap:
                pack["read_truncated"] = len(neighbours) - cap
        packs[it["name"]] = pack
    return packs


def shared_context(packs, waves):
    out = {}
    for i, w in enumerate(waves):
        counts = collections.defaultdict(int)
        for n in w:
            for f in packs.get(n, {}).get("read", []):
                counts[f] += 1
        shared = sorted(f for f, c in counts.items() if c > 1)
        if shared:
            out[str(i)] = shared
    return out


def _dependents(lane_count, edges):
    consumers = {}
    for consumer, producers in (edges or {}).items():
        for producer in producers:
            consumers[int(producer)] = consumers.get(int(producer), ()) + (int(consumer),)
    return tuple(len(_reachable(lane, consumers)) for lane in range(lane_count))


def lane_order(lanes, edges, items):
    waiting = _dependents(len(lanes), edges)
    costs = tuple(lane_cost(items, lane) for lane in lanes)
    return tuple(
        sorted(range(len(lanes)), key=lambda lane: (-waiting[lane], -costs[lane], lane))
    )


class ValidationError(ValueError):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = tuple(errors)


def plan_id_for(plan):
    body = {key: value for key, value in plan.items() if key != "plan_id"}
    encoded = json.dumps(body, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def _msp_label(items, msp):
    tags = tuple(str(items[i]["msp"]) for i in msp if items[i].get("msp") not in (None, ""))
    return tags[0] if tags else items[msp[0]]["name"]


def _observable(item):
    observable = item.get("observable")
    return isinstance(observable, str) and observable != ""


def _step_brief(item, pack, edit=None):
    files = list(edit) if edit else list(pack.get("edit") or item.get("files") or [])
    notes = {path: note for path, note in (item.get("file_notes") or {}).items() if path in files}
    return {
        "name": item["name"],
        "task": item.get("task") or item.get("text") or item["name"],
        "edit": files,
        "read": list(pack.get("read") or []),
        **({"acceptance": item["acceptance"]} if item.get("acceptance") else {}),
        **({"observable": item["observable"]} if _observable(item) else {}),
        **({"file_notes": notes} if notes else {}),
        **({"assumptions": item["assumptions"]} if item.get("assumptions") else {}),
    }


def whole_spec(leaves):
    return json.dumps({"leaves": list(leaves)}, indent=2)


def brief_spec(leaves):
    return whole_spec(
        [{key: value for key, value in leaves[0].items() if key != "task"}]
        if len(leaves) == 1
        else leaves
    )


def _lane_read_set(items, lane, packs, owned=()):
    write_set = frozenset(path for i in lane for path in _files(items[i])) | frozenset(owned)
    read = tuple(
        dict.fromkeys(
            path
            for i in lane
            for path in packs.get(items[i]["name"], {}).get("read", ())
            if path not in write_set
        )
    )
    return list(read), sum(packs.get(items[i]["name"], {}).get("read_truncated", 0) for i in lane)


CLONE_LINE = (
    "You build in a clone of your own that no other Worker shares. Change only the files in your "
    "write-set: when you return, mitosis copies them from the clone's working tree and commits "
    "them, and it never reads the clone's commits, branches or other refs, so do not rely on "
    "them. Run no command that changes the repository's index or history: no add, commit, "
    "stash, reset, checkout, restore, clean, merge or rebase."
)


TEST_SCOPE_LINE = (
    "Run only the tests your brief names and the test files you edit, each by name and in the "
    "foreground. Never run the project's full test suite, a discovery command over its test "
    "folder or a selection of affected tests, and never repeat a run on a second interpreter: "
    "mitosis judges the finished build itself once every Lane is done."
)


REFEREE_FILES = (
    "test configuration, snapshot, baseline, allowlist or skip list: mitosis reverts such a write "
    "and holds the MSP back."
)


def referee_line(locked):
    if locked:
        return "The locked checks %s are read-only. Never write them or any %s" % (", ".join(locked), REFEREE_FILES)
    return "Never write any %s" % REFEREE_FILES


PROBE_LINE = (
    "Probe files, the only files your probe may write: %s (and no others). Prove each test "
    "passes against a probe implementation written only inside these files, and fails on "
    "today's code by an "
    "assertion failure or a missing module, never by a crash in the harness; leave the probe "
    "in place in the clone and never commit it."
)


def probe_line(paths):
    return PROBE_LINE % ", ".join(paths)


EDIT_RULE = "Edit ONLY the files listed under `edit`."


def _task_line(leaves):
    if len(leaves) == 1:
        return "Task: " + leaves[0]["task"]
    return (
        "You own %d related pieces of work. Do them IN THE ORDER GIVEN - a later one may build "
        "on an earlier one." % len(leaves)
    )


def brief_text(brief):
    header = ["Lane %s of MSP %s" % (brief["lane"], brief["msp_label"])]
    if brief.get("charter"):
        header.append("Charter: %s (binding; read it fully before any Step)" % brief["charter"])
    if brief.get("document"):
        header.append(
            "Document: %s (open it from your worktree; it carries what no Step owns)"
            % brief["document"]
        )
    body = [_task_line(brief["steps"]), "", brief["spec"], "", EDIT_RULE]
    footer = [CLONE_LINE, TEST_SCOPE_LINE] + (
        [probe_line(brief["probe_files"])] if brief.get("probe_files") is not None else []
    ) + (
        [referee_line(brief.get("locked") or ())]
        if brief.get("role") != CHECK_AUTHOR or brief.get("locked")
        else []
    ) + ["When finished, return your reply as structured output:", brief["return_contract"]]
    return "\n\n".join("\n".join(part) for part in (header, body, footer)) + "\n"


def _brief(
    items,
    lane_index,
    lane,
    msp_index,
    msp_label,
    packs,
    charter,
    source,
    locked=(),
):
    write_set = tuple(dict.fromkeys(path for i in lane for path in _files(items[i])))
    read_set, read_overflow = _lane_read_set(items, lane, packs, write_set)
    leaves = [_step_brief(items[i], packs.get(items[i]["name"], {})) for i in lane]
    partial = {
        "lane": lane_index,
        "msp": msp_index,
        "msp_label": msp_label,
        "steps": leaves,
        "spec": brief_spec(leaves),
        "write_set": list(write_set),
        "read_set": read_set,
        "read_overflow": read_overflow,
        "charter": charter,
        "document": source.get("path") if isinstance(source, dict) else None,
        "return_contract": RETURN_CONTRACT,
        "locked": list(locked),
    }
    return {**partial, "text": brief_text(partial)}


def _check_author_brief(parts, msp, label, probe, packs, charter, source, locked):
    steps = [{**author, "files": list(paths)} for author, paths in parts]
    write_set = list(dict.fromkeys(path for _, paths in parts for path in paths))
    own = frozenset(referee.normalised(path) for path in write_set)
    others = [path for path in locked if referee.normalised(path) not in own]
    read_set, read_overflow = _lane_read_set(steps, tuple(range(len(steps))), packs, write_set)
    leaves = [_step_brief(step, packs.get(step["name"], {}), step["files"]) for step in steps]
    partial = {
        "lane": CHECK_AUTHOR_LANE,
        "msp": msp,
        "msp_label": label,
        "role": CHECK_AUTHOR,
        "steps": leaves,
        "spec": brief_spec(leaves),
        "write_set": write_set,
        "read_set": read_set,
        "read_overflow": read_overflow,
        "charter": charter,
        "document": source.get("path") if isinstance(source, dict) else None,
        "return_contract": RETURN_CONTRACT,
        "locked": others,
        "probe_files": list(probe),
    }
    names = list(dict.fromkeys(author["name"] for author, _ in parts))
    return {
        "msp": msp,
        "label": label,
        "name": ", ".join(names),
        "steps": names,
        "slice": write_set,
        "source": parts[0][0].get("source"),
        "brief": {**partial, "text": brief_text(partial)},
    }


def _msp_of_label(items, msps, label):
    found = [
        index
        for index, msp in enumerate(msps)
        if any(_check_author_label(items[i]) == label for i in msp)
    ]
    return found[0] if found else None


def _naming_steps(items, path):
    return tuple(
        index
        for index, item in enumerate(items)
        if path in {_norm(named) for named in _acceptance_files(item)}
    )


def _locked_file_errors(author, target, path, naming, owner, items, labels):
    name = author["name"]
    falls = tuple(dict.fromkeys(owner[i] for i in naming))
    if not naming:
        return (
            "%s: writes %s, which no Step's acceptance names, so no MSP can lock it"
            % (name, path),
        )
    if target is None and len(falls) > 1:
        first = next(i for i in naming if owner[i] == falls[0])
        second = next(i for i in naming if owner[i] == falls[1])
        return (
            "%s is named by the acceptance of %s (MSP %s) and %s (MSP %s), which fall in two "
            "MSPs, so no one MSP can lock it"
            % (
                path,
                items[first]["name"],
                labels[falls[0]],
                items[second]["name"],
                labels[falls[1]],
            ),
        )
    if target is None:
        return ()
    return tuple(
        "%s: names %s in its acceptance, which %s, the %s of MSP %s, writes"
        % (items[i]["name"], path, name, CHECK_AUTHOR, labels[target])
        for i in naming
        if owner[i] != target
    )


def check_author_slices(authors, items, msps):
    owner = _owners(msps, len(items))
    labels = tuple(_msp_label(items, msp) for msp in msps)
    errors = ()
    placed = ()
    for position, author in enumerate(authors):
        label = _check_author_label(author)
        target = None if label is None else _msp_of_label(items, msps, label)
        if label is not None and target is None:
            errors = errors + (
                "%s: carries msp %s, which names no MSP: no Step carries msp %s"
                % (author["name"], label, label),
            )
            continue
        for path in _files(author):
            naming = _naming_steps(items, path)
            found = _locked_file_errors(author, target, path, naming, owner, items, labels)
            if found:
                errors = errors + found
                continue
            placed = placed + ((owner[naming[0]] if target is None else target, position, path),)
    return errors, placed


def _slice_parts(authors, placed, msp):
    positions = sorted({position for target, position, _ in placed if target == msp})
    return tuple(
        (authors[position], tuple(path for target, at, path in placed if target == msp and at == position))
        for position in positions
    )


def _lane_edges(plan):
    return {
        int(consumer): tuple(int(producer) for producer in producers)
        for consumer, producers in (plan.get("lane_after") or {}).items()
    }


def msp_producers(plan):
    lanes = plan.get("lanes") or []
    producers = {index: () for index in range(len(plan.get("msps") or []))}
    for consumer, sources in _lane_edges(plan).items():
        own = lanes[consumer]["msp"]
        for producer in sources:
            other = lanes[producer]["msp"]
            if other != own and other not in producers[own]:
                producers = {**producers, own: producers[own] + (other,)}
    return {msp: tuple(sorted(found)) for msp, found in producers.items()}


def _merged(producers, msp):
    seen = frozenset()
    frontier = producers.get(msp, ())
    while frontier:
        fresh = tuple(
            producer for producer in dict.fromkeys(frontier) if producer not in seen and producer != msp
        )
        seen = seen | frozenset(fresh)
        frontier = tuple(further for producer in fresh for further in producers.get(producer, ()))
    return seen


def msp_merges(plan):
    producers = msp_producers(plan)
    return {msp: tuple(sorted(_merged(producers, msp))) for msp in producers}


def msp_mergers(plan):
    merges = msp_merges(plan)
    return {msp: tuple(other for other in sorted(merges) if msp in merges[other]) for msp in merges}


def probe_files(plan, msp):
    msps = plan.get("msps") or []
    return tuple(
        dict.fromkeys(
            path
            for index in (msp,) + msp_merges(plan).get(msp, ())
            for path in msps[index].get("files") or ()
        )
    )


def _merged_locks(first, second):
    merged = ()
    for path in tuple(first) + tuple(second):
        if referee.normalised(path) not in {referee.normalised(seen) for seen in merged}:
            merged = merged + (path,)
    return merged


def _present(value):
    if value is None:
        return False
    if isinstance(value, (list, tuple, dict)) and not value:
        return False
    return True


def _charter_text(charter, root):
    if not isinstance(charter, str) or not charter:
        return ""
    path = charter if os.path.isabs(charter) or root is None else os.path.join(root, charter)
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def charter_locks(charter, root=None):
    try:
        return referee.locked_paths(_charter_text(charter, root)), ()
    except ValueError as error:
        return (), ("the charter %s: %s" % (charter, error),)


def _read_only(item, locked):
    owned = {referee.normalised(path) for path in _acceptance_files(item)} & {
        referee.normalised(path) for path in locked
    }
    return {**item, "files": [path for path in item["files"] if _norm(path) not in owned]}


def without_locked_acceptance(items, locked):
    return [_read_only(item, locked) for item in items]


def referee_errors(items, locked):
    return tuple(
        "%s: writes %s, which %s; no Step may write the referee" % (item["name"], path, reason)
        for item in items
        for path in _files(item)
        for reason in (referee.referee_reason(path, locked),)
        if reason is not None
    )


LockedSteps = collections.namedtuple(
    "LockedSteps", ("counts", "authors", "locked", "items", "msps", "placed")
)


def locked_steps(items, charter=None, root=None, shared_files=()):
    charter_locked, lock_errors = charter_locks(charter, root)
    checked = validate(items, root=root)
    if checked["errors"] or lock_errors:
        raise ValidationError(tuple(checked["errors"]) + lock_errors)
    authors = check_author_steps(items)
    locked = _merged_locks(charter_locked, tuple(path for author in authors for path in _files(author)))
    built = without_locked_acceptance(without_check_author(items), locked)
    refused = referee_errors(built, locked)
    if refused:
        raise ValidationError(refused)
    msps = msp_items(built, shared_files)
    slice_errors, placed = check_author_slices(authors, built, msps)
    if slice_errors:
        raise ValidationError(slice_errors)
    return LockedSteps(checked["counts"], authors, locked, built, msps, placed)


def saved_slice_errors(entries, steps):
    owned = {
        (steps.authors[position]["name"], referee.normalised(path))
        for _, position, path in steps.placed
    }
    return tuple(
        "%s: its slice claims %s, which no check-author Step of the loaded items writes; "
        "a %s slice holds only its Steps' placed files" % (entry.get("name"), path, CHECK_AUTHOR)
        for entry in entries
        for path in entry.get("slice") or ()
        if not any((name, referee.normalised(path)) in owned for name in entry.get("steps") or ())
    )


def plan(
    items,
    charter=None,
    risk_markers=(),
    serial_markers=(),
    graph=None,
    hops=1,
    cap=CONTEXT_CAP,
    history=(),
    root=None,
    coalesce_below=2.0,
    coalesce_max=4,
    briefs=True,
    shared_files=(),
):
    shared = tuple(shared_files or ())
    items = [with_task(item) for item in items] if isinstance(items, list) else items
    counts, authors, locked, items, msps, placed = locked_steps(items, charter, root, shared)
    lanes = lane_items(items, shared)
    tiered = {item["name"]: _tiered(item, risk_markers, history) for item in items}
    tiers = {name: tier for name, (tier, _) in tiered.items()}
    tier_notes = {name: note for name, (_, note) in tiered.items() if note}
    packs = context_packs(items, graph, hops, cap)
    labels = tuple(_msp_label(items, msp) for msp in msps)
    source = items[0].get("source") if items else None
    steps, cost, waves = build_waves(items, shared)
    review = coupling_review(items, lanes, graph, risk_markers, history, shared)
    lane_tiers = tuple(
        "top" if any(tiers[items[i]["name"]] == "top" for i in lane) else "cheap" for lane in lanes
    )
    owners = lane_msps(items, lanes, shared)
    edges = lane_after(items, lanes)
    msp_entries = [
        {
            "label": labels[index],
            "steps": [items[i]["name"] for i in msp],
            "files": sorted({path for i in msp for path in _files(items[i])}),
        }
        for index, msp in enumerate(msps)
    ]
    lane_entries = [
        {
            "msp": owners[index],
            "steps": [items[i]["name"] for i in lane],
            "tier": lane_tiers[index],
            "cost": lane_cost(items, lane),
        }
        for index, lane in enumerate(lanes)
    ]
    lane_edges = {str(consumer): list(producers) for consumer, producers in edges.items()}
    related = {"msps": msp_entries, "lanes": lane_entries, "lane_after": lane_edges}
    body = {
        "source": source,
        "locked": list(locked),
        "items": items,
        "msps": msp_entries,
        "lanes": lane_entries,
        "lane_after": lane_edges,
        "tiers": tiers,
        "tier_notes": tier_notes,
        "verify_modes": verify_modes(items, serial_markers),
        "context_packs": packs,
        "shared_context": shared_context(packs, waves),
        "lane_order": list(lane_order(lanes, edges, items)),
        "coalesce": coalesce_groups(
            steps, waves, tiers, cost, coalesce_below, coalesce_max, ready_after(steps, waves, shared)
        ),
        "coupling_review": review,
        "verdict_groups": verdict_groups(review),
        "counts": counts,
        "shared_files": list(shared),
        "integration_order": integration_order(items, msps, shared),
        "shared_file_candidates": shared_file_candidates(items, shared),
        "briefs": [
            _brief(
                items,
                index,
                lane,
                owners[index],
                labels[owners[index]],
                packs,
                charter,
                source,
                locked,
            )
            for index, lane in enumerate(lanes)
        ],
        "check_authors": [
            _check_author_brief(
                parts,
                index,
                labels[index],
                probe_files(related, index),
                context_packs([author for author, _ in parts], graph, hops, cap),
                charter,
                source,
                locked,
            )
            for index in range(len(msps))
            for parts in (_slice_parts(authors, placed, index),)
            if parts
        ],
    }
    shaped = body if briefs else {key: value for key, value in body.items() if key not in BRIEF_KEYS}
    kept = {key: shaped[key] for key in PLAN_KEYS if key in shaped and _present(shaped[key])}
    identified = {**kept, "plan_id": plan_id_for(kept)}
    return {key: identified[key] for key in PLAN_KEYS if key in identified}


LOCK_TIER = "top"


def plan_tiers(plan):
    used = {lane.get("tier") for lane in plan.get("lanes") or ()}
    return tuple(sorted(used | ({LOCK_TIER} if plan.get("check_authors") else set()), key=str))


def new_session_id():
    return str(uuid.uuid4())


def carried_session(argv, session):
    return session if any(session in word for word in argv) else None
