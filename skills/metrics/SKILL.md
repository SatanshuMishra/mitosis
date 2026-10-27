---
name: metrics
description: Use when asked how mitosis is performing, what it spent, how its Lanes, acceptance gates or pull requests turned out, or how its recent runs went. Answers about mitosis alone from the records it keeps in the local metrics store, through SQL run with the duckdb command, and never reads a transcript.
---

# mitosis metrics

Answer questions about mitosis alone from the records it writes about its own
runs. Every invocation of the mitosis command is one run: a plan, a build, a
resume or a ship. Each run appends events at the boundaries of its work and
replaces one run summary holding its totals and its own spend.

The store is the folder `~/.plugin-metrics`, or the value of
`PLUGIN_METRICS_DIR` when that variable is set and not empty. mitosis writes
only under plugin=mitosis in it; read nothing outside plugin=mitosis, and
never open a Claude Code transcript, a run directory or a Worker log to
answer.

## Before any query

1. Look for the duckdb command with `command -v duckdb`. When it is not
   found, say that the duckdb command is needed to read mitosis's metrics,
   give `brew install duckdb`, and stop.
2. Read plugin=mitosis/catalogue.json in the store first. Its purpose and
   objectives say what mitosis is for and name the fields that measure each
   objective; each field's description says what it records and its reading
   says how to read the value, zero and null included. Pick the fields a
   question needs from those descriptions and readings, never from a guess at
   a column name.
3. List plugin=mitosis/kind=event/repo=*/*.jsonl and
   plugin=mitosis/kind=summary/repo=*/*.json in the store. DuckDB refuses a
   glob that matches no file, so skip the view whose glob matches nothing and
   say that the store holds no such records yet.

## The views

Run every query in one duckdb session that starts with these three blocks,
with `~/.plugin-metrics` replaced by the value of `PLUGIN_METRICS_DIR` when it
is set. The time zone comes first, because DuckDB prints timestamps in local
time otherwise. The two views name every field the catalogue describes with
its type, so a query reads the same columns over any store, even one whose
runs never wrote a field; a field the catalogue does not describe is not in
them.

```sql
SET TimeZone = 'UTC';
```

```sql
CREATE OR REPLACE VIEW events AS
SELECT * FROM read_json('~/.plugin-metrics/plugin=mitosis/kind=event/repo=*/*.jsonl',
    format = 'newline_delimited', hive_partitioning = true, union_by_name = true,
    columns = {
        'v': 'BIGINT',
        'ts': 'TIMESTAMPTZ',
        'plugin': 'VARCHAR',
        'plugin_version': 'VARCHAR',
        'kind': 'VARCHAR',
        'event': 'VARCHAR',
        'run': 'VARCHAR',
        'parent': 'VARCHAR',
        'session': 'VARCHAR',
        'mode': 'VARCHAR',
        'input': 'VARCHAR',
        'concurrency': 'BIGINT',
        'plan_id': 'VARCHAR',
        'run_dir': 'VARCHAR',
        'tier_models': 'STRUCT("tier" VARCHAR, "model" VARCHAR)[]',
        'stage': 'VARCHAR',
        'seconds': 'DOUBLE',
        'calls': 'BIGINT',
        'steps': 'BIGINT',
        'lanes': 'BIGINT',
        'msps': 'BIGINT',
        'clusters': 'BIGINT',
        'lane_width': 'BIGINT',
        'fatal': 'BIGINT',
        'lane': 'BIGINT',
        'msp': 'VARCHAR',
        'tier': 'VARCHAR',
        'model': 'VARCHAR',
        'session_id': 'VARCHAR',
        'state': 'VARCHAR',
        'exit': 'BIGINT',
        'timed_out': 'BOOLEAN',
        'outcome': 'VARCHAR',
        'reused': 'BOOLEAN',
        'undeclared': 'BIGINT',
        'unwritten': 'BIGINT',
        'crossing': 'BIGINT',
        'meaning': 'VARCHAR'
    });
```

