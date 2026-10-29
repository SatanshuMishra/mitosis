# hr-mitosis-referee: Run 2 of the harness-redesign program: mitosis as one locked referee
Status: parked
Updated: 2026-10-29T03:33:51.909Z
Next ids: c7 D8 R5 S1

## Goal

Run 2 of the harness-redesign program (~/.mitosis/harness-redesign/PROGRAM.md, thread harness-redesign in the pipeline repository): make mitosis enforce referee exclusion, gate teeth, full finding reporting, bounded subprocess calls, usage-limit parking and K6 outcome entries, shipped as ready pull requests through the full lane under overrides O1 to O10.

## Done when

- [x] c1 M1 Referee exclusion (reworded by D3): the charter's `- Locked:` paths plus referee globs can never be in a write-set: the gate, the mutant runs and every acceptance run execute in a clean checkout of the MSP's committed tree, with dependency folders from outside the Worker's control; a committed change to a `- Locked:` path or a referee glob, found by comparing tree objects with git's settings pinned, is reported fatal (exit 9) and reverted; uncommitted files in the Worker's worktree never reach the gate and are listed as ignored, never restored; `--plan-only` still refuses an items file whose write-set holds a referee path. evidence: round 8 green at 477fe8b; referee, clean gate and setup referee receipts pass, 718 OK on 3.15 and 3.9 (review/round-8/hr-mitosis-referee.suite.txt)
- [x] c2 M2 Gate teeth: an acceptance test passes the gate only when it fails without the work and passes with it, and is not skipped: the broken-add fixture (a test that only checks a symbol exists) and a skipped test each fail the gate; an inert test fails the gate and holds the MSP back from shipping. evidence: round 8 green at 477fe8b; GateTeeth locked rows pass, 718 OK on 3.15 and 3.9 (review/round-8/hr-mitosis-referee.suite.txt)
- [x] c3 M3 The JSON result lists every finding of every MSP and the exit code is the most severe: a run with an inconclusive gate in MSP 1 and an undeclared write in MSP 2 reports both. evidence: round 8 green at 477fe8b; locked finding-report rows pass, 718 OK on 3.15 and 3.9 (review/round-8/hr-mitosis-referee.suite.txt)
- [x] c4 M4 Every git call and test run has a time limit: an AST test fails on any subprocess call without `timeout`; a hanging pre-commit hook ends with a named error. evidence: round 8 green at 477fe8b; AST timeout test and hanging-hook test pass, 718 OK on 3.15 and 3.9 (review/round-8/hr-mitosis-referee.suite.txt)
- [x] c5 M5 A Worker that stops at a usage limit is parked, not failed, and `--resume` re-dispatches only parked and failed Lanes: a stub Worker printing a usage-limit line leaves its Lane `parked-limit`, and a resume skips every `ok` Lane. evidence: round 8 green at 477fe8b; parked-limit and resume rows pass, 718 OK on 3.15 and 3.9 (review/round-8/hr-mitosis-referee.suite.txt)
- [x] c6 M6 After every gate, one K6 outcome entry per acceptance test is appended to the trajectory store passed with `--trajectory`. evidence: round 8 green at 477fe8b; K6 outcome entry rows pass, 718 OK on 3.15 and 3.9 (review/round-8/hr-mitosis-referee.suite.txt)

## Where things stand

Integration: the MSP pull request https://github.com/SatanshuMishra/mitosis/pull/27 merged into feat/hr-mitosis-referee at 1b1bab9; pipeline integration-ready reports ready; the checkout's main is up to date with origin/main, so Sync deleted nothing. lighthouse:test run 1 on feat/hr-mitosis-referee against main is next, results in the run home's lighthouse/run-1. For the final report: the rejected and outside-class items (round 1 C4, S6, S3, S7, S8; round 2 C2, S1, S4; round 3 S2, S3; round 4 S3; round 5 C6; round 6 S3; round 7 S1; round 8 C1, S1, S2; the automated commit flags) and the GIT_DIFF_OPTS follow-up (an inherited GIT_DIFF_OPTS overrides -U0; pin it in PINNED_GIT with a receipt). The owner's checkout still holds this run's uncommitted glossary section and untracked ADRs 0006 to 0009, identical to the shipped copies.

## Next step

Awaiting merge of https://github.com/SatanshuMishra/mitosis/pull/27. The user starts Integration by saying the MSP pull request merged: then resume_thread hr-mitosis-referee, read /Users/satanshumishra/.mitosis/mitosis/hr-mitosis-referee/CHARTER.md, and run the pipeline:run skill's Integration stage on feat/hr-mitosis-referee.

