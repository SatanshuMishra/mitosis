---
name: mitosis
description: Use when one repository holds two or more changes that touch different files, or a document whose sections each become code, and the work should ship as separately reviewable draft pull requests built in parallel. Splits the work, builds each part on its own branch, checks that every named acceptance test actually fails without the work, opens one draft pull request per part, and never merges.
---

# mitosis

mitosis takes work that one agent would grind through serially, splits it so
several agents run at once, and produces draft pull requests a human can merge
without discovering that the parallelism broke something. A model may produce
anything; a model may judge nothing. Every gate in mitosis is a program.

This file says when to reach for it and how to invoke it. Every flag is
described by --help, and every design decision, with its cost and the test
that backs it, is in the specification at docs/specs. Do not look for either
here.

## When to reach for it

Reach for mitosis when all three hold:

1. Two or more changes are on the table, and at least two of them touch
   different files. Work that shares a file becomes one Lane and runs
   serially inside one branch; only disjoint write-sets buy parallelism.
2. Each change can name a test that proves it. The gate runs that test with
   the work present and again with the work reverted; a change with no such
   test is still built, but the run proves nothing about it and says so.
3. A human will review each part as its own pull request. mitosis opens
   draft pull requests and stops. Merging is the human's judgment.

Do not reach for it when:

- There is one change, or every change touches the same files. The plan
  collapses to one Lane, and the worktree, gate and pull request cost more
  than doing the work directly. Run with `--plan-only` when unsure; the
  report's split quality section shows the Lane count before anything spawns.
- The result must land in the current working tree. Workers build on
  branches cut from `--feature-branch`; anything uncommitted there is
  invisible to them. Commit first.
- The work needs the whole test suite or a merge to be verified. mitosis runs
  one acceptance property at a time and never merges; continuous integration
  on the pull request does the rest.

## What the caller supplies

mitosis names no model, holds no key and links no SDK. Every model call is a
subprocess built from a command template the caller passes. Each template is
split into argv before its placeholders are substituted, so a task containing
quotes or newlines lands as one argument and is never reinterpreted by a shell.

| Template | Spawns | Placeholders |
|---|---|---|
| `--dispatch-command` | one Worker per Lane, in its worktree | listed under --help |
| `--acceptance-command` | one acceptance property, in a worktree | file, test, worktree |
| `--pr-command` | one draft pull request per MSP | listed under --help |
| `--decompose-command` | the one pass that turns a document into Steps | prompt, model, document |

A Worker spawned from this adapter, with the Lane brief as its whole prompt
and the model chosen by tier:

```
claude -p {task} --model {model} --permission-mode bypassPermissions
```

The brief already carries the charter path, the document path, the Steps in
order, the write-set, the read-set and the return contract; the Worker needs
nothing else. Its last stdout line must be the one-line JSON return the brief
ends with, and a Claude Code Worker prints its final message last, so this
template satisfies that without wrapping.

Tiers are labels, not models. When the dispatch template uses the model
placeholder, every tier the plan assigns must be mapped, or mitosis refuses
to start rather than let a Lane run on whatever the default is:

```
--tier-model top=claude-opus-5 --tier-model cheap=claude-sonnet-5
```

The acceptance template must exit 0 when the property passes, 1 when it
fails, and anything else when it could not run. That third code is what lets
the gate tell a broken build from a failing test:

```
--acceptance-command "python3 -m pytest {file} -k {test} -q"
```

pytest exits 5 when it collects nothing, so a misspelled test identifier is
reported as inconclusive, not as a pass. Where the project's runner selects
tests differently, point the template at a small wrapper script that maps its
exit codes to those three.

Three more things every run needs: `--feature-branch`, which must exist and is
what every MSP branches from; `--timeout` in seconds, applied to every Worker,
acceptance run and pull-request command; and the repository root as the
working directory, because every Step path and the document path are relative
to it. mitosis refuses to run from anywhere else.

## The two inputs

Both converge on one list of Steps and one pipeline; nothing downstream can
tell them apart. Pass one, never both.

### Items: hand-written Steps

`--items` takes a JSON array. Each Step is one entry with the five required
fields and any of the optional ones; --help and the specification list them.
Two Steps that build a validator and then its command-line flag:

```json
[
  {
    "name": "validate-manifest",
    "task": "Add validate_manifest(path) to app/manifest.py. It returns a list of error strings, one per missing required key, and an empty list for a complete manifest. Required keys are name, version and entry.",
    "files": ["app/manifest.py", "tests/test_manifest.py"],
    "source": null,
    "acceptance": [
      {"file": "tests/test_manifest.py", "test": "a_missing_key_is_reported_by_name"}
    ],
    "type": "feature",
    "complexity": "simple"
  },
  {
    "name": "validate-flag",
    "task": "Add a --validate flag to app/cli.py that calls validate_manifest on the given path, prints each error on its own line, and exits 1 when the list is non-empty and 0 otherwise.",
    "files": ["app/cli.py", "tests/test_cli.py"],
    "source": null,
    "acceptance": [
      {"file": "tests/test_cli.py", "test": "validate_exits_one_on_a_bad_manifest"}
    ],
    "after": ["validate-manifest"],
    "type": "feature",
    "complexity": "simple"
  }
]
```

`source` is null because no document produced these Steps. `after` orders
the second behind the first; because their write-sets are disjoint, they
become two MSPs and the second stacks its pull request on the first.

