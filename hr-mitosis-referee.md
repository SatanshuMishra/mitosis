# hr-mitosis-referee: Run 2 of the harness-redesign program: mitosis as one locked referee
Status: active
Updated: 2026-10-28T19:48:57.856Z
Next ids: c7 D4 R5 S1

## Goal

Run 2 of the harness-redesign program (~/.mitosis/harness-redesign/PROGRAM.md, thread harness-redesign in the pipeline repository): make mitosis enforce referee exclusion, gate teeth, full finding reporting, bounded subprocess calls, usage-limit parking and K6 outcome entries, shipped as ready pull requests through the full lane under overrides O1 to O10.

## Done when

- [ ] c1 M1 Referee exclusion (reworded by D2): the charter's `- Locked:` paths plus referee globs can never be in a write-set: the gate, the mutant runs and every acceptance run execute in a clean checkout of the MSP's committed tree, with dependency folders from outside the Worker's control; a committed change to a `- Locked:` path or a referee glob, found by comparing tree objects with git's settings pinned, is reported fatal (exit 9) and reverted; uncommitted files in the Worker's worktree never reach the gate and are listed as ignored, never restored; `--plan-only` still refuses an items file whose write-set holds a referee path.
- [ ] c2 M2 Gate teeth: an acceptance test passes the gate only when it fails without the work and passes with it, and is not skipped: the broken-add fixture (a test that only checks a symbol exists) and a skipped test each fail the gate; an inert test fails the gate and holds the MSP back from shipping.
- [ ] c3 M3 The JSON result lists every finding of every MSP and the exit code is the most severe: a run with an inconclusive gate in MSP 1 and an undeclared write in MSP 2 reports both.
- [ ] c4 M4 Every git call and test run has a time limit: an AST test fails on any subprocess call without `timeout`; a hanging pre-commit hook ends with a named error.
- [ ] c5 M5 A Worker that stops at a usage limit is parked, not failed, and `--resume` re-dispatches only parked and failed Lanes: a stub Worker printing a usage-limit line leaves its Lane `parked-limit`, and a resume skips every `ok` Lane.
- [ ] c6 M6 After every gate, one K6 outcome entry per acceptance test is appended to the trajectory store passed with `--trajectory`.

## Where things stand

Stage 3, round 4 loop at 8015a99 on MSP mitosis/hr-mitosis-referee over base 5a94f41, with the hidden-untracked-files kind on the amendment path (requirement-wrong; ledger line corrected by hand after pipeline diagnosis refused a second record, backup review/round-4/findings.jsonl.before-requirement-wrong). Fixed with receipts on the MSP branch: every round 1 to 3 blocker and advisory, round 4 C1 at e10fde7 and S1 at 2163c08. Running: the agent finishing S2 (referee stage skipped on commit failure; receipts 8483108 and 4270c6f committed, its run.py fix uncommitted while its suites run). The build script amend-1.zsh and amend-1.items.json are in the run home; the logbook server is down, so the amendment boundary is not yet recorded. Amendment 1 (D2) is handed off and reported to the parent session: spec, decisions, record and docs/specs/hr-mitosis-referee.amend-1.items.json, two Steps in MSP hr-mitosis-referee-amend-1, 14 new rows for the check author in tests/test_clean_gate.py, glossary terms Gate folder and Ignored file, ADR 0009 and an amended ADR 0006. Rejected and outside-class items for the final report: round 1 C4, S6, S3, S7, S8; round 2 C2, S1, S4; round 3 S2, S3; round 4 S3; the automated commit flags. The checkout holds this run's uncommitted glossary section and ADRs 0006 to 0009, which ship only with the MSP.

## Next step

1. Verify the S2 finisher's commit, the locked files (zsh array split, with a control) and both test suites. 2. A fresh check-author agent writes tests/test_clean_gate.py from the 14 amendment rows on mitosis/hr-mitosis-referee, confirms each fails at the current head, commits it, and adds `- Locked: tests/test_clean_gate.py` and the new receipt-lock to CHARTER.md. 3. Run ~/.mitosis/mitosis/hr-mitosis-referee/amend-1.zsh with --plan-only, then the build, then merge the amendment branch into mitosis/hr-mitosis-referee. 4. Rerun pipeline obligations with the amendment criteria, then review round 5.

Needs: D1, D3, R1, R2.

## Decisions in force

- D1 Interphase hand-off: build the handed-off spec as one MSP of seven Steps: Hand off now: one MSP, seven ordered Steps, criteria 5.1 to 5.6 as M1 to M6 and 5.7 for docs and version 0.8.0, built from feat/hr-mitosis-referee cut from main 1391d39 after the O2 check author locks the acceptance tests.

- D3 Amendment 1 hand-off: build M1 as a clean gate checkout in MSP hr-mitosis-referee-amend-1: Hand off amendment 1. The gate, the mutant runs and every acceptance run execute in a clean checkout of the MSP's committed tree. A committed referee write, found from tree objects with git's settings pinned, is reverted and exits 9. Uncommitted files are listed as ignored and never restored. The amendment is built with amend-1.zsh (--plan-only first, always --no-push) and merged into mitosis/hr-mitosis-referee, then review round 5 judges it.

## Open risks

- R1 The paused step-commits-render-fields run (branch mitosis/step-commits-render-fields, round 1 diagnose, feature feat/step-commits-render-fields) changes run.py, mitosis.py and core.py too; this run cuts from main, so whichever merges second resolves the conflicts.
- R2 The owner's checkout holds an uncommitted GLOSSARY.md edit and untracked docs/adr/0004 and 0005 from step-commits-render-fields; this run builds from committed main and never touches them.
- R3 The installed mitosis 0.7.0 composes its own pull-request title and body, while the owner's hook accepts only what `pull-request render` prints; at ship, a refused title or body is taken from `pull-request render`, never edited by hand.
- R4 A referee-named file inside an installed dependency tree other than node_modules or a pyvenv.cfg folder holds the MSP referee-reverted (exit 9); every recent Laravel app's vendor/laravel/prompts/phpunit.xml does this, so a Laravel Worker that runs composer install is held back. Held by decision since round 2; a top risk for the final report.

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
- Automated commit security flags and their judgements (outside-class items for the final report): /Users/satanshumishra/.mitosis/mitosis/hr-mitosis-referee/review/automated-security-notes.md
- Amendment build: /Users/satanshumishra/.mitosis/mitosis/hr-mitosis-referee/amend-1.zsh with amend-1.items.json beside it
