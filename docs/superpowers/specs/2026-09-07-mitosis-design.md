# mitosis — design specification

Date: 2026-09-07
Status: approved for planning

This document supersedes every earlier mitosis design document. Any previously
archived report, decision, or specification is void. Nothing in this document
depends on them.

---

## 1. What mitosis is

mitosis is a Claude Code plugin.

It takes one document describing work to be done, splits that work into pieces
that can be built at the same time, builds each piece, and opens a pull request
for each one.

It stops there. A human reviews and merges.

### 1.1 The single responsibility

**A specification goes in. Shipped pull requests come out.**

That is the whole job. mitosis does not merge anything, and it does not ship the
feature branch. Both are somebody else's work.

### 1.2 What "shipped" means

An MSP is shipped when all three are true:

1. Its pull request is open.
2. Continuous integration is green on that pull request.
3. Review has asserted every acceptance property in its plan, and all of them
   hold.

Nothing further happens to it. It waits for a human.

---

## 2. Vocabulary

Every term used in this document, defined once.

| Term | Meaning |
|---|---|
| **SPEC** | The input document. A human wrote it. It describes work to be done. It is frozen the moment mitosis receives it. |
| **MSP** | Minimum Shippable Product. One piece of work small enough to be its own pull request, and complete enough that merging it does not break the branch it lands on. |
| **Cluster** | A group of MSPs that must be built one after another. Clusters are eligible to run at the same time as each other, bounded by the concurrent-cluster cap in section 10. |
| **Write-set** | The list of files an MSP is expected to change. |
| **Invariant** | A statement that must be true of what one phase produced. Fixed in this document, identical on every run. Defined in full in section 5.1. |
| **Acceptance property** | A statement about what one MSP's finished work must do, true when the work was done correctly and false when it was not. It describes an outcome, never a step toward one, and never the code that produces it. Written by that MSP's planner. Defined in full in section 5.1. |
| **Step** | One item of work in a plan. Steps say what to do. Acceptance properties say what must then be true. |
| **Assertion** | The act of checking an invariant or an acceptance property against the real artifact and affirming it. Where the check can be code, it is code. Where it is a judgment, a model answers it and shows the evidence. |
| **Assertion record** | What a phase emits alongside its product: one entry per invariant in that phase's set, each carrying a verdict and the evidence behind it. mitosis does not start the next phase until the record is complete and every entry is affirmed. The terminal report is not a phase, but emits one too, for T1 to T5, printed inside itself. |
| **Verdict** | Phase 6's pass or fail on one MSP, resting on its acceptance properties and nothing else. |
| **Planner** | The agent in Phase 4. Reads the SPEC and one MSP, writes the plan. Writes no code and no test. |
| **Worker** | The agent in Phase 5. Reads the plan, writes the code, keeps the build green. Never reads the SPEC, and never writes an acceptance assertion. |
| **Reviewer** | The agent in Phase 6. Reads the plan and the finished branch, writes its own assertions, returns a verdict. Never reads the SPEC, and never adopts the worker's tests. |
| **Worktree** | A separate checked-out copy of the repository. Each MSP gets its own, so no two workers can see or overwrite each other's files. |
| **Feature branch** | The shared branch that every MSP's pull request eventually targets. |
| **Stacked pull request** | A pull request whose base is another open pull request's branch, rather than the feature branch. |
| **Terminal report** | The output mitosis prints when the run ends. |

---

## 3. Scope

### 3.1 In scope

- Reading a SPEC in whatever form its author wrote it.
- Splitting the work into MSPs and clusters.
- Planning each MSP.
- Implementing each MSP.
- Driving each MSP's continuous integration to green.
- Reviewing each MSP's finished work against its plan.
- Opening one pull request per MSP.
- Reporting what happened.

### 3.2 Out of scope

- **Merging any pull request.** A human merges. mitosis never does.
- **Shipping the feature branch.** A separate, future shipping adapter owns this.
- **Requiring a particular SPEC format.** mitosis reads what you already wrote.
- **Judging whether the SPEC is good enough.** mitosis never asks the author to
  clarify, expand, or improve the document. It ships the one it was given.
- **A saved run-state file.** The repository itself is the record.
- **Re-checking anything against the SPEC after Phase 4.** Phase 1 reads it to
  freeze it and to recognise already-shipped work, Phase 2 reads it to split it,
  and Phase 4 reads it to plan one MSP. No phase after Phase 4 reads it at all. A
  later phase that reached back would be compensating for a planning failure
  instead of surfacing it.
- **Parallelising the tasks inside a single MSP.** Clusters are eligible to run
  at the same time as each other; the steps within one MSP run in one worktree, in
  order. Task-level parallelism is not part of this design.
- **Deciding whether finished work is good enough to merge.** Phase 6 reviews
  the work against the plan; whether it is good enough to merge is the human's
  call at the pull request.

---

## 4. The pipeline

Seven phases, in order. The mechanisms they rely on — invariants, acceptance
properties, failure handling, and the report — are specified in sections 5
through 7.

Every phase asserts a fixed set of invariants about its own output before the
next phase begins, and the terminal report asserts its own set as it is written.
Each set is listed with the thing it governs, and they all ask one question: did
this stage carry forward everything it was given, and is its output honest about
itself?

**A phase asserts its own invariants, and the first response to a failure is more
work, not an ending.** The set is that phase's exit condition, not a gate somebody
else holds. Stopping is still possible — 5.3 specifies when — but it is never the
first response. This is specified in 5.3 and 5.4, and the reasoning is decision
8.10.

### Phase 1 — Intake

**Input:** a path to a SPEC file.

**What happens:**

1. Read the SPEC. From this moment it is frozen and is never modified.
2. Read the repository: current branch, existing branches, open pull requests.
3. Create the feature branch if it does not already exist, named from the SPEC file.

**mitosis does not review the SPEC.** It does not judge whether the document is
clear, complete, or specific enough, and it never asks the author to improve it.
A vague SPEC produces a vague result, exactly as it would if a human implemented
it by hand. Writing a good SPEC is the author's job; shipping the one they wrote
is mitosis's.

**Where ambiguity goes instead:** it is interpreted, recorded, and reported.
Every place a reading was chosen rather than followed literally lands in the
terminal report's Assumptions section, specified in section 7. Nothing merges
before a human reads it.

**Preconditions are not SPEC review.** If the SPEC path does not resolve, or the
repository is in a state where a run cannot mean anything, mitosis refuses to
start. That is a check on the environment, never a judgment about the document.

**Invariants.**

| # | The statement that must be true | How it is asserted |
|---|---|---|
| **I1 — The freeze was taken** | The SPEC text handed to every later phase is byte-identical to the file this phase read. | Mechanically, by hash, at the end of this phase. |
| **I2 — Prior work identified** | Every open pull request belonging to this SPEC's run — one whose base is the feature branch, or whose base is another MSP branch in a stack rooted on it — is either matched to a specific MSP with a stated reason, or explicitly marked unrecognized. | A model reads each such open pull request against the SPEC. On a first run the branch has just been created and there are none, which satisfies this trivially. |

