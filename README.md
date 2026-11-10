# mitosis

mitosis takes work that one agent would grind through serially, splits it so
several agents run on it at once, and produces pull requests a human can
merge without discovering that the parallelism broke something. It validates
the input, schedules the split, prepares a worktree and branch per piece,
builds each Lane in its own clone of that branch, lands only the Lane's
write-set, gates the result, reconciles what was
actually written against the git log, and opens one pull request per
piece, stacked on its predecessor where one exists, or pushes each piece's
branch and hands back its pull request's title and body for the caller to
open.

## Inputs

mitosis takes exactly one of three inputs, and needs nothing else to run:

- `--items`, a JSON array of Steps, handed to it directly.
- `--spec`, a document in any format, which mitosis decomposes into that same
  Steps array, each Step with its task, in one structure call before
  scheduling.
- `--asks`, an asks file, one ask per line, each planned by its own call
  through `--decompose-command`.

All three converge on the same items format before anything downstream runs.
On `--spec` and `--asks` each Step writes its own acceptance tests: mitosis adds
every test file a Step's acceptance names to that Step's files, unless the
charter locks it, so a plan mitosis makes has no check-author Step.

The file `--items` names is read through mitosis's own items schema,
`schemas/mitosis-items.schema.json`, which accepts every file h12 accepts and
the reference planner's Steps: a Step carries `name`, `files`, and `task` or
`text`; `source`, `acceptance` and `spec_ref` are optional. `source` names the
document by absolute path and SHA-256. A file that fails it is refused with
exit 3, each error named with its Step, before anything is planned. Two Steps
that build a validator and then its command-line flag:

```json
[
  {
    "name": "validate-manifest",
    "task": "Add validate_manifest(path) to app/manifest.py, returning one error per missing key.",
    "files": ["app/manifest.py", "tests/test_manifest.py"],
    "source": {
      "path": "/work/app/docs/specs/manifest.md",
      "sha256": "4bc2c86cd92744fbb47c8f13e04a42d78fec19f8c032ab091e4a8255720346c8"
    },
    "acceptance": [
      {"file": "tests/test_manifest.py", "test": "a_missing_key_is_reported_by_name"}
    ],
    "spec_ref": ["1.1"],
    "complexity": "simple"
  },
  {
    "name": "validate-flag",
    "task": "Add --validate to app/cli.py, printing those errors and exiting 1 if any.",
    "files": ["app/cli.py", "tests/test_cli.py"],
    "source": {
      "path": "/work/app/docs/specs/manifest.md",
      "sha256": "4bc2c86cd92744fbb47c8f13e04a42d78fec19f8c032ab091e4a8255720346c8"
    },
    "acceptance": [
      {"file": "tests/test_cli.py", "test": "validate_exits_one_on_a_bad_manifest"}
    ],
    "spec_ref": ["1.2"],
    "after": ["validate-manifest"],
    "complexity": "simple"
  }
]
```

## What it never does

mitosis does not merge. Merging is a judgment call — review passed, CI green,
blast radius acceptable — and that judgment stays with a human.

mitosis does not run the full test suite; continuous integration does that, on
the pull request it opens. It does not name a model, hold an API key, or link
an SDK — every model invocation is a subprocess spawned from a command
template the caller supplies. And it does not judge a document's quality: it
reports how cleanly a document decomposed, and never refuses one.

## Install

mitosis installs as a Claude Code plugin from this repository. Add the
repository as a plugin marketplace, install the plugin, then restart Claude
Code:

```bash
claude plugin marketplace add SatanshuMishra/mitosis
claude plugin install mitosis@mitosis
```

The plugin carries the skill and the eighteen modules it runs — `core.py`,
`shape.py`, `decompose.py`, `run.py`, `isolation.py`, `pr_facts.py`,
`mitosis.py`, `metrics.py`, `spend.py`, `metrics_writer.py`, `referee.py`,
`mutants.py`, `outcomes.py`, `handoff.py`, `recon_text.py`, `step_names.py`,
`asks.py`, `own_tests.py` — the two data files they read,
`prices.json` and `catalogue.json`, the `contracts/` folder beside the
modules, which holds byte-identical copies of the program's
`contracts/validator.py` and of every owned schema mitosis writes or reads,
and the `schemas/` folder beside `contracts/`, which holds mitosis's own items
and recon schemas.
They need Python 3.9 or later. A run that names `--pr-type` and
`--pr-subject` also needs the pull-request command, 0.3.0 or later, on PATH
or named with `--render-command`, because mitosis writes no pull-request text
itself.

