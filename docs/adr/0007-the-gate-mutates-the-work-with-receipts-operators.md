# The gate mutates the work with receipts' operators, ported into mitosis

Status: accepted

A property passes the gate only when it fails without the work, passes with it, and fails against at least one mutant of its Step's added lines; otherwise it is weak. The mutants come from a Python port of receipts 0.9.5's G14 operator table, string masking and extension allowlist, held to receipts by a fixture generated from receipts itself, at most 12 per property and stopping at the first kill. We rejected calling receipts' verifier, which needs Node, a separate clean checkout and the target project's own receipts configuration, because mitosis must work installed alone; we rejected an operator set of mitosis's own because it would drift from the gate library the harness standardises on. The cost is that a new receipts operator reaches mitosis only when the port and its fixture are refreshed.
