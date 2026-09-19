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

There is no package and no dependency. Copy the four files at the root of this
repository — `core.py`, `decompose.py`, `run.py`, `mitosis.py` — into the
target project, anywhere they can be invoked by path.

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

The tests ship with the install, so a copied checkout can verify itself
wherever it lands.

## More

For why each decision was made, what it cost, and what evidence backs it, read
`SPEC.md`. For when an agent should reach for mitosis and how to invoke it,
read `adapters/claude-code/SKILL.md`.
