# mitosis

mitosis takes work that one agent would grind through serially, splits it so
several agents run on it at once, and produces pull requests a human can
merge without discovering that the parallelism broke something. It validates
the input, schedules the split, prepares an isolated worktree and branch per
piece, dispatches a Worker into each, gates the result, reconciles what was
actually written against the git log, and opens one pull request per
piece, stacked on its predecessor where one exists, or pushes each piece's
branch and hands back its pull request's title and body for the caller to
open.

## Inputs

mitosis takes exactly one of two inputs, and needs nothing else to run:

- `--items`, a JSON array of Steps, handed to it directly.
- `--spec`, a document in any format, which mitosis decomposes into that same
  Steps array before scheduling.

Both converge on the same items format before anything downstream runs.

## What it never does

mitosis does not merge. Merging is a judgment call — review passed, CI green,
blast radius acceptable — and that judgment stays with a human.

mitosis does not run the full test suite; continuous integration does that, on
the pull request it opens. It does not name a model, hold an API key, or link
an SDK — every model invocation is a subprocess spawned from a command
template the caller supplies. And it does not judge a document's quality: it
reports how cleanly a document decomposed, and never refuses one.

## Install

mitosis installs as a Claude Code plugin from this repository. Add the
repository as a plugin marketplace, install the plugin, then restart Claude
Code:

```bash
claude plugin marketplace add SatanshuMishra/mitosis
claude plugin install mitosis@mitosis
```

The plugin carries the skill and the ten modules it runs — `core.py`,
`shape.py`, `decompose.py`, `briefs.py`, `run.py`, `pr_facts.py`,
`mitosis.py`, `metrics.py`, `spend.py`, `metrics_writer.py` — and the two data
files they read, `prices.json` and `catalogue.json`, which need Python 3.9 or
later. A run that names `--pr-type` and `--pr-subject` also needs the
pull-request command, 0.3.0 or later, on PATH or named with
`--render-command`, because mitosis writes no pull-request text itself.

To update, refresh the marketplace, update the plugin, and restart:

```bash
claude plugin marketplace update mitosis
claude plugin update mitosis@mitosis
```

An installed copy only updates when the plugin's version changes, and every
release changes it; `CHANGELOG.md` lists them. The version is part of each
plan's id, so finish a run before updating: it cannot be resumed on another
release.

Without Claude Code, copy the ten modules into one directory and invoke
`mitosis.py` by path. They import one another, so they must stay side by
side.

## A worked invocation

```
python3 mitosis.py \
  --items items.json \
  --charter CHARTER.md \
  --feature-branch main \
  --dispatch-command "worker-cli --prompt {task} --model {model}" \
  --acceptance-command "python3 -m unittest {test}" \
  --pr-command "open-pr --branch {branch} --base {base} --title {title} --body {body}" \
  --pr-type feat \
  --pr-scope manifest \
  --pr-subject "validate manifests from the command line" \
  --pr-why "A bad manifest should fail before it ships." \
  --pr-impact "A release with a bad manifest now stops at validation." \
  --tier-model top=model-a \
  --tier-model cheap=model-b \
  --timeout 1800
```

mitosis composes no pull-request title or body. At ship it writes each MSP's
facts to a facts file, runs pull-request render --fields on it, and uses the
title and body render printed unchanged; when render refuses the facts, that
MSP ends `ship-failed` with render's problem as its reason, and the facts are
never reworded and retried. The title is `<type>(<scope>): <subject>` from
`--pr-type`, `--pr-scope` and `--pr-subject`. Render writes the body from
mitosis's facts: Problem from the `--pr-why` lines; Change, one entry per
Step from its task's first sentence; Impact from the `--pr-impact` lines, or
the `--pr-blast-radius` sentence when none is given; Testing, one row per
acceptance test saying what the gate proved, with a Not run row and its
reason for each test left unproven; and Trade-offs and open problems, for
what reconcile, stacking or the build found. `--pr-door` is still accepted
and no longer reaches the body.

Every run that names `--pr-type` and `--pr-subject`, `--plan-only` and
`--no-push` included, needs `--pr-impact` or `--pr-blast-radius`, and runs
render on each MSP's facts before any Worker is dispatched, so a missing
pull-request command, one older than 0.3.0 or a refusal stops the run before
a build is paid for.

