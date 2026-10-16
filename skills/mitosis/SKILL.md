---
name: mitosis
description: Use when one repository holds two or more changes that touch different files, or a document whose sections each become code, and the work should ship as separately reviewable draft pull requests built in parallel. Splits the work, builds each part on its own branch, checks that every named acceptance test actually fails without the work, opens one draft pull request per part, and never merges.
---

# mitosis

mitosis takes work that one agent would grind through serially, splits it so
several agents run at once, and produces draft pull requests a human can merge
without discovering that the parallelism broke something. A model may produce
anything; a model may judge nothing. Every gate in mitosis is a program.

This file says when to reach for it and how to invoke it; --help describes
every flag.

## When to reach for it

Reach for mitosis when all three hold:

1. Two or more changes are on the table, and at least two of them touch
   different files. Work that shares a file becomes one Lane and runs
   serially inside one branch; only disjoint write-sets buy parallelism.
2. Each change can name a test that proves it. The gate runs that test with
   the work present and again with the work reverted; a change with no such
   test is still built, but the run proves nothing about it and says so.
3. A human will review each part as its own pull request. mitosis opens
   draft pull requests, or pushes the branches for the caller to open, and
   stops. Merging is the human's judgment.

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

| Flag | Spawns | Placeholders |
|---|---|---|
| `--dispatch-command` | one Worker per Lane, in its worktree | listed under --help |
| `--acceptance-command` | one acceptance property, in a worktree | file, test, worktree |
| `--pr-command` | one draft pull request per MSP; optional, refused with `--no-push` | listed under --help |
| `--pr-type` `--pr-scope` `--pr-subject` `--pr-why` | nothing; they write every title and body | none |
| `--decompose-command` | the structure pass that turns a document into unbriefed Steps | prompt, model, document |
| `--brief-command` | one Worker per unbriefed Step, writing its task | prompt, model, step, document |

A Worker spawned from this skill, with the Lane brief as its whole prompt
and the model chosen by tier:

```
claude -p {task} --model {model} --permission-mode bypassPermissions
```

The brief already carries the charter path, the document path, the Steps in
order, the write-set, the read-set and the return contract; the Worker needs
nothing else. It must print the brief's one-line JSON return last, as a Claude
Code Worker's final message is. Narration after it, or a code fence around it, is tolerated: the last
line that parses as a JSON object is the return.

Tiers are labels, not models. When the dispatch template uses the model
placeholder, every tier the plan assigns must be mapped, or mitosis refuses
to start:

```
--tier-model top=claude-opus-5 --tier-model cheap=claude-sonnet-5
```

The acceptance template must exit 0 when the property passes, 1 when it
fails, and anything else when it could not run. That third code is what lets
the gate tell a broken build from a failing test:

```
--acceptance-command "python3 -m pytest {file} -k {test} -q"
```

pytest exits 5 when it collects nothing, so a misspelled test reads as
inconclusive, not a pass. For another runner, point the template at a small
wrapper that maps its exit codes to those three.

The wrapper carries one obligation the runner alone will not meet. Reverting
the implementation deletes what the work created, so a test importing a new
module stops loading, and a load error reads as inconclusive. A module missing
because it was reverted means the behaviour does not exist: exit 1. Any other
load error stays inconclusive. Catch the whole import family, since importing
a name out of a surviving package raises the general error.

Whatever else writes into a worktree is yours to exclude: editor state, build
caches and hooks that fire on a Worker are staged by the commit before the
gate and arrive as undeclared writes against every MSP. Ignore them first.

Three more things every run needs: `--feature-branch`, which must exist and is
what every MSP branches from; `--timeout` in seconds on a monotonic clock,
applied to every Worker, acceptance run and pull-request command; and the
repository root as the working directory, because every Step path and the
document path are relative to it; mitosis refuses to run from anywhere else.