**Why I2's two errors are not equally dangerous.** Failing to match a pull request
means an MSP is built a second time, which is loud, because it conflicts. Matching
the wrong one means an MSP is treated as finished and never built at all, which is
silent. A match that cannot be justified is therefore recorded as unrecognized:
building something twice is recoverable, and skipping it is not.

**Why the other half of this check is not here.** Someone may edit the SPEC while
a long run is in progress. Behavior stays correct, because the frozen copy is what
was used, but the human needs to be told that their document and the run have
diverged.

That comparison can only be made once the run is over, which is nowhere near this
phase's assertion record. A statement is asserted at the end of the phase that
owns it, so a statement with no moment to be asked in has no owner and nothing
forces it. It belongs to the terminal report instead, as T5.

**Output:** a frozen SPEC, the repository state, and the list of work already
shipped by an earlier run.

---

### Phase 2 — Decompose

**Input:** the frozen SPEC, the codebase, the list of already-shipped work.

**What happens:** one model pass reads the SPEC and the actual codebase, and
emits the list of MSPs. For each MSP it produces:

| Field | What it is |
|---|---|
| `id` | A short stable name for this MSP. |
| `brief` | What this piece of work is, in a few sentences. Enough for a planner, not enough to build from. |
| `writes` | The files this MSP is expected to change. |
| `needs` | The ids of MSPs that must be finished before this one starts. |

**What reading the codebase means:** the model is not given the repository as
text. It explores it with ordinary file tools — list a directory, search for a
string, open a file — the way a new engineer would on their first morning. It
reads the project's conventions, opens the files the SPEC appears to touch, and
looks at how work of this kind is already shaped here.

Four things depend on it:

| What it gets | Why the decomposition needs it |
|---|---|
| What already exists | "Add rate limiting" is one MSP in a project that already has a middleware layer and two in a project that does not. The same sentence decomposes differently in different repositories. |
| How big a piece is | An MSP must be small enough to review as one pull request and complete enough not to break the branch. Neither can be judged without knowing whether the change touches two files or forty. |
| Real file paths | Phase 3 decides what may run in parallel purely by checking whether two write-sets overlap. Invented paths make that check compare fiction against fiction, and two MSPs that should have been ordered run at the same time. |
| What is already done | On a re-run, the branches and open pull requests say which MSPs finished last time. |

**Why a model does this and not the SPEC author:** requiring the author to
hand-write this list means teaching every author a format before they can use the
tool. That is the problem this design exists to remove. The model reads the
codebase at decompose time, which a human writing a list in advance usually does
not.

**Why `brief` is short:** neither the worker nor the reviewer ever reads it. Only
the planner does, and the planner also has the whole SPEC. The brief has one job:
identify which slice of the SPEC this MSP covers.

**Excluding finished work:** MSPs whose pull request already exists are not
emitted. The decompose pass is told what is already shipped and plans only the
remainder. D1, below, is written to cover this: a part of the SPEC satisfied by
already-shipped work counts as covered exactly as one covered by an MSP in this
list, so excluding it here never reopens what D1 already closed.

**Invariants.** Six statements, fixed and identical on every run, asserted
against the finished MSP list before Phase 3 begins. Section 5 defines what an
invariant is and how one is asserted.

| # | The statement that must be true | How it is asserted |
|---|---|---|
| **D1 — Coverage** | Every piece of work the SPEC asks for is covered by at least one MSP — one in this list, or one already shipped by an earlier run. | A model reads the SPEC, the MSP list, and the already-shipped work, and builds the mapping: each part of the SPEC against what covers it, of either kind. A part mapped to nothing means the split is incomplete, so the pass adds the missing MSP and asserts again. A part mapped only to already-shipped work is marked as such, and no MSP is emitted for it. |
| **D2 — No invention** | No MSP proposes work the SPEC does not ask for. | The same mapping read the other way. An MSP mapping to nothing in the SPEC is removed. |
| **D3 — Shippability** | Each MSP, merged on its own onto its base, leaves that branch working. | A model judges each MSP against that definition. An MSP that only makes sense once a later one lands is not shippable, and the split is redrawn until it is. |
| **D4 — Grounded paths** | Every path in every `writes` either exists in the repository now, or is a file that MSP will create. | The existing case mechanically, against the repository. The create case as a model judgment. |
| **D5 — Dependency sufficiency** | If an MSP cannot be built until another MSP's work exists, that other MSP is named in its `needs`. | A model reads each MSP against the ones it could depend on. |
| **D6 — A valid order exists** | Every id named in a `needs` is an id in this list, and the graph has no cycle. | Mechanically. |

**The mapping is kept, not discarded.** What D1 and D2 produce goes into the
terminal report. A human holding the SPEC can then check the split in a couple of
minutes, instead of later failing to notice that a pull request nobody told them
to expect never arrived. This is what turns a missing MSP from an absence into a
line of text.

**Why D5 carries more weight than it looks:** a missing dependency edge puts two
MSPs in different clusters, so they run at the same time, and the second builds
against a base where the first one's work does not exist. A dependency edge that
should not be there costs only parallelism. A missing one costs correctness.

**Output:** a list of MSPs, and the coverage mapping.

---

### Phase 3 — Cluster and schedule

**Input:** the MSP list.

**What happens:** the schedule is pure computation. No model decides it, and the
same MSP list always produces the same clusters. A model is involved afterwards,
but only to assert C5 against the finished schedule; it never changes it.

1. Build a graph whose nodes are MSPs.
2. Draw an edge between two MSPs when **either** of these is true:
   - one declares the other in its `needs`, or
   - their write-sets share at least one file.
3. Each connected group of MSPs becomes one cluster.
4. Inside a cluster, order the MSPs by a topological sort on `needs` edges, with
   ties broken by ascending id in lexicographic byte order.

**The execution rule:**

- **Clusters are eligible to run at the same time as each other**, bounded by the
  concurrent-cluster cap in section 10.
- **MSPs inside a cluster run one after another.**

**Why file overlap creates an ordering edge:** this is the entire job of the
write-set. Two MSPs that touch the same file must not run at once — not because
the files would be corrupted, but because the second one needs to see the first
one's work. Two MSPs adding different routes to one file, run blindly in
parallel, can both register the same path and merge cleanly with a bug.

**Why this phase must be deterministic:** everything upstream is a model's
judgment. The schedule is the one thing that must be reproducible, so a given MSP
list always produces the same execution order.

**Why the order needs a tie-break.** A `needs` edge has a direction — one MSP
names the other — but a file-overlap edge does not: it forces two MSPs apart in
time without saying which comes first. Two MSPs joined only by a shared file would
otherwise admit either order, and the order is load-bearing: it decides which MSP
is first in its cluster, which decides that MSP's pull request base and, for the
first MSP in the cluster, the base of the whole stack.

