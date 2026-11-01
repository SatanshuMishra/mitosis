# fix-resumed-ship-proven-head: A resumed ship pushes a proven head that moved past the pushed head
Status: active
Updated: 2026-11-01T10:41:06.964Z
Next ids: c5 D1 R1 S1

## Goal

Defect P10: a resumed ship with --proven-head naming a commit that descends from an MSP's pushed head fast-forward pushes the branch and re-renders the body; an equal head is a no-op; a non-descendant head is refused, never force-pushed.

## Done when

- [x] c1 A receipt test fails on main and passes after: a later proven head moves the origin branch to it and the returned body is re-rendered; an equal head is a no-op; a non-descendant head is refused evidence: 4 new ProvenHead tests; 3 fail on main (origin left at old head, refusal exits 0), all 10 ProvenHead tests pass after; removing the ancestry check or the kept pull request each fails its test
- [ ] c2 The full test suite passes
- [x] c3 The version is bumped within 0.x.y with a CHANGELOG entry evidence: 0.10.1 in core.py and plugin.json, CHANGELOG heading 0.10.1, version tests renamed version_is_0_10_1
- [ ] c4 A ready pull request is open, assigned to the owner, bound with Auto-Fix on

## Where things stand

Fix and release are committed on claude/fix-resumed-ship-proven-head-7a721e, not pushed; the full suite is rerunning after the skill file was kept under its 20480-byte cap.

## Next step

When the suite passes, push, render the body from the scratchpad pr-fields.json with the suite result filled in, and open the pull request.
## Decisions in force

## Open risks

## Settled, do not reopen

## Where the details live

- Defects log: ~/.mitosis/harness-redesign/examples/hr-mitosis-isolation/pipeline-0.11.0-defects.md (P10)
- Root cause: run.py _settled treats shipped and pushed as terminal on resume
- Receipts: tests/test_mitosis.py class ProvenHead
