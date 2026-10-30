# hr-mitosis-isolation: Run 7 of the harness-redesign program: mitosis Worker isolation, pinned diff environment, check-author lane, mutant applicability and resumable gate classes
Status: active
Updated: 2026-10-30T02:05:39.288Z
Next ids: c8 D2 R3 S2

## Goal

Build W1 to W5 of ~/.mitosis/harness-redesign/PROGRAM.md (section Run 7 mitosis, hr-mitosis-isolation) in ~/Documents/DevLabs/mitosis through pipeline 0.11.0's full lane, ending with the MSP pull requests open, ready and green, then Integration and Clean-up with the C14 and F1 install check, so the harness hands mitosis any task with no rescue (I1).

## Done when

- [ ] c1 W1: each Worker builds in its own clone with no shared git directory, sandboxed to that clone; mitosis takes only its committed changes into a checkout it controls; a Worker that moves a ref, writes refs/replace, a graft or shared config, or writes outside its clone changes nothing mitosis judges
- [ ] c2 W2: every git call mitosis makes clears GIT_DIFF_OPTS, GIT_EXTERNAL_DIFF and the other diff-shaping variables; a receipt with GIT_DIFF_OPTS=-U5 inherited still finds the weak check
- [ ] c3 W3: mitosis runs a role check-author Lane first, lets it write exactly the record's locked paths, commits and locks them on FEATURE, then plans the other Lanes; an interphase 0.14.0 items file plans and builds with no manual O2 step
- [ ] c4 W4: mutants are sampled only from lines the acceptance test executes on the work; a property reading no Worker code is not-applicable; a survivor the full suite kills is covered-elsewhere naming the killing tests
- [ ] c5 W5: a gate classification change is recorded per property outside the plan id, so --resume keeps every built Lane and re-gates only the changed properties
- [ ] c6 MSP pull requests open, ready, assigned, bound with Auto-fix, review green; M1 to M6 locked tests from run 2 still locked and passing
- [ ] c7 C14 and F1: mitosis version bumped in the FEATURE pull request; at Clean-up the merged version is installed and matches main file by file; later commands run from the installed cache path

## Where things stand

FEATURE feat/hr-mitosis-isolation holds the lock commit b7d8b44 (seven test files, 29 acceptance lines, written by a hand-dispatched check-author: O2 override), pushed. At the lock every new line exits 1 by assertion and the other 718 tests pass (lock-suite.txt: 33 failures, all new). run.zsh --plan-only exits 0: one MSP isolation of six Lanes, referee K2 at b7d8b44 over 19 locked paths; every Lane is flagged above 150000 estimated tokens (attention only). The first build (run.zsh, --no-push) is running in the background, writing build-1.out.txt and build-1.err.txt in the run home; MSP PR subject is "isolate Workers in clones and gate on executed lines".

## Next step

When the build ends, read the last JSON line of build-1.out.txt, write each lane Assumption note to review/isolation.assumptions.txt, then start review round 1.

Needs: D1, R1.

## Decisions in force

- D1 Interphase hand-off: spec hr-mitosis-isolation handed off for build: Handed off. Next: pipeline feature-branch, then a fresh check-author agent writes and locks the seven test files on FEATURE (O2 override), then plan-only and build.

## Open risks

- R1 The checkout holds the owner's uncommitted GLOSSARY.md Pull requests section and untracked docs/adr/0004 and 0005 from a paused run; never touch, commit or delete them; build from the committed state.
- R2 ADRs 0010 to 0014 are untracked in the owner's checkout beside the owner's untracked 0004 and 0005; ship only 0010 to 0014 on the FEATURE branch through a temporary worktree, never 0004 or 0005. Bears on c6.

## Settled, do not reopen

- S1 Driver ruling on outside writes: files replayed from a Worker's clone are judged by reconcile only (exit 6 kept or exit 9 reverted) and never reach the gate's tree, which holds each such path as at the Lane's start; only regular files and start-tree deletions are replayed; proven by acceptance row an_outside_write_never_reaches_the_gate.

## Where the details live

- Program: /Users/satanshumishra/.mitosis/harness-redesign/PROGRAM.md (intent I1 to I6, contracts K1 to K7, overrides O1 to O10, run 7 W1 to W5, follow-up F1)
- Frozen schemas: /Users/satanshumishra/.mitosis/harness-redesign/contracts/ (k1-acceptance, k2-locked, k3-check-result, k6-outcome)
- Worked examples: /Users/satanshumishra/.mitosis/harness-redesign/examples/hr-mitosis-referee/ and examples/hr-pipeline-conductor/ (weak-proof-classes.md, r1-gate-evidence.md)
- Run home: /Users/satanshumishra/.mitosis/mitosis/hr-mitosis-isolation/
- Program driver session: local_1353e243-8b09-4cc3-a370-2b58b294d057
- Spec, decisions, record, items: /Users/satanshumishra/Documents/DevLabs/mitosis/docs/specs/hr-mitosis-isolation.* (gitignored)
- Decision records of this change: docs/adr/0010 to 0014 in the owner's checkout, untracked
- Pipeline defects found: run home pipeline-0.11.0-defects.md (P1 to P4)