**Why id, not list position.** Phase 2 is a model pass; it can emit the same set
of MSPs in a different order on two runs over the same SPEC without that being a
defect. Breaking ties on ascending id, in lexicographic byte order, is stable
under that reordering. Breaking them on the position an MSP happened to appear in
Phase 2's output would not be.

**A single cluster is a valid outcome.** If every MSP depends on the previous one,
mitosis runs them in sequence and reports parallelism as one. It does not refuse.

**Invariants.** These are asserted against the finished schedule rather than
against the algorithm, so they still mean the same thing if the clustering code is
ever rewritten.

No failure here is fixed by iterating, because this phase has no freedom: the
schedule is a pure function of the MSP list, so there is nothing it could do
differently. C1 to C4 failing means the clustering code is broken, which no agent
can repair, and the run stops. C5 failing means the MSP list is wrong, so the fix
is a `needs` edge back in Phase 2.

| # | The statement that must be true | How it is asserted |
|---|---|---|
| **C1 — Nothing lost or duplicated** | Every MSP appears in exactly one cluster. | Mechanically. |
| **C2 — Order matches the rule** | Inside a cluster, the order is exactly the one step 4's ordering rule produces: a topological sort on `needs` edges, ties broken by ascending id. | Mechanically. |
| **C3 — No cross-cluster file sharing** | No two MSPs in different clusters share a file. | Mechanically. |
| **C4 — The safety property** | Two MSPs run at the same time only if neither needs the other and they share no file. | Mechanically, against the schedule. |
| **C5 — No hidden interaction** | No two MSPs scheduled at the same time actually depend on each other in a way their file lists do not show. | A model reads every concurrent pair. |

**Why C5 exists, and why it is not a guard against a bug in the code.** File
overlap is a **stand-in** for "these two pieces interact." That stand-in is blind
to a real case: one MSP defines a configuration key in one file, and another reads
it from a different file. No shared path, so they run at the same time, and the
second builds against a base where the key does not exist.

Nothing else in this design can see that. C5 is the model checking the property
the stand-in only approximates, which is why it is worth paying for even though
this phase is deterministic.

**Output:** clusters, and an execution order inside each.

---

### Phase 4 — Plan

**Input:** the frozen SPEC, one MSP, and the branch this MSP will be built on.

Runs once per MSP. **Phases 4 to 7 run as a unit, one MSP at a time.** An MSP goes
through planning, building, review and shipping before the next MSP in its cluster
is planned. Clusters are still eligible to run at the same time as each other.

What P1 needs from this is narrower than the whole unit: the previous MSP's branch,
which exists once that MSP's Phase 5 has finished. Running the unit to completion
is the simpler rule and costs nothing, because MSPs in a cluster are sequential
either way.

**What this phase is responsible for, in one sentence:** producing a plan that
covers this MSP's slice of the SPEC completely and can actually be built.

**What happens:** a planner reads the whole SPEC and this one MSP, and produces
one artifact — the plan — with two clearly separated parts.

| Part | What it is |
|---|---|
| **Steps** | The work to do, in order: what to build, where, following what patterns already in this codebase. Steps say what to do. |
| **Acceptance properties** | What must be true once the work is finished. Outcomes, never steps toward one, and never the code that produces them. Defined in section 5.1. |

**Why there is a planning phase at all:** without one, the decompose pass would
have to write a complete implementation brief for every MSP up front — hundreds of
lines of detail guessed before any of it was examined closely. A planner reading
the full SPEC for one MSP produces a better plan and a shorter decomposition.

**The planner reads the SPEC. Nothing after this phase does.** This is the
strictest boundary in the pipeline. The planner needs full context to write a
correct plan; everything downstream works from the plan alone.

The consequence is deliberate and worth stating plainly: a plan that misses part
of its slice of the SPEC will never be caught later. A phase that re-checked
against the SPEC would be compensating for a planning failure rather than
surfacing it, and would make every later phase worse at its own job. This is why
the bar here is a requirement rather than a goal.

**Invariants.** Eight statements, fixed, asserted against the finished plan before
Phase 5 begins.

| # | The statement that must be true | How it is asserted |
|---|---|---|
| **P1 — Already false** | Every acceptance property is false against this MSP's base branch: the feature branch for the first MSP in a cluster, the previous MSP's branch for every later one. | A model checks each property against that branch. |
| **P2 — Slice coverage** | Every behavior this MSP's part of the SPEC requires is covered by at least one acceptance property. | A model maps the slice against the property list. Anything unmapped means a property is missing, and the planner writes it. |
| **P3 — No cheap satisfaction** | For every property, the cheapest way to make it hold requires the real behavior. | The model names the laziest thing that would satisfy the property literally, and that answer is judged. See below. |
| **P4 — Checkable without reading the code** | Every property can be checked without opening the implementation that produces it. | A model judgment. |
| **P5 — Grounded** | Every piece of existing code the steps refer to exists in this repository. | Mechanically. |
| **P6 — In scope and shippable** | The plan stays inside the MSP's brief and its declared files, and describes no work that would leave the branch broken if merged on its own. | A model compares the plan against the brief and `writes`. |
| **P7 — Properties are not steps** | Acceptance properties are stated separately from the steps, and no property restates a step. | A model judgment. |
| **P8 — Steps reach the properties** | For every property, the plan names which steps produce it. | A model maps properties against steps. A property no step produces means a step is missing, and the planner adds it. |

**Why P1 can be asserted at all.** Phase 4 writes no code. At the moment P1 is
asked — the end of this phase — the plan exists and the work does not, so "before
the work" is simply the present, and nothing needs to have been captured earlier.
This is also why P1 names a branch rather than a moment: a branch can be checked
whenever you like, and a moment that has passed cannot.

**P1 and R3 are the same claim, checked twice.** Phase 6's R3 reverts the finished
work, which lands the branch in exactly the state P1 described, and requires every
assertion to fail there.

| | P1 | R3 |
|---|---|---|
| When | End of Phase 4, before anything is built | End of Phase 6, after the work and a green build |
| How | A model reads each property against the base branch | Revert, run, observe, restore |
| What it is | A judgment, which can be wrong | A fact |
| Cost of catching a bad property here | One more round of planning | A build and a review pass, thrown away |

Dropping P1 would not lose correctness, because R3 still catches a property that
was already true. It would move every catch to the far side of a build. P1 is the
cheap fallible check and R3 the expensive certain one, and keeping both is what
makes the common case cheap.

**How P3 works, and where its output goes.** Asking a model whether a property is
strong invites a yes, because that is what the question invites. Asking it for the
least work that would satisfy the property literally produces something concrete
that can be judged instead. If the answer is "create an empty file," the property
is rejected and rewritten. If the answer requires the behavior, the property
stands.

