import concurrent.futures
import json
import os

import decompose
import handoff
import recon_text
import step_names

RECON_SCHEMA = "recon-return"
DROPPED = "recon named producers that do not exist in this work-set; the edges were dropped"
EMPTY = "recon returned no usable items - this ask produced no work and no PR"
NO_ITEMS = "recon produced no items"

CARRIED_FIELDS = ("file_notes", "type", "complexity", "after", "contract_group")


def read_asks(path):
    with open(path, encoding="utf-8") as handle:
        return tuple(line.strip() for line in handle if line.strip())


def _item_errors(index, item):
    name = item.get("name")
    if not isinstance(name, str) or not name:
        return ("item #%d has no name" % index,)
    files = item.get("files") or []
    if not files or any(not path for path in files):
        return ("item %s has no files" % name,)
    return ()


def read(index, stdout):
    reply, errors = handoff.read_reply(stdout, RECON_SCHEMA)
    if errors:
        return None, errors
    found = tuple(
        error
        for position, item in enumerate(reply["items"], 1)
        for error in _item_errors(position, item)
    )
    if found:
        return None, found
    return reply, ()


def _argv_for(template, prompt, document, model, session_id, schema):
    substitutions = {
        decompose.PROMPT_PLACEHOLDER: prompt,
        decompose.DOCUMENT_PLACEHOLDER: document or "",
        decompose.SESSION_PLACEHOLDER: session_id,
        decompose.SCHEMA_PLACEHOLDER: schema,
    }
    if model is not None:
        substitutions = {**substitutions, decompose.MODEL_PLACEHOLDER: model}
    return decompose.build_argv(template, substitutions)


def _job(prompt, template, document, model, schema, log):
    return {
        "argv_for": lambda session_id: _argv_for(
            template, prompt, document, model, session_id, schema
        ),
        "prompt": prompt,
        "log": log,
        "schema": schema,
        "model": model,
    }


def _log(log_dir, number):
    if log_dir is None:
        return None
    return os.path.join(log_dir, "ask-%d.log" % number)


def _good(spawned):
    return spawned["reply"] is not None and spawned["reason"] is None and spawned["exit"] in (None, 0)


def _call_errors(number, ask, spawned):
    if _good(spawned):
        return []
    lead = 'the planning call for ask %d, "%s", failed%s: ' % (
        number + 1,
        ask,
        " after one re-ask" if spawned.get("reasked") else "",
    )
    reply_errors = [] if spawned["reply"] is not None else list(spawned["reply_errors"])
    reason = [spawned["reason"]] if spawned["reason"] else []
    exited = ["exited %d" % spawned["exit"]] if spawned["exit"] not in (None, 0) else []
    return [lead + error for error in reply_errors + reason + exited]


def _step(item):
    return {
        "name": item["name"],
        "task": item["task"],
        "files": list(item["files"]),
        "source": None,
        "observable": item["acceptance"],
        "acceptance": [{"file": test["file"], "test": test["test"]} for test in item["tests"]],
        **{field: item[field] for field in CARRIED_FIELDS if field in item},
    }


def _dropped(steps):
    known = {step["name"] for step in steps}
    kept = []
    dropped = []
    for step in steps:
        bad = [entry for entry in step.get("after") or [] if entry not in known]
        if not bad:
            kept.append(step)
            continue
        remaining = [entry for entry in step["after"] if entry in known]
        trimmed = {key: value for key, value in step.items() if key != "after"}
        kept.append({**trimmed, "after": remaining} if remaining else trimmed)
        dropped.append({"step": step["name"], "dropped_after": bad})
    return kept, dropped


def plan_asks(
    asks,
    template,
    root,
    model=None,
    graph_path=None,
    concurrency=4,
    reask=None,
    env=None,
    log_dir=None,
    prompt_template=None,
    document=None,
):
    schema = json.dumps(handoff.call_schema(RECON_SCHEMA), sort_keys=True)
    base = dict(os.environ if env is None else env)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(concurrency, 1)) as pool:
        pending = [
            pool.submit(
                decompose.spawn_replies,
                [
                    _job(
                        recon_text.prompt(ask, graph_path, prompt_template),
                        template,
                        document,
                        model,
                        schema,
                        _log(log_dir, number),
                    )
                ],
                1,
                root,
                read,
                RECON_SCHEMA,
                reask=reask,
                env={**base, **recon_text.environment(ask, graph_path)},
            )
            for number, ask in enumerate(asks)
        ]
        spawned = [future.result()[0] for future in pending]
    errors = [
        error
        for number, (ask, one) in enumerate(zip(asks, spawned))
        for error in _call_errors(number, ask, one)
    ]
    good = [(ask, one["reply"]["items"]) for ask, one in zip(asks, spawned) if _good(one)]
    empty = [{"ask": ask, "error": EMPTY} for ask, items in good if not items]
    steps, dropped = _dropped(
        step_names.renamed([_step(item) for _, items in good for item in items])
    )
    return {
        "items": steps,
        "dropped": dropped,
        "empty": empty,
        "errors": errors if errors or steps else [NO_ITEMS],
        "sessions": [one["session_id"] for one in spawned],
    }


def notices(result):
    return [
        "Step %s: %s: %s" % (entry["step"], DROPPED, ", ".join(entry["dropped_after"]))
        for entry in result["dropped"]
    ] + ['ask "%s": %s' % (entry["ask"], entry["error"]) for entry in result["empty"]]
