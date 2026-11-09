# Changelog

Each heading is a release of the mitosis plugin. An installed copy updates
only when the version changes, so every release changes it.

## 0.15.1 - 2026-11-08

- A test that passes without the work counts as a guard of unchanged
  behaviour when another test of its Step fails without the work, at the
  lock and at the gate, so a spec's rows for what must stay the same no
  longer block every Lane.
- A Step whose tests all pass without the work is still refused.

## 0.15.0 - 2026-11-08

- mitosis takes a file of plain-text asks, with one planning call per ask.
- A spec is planned with the reference planner's recon text in its structure
  prompt, and each structure call sees the recon environment variables.
- mitosis writes the lock stage for every plan it makes and refuses a plan
  that names a test nothing writes.
- mitosis owns its items schema and accepts text as an alias for task.
- A retry with one Step replaced no longer stops on its earlier worktree.

## 0.14.1 - 2026-11-08

Names only; plans and builds are unchanged.

- A Worker's variables are now MITOSIS_BRANCH, MITOSIS_CLUSTER, MITOSIS_ITEM,
  MITOSIS_ITEMS, MITOSIS_PLAN_ID, MITOSIS_TIER and MITOSIS_FILES.
- MITOSIS_TRAJECTORY_STORE overrides the default history store.

## 0.14.0 - 2026-11-08

Planning drops the estimates and findings that proved unreliable, and markers
take one comma-separated value. This release breaks compatibility.

- Install the pipeline release that accepts this result and run state before
  installing this one; an older pipeline refuses mitosis's result and state.
- The --session-tokens flag is removed, along with the token estimate on each Lane,
  the session limit and the MSP dependency groups.
- The plan no longer carries the version, clusters, coupling_order,
  session_tokens or sizes keys, and the plan id no longer hashes a version key.
  It adds `tier_notes`, `shared_context` and `verdict_groups`.
- The lane-cycle and manifest-exports-nothing findings are removed, and exit 23
  with them; an empty manifest now exits 0.
- `--risk-markers` and `--serial-markers` take one comma-separated value, such
  as `--risk-markers auth,billing`, and a repeated flag keeps the last value.
  The old space-separated form, `--risk-markers auth billing`, now exits 2.
  `--serial-verify-markers` is the new name for `--serial-markers`, which
  stays as an alias.
- `--dry-run` prints each Worker's command and builds nothing.

## 0.13.3 - 2026-11-06

Plan refuses the worker commands a build would refuse.

- `--plan-only` now checks `--dispatch-command` and `--reask-command` whenever a
  dispatch command is given. Before this, a stale command passed plan and stopped
  the run at build.

## 0.13.2 - 2026-11-06

Any Steps now give a pull request that meets its owned format.

- A Step's change line stands in with the Step's name when its opening sentence
  repeats another Step's or breaks the format, and the list is shortened when it
  passes twelve lines or three hundred words. Before this, Steps that opened with
  the same project description stopped the run at plan.
- A testing row stands in with a number when a test or Step name breaks the
  format, and rows past twelve fold into their totals.

## 0.13.1 - 2026-11-06

A landed Lane stays landed when its clone cannot be removed.

- mitosis records a Lane as landed before removing its clone, and a removal
  that fails, as when a background process is still writing in the clone, now
  leaves the clone behind with a warning instead of stopping the run. Before
  this, a resume landed the half-removed clone again and committed the
  deletions as the Worker's work.

## 0.13.0 - 2026-11-04

Every hand-off mitosis writes or reads has one owned format, checked where it
is produced.

- mitosis vendors byte-identical copies of the program's validator and owned
  schemas in `contracts/`, and `handoff.py` checks each hand-off against them.
  mitosis owns h8, the result line, h9, the run state, h10, the Worker and
  check-author reply, h10b, the structure reply, h10c, the brief reply, k2,
  the lock record, and k6, each outcome entry; it checks h12, the items it
  reads, h15, the pull request's facts file, and h16, render's reply, which
  other plugins own. h8 and h9 accept the lock on each MSP, every lock state
  and the finding kinds a stopped lock reports.