To update, refresh the marketplace, update the plugin, and restart:

```bash
claude plugin marketplace update mitosis
claude plugin update mitosis@mitosis
```

An installed copy only updates when the plugin's version changes, and every
release changes it; `CHANGELOG.md` lists them. The version is part of each
plan's id, so finish a run before updating: it cannot be resumed on another
release.

Without Claude Code, copy the nineteen modules, the `contracts/` folder and
the `schemas/` folder into one directory and invoke `mitosis.py` by path. The
modules import one another and `handoff.py` loads `contracts/validator.py` and
the schemas in both folders beside it, so they must stay side by side.

## A worked invocation

```
python3 mitosis.py \
  --items items.json \
  --charter CHARTER.md \
  --feature-branch main \
  --dispatch-command "claude -p {task} --model {model} --session-id {session_id} --output-format json --json-schema {schema} --permission-mode bypassPermissions" \
  --reask-command "claude -p {prompt} --resume {session_id} --model {model} --output-format json --json-schema {schema} --permission-mode bypassPermissions" \
  --acceptance-command "python3 -m unittest {test}" \
  --pr-command "open-pr --branch {branch} --base {base} --title {title} --body {body}" \
  --pr-type feat \
  --pr-scope manifest \
  --pr-subject "validate manifests from the command line" \
  --pr-why "A bad manifest should fail before it ships." \
  --pr-impact "A release with a bad manifest now stops at validation." \
  --tier-model top=model-a \
  --tier-model cheap=model-b
```

mitosis composes no pull-request title or body. At ship it writes each MSP's
facts to a facts file, runs pull-request render --fields on it, and uses the
title and body render printed unchanged; when render refuses the facts, that
MSP ends `ship-failed` with render's problem as its reason, and the facts are
never reworded and retried. The title is `<type>(<scope>): <subject>` from
`--pr-type`, `--pr-scope` and `--pr-subject`. Render writes the body from
mitosis's facts: Problem from the `--pr-why` lines; Change, one entry per
Step from its task's first sentence; Impact from the `--pr-impact` lines, or
the `--pr-blast-radius` sentence when none is given; Testing, one row per
acceptance test saying what the gate proved, with a Not run row and its
reason for each test left unproven; and Trade-offs and open problems, for
what reconcile, stacking or the build found. `--pr-door` is still accepted
and no longer reaches the body.

Every run that names `--pr-type` and `--pr-subject`, `--plan-only` and
`--no-push` included, needs `--pr-impact` or `--pr-blast-radius`, and runs
render on each MSP's facts before any Worker is dispatched, so a missing
pull-request command, one older than 0.3.0 or a refusal stops the run before
a build is paid for.

Every Lane is one Worker session, which builds the Lane's Steps in Step order
on its MSP's branch, and lands one commit with the subject
`<type>(<scope>): <steps>`, from `--pr-type` and `--pr-scope`, or `chore`
with no scope when the run names none, its Steps' names joined by commas,
and each Step's task's first sentence as a line of the body. Steps share a
Lane exactly when they share a file, so the branch's history is one commit
per Lane.

Leave out `--pr-command` and mitosis pushes each branch, opens nothing, ends each such MSP `pushed`, and records
the title and body in the run's state and in its JSON summary, so the caller
opens each pull request itself:

```
python3 mitosis.py \
  --items items.json \
  --charter CHARTER.md \
  --feature-branch main \
  --dispatch-command "claude -p {task} --model {model} --session-id {session_id} --output-format json --json-schema {schema} --permission-mode bypassPermissions" \
  --reask-command "claude -p {prompt} --resume {session_id} --model {model} --output-format json --json-schema {schema} --permission-mode bypassPermissions" \
  --acceptance-command "python3 -m unittest {test}" \
  --pr-type feat \
  --pr-subject "validate manifests from the command line" \
  --pr-why "A bad manifest should fail before it ships." \
  --pr-impact "A release with a bad manifest now stops at validation." \
  --tier-model top=model-a \
  --tier-model cheap=model-b
```