Every pull request is written in the house format. The title is
`<type>(<scope>): <subject>` from `--pr-type`, the optional `--pr-scope` and
`--pr-subject`, with the MSP label in parentheses after the subject when the
plan has several MSPs. The body holds What changed, one entry per Step from
its task's first sentence; Why, the `--pr-why` lines, one to eight; Risk, only
when reconcile, a foreign write, a manifest, stacking or the gate found
something; and Verification, a Verified line per property the gate proved,
then a Not verified line for every other. A run allowed to push needs
`--pr-type`, `--pr-subject` and `--pr-why`, checked before any Worker spawns.
Without `--pr-command` it pushes each MSP branch, opens no pull request and
ends that MSP `pushed`, recording its title and body for the caller to open.

## The two inputs

Both converge on one list of Steps and one pipeline; nothing downstream can
tell them apart. Pass one, never both.

### Items: hand-written Steps

`--items` takes a JSON array. Each Step is one entry with the five required
fields and any of the optional ones; --help lists them.
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

Steps sharing a `contract_group` are halves of one interface, so they ship as
one MSP and one Worker builds them in sequence, which stops two Workers
inventing two shapes for it. To build the halves in parallel, pin the group:
give exactly one of its Steps `"type": "contract"` to fix the interface, and
make every other Step in the group wait on it through `after`. To ship Steps
together without making them one Worker's, give them one `msp` tag instead.

Plan first. This validates the Steps, schedules them, writes the plan into
the run directory and prints the plan-stage report; it spawns nothing:

```
python3 "${CLAUDE_PLUGIN_ROOT}/mitosis.py" --items plan.json --plan-only
```

Read the report. An `after` naming a Step that does not exist is fatal and is
named; a write-set path that does not exist yet, an empty `acceptance` list
and a non-empty `assumptions` list are each counted, not fatal. When the
Lane count and the tiers look right, run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/mitosis.py" --items plan.json \
  --charter docs/CHARTER.md \
  --feature-branch main \
  --dispatch-command "claude -p {task} --model {model} --permission-mode bypassPermissions" \
  --acceptance-command "python3 -m pytest {file} -k {test} -q" \
  --pr-command "gh pr create --draft --head {branch} --base {base} --title {title} --body {body}" \
  --pr-type feat --pr-subject "validate manifests from the command line" \
  --pr-why "A bad manifest should fail before it ships." \
  --tier-model top=claude-opus-5 --tier-model cheap=claude-sonnet-5 \
  --timeout 1800 --concurrency 4
```

The pull-request template's last stdout line is recorded as the pull
request; the one above prints the URL there. Drop `--pr-command` to push only.

### Spec: a document

`--spec` takes a document in any format and reaches the plan in stages, so
you can read a document's split before any brief is bought.

`--plan-only` runs one structure dispatch over the whole of the document,
because the two fields that join distant parts of it, `after` and
`contract_group`, cannot be seen from any one section. The stage needs
`--decompose-command`, and its prompt is large, so let it arrive on stdin.
No Step returned here carries a `task`; nothing is briefed yet:

```
python3 "${CLAUDE_PLUGIN_ROOT}/mitosis.py" --spec docs/specs/search.md --plan-only \
  --decompose-command "claude -p --model {model} --permission-mode bypassPermissions" \
  --tier-model top=claude-opus-5 \
  --timeout 900
