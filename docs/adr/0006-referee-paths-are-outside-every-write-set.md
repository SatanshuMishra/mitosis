# Referee paths are outside every write-set

Status: accepted

mitosis keeps every referee path out of every write-set: a path the charter locks, a test-configuration file or snapshot anywhere, or a baseline, allowlist or skip list inside a test location. A Step's own locked acceptance file is handed to its Worker read-only, any other referee path in a Step's files refuses the plan, and any such path the MSP branch commits is restored after the pre-gate commit, holding that MSP back; what a Worker leaves uncommitted never reaches the gate (0009). The acceptance tests are written and locked by a check author before the build, so the builder never owns what judges it. We chose refusal by code over telling Workers not to touch those files, because an instruction cannot stop a Worker that writes them anyway; the cost is that a real need to change test configuration, or a test-location baseline or allowlist, has to come from outside the build.
