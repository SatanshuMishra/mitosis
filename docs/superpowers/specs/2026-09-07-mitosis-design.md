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
3. Every invariant in its plan has been asserted and passes.

Nothing further happens to it. It waits for a human.

---

## 2. Vocabulary

Every term used in this document, defined once.

| Term | Meaning |
|---|---|
| **SPEC** | The input document. A human wrote it. It describes work to be done. It is frozen the moment mitosis receives it. |
| **MSP** | Minimum Shippable Product. One piece of work small enough to be its own pull request, and complete enough that merging it does not break the branch it lands on. |
| **Cluster** | A group of MSPs that must be built one after another. Clusters run at the same time as each other. |
| **Write-set** | The list of files an MSP is expected to change. |
| **Invariant** | A statement that must be true when an MSP is correctly built. |
| **Assertion** | Executable proof that an invariant holds. |
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
- Opening one pull request per MSP.
- Reporting what happened.

### 3.2 Out of scope

- **Merging any pull request.** A human merges. mitosis never does.
- **Shipping the feature branch.** A separate, future shipping adapter owns this.
- **Requiring a particular SPEC format.** mitosis reads what you already wrote.
- **Judging whether the SPEC is good enough.** mitosis never asks the author to
  clarify, expand, or improve the document. It ships the one it was given.
- **A saved run-state file.** The repository itself is the record.
- **Reviewing its own output.** The pull request is the review surface.

---

## 4. The pipeline

Six phases, in order. The mechanisms they rely on — invariants, failure handling,
and the report — are specified in sections 5 through 7.

### Phase 1 — Intake

**Input:** a path to a SPEC file.

**What happens:**

1. Read the SPEC. From this moment it is frozen and is never modified.
2. Read the repository: current branch, existing branches, open pull requests.

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

**Output:** a frozen SPEC and the repository state.

---

### Phase 2 — Decompose

**Input:** the frozen SPEC, the codebase, the list of already-shipped work.

**What happens:** one model pass reads the SPEC and the actual codebase, and
emits the list of MSPs. For each MSP it produces:

| Field | What it is |
|---|---|
| `id` | A short stable name for this MSP. |
| `brief` | What this piece of work is, in a few sentences. Enough for a planner, not for an implementer. |
| `writes` | The files this MSP is expected to change. |
| `needs` | The ids of MSPs that must be finished before this one starts. |

**Why a model does this and not the SPEC author:** requiring the author to
hand-write this list means teaching every author a format before they can use the
tool. That is the problem this design exists to remove. The model reads the
codebase at decompose time, which a human writing a list in advance usually does
not.

**Why `brief` is short:** the implementer never reads it. A planner reads it, and
the planner also has the whole SPEC. The brief only has to identify which slice
of the SPEC this MSP covers.

**Excluding finished work:** MSPs whose pull request already exists are not
emitted. The decompose pass is told what is already shipped and plans only the
remainder.

**Output:** a list of MSPs.

---

### Phase 3 — Cluster and schedule

**Input:** the MSP list.

**What happens:** pure computation. No model is involved. This phase is
deterministic — the same MSP list always produces the same clusters.

1. Build a graph whose nodes are MSPs.
2. Draw an edge between two MSPs when **either** of these is true:
   - one declares the other in its `needs`, or
   - their write-sets share at least one file.
3. Each connected group of MSPs becomes one cluster.
4. Inside a cluster, order the MSPs so every dependency comes before what depends
   on it.

**The execution rule:**

- **Clusters run at the same time as each other.**
- **MSPs inside a cluster run one after another.**

**Why file overlap creates an ordering edge:** this is the entire job of the
write-set. Two MSPs that touch the same file must not run at once — not because
the files would be corrupted, but because the second one needs to see the first
one's work. Two MSPs adding different routes to one file, run blindly in
parallel, can both register the same path and merge cleanly with a bug.

**Why this phase must be deterministic:** everything upstream is a model's
judgment. The schedule is the one thing that must be reproducible, so a given MSP
list always produces the same execution order.

**A single cluster is a valid outcome.** If every MSP depends on the previous one,
mitosis runs them in sequence and reports parallelism as one. It does not refuse.

**Output:** clusters, and an execution order inside each.

---

### Phase 4 — Plan

**Input:** the frozen SPEC, and one MSP.

Runs once per MSP.

**What happens:** a planner reads the whole SPEC and this one MSP, and produces:

1. **An implementation plan.** What to build, where, following what existing
   patterns in this codebase.
2. **A set of invariants.** Statements that must be true when this MSP is
   correctly built.

**Why there is a planning phase at all:** without one, the decompose pass would
have to write a complete implementation brief for every MSP up front — hundreds of
lines of detail guessed before any of it was examined closely. A planner reading
the full SPEC for one MSP produces a better plan and a shorter decomposition.

**The planner reads the SPEC. The implementer does not.** This is deliberate.
The planner needs full context to write a correct plan. The implementer needs
isolation, so its blast radius is bounded by what it was told.

**Output:** a plan and its invariants. Invariants are specified in section 5.

---

### Phase 5 — Implement

**Input:** one MSP's plan and its invariants.

Runs once per MSP, in the order the schedule set.

**What happens:**

1. Create a worktree and a branch for this MSP.
2. The worker receives the plan. It does not receive the SPEC.
3. It writes the code.
4. It drives continuous integration to green.
5. It produces an executable assertion for each invariant and proves each passes.