- `--dispatch-command`, `--decompose-command` and `--brief-command` must carry
  `{schema}`, the call schema as JSON text, and `{session_id}`; a template
  without either is refused with exit 3, where one without `{session_id}`
  used to run unpriced. Each reply is read only from the structured_output field of the JSON object
  the command prints, never from the last JSON line of prose.
- `--reask-command`, offered `{prompt}`, `{session_id}`, `{schema}` and
  `{model}`, is required for any run that calls a model; a reply that fails its
  owned schema is asked once more in the same session with the errors, and a
  reply that still fails fails its call.
- A run state, result line or lock record that fails its owned schema stops
  the run with exit 15 and writes nothing for it. A reason in the result line
  is no longer cut short.
- The file `--items` names must meet h12, with `spec_ref` on every Step and
  `source` naming the document, or the run is refused with exit 3.
- A pull request is recorded only as the http or https URL the pull-request
  command prints on its last line; any other line ends the MSP `ship-failed`.

## 0.12.0 - 2026-11-03

Each MSP locks its own acceptance tests, and mitosis has no wall-clock limit.

- Each MSP's check-author locks only that MSP's tests, as the first commit of
  that MSP's branch, judged against the check-author's probe: each locked
  test must exit 1 at the feature base plus the locked files and exit 0 once
  the probe is added, and the probe is never committed. A check-author with
  an `msp` label belongs to that MSP; the one unlabelled check-author is split
  by acceptance ownership. The feature branch is not moved at lock time.
- A failed lock blocks only its MSP and the MSPs that merge it, directly or
  through another; every other MSP is built, gated and finished.
- The result's `msps[].lock` replaces the top-level `lock`, and each MSP's
  gate, reconcile and referee judge against its own lock commit.
- mitosis has no wall-clock limit: no Worker, check-author, acceptance run,
  gate setup, render probe or git command has a timeout, and the reaper ends
  a finished Lane's leftover processes until none is left, so exit 14 is
  gone. `--timeout` is deprecated and ignored: a run given it prints one
  stderr line saying so, and a run without it starts.
- Every process mitosis starts runs with no input, the null device or a
  fixed input written in full, with terminal and pager prompts off, and with
  git's dead-peer detection on: ssh in batch mode with keep-alives and
  `http.lowSpeedLimit` and `http.lowSpeedTime` set.

## 0.11.0 - 2026-11-01

Worker clones start warm from copy-on-write copies of the caches a project
declares.

- A project opts in with a `.worktreeinclude` at its repository root, in
  gitignore syntax. mitosis copies each listed path that is ignored and
  untracked into every Lane clone and the check-author's clone by
  copy-on-write only, so a Worker can run the project's tests without
  re-installing or rebuilding its caches.
- A Python virtual environment and a secret-looking name are refused, even
  inside a listed folder, whose other contents are then copied one by one;
  a copy holding a symbolic link that leaves the clone is refused whole, and
  a path that cannot be copied leaves the Lane cold. Seeded paths never land and never reach the
  gate's clean checkout.
- Each Lane record and the lock record list `seeded`, seed_skipped and
  seed_seconds. With no `.worktreeinclude`, nothing changes.

## 0.10.1 - 2026-11-01

A resumed ship pushes a proven head that moved past what mitosis had pushed.

- A resume that pushes now ships an MSP already `pushed` or `shipped` again
  when `--proven-head` names a commit other than the one mitosis pushed.
  Earlier releases treated both states as final on a resume, so review
  commits made after the push stayed off the remote and the returned body
  still named the old proven commit. mitosis now pushes the branch to the new
  head as a fast-forward and renders the title and body again for it. An MSP
  whose pull request mitosis opened keeps that pull request and its address,
  and the pull-request command is not run again.
- A proven head equal to the pushed commit leaves the MSP and the remote as
  they were.