**That analysis never reaches the worker.** It is evidence for the P3 assertion,
kept in the terminal report so a human can audit why a property was accepted. It
is not part of the plan. Handing a worker a written description of the minimum
that passes would defeat the property it was written to strengthen.

**Why P4 is about reading the code, and not about being visible to a user.**
Password hashing on signup is invisible to any user and is a perfectly good
property: "after a signup, the users table contains no plaintext password" is
checked by signing up and querying the table. What P4 excludes is a property that
can only be confirmed by opening the implementation, such as "`auth.py` defines
`hash_password` and it calls bcrypt."

Two things go wrong without P4. A property pinned to internal structure breaks
whenever somebody refactors without changing behavior, and the usual response is
to weaken the property until the build is green again. More important here: a
reviewer who must read the implementation to check a property is reading the
worker's code to decide whether the worker's code is right, which is exactly the
coupling Phase 6 exists to break.

**How the eight fit together.** They are two chains and a frame, and reading them
as eight separate rules misses what they are doing.

*The completeness chain* runs SPEC slice → acceptance properties → steps. P2
guards the first joint and P8 guards the second. A gap at either one is work the
SPEC asked for that will simply never happen, and by the boundary above, nothing
downstream is built to notice.

*The meaningfulness chain* asks whether each property can actually tell correct
from incorrect. P1 separates before from after. P3 separates real work from token
work. P4 keeps the property checkable by somebody who did not write the code. A
property failing any one of the three can hold while the MSP does nothing useful.

*The frame* is P5, P6 and P7. The first two keep the plan attached to reality:
real code, declared scope, a branch that still works when merged alone. P7 is
load-bearing in a way that is easy to miss — if properties and steps collapse into
one list, P2 and P8 become the same check, and the meaningfulness chain has
nothing left to attach to.

**Output:** a plan, with its steps and its acceptance properties.

---

### Phase 5 — Implement

**Input:** one MSP's plan, and — when Phase 6 has sent the MSP back — the failing
verdict that returned it.

Runs once per MSP, in the order the schedule set.

**What this phase is responsible for, in one sentence:** building what the plan
says, and nothing else.

**What happens:**

1. Create a worktree and a branch for this MSP.
2. The worker receives the plan. It does not receive the SPEC.
3. It writes the code.
4. It drives continuous integration to green.

**The worker writes no acceptance assertion.** Its own tests are ordinary
development — you write tests to know your code works while you are writing it,
and they run in continuous integration like any other test. They are not the
acceptance gate. Phase 6 writes the acceptance assertions independently, for the
reason in section 5.5.

**Isolation is physical, not by convention.** Each MSP gets its own checked-out
copy of the repository. Two workers running at once cannot see or overwrite each
other's files, regardless of what their write-sets claimed.

**The steps inside one MSP run in order, in one worktree.** There is no task-level
parallelism. Parallelism in this design is between clusters and nowhere else.

**Red integration is ordinary work, not a special state.** A failing build is a
bug, and fixing bugs is what the worker does. There is no retry counter and no
attempt limit. If an MSP genuinely cannot be finished, that is a failure of the
SPEC or of the plan, and it escalates into the terminal report.

**Invariants.**

| # | The statement that must be true | How it is asserted |
|---|---|---|
| **W1 — Green on this commit** | Continuous integration is green on the branch's current head, not on an earlier commit. | Mechanically, comparing the build's commit to the branch head. |
| **W2 — Every step done** | Every step in the plan has corresponding work in the diff. | A model reads the plan's steps against the diff. |
| **W3 — Nothing beyond the plan** | No work was done that the plan did not ask for. | A model reads the diff against the plan. |

**Why W3 matters more than it sounds.** Phase 6 checks the plan's acceptance
properties and nothing else. Anything built that the plan never asked for is
therefore work that nobody will ever check.

**Why W1 is worth stating.** A build can go green and then more commits can land,
leaving a green result that describes code nobody tested. Section 5.7 says a red
build invalidates every prior assertion; W1 is the same idea applied to the build
result itself.

Files actually changed are recorded for the drift report. That is data collection
and not a gate — see decision 8.4.

**Output:** a branch with committed work and green integration.

---

### Phase 6 — Review

**Input:** one MSP's plan, and its finished branch.

Runs after Phase 5, once per MSP — and again each time a failing verdict sends the
MSP back and it returns.

**What this phase is responsible for, in one sentence:** proving that the finished
work satisfies the plan, without trusting anything the worker said about it.

**What happens:** a reviewer takes the plan's acceptance properties and, for each
one, writes its own executable assertion and runs it. It returns a verdict.

**Why this is a separate phase and not the end of Phase 5.** A worker that writes
the code and then writes the check proving that code correct is grading its own
homework. Section 8.3 already says an implementer's own opinion that it finished
is not evidence; letting it author its own acceptance assertions is that opinion
in executable form. Two independent authors is the entire mechanism.

**The reviewer does not read the SPEC.** It checks the work against the plan. If
the plan was wrong, that is a Phase 4 failure, and reaching past the plan to the
SPEC would hide the failure rather than surface it.

**The reviewer does not adopt the worker's tests.** It writes its own from the
acceptance properties. Reusing the worker's tests collapses two independent
authors back into one, with an extra step in front of it.

**Invariants.**

| # | The statement that must be true | How it is asserted |
|---|---|---|
| **R1 — Independently authored** | Every assertion was written by the reviewer, not taken from the worker's tests. | Mechanically, by provenance: every assertion file was authored in a review commit and none in a worker commit. Stated this way it still holds on a second review pass, where the files already exist from the first. |
| **R2 — Every property covered** | Every acceptance property has its own assertion. None skipped, none folded into another. | Mechanically, by mapping properties to assertions. |
| **R3 — Inertness** | With this MSP's change reverted, **every** assertion fails. | By actually reverting, running all of them, observing every failure, and restoring. |
| **R4 — Against the shipped code** | The assertions ran against the branch's current head, with continuous integration green. | Mechanically. |
| **R5 — Verdict on properties only** | The pass or fail rests on the stated acceptance properties and nothing else. | A model judgment on the verdict text. |
| **R6 — The SPEC was not read** | The SPEC is not among the reviewer's inputs. | Mechanically, by construction. |

**R3 is the strongest statement in this set.** An assertion suite that still
passes when the work is removed is testing nothing at all.

**Why it demands every assertion and not one.** R3 is the executable form of P1,
and P1 is about every property, not some of them. A reverted branch is exactly the
state P1 describes — the work does not exist — so an assertion that still passes
there is checking something P1 already claimed was false. One of the two is then
wrong, and neither the planner nor the reviewer would ever find out.

Phase 4 sets out why both P1 and R3 exist rather than only this one.

