import os

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
