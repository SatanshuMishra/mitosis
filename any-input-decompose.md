# any-input-decompose: Mitosis takes any input fanout takes (asks, specs, items)
Status: parked
Updated: 2026-11-08T22:52:21.132Z
Next ids: c2 D3 R1 S1

## Goal

Mitosis plans an asks file, a spec of any kind or an items file as fanout does, writes its own lock stage and refuses a plan naming a test nothing writes, built from docs/specs/any-input-decompose.md after fanout-parity merges.

## Done when

- [ ] c1 The any-input-decompose MSP pull request is open with review green or its blockers listed

## Where things stand

Not started.

## Next step

After the fanout-parity pull request merges, sync main, rerun lock_feasibility on docs/specs/any-input-decompose.record.json and start its run.

## Decisions in force

- D1 hand-off: build any-input-decompose unattended after fanout-parity merges: Hand off any-input-decompose once fanout-parity merges, from the merged main.
- D2 hand-off: build the handed-off any-input spec unattended from a clean clone: Build from a clean clone at ~/.mitosis/mitosis/checkouts/any-input-decompose/mitosis, the drive carrying every stage unattended.

## Open risks

## Settled, do not reopen

## Where the details live

- Spec: docs/specs/any-input-decompose.md
- Decisions: docs/specs/any-input-decompose.decisions.md
- Record: docs/specs/any-input-decompose.record.json
- Program thread: spec-only-any-input in the pipeline repository's logbook
