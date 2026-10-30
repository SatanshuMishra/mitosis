# hr-mitosis-isolation: Run 7 of the harness-redesign program: mitosis Worker isolation, pinned diff environment, check-author lane, mutant applicability and resumable gate classes
Status: active
Updated: 2026-10-30T01:33:39.021Z
Next ids: c8 D2 R2 S1

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

Stage 1 starting: main synced at 58f258c, lighthouse readiness ready with nothing open, receipts.config.json already committed so receipts init is skipped.

## Next step

Invoke interphase:feature with the run 7 request and Work autonomously., answering every question as an Assumption citing I1 to I6 (O1).

## Decisions in force

- D1 Interphase hand-off: spec hr-mitosis-isolation handed off for build: Handed off. Next: pipeline feature-branch, then a fresh check-author agent writes and locks the seven test files on FEATURE (O2 override), then plan-only and build.

## Open risks

- R1 The checkout holds the owner's uncommitted GLOSSARY.md Pull requests section and untracked docs/adr/0004 and 0005 from a paused run; never touch, commit or delete them; build from the committed state.

## Settled, do not reopen

## Where the details live

- Program: /Users/satanshumishra/.mitosis/harness-redesign/PROGRAM.md (intent I1 to I6, contracts K1 to K7, overrides O1 to O10, run 7 W1 to W5, follow-up F1)
- Frozen schemas: /Users/satanshumishra/.mitosis/harness-redesign/contracts/ (k1-acceptance, k2-locked, k3-check-result, k6-outcome)
- Worked examples: /Users/satanshumishra/.mitosis/harness-redesign/examples/hr-mitosis-referee/ and examples/hr-pipeline-conductor/ (weak-proof-classes.md, r1-gate-evidence.md)
- Run home: /Users/satanshumishra/.mitosis/mitosis/hr-mitosis-isolation/
- Program driver session: local_1353e243-8b09-4cc3-a370-2b58b294d057
