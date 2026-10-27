# mitosis-metrics: Mitosis metrics on the shared plugin-metrics store
Status: parked
Updated: 2026-10-27T07:17:25.673Z
Next ids: c8 D3 R5 S1

## Goal

Mitosis records events and a run summary personalised to its purpose (split work into independently reviewable MSPs, build lanes in parallel, gate every acceptance test on failing without the work, one pull request per MSP) in the shared ~/.plugin-metrics store, through a word-for-word copy of the pipeline's reference writer, with its own catalogue and a small self-query skill, carried through the full lane to its MSP pull request.

## Done when

- [x] c1 The spec, decisions file, record and items for mitosis-metrics are in docs/specs/ and the glossary and decision records interphase wrote are uncommitted in the checkout evidence: docs/specs/mitosis-metrics.md, .decisions.md, .record.json (status handed-off) and .items.json exist; check_record --complete, check_spec and check_items pass; GLOSSARY.md and docs/adr/0001 to 0003 show as untracked in git status
- [x] c2 The FEATURE branch is pushed and the run home ~/.mitosis/mitosis/mitosis-metrics/ holds CHARTER.md, worker-rules.txt, dispatch.zsh, run.zsh and accept.zsh exists evidence: git ls-remote shows refs/heads/feat/mitosis-metrics at d655c7a; ~/.mitosis/mitosis/mitosis-metrics holds CHARTER.md, worker-rules.txt, dispatch.zsh, run.zsh, items.json, graph.json and feature.json; ~/.mitosis/mitosis/accept.zsh exists
- [x] c3 run.zsh --plan-only passes with no fatal finding evidence: run.zsh --plan-only exited 0 with plan_id 306926ac7a29, one MSP, five Steps, no fatal finding; its log is plan-1.log in the run home
- [x] c4 The thread is parked with a next step gated on the shared reference writer existing on origin/feat/unified-metrics or origin/main evidence: the next step names the six-path ls-tree check on origin/main or origin/feat/unified-metrics and the run.zsh command, recorded with D1, and the thread is parked with it
- [x] c5 The first build commits the mitosis-metrics MSP on its branch evidence: build-1.log last JSON line: MSP mitosis-metrics committed on mitosis/mitosis-metrics at c29b657, five Lanes ok, reconcile empty, gate 50 pass and 4 inconclusive (tests/test_docs.py imports metrics_writer at module load)
- [ ] c6 The MSP's review verdict is green or blocked, with every confirmed advisory fixed
- [ ] c7 The MSP pull request is open against feat/mitosis-metrics, assigned to the owner, with its review comment posted

## Where things stand

Stage 3 round 1 for MSP mitosis-metrics: diff, obligations (51), 54 gate proofs and the assumptions file are written in the run home's review folder, the full suite passed at head c29b657 on Python 3.15 and 3.9 (561 tests each), and code-reviewer, security-reviewer and conformance-auditor are reviewing. A Lane reported that an early test run wrote the real ~/.plugin-metrics and removed it; the folder is absent now, and round 1 judges test isolation (criterion 5.17).

## Next step

When the round 1 suite run finishes (review/round-1/mitosis-metrics.suite.txt in the run home), dispatch code-reviewer, conformance-auditor and security-reviewer with the briefs in review/round-1/briefs/, collect findings with M findings for the 4 inconclusive gate tests, then run pipeline candidates, validator, record and verdict.

Needs: D2, R1, R4.

## Decisions in force

- D2 Interphase hand-off: start the first build from the shared writer on origin/feat/unified-metrics: Build now from origin/feat/unified-metrics at a39ab110c1e3c42305123e8d8176ae588b3a9bb0, named on the CHARTER's Reference line; plan-only passed with plan_id 6a96e4ac61c7.

## Open risks

- R1 The Steps copy the reference writer, fixtures, schemas and format document byte for byte; if any copied text holds a term in the repository's DENY_TERMS secret, the CI release check fails and the copy may not be edited. The secret is unreadable here, so this is unproven. Bears on c3.
- R4 mitosis counts the invoking session's messages inside its span, because the pipeline spec leaves a child's span out of the parent's own spend; if the pipeline's merged implementation differs, that window is double counted or dropped.

## Settled, do not reopen

## Where the details live

- Pipeline spec: /Users/satanshumishra/Documents/DevLabs/pipeline/docs/specs/unified-metrics.md
- Pipeline decisions (intent I1 to I6): /Users/satanshumishra/Documents/DevLabs/pipeline/docs/specs/unified-metrics.decisions.md
- Shared writer source: pipeline checkout at origin/feat/unified-metrics, commit a39ab110c1e3c42305123e8d8176ae588b3a9bb0
- Spec, decisions (I1 to I7 and every delegated answer), record and items: /Users/satanshumishra/Documents/DevLabs/mitosis/docs/specs/mitosis-metrics.{md,decisions.md,record.json,items.json}
- Run home: ~/.mitosis/mitosis/mitosis-metrics/ (CHARTER.md, run.zsh, build-1.log, rounds.md); acceptance script: ~/.mitosis/mitosis/accept.zsh