Every Step of a Lane is its own Worker session, run in Step order on its
MSP's branch, and lands its own commit with the subject
`<type>(<scope>): <step>`, from `--pr-type` and `--pr-scope`, or `chore`
with no scope when the run names none, and its task's first sentence as the
body. The branch's history is one commit per Step, and each Change entry
maps to one of them.

Leave out `--pr-command` and mitosis pushes each branch, opens nothing, ends each such MSP `pushed`, and records
the title and body in the run's state and in its JSON summary, so the caller
opens each pull request itself:

```
python3 mitosis.py \
  --items items.json \
  --charter CHARTER.md \
  --feature-branch main \
  --dispatch-command "worker-cli --prompt {task} --model {model}" \
  --acceptance-command "python3 -m unittest {test}" \
  --pr-type feat \
  --pr-subject "validate manifests from the command line" \
  --pr-why "A bad manifest should fail before it ships." \
  --pr-impact "A release with a bad manifest now stops at validation." \
  --tier-model top=model-a \
  --tier-model cheap=model-b \
  --timeout 1800
```

Run `python3 mitosis.py --help` for every flag; this is one path through it,
not the full surface.

## Holding and resuming a run

`--no-push` builds, gates and reconciles every MSP and publishes nothing;
`--resume` with the same input and run directory continues a run, and
without `--no-push` ships what was held. A resume reuses an MSP's recorded
gate result when it passed, the MSP's HEAD is the commit the gate ran at and
its worktree holds no uncommitted change; otherwise the MSP is gated again.

A caller that proved a later head itself names it with
`--proven-head <MSP label>=<commit>`, once per MSP. mitosis ships that head
without gating again when the MSP's HEAD equals the commit, its worktree is
clean and a passing gate result is recorded, and the pull request's Testing
section gets a Proven head row, marked Not run, naming both commits. A head
that differs, or an MSP with no recorded gate result, is gated as before; a
malformed value or a label the plan does not hold exits 2 before anything is
built.

## Metrics

mitosis records metrics about its own work in the metrics store on this
machine, the folder `~/.plugin-metrics`, or the folder `PLUGIN_METRICS_DIR`
names when it is set. Each invocation of the mitosis command is one run, from
the moment its run directory is known to its exit, so a plan, a build, a
resume and a ship are four runs, joined by their `plan_id` and `run_dir`. A
run appends events at the boundaries of its work and replaces one run summary
holding how the work split, how parallel the Lanes ran, how the gates came
out, how each MSP ended and the run's own spend. The store holds run metadata
only, never a task, a brief, a Worker's output or file contents, and a
metrics write that fails prints one line on stderr without changing the exit
code or the last JSON line.

mitosis gives every Worker dispatch, structure call and brief call a fresh
session id, offered as the `{session_id}` placeholder on the dispatch,
decompose and brief templates. Pass it to the model command so that call's
transcript is named by it, and mitosis prices the call from that transcript
when the summary is written:

```
claude -p {task} --model {model} --session-id {session_id} --permission-mode bypassPermissions
```

A template without the placeholder still runs; its calls are counted in
`sessions_missing` and add no spend. mitosis gives `PLUGIN_METRICS_PARENT`,
set to `mitosis/<run id>`, to every command it starts, so a run another tool
records from inside one of them names its parent, is listed among the run's
children, and keeps its spend apart from mitosis's own.

`catalogue.json` describes every event and field mitosis writes and carries
named queries, and is copied into the store. The plugin's metrics skill,
`skills/metrics/SKILL.md`, answers how mitosis is performing from those
records through the duckdb command. `docs/metrics-format.md` describes the
record format.

## Tests

```
python3 -m unittest discover tests
```

The tests ship with the plugin, so an installed copy can verify itself
wherever it lands.

## Releasing

A pull request that changes the modules, the skill or the plugin manifest
must raise the version in `core.py` and `.claude-plugin/plugin.json` together
and add its heading to `CHANGELOG.md`; the tests and continuous integration
refuse it otherwise. Versions stay below 1.0.0 until mitosis is released.
Merging to `main` publishes the release, and continuous integration then tags
the merge `mitosis--v<version>`. Keep a branch up to date with `main` before
merging it, because a check that passed against an older `main` is not rerun.

Continuous integration also refuses published text, including a pull
request's title and body, that contains a term listed in the `DENY_TERMS`
repository secret. A pull request from a fork cannot read that secret, so its
check fails until the branch is pushed here.

## More

For when an agent should reach for mitosis and how to invoke it, read
`skills/mitosis/SKILL.md`.
