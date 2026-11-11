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
The id mitosis gives one Worker dispatch or one structure call, which names that call's transcript.

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

**Lane commit**:
The one commit a Lane's Worker session lands on its MSP's branch, with the subject `<type>(<scope>): <step names>`, its Steps' names joined by commas.
_Avoid_: step commit, landing commit

## Worker clones

**Seed file**:
The `.worktreeinclude` at the root of the user's checkout, in gitignore syntax, listing the gitignored paths a project lets every Worker clone start with; without it nothing is seeded.
_Avoid_: include file, copy list

**Seeded path**:
A path the seed file matches that the repository ignores and does not track, copied by copy-on-write from the user's checkout into a Lane clone or the check-author's clone before its Worker starts; it never lands and never reaches the gate folder.
_Avoid_: warm cache, copied folder

## Referee and gate

**Referee**:
The checks that judge a Worker's work and everything they read: the locked checks, test configuration, snapshots, baselines, allowlists and skip lists.
_Avoid_: judge, oracle

**Locked check**:
An acceptance test a check author writes before the build, locked only when it fails on the feature base and passes against the check author's probe, and committed as the first commit of the branch of the one MSP whose Steps name it; a path a `- Locked:` line of the charter names is a locked check every MSP holds.
_Avoid_: frozen test, pinned test

**Guard**:
An acceptance test that passes with and without the work while another test of its Step fails without it, so it proves the work left that behaviour alone and not that the work exists. The lock accepts it, and at the gate it must pass against the finished work like any other test.
_Avoid_: regression test, no-op test

**Probe files**:
The files a check author's probe may write for an MSP: the files of that MSP's Steps and of every MSP it merges. Only probe writes there are overlaid when mitosis judges a locked check, and no probe write is committed.
_Avoid_: scratch implementation, stub files

**Lock commit**:
The commit holding an MSP's locked checks on top of the feature base: the first commit of that MSP's branch.
_Avoid_: test commit, base commit

**Referee path**:
A path the charter locks, a test-configuration file or snapshot anywhere, or a baseline, allowlist or skip list inside a test location; a Step's own locked acceptance file is handed to its Worker read-only, any other in a Step's files refuses the plan, and a committed write to one is restored and holds its MSP back.

**Gate folder**:
The clean checkout the gate runs in: a folder outside the Worker's worktree, written from the MSP commit's tree objects with git's settings pinned, holding dependency folders from the repository root or the gate setup command, and reset to its target tree before every acceptance run. It is not a git repository.
_Avoid_: gate tree, normalized worktree

**Ignored file**:
A file in the Worker's worktree that the MSP's commit does not carry; it never reaches the gate, is listed in the MSP's `ignored` list and is never restored.
_Avoid_: stray, uncommitted write

**Held-back MSP**:
An MSP with an acceptance test still failing against the finished work after one repair, or whose whole change a repository hook refused, committed on its branch in the state gate-failed and never pushed.

**Parked Lane**:
A Lane whose Worker stopped at a usage limit, or that was not dispatched after one, recorded parked-limit so a resume runs it.
_Avoid_: paused Lane, failed Lane

**Finding**:
One entry of the run's findings list: a Lane or MSP result that keeps the run from exit 0, with its kind and its exit code.

**Outcome entry**:
The line the gate appends to the trajectory file for one acceptance test, in the K6 shape receipts' trajectory store holds.

## Hand-offs

**Hand-off**:
A file or value one tool writes and another reads, such as the result line, state.json, a Worker's reply or an items file, each with one owned schema.
_Avoid_: interface file, output format

**Owned schema**:
The program's JSON Schema that fixes one hand-off's format, carried byte for byte in the contracts folder and checked by whoever writes and whoever reads the hand-off.
_Avoid_: contract copy, format spec

**Call schema**:
The schema mitosis passes to a model command through the schema placeholder, derived from an owned schema and never wider than it.

**Structured reply**:
The value a model command returns as the structured_output of its JSON result, the only part of a model's output mitosis reads.
_Avoid_: return line, last JSON line

**Re-ask**:
A second turn of the same model session, sent through the re-ask command with the schema errors of a reply that failed, whose reply is judged once more and never re-asked again.
_Avoid_: retry, re-dispatch

## Inputs

**Ask**:
A plain-text request for work, from one line to a few paragraphs, given one per line; mitosis plans each ask on its own and in parallel.
_Avoid_: task, prompt, crude input

**Spec**:
A document with sections that describes work, written by any author in any format.
_Avoid_: design doc, plan

**Own test**:
An acceptance test the Step it checks writes in the same Worker session as the work, its file part of that Step's files; every Step of a plan mitosis makes from a spec or asks writes its own tests.
_Avoid_: unlocked test, worker test

## Plans

**Departure**:
A behaviour in which mitosis differs from the reference planner, the planner mitosis is built from; it stays only when a measurement or a reproduction shows the reference planner's behaviour failing where mitosis's does not.
_Avoid_: improvement, divergence

**Tier note**:
A line of a plan that says why a Step's tier was raised: its blast radius, its complexity, a risk marker or a recorded breakage.
_Avoid_: tier reason, bump reason

**Coupled pair**:
Two Lanes a coupling signal links, which mitosis flags for a verdict and never orders itself.
_Avoid_: ordered pair, conflict

**Verdict group**:
The coupled pairs that share one set of coupling signals, answered by one verdict.
_Avoid_: pair group, cluster

**Shared context**:
A file two or more Steps read but none edits, listed once for all of them.
_Avoid_: common files

**Saved plan**:
A plan file mitosis builds as written, with the charter's locks and its check authors' files merged back into its locked list.
_Avoid_: plan replay

**Shared file**:
A file a caller declares as edited by many MSPs in separate regions; an overlap on it alone fuses no MSP and orders none.
_Avoid_: hub file, common file

**Integration order**:
The order to merge the MSPs that edit one shared file: producers first, then the longest remaining chain, then input order.
_Avoid_: merge queue, shared order

**Output tail**:
The end of a Worker's last reply, kept in its Lane record when no valid reply came back.
_Avoid_: raw output, log tail
