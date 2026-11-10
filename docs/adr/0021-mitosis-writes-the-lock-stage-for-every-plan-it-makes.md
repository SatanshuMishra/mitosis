# Mitosis writes the lock stage for every plan it makes

Status: superseded by 0026.

A plan built from an ask or a spec has no author to write its acceptance tests, so mitosis generates one check-author per MSP from the plan's acceptance entries, keeps each Step's prose acceptance line word for word beside the named test that checks it, and refuses before any build a plan that names a test nothing writes. We chose this over letting such plans build without locked tests, because the gate's claim is that a test failed before the work and passed after it, and a plan with no author would lose that. The cost is one more Step per MSP and a lock stage that can refuse or stall.