Plan first. This validates the Steps, schedules them, writes the plan into
the run directory and prints the plan-stage report; it spawns nothing:

```
python3 /path/to/mitosis.py --items plan.json --plan-only
```

Read the report. An `after` naming a Step that does not exist is fatal and is
named; a write-set path that does not exist yet, an empty `acceptance` list
and a non-empty `assumptions` list are each counted, not fatal. When the
Lane count and the tiers look right, run:

```
python3 /path/to/mitosis.py --items plan.json \
  --charter docs/CHARTER.md \
  --feature-branch main \
  --dispatch-command "claude -p {task} --model {model} --permission-mode bypassPermissions" \
  --acceptance-command "python3 -m pytest {file} -k {test} -q" \
  --pr-command "gh pr create --draft --head {branch} --base {base} --title {title} --body {body}" \
  --tier-model top=claude-opus-5 --tier-model cheap=claude-sonnet-5 \
  --timeout 1800 --concurrency 4
```

The pull-request template's last stdout line is recorded as the pull
request; the one above prints the URL there.

### Spec: a document

`--spec` takes a document in any format. One decompose pass over the whole of
it produces the Steps, because the two fields that join distant parts of a
document, `after` and `contract_group`, cannot be seen from any one section.
The pass needs `--decompose-command`, and its prompt is large, so let it
arrive on stdin:

```
python3 /path/to/mitosis.py --spec docs/specs/search.md --plan-only \
  --decompose-command "claude -p --model {model} --permission-mode bypassPermissions" \
  --tier-model top=claude-opus-5 \
  --timeout 900
```

Decompose uses the top mapping. This run spawns exactly one model process,
persists the Steps it returned into the run directory, and prints the
decompose report followed by the plan-stage report. Read three things there:
the coverage map, which lists the document's sections no Step claimed; the
assumptions, each a reading the decomposer chose where the document was
underdetermined, and a Step carrying one is never rated `simple`; and the
count of global constraints it extracted, where zero on a document that has
binding rules means the Workers will not be told them.

A section nobody claimed, or an assumption you would have made differently,
is fixed in the document, not in the Steps. Edit the document and plan again.
When the plan reads right, run it with `--resume`, which reads the persisted
Steps instead of decomposing a second time:

```
python3 /path/to/mitosis.py --spec docs/specs/search.md --resume \
  --charter docs/CHARTER.md \
  --feature-branch main \
  --dispatch-command "claude -p {task} --model {model} --permission-mode bypassPermissions" \
  --acceptance-command "python3 -m pytest {file} -k {test} -q" \
  --pr-command "gh pr create --draft --head {branch} --base {base} --title {title} --body {body}" \
  --tier-model top=claude-opus-5 --tier-model cheap=claude-sonnet-5 \
  --timeout 1800 --concurrency 4
```

Every Step carries the document's path and hash in `source`, so each Worker
opens the document from its own worktree, and the report's drift section says
whether the document changed after the Steps were cut from it.

## What a run does

Validate, schedule, one worktree and branch per MSP under the run directory,
one Worker per Lane with producers merged in first, then per MSP: commit,
gate, reconcile, push, open a draft pull request stacked on its single
predecessor, report.

The gate runs each acceptance property twice, with the work present and with
the implementation reverted on a probe branch. `pass` means the property
failed without the work, so it is load-bearing. `inert` means it passed
without the work, so it proves nothing, and the pull request does not open.
`inconclusive` means reverting broke the build. `not-applicable` means the
Step declared no property, which is counted so nobody mistakes silence for
coverage. Which files count as implementation is derived: the MSP's write-set
minus the files its acceptance properties name.

Reconcile compares what the git log says changed against what the Steps
declared. A write outside the declaration, or a declared path never written,
is a finding, and a write into another MSP's files is fatal.

## Reading the result

The exit code is zero only when every MSP reached `shipped` and reconcile
found nothing. Every other outcome is a distinct non-zero code, printed with
its meaning on the report's last line. Each Lane ends `ok`, `failed`,
`blocked` or `merge-blocked`; each MSP ends `shipped`, `gate-failed`,
`gate-inconclusive` or `ship-failed`. The two Lane states that block
dependents differ by the human action they need: `merge-blocked` wants a
conflict resolved, `blocked` wants a predecessor fixed.

The run directory, printed in the report, holds the plan, the state, the
persisted Steps, every Worker's full stdout and stderr, and the worktrees.
Read a Lane's log there rather than asking the Worker what it did; the
one-line return it printed is a claim, and the git log is the record.

Nothing is cleaned up after a failure except the gate's probe branch.
Worktrees and branches survive so the work is inspectable, and a gate failure
leaves a local commit on an unpushed branch.

## Resuming

Pass `--resume` with the same input and run directory. Lanes already `ok`
and MSPs already `shipped` are skipped, a failed gate is re-run without
rebuilding the Lane, and a producer whose branch was deleted after its pull
request merged is still found through its recorded commit on the feature
branch. The plan id must match: if the Steps changed, the prior results do not
apply, and mitosis refuses rather than resume against a different plan.

## What mitosis never does

Merge. Run the full test suite. Name a model, hold a key or link an SDK.
Judge a document's quality; it reports how cleanly one decomposed and never
refuses one. Clean up after a failure, beyond the probe branch.

## Checking an installed copy

The install is a copy. --version prints the version of the copy in hand, and
the tests ship with it:

```
python3 /path/to/mitosis.py --version
python3 -m unittest discover /path/to/tests
```
