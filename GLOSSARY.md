# mitosis

mitosis splits a batch of changes or a document into parts built in parallel, proves each part's acceptance tests fail without the work, and opens one pull request per part. These terms name what it records about itself.

## Metrics

**Metrics store**:
The folder tree on this machine, ~/.plugin-metrics or PLUGIN_METRICS_DIR, where mitosis and the tools around it write their events, run summaries and catalogues in one shared format.
_Avoid_: metrics database, telemetry file

**Metrics run**:
One invocation of the mitosis command, from the moment its run directory is known to its exit, recorded under one run id; a resume or a ship of the same run directory is a new metrics run.
_Avoid_: build, run directory

**Run summary**:
The one line per metrics run holding its totals and its own spend, replaced whole each time it is written.

**Catalogue**:
The file describing every event and field mitosis writes, its purpose and the objectives each field measures, copied into the metrics store.

**Own spend**:
The tokens and dollars of the model sessions a metrics run started, plus the invoking session's messages inside the run's span, never a child run's.
_Avoid_: total cost, tree cost

**Session id**:
The id mitosis gives one Worker dispatch or one structure or brief call, which names that call's transcript.

**Child run**:
A metrics run of any tool whose parent is a mitosis metrics run, because a command mitosis started ran it.

**Caller window**:
The invoking session's messages timed between a metrics run's start and its summary, counted in mitosis's own spend.

## Pull requests

**Facts file**:
The JSON file mitosis writes for one MSP at ship, holding the title, problem, change, impact, testing and trade-offs facts that pull-request render --fields turns into the pull request's title and body.
_Avoid_: body file, pull request template

**Render probe**:
The call of pull-request render --fields that mitosis makes at the start of a run that names a type and subject, with each MSP's worst-case facts, so a missing, old or refusing renderer stops the run before any Worker is dispatched.
_Avoid_: preflight check, dry run

**Step commit**:
The one commit a Step's Worker session lands on its Lane's branch, with the subject `<type>(<scope>): <step name>`.
_Avoid_: lane commit, landing commit