**Why R5 exists.** A reviewer allowed to fail an MSP on general opinion puts
unbounded judgment back into a pipeline built to avoid it. The verdict is about
the properties the planner stated, or it is not a verdict.

**When review fails,** the MSP goes back to Phase 5 and the worker fixes it. That
is ordinary work, like a red build, and there is no attempt limit. A verdict that
cannot be satisfied escalates into the terminal report.

**Output:** a verdict, and the assertions that produced it.

---

### Phase 7 — Ship

**Input:** a reviewed MSP branch with a passing verdict, and the record of every
check the run performed on it.

**What happens:** open one pull request.

**Where it points:**

| Position | Base branch |
|---|---|
| First MSP in its cluster | The feature branch |
| Every later MSP in that cluster | The branch of the MSP immediately before it |

This makes each cluster a **stack** of pull requests. MSPs inside a cluster are
serialized because one needs the other or because they touch the same file, and
each is built on its predecessor's branch — that is what makes the stack.

An overlap-only successor pays a cost for this: its pull request cannot merge
until a predecessor it does not actually need merges first, because that
predecessor's branch is its base.

Clusters are independent, so each cluster's stack targets the feature branch on
its own.

**mitosis ends here.** The pull request stays open. A human reviews it. A human
merges it. A future shipping adapter takes the feature branch onward.

**Pull requests are opened through the project's centralized tool**, never
ad-hoc. Title grammar and body fields follow that tool's mandatory format.

**Invariants.**

| # | The statement that must be true | How it is asserted |
|---|---|---|
| **S1 — Reviewed first** | Phase 6 returned a passing verdict for this MSP. | Mechanically. |
| **S2 — Correct base** | The base branch is what the stacking rule above says it is. | Mechanically. |
| **S3 — No invented verification** | Every check listed as verified corresponds to a command that actually ran, with its real result. | A model compares each claimed line against what the run executed. |
| **S4 — No silent omission** | A check that was not run appears as not-verified, rather than being left out. | A model compares the checks the run actually performed against what the body lists. |

**Why S3 and S4 are two halves of one problem.** A fabricated verification line is
an obvious lie. An omitted one is not obviously anything: a reviewer reads a short
verification list as "that was everything worth checking," which is the same false
assurance arriving quietly. This is the one place mitosis can mislead the only
human in the system, which is why both halves are stated.

**Output:** open pull requests.

---

## 5. Invariants and acceptance properties

These are how mitosis knows something is correct without a human looking. They
are the mechanism the whole design rests on. Everything else — the phases, the
isolation, the stacking — is arrangement.

### 5.1 The two things, and why they do not share a name

| | **Invariant** | **Acceptance property** |
|---|---|---|
| What it is about | The output of one phase | One MSP's finished behavior |
| Written by | This document | That MSP's planner, in Phase 4 |
| Changes between runs | Never | Every time |
| Human review before it is used | Yes. A person writes and edits this document before any run. | None. A model writes them mid-run and they are used immediately. |
| Read by a human afterwards | Here, in this document | In the terminal report, under Plan coverage, Property strength and Review |
| Checked | At the end of the phase that owns it, or as the report is written for T1 to T5 | In Phase 6, by the reviewer |

**An invariant is a statement that must be true of what a phase produced.** There
are eight fixed sets — one for each of the seven phases, and one for the terminal
report — each listed with the thing it governs.

**An acceptance property is a statement about what one MSP's finished work must
do**, true when the work was done correctly and false when it was not. It
describes an outcome, never a step toward one, and never the code that produces
it.

Three examples make that boundary concrete.

| Statement | What it actually is |
|---|---|
| "Add a `password_hash` column, and hash on signup." | A **step**. It says what to do, not what must then be true. |
| "`auth.py` defines `hash_password`, and it calls bcrypt." | **Code**, not behavior. Checking it means reading the implementation. |
| "After a signup, the users table contains no plaintext password." | An **acceptance property**. An outcome, checkable without opening the hashing code. |

**Why they carry different names.** Invariants are fixed, versioned, and read by a
human before they ever run. Acceptance properties are generated fresh by a model
during the run and used the moment they exist, with nobody between writing and
use. Both are read afterwards, in the report — but only one was reviewed before it
mattered. Calling them by the same name would hide that difference, which is the
most important thing about them.

### 5.2 A model is asked. A program checks what a program can.

Some statements can be settled by running code — a graph has no cycle, a path
exists on disk. Those are checked mechanically, and that check is cheap enough
that there is never a reason to skip it.

**A mechanical check never replaces the question.** The model is asked it too, in
plain words, against the actual artifact — the same way a careful human reviewer
would be asked, and for the same reason. Code only checks what somebody thought
to encode in advance. The questions that matter most have no mechanical form at
all: whether a decomposition covers everything the SPEC asked for is a reading
task, not a computation.

**Asserting means showing the work.** An assertion is not a yes. It is the
evidence that makes the yes checkable — the coverage mapping, the run that failed
before the fix, the file that was opened. A bare affirmation is the thing this
mechanism exists to replace.

### 5.3 What happens when a statement cannot be affirmed

Never a warning, and never a note attached to output that carries on regardless.

**The first response is more work, not stopping.** An invariant is the phase's
exit condition, not a gate held by somebody else. The agent that could not affirm
one is the agent holding the context needed to fix it: an uncovered requirement
means find it and add the MSP, a property no step produces means add the step, a
red build means fix the build. It then asserts again. The phase ends when every
statement holds, not when the agent believes the work is done.

Three outcomes exist, and which applies depends on whether the phase has the
freedom to fix what it found.

| Outcome | When it applies | Example |
|---|---|---|
| **Iterate** | The phase can fix it with the context it already has. This is the normal case, and most statements only ever end here. | D1 finds an uncovered requirement, so the decompose pass adds the missing MSP and asserts again. |
| **Return to an earlier phase** | The failure is real, but this phase has no freedom to fix it. | C5 finds a hidden dependency. Phase 3 is pure computation over the MSP list, so the fix is a `needs` edge in Phase 2. |
| **Stop** | No agent can fix it, or iterating has been exhausted. | I1's hash does not match, which is a fact about the world, not a decision. C1 to C4 failing means the clustering code itself is broken. |

**There is no attempt limit on iterating**, exactly as there is none on a red
build. A run or an MSP that genuinely cannot satisfy a statement escalates to a
pause, which is section 6.1.

**When it does stop, what stops depends on how much has been built.**

| Where | What stops |
|---|---|
| Phases 1 to 3 | The whole run. These finish before any MSP is planned, so nothing has been built and this is the cheapest possible place to fail. |
| Phases 4 to 7 | That MSP, and every MSP behind it in its cluster. Other clusters finish normally. A failing review **verdict** is not a stop at all: it returns the MSP to Phase 5, as 6.1 says. |
| The terminal report | Nothing. The report is the output, so the failure is printed inside it. |

