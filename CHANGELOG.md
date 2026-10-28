# Changelog

Each heading is a release of the mitosis plugin. An installed copy updates
only when the version changes, so every release changes it.

## 0.8.0 - 2026-10-28

mitosis no longer writes pull-request text itself, and each Step lands as its
own commit.

- At ship mitosis writes each MSP's facts to a facts file, runs
  pull-request render --fields on it, and uses the title and body render
  printed unchanged. Render writes the body from those facts: Problem from
  the `--pr-why` lines, Change one entry per Step, Impact, Testing one row
  per acceptance test with a Not run row and its reason for each one left
  unproven, and Trade-offs and open problems for what reconcile, stacking or
  the build found. A refusal ends the MSP `ship-failed` with render's problem
  as its reason and is never retried with altered facts.
- A run needs pull-request 0.3.0 or later on PATH, or named with the new
  `--render-command`. Every run that names `--pr-type` and `--pr-subject`,
  `--plan-only` and `--no-push` included, needs `--timeout` to bound the probe
  and runs render on each MSP's facts
  before any Worker is dispatched, and refuses the run when the command is
  missing, older than 0.3.0 or refuses the facts.
- The new repeatable `--pr-impact` gives the Impact lines; without it the
  `--pr-blast-radius` sentence stands in, and a run that names `--pr-type`
  and `--pr-subject` with neither is refused. `--pr-door` is still accepted
  and no longer reaches the body.
- Every Step of a Lane is its own Worker session, run in Step order on its
  MSP's branch, and lands its own commit with the subject
  `<type>(<scope>): <step>`, or chore with no scope when the run names no
  type, and its task's first sentence as the body. `--session-tokens` only
  sizes the estimate.

## 0.7.0 - 2026-10-27

mitosis records metrics about its own runs, and a new metrics skill answers
how it is performing from them.

- Every invocation of the mitosis command is one run in the metrics store,
  `~/.plugin-metrics`, or `PLUGIN_METRICS_DIR` when it is set, once its run
  directory is known: a plan, a build, a resume or a ship. It appends events
  at the boundaries of its work, the run's start and end, the structure and
  brief stages, the plan, each Lane, each gate, reconcile and each MSP, and
  replaces one run summary holding the split, the parallelism, the gate
  outcomes, the MSP states and its own spend. The store holds run metadata
  only, never a task, a brief, a Worker's output or file contents.
- A run's own spend is priced when its summary is written, by exact model id
  in the five token kinds, from the price table `prices.json` and its date.
  A model the table lacks keeps its tokens with no price, and a call whose
  transcript is not found is counted in `sessions_missing`, so
  `spend_complete` says whether the figure is whole.
- Every Worker dispatch, structure call and brief call gets a fresh session
  id, offered as the `{session_id}` placeholder on the dispatch, decompose
  and brief templates, for `claude -p --session-id {session_id}`. A Lane
  records it as `session_id`, and its spend is read from that session's
  transcript.
- Every command mitosis starts gets `PLUGIN_METRICS_PARENT` naming the run,
  so a run another tool records under it is listed among its children, with
  that child's own spend kept apart.
- `catalogue.json` describes mitosis's purpose, its objectives, every event
  and field it writes and six named queries, and is copied into the store.
  The metrics skill reads it first and answers through the duckdb command
  over plugin=mitosis alone; without DuckDB it names `brew install duckdb`
  and stops.
- A metrics write that fails prints one line on stderr and never changes the
  exit code or the last JSON line.
- `docs/metrics-format.md` describes the shared record format.

## 0.6.0 - 2026-10-16

A slow acceptance test no longer runs twice on code that was already gated
or proven.

- A resume reuses an MSP's recorded gate result when that result passed, the
  MSP's HEAD is the commit the gate ran at, and its worktree holds no
  uncommitted change. Any other MSP is gated again, so a blocking gate is
  always retried. Only the same run directory's state is read.
- `--proven-head <MSP label>=<commit>`, repeatable, lets a caller that proved
  a later head ship it without gating again, when the MSP's HEAD equals that
  commit, its worktree is clean and a gate result that passed is recorded.
  The pull request's Verification section then ends with a Not verified:
  line naming the commit the gate ran at and the commit the caller proved. A
  head that differs, or an MSP with no recorded gate result, is gated as
  before. A value that is not a label, an equals sign and 7 to 40 lowercase
  hexadecimal digits, or that names no MSP of the plan, exits 2 before
  anything is built.