**Isolation is physical, not by convention.** Each MSP gets its own checked-out
copy of the repository. Two workers running at once cannot see or overwrite each
other's files, regardless of what their write-sets claimed.

**Red integration is ordinary work, not a special state.** A failing build is a
bug, and fixing bugs is what the worker does. There is no retry counter and no
attempt limit. If an MSP genuinely cannot be finished, that is a failure of the
SPEC or of the plan, and it escalates into the terminal report.

**Output:** a branch with committed work, green integration, and proven invariants.

---

### Phase 6 — Ship

**Input:** a finished MSP branch.

**What happens:** open one pull request.

**Where it points:**

| Position | Base branch |
|---|---|
| First MSP in its cluster | The feature branch |
| Every later MSP in that cluster | The branch of the MSP immediately before it |

This makes each cluster a **stack** of pull requests. Because MSPs inside a
cluster depend on each other, each one's changes only make sense on top of the
previous one's.

Clusters are independent, so each cluster's stack targets the feature branch on
its own.

**mitosis ends here.** The pull request stays open. A human reviews it. A human
merges it. A future shipping adapter takes the feature branch onward.

**Pull requests are opened through the project's centralized tool**, never
ad-hoc. Title grammar and body fields follow that tool's mandatory format.

**Output:** open pull requests.

---

## 5. Invariants

Invariants are how mitosis knows an MSP is correct without a human looking.

### 5.1 What an invariant is

A statement that must be true when this MSP is correctly built.

Not a test. Not a description of the work. A statement of fact about the finished
system that can be proven by running something.

#### 5.2 Who does what

| Role | Responsibility |
|---|---|
| **Planner** | Determines the invariants. Writes the statements. Writes no test code. |
| **Implementer** | Produces an executable assertion for each invariant and proves it passes. |

#### 5.3 The strict requirement on the planner

**If every invariant passes and the MSP still does not work, the invariants were
designed wrong.**

An invariant that can pass without meaning the MSP is correctly implemented is
worthless. Designing invariants such that all of them passing genuinely means the
MSP works is the planner's core responsibility, and it is a strict requirement,
not a goal.

#### 5.4 Ordering against continuous integration

**Continuous integration must be green before invariants are asserted.**

Assertions run against working code. If integration is red, the code is not
working, and asserting against it proves nothing.

If integration later goes red — because the code changed — every invariant must be
asserted again. A previously passing assertion against different code is not
evidence.

#### 5.5 The two are independent checks

Green integration says the codebase still works. Passing invariants say this MSP
did what it was asked to do. Neither implies the other, and an MSP needs both.

---

## 6. Failure

### 6.1 What happens when an MSP cannot finish

The MSP pauses. Its branch and any work on it stay in place.

Every MSP after it **in the same cluster** also pauses, because each depends on
the one before it.

**Other clusters are unaffected and finish normally.** A failure in one cluster
never stops another.

### 6.2 Why there is no elaborate failure machinery

An MSP that cannot reach green integration means the SPEC was unclear or the plan
was wrong. The defect is upstream, in specification or planning, not in shipping.

Building recovery machinery for it would be designing around a problem that should
be fixed where it starts. mitosis pauses, reports honestly, and stops.

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
| **Assumptions** | Every place the SPEC was interpreted rather than followed literally. The decompose pass and every planner emit these. |
| **Parallelism** | How many clusters ran at once, and how long the longest chain was. |
| **File drift** | For each MSP, files it declared against files it actually changed. |

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

**Why "paused" must be loud:** work that was never planned produces no pull
request, no conflict, and no failing build. Absence has no signal of its own. The
report is the only place it can appear.

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

### 8.3 Correctness is invariants, not human review

**Decision:** the planner designs invariants. The implementer asserts them. All
passing, plus green integration, means done.

**Why:** with no human in the loop, "done" has to be something a program can
decide. An implementer's own opinion that it finished is not evidence.

**The burden this creates:** the planner must design invariants whose passing
genuinely means the MSP works. This is stated as a strict requirement in 5.3.

### 8.4 Write-sets schedule; they never gate

**Decision:** write-sets decide what cannot run at the same time. Afterwards, the
difference between declared and actual files is recorded and never acted on.

**Why:** the write-set is a guess made before the code exists. Enforcing it would
pay a full rebuild for a guess that harmed nothing. The collisions that actually
matter are caught by git when the pull requests merge.

### 8.5 Clusters are dependency chains; clusters run in parallel

**Decision:** MSPs that depend on each other, or that share a file, go in one
cluster and run in sequence. Clusters run at the same time.

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

**What it costs:** a bad decomposition burns a full parallel run before anyone
sees it. That is time and tokens, not correctness, since nothing merges without
review.

### 8.8 Git is the state

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
| **A completeness critic** | A second model asking the decomposer's own question, whose findings nobody could act on. |
| **Binding write-sets** | Pays a full rebuild to enforce a guess, against collisions git already catches. |
| **A saved run-state file** | Duplicates what branches and pull requests already record. |
| **Refusing a SPEC with no parallelism** | The per-MSP planning, testing and pull request discipline is valuable even when parallelism is one. |

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
- A test that has never failed proves nothing. Assertions must be shown failing
  before the work and passing after it.
- Nothing connects to a live database or cloud admin surface.