**Why Phase 4 sits with 5 to 7 and not with 1 to 3.** Phases 4 to 7 run as a unit,
one MSP at a time, so a planning failure on the third MSP in a cluster arrives
after the first two have already been built and shipped. Stopping the whole run
there would throw away finished work over a plan for one piece.

In every case the record names the statement, what was expected, and what was
found.

### 5.4 When a statement is asserted, and what forces it

**When.** At the end of the phase's work, before anything is handed on — and
again after every round of fixing, because a failure sends the agent back rather
than ending the phase.

**How many times.** Once per execution of the phase, plus once more for each
round of fixing. There is no fixed number and no cap.

Phases 1 to 3 run once per run, and phases 4 to 7 once per MSP, **when nothing
goes wrong**. That is the floor, not the count. Two things raise it: a phase that
iterates asserts again on each round, and a phase can be re-entered from
downstream — C5 sends a fix back to Phase 2, and a failing verdict sends an MSP
back to Phase 5 and then through Phase 6 again.

**Re-assertion when the thing underneath changes.** An affirmed statement
describes the output as it stood when it was affirmed. If that output changes
afterwards, the statement is asserted again. The common case: a failing review
returns an MSP to Phase 5, the branch changes, and W1 to W3 and then R1 to R6 all
have to hold again. A previous affirmation against different output is not
evidence, which is the same rule 5.7 applies to a red build.

**What forces it, rather than leaving it a suggestion.** Every phase emits an
**assertion record** alongside its product: one entry per invariant in that
phase's set, carrying the verdict and the evidence behind it. mitosis does not
start the next phase until that record exists, has an entry for every statement in
the set, and every entry is affirmed.

That check is code, not judgment. It reads the record's shape — are all the
statements present, does each carry a verdict, is the evidence non-empty — and it
never re-decides whether a judgment was right. A model cannot skip an assertion,
because skipping one leaves the record incomplete and the run does not advance.

**What this buys, and what it does not.** It makes asserting mandatory rather
than advisory: no statement can be quietly passed over, and none can be answered
with a bare yes. It does not make the judgment inside an entry true. A model can
still affirm something it should have refused, and nothing here catches that —
which is why the evidence is kept and printed in the terminal report, where the
person who wrote the SPEC can read it.

### 5.5 Who does what

| Role | Responsibility |
|---|---|
| **This document** | Fixes every invariant. They do not change between runs. |
| **Each phase's own agent** | Asserts that phase's invariants against its own output, and keeps working until every one of them holds. Decision 8.10 explains why this is not self-grading. |
| **Planner** (Phase 4) | Writes the acceptance properties for one MSP. Writes no test code. |
| **Worker** (Phase 5) | Writes the code and keeps the build green. Writes no acceptance assertion. |
| **Reviewer** (Phase 6) | Writes an executable assertion for each acceptance property, independently, and runs it. |

**The worker never writes its own acceptance assertion.** Section 8.3 states that
an implementer's own opinion that it finished is not evidence. A worker that wrote
both the code and the check proving that code correct is exactly that opinion,
with extra steps in front of it. Phase 6 exists to break that, and it is the reason
planning, building and checking are three phases rather than two.

**This is narrower than it sounds**, and the second row above is why. The worker
does assert W1 to W3 about its own diff, because those statements are fixed in this
document and it cannot make them easier. What it may not do is author the
instrument that measures an acceptance property, whose wording a model produced
during this run. Decision 8.10 draws that line.

### 5.6 The strict requirement, and what now enforces it

**If every acceptance property holds and the MSP still does not work, the
properties were designed wrong.**

A property that can hold without the MSP being correctly implemented is worthless.
This requirement used to be stated here and checked nowhere. It is now enforced by
the meaningfulness chain from Phase 4: **P1** requires every property to be false
before the work starts, **P3** requires the cheapest way of satisfying it to need
the real behavior, and **P4** requires it to be checkable without opening the
implementation. **P7** is separate: it is the structural precondition that keeps
properties stated apart from steps, so the meaningfulness chain has something to
attach to.

### 5.7 Ordering against continuous integration

**Continuous integration must be green before acceptance properties are
asserted.**

Assertions run against working code. If the build is red the code is not working,
and asserting against it proves nothing.

If the build later goes red because the code changed, every property must be
asserted again. A previously passing assertion against different code is not
evidence.

### 5.8 The two are independent checks

Green integration says the codebase still works. Passing acceptance properties say
this MSP did what it was asked to do. Neither implies the other, and an MSP needs
both.

---

## 6. Failure

### 6.1 What happens when an MSP cannot finish

The MSP pauses. Whatever exists for it — a branch, a plan, committed work — stays
in place, and nothing is unwound.

A pause is the last resort, never the first response. A statement that cannot be
affirmed first sends the agent back to do the missing work, as 5.3 describes, and
only becomes a pause once that is exhausted.

Every MSP after it **in the same cluster** also pauses, because each is built on
its predecessor's branch — whether it needed that predecessor's work or only
shared a file with it, it is serialized behind it either way. Since phases 4 to 7
run one MSP at a time, those have usually not been planned yet, so there is
nothing of theirs to preserve. They are paused before they ever start, and the
report is the only place they appear at all.

**Other clusters are unaffected and finish normally.** A failure in one cluster
never stops another.

**A failed review is not a failure of this kind.** When Phase 6 returns a failing
verdict, the MSP goes back to Phase 5 and the worker fixes it, exactly as it would
fix a red build. There is no attempt limit. Only a verdict that cannot be
satisfied at all becomes a pause.

### 6.2 Why there is no elaborate failure machinery

An MSP that cannot reach green integration means the SPEC was unclear or the plan
was wrong. The defect is upstream, in specification or planning, not in shipping.

Building recovery machinery for it would be designing around a problem that should
be fixed where it starts. The affected cluster pauses and mitosis reports it
honestly. Every other cluster finishes.

### 6.3 Resuming

There is no saved run state. A human resolves whatever blocked the paused MSP,
then runs mitosis again on the same SPEC.

**The repository is the state.** On a re-run, intake reads existing branches and
open pull requests, and the decompose pass is told which work already shipped. It
plans only the remainder.

---

## 7. The terminal report

The report is the only thing a human reads when the run ends. It is the sole
output of an unattended process, so it must be complete and honest.

It contains:

| Section | Content |
|---|---|
| **Shipped** | Every MSP that finished, with its pull request link and cluster. |
| **Paused** | Every MSP that did not finish, what stopped it, and what is now blocked behind it. |
| **Coverage** | The mapping Phase 2 produced, in both directions: each part of the SPEC against the MSP or MSPs that cover it — including one already shipped by an earlier run and not part of this run's decomposition — and each MSP against the part or parts of the SPEC it covers. |
| **Plan coverage** | For each MSP, the mapping P2 produced: its slice of the SPEC against the acceptance properties covering it. |
| **Property strength** | For each acceptance property, the cheapest satisfaction P3 named, and why it was judged sufficient. |
| **Review** | For each MSP, the verdict and the assertions that produced it. |
| **Assumptions** | Every place the SPEC was interpreted rather than followed literally. The decompose pass and every planner emit these. |
| **Parallelism** | How many clusters ran at once, and how long the longest chain was. |
| **File drift** | For each MSP, files it declared against files it actually changed. |
| **SPEC drift** | Whether the SPEC file on disk still matches the copy this run froze. |