- A head that does not descend from the pushed commit is refused and never
  force-pushed: the MSP ends `ship-failed`, with exit 22, and its record keeps
  the earlier push.

## 0.10.0 - 2026-10-30

Each Worker builds in its own clone, the acceptance checks are locked before
the build, and mutants come only from the lines an acceptance test runs.

- Each Lane's Worker builds in its own clone of its MSP branch under the run
  directory's `clones` folder, with its own git directory and object store
  and no remote. The MSP worktree is mitosis's own checkout, where no Worker
  runs. When the Worker exits, mitosis reads the Lane's write-set from the
  clone's files without running git there and lands only those paths in one
  commit. A write outside the write-set is replayed into mitosis's checkout
  for reconcile alone, so an undeclared write still exits 6 and a referee
  write is still reverted with exit 9, and the gate sees each such path as it
  stood when the Lane started.
- `--worker-sandbox` runs every Worker under sandbox-exec with a profile that
  refuses file writes outside its clone, a private temp folder, its session
  transcript folder, /dev/null, /dev/tty, /dev/fd and each `--worker-write`
  path. It also runs the gate's `--gate-setup`, acceptance, mutant and
  `--suite-command` runs and the lock stage's checks under sandbox-exec,
  writing only their clean checkout (`gate/msp-<n>/checkout-*` or
  `lock/checkout-*` under the run directory), a private temp folder
  (`gate/msp-<n>.tmp` or `lock.tmp`, passed as TMPDIR and holding the line
  traces and suite reports) and each `--worker-write` path, so a gate setup
  that needs another writable path names it with `--worker-write`. mitosis's
  logs and state stay outside every write grant, and no sandboxed command may
  rename, remove or replace a folder it writes into. A `--worker-write` path
  that is, holds or lies inside the run directory, the repository's working
  tree, its git directory (`git rev-parse --git-common-dir`, so a linked
  worktree's shared one) or a folder mitosis places MSP worktrees in, compared
  by resolved real path, refuses the run with exit 3 before any Worker starts,
  naming the one it overlaps. A host without
  sandbox-exec refuses the run with exit 3 before any Worker starts, and the
  JSON result's `sandbox` names sandbox-exec or `off`. When a Worker, gate or
  lock-stage command exits, mitosis ends its process group and every process
  left in that run's sandbox before reading its output; a process it cannot
  end within 10 seconds stops the run with exit 14.
- Every git call drops the environment variables that shape a diff, widen a
  pathspec, locate another repository or point at other objects, so nothing
  the caller's shell sets changes what mitosis reads.
- A Step with role check-author is the lock stage. It never becomes a Lane,
  an MSP or a pull request; when the feature branch lacks a locked path, it
  runs first in its own clone, each other Step's acceptance test must exit 1
  at the tree it wrote, and mitosis commits the locked paths on the feature
  branch as `test: lock the acceptance checks` before any Lane is built. Its
  record is the JSON result's `lock` object. A feature branch that already
  holds every locked path runs no check-author.
- The gate samples its mutants only from added lines the with-work acceptance
  run executed, traced by a standard-library tracer. Each property's
  mutation is killed, `weak`, `not-applicable` when the test loads none
  of the Step's files or the Step added no code file for a mutant to change, or
  covered-elsewhere, and its `coverage` is `traced` only when the sampled
  lines came from the trace and `untraced` whenever they did not, as when no
  trace was written, only JavaScript, TypeScript or shell lines were sampled,
  or the gate fell back to every added line.
- `--suite-command` runs the project's full suite once with no mutant and
  then on each mutant every acceptance test of a property survives; when the
  run with no mutant passed and the suite fails on each mutant, the property
  is covered-elsewhere and names the failing tests in `killers` instead of
  `weak`. A suite that fails or times out with no mutant kills nothing, and
  the property stays `weak` with that reason.
- `--gate-classes` names a JSON list of `{test, class, reason}` objects
  classing a property `not-applicable`, covered-elsewhere or `removed`. The
  classes stay out of the plan id, so `--resume` keeps every built Lane and
  each unclassed property reuses its verdict recorded at the same commit.
- A property `--gate-classes` classes reads in the pull request's Testing
  section as Not run, naming its class, never as proven.

## 0.9.0 - 2026-10-30

mitosis no longer writes pull-request text itself, and each Step lands as its
own commit.

- At ship mitosis writes each MSP's facts to a facts file, runs
  pull-request render --fields on it, and uses the title and body render
  printed unchanged. Render writes the body from those facts: Problem from
  the `--pr-why` lines, Change one entry per Step, Impact, Testing one row
  per acceptance test with a Not run row and its reason for each one left
  unproven, and Trade-offs and open problems for what reconcile, stacking or
  the build found. A refusal ends the MSP `ship-failed` with render's problem
  as its reason and is never retried with altered facts.
- A run needs pull-request 0.3.0 or later on PATH, or named with the new
  `--render-command`. Every run that names `--pr-type` and `--pr-subject`,
  `--plan-only` and `--no-push` included, needs `--timeout` to bound the probe
  and runs render on each MSP's facts
  before any Worker is dispatched, and refuses the run when the command is
  missing, older than 0.3.0 or refuses the facts.
- The new repeatable `--pr-impact` gives the Impact lines; without it the
  `--pr-blast-radius` sentence stands in, and a run that names `--pr-type`
  and `--pr-subject` with neither is refused. `--pr-door` is still accepted
  and no longer reaches the body.
- Every Step of a Lane is its own Worker session, run in Step order on its
  MSP's branch, and lands its own commit with the subject
  `<type>(<scope>): <step>`, or chore with no scope when the run names no
  type, and its task's first sentence as the body.

## 0.8.0 - 2026-10-28

The files that judge the work are a locked referee no Worker may write, and
the gate withholds a pull request it cannot certify.

- A charter names its locked checks one per line as `- Locked: <path>`, and
  built-in referee markers cover test configuration, snapshots, baselines,
  allowlists and skip lists. Planning refuses a Step whose files hold a
  referee path, with exit 3. A Worker's committed write to one, found from
  git's tree objects with Worker-written settings kept out, is reverted in
  its own commit, and its MSP is held back as `referee-reverted` and never
  pushed, with exit 9. The JSON result's `referee` object names the locked
  paths, their receipt lock and the commit they were read at.
- A `- Locked:` path the feature branch does not hold, in a real run, and a
  `- Locked:` line that is empty, absolute or contains `..` are refused with
  exit 3.
- The gate runs each passing property against up to 12 mutants of its Step's
  added lines and judges it `weak` when no mutant fails it, with exit 24. An
  MSP whose gate is `weak`, `inert` or `inconclusive` ends `gate-failed`,
  committed and never pushed, so the gate now withholds the pull request.
- The JSON result carries a `findings` list, one entry per problem with its
  MSP, Lane, kind, exit code and detail, and the exit code is the most severe
  finding's.
- Every git call times out after 600 seconds with an error naming the
  command.
- A Worker that stops at a usage limit leaves its Lane `parked-limit` and its
  MSP `parked`, with exit 13, and `--resume` runs every Lane that is not `ok`.
- `--trajectory` also receives one outcome entry per acceptance test after
  every gate.
- The gate, every mutant run and every acceptance run execute in a clean
  checkout of the MSP's committed tree, a folder outside the Worker's
  worktree reset to the commit's raw blobs before every run. It is
  not a git repository. Files the commit does not carry never reach the gate
  and are listed in each MSP's `ignored` list, never restored.
- `--gate-setup` runs once per gate, so what it writes survives each run's
  reset only at top-level names the commit lacks, such as the `.git` folder
  an acceptance command that runs git needs it to make. A Git LFS file
  reaches the gate as pointer text and a committed eol attribute is not
  applied in every run, whatever `--gate-setup` does, so a test that reads
  the real content ends `inconclusive` and holds its MSP back with exit 21.
- A top-level file or folder `--gate-setup` makes is a referee write, fatal
  with exit 9, when its name is a referee path such as `conftest.py`, a
  referee folder such as `__snapshots__` or `.receipts`, or a folder holding
  a `- Locked:` path; mitosis never walks into a kept folder such as
  node_modules.
- Dependency folders come from the repository root's node_modules and
  virtual-environment folders, or from the new `--gate-setup` command, run
  once per gate with `{worktree}`. A failed or timed-out gate setup holds the
  MSP back as `gate-failed`, with exit 21, and so does a refused pre-gate
  commit.
- An acceptance run that leaves a referee path in the clean checkout
  differing from the commit holds its MSP back as `referee-reverted`, with
  exit 9.
- Ship refuses a head that is not the gated commit, as `ship-failed`, with
  exit 22.
- The referee and the gate judge the MSP branch's commit, never the
  worktree's HEAD. A worktree HEAD that is detached, on another branch or at
  another commit holds the MSP back as `referee-reverted`, fatal with exit 9,
  after any referee write on the branch is reverted, and the gate does not
  run.
- A resume's gate reuse and `--proven-head` depend only on the branch head's
  commit, never on files the worktree holds outside it.

## 0.7.0 - 2026-10-27

mitosis records metrics about its own runs, and a new metrics skill answers
how it is performing from them.

- Every invocation of the mitosis command is one run in the metrics store,
  `~/.plugin-metrics`, or `PLUGIN_METRICS_DIR` when it is set, once its run
  directory is known: a plan, a build, a resume or a ship. It appends events
  at the boundaries of its work, the run's start and end, the structure and
  brief stages, the plan, each Lane, each gate, reconcile and each MSP, and
  replaces one run summary holding the split, the parallelism, the gate
  outcomes, the MSP states and its own spend. The store holds run metadata
  only, never a task, a brief, a Worker's output or file contents.
- A run's own spend is priced when its summary is written, by exact model id
  in the five token kinds, from the price table `prices.json` and its date.
  A model the table lacks keeps its tokens with no price, and a call whose
  transcript is not found is counted in `sessions_missing`, so
  `spend_complete` says whether the figure is whole.
- Every Worker dispatch, structure call and brief call gets a fresh session
  id, offered as the `{session_id}` placeholder on the dispatch, decompose
  and brief templates, for `claude -p --session-id {session_id}`. A Lane
  records it as `session_id`, and its spend is read from that session's
  transcript.
- Every command mitosis starts gets `PLUGIN_METRICS_PARENT` naming the run,
  so a run another tool records under it is listed among its children, with
  that child's own spend kept apart.
- `catalogue.json` describes mitosis's purpose, its objectives, every event
  and field it writes and six named queries, and is copied into the store.
  The metrics skill reads it first and answers through the duckdb command
  over plugin=mitosis alone; without DuckDB it names `brew install duckdb`
  and stops.
- A metrics write that fails prints one line on stderr and never changes the
  exit code or the last JSON line.
- `docs/metrics-format.md` describes the shared record format.

## 0.6.0 - 2026-10-16

A slow acceptance test no longer runs twice on code that was already gated
or proven.

- A resume reuses an MSP's recorded gate result when that result passed, the
  MSP's HEAD is the commit the gate ran at, and its worktree holds no
  uncommitted change. Any other MSP is gated again, so a blocking gate is
  always retried. Only the same run directory's state is read.
- `--proven-head <MSP label>=<commit>`, repeatable, lets a caller that proved
  a later head ship it without gating again, when the MSP's HEAD equals that
  commit, its worktree holds no uncommitted change and a gate result that
  passed is recorded.
  The pull request's Verification section then ends with a Not verified:
  line naming the commit the gate ran at and the commit the caller proved. A
  head that differs, or an MSP with no recorded gate result, is gated as
  before. A value that is not a label, an equals sign and 7 to 40 lowercase
  hexadecimal digits, or that names no MSP of the plan, exits 2 before
  anything is built.

## 0.5.0 - 2026-10-16

Every pull request body can carry the merge-danger call the pull-request
plugin renders, and mitosis no longer describes its pull requests as drafts.

- `--pr-door` takes one-way or two-way and `--pr-blast-radius` takes a
  sentence; given together, they open every body's Risk section as a
  `Door:` line and a `Blast radius:` line, ahead of any line reconcile, the
  gate or stacking writes. One without the other, a door outside the two
  values, or a blast radius that is not a house sentence is refused before
  any Worker spawns. The run state records the pair, a resume that leaves
  both flags out carries it over, and a resume that passes a different pair
  is refused, so every pull request of one run carries one door call.
- A Why line, the subject, or any other value beginning with `door:` or
  `blast radius:` is refused, as `verified:` already was, so no value can
  pose as a merge-danger line.
- The `--pr-command` help, the skill, the README and the plugin manifests
  speak of pull requests, not draft pull requests, and the skill's example
  command no longer opens a draft.

## 0.4.0 - 2026-10-15

A Lane too large for one Worker session is built as several fresh sessions
in a row on its MSP's branch.

- Every Lane carries an estimate of the tokens its session will hold, from
  its Step count, the length of each task, the sizes of its write-set and
  read-set files, the charter and document every session reads, and, for a
  later session of a split Lane, the files the earlier sessions wrote.
- The plan-stage report prints each Lane's estimate, names every split Lane
  and its sessions, and names a Step whose estimate alone is above the limit.
  The JSON summary gives each Lane its session position.
- Gate, reconcile and ship are unchanged, since they work per MSP. A resume
  skips every session already `ok` and continues from the next one. A file a
  session left half-written when it failed stays out of another Lane's
  landing, so the next run of that session finishes it.
- The plan records the file sizes it measured, and a resume plans from those
  sizes, so a file that changed in the checkout after the run started does
  not change the plan id.

## 0.3.0 - 2026-10-10

Every pull request mitosis writes follows one house format, and a run can
ship without a pull-request command.

- The title is `<type>(<scope>): <subject>` from the new `--pr-type`,
  `--pr-scope` and `--pr-subject` flags, with the MSP label in parentheses
  when the plan has several MSPs.
- The body holds What changed, one entry per Step from its task's first
  sentence; Why, the lines given with `--pr-why`; Risk, only when reconcile,
  a foreign write, a manifest, stacking or the gate found something; and
  Verification, one line per acceptance property by its gate verdict. No gate
  reason, acceptance command or draft notice is written into it.
- `--pr-type`, `--pr-subject` and at least one `--pr-why` are required on a
  run allowed to push, and every one of the four is checked before any
  Worker spawns.
- `--pr-command` is optional. Without it a run allowed to push pushes each
  MSP branch, opens no pull request, ends that MSP in the new state `pushed`,
  and records the title and body in its state and in the JSON summary. A
  resume skips a `pushed` MSP, and `pushed` counts toward exit 0.

## 0.2.0 - 2026-09-21

The first release that installs as a Claude Code plugin and updates by
version.

- The skill runs the copy the plugin installed, and the plugin's version is
  the one `--version` prints.
- Every plan runs and every MSP ships as a draft pull request. A finding is
  written into the pull request, the JSON summary and the exit code; it never
  stops the run.
- MSPs that wait on each other are fused into one. Steps that share a risk
  marker, a recorded regression or a migration folder are put in order, and a
  pair joined only by an import is named before anything is built.
- A Lane that changes a file another Lane owns is named in the pull request.
- `--no-push` builds and commits every MSP without pushing or opening a pull
  request.
- The last line of stdout is a one-line JSON summary for the calling session.
- Four Workers run at once and each read-set holds at most forty files, by
  default.
- The version is part of each plan's id, so a run started on one release
  cannot be resumed on another.

## 0.1.0

The development version. It was never released.
