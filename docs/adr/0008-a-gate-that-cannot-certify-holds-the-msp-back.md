# A gate that cannot certify holds the MSP back

Status: accepted

When an MSP's gate is weak, inert or inconclusive, or the gate could not run, mitosis commits the branch and never pushes it or opens its pull request; the state is gate-failed. Earlier releases shipped such MSPs with a "Not verified" line and a non-zero exit, and about a third of gated MSPs reached a human that way unproven. We chose to withhold the pull request because a property the gate cannot certify is a silent pass once it ships; the caller can still ship a head it proved by other means with --proven-head.
