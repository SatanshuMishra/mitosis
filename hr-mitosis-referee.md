# hr-mitosis-referee: Run 2 of the harness-redesign program: mitosis as one locked referee
Status: active
Updated: 2026-10-28T20:37:59.107Z
Next ids: c7 D4 R5 S1

## Goal

Run 2 of the harness-redesign program (~/.mitosis/harness-redesign/PROGRAM.md, thread harness-redesign in the pipeline repository): make mitosis enforce referee exclusion, gate teeth, full finding reporting, bounded subprocess calls, usage-limit parking and K6 outcome entries, shipped as ready pull requests through the full lane under overrides O1 to O10.

## Done when

- [ ] c1 M1 Referee exclusion (reworded by D3): the charter's `- Locked:` paths plus referee globs can never be in a write-set: the gate, the mutant runs and every acceptance run execute in a clean checkout of the MSP's committed tree, with dependency folders from outside the Worker's control; a committed change to a `- Locked:` path or a referee glob, found by comparing tree objects with git's settings pinned, is reported fatal (exit 9) and reverted; uncommitted files in the Worker's worktree never reach the gate and are listed as ignored, never restored; `--plan-only` still refuses an items file whose write-set holds a referee path.
- [ ] c2 M2 Gate teeth: an acceptance test passes the gate only when it fails without the work and passes with it, and is not skipped: the broken-add fixture (a test that only checks a symbol exists) and a skipped test each fail the gate; an inert test fails the gate and holds the MSP back from shipping.
- [ ] c3 M3 The JSON result lists every finding of every MSP and the exit code is the most severe: a run with an inconclusive gate in MSP 1 and an undeclared write in MSP 2 reports both.
- [ ] c4 M4 Every git call and test run has a time limit: an AST test fails on any subprocess call without `timeout`; a hanging pre-commit hook ends with a named error.
- [ ] c5 M5 A Worker that stops at a usage limit is parked, not failed, and `--resume` re-dispatches only parked and failed Lanes: a stub Worker printing a usage-limit line leaves its Lane `parked-limit`, and a resume skips every `ok` Lane.
- [ ] c6 M6 After every gate, one K6 outcome entry per acceptance test is appended to the trajectory store passed with `--trajectory`.

## Where things stand

Stage 3, review round 5 setup on MSP mitosis/hr-mitosis-referee over base 5a94f41, head 8abacb8: the round 1 to 4 fixes (round 4 C1, S1 and S2 last, at 50fd80c), the check author's locked tests/test_clean_gate.py at d32c436, and amendment 1 (D3) built by amend-1.zsh and fast-forwarded in (Lanes clean-gate-checkout and clean-gate-docs ok; gate 11 pass, 3 inert regression guards; 14 locked files unchanged; receipt-lock 99abc6c2e36a46f71468f1a7930e5a9a48be44e2d74ef44fada3e18d7377bbe1). The hidden-untracked-files kind is requirement-wrong by the owner's decision (ledger line set by hand after pipeline diagnosis refused a second record; backup review/round-4/findings.jsonl.before-requirement-wrong). Obligations rerun with 5.1 to 5.7 (60 obligations). Running: the full suite on both Pythons at 8abacb8 into review/round-5/hr-mitosis-referee.suite.txt. The round-5 code-reviewer, security-reviewer and conformance-auditor (5.1, 5.7) briefs are rendered in review/round-5 and wait for that suite; their diagnoses are the two fixed round-4 kinds only, since hidden untracked files was settled by amendment, not by a fix. Open from the docs Lane: mitosis.py's --resume help still says "no uncommitted change"; advisory C4 of round 4 is addressed by the docs Step and is for round 5 to judge. Rejected and outside-class items for the final report: round 1 C4, S6, S3, S7, S8; round 2 C2, S1, S4; round 3 S2, S3; round 4 S3; the automated commit flags. The checkout holds this run's uncommitted glossary section and ADRs 0006 to 0009, which ship only with the MSP.

## Next step

When the round-5 suite finishes, render and dispatch the round-5 code-reviewer, security-reviewer and conformance-auditor (criteria 5.1 and 5.7) briefs on the fix diff 8015a99..8abacb8, then validate, verdict and fix per O5 and O6, then ship.

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
