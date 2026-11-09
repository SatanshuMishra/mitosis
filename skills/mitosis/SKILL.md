---
name: mitosis
description: Use when one repository holds two or more changes that touch different files, or a document whose sections each become code, and the work should ship as separately reviewable pull requests built in parallel. Splits the work, builds each part on its own branch, checks that every part's named acceptance tests actually fail without the work or guard what must stay the same, opens one pull request per part, and never merges.
---

# mitosis

mitosis takes work that one agent would grind through serially, splits it so
several agents run at once, and produces pull requests a human can merge
without discovering that the parallelism broke something. A model may produce
anything; a model may judge nothing. Every gate in mitosis is a program.

This file says when to reach for it; --help describes every flag.

## When to reach for it

Reach for mitosis when all three hold:

1. Two or more changes are on the table, and at least two of them touch
   different files. Work that shares a file becomes one Lane and runs
   serially inside one branch; only disjoint write-sets buy parallelism.
2. Each change can name a test that proves it. The gate runs that test with
   the work present and again with the work reverted; a change with no such
   test is still built, but the run proves nothing about it and says so.
3. A human will review each part as its own pull request.

Do not reach for it when:

- There is one change, or every change touches the same files: the plan
  collapses to one Lane. Run with `--plan-only` when unsure; the report shows
  the Lane count before anything spawns.
- The result must land in the current working tree. Workers build from
  `--feature-branch` and never see uncommitted work. Commit first.
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
| `--dispatch-command` | one Worker session per Step, in its own clone | listed under --help |
| `--acceptance-command` | one acceptance property, in a worktree | file, test, worktree |
| `--pr-command` | one pull request per MSP; optional, refused with `--no-push` | listed under --help |
| `--render-command` | render --fields, per MSP at start and ship | none |
| `--pr-type` `--pr-scope` `--pr-subject` `--pr-why` `--pr-impact` `--pr-blast-radius` | nothing; they are facts handed to render | none |
| `--decompose-command` | the structure pass that turns a document into unbriefed Steps | prompt, model, document, session_id, schema |
| `--brief-command` | one Worker per unbriefed Step, writing its task | prompt, model, step, document, session_id, schema |
| `--reask-command` | the same session once more, for a reply failing its owned schema; required to call a model | prompt, session_id, schema, model |
| `--worker-sandbox` | each Worker, gate and lock-stage run under sandbox-exec, writing only its clone or checkout, temp, transcript and `--worker-write` paths; exit 3 without it | none |
| `--worker-write` | nothing; one more sandbox-writable path; repeatable; exit 3 if it overlaps the run directory, repository or its git directory | none |
| `--suite-command` | the full suite unmutated, then per surviving mutant; exit 1 kills only after an unmutated pass, else `weak` | worktree, report |
| `--gate-classes` | nothing; a JSON `{test, class, reason}` list classing properties | none |

A Worker's prompt is its whole brief. Dispatch, decompose and brief templates
must carry `{schema}`, the call schema as JSON text, and `{session_id}`, or
mitosis exits 3; the runs below show them with the re-ask template. A reply is read
only from the structured_output of the JSON object printed; one failing its
owned schema is re-asked once in that session, then fails its call. Every
hand-off mitosis writes is checked against `contracts/` first; a state, result
or lock record that fails exits 15.

A brief holds a Task line and JSON leaves inside the Lane header and
the clone, referee and return lines. `--prompt-template` replaces a build
Lane's brief after its Lane line, filling cluster, spec, tier and contract.
`--no-briefs` drops the briefs, only with `--plan-only`; else exit 2.

Tiers are labels; with `{model}` in the template, map every tier the plan
assigns, or mitosis refuses to start:

```
--tier-model top=claude-opus-5 --tier-model cheap=claude-sonnet-5
```

The acceptance template exits 0 on a pass, 1 on a fail and anything else
when it could not run, so the gate tells a broken build from a failing test:

```
--acceptance-command "python3 -m pytest {file} -k {test} -q"
```

pytest exits 5 when it collects nothing, so a misspelled test reads as
inconclusive. Wrap another runner to map its exit codes to those three.

Reverting the implementation deletes new modules, so the wrapper exits 1 when
an import fails on a module the revert removed, catching the whole import
family; any other load error stays inconclusive.

Exclude editor state and build caches, or they arrive as undeclared writes.

Two more things every run needs: `--feature-branch`, which must exist and is
what every MSP branches from, and the repository root as the working
directory, since every path is relative to it, or mitosis refuses to run.
`--timeout` is deprecated and ignored, mitosis has no wall-clock limit; a
command's exit ends every wait, and every process gets no input.

mitosis writes no pull-request text: at ship it writes each MSP's facts file,
runs pull-request render --fields on it and uses the printed title and body
unchanged; a refusal ends the MSP `ship-failed`, never retried. It needs
pull-request 0.3.0 or later on PATH or named with `--render-command`. Naming `--pr-type` and `--pr-subject`, even
with `--no-push`, needs `--pr-impact` or `--pr-blast-radius`,
and probes render before any Worker spawns. Pushing also needs `--pr-why`;
without `--pr-command` each MSP branch is pushed and ends `pushed`.

