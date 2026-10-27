import datetime
import glob
import itertools
import json
import os
import types

TOKEN_KINDS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")

SYNTHETIC_MODEL = "<synthetic>"

PRICES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prices.json")


def load_prices():
    with open(PRICES_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def field(record, name):
    if isinstance(record, dict) or isinstance(record, types.MappingProxyType):
        return record.get(name)
    return getattr(record, name, None)


def instant(value):
    if isinstance(value, datetime.datetime):
        return value if value.tzinfo is not None else None
    if not isinstance(value, str):
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def usage_tokens(usage):
    cache_creation = usage.get("cache_creation")
    if isinstance(cache_creation, dict):
        cache_write_5m = count(cache_creation.get("ephemeral_5m_input_tokens"))
        cache_write_1h = count(cache_creation.get("ephemeral_1h_input_tokens"))
    else:
        cache_write_5m = count(usage.get("cache_creation_input_tokens"))
        cache_write_1h = 0
    return (
        count(usage.get("input_tokens")),
        cache_write_5m,
        cache_write_1h,
        count(usage.get("cache_read_input_tokens")),
        count(usage.get("output_tokens")),
    )


def transcripts(home, session):
    projects = os.path.join(home, ".claude", "projects")
    escaped = glob.escape(session)
    return sorted(glob.glob(os.path.join(glob.escape(projects), "*", escaped + ".jsonl"))) + sorted(
        glob.glob(os.path.join(glob.escape(projects), "*", escaped, "subagents", "*.jsonl"))
    )


def parsed_line(text):
    try:
        line = json.loads(text)
    except ValueError:
        return None
    return line if isinstance(line, dict) else None


def message_entry(line):
    if line.get("type") != "assistant":
        return None
    message = line.get("message")
    if not isinstance(message, dict):
        return None
    model = message.get("model")
    if not isinstance(model, str) or model == SYNTHETIC_MODEL:
        return None
    key = message.get("id") or line.get("uuid")
    if not isinstance(key, str):
        return None
    moment = instant(line.get("timestamp"))
    if moment is None:
        return None
    usage = message.get("usage")
    return (key, model, moment, usage_tokens(usage if isinstance(usage, dict) else {}))


def transcript_entries(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        lines = tuple(parsed_line(text) for text in handle)
    entries = (message_entry(line) for line in lines if line is not None)
    return tuple(entry for entry in entries if entry is not None)


def inside(moment, start, end):
    return start is not None and end is not None and start <= moment <= end


def session_entries(home, source, session, children, window):
    child_spans = tuple(
        (instant(field(child, "start")), instant(field(child, "end")))
        for child in children
        if field(child, "session") == session
    )
    entries = itertools.chain.from_iterable(transcript_entries(path) for path in transcripts(home, session))
    return tuple(
        (key, source, model, tokens)
        for key, model, moment, tokens in entries
        if (window is None or inside(moment, *window))
        and not any(inside(moment, start, end) for start, end in child_spans)
    )


def first_by_key(entries):
    return tuple({entry[0]: entry for entry in reversed(entries)}.values())


def row_usd(model, tokens, per_million):
    price = per_million.get(model)
    if not isinstance(price, dict):
        return None
    return round(sum(tokens[kind] * price[kind] for kind in TOKEN_KINDS) / 1000000, 6)


def summed(token_rows):
    return {kind: sum(row[index] for row in token_rows) for index, kind in enumerate(TOKEN_KINDS)}


def frozen(value):
    if isinstance(value, dict):
        return types.MappingProxyType({key: frozen(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(frozen(item) for item in value)
    return value


def as_dict(value):
    if isinstance(value, (dict, types.MappingProxyType)):
        return {key: as_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [as_dict(item) for item in value]
    return value


def run_spend(home, calls, caller, span, children, prices):
    every_call = tuple(calls)
    present = tuple(
        call for call in every_call if field(call, "session") is not None and transcripts(home, field(call, "session"))
    )
    sessions_missing = len(every_call) - len(present)
    call_entries = itertools.chain.from_iterable(
        session_entries(home, field(call, "source"), field(call, "session"), children, None) for call in present
    )
    caller_entries = session_entries(home, "caller", caller, children, span) if caller is not None else ()
    counted = first_by_key(tuple(call_entries) + caller_entries)
    per_million = prices.get("per_million") or {}
    by_row = itertools.groupby(sorted(counted, key=lambda entry: (entry[1], entry[2])), key=lambda entry: (entry[1], entry[2]))
    grouped = tuple((source, model, summed(tuple(entry[3] for entry in group))) for (source, model), group in by_row)
    rows = [
        {"source": source, "model": model, "tokens": tokens, "usd": row_usd(model, tokens, per_million)}
        for source, model, tokens in grouped
    ]
    totals = {kind: sum(row["tokens"][kind] for row in rows) for kind in TOKEN_KINDS}
    return frozen(
        {
            "spend": rows,
            "tokens": totals,
            "usd": round(sum(row["usd"] for row in rows if row["usd"] is not None), 6),
            "prices_date": prices.get("date"),
            "unpriced_models": sorted({row["model"] for row in rows if row["usd"] is None}),
            "sessions_missing": sessions_missing,
        }
    )