```sql
CREATE OR REPLACE VIEW summaries AS
SELECT * FROM read_json('~/.plugin-metrics/plugin=mitosis/kind=summary/repo=*/*.json',
    format = 'newline_delimited', hive_partitioning = true, union_by_name = true,
    columns = {
        'v': 'BIGINT',
        'ts': 'TIMESTAMPTZ',
        'plugin': 'VARCHAR',
        'plugin_version': 'VARCHAR',
        'kind': 'VARCHAR',
        'event': 'VARCHAR',
        'run': 'VARCHAR',
        'parent': 'VARCHAR',
        'session': 'VARCHAR',
        'mode': 'VARCHAR',
        'input': 'VARCHAR',
        'exit': 'BIGINT',
        'meaning': 'VARCHAR',
        'plan_id': 'VARCHAR',
        'run_dir': 'VARCHAR',
        'steps': 'BIGINT',
        'lanes': 'BIGINT',
        'msps': 'BIGINT',
        'clusters': 'BIGINT',
        'lane_width': 'BIGINT',
        'peak_parallel': 'BIGINT',
        'concurrency': 'BIGINT',
        'lane_states': 'STRUCT("ok" BIGINT, "failed" BIGINT, "blocked" BIGINT, "merge_blocked" BIGINT, "held" BIGINT)',
        'timed_out': 'BIGINT',
        'lane_seconds': 'DOUBLE',
        'wall_seconds': 'DOUBLE',
        'msp_states': 'STRUCT("shipped" BIGINT, "pushed" BIGINT, "unchanged" BIGINT, "committed" BIGINT, "ship_failed" BIGINT, "blocked" BIGINT)',
        'gates': 'STRUCT("pass" BIGINT, "inert" BIGINT, "inconclusive" BIGINT, "not_applicable" BIGINT)',
        'gates_reused': 'BIGINT',
        'reconcile': 'STRUCT("undeclared" BIGINT, "unwritten" BIGINT, "crossing" BIGINT, "fatal" BIGINT)',
        'foreign_writes': 'BIGINT',
        'stacking_exceptions': 'BIGINT',
        'tier_models': 'STRUCT("tier" VARCHAR, "model" VARCHAR)[]',
        'lane_runs': 'STRUCT("lane" BIGINT, "msp" VARCHAR, "tier" VARCHAR, "model" VARCHAR, "session_id" VARCHAR, "state" VARCHAR, "seconds" DOUBLE)[]',
        'msp_runs': 'STRUCT("msp" VARCHAR, "state" VARCHAR, "gate" VARCHAR, "reconcile_clean" BOOLEAN)[]',
        'usd': 'DOUBLE',
        'tokens': 'STRUCT("input" BIGINT, "cache_write_5m" BIGINT, "cache_write_1h" BIGINT, "cache_read" BIGINT, "output" BIGINT)',
        'spend': 'STRUCT("source" VARCHAR, "model" VARCHAR, "tokens" STRUCT("input" BIGINT, "cache_write_5m" BIGINT, "cache_write_1h" BIGINT, "cache_read" BIGINT, "output" BIGINT), "usd" DOUBLE)[]',
        'prices_date': 'VARCHAR',
        'unpriced_models': 'VARCHAR[]',
        'sessions_missing': 'BIGINT',
        'spend_complete': 'BOOLEAN',
        'children': 'STRUCT("plugin" VARCHAR, "run" VARCHAR, "usd" DOUBLE, "missing" BOOLEAN)[]',
        'spend_scope': 'VARCHAR'
    });
```

Pass the blocks and the query on standard input, so quoting stays intact:

```
duckdb -csv <<'SQL'
SET TimeZone = 'UTC';
CREATE OR REPLACE VIEW summaries AS ...;
SELECT ...;
SQL
```

## Queries

Prefer the catalogue's named queries. Its queries list gives each one's name,
the question it answers and its sql, written against the views above: the
recent runs, how the Lanes ended, how the gates came out, how the MSPs ended,
how parallel each build ran, and where the spend went by source and model.
Write your own SQL only when no named query answers the question, and then
only over fields the catalogue describes.

The runs in scope, their repositories and the period they cover:

```sql
SELECT repo, count(*) AS runs, min(ts) AS first_run, max(ts) AS last_run
FROM summaries
GROUP BY repo
ORDER BY last_run DESC;
```

The runs whose spend is partial or that started child runs:

```sql
SELECT run, repo, ts, usd, spend_complete, sessions_missing,
    to_json(unpriced_models) AS unpriced_models, to_json(children) AS children
FROM summaries
WHERE spend_complete IS NOT TRUE OR len(unpriced_models) > 0 OR len(children) > 0
ORDER BY ts DESC;
```

The child runs that wrote no summary:

```sql
SELECT run, repo, json_extract_string(child, '$.plugin') AS child_plugin,
    json_extract_string(child, '$.run') AS child_run
FROM (SELECT run, repo, unnest(json_extract(to_json(children), '$[*]')) AS child FROM summaries)
WHERE json_extract_string(child, '$.missing') = 'true';
```

How each Lane of the recent runs ended, from the events:

```sql
SELECT run, repo, lane, msp, tier, model, state, seconds
FROM events
WHERE event = 'lane.end'
ORDER BY ts DESC
LIMIT 50;
```

## Answering

Answer with the figures the queries returned, and name the runs in scope by
their run ids, their repositories and the period from the first `ts` to the
last. Say how many runs the figures cover.

Say plainly when a run's `spend_complete` is false: its `usd` adds only the
spend that was found, `sessions_missing` counts the calls whose transcript
was not found, and `unpriced_models` lists the models the price table lacks,
whose tokens are kept with no price. Say plainly when a child run is
`missing`: it wrote no summary, so its spend is unknown, never zero. A child
run's spend is its own and is never part of mitosis's `usd`.

The figures say what happened. Report them as they are, without judging
them.