Needs: D6, R1, R2.

## Decisions in force

- D1 Interphase hand-off: build the handed-off spec as one MSP of seven Steps: Hand off now: one MSP, seven ordered Steps, criteria 5.1 to 5.6 as M1 to M6 and 5.7 for docs and version 0.8.0, built from feat/hr-mitosis-referee cut from main 1391d39 after the O2 check author locks the acceptance tests.

- D3 Amendment 1 hand-off: build M1 as a clean gate checkout in MSP hr-mitosis-referee-amend-1: Hand off amendment 1. The gate, the mutant runs and every acceptance run execute in a clean checkout of the MSP's committed tree. A committed referee write, found from tree objects with git's settings pinned, is reverted and exits 9. Uncommitted files are listed as ignored and never restored. The amendment is built with amend-1.zsh (--plan-only first, always --no-push) and merged into mitosis/hr-mitosis-referee, then review round 5 judges it.
- D4 Round 5 revert shape: object-built revert of the MSP branch, worktree sync that never deletes: Third option, sent to the running fix agent as a refinement of item 6 of review/round-5/fix-brief-pinned-state.md; its receipts assert the branch tree equals the pinned base at every referee path.
- D5 Amendment 2 hand-off: build the gate-setup referee check and the corrected docs in MSP hr-mitosis-referee-amend-2: Hand off amendment 2 now. Items in /Users/satanshumishra/.mitosis/mitosis/hr-mitosis-referee/amend-2.items.json; tests/test_setup_referee_write.py and tests/test_gate_setup_docs.py are written and locked by a check author before amend-2.zsh --plan-only, which waits until the pinned-state fix agent's commits are verified.
- D6 Bounded Worker threat model for run 2: Option A. The Worker is a model that may game tests, not an attacker. Run 2 guarantees and proves in code that (1) a committed change to a - Locked: path or referee glob at the commit the gate judges is fatal (exit 9) and reverted; (2) the gate, mutant and acceptance runs see only the clean committed tree plus what --gate-setup prepares, with referee-named files and folders setup leaves made fatal; (3) uncommitted Worker files never reach the gate. Attacks on git internals shared through the common git directory (moving refs, refs/replace, grafts, shared config, index or object-store tampering) and writes outside the Worker's worktree need a sandbox, are rated LOW and do not block; they are listed in ~/.mitosis/mitosis/do-not-flag.md. Round 6's S1 and S2 are inside the model and are fixed; round 7 reviews only that fix diff with the do-not-flag file set, and a bounded-out finding goes to the final report as LOW. The pull request carries it as an assumption citing I1 and I4. The spec text is not amended, because pipeline 0.10.1 amends the spec only on a requirement-wrong diagnosis; it is folded into an amendment if one arises.
- D7 Ship boundary: ship MSP hr-mitosis-referee at the green head 477fe8b: Ship now: SHIP=1 run.zsh --resume --proven-head hr-mitosis-referee=477fe8b3fc826da233bd7c3762a6ece1585f9d82, a ready pull request assigned to the user, the review comment plus the receipt-lock lines and D6 posted on it, Auto-fix on.

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
- Automated commit security flags and their judgements (outside-class items for the final report): /Users/satanshumishra/.mitosis/mitosis/hr-mitosis-referee/review/automated-security-notes.md
- Amendment builds: /Users/satanshumishra/.mitosis/mitosis/hr-mitosis-referee/amend-1.zsh and amend-2.zsh, each with its amend-<n>.items.json beside it
- Check author brief model: /Users/satanshumishra/.mitosis/mitosis/hr-mitosis-referee/review/check-author-amend-1.md
- Parent's class guidance for S1 (shared git directory routes: refs, packed-refs, refs/replace, info/grafts, config): /Users/satanshumishra/.mitosis/mitosis/hr-mitosis-referee/review/round-5/s1-class-guidance.md
- Fix agent rules: /Users/satanshumishra/.mitosis/mitosis/hr-mitosis-referee/review/round-1/fix-brief-common.md
- Parent's pinned-state guidance for S1, P2, P3 and C1 (one class, one fix agent when one implementation covers them; requirement-wrong if the requirement forces reading the Worker's submodule checkout, reported to the parent): /Users/satanshumishra/.mitosis/mitosis/hr-mitosis-referee/review/round-5/pinned-state-guidance.md
- Ship result (mitosis JSON) and the posted comments: /Users/satanshumishra/.mitosis/mitosis/hr-mitosis-referee/ship.out, review/hr-mitosis-referee.comment.md and review/hr-mitosis-referee.comment-locks.md