## 0.5.0 - 2026-10-16

Every pull request body can carry the merge-danger call the pull-request
plugin renders, and mitosis no longer describes its pull requests as drafts.

- `--pr-door` takes one-way or two-way and `--pr-blast-radius` takes a
  sentence; given together, they open every body's Risk section as a
  `Door:` line and a `Blast radius:` line, ahead of any line reconcile, the
  gate or stacking writes. One without the other, a door outside the two
  values, or a blast radius that is not a house sentence is refused before
  any Worker spawns. The run state records the pair, a resume that leaves
  both flags out carries it over, and a resume that passes a different pair
  is refused, so every pull request of one run carries one door call.
- A Why line, the subject, or any other value beginning with `door:` or
  `blast radius:` is refused, as `verified:` already was, so no value can
  pose as a merge-danger line.
- The `--pr-command` help, the skill, the README and the plugin manifests
  speak of pull requests, not draft pull requests, and the skill's example
  command no longer opens a draft.

## 0.4.0 - 2026-10-15

A Lane too large for one Worker session is built as several fresh sessions
in a row on its MSP's branch.

- Every Lane carries an estimate of the tokens its session will hold, from
  its Step count, the length of each task, the sizes of its write-set and
  read-set files, the charter and document every session reads, and, for a
  later session of a split Lane, the files the earlier sessions wrote.
- A Lane estimated above the new `--session-tokens` flag, 150000 by default,
  is split in its Step order into consecutive Lanes. Each waits on the one
  before it, runs once that one has landed, may edit every file the earlier
  sessions wrote, and its brief names the Steps they built and the Steps left
  for later sessions. A Lane waiting on a split Lane waits on its last
  session.
- The plan-stage report prints each Lane's estimate, names every split Lane
  and its sessions, and names a Step whose estimate alone is above the limit.
  The JSON summary gives each Lane its session position.
- Gate, reconcile and ship are unchanged, since they work per MSP. A resume
  skips every session already `ok` and continues from the next one. A file a
  session left half-written when it failed stays out of another Lane's
  landing, so the next run of that session finishes it.
- The plan records the file sizes it measured, and a resume plans from those
  sizes, so a file that changed in the checkout after the run started does
  not change the plan id.

## 0.3.0 - 2026-10-10

Every pull request mitosis writes follows one house format, and a run can
ship without a pull-request command.

- The title is `<type>(<scope>): <subject>` from the new `--pr-type`,
  `--pr-scope` and `--pr-subject` flags, with the MSP label in parentheses
  when the plan has several MSPs.
- The body holds What changed, one entry per Step from its task's first
  sentence; Why, the lines given with `--pr-why`; Risk, only when reconcile,
  a foreign write, a manifest, stacking or the gate found something; and
  Verification, one line per acceptance property by its gate verdict. No gate
  reason, acceptance command or draft notice is written into it.
- `--pr-type`, `--pr-subject` and at least one `--pr-why` are required on a
  run allowed to push, and every one of the four is checked before any
  Worker spawns.
- `--pr-command` is optional. Without it a run allowed to push pushes each
  MSP branch, opens no pull request, ends that MSP in the new state `pushed`,
  and records the title and body in its state and in the JSON summary. A
  resume skips a `pushed` MSP, and `pushed` counts toward exit 0.

## 0.2.0 - 2026-09-21

The first release that installs as a Claude Code plugin and updates by
version.

- The skill runs the copy the plugin installed, and the plugin's version is
  the one `--version` prints.
- Every plan runs and every MSP ships as a draft pull request. A finding is
  written into the pull request, the JSON summary and the exit code; it never
  stops the run.
- MSPs that wait on each other are fused into one. Steps that share a risk
  marker, a recorded regression or a migration folder are put in order, and a
  pair joined only by an import is named before anything is built.
- A Lane that changes a file another Lane owns is named in the pull request.
- `--no-push` builds and commits every MSP without pushing or opening a pull
  request.
- The last line of stdout is a one-line JSON summary for the calling session.
- Four Workers run at once and each read-set holds at most forty files, by
  default.
- The version is part of each plan's id, so a run started on one release
  cannot be resumed on another.

## 0.1.0

The development version. It was never released.