Run `python3 mitosis.py --help` for every flag; this is one path through it,
not the full surface. These flags shape how the plan splits the work, where a
run keeps its state, how Workers are confined and how the gate judges a
property:

| Flag | What it does |
|---|---|
| `--shared-files LIST` | comma-separated files that MSPs may each edit without being fused; an entry matches a path equal to it, a path ending in a slash and the entry, or a path it matches as a case-sensitive glob; an overlap only on such a file makes no MSP, Lane edge or scheduling conflict, is listed in the coupling review, and the plan records the order to integrate each file two MSPs edit; a Worker's write into a shared file another MSP declares is reported and is not fatal |
| `--worktree-root PATH` | places each MSP's worktree in a folder named after its slug under PATH, read against the repository root, in place of the run directory's trees folder; a second live run on the same branch is refused with exit 5, and a small record in the repository's git folder lets a later run take the branch from a stopped tree; a PATH that cannot be created, or a `--worker-write` path that is, holds or lies inside it, refuses the run with exit 3 before any Worker starts |
| `--no-run-dir` | keeps the plan, state, logs and clones in a private temp folder removed before mitosis exits, whether the run shipped, failed or was refused; `--run-dir` is ignored, `--resume` starts from nothing, each Worker's standard output is printed before the report, and the result line carries no run directory; the branches and worktrees stay, at `--worktree-root` or where a run of the same input keeps them |
| `--worker-sandbox` | runs every Worker under sandbox-exec, refusing file writes outside its clone, a private temp folder, its session transcript folder, /dev/null, /dev/tty, /dev/fd and each `--worker-write` path; it also runs the gate's setup, acceptance, mutant and suite runs and the lock stage's checks under sandbox-exec, refusing writes outside their clean checkout, a private temp folder and each `--worker-write` path, so a `--gate-setup` that needs another writable path names it with `--worker-write`; a host without sandbox-exec refuses the run with exit 3 before any Worker starts |
| `--worker-write PATH` | one more path a sandboxed Worker, gate run or lock-stage check may write under; repeatable; a path that is, holds or lies inside the run directory, the repository or its git directory refuses the run with exit 3 |
| `--suite-command TEMPLATE` | deprecated and ignored; a surviving mutant is reported and holds nothing |
| `--brief-command TEMPLATE` | deprecated and ignored; the split writes each Step's task, so no brief call runs |
| `--gate-classes PATH` | a JSON list of `{test, class, reason}` objects classing a property `not-applicable`, covered-elsewhere or `removed`, judged without a run and kept out of the plan id |

## Model replies and owned hand-offs

Every model call is a command template, and each reply has an owned schema
in `contracts/`. `--dispatch-command` and `--decompose-command` must each
carry `{schema}`, the call schema the reply must
meet as structured output, as JSON text, and `{session_id}`, the session a
re-ask resumes; a template missing either is refused with exit 3 before
anything is built. Any run that calls a model also needs `--reask-command`,
offered `{prompt}`, `{session_id}`, `{schema}` and `{model}`, and refused
with exit 3 when it is absent or lacks one of the first three.

mitosis reads each reply only from the structured_output field of the JSON
object the command prints, never from prose. A reply that is missing or fails
its owned schema is asked again once, through `--reask-command`, in the same
session, with the schema errors as `{prompt}`. A reply that still fails ends
that Worker's Lane, or that structure call, as failed, naming the
errors; it is never defaulted, skipped, coerced or cut short. With Claude
Code, the two templates are:

```
claude -p {task} --model {model} --session-id {session_id} --output-format json --json-schema {schema} --permission-mode bypassPermissions
claude -p {prompt} --resume {session_id} --model {model} --output-format json --json-schema {schema} --permission-mode bypassPermissions
```

The structure template takes `{prompt}` in place of `{task}`, or reads the
prompt from stdin.

Every hand-off mitosis writes is checked against its owned schema in
`contracts/` immediately before it is written: h8 for the result line, h9 for
the run state, k2 for the lock record, h15 for each pull request's facts file
and k6 for each `--trajectory` outcome entry. Render's reply is read through
h16, and Worker and structure replies meet h10 and h10b. A run
state, result line or lock record that fails stops the run with exit 15, its
schema errors named and nothing written for it. A facts file that fails is a
render refusal, so its MSP ends `ship-failed` with the errors as its reason,
and outcome entries that fail are not appended; the MSP records the errors as
its trajectory error. A pull request is recorded only as
the http or https URL the pull-request command prints on its last line; any
other last line ends that MSP `ship-failed`.