**Why assumptions must be complete:** mitosis never asks the author to clarify
the SPEC, so interpreting an unclear passage is the only thing it can do with
one. This section is the sole place that interpretation becomes visible. An
interpretation that is made and not recorded is indistinguishable from the SPEC
having said it.

**Why file drift is reported and never acted on:** the write-set's job ended when
the schedule was computed. Knowing that declarations were wrong tells you whether
the decomposition is any good. Re-running an MSP because it touched one extra file
that nothing else touched would pay a full rebuild for a mistake that harmed
nothing.

**Why "paused" must be loud:** a paused MSP opens no pull request to reject and
produces no failing build to notice. Nothing else in the run states it, so the
report must.

**Why coverage is printed even though D1 already passed:** D1 sends the decompose
pass back to add whatever it finds missing, so any decomposition that reaches the
report has already claimed to cover the SPEC. Printing the mapping lets the
person who wrote the SPEC check that claim against their own document in a couple
of minutes. It is the difference between trusting an assertion and being able to
see it.

**Plan coverage is printed for the same reason, one level down.** A plan that
quietly narrowed its slice is never caught by a later phase, by design. Printing
the per-MSP mapping gives the same two-minute check at plan level that Coverage
gives at split level.

**Invariants.** The report is not a phase, and a failure here cannot stop
anything, because the report is the output. A failed statement is printed inside
the report rather than raised outside it.

| # | The statement that must be true | How it is asserted |
|---|---|---|
| **T1 — Everything accounted for** | Every MSP from the decomposition appears in exactly one of Shipped or Paused. | Mechanically, against the MSP list. |
| **T2 — No assumption dropped** | Every assumption any emitter recorded reaches the report. | Mechanically, by count and identity. |
| **T3 — The real mappings** | The Coverage, Plan coverage and Property strength sections print what D1, D2, P2 and P3 actually produced, not summaries written at report time. | Mechanically, by comparison. |
| **T4 — Paused entries say something** | Every paused entry names what stopped it and what is blocked behind it. | A model judges specificity. "Failed" is not enough, and the entry is rewritten until it says something a human can act on. |
| **T5 — The SPEC did not change underneath** | The SPEC file on disk still matches the copy Phase 1 froze. | Mechanically, by hash. |

**Why T3 exists.** Regenerating a mapping at report time is a laundering step: it
gives a second pass the chance to paper over a gap the first one found. Print the
artifact that was actually asserted, or the assertion meant nothing.

**Why T5 is here and not in Phase 1.** Phase 1 can only prove that it froze the
document correctly, which is I1. Whether the file changed afterwards is unanswerable
until the run ends, and a statement is asserted at the end of the phase that owns
it. The report is also where it matters most: somebody about to read these results
needs to know first whether the document in front of them is the one that was built
from.

---

## 8. Decisions and their reasoning

Every decision below was made deliberately. The reasoning is recorded so nobody
reverses one without knowing what it cost.

### 8.1 A model decomposes the SPEC; there is no required format

**Decision:** mitosis reads whatever document the author already wrote. There is
no embedded block, no schema, no required section.

**Why:** the alternative requires every SPEC author to learn a format before the
tool is usable. That teaching problem is large — a format needs a specification, a
validator, a canonical example, and error messages that work. All of that exists
only to check something a model can produce directly.

**What it costs:** two runs on the same SPEC may decompose differently. Nobody
hand-designed the split, so the decomposition is only as good as the model.

### 8.2 There is a planning phase per MSP

**Decision:** decompose emits short briefs. A planner then reads the full SPEC for
each MSP and produces the implementation plan.

**Why:** without it, the decomposition would have to carry a complete
implementation brief per MSP, written before any of the work was examined. That
made the decomposition large, repetitive against the SPEC prose, and worse than a
plan written with full context.

### 8.3 Correctness is asserted statements, not human review

**Decision:** every phase has invariants that must be affirmed before anything
downstream proceeds, and they are fixed in this document. Each MSP additionally
has acceptance properties, written by its planner and asserted by a separate
reviewer. All passing, plus green integration, means done.

**Why:** with no human in the loop, "done" has to be decided by something other
than the opinion of whoever did the work. An implementer's own opinion that it
finished is not evidence, and neither is a decomposer's own opinion that it split
the SPEC correctly.

**Why a model asserts and not only a program:** most of what makes a phase's
output correct cannot be computed. Whether a decomposition covers the SPEC is a
reading task. Checking only what a program can check would leave the most
important properties unchecked, so a model is asked the question in plain words
and must show its evidence. Where a program can settle it, the program runs as
well — see 5.2.

**The burden this creates:** whoever writes a statement must write one whose
passing genuinely means the work is correct. For acceptance properties that is the
planner, and P1, P3 and P4 are what enforce it, with P7 as the separate
precondition that keeps properties apart from steps in the first place. For
invariants it is this document, and they get the human review that generated
properties never do.

### 8.4 Write-sets schedule; they never gate

**Decision:** write-sets decide what cannot run at the same time. Afterwards, the
difference between declared and actual files is recorded and never acted on.

**Why:** the write-set is a guess made before the code exists. Enforcing it would
pay a full rebuild for a guess that harmed nothing. The collisions that actually
matter are caught by git when the pull requests merge.

### 8.5 Clusters are dependency chains; clusters run in parallel

**Decision:** MSPs that depend on each other, or that share a file, go in one
cluster and run in sequence. Clusters are eligible to run at the same time as
each other, bounded by the cap in section 10.

**Why:** this is the natural shape of the work, and it maps exactly onto stacked
pull requests. A cluster is a stack.

### 8.6 One pull request per MSP; mitosis never merges

**Decision:** each MSP opens its own pull request into the feature branch or onto
its predecessor. mitosis stops there.

**Why:** the earlier design merged MSPs locally and opened one pull request for
everything. That made the local merge the only thing catching two workers touching
one file, and it hid an incomplete decomposition inside a single large review.

Separate pull requests hand conflict detection to git, and give each piece of work
its own review surface.

### 8.7 No human after invocation

**Decision:** once mitosis is invoked, no human is consulted until the run ends.
It asks nothing at intake, because it does not review the SPEC.

**Why:** a checkpoint in the middle requires somebody awake while it runs, which
defeats unattended parallel execution. Review still happens — at the pull request,
where a human reads actual code instead of a plan predicting it.

