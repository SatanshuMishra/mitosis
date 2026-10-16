# ship-reuses-gate: Ship reuses a proven gate (mitosis S2, full lane)
Status: active
Updated: 2026-10-16T21:12:05.097Z
Next ids: c3 D3 R1 S1

## Goal

A mitosis resume reuses a passing gate recorded at the same head, and a new --proven-head flag lets a caller ship a head it proved without re-gating; ships as one MSP pull request into the FEATURE branch, mitosis 0.6.0.

## Done when

- [ ] c1 The MSP pull request is open into the FEATURE branch, ready for review (or draft only when a diagnosis classes a blocker blocked), assigned to the owner
- [ ] c2 Every confirmed blocker is fixed with a receipt test that fails first, or named at the top of a draft pull request

## Where things stand

Not started.

## Next step

Run the first build with ~/.mitosis/mitosis/ship-reuses-gate/run.zsh, then review round 1 of MSP ship-reuses-gate.

Needs: D2.

## Decisions in force

- D2 interphase hand-off: build the handed-off spec as one MSP: One MSP, ship-reuses-gate, built as three Worker sessions in a row on its branch (plan 6f008d7094cf); recorded as an assumption in the MSP's assumptions file.

## Open risks

## Settled, do not reopen

## Where the details live

- Spec: /Users/satanshumishra/Documents/DevLabs/mitosis/docs/specs/ship-reuses-gate.md (decisions, record and items beside it)
- Run home: ~/.mitosis/mitosis/ship-reuses-gate/
- Run dir: ~/.mitosis/runs/mitosis/ship-reuses-gate
- Build with the installed mitosis 0.5.0 at ~/.claude/plugins/cache/mitosis/mitosis/0.5.0/mitosis.py, never the checkout
- Review loop: no round cap; a confirmed blocker leaves the loop only when a fresh diagnosis agent classes it blocked by a named constraint (decisions file)
