# fanout-parity: Mitosis plans and builds as fanout does (fanout parity)
Status: parked
Updated: 2026-11-08T07:46:23.069Z
Next ids: c2 D3 R1 S1

## Goal

Mitosis's planning and execution match fanout's behaviour wherever no measured evidence backs a departure, keeping the gate, the locks, reconcile and isolation, built from docs/specs/fanout-parity.md.

## Done when

- [ ] c1 The fanout-parity MSP pull request is open with review green or its blockers listed

## Where things stand

Not started.

## Next step

The drive entered repair and runs it next.

## Decisions in force

- D1 hand-off: build fanout-parity unattended from the approved read-back: Hand off fanout-parity to mitosis now; any-input-decompose is cut after the fanout-parity pull request merges.
- D2 repair: ship fail: correct: The round-1 fix pass added regression tests to the locked tests/test_accept_planning_coupling.py and tests/test_accept_ports.py; the referee has restored both on the branch, so a rerun should now pass, and the charter will name the locked files so no later fix writes them.

## Open risks

## Settled, do not reopen

## Where the details live

- Spec: docs/specs/fanout-parity.md
- Decisions: docs/specs/fanout-parity.decisions.md
- Record: docs/specs/fanout-parity.record.json
- Program thread: spec-only-any-input in the pipeline repository's logbook
