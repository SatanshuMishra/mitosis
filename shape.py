import core

SCALAR_KEYS = (
    "steps",
    "lanes",
    "msps",
    "largest_lane",
    "msps_per_step",
    "parallelism",
    "fused_without_overlap",
    "lane_cycles",
)


def _write_set(item):
    return frozenset(core._files(item))


def _depths(nodes, producers_of):
    depth = {}

    def resolve(node, resolving):
        if node in depth:
            return depth[node]
        if node in resolving:
            return 0
        producers = tuple(producers_of(node))
        below = resolving | frozenset((node,))
        value = 1 + max(resolve(p, below) for p in producers) if producers else 0
        depth[node] = value
        return value

    for node in nodes:
        resolve(node, frozenset())
    return {node: depth[node] for node in nodes}


def _widest_layer(depths):
    layers = {}
    for level in depths.values():
        layers[level] = layers.get(level, 0) + 1
    return max(layers.values()) if layers else 0


def _lane_pairs(lane):
    return tuple((lane[a], lane[b]) for a in range(len(lane)) for b in range(a + 1, len(lane)))


def _fused_pairs(items, lanes):
    return tuple(
        (a, b)
        for lane in lanes
        for a, b in _lane_pairs(lane)
        if not (_write_set(items[a]) & _write_set(items[b]))
    )


def scalars(items):
    msps = core.msp_items(items)
    lanes = core.lane_items(items)
    edges = core.lane_after(items, lanes)
    depths = _depths(range(len(lanes)), lambda lane: edges.get(lane, ()))
    steps = len(items)
    return {
        "steps": steps,
        "lanes": len(lanes),
        "msps": len(msps),
        "largest_lane": max((len(lane) for lane in lanes), default=0),
        "msps_per_step": len(msps) / steps if steps else 0.0,
        "parallelism": _widest_layer(depths),
        "fused_without_overlap": len(_fused_pairs(items, lanes)),
        "lane_cycles": sum(len(group) for group in core.lane_cycles(items, lanes)),
    }


FINDING_KINDS = (
    "lane-cycle",
    "shared-directory-manifest",
    "group-is-a-chain",
    "group-has-no-producer",
    "fused-without-overlap",
)

FUSED_SHOWN = 8


def _name(items, index):
    name = items[index].get("name")
    return name if isinstance(name, str) and name else "item #%d" % index


def _group(item):
    value = item.get("contract_group")
    if value is None or value == "":
        return None
    return str(value)


def _groups(items):
    groups = {}
    for index, item in enumerate(items):
        group = _group(item)
        if group is not None:
            groups[group] = groups.get(group, ()) + (index,)
    return groups


def _producers(items, index, by_name):
    return tuple(
        by_name[name]
        for name in core._after(items[index])
        if name in by_name and by_name[name] != index
    )


def _producers_within(items, members, by_name):
    member_set = frozenset(members)
    return {
        member: tuple(p for p in _producers(items, member, by_name) if p in member_set)
        for member in members
    }


def _longest_chain(items, members, producers):
    depths = _depths(members, lambda member: producers[member])
    deepest = max(members, key=lambda member: (depths[member], -member))
    chain = (deepest,)
    while producers[chain[0]]:
        candidates = producers[chain[0]]
        step_back = max(candidates, key=lambda member: (depths[member], -member))
        if step_back in chain or depths[step_back] >= depths[chain[0]]:
            break
        chain = (step_back,) + chain
    return chain, depths[deepest]


def _chain_findings(items, groups, by_name):
    found = []
    for group, members in groups.items():
        producers = _producers_within(items, members, by_name)
        chain, depth = _longest_chain(items, members, producers)
        if depth >= 2:
            found.append(
                {
                    "kind": "group-is-a-chain",
                    "detail": "contract_group '%s' contains the after chain %s, which is depth %d"
                    % (group, " -> ".join(_name(items, i) for i in chain), depth),
                }
            )
    return found


def _producer_findings(items, groups):
    return [
        {
            "kind": "group-has-no-producer",
            "detail": "contract_group '%s' has no member of type contract, so its Steps ship "
            "as one pull request in whatever order their after edges give, with no producer first"
            % group,
        }
        for group, members in groups.items()
        if not any(items[i].get("type") == "contract" for i in members)
    ]


def _joined_by(items, a, b, by_name):
    parts = ()
    group_a, group_b = _group(items[a]), _group(items[b])
    if group_a is not None and group_a == group_b:
        parts = parts + ("contract_group '%s'" % group_a,)
    if a in _producers(items, b, by_name) or b in _producers(items, a, by_name):
        parts = parts + ("an after edge",)
    if parts:
        return "joined by " + " and ".join(parts)
    return "joined by neither a contract_group nor an after edge directly"


def _fused_findings(items, lanes, by_name):
    return [
        {
            "kind": "fused-without-overlap",
            "detail": "%s and %s share a Lane with no file in common, %s"
            % (_name(items, a), _name(items, b), _joined_by(items, a, b, by_name)),
        }
        for a, b in _fused_pairs(items, lanes)
    ]


def _directory(path):
    return path.rpartition("/")[0]


def _owns_sibling(own, other, directory):
    return any(_directory(path) == directory for path in own - other)


def _manifest_findings(items):
    found = []
    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            left, right = _write_set(items[a]), _write_set(items[b])
            shared = left & right
            if len(shared) != 1:
                continue
            manifest = next(iter(shared))
            directory = _directory(manifest)
            if _owns_sibling(left, right, directory) and _owns_sibling(right, left, directory):
                found.append(
                    {
                        "kind": "shared-directory-manifest",
                        "detail": "%s and %s overlap only on %s and each owns another file in %s"
                        % (_name(items, a), _name(items, b), manifest, directory or "."),
                    }
                )
    return found


def _cycle_findings(items, lanes):
    return [
        {
            "kind": "lane-cycle",
            "detail": "Lanes %s wait on each other and none of them can start: %s"
            % (
                ", ".join(str(lane) for lane in component),
                "; ".join(
                    "Lane %d holds %s"
                    % (lane, ", ".join(_name(items, i) for i in lanes[lane]))
                    for lane in component[:3]
                ),
            ),
        }
        for component in core.lane_cycles(items, lanes)
    ]


def _capped(found):
    fused = [entry for entry in found if entry["kind"] == "fused-without-overlap"]
    if len(fused) <= FUSED_SHOWN:
        return found
    kept = [entry for entry in found if entry["kind"] != "fused-without-overlap"]
    return (
        kept
        + fused[:FUSED_SHOWN]
        + [
            {
                "kind": "fused-without-overlap",
                "detail": "%d further fused pairs are not listed"
                % (len(fused) - FUSED_SHOWN),
            }
        ]
    )


def _readable(items):
    return isinstance(items, list) and all(isinstance(item, dict) for item in items)


def findings(items):
    if not _readable(items):
        return []
    try:
        lanes = core.lane_items(items)
        by_name = core._by_name(items)
    except TypeError:
        return []
    groups = _groups(items)
    found = (
        _cycle_findings(items, lanes)
        + _chain_findings(items, groups, by_name)
        + _producer_findings(items, groups)
        + _fused_findings(items, lanes, by_name)
        + _manifest_findings(items)
    )
    ranked = sorted(
        found, key=lambda finding: (FINDING_KINDS.index(finding["kind"]), finding["detail"])
    )
    return _capped(ranked)
