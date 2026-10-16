# ship-reuses-gate: Ship reuses a proven gate (mitosis S2, full lane)
Status: active
Updated: 2026-10-16T21:38:24.410Z
Next ids: c3 D3 R2 S1

## Goal

A mitosis resume reuses a passing gate recorded at the same head, and a new --proven-head flag lets a caller ship a head it proved without re-gating; ships as one MSP pull request into the FEATURE branch, mitosis 0.6.0.

## Done when

- [ ] c1 The MSP pull request is open into the FEATURE branch, ready for review (or draft only when a diagnosis classes a blocker blocked), assigned to the owner
- [ ] c2 Every confirmed blocker is fixed with a receipt test that fails first, or named at the top of a draft pull request

## Where things stand

Build 1 committed MSP ship-reuses-gate at 24af33c on mitosis/ship-reuses-gate, base feat/ship-reuses-gate (dfd60b6). Suite passes on 3.14 and 3.9. Review round 1: conformance found all three criteria met; code and security reviews are running.

## Next step

Collect round 1 findings into review/round-1/ship-reuses-gate.findings.json, then run pipeline candidates, the validator, record and verdict.

Needs: D2, R1.

## Decisions in force

- D2 interphase hand-off: build the handed-off spec as one MSP: One MSP, ship-reuses-gate, built as three Worker sessions in a row on its branch (plan 6f008d7094cf); recorded as an assumption in the MSP's assumptions file.

## Open risks

- R1 mitosis recorded the gate inconclusive 12/12 because ~/.mitosis/mitosis/accept.zsh named tests as module attributes; the script now resolves the test's class, an emulated gate gave 8 pass and 4 inert, and the real gate reruns at the ship resume. The 4 inert rows keep today's behaviour by design; 0.5.0 still pushes an inert MSP and lists each row on a Not verified line. Bears on c1.

## Settled, do not reopen

## Where the details live

- Spec: /Users/satanshumishra/Documents/DevLabs/mitosis/docs/specs/ship-reuses-gate.md (decisions, record and items beside it)
- Run home: ~/.mitosis/mitosis/ship-reuses-gate/
- Run dir: ~/.mitosis/runs/mitosis/ship-reuses-gate
- Build with the installed mitosis 0.5.0 at ~/.claude/plugins/cache/mitosis/mitosis/0.5.0/mitosis.py, never the checkout
- Review loop: no round cap; a confirmed blocker leaves the loop only when a fresh diagnosis agent classes it blocked by a named constraint (decisions file)
