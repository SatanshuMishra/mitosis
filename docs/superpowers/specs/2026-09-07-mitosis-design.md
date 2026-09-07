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
| **Invariant** | A statement that must be true when the work was done correctly. Some are about one MSP. Some are about the output of a whole phase. |
| **Assertion** | The act of checking an invariant against the real artifact and affirming it. Where the check can be code, it is code. Where it is a judgment, a model answers it and shows the evidence. |
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

**Why `brief` is short:** the implementer never reads it. A planner reads it, and
the planner also has the whole SPEC. The brief only has to identify which slice
of the SPEC this MSP covers.

**Excluding finished work:** MSPs whose pull request already exists are not
emitted. The decompose pass is told what is already shipped and plans only the
remainder.

**Invariants.** Six statements, fixed and identical on every run, asserted
against the finished MSP list before Phase 3 begins. Section 5 defines what a
phase invariant is and how one is asserted.

| # | The statement that must be true | How it is asserted |
|---|---|---|
| **D1 — Coverage** | Every piece of work the SPEC asks for is covered by at least one MSP. | A model reads the SPEC and the MSP list and builds the mapping: each part of the SPEC against the MSP or MSPs covering it. Anything left unmapped fails. |
| **D2 — No invention** | No MSP proposes work the SPEC does not ask for. | The same mapping read the other way. An MSP mapping to nothing in the SPEC fails. |
| **D3 — Shippability** | Each MSP, merged on its own onto its base, leaves that branch working. | A model judges each MSP against that definition. An MSP that only makes sense once a later one lands is not shippable and fails. |
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

Invariants are how mitosis knows something is correct without a human looking.
They are the mechanism the whole design rests on. Everything else — the phases,
the isolation, the stacking — is arrangement; this is the part that makes an
unattended run trustworthy.

### 5.1 What an invariant is

A statement that must be true when the work was done correctly.

Not a test. Not a description of the work. A statement of fact about a finished
thing, which something must actually check and affirm rather than assume.

### 5.2 The two kinds

| Kind | What it is about | Who writes the statements | When it is asserted |
|---|---|---|---|
| **Phase invariant** | The output of one pipeline phase | This document. Fixed and identical on every run. | At the end of that phase, before the next one starts |
| **MSP invariant** | One MSP's finished code | That MSP's planner, in Phase 4 | In Phase 5, after continuous integration is green |

Phase invariants are constant because what makes a decomposition or a schedule
correct does not vary between projects. MSP invariants are designed fresh each
time, because what makes one piece of work correct is specific to that work.

### 5.3 A model asserts. A program checks what a program can.

Some invariants can be settled by running code — a graph has no cycle, a path
exists on disk. Those are checked mechanically, and the mechanical check is cheap
enough that there is never a reason to skip it.

**A mechanical check never replaces the assertion.** The model is asked the
question too, in plain words, against the actual artifact — the same way a
careful human reviewer would be asked, and for the same reason. Code only checks
what somebody thought to encode. The questions that matter most have no
mechanical form at all: whether a decomposition covers everything the SPEC asked
for is a reading task, not a computation.

**Asserting means showing the work.** An assertion is not a yes. It is the
evidence that makes the yes checkable — the coverage mapping, the failing run
before the fix, the file that was opened. A bare affirmation is the thing this
mechanism exists to replace.

**An invariant that cannot be affirmed stops the run.** It does not warn, and the
run does not continue with a note attached. mitosis pauses and the terminal
report names the invariant, what was expected, and what was found. Failing at the
end of Phase 2 is cheap, because nothing has been built yet.

### 5.4 Who does what

| Role | Responsibility |
|---|---|
| **This document** | Determines every phase invariant. They do not change between runs. |
| **Planner** | Determines the MSP invariants for one MSP. Writes the statements. Writes no test code. |
| **Implementer** | Produces an executable assertion for each MSP invariant and proves it passes. |

### 5.5 The strict requirement on the planner

**If every invariant passes and the MSP still does not work, the invariants were
designed wrong.**

An invariant that can pass without meaning the MSP is correctly implemented is
worthless. Designing invariants such that all of them passing genuinely means the
MSP works is the planner's core responsibility, and it is a strict requirement,
not a goal.

### 5.6 Ordering against continuous integration

**Continuous integration must be green before invariants are asserted.**

Assertions run against working code. If integration is red, the code is not
working, and asserting against it proves nothing.

If integration later goes red — because the code changed — every invariant must be
asserted again. A previously passing assertion against different code is not
evidence.

### 5.7 The two are independent checks

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
| **Coverage** | The mapping Phase 2 produced: each part of the SPEC against the MSP or MSPs that cover it. |
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

**Why "paused" must be loud:** a paused MSP opens no pull request to reject and
produces no failing build to notice. Nothing else in the run states it, so the
report must.

**Why coverage is printed even though D1 already passed:** the Phase 2 coverage
invariant stops the run when it finds a gap, so any decomposition that reaches
the report has already claimed to cover the SPEC. Printing the mapping lets the
person who wrote the SPEC check that claim against their own document in a couple
of minutes. It is the difference between trusting an assertion and being able to
see it.

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

**Decision:** every phase and every MSP has invariants that must be affirmed
before anything downstream proceeds. Phase invariants are fixed in this document.
MSP invariants are designed by the planner and asserted by the implementer. All
passing, plus green integration, means done.

**Why:** with no human in the loop, "done" has to be decided by something other
than the opinion of whoever did the work. An implementer's own view that it
finished is not evidence, and neither is a decomposer's own view that it split
the SPEC correctly.

**Why a model asserts and not only a program:** most of what makes a phase's
output correct cannot be computed. Whether a decomposition covers the SPEC is a
reading task. Checking only what a program can check would leave the most
important properties unchecked, so a model is asked the question in plain words
and must show its evidence. Where a program can settle it, the program runs as
well — see 5.3.

**The burden this creates:** whoever writes an invariant must write one whose
passing genuinely means the work is correct. For MSP invariants that is the
planner, stated as a strict requirement in 5.5. For phase invariants it is this
document.

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
| **A completeness critic** | **Superseded, not still rejected.** The original objection was that it put a second model on the decomposer's own question and produced findings nobody could act on. Under phase invariants the findings are acted on: D1 and D2 stop the run, and their mapping reaches the report. The objection no longer holds. |
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
