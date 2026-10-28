# hr-mitosis-referee: Run 2 of the harness-redesign program: mitosis as one locked referee
Status: active
Updated: 2026-10-28T21:30:41.662Z
Next ids: c7 D5 R5 S1

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

Stage 3, round 5 fix pass on MSP mitosis/hr-mitosis-referee over base 5a94f41, head 8abacb8 (round 1 to 4 fixes, the check author's locked tests/test_clean_gate.py at d32c436, amendment 1 per D3; 14 locked files, receipt-lock 99abc6c2e36a46f71468f1a7930e5a9a48be44e2d74ef44fada3e18d7377bbe1). The hidden-untracked-files kind is requirement-wrong by the owner's decision (ledger line set by hand after pipeline diagnosis refused a second record; backup review/round-4/findings.jsonl.before-requirement-wrong). Round 5 verdict is loop after five recorded diagnoses (review/round-5/hr-mitosis-referee.verdict.json; readable record hr-mitosis-referee.md beside it). Fixable as one change: S1 unpinned referee base, P2 submodule referee write unreported, P3 referee stage skipped on commit failure (live routes: a refused Lane landing commit and a Worker failing after committing), C1 submodule checkout deleted. Running: one fix agent on that change (review/round-5/fix-brief-pinned-state.md). Requirement-wrong: C3 docs misdescribe behaviour; amendment 2 is written (spec LFS/eol assumption corrected, decision 18, criterion 5.7 rows GateSetupDocs.* in tests/test_gate_setup_docs.py, Step gate-setup-docs in MSP hr-mitosis-referee-amend-2, which also carries C4 and the --resume help; docs/specs/hr-mitosis-referee.amend-2.items.json; record in-progress, guard snapshot docs/specs/hr-mitosis-referee.guard.json; backups scratchpad/*.before-amend-2.*). Running: its one spec review. R4 recheck at 8abacb8 (probe scratchpad/r4probe/code/tests/r4_vendor_probe.py on an archive copy): an uncommitted vendor/laravel/prompts/phpunit.xml under a .gitignore or Worker info/exclude rule gives exit 0, referee [], ignored ['vendor/']; only with no ignore rule does the pre-gate commit commit it and exit 9, as M1 requires for a committed referee path. It goes into the fix pass as a committed regression guard, rerun at the round-6 head before R4 is retired. Advisories to fix: C2 unbounded added-line diff (run.py _added_lines), C4 undocumented submodule hold, C5 76c5400 deleted 7 tests without a test-removal note; plus mitosis.py's --resume help still saying "no uncommitted change". Rejected and outside-class items for the final report: round 1 C4, S6, S3, S7, S8; round 2 C2, S1, S4; round 3 S2, S3; round 4 S3; round 5 C6; the automated commit flags. The checkout holds this run's uncommitted glossary section and ADRs 0006 to 0009, which ship only with the MSP.

## Next step

When the pinned-state fix agent reports, verify its commits, the 14 locked files and both suites; meanwhile run interphase amendment 2 for C3 (spec assumption line 184, with C4 and the --resume help in its docs Step), build it with amend-2.zsh after the fix lands, then fresh fix agents for C2 and C5, then round 6 on the fix diff, then ship.

Needs: D1, D3, R1, R2.

## Decisions in force

- D1 Interphase hand-off: build the handed-off spec as one MSP of seven Steps: Hand off now: one MSP, seven ordered Steps, criteria 5.1 to 5.6 as M1 to M6 and 5.7 for docs and version 0.8.0, built from feat/hr-mitosis-referee cut from main 1391d39 after the O2 check author locks the acceptance tests.

- D3 Amendment 1 hand-off: build M1 as a clean gate checkout in MSP hr-mitosis-referee-amend-1: Hand off amendment 1. The gate, the mutant runs and every acceptance run execute in a clean checkout of the MSP's committed tree. A committed referee write, found from tree objects with git's settings pinned, is reverted and exits 9. Uncommitted files are listed as ignored and never restored. The amendment is built with amend-1.zsh (--plan-only first, always --no-push) and merged into mitosis/hr-mitosis-referee, then review round 5 judges it.
- D4 Round 5 revert shape: object-built revert of the MSP branch, worktree sync that never deletes: Third option, sent to the running fix agent as a refinement of item 6 of review/round-5/fix-brief-pinned-state.md; its receipts assert the branch tree equals the pinned base at every referee path.

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
- Parent's class guidance for S1 (shared git directory routes: refs, packed-refs, refs/replace, info/grafts, config): /Users/satanshumishra/.mitosis/mitosis/hr-mitosis-referee/review/round-5/s1-class-guidance.md
- Fix agent rules: /Users/satanshumishra/.mitosis/mitosis/hr-mitosis-referee/review/round-1/fix-brief-common.md
- Parent's pinned-state guidance for S1, P2, P3 and C1 (one class, one fix agent when one implementation covers them; requirement-wrong if the requirement forces reading the Worker's submodule checkout, reported to the parent): /Users/satanshumishra/.mitosis/mitosis/hr-mitosis-referee/review/round-5/pinned-state-guidance.md
