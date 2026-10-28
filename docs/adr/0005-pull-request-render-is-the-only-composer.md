# pull-request render is the only composer

Status: accepted

mitosis composes no pull-request title or body. At ship it writes a facts file for the MSP and runs pull-request render --fields on it once, using the printed title and body unchanged; a refusal fails the ship with render's message and is never retried with altered facts. The format rules then live only in the pull-request plugin, at the price of needing pull-request 0.3.0 or later on PATH, which the render probe checks before any Worker runs. It replaces the house format mitosis composed itself, with its What changed, Why, Risk, Door, Blast radius and Verification sections.
