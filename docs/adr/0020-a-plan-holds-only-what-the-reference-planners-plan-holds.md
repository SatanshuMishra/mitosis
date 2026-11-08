# A plan holds only what the reference planner's plan holds

Status: accepted. Amends ADR 0004: --session-tokens and the per-Step token estimate are gone.

plan.json carries no token estimate, session limit, file sizes, clusters, waves or ready-after, as the reference planner's plan does not; waves and ready-after are still computed, to order each Lane's Steps and to group cheap Steps for coalescing. Its plan id is the reference planner's hash of the plan with no version key, so a release alone never changes it. We chose the reference planner's shape because no replay showed the extra keys changing a build for the better, and each was a field every reader had to keep in step. Every Step still runs as its own Worker session.
