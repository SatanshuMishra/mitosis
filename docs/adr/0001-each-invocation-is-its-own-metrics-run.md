# Each invocation of mitosis is its own metrics run

Status: accepted

mitosis records one metrics run per invocation of its command, so a plan-only pass, a build, a resume and a ship of the same run directory are four runs joined by their plan id. A harness that starts mitosis leaves out of its own spend every message timed inside a child run's span, so a single run stretching from build to ship would take the harness's whole review loop into mitosis's span and lose it from both. We chose this over one metrics run per run directory, which reads more naturally but would make a run's span cover work mitosis never did.
