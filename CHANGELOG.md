# Changelog

Each heading is a release of the mitosis plugin. An installed copy updates
only when the version changes, so every release changes it.

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
