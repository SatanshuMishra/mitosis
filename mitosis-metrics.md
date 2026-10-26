# mitosis-metrics: Mitosis metrics on the shared plugin-metrics store
Status: active
Updated: 2026-10-26T23:51:42.792Z
Next ids: c5 D2 R5 S1

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

Wait until, in /Users/satanshumishra/Documents/DevLabs/pipeline, git fetch -q origin && git ls-tree --name-only origin/main -- pipeline_tool/metrics_writer.py metrics/fixtures/cases.json metrics/fixtures/expected.json metrics/schema/line.schema.json metrics/schema/catalogue.schema.json docs/metrics-format.md lists all six paths, or the same against origin/feat/unified-metrics does. Then resume_thread mitosis-metrics, replace PENDING on the Reference line of ~/.mitosis/mitosis/mitosis-metrics/CHARTER.md with that ref and its commit (origin/main when it holds all six), check run.zsh against the then-current pipeline:run skill and installed mitosis version, run ~/.mitosis/mitosis/mitosis-metrics/run.zsh, and carry the rest of pipeline:run (review, validate, fix, ship) to the last pull request.

Needs: D1.

## Decisions in force

- D1 Interphase hand-off: hold the first build until the shared reference writer lands: Hold the first build until the shared writer files exist; the next session writes the resolved ref and commit on the CHARTER's Reference line, then runs ~/.mitosis/mitosis/mitosis-metrics/run.zsh and the rest of pipeline:run.

## Open risks

- R1 The Steps copy the reference writer, fixtures, schemas and format document byte for byte; if any copied text holds a term in the repository's DENY_TERMS secret, the CI release check fails and the copy may not be edited. The secret is unreadable here, so this is unproven. Bears on c3.
- R2 Plan-only flags the session-placeholder, record-metrics and metrics-skill-and-docs Steps at about 230k, 265k and 251k estimated tokens, above the 150k one Worker session should hold; each is one Step, so mitosis cannot split it further.
- R3 run.zsh pins the installed mitosis 0.6.0 path and exports PIPELINE_EVENTS as pipeline 0.10.0's run skill says; once the pipeline's unified-metrics change ships, the run skill exports PLUGIN_METRICS_PARENT instead, so run.zsh must follow the skill current at build time.
- R4 mitosis counts the invoking session's messages inside its span, because the pipeline spec leaves a child's span out of the parent's own spend; if the pipeline's merged implementation differs, that window is double counted or dropped.

## Settled, do not reopen

## Where the details live

- Pipeline spec (format description until the writer lands): /Users/satanshumishra/Documents/DevLabs/pipeline/docs/specs/unified-metrics.md
- Pipeline decisions (intent I1 to I6): /Users/satanshumishra/Documents/DevLabs/pipeline/docs/specs/unified-metrics.decisions.md
- Writer check, in the pipeline checkout: git fetch -q origin && git ls-tree --name-only origin/feat/unified-metrics -- pipeline_tool/metrics_writer.py metrics/metrics-writer.ts docs/metrics-format.md, and the same against origin/main
- Spec, decisions (I1 to I7 and every delegated answer), record and items: /Users/satanshumishra/Documents/DevLabs/mitosis/docs/specs/mitosis-metrics.{md,decisions.md,record.json,items.json}
- Run home: ~/.mitosis/mitosis/mitosis-metrics/; acceptance script: ~/.mitosis/mitosis/accept.zsh