```

Decompose uses the top mapping. This run spawns one model process, persists
its Steps and prints a report. Read four things: the Split shape findings,
worst first; the scalars below them, which score the split before any brief
exists; the coverage map of sections no Step claimed; and the assumptions,
each a reading chosen where the document was underdetermined, which keeps
its Step from being rated `simple`.

No finding stops a run. Each one is printed, written into the pull request it
belongs to, listed for the caller and given a distinct exit code.

`manifest-exports-nothing` means the file that declares what a package exports
is written by a Step built before the modules it must export, so it would ship
empty and the package would have no public interface, while every test still
passes. Give that file to a
Step with an `after` edge reaching every Step whose modules it exports.

`lane-cycle` means Lanes that waited on each other, so one Worker builds them
all in one sitting. mitosis never refuses a knot: MSPs that wait on each other
become one MSP and one pull request, and Lanes that wait on each other become
one Lane. The work still ships, in sequence instead of in parallel, and
`lane_cycles` and `fused_without_overlap` count what that cost.

An assumption you would have decided differently does not go back into the
document. Write it into the decisions file, in your own words, and name it
with `--decisions PATH` (default: `<document without extension>.decisions.md`
when that file exists). The Decisions section of the report then names the
file and counts its settled questions, or says none was supplied. That file
binds every later structure dispatch and brief for this document, nothing in
mitosis merges or rewords an entry, and a question answered once is never
re-asked.

Where a document is soft, one dispatch will not tell you so. `--structure-
samples N` runs N structure dispatches at once, scores each with the same
scalars, and prints them ranked with where they disagree: a file split
differently, or another MSP count or parallelism. The best is persisted, or
the Kth with `--pick K`. A disagreement over a file means the document never
said who owns it.

Revise the structure once the decisions file has grown, without paying to
rebuild every Step:

```
python3 "${CLAUDE_PLUGIN_ROOT}/mitosis.py" --spec docs/specs/search.md --plan-only --revise \
  --decompose-command "claude -p --model {model} --permission-mode bypassPermissions" \
  --tier-model top=claude-opus-5 \
  --timeout 900
