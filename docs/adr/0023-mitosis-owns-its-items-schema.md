# Mitosis owns its items schema

Status: accepted.

Mitosis accepts every item interphase or the reference planner accepts, including the reference planner's type values, text, cost and file notes, stays strict about structure, and no longer treats interphase's schema as its input contract. We chose this so each plugin stands alone: interphase can drop its items file without mitosis changing, and any author can hand mitosis a plan. The cost is a wider schema mitosis must keep accepting.
