---
name: mitosis
description: Use when one repository holds two or more changes that touch different files, or a document whose sections each become code, and the work should ship as separately reviewable pull requests built in parallel. Splits the work, builds each part on its own branch, checks that every named acceptance test actually fails without the work, opens one pull request per part, and never merges.
---

# mitosis

mitosis takes work that one agent would grind through serially, splits it so
several agents run at once, and produces pull requests a human can merge
without discovering that the parallelism broke something. A model may produce
anything; a model may judge nothing. Every gate in mitosis is a program.

This file says when to reach for it and how; --help describes every flag.

## When to reach for it

Reach for mitosis when all three hold:

1. Two or more changes are on the table, and at least two of them touch
   different files. Work that shares a file becomes one Lane and runs
   serially inside one branch; only disjoint write-sets buy parallelism.
2. Each change can name a test that proves it. The gate runs that test with
   the work present and again with the work reverted; a change with no such
   test is still built, but the run proves nothing about it and says so.
3. A human will review each part as its own pull request. mitosis opens
   pull requests, or pushes the branches for the caller to open, and
   stops.

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
| `--pr-command` | one pull request per MSP; optional, refused with `--no-push` | listed under --help |
| `--pr-type` `--pr-scope` `--pr-subject` `--pr-why` `--pr-door` `--pr-blast-radius` | nothing; they write every title and body | none |
| `--decompose-command` | the structure pass that turns a document into unbriefed Steps | prompt, model, document |
| `--brief-command` | one Worker per unbriefed Step, writing its task | prompt, model, step, document |

A Worker spawned from this skill, with the Lane brief as its whole prompt
and the model chosen by tier:

```
claude -p {task} --model {model} --session-id {session_id} --permission-mode bypassPermissions
```

The brief carries all the Worker needs: the charter and document paths, the
Steps in order, write-set, read-set and return contract. The last
line of its output that parses as a JSON object is its return, so narration
after it or a fence around it is tolerated.

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

The wrapper has one more duty. Reverting the implementation deletes what the
work created, so a test importing a new module stops loading. A module missing
because it was reverted means the behaviour does not exist: exit 1. Any other
load error stays inconclusive. Catch the whole import family, since importing
a name out of a surviving package raises the general error.

Exclude whatever else writes into a worktree, such as editor state and build
caches, or it arrives as undeclared writes against every MSP.

Three more things every run needs: `--feature-branch`, which must exist and is
what every MSP branches from; `--timeout` in seconds on a monotonic clock,
applied to every Worker, acceptance run and pull-request command; and the
repository root as the working directory, since every path is relative to it,
or mitosis refuses to run.

Every pull request is written in the house format. The title is
`<type>(<scope>): <subject>` from `--pr-type`, the optional `--pr-scope` and
`--pr-subject`, with the MSP label in parentheses after the subject when the
plan has several MSPs. The body holds What changed, one entry per Step from
its task's first sentence; Why, the one to eight `--pr-why` lines; Risk, the
door and blast-radius lines, then any reconcile, foreign-write, manifest,
stacking or gate finding; and Verification, a Verified line per property the
gate proved and a Not verified line for every other. A run allowed to push
needs `--pr-type`, `--pr-subject` and `--pr-why`, checked before any Worker
spawns. Without `--pr-command` it pushes each MSP branch and ends that MSP
`pushed`, recording its title and body for the caller to open.

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
    "task": "Add validate_manifest(path) to app/manifest.py, returning one error string per missing required key (name, version, entry) and an empty list for a complete manifest.",
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
    "task": "Add a --validate flag to app/cli.py that prints validate_manifest's errors for the given path, one per line, and exits 1 if there are any, else 0.",
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

Steps sharing a `contract_group` are halves of one interface, so one Worker
builds them in sequence in one MSP, which stops two Workers inventing two
shapes for it. To build the halves in parallel, give exactly one of them
`"type": "contract"` to fix the interface and make every other wait on it
through `after`. To ship Steps together without making them one Worker's, give
them one `msp` tag.

Plan first. This validates and schedules the Steps, writes the plan into the
run directory and prints the plan-stage report; it spawns nothing:

```
python3 "${CLAUDE_PLUGIN_ROOT}/mitosis.py" --items plan.json --plan-only
```

An `after` naming a Step that does not exist is fatal and is
named; a write-set path that does not exist yet, an empty `acceptance` list
and a non-empty `assumptions` list are each counted, not fatal. When the
Lane count and the tiers look right, run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/mitosis.py" --items plan.json \
  --charter docs/CHARTER.md \
  --feature-branch main \
  --dispatch-command "claude -p {task} --model {model} --permission-mode bypassPermissions" \
  --acceptance-command "python3 -m pytest {file} -k {test} -q" \
  --pr-command "gh pr create --head {branch} --base {base} --title {title} --body {body}" \
  --pr-type feat --pr-subject "validate manifests from the command line" \
  --pr-why "A bad manifest should fail before it ships." \
  --tier-model top=claude-opus-5 --tier-model cheap=claude-sonnet-5 \
  --timeout 1800 --concurrency 4
