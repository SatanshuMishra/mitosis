# mitosis-metrics: Mitosis metrics on the shared plugin-metrics store
Status: active
Updated: 2026-10-26T23:26:00.936Z
Next ids: c5 D1 R1 S1

## Goal

Mitosis records events and a run summary personalised to its purpose (split work into independently reviewable MSPs, build lanes in parallel, gate every acceptance test on failing without the work, one pull request per MSP) in the shared ~/.plugin-metrics store, through a word-for-word copy of the pipeline's reference writer, with its own catalogue and a small self-query skill, carried through the full lane up to the first build.

## Done when

- [ ] c1 The spec, decisions file, record and items for mitosis-metrics are in docs/specs/ and the glossary and decision records interphase wrote are uncommitted in the checkout
- [ ] c2 The FEATURE branch is pushed and the run home ~/.mitosis/mitosis/mitosis-metrics/ holds CHARTER.md, worker-rules.txt, dispatch.zsh, run.zsh and accept.zsh exists
- [ ] c3 run.zsh --plan-only passes with no fatal finding
- [ ] c4 The thread is parked with a next step gated on the shared reference writer existing on origin/feat/unified-metrics or origin/main

## Where things stand

Stage 1 of pipeline:run, full lane. Checkout synced on main. Lighthouse readiness is ready (claude_cli answered). receipts.config.json is already committed, so no receipts set-up or receipts commit is needed. The shared reference writer is on neither origin/feat/unified-metrics nor origin/main yet.

## Next step

Invoke pipeline:run and run Stage 1: interphase:feature full-lane interview for slug mitosis-metrics, answering every round as the owner's delegated answer citing I1 to I6 or a binding decision.

## Decisions in force

## Open risks

## Settled, do not reopen

## Where the details live

- Pipeline spec (format description until the writer lands): /Users/satanshumishra/Documents/DevLabs/pipeline/docs/specs/unified-metrics.md
- Pipeline decisions (intent I1 to I6): /Users/satanshumishra/Documents/DevLabs/pipeline/docs/specs/unified-metrics.decisions.md
- Writer check: git fetch -q origin && git ls-tree --name-only origin/feat/unified-metrics -- pipeline_tool/metrics_writer.py metrics/metrics-writer.ts docs/metrics-format.md (in the pipeline checkout; also origin/main)
