# Changelog

Each heading is a release of the mitosis plugin. An installed copy updates
only when the version changes, so every release changes it.

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
