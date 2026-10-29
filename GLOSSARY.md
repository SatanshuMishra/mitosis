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

## Referee and gate

**Referee**:
The checks that judge a Worker's work and everything they read: the locked checks, test configuration, snapshots, baselines, allowlists and skip lists.
_Avoid_: judge, oracle

**Locked check**:
An acceptance test a check author writes and commits on the feature branch before the build, named on a `- Locked:` line of the charter.
_Avoid_: frozen test, pinned test

**Referee path**:
A path the charter locks, a test-configuration file or snapshot anywhere, or a baseline, allowlist or skip list inside a test location; a Step's own locked acceptance file is handed to its Worker read-only, any other in a Step's files refuses the plan, and a committed write to one is restored and holds its MSP back.

**Gate folder**:
The clean checkout the gate runs in: a folder outside the Worker's worktree, written from the MSP commit's tree objects with git's settings pinned, holding dependency folders from the repository root or the gate setup command, and reset to its target tree before every acceptance and mutant run. It is not a git repository.
_Avoid_: gate tree, normalized worktree

**Ignored file**:
A file in the Worker's worktree that the MSP's commit does not carry; it never reaches the gate, is listed in the MSP's `ignored` list and is never restored.
_Avoid_: stray, uncommitted write

**Mutant**:
A copy of the work with one added line changed on purpose by one operator of receipts' G14 table, which a passing property must fail against.

**Weak property**:
An acceptance property that fails without the work and passes with it, yet passes against every mutant tried, so it cannot tell working code from broken code.
_Avoid_: shallow test

**Held-back MSP**:
An MSP whose gate is weak, inert or inconclusive, committed on its branch in the state gate-failed and never pushed.

**Parked Lane**:
A Lane whose Worker stopped at a usage limit, or that was not dispatched after one, recorded parked-limit so a resume runs it.
_Avoid_: paused Lane, failed Lane

**Finding**:
One entry of the run's findings list: a Lane or MSP result that keeps the run from exit 0, with its kind and its exit code.

**Outcome entry**:
The line the gate appends to the trajectory file for one acceptance test, in the K6 shape receipts' trajectory store holds.