**What it costs:** the Phase 1 to 3 invariants send each phase back to fix what
they find, and stop the run only once that is exhausted — either way before
anything is built, so a bad split no longer burns a parallel run by itself. What
survives is narrower: a split or a plan that its own assertions wrongly affirmed,
which is not caught until a human reads the pull requests. That is time and
tokens, not correctness, since nothing merges without review.

### 8.8 Review is its own phase, and the worker writes no acceptance assertion

**Decision:** Phase 5 builds. Phase 6 writes independent assertions for the plan's
acceptance properties and returns a verdict. The worker writes no acceptance
assertion, and the reviewer adopts none of the worker's tests.

**What this rule does not cover.** It governs acceptance properties, whose
statements a model wrote during this run. It does not stop an agent from
asserting the fixed invariants of its own phase, which is a different act for the
reason given in 8.10.

**Why:** the earlier design had the worker produce the executable assertion for
each statement and prove it passed. The planner wrote the statement, but the
worker wrote the check — and a weak check of a strong statement passes. That
contradicted 8.3 in the same document's own words.

**Why the reviewer does not read the SPEC:** its job is the plan. Needing the SPEC
would mean Phase 4 failed, and reaching past the plan would hide that failure
instead of surfacing it — while making both phases worse at their own jobs.

**What it costs:** one more model pass per MSP, which is the most expensive change
in this design. Review runs once per piece rather than once per run.

### 8.9 Acceptance properties live in the plan, and do not share a name with invariants

**Decision:** what each MSP must be true of is part of its plan, not a separate
artifact. The word "invariant" is reserved for the fixed statements in this
document.

**Why:** the rules that make a correctness statement worth having — false before
the work, not cheaply satisfiable, checkable from outside — are general and do not
vary by project, so they belong in the fixed set a human reviews. What remains
specific to one MSP is the statement itself, and that is already what a plan is
for. Keeping one word for both would have hidden the difference between a
statement a human vetted and one a model invented mid-run.

**What it does not change:** something generated per run still decides what
correct means, because the SPEC is different every run. This relocates that
judgment and bounds it with eight rules. It does not remove it.

### 8.10 A phase asserts its own invariants

**Decision:** the agent that did a phase's work asserts that phase's invariants.
There is no separate checker and no independent pass.

**Why this does not contradict 8.8:** 8.8 objects to an agent authoring the
instrument that measures it. A worker that writes both the code and the check
proving that code correct can write a weak check and pass. A phase invariant has
no such opening — its statement is fixed in this document, and the agent can only
answer it or fail to. It cannot make the question easier.

**Why an independent asserter would be worse.** It has no context, because it did
not do the work. When it finds a gap it can report the gap and nothing else,
which is the completeness critic in section 9: findings nobody is positioned to
act on. The agent holding the context is the only one that can act on the answer,
which is why it has to be the one asked.

**What an invariant therefore is:** the phase's exit condition, not a gate held by
somebody else. A statement that cannot be affirmed means there is work left, and
the agent goes and does it.

**Where independence still lives.** Acceptance properties get it from Phase 6,
because their statements are generated during the run and the instrument needs a
second author. Phase invariants get it from the human reading the terminal report,
which is why 5.2 requires the evidence to be shown and why the report prints it.

**What it costs:** the agent that wants to be finished is the one deciding whether
it is. Self-assertion catches oversight well and motivated reasoning less well.
The alternative is worse at both, and every definition of done a human works to
has the same shape.

### 8.11 Git is the state

**Decision:** no run-state file. A re-run reads branches and open pull requests.

**Why:** a state file is a second record of something git already knows, and a
second record can go stale.

---

## 9. Rejected alternatives

Recorded so they are not re-proposed without new information.

| Rejected | Why |
|---|---|
| **An embedded machine-readable block in the SPEC** | Requires teaching every author a format, plus a validator, a canonical example, and error messages. All of it exists to check something a model can emit directly. |
| **A complete implementation brief per MSP in the decomposition** | Solved a problem that only existed because there was no planning phase. Adding the planner removed the need. |
| **Merging MSPs locally, one pull request for the feature** | Made a local serial merge the only collision check, and hid incomplete work inside one large review. |
| **A human gate before workers spawn** | Requires a human present mid-run. Pull request review does the same job later, on real code. |
| **Asking the author clarifying questions at intake** | SPEC quality is not mitosis's responsibility. Intake is also the phase with the least information — nothing is decomposed and no planner has read the SPEC against the codebase. Ambiguity is interpreted and reported instead. |
| **The implementer writing its own acceptance assertion** | The planner wrote the statement but the worker wrote the check, and a weak check of a strong statement passes. Contradicted 8.3. Replaced by Phase 6. This concerns acceptance properties only — a phase still asserts its own fixed invariants, per 8.10. |
| **A separate MSP-invariant artifact alongside the plan** | A second specification of the same thing, written by the same planner in the same pass. Folded into the plan as acceptance properties, governed by fixed Phase 4 invariants. |
| **Task-level parallelism inside one MSP** | Not rejected on merit — out of scope for this design. It would need the plan's steps to carry the files they touch and the steps they need, and a rule for what two concurrent writers may do in one worktree. It does not need local merging: all steps share one worktree and one branch, so two steps writing different files produce no merge operation at all, and nothing here reopens what 8.6 settled. It is its own decision. |
| **A completeness critic** | **Superseded, not still rejected.** The original objection was that it put a second model on the decomposer's own question and produced findings nobody could act on. Under the Phase 2 invariants the findings are acted on by the pass that produced them: D1 and D2 send it back to fix the split, and their mapping reaches the report. The objection no longer holds. |
| **Binding write-sets** | Pays a full rebuild to enforce a guess, against collisions git already catches. |
| **A saved run-state file** | Duplicates what branches and pull requests already record. |
| **Refusing a SPEC with no parallelism** | The per-MSP planning, review and pull request discipline is valuable even when parallelism is one. |

---

## 10. Operational parameters

Defaults, changeable without changing the design.

| Parameter | Default |
|---|---|
| Feature branch | Created by mitosis if absent, named from the SPEC file |
| Concurrent clusters | Capped at a configurable number |
| Invocation | A slash command taking a path to the SPEC |

---

## 11. Constraints inherited from the project

These are not mitosis's decisions. They bind any code it writes and any pull
request it opens.

- Pull requests are opened only through the centralized tool, in its mandatory
  format, with an honest verification section. A check that was not run is never
  reported as verified.
- No pull request may break the branch it merges into. This is what makes an MSP
  minimum and shippable rather than merely small.
- Code written by mitosis carries no comments.
- A test that has never failed proves nothing. An assertion must be seen failing
  and then passing, in one of two forms: red before the work and green after it,
  or — where the assertion is written after the work already exists — red when the
  work is reverted and green when it is restored. Phase 6 uses the second form,
  and R3 is where it is enforced.
- Nothing connects to a live database or cloud admin surface.