```

The pull-request template's last stdout line is recorded as the pull request.

### Spec: a document

`--spec` takes a document in any format and reaches the plan in stages, so you
can read its split before any brief is bought.

`--plan-only` runs one structure dispatch over the whole document, because the
two fields that join its distant parts, `after` and `contract_group`, cannot
be seen from one section. The stage needs `--decompose-command`, and its
prompt is large, so let it arrive on stdin. No Step returned here carries a
`task`; nothing is briefed yet:

```
python3 "${CLAUDE_PLUGIN_ROOT}/mitosis.py" --spec docs/specs/search.md --plan-only \
  --decompose-command "claude -p --model {model} --permission-mode bypassPermissions" \
  --tier-model top=claude-opus-5 \
  --timeout 900
```

It spawns one model process, persists
its Steps and prints a report. Read four things: the Split shape findings,
worst first; the scalars below them, which score the split before any brief
exists; the coverage map of sections no Step claimed; and the assumptions,
each a reading chosen where the document was underdetermined, which keeps
its Step from being rated `simple`.

No finding stops a run. Each one is printed, written into the pull request it
belongs to, listed for the caller and given a distinct exit code.

`manifest-exports-nothing` means the file that declares what a package exports
is written by a Step built before the modules it must export, so it would ship
empty while every test still passes. Give that file to a Step with an `after`
edge reaching every Step whose modules it exports.

`lane-cycle` means Lanes that waited on each other, so one Worker builds them
in one sitting; mitosis fuses such MSPs and Lanes rather than refuse, and
`lane_cycles` and `fused_without_overlap` count what that cost.

An assumption you would have decided differently goes into the decisions file,
in your own words, not back into the document; name it with `--decisions PATH`
(default: `<document without extension>.decisions.md` when that file exists);
the report's Decisions section counts its settled questions. That file binds
every later structure dispatch and brief for this document; mitosis never
rewords an entry or re-asks a question answered once.

`--structure-samples N` runs N structure dispatches at once, ranks them by
the same scalars and shows where they disagree; the best is persisted, or the
Kth with `--pick K`. A file in dispute is one the document never assigned.

Revise the structure once the decisions file has grown, without paying to
rebuild every Step: run the same `--plan-only` command with `--revise` added.

`--revise` hands the persisted structure to one small dispatch that returns
only a delta; every other Step is kept as it was, brief included, so only the
Steps the delta changes, adds or removes cost a brief.

When the plan reads right, run it with `--resume`. It writes a brief for every
Step still unbriefed, in parallel, then plans and builds; a revision that
touched three Steps buys three brief dispatches. Pass the items run's flags
with `--spec` and `--resume` in place of `--items`, plus `--brief-command`
taking the structure stage's template.

Every Step carries the document's path and hash in `source`, so each Worker
opens the document from its own worktree, and the report's drift section says
whether it changed since the Steps were cut.

## What a run does

Validate, schedule, one worktree and branch per MSP under the run directory,
one Worker per Lane with producers merged in first, then per MSP: commit,
gate, reconcile, push, open a pull request stacked on its single
predecessor when `--pr-command` is given, report.

mitosis estimates each Lane's tokens and splits one above `--session-tokens` (default 150000), in Step order, into consecutive
Lanes. Each runs as a fresh session once the one before lands, may edit what
earlier ones wrote and is told which Steps they built; a resume starts at the
first not `ok`. The split quality section names each split and any Step too
large alone.

The Lanes of one MSP share its worktree; `--concurrency` Workers, four by
default, run at once, each told to change only its write-set.
A finished Lane is `held` until no Worker runs in its worktree, then
committed, so a hook that stashes unstaged files never touches a sibling's
work; no new Lane starts in that MSP meanwhile, and a resume lands a `held`
Lane without paying again. Producer merges run no hooks; a git command meeting
another process's lock is retried, and every git call times out after 600
seconds with a named error. A later change to a file whose Lane already
committed is kept in its own commit and named in the pull request, `attention`
and the exit code.

Steps with no file in common are ordered when both touch one risk marker, a
recorded regression links them, or both add a numbered migration to one
folder. A pair joined only by an import runs in parallel and is named in the
Coupling section and `attention`: add an `after` edge where one needs the
other's output.

mitosis refuses with exit 3 when git has no commit identity. A refused Lane
commit fails that Lane alone. Hooks
see each Lane's message, `chore(<MSP>): land Lane <n> (<Steps>)`, so a
hook that refuses that form fails every Lane after its Worker has run.

The gate runs each acceptance property twice, with the work present and with
the implementation reverted. `pass`: it failed without the work.
`inert`: it passed without it, proving nothing. `inconclusive`: reverting
broke the build. `not-applicable`: the Step declared none. Implementation is derived: the MSP's
write-set minus the files its acceptance properties name.

Each passing property then runs against up to 12 mutants of its Step's added
lines, such as a flipped comparison, or dropped added lines when no operator
fits. `weak` means no mutant failed it, exit 24. An MSP whose gate is `weak`, `inert` or `inconclusive`
ends `gate-failed`: committed, never pushed, no pull request. `--trajectory`
also gets one outcome entry per acceptance test after every gate.

The referee is what judges the work, and no Worker writes it: the charter's
`- Locked:` lines, plus built-in markers for test configuration, snapshots,
baselines, allowlists and skip lists. Planning refuses a Step whose files hold
a referee path, exit 3. A committed write to one is reverted; it, or an
acceptance run's write, holds the MSP back `referee-reverted`, exit 9. The JSON
result's `referee` object names the locked paths, their receipt lock and
commit. A real run exits 3 on a `- Locked:` path the feature branch lacks, or
a `- Locked:` line that is empty, absolute or contains `..`.

Every gate, mutant and acceptance run uses a clean checkout of the commit,
not a git repository, reset to raw blobs before each run; files the commit
lacks never reach it and are listed in `ignored`.

Dependencies are the root's node_modules and venvs, or `--gate-setup`, run
once per gate with `{worktree}`; its output survives only at top-level names
the commit lacks, such as the `.git` a git-running test needs. A top-level referee
file it makes, such as `conftest.py`, is fatal, exit 9. A Git LFS file stays
pointer text and eol attributes may not apply, whatever `--gate-setup` does;
a test reading real content ends `inconclusive`. That, a failed gate setup or
a refused pre-gate commit holds the MSP back `gate-failed`, exit 21. Ship
refuses any head but the gated one, `ship-failed`, exit 22.

An unreadable base submodule whose gitlink the branch changed cannot be
judged, exit 21; a committed referee write inside one is reverted by
resetting its gitlink to the base's, exit 9.

Reconcile compares what the git log says changed against what the Steps
declared. A write outside the declaration, or a declared path never written,
is a finding. A write into a file another MSP owns is the serious one: merging
both pull requests as they stand overwrites one with the other. The Risk line of the pull request that made the write
names it, and it sets the exit code.

## Reading the result

The exit code is zero only when every MSP reached `shipped`, `pushed`,
`unchanged` or `committed`, every gate verdict was `pass` or `not-applicable`,
and reconcile found nothing. Otherwise it is the most severe entry of the JSON
result's `findings` list, printed with its meaning on the report's last line.
Each Lane ends `ok`, `failed`, `blocked`, `merge-blocked` or `parked-limit`;
each MSP ends `shipped`, `pushed`, `unchanged`, `committed`, `ship-failed`,
`blocked`, `referee-reverted`, `gate-failed` or `parked`. An MSP is
`unchanged` when its branch adds nothing to its pull request's base, so
nothing is pushed or opened, and a dependant's pull request stacks on that
base instead. Of the two Lane states that block dependents, `merge-blocked`
wants a conflict resolved and `blocked` a predecessor fixed.

The last line of mitosis's output is one JSON object: the exit code and
its meaning, every Lane with its state, reason, notes and log paths, every MSP
with its branch, base, pull request, title, body, gate verdict and reconcile
findings, and an `attention` list of what a human or the caller should look
at. An agent that dispatched mitosis reads that line, not the report.

The run directory holds the plan, the state, the persisted Steps, every
Worker's full stdout and stderr, and the worktrees, which survive a failure
with their branches for inspection. Read a Lane's log there; its return is a
claim, and the git log is the record.

## Resuming

A Worker stopped at a usage limit parks its Lane `parked-limit` and its MSP
`parked`, exit 13; `--resume` after the reset runs every Lane not `ok`.

Pass `--resume` with the same input and run directory. Lanes already `ok` and
MSPs already `shipped` or `pushed` are skipped, as is an MSP `unchanged` whose
producers all finished. An MSP that did not ship is gated again, without
rebuilding its Lanes, unless its recorded gate passed at its current HEAD. A
producer whose branch was deleted after its pull request merged is found
through its recorded commit on the feature branch. If the Steps changed, the plan id differs and mitosis refuses to resume.

## Holding the work locally

Pass `--no-push` to build, gate and reconcile every MSP and publish nothing:
each ends `committed` on its local branch, or `unchanged` when it adds nothing
to its base. The pull-request flags are then not needed and `--pr-command` is
refused. Run the project's own checks on those branches; `--resume` without
`--no-push` ships what was held, and with it leaves a `committed` MSP where it
is.

`--proven-head <MSP label>=<commit>`, repeatable, ships a caller-proved HEAD
ungated when a passing gate is recorded; a Not verified: line names both
commits. A malformed value or label exits 2.

## Metrics

Each invocation is one run in the metrics store, `~/.plugin-metrics` or
`PLUGIN_METRICS_DIR`, with one summary of split, gates, MSP states and spend.
Each model call gets a fresh `{session_id}` placeholder; given to claude -p
--session-id as above, it names the transcript the call is priced from. Every
command mitosis starts gets `PLUGIN_METRICS_PARENT` naming the run.

## Checking an installed copy

--version prints the installed plugin's version, and its tests ship with it:

```
python3 "${CLAUDE_PLUGIN_ROOT}/mitosis.py" --version
python3 -m unittest discover "${CLAUDE_PLUGIN_ROOT}/tests"
```