## Workers build in their own clones

Each Lane's Worker builds in its own clone of its MSP branch, under the run
directory's `clones` folder, with its own git directory and object store and
no remote. The MSP worktree is mitosis's own checkout, where no Worker runs,
so no Worker can reach the refs, settings or objects mitosis reads. When the
Worker exits, mitosis reads the Lane's write-set from the clone's files,
without running git in the clone, writes those paths into its checkout and
commits them as the Lane's landing; a write-set path behind a symbolic-link
folder fails the Lane. A file changed outside the write-set is replayed into
mitosis's checkout as an uncommitted change and judged by reconcile alone:
an undeclared write is kept and reported and does not set the run's exit code, and a
referee write is reverted and exits 9. The gate and every acceptance run see such a path as it stood when
the Lane started, so an uncommitted helper never makes a test pass. A Lane
does not see a sibling's uncommitted writes.

Without `--worker-sandbox` the clone keeps git state apart, but a Worker can
still write any path the caller's account can; with it, the JSON result's
`sandbox` reads sandbox-exec instead of `off`. The sandbox also wraps every
later command that runs Worker-written code: the gate's `--gate-setup`,
acceptance and mutant runs, and the lock stage's checks.
Each may write only the clean checkout it runs in, `gate/msp-<n>/checkout-*`
or `lock/msp-<n>/checkout-*` under the run directory, a private temp folder beside
its log folder, `gate/msp-<n>.tmp` or `lock/msp-<n>.tmp`, passed as TMPDIR and
holding the line traces and suite reports, and each `--worker-write` path; a
`--gate-setup` that writes anywhere else names that path with
`--worker-write`. mitosis's own logs and state stay outside every write
grant, and no sandboxed command may rename, remove or replace the clone,
checkout, temp or transcript folder it writes into, only fill it, so a
symbolic link it leaves never redirects a later write by mitosis. A
`--worker-write` path that is, holds or lies inside the run directory, the
repository's working tree, its git directory as `git rev-parse
--git-common-dir` reports it (the main checkout's, for a linked worktree), or
a folder mitosis places MSP worktrees in, compared by resolved real path,
refuses the run with exit 3 before any Worker or model call, naming the one it
overlaps. When a Worker, gate or lock-stage command exits, mitosis ends
its process group and, under `--worker-sandbox`, every process left in that
run's sandbox before reading anything the run could change, and keeps ending
them until none is left. Every git call mitosis makes
drops the environment variables that shape a diff, widen a pathspec, locate
another repository or point at other objects.

## Warm Worker clones

A Worker's clone holds no gitignored file, so it starts cold: no virtual
environment, no build cache, no downloaded native library. A project opts in
by writing a `.worktreeinclude` at its repository root, in gitignore syntax,
the same file worktrunk and Claude Code read. mitosis copies each listed path
that the repository ignores and does not track into every Lane clone and the
check-author's clone, by copy-on-write only (`clonefile` on macOS APFS,
`cp --reflink=always` on Linux), never a plain copy, a hard link or a symbolic
link.

```
.dart_tool/
third_party/llamadart-native/
```

A Python virtual environment, a secret-looking name (`.env`, `.env.*`,
`*.pem`, `*.key`, `*.p12`, `*.pfx`, id_rsa, id_dsa, id_ecdsa,
id_ed25519, `.npmrc`, `.netrc`, `.pgpass`, `.envrc`, `.git-credentials`,
`*.jks`, `*.keystore`, `key.properties`) and a copy holding a symbolic link
that leaves the clone are refused, and so is any path inside a virtual
environment. A listed folder that holds a secret-looking name or a virtual
environment anywhere inside is copied without it, its other contents one by
one. A path that cannot be copied leaves the
Lane cold, as without the file; it never stops the Lane. Seeded paths never
land and never reach the gate's clean checkout. Each Lane record and the lock
record list `seeded`, seed_skipped and seed_seconds. With no
`.worktreeinclude`, nothing changes.

## Check-author Steps an items file declares

mitosis never writes a check-author Step itself, and a structure reply whose
Step carries a `role` is asked again in the same session. An `--items` file
may still declare one, and mitosis folds it: each Step whose acceptance names
a file the check-author writes writes that file with its work, the
check-author's `after` edges go, a file no Step's acceptance names is dropped
with a notice, and no lock stage runs. Only a saved plan given by
`--plan-file` that was planned with check-authors keeps them, and they run as
the lock stage below. A plan made from folded items records the check-author
Steps it folded under `folded_check_authors`, and a saved plan has its items
folded the same way only when that record names exactly the items'
check-author Steps; one with neither that record nor check-authors is refused
as a plan that lost its check-authors.

A Step with `"role": "check-author"` writes the locked acceptance checks and
builds nothing. It never becomes a Lane, an MSP or a pull request, and its
name leaves every other Step's `after` list. A check-author named exactly
check-author is the unlabelled one whatever its `msp` says; earlier interphase
releases render it with no `msp` or with that name as its `msp`. It is split by
acceptance ownership, each locked file going to the MSP whose Steps' acceptance
entries name it. Any other check-author whose `msp` names an MSP belongs to
that MSP. The plan
refuses a label that names no MSP, two check-authors with one label, a locked
file two MSPs' acceptance entries name, a locked file no acceptance names, a
locked file two check-authors write, and a Step whose acceptance names a file
another MSP's check-author writes.

Each MSP's check-author locks only that MSP's tests, as the first commit of
that MSP's branch. Before any Lane is dispatched, each MSP whose locked files
the feature branch lacks runs its check-author in its own clone at the feature
base, up to `--concurrency` at once. mitosis judges each locked test against
the check-author's probe: it must exit 1 at the feature base plus the locked
files, and exit 0 once the check-author's probe writes are added, the clone's
writes inside the files of that MSP's Steps and of every MSP it merges. A test
that exits 0 at the feature base counts as a guard of behaviour that must stay
the same when another locked test of its Step exits 1 there. A test that fails
with the probe or cannot run refuses the lock, naming it, and so does every
test of a Step with no test that exits 1. The probe is never committed; mitosis commits the locked files
alone onto the feature base as `test: lock the acceptance checks`, cuts that
MSP's branch and worktree from that commit, and never moves the feature
branch or pushes at lock time, so the lock commit reaches the remote with the
MSP's branch at ship and its pull request carries its own tests. An MSP whose
locked files the feature branch already holds is locked there with no
check-author, so checks locked by hand keep working.

Each MSP's lock record is its `lock` object in the JSON result's `msps` list,
which replaces the top-level `lock` of earlier releases. A failed lock blocks
only its MSP and the MSPs that merge it, directly or through another, and
every other MSP is built, gated and finished. The gate and reconcile of an MSP
judge against its own lock commit, and its referee covers the locked files of
its own lock and of every MSP it merges.

## Holding and resuming a run

`--no-push` builds, gates and reconciles every MSP and publishes nothing;
`--resume` with the same input and run directory continues a run, and
without `--no-push` ships what was held. A resume reuses an MSP's recorded
gate result when it passed and the MSP's HEAD is the commit the gate ran at;
otherwise the MSP is gated again. Files the worktree holds outside that commit
play no part, since the gate never reads them.

A caller that proved a later head itself names it with
`--proven-head <MSP label>=<commit>`, once per MSP. mitosis ships that head
without gating again when the MSP's HEAD equals the commit and a passing
gate result is recorded, and the pull request's Testing section gets a
Proven head row, marked Not run, naming both commits. A head that
differs, or an MSP with no recorded gate result, is gated as before; a
malformed value or a label the plan does not hold exits 2 before anything is
built.

An MSP already `pushed` or `shipped` stays as it is on a resume, unless a
resume that pushes names a `--proven-head` other than the commit mitosis
pushed. Then mitosis ships that MSP again: it pushes the branch to the new
head as a fast-forward and renders the title and body again for it, and an
MSP whose pull request mitosis opened keeps that pull request, with the
pull-request command not run again, so the caller applies the new body. A
head that does not descend from the pushed commit is never force-pushed: the
MSP ends `ship-failed`, with exit 22, and its record keeps the earlier push.

A gate that is interrupted leaves the MSP's worktree as it was, because
the gate never runs there; the next gate removes the gate folder left behind.

## The referee and the gate

The referee is the set of files that decide whether the work is done, and no
Worker may write it. A charter names its locked checks one per line, as
`- Locked: tests/test_search.py`, and mitosis adds built-in referee markers
for test configuration such as `conftest.py` or `pytest.ini`, snapshots,
baselines, allowlists and skip lists. Planning refuses a Step whose files
hold a referee path, with exit 3, before anything is built. A Worker that
writes one anyway has the file restored from the feature branch, and its MSP
is held back as `referee-reverted` and never pushed, with exit 9. mitosis
finds such a write after the MSP's pre-gate commit, and also when that commit
fails, by comparing the feature branch's tree with the branch head's tree
through git's tree objects, with replace objects, system and global config,
attributes and filters kept out, so no Worker-written setting shapes what it
reads. The revert is a commit before the gate. The referee and the gate
judge one commit, the MSP branch's, so a worktree HEAD that is detached, on
another branch or at another commit is fatal too, with exit 9, and the gate
does not run. The JSON
result's `referee` object names the locked paths, their receipt lock and the
commit they were read at. A real run refuses, with exit 3, a charter whose
`- Locked:` path the feature branch does not hold, and a `- Locked:` line that
is empty, absolute or contains `..`.

The gate runs each acceptance property with the work present and with the
implementation reverted, and a property that exits 1 with the work present is
the one verdict that holds its MSP. mitosis first gives the Lane that owns the
failing Step one repair, with the test's name and output, and judges again. A
test still failing after that ends the MSP `gate-failed`, with exit 21: its work
is committed on its branch and never pushed. A property the acceptance command
cannot run with the work present, exiting with neither 0 nor 1, gets the same
one repair, told that the command is given the test file and the test's name
separately; when it still cannot run, its MSP ships with it `inconclusive`.

Every other verdict ships and is named in the pull request's Testing table.
`pass` is a property that failed without the work, `inert` one that passed
without it, and `inconclusive` one the acceptance command could not judge. A
gate that could not run ships the same way, with a `Not run` row.

A passing property then runs against up to 12 mutants of its Step's added
lines, each one a small change to an added line, such as a flipped comparison,
a swapped operator, a flipped true or false, a number shifted by one or a
replaced return value. Files in a test location are never mutated. Strings and
comments are left alone, and when no operator applies to any added line the
gate drops the added lines of each file instead. For a Python Step the gate
also tries negating each `if` that holds added code inside a function and
emptying each function the Step added. A property no mutant fails is judged
`weak`: reviewers see it in the Testing table and it holds nothing. An
`inert` property runs against the mutants too, and one a mutant fails passes,
because it guards behaviour the work must keep; the Testing table reports it
as a guard, and its Step's other properties that pass either way are guards
beside it. A `weak` or `inert` property
that its Lane wrote and no lock holds goes back to that Lane once, told the
surviving mutant or that the test passes without the work, and the MSP is
gated again; after that it ships as before.

The with-work run of each property records, through a standard-library
tracer first on `PYTHONPATH`, the lines it executed, and the gate samples its
mutants only from added lines executed inside a function and added
assignments an executed function reads. Each property in the JSON result
carries two fields. mutation is killed when a mutant failed it, `weak`
when none did, `not-applicable` when the test loads none of the Step's
Python files or the Step added no code file for a mutant to change, and
covered-elsewhere when a `--gate-classes` entry classed it so.
`coverage` is `traced` only when the sampled lines came from
the trace, and `untraced` whenever they did not: when the run wrote no trace,
when only JavaScript, TypeScript or shell lines were sampled, or when the gate
fell back to every added line. A test that
loads the Step's files but executes no candidate line also falls back to
every added line, so an import-only test cannot pass unseen.

`--gate-classes PATH` excuses a property without editing the Steps: each
`{test, class, reason}` entry names a property as `<file>::<name>` and
classes it `not-applicable`, covered-elsewhere or `removed`, with a
one-line reason. A classed property is judged by its class without a run.
The classes stay out of the plan id and are stored in the run state with
their SHA-256, so `--resume` keeps every built Lane and each unclassed
property reuses the verdict recorded at the MSP's unchanged commit. An entry
naming no property of the plan is refused.

The gate, every mutant run and every acceptance run execute in a clean
checkout of the MSP's committed tree, a folder outside the MSP's worktree
written from git's tree objects and passed as `{worktree}`. The folder is
reset to its target tree before every run and removed after the gate. It is
not a git repository and holds the commit's raw blob bytes. Files the commit
does not carry never reach the gate: each MSP's `ignored` list in the JSON
result names them, and mitosis never restores, removes or judges them.

The folder is reset to the commit's raw blob bytes before every acceptance
and mutant run, while `--gate-setup` runs once per gate, so what setup writes
survives that reset only at top-level names the commit lacks, such as the
`.git` folder a setup's `git init` makes or a new top-level build folder. An
acceptance command or test that runs git needs `--gate-setup`, which can make
`.git` at the top level. A top-level file the setup makes at a referee path,
such as `conftest.py`, and a top-level folder whose name is a referee folder or
holds a `- Locked:` path, such as `__snapshots__` or `.receipts`, is a referee
write, fatal with exit 9; mitosis judges that top-level name and never walks
into a kept folder such as node_modules. A Git LFS file
arrives as its pointer text, and a committed eol attribute is not applied in
every run, whatever `--gate-setup` does. An acceptance test that reads the
real content fails, and holds the MSP back as `gate-failed`, with exit 21, when
the acceptance command exits 1. The gate cannot tell pointer text from real content, so a property
that holds on pointer text is judged on pointer text.

Dependency folders come from outside the Worker's control. Without
`--gate-setup`, the gate links in each node_modules folder and each
virtual-environment folder, one holding `pyvenv.cfg`, that the repository
root holds and the commit lacks. With `--gate-setup`, the gate links nothing
and runs that command once per gate in the clean checkout, with `{worktree}`
naming it; a project whose root folders point back
into the root checkout, such as an editable install, uses it. A gate setup
that exits non-zero runs no acceptance test, records a gate error naming the
gate setup, and holds nothing.
A pre-gate commit that git refuses holds the MSP back as `gate-failed`, with
exit 21, its work left uncommitted in the worktree and no gate run.

A Lane commits without the repository's hooks, because a Lane is a partial tree.
The pre-commit hook runs once per MSP, over the whole change as staged on its
base, in a throwaway checkout, so it never moves the branch and whatever it
edits or stages is discarded. Under `--worker-sandbox` it runs confined like the
gate and can write nothing under the repository's `.git`, and every other
repository hook and fsmonitor are off, because Worker-written code could sit
behind any of them. A clean or smudge filter the repository configures to a
tracked program still runs outside the sandbox. A hook that refuses the change
holds the MSP back as `gate-failed`, with exit 21.

An acceptance run that leaves a referee path in the clean checkout differing
from the commit is caught the same way as a committed write: the gate lists
it under its `referee` list, and the MSP is held back as `referee-reverted`
and never pushed, with exit 9.

A Lane whose Worker fails is dispatched once more, told why, and told it may
change a file outside its write-set when that was the obstacle; mitosis keeps
that write, shows it to the gate and reports it. A Worker that reports failure twice leaves its Lane
`failed` and its MSP unshipped, while every other MSP carries on. A check-author
whose lock is refused gets one retry too: the same session continues in its
clone, told what was refused, and it starts again only when that session
cannot continue. A lock refused twice no longer
stops the build: the MSP builds from its base, its Steps' locked tests are judged
`not-applicable` with the refusal as the reason, and the pull request says so.

A repair lands the way a first attempt does: a write to another Lane's file is
reported, a file a sibling had overwritten reaches the gate again, and an MSP
built on the repaired one merges the repair before its own gate. A repair that
stops at a usage limit parks the MSP with exit 13, and `--resume` finishes it.

A base submodule whose gitlink the branch changed and whose commits cannot be
read holds the MSP back as `gate-failed`, with exit 21, and a gate error
saying the submodule cannot be judged. A committed referee write inside a base
submodule is reverted by resetting the submodule's gitlink to the base's, and
holds the MSP back as `referee-reverted`, with exit 9.

Ship refuses an MSP whose branch head is not the gated commit unless
`--proven-head` names that head; the refusal ends the MSP `ship-failed`, with
exit 22, and nothing is pushed.

`--trajectory PATH` reads prior outcomes to raise a surface's tier, and after
every gate also receives one outcome entry per acceptance test, appended to
that file.

## No wall-clock limit

`--timeout` is deprecated and ignored, mitosis has no wall-clock limit. A run
given it prints one stderr line saying so and runs as it would without it, so
a script written before this release keeps starting. A Worker, a
check-author, an acceptance run, a gate setup and a git command each run until
they exit, and a command's exit, not a timer, ends every wait; telling a hung
run from a slow one is the caller's, by watching for progress. Every process
mitosis starts reads the null device as its input, or a fixed input mitosis
writes in full and closes, and runs with `GIT_TERMINAL_PROMPT=0`,
`GH_PROMPT_DISABLED=1`, `GIT_PAGER=cat` and `PAGER=cat`. Its ssh is the
caller's own `GIT_SSH_COMMAND`, the repository's `core.sshCommand`, the
caller's `GIT_SSH` or ssh, followed by `-o BatchMode=yes -o
ServerAliveInterval=15 -o ServerAliveCountMax=4`, and `http.lowSpeedLimit=1`
with `http.lowSpeedTime=60` follow any git configuration the caller set, so
git's dead-peer detection ends a connection whose peer went away instead of
waiting on it.

A Worker that stops at a usage limit leaves its Lane `parked-limit`; Lanes
not yet dispatched park with it, and their MSPs end `parked`, with exit 13.
After the limit resets, `--resume` runs every Lane that is not `ok`.

The JSON result's `findings` list carries one entry per problem the run
found, each naming its MSP, its Lane, its kind, its exit code and a detail,
and the exit code is the most severe finding's, an undeclared write aside,
which is reported with exit 6 and sets no exit code.

## Metrics

mitosis records metrics about its own work in the metrics store on this
machine, the folder `~/.plugin-metrics`, or the folder `PLUGIN_METRICS_DIR`
names when it is set. Each invocation of the mitosis command is one run, from
the moment its run directory is known to its exit, so a plan, a build, a
resume and a ship are four runs, joined by their `plan_id` and `run_dir`. A
run appends events at the boundaries of its work and replaces one run summary
holding how the work split, how parallel the Lanes ran, how the gates came
out, how each MSP ended and the run's own spend. The store holds run metadata
only, never a task, a brief, a Worker's output or file contents, and a
metrics write that fails prints one line on stderr without changing the exit
code or the last JSON line.

mitosis gives every Worker dispatch and structure call a fresh session id,
offered as the `{session_id}` placeholder on the dispatch and decompose
templates, each of which must carry it. The model
command names that call's transcript by it, and mitosis prices the call from
that transcript when the summary is written:

```
claude -p {task} --model {model} --session-id {session_id} --output-format json --json-schema {schema} --permission-mode bypassPermissions
```

mitosis gives `PLUGIN_METRICS_PARENT`,
set to `mitosis/<run id>`, to every command it starts, so a run another tool
records from inside one of them names its parent, is listed among the run's
children, and keeps its spend apart from mitosis's own.

`catalogue.json` describes every event and field mitosis writes and carries
named queries, and is copied into the store. The plugin's metrics skill,
`skills/metrics/SKILL.md`, answers how mitosis is performing from those
records through the duckdb command. `docs/metrics-format.md` describes the
record format.

## Tests

```
python3 -m unittest discover tests
```

The tests ship with the plugin, so an installed copy can verify itself
wherever it lands.

## Releasing

A pull request that changes the modules, the skill or the plugin manifest
must raise the version in `core.py` and `.claude-plugin/plugin.json` together
and add its heading to `CHANGELOG.md`; the tests and continuous integration
refuse it otherwise. Versions stay below 1.0.0 until mitosis is released.
Merging to `main` publishes the release, and continuous integration then tags
the merge `mitosis--v<version>`. Keep a branch up to date with `main` before
merging it, because a check that passed against an older `main` is not rerun.

Continuous integration also refuses published text, including a pull
request's title and body, that contains a term listed in the `DENY_TERMS`
repository secret. A pull request from a fork cannot read that secret, so its
check fails until the branch is pushed here.

## More

For when an agent should reach for mitosis and how to invoke it, read
`skills/mitosis/SKILL.md`.