```

`--revise` hands the persisted structure back to one small dispatch, which
returns only a delta against it. Every Step you did not complain about is
kept exactly as it was, brief included; only the Steps the delta names as
changed, added or removed cost anything at the brief stage that follows.

When the plan reads right, run it with `--resume`. This writes a brief for
every Step still unbriefed, in parallel, and then plans and builds; a
revision that touched three Steps buys three brief dispatches, not a rebuild
of all of them. Pass the items run's flags with `--spec` and `--resume` in
place of `--items`, plus `--brief-command` taking the structure stage's
template.

Every Step carries the document's path and hash in `source`, so each Worker
opens the document from its own worktree, and the report's drift section says
whether the document changed after the Steps were cut from it.

## What a run does

Validate, schedule, one worktree and branch per MSP under the run directory,
one Worker per Lane with producers merged in first, then per MSP: commit,
gate, reconcile, push, open a draft pull request stacked on its single
predecessor when `--pr-command` is given, report.

A Worker session should stay small. mitosis estimates each Lane's tokens from
its Steps, tasks, write-set and read-set sizes, charter and document, and
splits one above `--session-tokens` (default 150000) in Step order into
consecutive Lanes. Each runs as a fresh session once the one before it lands,
may edit what earlier sessions wrote, and is told which Steps they built; a
resume starts at the first not `ok`. The split quality section names each
split and any Step too large alone.

The Lanes of one MSP share its worktree, and `--concurrency` Workers run at
once, four by default. Each brief tells its Worker that others are writing in
the same tree, to change only its write-set and to leave the index and history
alone. A finished Lane is `held` until no Worker is running in its worktree,
then committed, so a commit hook that stashes unstaged files never touches a
sibling's work; no new Lane starts in that MSP meanwhile, and a resume lands a
`held` Lane without paying for it again. Merges of a producer's branch run no
hooks. A git command that meets another process's lock is retried for a few
seconds. When a worktree goes quiet, a change to a file whose Lane had already
committed is kept in its own commit and named in the pull request, in
`attention` and in the exit code, against the Lane that made it, or against
every Lane that finished alongside it when git cannot tell which.

Steps with no file in common can still depend on each other. A pair is put in
order, the later behind the earlier, when both touch one risk marker, a
recorded regression links them, or both add a numbered migration to one
folder; the Coupling section lists every order added. A pair joined only by
an import runs in parallel and is named there and in `attention` before
anything is built: add an `after` edge where one needs the other's output.

Before anything is written, mitosis checks that git has a commit identity and
refuses with exit 3 if not. A refused Lane commit fails that Lane alone. Hooks
see each Lane's landing message, `chore(<MSP>): land Lane <n> (<Steps>)`, so a
hook that refuses that form fails every Lane after its Worker has run.

The gate runs each acceptance property twice, with the work present and with
the implementation reverted on a probe branch. `pass` means the property
failed without the work, so it is load-bearing. `inert` means it passed
without the work, so it proves nothing. `inconclusive` means reverting broke
the build. `not-applicable` means the Step declared no property, which is
counted so nobody mistakes silence for coverage. Which files count as
implementation is derived: the MSP's write-set minus the files its acceptance
properties name.

The gate never withholds a pull request. Every verdict becomes a Verified or
Not verified line of the body, and an `inert` or `inconclusive` verdict sets
the exit code, so the reviewer decides what an unproven property is worth.

Reconcile compares what the git log says changed against what the Steps
declared. A write outside the declaration, or a declared path never written,
is a finding. A write into a file another MSP owns is the serious one: both
pull requests then change that file, so merging them as they stand overwrites
one with the other. The Risk line of the pull request that made the write
names it, and it sets the exit code.

## Reading the result

The exit code is zero only when every MSP reached `shipped`, `pushed`,
`unchanged` or `committed`, every gate verdict was `pass` or `not-applicable`,
and reconcile found nothing. Every other outcome is a distinct non-zero code,
printed with its meaning on the report's last line. Each Lane ends `ok`,
`failed`, `blocked` or `merge-blocked`; each MSP ends `shipped`, `pushed`,
`unchanged`, `committed`, `ship-failed` or `blocked`.
An MSP is `unchanged` when its branch adds nothing to its pull request's base,
so nothing is pushed and no pull request opens. When mitosis chooses where a
dependant's pull request stacks, it uses that base in place of the unchanged
MSP's branch. The two Lane states that block dependents differ by the human
action they need: `merge-blocked` wants a conflict resolved, `blocked` wants a
predecessor fixed.

The last line of mitosis's own output is one JSON object: the exit code and
its meaning, every Lane with its state, reason, notes and log paths, every MSP
with its branch, base, pull request, title, body, gate verdict and reconcile
findings, and an `attention` list naming what a human or the calling session
should look at.
An agent that dispatched mitosis reads that line rather than the report.

The run directory, printed in the report, holds the plan, the state, the
persisted Steps, every Worker's full stdout and stderr, and the worktrees.
Read a Lane's log there rather than asking the Worker what it did; the
one-line return it printed is a claim, and the git log is the record.

Nothing is cleaned up after a failure except the gate's probe branch;
worktrees and branches survive so the work is inspectable.

## Resuming

Pass `--resume` with the same input and run directory. Lanes already `ok`
and MSPs already `shipped` or `pushed` are skipped, as is an MSP `unchanged`
whose producers all finished. An MSP that did not ship is gated again without
rebuilding its Lanes, and a producer whose branch was deleted after its pull
request merged is still found through its recorded commit on the feature
branch. The plan id must match: if the Steps changed, the prior results do not
apply, and mitosis refuses rather than resume against a different plan.

## Holding the work locally

Pass `--no-push` to build, gate and reconcile every MSP without publishing
anything. Each MSP ends `committed` on its local branch, or `unchanged` when it
adds nothing to its base: nothing is pushed and no pull request opens, so the
pull-request flags are not needed and `--pr-command` is refused. Run the
project's own checks against those branches, then `--resume` without
`--no-push` ships what was held. Resuming with `--no-push` again leaves a
`committed` MSP where it is.

## Checking an installed copy

--version prints the installed plugin's version, and its tests ship with it:

```
python3 "${CLAUDE_PLUGIN_ROOT}/mitosis.py" --version
python3 -m unittest discover "${CLAUDE_PLUGIN_ROOT}/tests"
```
