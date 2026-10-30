# Gate classes live outside the plan id

Status: accepted

`--gate-classes` names a JSON list of per-property classes (not-applicable, covered-elsewhere, removed), each with a reason, kept out of the plan and its id and stored in the run state with its SHA-256. A classed property is judged by its class without a run; on `--resume`, every built Lane is kept and each unclassed property reuses its verdict recorded at the same MSP commit, and every gate still appends one outcome entry per property. We chose this over editing the items file, which changes the plan id and makes `--resume` refuse, forcing a rebuild of every Lane to drop or excuse one property. An entry naming no property of the plan is refused, so a typo never silently excuses nothing.
