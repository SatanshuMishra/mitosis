# mitosis-metrics: Mitosis metrics on the shared plugin-metrics store
Status: parked
Updated: 2026-10-27T06:28:19.487Z
Next ids: c5 D2 R5 S1

## Goal

Mitosis records events and a run summary personalised to its purpose (split work into independently reviewable MSPs, build lanes in parallel, gate every acceptance test on failing without the work, one pull request per MSP) in the shared ~/.plugin-metrics store, through a word-for-word copy of the pipeline's reference writer, with its own catalogue and a small self-query skill, carried through the full lane to its MSP pull request.

## Done when

- [x] c1 The spec, decisions file, record and items for mitosis-metrics are in docs/specs/ and the glossary and decision records interphase wrote are uncommitted in the checkout evidence: docs/specs/mitosis-metrics.md, .decisions.md, .record.json (status handed-off) and .items.json exist; check_record --complete, check_spec and check_items pass; GLOSSARY.md and docs/adr/0001 to 0003 show as untracked in git status
- [x] c2 The FEATURE branch is pushed and the run home ~/.mitosis/mitosis/mitosis-metrics/ holds CHARTER.md, worker-rules.txt, dispatch.zsh, run.zsh and accept.zsh exists evidence: git ls-remote shows refs/heads/feat/mitosis-metrics at d655c7a; ~/.mitosis/mitosis/mitosis-metrics holds CHARTER.md, worker-rules.txt, dispatch.zsh, run.zsh, items.json, graph.json and feature.json; ~/.mitosis/mitosis/accept.zsh exists
- [x] c3 run.zsh --plan-only passes with no fatal finding evidence: run.zsh --plan-only exited 0 with plan_id 306926ac7a29, one MSP, five Steps, no fatal finding; its log is plan-1.log in the run home
- [x] c4 The thread is parked with a next step gated on the shared reference writer existing on origin/feat/unified-metrics or origin/main evidence: the next step names the six-path ls-tree check on origin/main or origin/feat/unified-metrics and the run.zsh command, recorded with D1, and the thread is parked with it

## Where things stand

Stage 2, first build starting: the shared writer files are on the pipeline's origin/feat/unified-metrics at a39ab110c1e3c42305123e8d8176ae588b3a9bb0 and not on origin/main, and the CHARTER's Reference line names that ref and commit. The writer's public functions (append_event, write_summary, write_catalogue, prune, run_id, store_root) match what the spec names, and prices.json is unchanged at date 2026-10-06. run.zsh matches the installed pipeline 0.10.0 run skill and mitosis 0.6.0, which are still the newest installed.

## Next step

Run ~/.mitosis/mitosis/mitosis-metrics/run.zsh --plan-only once more, then the first build with run.zsh in the background, read its last JSON line, and carry pipeline:run Stage 3 review, Validate, fixes and Ship to the MSP pull request.

Needs: D1, R1, R2, R4.

## Decisions in force

- D1 Interphase hand-off: hold the first build until the shared reference writer lands: Hold the first build until the shared writer files exist; the next session writes the resolved ref and commit on the CHARTER's Reference line, then runs ~/.mitosis/mitosis/mitosis-metrics/run.zsh and the rest of pipeline:run.

## Open risks

- R1 The Steps copy the reference writer, fixtures, schemas and format document byte for byte; if any copied text holds a term in the repository's DENY_TERMS secret, the CI release check fails and the copy may not be edited. The secret is unreadable here, so this is unproven. Bears on c3.
- R2 Plan-only flags the session-placeholder, record-metrics and metrics-skill-and-docs Steps at about 230k, 265k and 251k estimated tokens, above the 150k one Worker session should hold; each is one Step, so mitosis cannot split it further.
- R4 mitosis counts the invoking session's messages inside its span, because the pipeline spec leaves a child's span out of the parent's own spend; if the pipeline's merged implementation differs, that window is double counted or dropped.

## Settled, do not reopen

## Where the details live

- Pipeline spec: /Users/satanshumishra/Documents/DevLabs/pipeline/docs/specs/unified-metrics.md
- Pipeline decisions (intent I1 to I6): /Users/satanshumishra/Documents/DevLabs/pipeline/docs/specs/unified-metrics.decisions.md
- Shared writer source: pipeline checkout at origin/feat/unified-metrics, commit a39ab110c1e3c42305123e8d8176ae588b3a9bb0
- Spec, decisions (I1 to I7 and every delegated answer), record and items: /Users/satanshumishra/Documents/DevLabs/mitosis/docs/specs/mitosis-metrics.{md,decisions.md,record.json,items.json}
- Run home: ~/.mitosis/mitosis/mitosis-metrics/; acceptance script: ~/.mitosis/mitosis/accept.zsh
