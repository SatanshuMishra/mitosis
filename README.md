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

The plugin carries the skill and the thirteen modules it runs — `core.py`,
`shape.py`, `decompose.py`, `briefs.py`, `run.py`, `isolation.py`, `mitosis.py`,
`metrics.py`, `spend.py`, `metrics_writer.py`, `referee.py`, `mutants.py`,
`outcomes.py` — and the two data files they read,
`prices.json` and `catalogue.json`, which need Python 3.9 or later and nothing
else.

To update, refresh the marketplace, update the plugin, and restart:

```bash
claude plugin marketplace update mitosis
claude plugin update mitosis@mitosis
```

An installed copy only updates when the plugin's version changes, and every
release changes it; `CHANGELOG.md` lists them. The version is part of each
plan's id, so finish a run before updating: it cannot be resumed on another
release.

Without Claude Code, copy the thirteen modules into one directory and invoke
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
  --pr-door two-way \
  --pr-blast-radius "Manifest validation only; a revert restores the old command." \
  --tier-model top=model-a \
  --tier-model cheap=model-b \
  --timeout 1800
```

Every title and body is written in one house format: the title is
`<type>(<scope>): <subject>`, and the body holds What changed, Why, an
optional Risk section and Verification. The Risk section opens with a
`Door:` line and a `Blast radius:` line when `--pr-door` and
`--pr-blast-radius` are given, which go together or not at all, then holds a
line naming what reconcile, the gate or stacking found, when they found
something. Leave out `--pr-command` and mitosis
pushes each branch, opens nothing, ends each such MSP `pushed`, and records
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
gate result when it passed and the MSP's HEAD is the commit the gate ran at;
otherwise the MSP is gated again. Files the worktree holds outside that commit
play no part, since the gate never reads them.

A caller that proved a later head itself names it with
`--proven-head <MSP label>=<commit>`, once per MSP. mitosis ships that head
without gating again when the MSP's HEAD equals the commit and a passing
gate result is recorded, and the pull request's
Verification section names both commits on a Not verified: line. A head that
differs, or an MSP with no recorded gate result, is gated as before; a
malformed value or a label the plan does not hold exits 2 before anything is
built.

A gate that is interrupted leaves the Worker's worktree as it was, because
the gate never runs there; the next gate removes the gate folder left behind.

## The referee and the gate

The referee is the set of files that decide whether the work is done, and no
Worker may write it. A charter names its locked checks one per line, as
`- Locked: tests/test_search.py`, and mitosis adds built-in referee markers
for test configuration such as `conftest.py` or `pytest.ini`, snapshots,
baselines, allowlists and skip lists. Planning refuses a Step whose files
hold a referee path, with exit 3, before anything is built. A Worker that
writes one anyway has the file restored from the feature branch, and its MSP
is held back as `referee-reverted` and never pushed, with exit 9. mitosis
finds such a write after the MSP's pre-gate commit, and also when that commit
fails, by comparing the feature branch's tree with the branch head's tree
through git's tree objects, with replace objects, system and global config,
attributes and filters kept out, so no Worker-written setting shapes what it
reads. The revert is a commit before the gate. The referee and the gate
judge one commit, the MSP branch's, so a worktree HEAD that is detached, on
another branch or at another commit is fatal too, with exit 9, and the gate
does not run. The JSON
result's `referee` object names the locked paths, their receipt lock and the
commit they were read at. A real run refuses, with exit 3, a charter whose
`- Locked:` path the feature branch does not hold, and a `- Locked:` line that
is empty, absolute or contains `..`.

The gate runs each acceptance property with the work present and with the
implementation reverted, then runs each passing property against up to 12
mutants of its Step's added lines, each one a small change to an added line,
such as a flipped comparison, a swapped operator, a flipped true or false, a
number shifted by one or a replaced return value. Strings and comments are
left alone, and when no operator applies to any added line the gate drops the
added lines of each file instead. A Step that changed a symbolic link is
judged `inconclusive`, because the gate never writes through a link. A
property no mutant fails is judged `weak`, with exit 24: it passes whether or
not the work is right. An MSP
whose gate is `weak`, `inert` or `inconclusive` ends `gate-failed`: its work
is committed on its branch and never pushed, so no pull request opens on an
unproven property.

The gate, every mutant run and every acceptance run execute in a clean
checkout of the MSP's committed tree, a folder outside the Worker's worktree
written from git's tree objects and passed as `{worktree}`. The folder is
reset to its target tree before every run and removed after the gate. It is
not a git repository and holds the commit's raw blob bytes. Files the commit
does not carry never reach the gate: each MSP's `ignored` list in the JSON
result names them, and mitosis never restores, removes or judges them.

The folder is reset to the commit's raw blob bytes before every acceptance
and mutant run, while `--gate-setup` runs once per gate, so what setup writes
survives that reset only at top-level names the commit lacks, such as the
`.git` folder a setup's `git init` makes or a new top-level build folder. An
acceptance command or test that runs git needs `--gate-setup`, which can make
`.git` at the top level. A top-level file the setup makes at a referee path,
such as `conftest.py`, and a top-level folder whose name is a referee folder or
holds a `- Locked:` path, such as `__snapshots__` or `.receipts`, is a referee
write, fatal with exit 9; mitosis judges that top-level name and never walks
into a kept folder such as node_modules. A Git LFS file
arrives as its pointer text, and a committed eol attribute is not applied in
every run, whatever `--gate-setup` does. An acceptance test that reads the
real content ends `inconclusive` and holds the MSP back as `gate-failed`, with
exit 21. The gate cannot tell pointer text from real content, so a property
that holds on pointer text is judged on pointer text.

Dependency folders come from outside the Worker's control. Without
`--gate-setup`, the gate links in each node_modules folder and each
virtual-environment folder, one holding `pyvenv.cfg`, that the repository
root holds and the commit lacks. With `--gate-setup`, the gate links nothing
and runs that command once per gate in the clean checkout, with `{worktree}`
naming it and the run's `--timeout`; a project whose root folders point back
into the root checkout, such as an editable install, uses it. A gate setup
that exits non-zero or times out runs no acceptance test and holds the MSP
back as `gate-failed`, with exit 21, and a gate error naming the gate setup.
A pre-gate commit that git refuses holds the MSP back the same way, with
exit 21, its work left uncommitted in the worktree and no gate run.

An acceptance run that leaves a referee path in the clean checkout differing
from the commit is caught the same way as a committed write: the gate lists
it under its `referee` list, and the MSP is held back as `referee-reverted`
and never pushed, with exit 9.

A base submodule whose gitlink the branch changed and whose commits cannot be
read holds the MSP back as `gate-failed`, with exit 21, and a gate error
saying the submodule cannot be judged. A committed referee write inside a base
submodule is reverted by resetting the submodule's gitlink to the base's, and
holds the MSP back as `referee-reverted`, with exit 9.

Ship refuses an MSP whose branch head is not the gated commit unless
`--proven-head` names that head; the refusal ends the MSP `ship-failed`, with
exit 22, and nothing is pushed.

`--trajectory PATH` reads prior outcomes to raise a surface's tier, and after
every gate also receives one outcome entry per acceptance test, appended to
that file.

Every git call times out after 600 seconds and fails with an error naming the
command and that a hook or a lock may be hanging, so a stuck git never stalls
a run.

A Worker that stops at a usage limit leaves its Lane `parked-limit`; Lanes
not yet dispatched park with it, and their MSPs end `parked`, with exit 13.
After the limit resets, `--resume` runs every Lane that is not `ok`.

The JSON result's `findings` list carries one entry per problem the run
found, each naming its MSP, its Lane, its kind, its exit code and a detail,
and the exit code is the most severe finding's.

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
