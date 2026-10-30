# hr-mitosis-isolation: Run 7 of the harness-redesign program: mitosis Worker isolation, pinned diff environment, check-author lane, mutant applicability and resumable gate classes
Status: parked
Updated: 2026-10-30T08:09:34.385Z
Next ids: c8 D3 R3 S2

## Goal

Build W1 to W5 of ~/.mitosis/harness-redesign/PROGRAM.md (section Run 7 mitosis, hr-mitosis-isolation) in ~/Documents/DevLabs/mitosis through pipeline 0.11.0's full lane, ending with the MSP pull requests open, ready and green, then Integration and Clean-up with the C14 and F1 install check, so the harness hands mitosis any task with no rescue (I1).

## Done when

- [x] c1 W1: each Worker builds in its own clone with no shared git directory, sandboxed to that clone; mitosis takes only its committed changes into a checkout it controls; a Worker that moves a ref, writes refs/replace, a graft or shared config, or writes outside its clone changes nothing mitosis judges evidence: tests/test_worker_isolation.py's 13 acceptance tests and the round 1 and round 2 sandbox receipts pass alone at cacce46 (review/isolation.proofs.jsonl); conformance MET; security-reviewer clean on the advisory diff
- [x] c2 W2: every git call mitosis makes clears GIT_DIFF_OPTS, GIT_EXTERNAL_DIFF and the other diff-shaping variables; a receipt with GIT_DIFF_OPTS=-U5 inherited still finds the weak check evidence: tests/test_pinned_diff_env.py's two acceptance tests pass alone at cacce46 and fail with the work reverted (mitosis gate)
- [x] c3 W3: mitosis runs a role check-author Lane first, lets it write exactly the record's locked paths, commits and locks them on FEATURE, then plans the other Lanes; an interphase 0.14.0 items file plans and builds with no manual O2 step evidence: tests/test_check_author_lane.py's six acceptance tests, including an_items_file_with_a_check_author_plans, pass alone at cacce46; conformance MET
- [x] c4 W4: mutants are sampled only from lines the acceptance test executes on the work; a property reading no Worker code is not-applicable; a survivor the full suite kills is covered-elsewhere naming the killing tests evidence: tests/test_mutant_coverage.py's five acceptance tests pass alone at cacce46; round 1 C3 and C4 fixes confirmed by the round 2 validator
- [x] c5 W5: a gate classification change is recorded per property outside the plan id, so --resume keeps every built Lane and re-gates only the changed properties evidence: tests/test_gate_classes.py's two acceptance tests pass alone at cacce46
- [x] c6 MSP pull requests open, ready, assigned, bound with Auto-fix, review green; M1 to M6 locked tests from run 2 still locked and passing evidence: SatanshuMishra/mitosis#29 open, ready, assigned to SatanshuMishra, review comment posted, bound with Auto-fix; round 2 verdict green; run 2's locked tests unchanged since 58f258c except the permitted version_is_0_9_0 re-lock, suite 778 OK on 3.9 and 3.14
- [ ] c7 C14 and F1: mitosis version bumped in the FEATURE pull request; at Clean-up the merged version is installed and matches main file by file; later commands run from the installed cache path

## Where things stand

FEATURE feat/hr-mitosis-isolation holds the lock commit b7d8b44 (O2 override), pushed. MSP branch mitosis/isolation is pushed at bb7adda as SatanshuMishra/mitosis#29 (ready, assigned, bound, Auto-fix on); the driver has told the owner to hold its merge until round 3 is green. Round 3 (driver escalation of W1, a --worker-write path overlapping the run folder, and W2, a confined process outliving its run): both fixes and the ADR lint fix that turned #29's CI red are on mitosis/isolation at head 27f92e7, not pushed (fix-check clean, locked files unchanged; fix notes in review/round-3/fix-*.out.txt); round 3 review of bb7adda..27f92e7: suite 790 OK on 3.9 and 3.14, no blocker, validator judging 5 LOW candidates in review/round-3/isolation.candidates.md (S1 is W1's class for the repository and its git directory). Stages run by hand, not through pipeline drive, and reviewers get the run-local do-not-flag.md. Four assumptions are in review/isolation.assumptions.txt.

## Next step

When the round 3 validator ends, save its array as review/round-3/isolation.verdicts.json and run record, verdict and outcome; send a confirmed S1 to a fix agent as W1's second attempt (a rejected S1 goes to the driver); on green record a new ship boundary superseding D2 and re-ship with --proven-head isolation=<head> to push mitosis/isolation so #29 updates, refresh its review comment, and report to the driver with where W4's sampling lives (run.py and linetrace/sitecustomize.py; mutants.py unchanged, write-set wider than needed).

Needs: R1.

## Decisions in force

- D1 Interphase hand-off: spec hr-mitosis-isolation handed off for build: Handed off. Next: pipeline feature-branch, then a fresh check-author agent writes and locks the seven test files on FEATURE (O2 override), then plan-only and build.
- D2 Ship boundary: MSP isolation ships at proven head bb7adda: Ship with --proven-head isolation=bb7addad14457aa7bf72f5f4818f695f43d5db6e via SHIP=1 run.zsh --resume, then open the PR ready, assigned, bound with Auto-fix.

## Open risks

- R1 The checkout holds the owner's uncommitted GLOSSARY.md Pull requests section and untracked docs/adr/0004 and 0005 from a paused run; never touch, commit or delete them; build from the committed state.

## Settled, do not reopen

- S1 Driver ruling on outside writes: files replayed from a Worker's clone are judged by reconcile only (exit 6 kept or exit 9 reverted) and never reach the gate's tree, which holds each such path as at the Lane's start; only regular files and start-tree deletions are replayed; proven by acceptance row an_outside_write_never_reaches_the_gate.

## Where the details live

- Program: /Users/satanshumishra/.mitosis/harness-redesign/PROGRAM.md (intent I1 to I6, contracts K1 to K7, overrides O1 to O10, run 7 W1 to W5, follow-up F1)
- Frozen schemas: /Users/satanshumishra/.mitosis/harness-redesign/contracts/ (k1-acceptance, k2-locked, k3-check-result, k6-outcome)
- Worked examples: /Users/satanshumishra/.mitosis/harness-redesign/examples/hr-mitosis-referee/ and examples/hr-pipeline-conductor/ (weak-proof-classes.md, r1-gate-evidence.md)
- Run home: /Users/satanshumishra/.mitosis/mitosis/hr-mitosis-isolation/
- Program driver session: local_1353e243-8b09-4cc3-a370-2b58b294d057
- Spec, decisions, record, items: /Users/satanshumishra/Documents/DevLabs/mitosis/docs/specs/hr-mitosis-isolation.* (gitignored)
- Decision records of this change: docs/adr/0010 to 0014 on mitosis/isolation
- Citable run material: /Users/satanshumishra/.mitosis/harness-redesign/examples/hr-mitosis-isolation/
- Pipeline defects found: run home pipeline-0.11.0-defects.md (P1 to P9)