## The two inputs

Both converge on one list of Steps and one pipeline; nothing downstream can
tell them apart. Pass one, never both.

### Items: hand-written Steps

`--items` takes a JSON array that must meet h12 in `contracts/`, or exit 3:
every Step has `spec_ref`, its document sections, and `source`, the
document's absolute path and SHA-256. A validator, then its flag:

```json
[
  {"name": "validate-manifest",
   "task": "Add validate_manifest(path) to app/manifest.py, returning one error per missing key.",
   "files": ["app/manifest.py", "tests/test_manifest.py"],
   "source": {"path": "/work/app/docs/manifest.md", "sha256": "4bc2c86cd92744fbb47c8f13e04a42d78fec19f8c032ab091e4a8255720346c8"},
   "acceptance": [{"file": "tests/test_manifest.py", "test": "a_missing_key_is_reported_by_name"}],
   "spec_ref": ["1.1"]},
  {"name": "validate-flag",
   "task": "Add --validate to app/cli.py, printing those errors and exiting 1 if any.",
   "files": ["app/cli.py", "tests/test_cli.py"],
   "source": {"path": "/work/app/docs/manifest.md", "sha256": "4bc2c86cd92744fbb47c8f13e04a42d78fec19f8c032ab091e4a8255720346c8"},
   "acceptance": [{"file": "tests/test_cli.py", "test": "validate_exits_one_on_a_bad_manifest"}],
   "spec_ref": ["1.2"], "after": ["validate-manifest"]}
]
```

`after` orders the second behind the first; with disjoint write-sets they
become two MSPs, the second stacking its pull request on the first.

Steps sharing a `contract_group` are halves of one interface, built in sequence by one
Lane in one MSP. To build them in parallel, give one `"type": "contract"`
and make the others wait on it through `after`. One `msp` tag ships Steps
together.

Plan first; this validates, schedules and writes the plan, spawning nothing:

```
python3 "${CLAUDE_PLUGIN_ROOT}/mitosis.py" --items plan.json --plan-only
```

An `after` naming no Step is fatal; a write-set path not yet made, an empty
`acceptance` and a non-empty `assumptions` are counted, not fatal. When the
Lanes and tiers look right, run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/mitosis.py" --items plan.json \
  --charter docs/CHARTER.md \
  --feature-branch main \
  --dispatch-command "claude -p {task} --model {model} --session-id {session_id} --output-format json --json-schema {schema} --permission-mode bypassPermissions" \
  --reask-command "claude -p {prompt} --resume {session_id} --model {model} --output-format json --json-schema {schema} --permission-mode bypassPermissions" \
  --acceptance-command "python3 -m pytest {file} -k {test} -q" \
  --pr-command "gh pr create --head {branch} --base {base} --title {title} --body {body}" \
  --pr-type feat --pr-subject "validate manifests from the command line" \
  --pr-why "A bad manifest should fail before it ships." \
  --pr-impact "Bad manifests stop at validation." \
  --tier-model top=claude-opus-5 --tier-model cheap=claude-sonnet-5 \
  --concurrency 4
```

The pull-request template's last stdout line, an http or https URL, is
recorded as the pull request.

### Spec: a document

`--spec` takes a document in any format and reaches the plan in stages, so you
can read its split before any brief is bought.

`--plan-only` runs one structure dispatch over the whole document, because the
two fields that join its distant parts, `after` and `contract_group`, cannot
be seen from one section. The stage needs `--decompose-command`, and its
prompt is large, so let it arrive on stdin. No Step returned here carries a
`task` yet:

```
python3 "${CLAUDE_PLUGIN_ROOT}/mitosis.py" --spec docs/specs/search.md --plan-only \
  --decompose-command "claude -p --model {model} --session-id {session_id} --output-format json --json-schema {schema} --permission-mode bypassPermissions" \
  --reask-command "claude -p {prompt} --resume {session_id} --model {model} --output-format json --json-schema {schema} --permission-mode bypassPermissions" \
  --tier-model top=claude-opus-5
