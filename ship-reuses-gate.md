# ship-reuses-gate: Ship reuses a proven gate (mitosis S2, full lane)
Status: parked
Updated: 2026-10-16T23:49:43.689Z
Next ids: c3 D4 R2 S1

## Goal

A mitosis resume reuses a passing gate recorded at the same head, and a new --proven-head flag lets a caller ship a head it proved without re-gating; ships as one MSP pull request into the FEATURE branch, mitosis 0.6.0.

## Done when

- [x] c1 The MSP pull request is open into the FEATURE branch, ready for review (or draft only when a diagnosis classes a blocker blocked), assigned to the owner evidence: https://github.com/SatanshuMishra/mitosis/pull/19 open from mitosis/ship-reuses-gate (c293440) into feat/ship-reuses-gate, draft false, assigned SatanshuMishra, review comment posted
- [x] c2 Every confirmed blocker is fixed with a receipt test that fails first, or named at the top of a draft pull request evidence: round 2 verdict green (review/round-2/ship-reuses-gate.verdict.json): C1 and S1 fixed at e6e74c9 with receipt tests HouseFormat.the_verification_section_ends_with_its_closing_lines_past_the_limit and GateReuse.test_resume_with_a_file_git_status_hides_gates_again / ..._submodule_move_... red at 24af33c and green after; M1 fixed in the acceptance script, red-then-green on all 12 rows

## Where things stand

Integration done: lighthouse verdict green (suite passed on main and FEATURE, every new-behaviour path and the plain-build and release-check neighbours passed; the skill-selection neighbour unproven, needing a signed-in session). FEATURE pull request SatanshuMishra/mitosis#20 is open from feat/ship-reuses-gate into main, ready for review, assigned to the owner, not merged. The FEATURE branch carries the lighthouse profile commit a2d59af; the owner's untracked .lighthouse/profile.json in the checkout differs from it.

## Next step

The owner starts Clean-up by saying the FEATURE pull request merged.

## Decisions in force

- D2 interphase hand-off: build the handed-off spec as one MSP: One MSP, ship-reuses-gate, built as three Worker sessions in a row on its branch (plan 6f008d7094cf); recorded as an assumption in the MSP's assumptions file.
- D3 ship: push MSP ship-reuses-gate and open its pull request ready for review: Ship: resume with SHIP=1 so mitosis gates again with the fixed acceptance script and pushes mitosis/ship-reuses-gate, then open the pull request into feat/ship-reuses-gate, ready for review and assigned to the owner, and post the review comment.

## Open risks


## Settled, do not reopen

## Where the details live

- Spec: /Users/satanshumishra/Documents/DevLabs/mitosis/docs/specs/ship-reuses-gate.md (decisions, record and items beside it)
- Run home: ~/.mitosis/mitosis/ship-reuses-gate/
- Run dir: ~/.mitosis/runs/mitosis/ship-reuses-gate
- Build with the installed mitosis 0.5.0 at ~/.claude/plugins/cache/mitosis/mitosis/0.5.0/mitosis.py, never the checkout
- Lighthouse results, report.md and verdict.json: ~/.mitosis/mitosis/ship-reuses-gate/lighthouse
- Review loop: no round cap; a confirmed blocker leaves the loop only when a fresh diagnosis agent classes it blocked by a named constraint (decisions file)
