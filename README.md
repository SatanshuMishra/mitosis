# mitosis

mitosis takes work that one agent would grind through serially, splits it so
several agents run on it at once, and produces draft pull requests a human can
merge without discovering that the parallelism broke something. It validates
the input, schedules the split, prepares an isolated worktree and branch per
piece, dispatches a Worker into each, gates the result, reconciles what was
actually written against the git log, and opens one draft pull request per
piece, stacked on its predecessor where one exists.

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

The plugin carries the skill and the six modules it runs — `core.py`,
`shape.py`, `decompose.py`, `briefs.py`, `run.py`, `mitosis.py` — which need
Python 3.9 or later and nothing else.

To update, refresh the marketplace, update the plugin, and restart:

```bash
claude plugin marketplace update mitosis
claude plugin update mitosis@mitosis
```

An installed copy only updates when the plugin's version changes, and every
release changes it; `CHANGELOG.md` lists them. The version is part of each
plan's id, so finish a run before updating: it cannot be resumed on another
release.

Without Claude Code, copy the six modules into one directory and invoke
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
  --pr-command "open-pr --branch {branch} --base {base}" \
  --tier-model top=model-a \
  --tier-model cheap=model-b \
  --timeout 1800
```

Run `python3 mitosis.py --help` for every flag; this is one path through it,
not the full surface.

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
the merge `mitosis--v<version>`.

## More

For when an agent should reach for mitosis and how to invoke it, read
`skills/mitosis/SKILL.md`.
