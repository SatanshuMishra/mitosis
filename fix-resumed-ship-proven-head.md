# fix-resumed-ship-proven-head: A resumed ship pushes a proven head that moved past the pushed head
Status: active
Updated: 2026-11-01T10:29:22.362Z
Next ids: c5 D1 R1 S1

## Goal

Defect P10: a resumed ship with --proven-head naming a commit that descends from an MSP's pushed head fast-forward pushes the branch and re-renders the body; an equal head is a no-op; a non-descendant head is refused, never force-pushed.

## Done when

- [ ] c1 A receipt test fails on main and passes after: a later proven head moves the origin branch to it and the returned body is re-rendered; an equal head is a no-op; a non-descendant head is refused
- [ ] c2 The full test suite passes
- [ ] c3 The version is bumped within 0.x.y with a CHANGELOG entry
- [ ] c4 A ready pull request is open, assigned to the owner, bound with Auto-Fix on

## Where things stand

Not started.

## Next step

Write the receipt tests in tests/test_mitosis.py ProvenHead and confirm they fail on main.

## Decisions in force

## Open risks

## Settled, do not reopen

## Where the details live

- Defects log: ~/.mitosis/harness-redesign/examples/hr-mitosis-isolation/pipeline-0.11.0-defects.md (P10)
- Root cause: run.py _settled treats shipped and pushed as terminal on resume
