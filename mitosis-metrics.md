# mitosis-metrics: Mitosis metrics on the shared plugin-metrics store
Status: active
Updated: 2026-10-26T23:51:33.751Z
Next ids: c5 D2 R1 S1

## Goal

Mitosis records events and a run summary personalised to its purpose (split work into independently reviewable MSPs, build lanes in parallel, gate every acceptance test on failing without the work, one pull request per MSP) in the shared ~/.plugin-metrics store, through a word-for-word copy of the pipeline's reference writer, with its own catalogue and a small self-query skill, carried through the full lane up to the first build.

## Done when

- [ ] c1 The spec, decisions file, record and items for mitosis-metrics are in docs/specs/ and the glossary and decision records interphase wrote are uncommitted in the checkout
- [ ] c2 The FEATURE branch is pushed and the run home ~/.mitosis/mitosis/mitosis-metrics/ holds CHARTER.md, worker-rules.txt, dispatch.zsh, run.zsh and accept.zsh exists
- [ ] c3 run.zsh --plan-only passes with no fatal finding
- [ ] c4 The thread is parked with a next step gated on the shared reference writer existing on origin/feat/unified-metrics or origin/main

## Where things stand

Stage 1, interphase Phase 9: the interview is done with delegated answers and an approved read-back; the spec, record and items are rendered and pass check_spec and check_items; the one spec-reviewer pass is running. GLOSSARY.md and docs/adr/0001 to 0003 are written, uncommitted. FEATURE branch feat/mitosis-metrics is pushed at d655c7a. The run home holds feature.json, CHARTER.md (its Reference line reads PENDING), worker-rules.txt, dispatch.zsh, run.zsh and graph.json; items.json and plan-only wait on the review fixes. Lighthouse readiness is ready and receipts.config.json is already committed, so Stage 2 needs no receipts commit. The shared reference writer is on neither pipeline ref yet.

## Next step

Apply the spec-reviewer's real findings, re-render and re-check, then run interphase Phase 10 hand-off and go on to Stage 2 set-up.

## Decisions in force

- D1 Interphase hand-off: hold the first build until the shared reference writer lands: Hold the first build until the shared writer files exist; the next session writes the resolved ref and commit on the CHARTER's Reference line, then runs ~/.mitosis/mitosis/mitosis-metrics/run.zsh and the rest of pipeline:run.

## Open risks

## Settled, do not reopen

## Where the details live

- Pipeline spec (format description until the writer lands): /Users/satanshumishra/Documents/DevLabs/pipeline/docs/specs/unified-metrics.md
- Pipeline decisions (intent I1 to I6): /Users/satanshumishra/Documents/DevLabs/pipeline/docs/specs/unified-metrics.decisions.md
- Writer check, in the pipeline checkout: git fetch -q origin && git ls-tree --name-only origin/feat/unified-metrics -- pipeline_tool/metrics_writer.py metrics/metrics-writer.ts docs/metrics-format.md, and the same against origin/main
- Spec, decisions (I1 to I7 and every delegated answer), record and items: /Users/satanshumishra/Documents/DevLabs/mitosis/docs/specs/mitosis-metrics.{md,decisions.md,record.json,items.json}
- Run home: ~/.mitosis/mitosis/mitosis-metrics/; acceptance script: ~/.mitosis/mitosis/accept.zsh
