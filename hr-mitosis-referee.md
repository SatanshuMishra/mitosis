# hr-mitosis-referee: Run 2 of the harness-redesign program: mitosis as one locked referee
Status: active
Updated: 2026-10-28T09:55:39.938Z
Next ids: c7 D2 R3 S1

## Goal

Run 2 of the harness-redesign program (~/.mitosis/harness-redesign/PROGRAM.md, thread harness-redesign in the pipeline repository): make mitosis enforce referee exclusion, gate teeth, full finding reporting, bounded subprocess calls, usage-limit parking and K6 outcome entries, shipped as ready pull requests through the full lane under overrides O1 to O10.

## Done when

- [ ] c1 M1 Referee exclusion: the charter's `- Locked:` paths plus referee globs (test configuration, baselines, allowlists, snapshots, skip lists) can never be in a write-set: `--plan-only` refuses an items file whose write-set includes one, and reconcile reports a Worker write to one as fatal and reverts it.
- [ ] c2 M2 Gate teeth: an acceptance test passes the gate only when it fails without the work and passes with it, and is not skipped: the broken-add fixture (a test that only checks a symbol exists) and a skipped test each fail the gate; an inert test fails the gate and holds the MSP back from shipping.
- [ ] c3 M3 The JSON result lists every finding of every MSP and the exit code is the most severe: a run with an inconclusive gate in MSP 1 and an undeclared write in MSP 2 reports both.
- [ ] c4 M4 Every git call and test run has a time limit: an AST test fails on any subprocess call without `timeout`; a hanging pre-commit hook ends with a named error.
- [ ] c5 M5 A Worker that stops at a usage limit is parked, not failed, and `--resume` re-dispatches only parked and failed Lanes: a stub Worker printing a usage-limit line leaves its Lane `parked-limit`, and a resume skips every `ok` Lane.
- [ ] c6 M6 After every gate, one K6 outcome entry per acceptance test is appended to the trajectory store passed with `--trajectory`.

## Where things stand

Stage 2 build round 0 is running (run.zsh, output in the run home's build.out, build.err and build.exit). feat/hr-mitosis-referee holds the locked checks at 5a94f41 (13 files, 28 acceptance tests all red for absent behaviour, existing suite green on 3.9 and 3.15, no write under the real HOME); the charter lists them on `- Locked:` lines and receipts-lock.txt holds their receipt-lock line. Plan-only passed with one MSP of seven Lanes. The checkout holds this run's uncommitted glossary section "Referee and gate" and ADRs 0006 to 0008, which ship only with the MSP.

## Next step

When the build exits: read build.out's last line, write each Lane's Assumption notes to review/hr-mitosis-referee.assumptions.txt, run the locked checks at the MSP head (O4), then start review round 1.

Needs: D1, R1, R2.

## Decisions in force

- D1 Interphase hand-off: build the handed-off spec as one MSP of seven Steps: Hand off now: one MSP, seven ordered Steps, criteria 5.1 to 5.6 as M1 to M6 and 5.7 for docs and version 0.8.0, built from feat/hr-mitosis-referee cut from main 1391d39 after the O2 check author locks the acceptance tests.

## Open risks

- R1 The paused step-commits-render-fields run (branch mitosis/step-commits-render-fields, round 1 diagnose, feature feat/step-commits-render-fields) changes run.py, mitosis.py and core.py too; this run cuts from main, so whichever merges second resolves the conflicts.
- R2 The owner's checkout holds an uncommitted GLOSSARY.md edit and untracked docs/adr/0004 and 0005 from step-commits-render-fields; this run builds from committed main and never touches them.

## Settled, do not reopen

## Where the details live

- Program: /Users/satanshumishra/.mitosis/harness-redesign/PROGRAM.md (run 2 of harness-redesign; intent I1 to I6, contracts K1 to K7, overrides O1 to O10)
- Contracts: /Users/satanshumishra/.mitosis/harness-redesign/contracts/ (k1-acceptance, k2-locked, k3-check-result, k6-outcome)
- Audit: /Users/satanshumishra/.mitosis/harness-redesign/audit/mitosis.md
- Run home: /Users/satanshumishra/.mitosis/mitosis/hr-mitosis-referee
- Run directory: /Users/satanshumishra/.mitosis/runs/mitosis/hr-mitosis-referee
- Spec, decisions, record and items: /Users/satanshumishra/Documents/DevLabs/mitosis/docs/specs/hr-mitosis-referee.{md,decisions.md,record.json,items.json}
- Record generator: /private/tmp/claude-501/-Users-satanshumishra-Documents-DevLabs-mitosis/6f256c0e-eb96-4236-a7f2-dfa8b481c5eb/scratchpad/make_record.py
- receipts 0.9.5 CLI (offline): node ~/.npm/_npx/d44e7b340d275612/node_modules/receipts-cli/bin/receipts.js
