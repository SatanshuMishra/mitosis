# mitosis-metrics: Mitosis metrics on the shared plugin-metrics store
Status: parked
Updated: 2026-10-27T08:03:25.654Z
Next ids: c8 D4 R5 S1

## Goal

Mitosis records events and a run summary personalised to its purpose (split work into independently reviewable MSPs, build lanes in parallel, gate every acceptance test on failing without the work, one pull request per MSP) in the shared ~/.plugin-metrics store, through a word-for-word copy of the pipeline's reference writer, with its own catalogue and a small self-query skill, carried through the full lane to its MSP pull request.

## Done when

- [x] c1 The spec, decisions file, record and items for mitosis-metrics are in docs/specs/ and the glossary and decision records interphase wrote are uncommitted in the checkout evidence: docs/specs/mitosis-metrics.md, .decisions.md, .record.json (status handed-off) and .items.json exist; check_record --complete, check_spec and check_items pass; GLOSSARY.md and docs/adr/0001 to 0003 show as untracked in git status
- [x] c2 The FEATURE branch is pushed and the run home ~/.mitosis/mitosis/mitosis-metrics/ holds CHARTER.md, worker-rules.txt, dispatch.zsh, run.zsh and accept.zsh exists evidence: git ls-remote shows refs/heads/feat/mitosis-metrics at d655c7a; ~/.mitosis/mitosis/mitosis-metrics holds CHARTER.md, worker-rules.txt, dispatch.zsh, run.zsh, items.json, graph.json and feature.json; ~/.mitosis/mitosis/accept.zsh exists
- [x] c3 run.zsh --plan-only passes with no fatal finding evidence: run.zsh --plan-only exited 0 with plan_id 306926ac7a29, one MSP, five Steps, no fatal finding; its log is plan-1.log in the run home
- [x] c4 The thread is parked with a next step gated on the shared reference writer existing on origin/feat/unified-metrics or origin/main evidence: the next step names the six-path ls-tree check on origin/main or origin/feat/unified-metrics and the run.zsh command, recorded with D1, and the thread is parked with it
- [x] c5 The first build commits the mitosis-metrics MSP on its branch evidence: build-1.log last JSON line: MSP mitosis-metrics committed on mitosis/mitosis-metrics at c29b657, five Lanes ok, reconcile empty, gate 50 pass and 4 inconclusive (tests/test_docs.py imports metrics_writer at module load)
- [x] c6 The MSP's review verdict is green or blocked, with every confirmed advisory fixed evidence: review/round-2/mitosis-metrics.verdict.json reads outcome green with no blocker or advisory; P1 and P2 validated fixed; round 1 advisories C7, S4, C8 fixed; all 42 gated acceptance tests pass alone at 97de1cb, proofs recorded
- [x] c7 The MSP pull request is open against feat/mitosis-metrics, assigned to the owner, with its review comment posted evidence: SatanshuMishra/mitosis#21 open ready for review, head mitosis/mitosis-metrics at 3c716d4, base feat/mitosis-metrics, assigned to SatanshuMishra, review comment 6436756475 posted; the ship re-gate passed 54 of 54 acceptance tests

## Where things stand

Ship for MSP mitosis-metrics: the branch reviewed green at 97de1cb and holds the glossary and decision records commit 3c716d4 on top. The ship run (log ship-1.log in the run home) is gating the branch again, since the recorded gate at c29b657 blocks on 4 inconclusive verdicts, and then pushes it. The declared skill views are proven on the local DuckDB 2.0 build only; CI pins 1.5.6.

## Next step

When the ship run in ship-1.log ends pushed, read the MSP's title, body, branch and base from its last JSON line, open the pull request with create_pull_request ready for review, assign it to the owner, post review/mitosis-metrics.comment.md, then park.

Needs: D3, R1.

## Decisions in force

- D2 Interphase hand-off: start the first build from the shared writer on origin/feat/unified-metrics: Build now from origin/feat/unified-metrics at a39ab110c1e3c42305123e8d8176ae588b3a9bb0, named on the CHARTER's Reference line; plan-only passed with plan_id 6a96e4ac61c7.
- D3 Ship boundary: push MSP mitosis-metrics and open its pull request against feat/mitosis-metrics: Ship now: run SHIP=1 run.zsh --resume --proven-head mitosis-metrics=97de1cbf1ffbe930ca92ba23cb52683ee891997e, open the pull request ready for review against feat/mitosis-metrics, assign it to the owner and post the review comment.

## Open risks

- R1 The Steps copy the reference writer, fixtures, schemas and format document byte for byte; if any copied text holds a term in the repository's DENY_TERMS secret, the CI release check fails and the copy may not be edited. The secret is unreadable here, so this is unproven. Bears on c3.
- R4 mitosis counts the invoking session's messages inside its span, because the pipeline spec leaves a child's span out of the parent's own spend; if the pipeline's merged implementation differs, that window is double counted or dropped.

## Settled, do not reopen

## Where the details live

- Pipeline spec: /Users/satanshumishra/Documents/DevLabs/pipeline/docs/specs/unified-metrics.md
- Pipeline decisions (intent I1 to I6): /Users/satanshumishra/Documents/DevLabs/pipeline/docs/specs/unified-metrics.decisions.md
- Shared writer source: pipeline checkout at origin/feat/unified-metrics, commit a39ab110c1e3c42305123e8d8176ae588b3a9bb0
- Spec, decisions (I1 to I7 and every delegated answer), record and items: /Users/satanshumishra/Documents/DevLabs/mitosis/docs/specs/mitosis-metrics.{md,decisions.md,record.json,items.json}
- Run home: ~/.mitosis/mitosis/mitosis-metrics/ (CHARTER.md, run.zsh, build-1.log, rounds.md, review/ with each round's diff, suite output, briefs, findings and verdicts); acceptance script: ~/.mitosis/mitosis/accept.zsh
