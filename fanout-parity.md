# fanout-parity: Mitosis plans and builds as fanout does (fanout parity)
Status: parked
Updated: 2026-11-08T20:44:27.096Z
Next ids: c2 D6 R1 S1

## Goal

Mitosis's planning and execution match fanout's behaviour wherever no measured evidence backs a departure, keeping the gate, the locks, reconcile and isolation, built from docs/specs/fanout-parity.md.

## Done when

- [ ] c1 The fanout-parity MSP pull request is open with review green or its blockers listed

## Where things stand

Not started.

## Next step

The drive entered review and runs it next.

## Decisions in force

- D1 hand-off: build fanout-parity unattended from the approved read-back: Hand off fanout-parity to mitosis now; any-input-decompose is cut after the fanout-parity pull request merges.
- D2 repair: ship fail: correct: The round-1 fix pass added regression tests to the locked tests/test_accept_planning_coupling.py and tests/test_accept_ports.py; the referee has restored both on the branch, so a rerun should now pass, and the charter will name the locked files so no later fix writes them.
- D3 repair: ship fail: amend-spec: The build is done: all 10 Lanes finished and 57 locked tests pass. The gate failed because three locked tests are weak, and two of them (C19.2 the_session_limit_flag_is_gone, C27.2 an_early_manifest_is_not_a_finding) only check that code was removed, so no mutation of the work can ever make them fail. The third (C30.2 a_run_with_no_briefs_is_refused) is real: the refusal at mitosis.py:2493 is there, but the gate's fixed choice of mutants never touched that line. Rerunning ship or correcting the worker rules or charter cannot change the result, and run.zsh may only change its --pr- lines, so it cannot pass --gate-classes removed. The spec's amendment step should restate these three rows so a mutant can make them fail, or class them as removed. If the amendment step cannot do that, park the run for the owner.
- D4 repair: build fail: park: build: run.zsh exited with code 3: exit 3: refused to start; repair: Build refuses (exit 3) because the run directory already holds the finished, review-green run and build never passes --resume; re-entering ship gives the same weak-gate exit 24 that correct and amend-spec already failed to clear (the amendment left the three rows unchanged), and fixing it needs --gate-classes in run.zsh, re-locked tests, or a cleared run directory, all of which only the owner can do.
- D5 repair: clear the weak-gate park by classing three rows and resuming at review round 3: class the three rows with --gate-classes, restore plan ad0951f2d793, move the dropped tests to unlocked files and resume at review round 3

## Open risks

## Settled, do not reopen

## Where the details live

- Spec: docs/specs/fanout-parity.md
- Decisions: docs/specs/fanout-parity.decisions.md
- Record: docs/specs/fanout-parity.record.json
- Program thread: spec-only-any-input in the pipeline repository's logbook