```

It persists its Steps and reports the Split shape findings, scalars,
unclaimed sections and assumptions, each keeping its Step from `simple`.

No finding stops a run; each is printed, put in its pull request and given an
exit code.

An assumption you would decide differently goes into the decisions file,
`--decisions PATH` (default: `<document without extension>.decisions.md` when
it exists), which binds every later structure dispatch and brief.

`--structure-samples N` runs N structure dispatches, ranks them and shows
where they disagree; the best is persisted, or the Kth with `--pick K`.

Add `--revise` to the same `--plan-only` command to revise the structure: one
small dispatch returns a delta, and only the Steps it touches cost a brief.

When the plan reads right, run it with `--resume`, the items run's flags with
`--spec` in place of `--items`, plus `--brief-command`; it briefs every
unbriefed Step in parallel, then builds.

The report's drift section says whether the document `source` names changed
since the Steps were cut.

## What a run does

Validate, schedule, lock the checks, one worktree and branch per MSP under the
run directory, one Worker session per Step, producers merged in first, then
per MSP: land, commit, gate, reconcile, push, open a pull request stacked on its single
predecessor when `--pr-command` is given, report.

Every Step of a Lane is its own Worker session, in Step order on its MSP's
branch, and lands one commit: subject `<type>(<scope>): <step>` (chore, no
scope, if absent), body its task's first sentence. Each starts once the one
before lands, may edit what earlier ones wrote and is told which Steps they
built; a resume starts at the first not `ok`.

Each session builds in its own clone of the MSP branch under the run
directory's `clones` folder, never in the MSP worktree.
`--concurrency` Workers, four by default, run at once. mitosis reads a
finished Lane's write-set from the clone's files, never running git there,
and lands only those paths as one commit. A finished Lane is `held` until no
Worker runs in its MSP, and a resume lands it without paying again. A write
outside the write-set is judged by reconcile only, exit 6, or reverted as a
referee write, exit 9; the gate never sees it. JSON `sandbox` is
sandbox-exec or `off`. Each git call
drops the diff, pathspec, repository and object variables. Producer merges run no hooks; a git command meeting
another process's lock is retried. A later change to a file whose Lane already
committed is kept in its own commit and named in the pull request, `attention`
and the exit code.

Steps with no file in common are ordered when they share a risk marker, a
recorded regression or a migration folder. A pair joined only by an import
runs in parallel and is named in `attention`; add an `after` edge if needed.

mitosis refuses with exit 3 when git has no commit identity. A refused commit
fails that Lane alone, so a hook refusing the Step commit's form fails every
Lane after its Worker has run.

The gate runs each acceptance property twice, with the work present and with
the implementation reverted. `pass`: it failed without the work.
`inert`: it passed without it, proving nothing. `inconclusive`: reverting
broke the build. `not-applicable`: the Step declared none, or the property is a
guard. A property that passes both ways is a guard of behaviour that must stay
the same when another property of its Step is `pass`; it gets no mutants, and
without that other property it stays `inert`. Implementation is derived: the MSP's
write-set minus the files its acceptance properties name.

Each passing property then runs against up to 12 mutants of its Step's added
lines, such as a flipped comparison, or dropped added lines when no operator
fits. `weak` means no mutant failed it, exit 24. An MSP whose gate is `weak`, `inert` or `inconclusive`
ends `gate-failed`: committed, never pushed, no pull request. `--trajectory`
also gets one outcome entry per acceptance test after every gate.

Mutants come only from added lines the with-work run executed. Each
property's mutation is killed, `weak`, `not-applicable` (the test loads none
of the Step's files) or covered-elsewhere (`--suite-command` passed unmutated,
then failed on each survivor; `killers` names the tests); `coverage` is
`untraced` unless its mutants came from the trace. `--gate-classes` classes a property `<file>::<name>`
`not-applicable`, covered-elsewhere or `removed` with a reason, judged
without a run and kept out of the plan id, so `--resume` rebuilds nothing; an
entry naming no property is refused.

A Step with `"role": "check-author"` is the lock stage, never a Lane, MSP
or pull request. Each MSP's check-author locks its tests on that MSP's branch,
from its own clone at the feature base: one per `msp` label, or the unlabelled
one split by acceptance owner. Each test must exit 0 with its uncommitted
probe, and each Step must hold one that exits 1 there; only the locked files
are committed, as the MSP's first commit. Its record is the MSP's `lock` in JSON `msps`; a failed one blocks
only it and the MSPs merging it.

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
lacks never reach it; `ignored` lists them.

Dependencies are the root's node_modules and venvs, or `--gate-setup`, run
once per gate with `{worktree}`; its output survives only at top-level names
the commit lacks, such as the `.git` a git-running test needs. A top-level referee
file or folder it makes, such as `conftest.py`, is fatal, exit 9. A Git LFS file stays
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
both pull requests as they stand overwrites one with the other. The pull
request that made the write names it under Trade-offs and open problems, and
it sets the exit code.

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

The run directory holds the plan, the state, the Steps, every Worker's
output, and the worktrees, which survive a failure for inspection. Read a Lane's log there; its return is a
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
to its base, and `--pr-command` is refused. Run the project's own checks on
those branches; `--resume` without `--no-push` ships what was held, and with
it leaves a `committed` MSP where it is.

`--proven-head <MSP label>=<commit>`, repeatable, ships a caller-proved HEAD
ungated when a passing gate is recorded; a Proven head row in Testing names
both commits. A malformed value or label exits 2.

## Metrics

Each invocation is one run in the metrics store, `~/.plugin-metrics` or
`PLUGIN_METRICS_DIR`, with one summary of split, gates, MSP states and spend.
Each model call's fresh `{session_id}` names the transcript it is priced
from. Every command mitosis starts gets `PLUGIN_METRICS_PARENT` naming the run.

## Checking an installed copy

--version prints the installed plugin's version, and its tests ship with it:

```
python3 "${CLAUDE_PLUGIN_ROOT}/mitosis.py" --version
python3 -m unittest discover "${CLAUDE_PLUGIN_ROOT}/tests"
```
