# mitosis --spec evaluation protocol

Status: pre-registered. Written 2026-09-19, before any trial in this protocol
has been run.

This document is written first on purpose. Every hypothesis, metric, threshold
and kill criterion below is fixed before a single document is decomposed, so
that the analysis cannot be fitted to the results afterwards. Any change made
after the first trial is recorded in §10 with its date and reason, and trials
run before the change are reported separately.

---

## 1. Why the existing evidence does not count

One end-to-end `--spec` run succeeded on 2026-09-19 with a clean result. It is
not evidence of generalisation, for six specific reasons.

| # | Contamination | Consequence |
|---|---|---|
| 1 | The author of the tool wrote the test document | Its ambiguities are the author's own, not a stranger's |
| 2 | Three decompositions of that document were read before the run | The author knew the failure modes in advance |
| 3 | The decisions file was written with that knowledge | It encodes answers the failures revealed, so it is an oracle, not an intervention |
| 4 | The split scalars were designed after seeing the splits they now flag | The detector was fitted to its own training set |
| 5 | The acceptance properties were written by the same agents whose work they judge | A `pass` verdict is internally consistent, not externally validated |
| 6 | n = 1 document, 1 project, 1 language, 1 domain | No variance estimate exists |

Contamination 4 is the most serious. `fused_without_overlap` was built from
three recorded splits and then validated against those same three splits. That
is a fitted parameter reported as a finding.

Contamination 5 is the most consequential for the claim that mitosis works.
The gate proves a property fails when the implementation is reverted. It does
not prove the property was worth asserting. Nothing so far has checked the
shipped code against a specification of correctness that mitosis never saw.

---

## 2. What is being tested

Six hypotheses. Each states a prediction, a metric, and the observation that
would falsify it.

### H1 — Staging reduces the cost of reaching a plan

**Predict** median cost of one structure stage is below 50% of the median cost
of one combined structure-and-brief dispatch on the same document.

**Metric** the dispatch's reported cost per dispatch, from the model's own envelope.

**Falsified if** the ratio is above 0.75 on a majority of documents.

**Control** the combined pipeline at the commit preceding the staging change,
checked out and run against the same document and repository state.

### H2 — A decisions file monotonically reduces open questions

**Predict** the count of Steps carrying assumptions strictly decreases from
trial 1 to trial 2 on at least 75% of documents, where the decisions file for
trial 2 is authored **only** from trial 1's printed report.

**Metric** assumption count, and the count of trial-1 assumption subjects that
reappear in trial 2.

**Falsified if** the count fails to decrease on more than 25% of documents, or
if settled subjects reappear at any rate above 10%.

**Protocol control** the decisions file must be written and committed before
trial 2 executes, from the report text alone, with no inspection of the
resulting split. The commit timestamp is the evidence.

### H3 — `fused_without_overlap` predicts a split a reader rejects

**Predict** on documents never used to design the scalar, a non-zero value
corresponds to at least one Lane pair a blind reader separates, with precision
at or above 0.8.

**Metric** blind pairing. Every Lane pair with disjoint write-sets from every
trial is pooled, stripped of its finding label and of which document it came
from, shuffled, and judged one at a time on the single question: should these
two Steps be one Worker's serial work, or two? The judgement is recorded
before the labels are rejoined.

**Falsified if** precision is below 0.6, which would mean the scalar mostly
flags justified fusions.

**Known weakness** the judge is the tool's author. Blinding reduces but does
not remove this. Recorded as a limitation, not solved.

### H4 — Per-Step briefs stay coherent

**Predict** independently written briefs produce code that merges with zero
conflicts and where no Step's tests are broken by another's.

**Metric** merge conflict count across all MSP branches; test failures on the
merged tree attributable to a cross-Step interface mismatch.

**Falsified if** any document produces a cross-Step interface mismatch.

### H5 — A gate `pass` predicts external conformance

**Predict** nothing. This is the measurement the protocol exists for, and the
author's expectation is that it will be **falsified**.

**Metric** for each MSP that gated `pass`, run an external conformance suite
that mitosis, the decomposer, the brief writers and the Workers never saw.
Report the rate at which a `pass` coexists with an external failure.

**Why it matters** the gate's verdict is currently the strongest claim mitosis
makes. It is entirely internal: the Worker writes the property, the Worker
writes the implementation, and the gate checks only that one depends on the
other. If a gate `pass` routinely coexists with external non-conformance, the
verdict means "this test is load-bearing" and not "this code is right", and
every report should say so.

### H6 — The pipeline ships working, reviewable code from an unseen document

**Predict** at least 60% of documents reach exit 0 with zero reconcile
findings and a clean merge.

**Metric** exit code, reconcile findings, merge conflicts, external suite pass
rate, achieved parallelism.

**Falsified if** below 50%.

---

## 3. The corpus

Eight documents. None authored for this protocol. Each selected before any
trial runs and listed here in full, so the set cannot be pruned afterwards.

| # | Document | Source | External oracle | Expected difficulty |
|---|---|---|---|---|
| D1 | RFC 4648, whole | IETF | test vectors in §10 | low; five near-independent codecs |
| D2 | RFC 6902 | IETF | Appendix A plus the public json-patch-tests corpus | medium; six operations over one document model |
| D3 | RFC 4180 | IETF | hand-derived from the text, plus a public CSV edge-case corpus | high; famously underspecified |
| D4 | Semantic Versioning 2.0.0 | semver.org | the published precedence examples | low; small and precise |
| D5 | RFC 3339 | IETF | the grammar's own examples plus boundary cases | medium; one tightly coupled grammar |
| D6 | bencode | BitTorrent BEP 3 | round-trip vectors from the spec | low; four types, tiny |
| D7 | TOML, the toml.io main-branch text | toml.io | the toml-test suite valid and invalid cases | high; large and interdependent |
| D8 | `.gitignore` pattern semantics | git documentation | behaviour compared against `git check-ignore` | high; ambiguous and stateful |

**Selection rules, fixed now.** A document qualifies only if it was published
by someone else, is implementable against the Python standard library with no
network access, and has an oracle that exists independently of anything this
protocol produces. D3 and D8 are included **because** they are underspecified;
a corpus of only precise documents would measure the easy case.

**Known confound.** Every one of these is likely in the model's training data.
This inflates H4, H5 and H6, because subject familiarity makes implementation
easier. It does **not** inflate H1, H2 or H3, which measure how a document is
split rather than whether its subject is known: memorising base64 does not
help a decomposer avoid a bad `contract_group`. The two groups are reported
separately and never pooled.

---

## 4. Trial structure

Each document gets a fresh repository containing only a charter, an acceptance
wrapper, and the document. No prior code, so no cross-document leakage.

| Stage | Runs on | Cost per document |
|---|---|---|
| A. Structure, control | all 8, at the pre-staging commit | one combined dispatch |
| B. Structure, treatment | all 8, at HEAD | one structure dispatch |
| C. Structure, treatment, second trial | all 8, with a decisions file from B's report | one structure dispatch |
| D. Full build | 4 of 8, chosen by lot before stage A | briefs plus Workers |
| E. External conformance | the 4 built | no model |

Stages A to C are cheap and answer H1, H2 and H3 across the whole corpus.
Stage D is expensive and answers H4 and H6 on a random half. Stage E answers
H5 and costs nothing but time.

**The four built documents are drawn by lot before stage A and recorded in
§9**, so the expensive half cannot be chosen after seeing which decompose
well.

---

## 5. Controls

| Confound | Control |
|---|---|
| Author knows the document | No document authored here; ambiguity is the publisher's |
| Author knows the failure modes | Decisions files written only from a printed report, committed before the next trial |
| Detector fitted to its data | `fused_without_overlap` validated only on splits from this corpus, never on the three that designed it |
| Judgement of "good split" | Blind, shuffled, label-stripped, recorded before unblinding |
| Acceptance written by the judged agent | External conformance suite the pipeline never sees |
| Model drift over the window | Model id recorded per dispatch; trial order randomised, not grouped by document |
| Cherry-picking the successful run | Every trial reported, including failures and crashes; no trial is rerun for a better number unless the failure is in the harness, and any such rerun is listed in §10 |

---

## 6. What is measured, per trial

Structure stage: cost, wall time, turns, output tokens, cache reads, Step
count, every scalar, every finding, assumption count and subjects,
constraint count, coverage, decisions count.

Build stage: brief count and cost, Lane and MSP outcomes, gate outcomes by
class, reconcile findings, merge conflicts, achieved parallelism, suite result.

External stage: conformance pass rate per MSP, and the joint distribution of
gate verdict against external result.

---

## 7. Kill criteria

Stated now so that a bad result cannot be reframed as a partial success.

mitosis's `--spec` path is **not working** if any of these holds across the
corpus:

- fewer than 50% of built documents reach exit 0
- a gate `pass` coexists with external non-conformance in more than 20% of MSPs
- reconcile reports findings on a majority of documents
- median achieved parallelism is at or below 1.2 times serial
- `fused_without_overlap` precision is below 0.6 under blind judgement

Progress short of that is reported as progress, with the specific hypotheses
that survived and those that did not.

---

## 8. What this protocol cannot establish

It has eight documents, one model, one language and one author. It can show
that the pipeline works or fails across a varied corpus it has not seen. It
cannot estimate performance on a language mitosis has never built in, on a
document longer than any here, on a repository with existing code that must be
modified rather than created, or under a different model.

The `--items` path is out of scope; it is unchanged and separately exercised.

---

## 9. Pre-registered random draw

The four documents for stage D, drawn before stage A, recorded here with the
seed so the draw is reproducible.

Seed text "mitosis-spec-evaluation-2026-09-19", taken as the first 16 hex
digits of its SHA-256, giving 9627601857392515639, fed to Python's
`random.Random.sample` over the eight documents in §3 order.

Drawn for stage D, the full build: **D2 RFC 6902, D4 Semantic Versioning
2.0.0, D5 RFC 3339, D7 TOML subset.**

Structure stages only: D1 RFC 4648, D3 RFC 4180, D6 bencode, D8 gitignore.

The draw is reproducible from the seed text alone and was performed and
committed before stage A.

---

## 10. Amendments

**2026-09-19, before stage A.** Added a stage 0 harness shakedown on D1: one
structure dispatch whose only purpose is to prove the harness runs. Its result
is reported but excluded from every hypothesis if any harness change follows
it, because a trial that caused a change to the apparatus cannot also measure
the apparatus. Reason: running eight documents through an unproven harness
risks spending the whole budget on a scripting fault.

**2026-09-20, after stage B and stage D.** The scalar set changed mid-protocol.
`lane_cycles` was added as an eighth scalar when cycle detection was written, so
corpus run 1 and corpus run 2 do not record the same set, and §6's promise to
record "every scalar" means a different thing in each. Run 1's structures are
still on disk and can be rescored with any later scalar, which is how the
comparison in this report was made; no run-1 number was restated from memory.
Recorded because a pre-registered metric that changes mid-evaluation is exactly
what pre-registration exists to expose, and two earlier amendments to this file
were prompted by a documentation lint rather than by the science.

**2026-09-19, before stage A.** Every document is handed to mitosis whole and
byte-for-byte as its publisher issued it. No section is trimmed, including
front matter, security considerations and references. Reason: trimming is
authoring, and the point of this corpus is that no part of it was shaped here.
The consequence is that coverage will honestly report boilerplate sections as
unclaimed, and that is read as a property of the document rather than a fault
in the split.

**2026-09-20, after corpus run 3 and before stage D.** Corpus run 3 is
discarded and stage D is built from corpus run 4 instead. Two sessions held the
same output paths open at once, forty-four seconds apart, and the second
truncated files the first was still writing at its own offset. The reports are
blends: `contaminated-reports-b3/D6-B3.txt` carries a complete clean run ending
`exit 0: the structure was written` followed by a second writer's output ending
`exit 3: refused to start`. Every run-3 report is therefore unusable. The eight
run-3 structures were checked individually against the raw model output stored
beside them and each matches one dispatch name for name, so the structures were
single-writer even though the reports were not; they are kept under
`contaminated-runs3/` and are not used for any claim here. Run 4 takes a
per-document atomic lock and records `SKIPPED` rather than interleaving. Reason:
a result that needs an argument about which bytes came from which writer is not
a result, and the four builds drawn in §7 are the most expensive arm in this
protocol.

**2026-09-20, before stage E.** One of the five frozen oracle digests does not
verify what it appears to verify. `FROZEN.json` records `1bbab111e8d0dfb4` for
the 777-file toml-test corpus. That value is reproducible, by hashing the sorted
file path strings and never opening a file, so it moves only when a file is
added, removed or renamed. Rewriting the entire body of any of the 777 cases,
including turning an expected failure into an expected success, leaves it
unchanged; this was demonstrated on a throwaway copy, where the path digest held
at `1bbab111e8d0dfb4` while a content digest moved. The four single-file digests
are genuine content hashes and all reproduce exactly. The TOML corpus is pinned
instead by its git commit `ff49d10` with a clean working tree and the counts 266
valid and 511 invalid, which reproduces and pins contents exactly, since git
content-hashes every file in the tree. `FROZEN.json` is left exactly as written;
the recipe, the demonstration of its blindness and the replacement check are
recorded beside it in `oracles/FROZEN-RECIPE.md`, with `verify-oracles.py`
running the check. The earlier stage E figure of 932 of 945 was measured against
this corpus under this digest, so it was pinned by the clone being untouched
rather than by any check. Reason: a pre-registered hash that cannot detect the
tampering it exists to detect is worse than no hash, because it is reported as
assurance, and stage E's whole purpose is to be checkable by someone who does
not trust the people who ran it.

**2026-09-20, after stage E.** §3 named D7 "TOML v1.0.0, a named subset" and
both halves of that are wrong. The fetched file is the toml.io main-branch
text, which is the unreleased 1.1.0 draft: it states that seconds may be
omitted and its own worked example shows a multi-line inline table with a
trailing comma, neither of which 1.0.0 permits. It was also handed over whole,
not as a subset, per the amendment above. The mislabel had a direct cost:
D7's conformance was first scored against `files-toml-1.0.0`, which reported
eleven failures, nine of which are cases 1.1.0 deliberately makes valid.
Scored against `files-toml-1.1.0` the result is 710 of 712. The row is
corrected and the three scopes are reported side by side rather than one
being chosen. Reason: the corpus entry is the only record of what a document
actually was, and an oracle chosen from a wrong label measures a different
specification than the one the Workers were given.

**2026-09-20, after stage B run 4.** H3 is unevaluable on this corpus and is
closed rather than left pending. Its metric is the precision of
`fused_without_overlap`, which requires at least one non-zero value to judge
blindly. The scalar reads **0 on all eight documents** in run 4, as it did in
runs 2 and 3, because the contract change that stopped Steps being fused
without a shared file removed the behaviour the scalar detects. Precision
over zero positives is undefined, so the pre-registered blind pairing has
nothing to pair. This is recorded as a hypothesis the corpus cannot test, not
as one that passed: the scalar may still be right and may still be wrong, and
nothing here distinguishes those. A corpus that still produced needless
fusion would be needed to settle it.

**2026-09-20, before stage C.** H2's clean arm is D1, D3, D6 and D8 only. The
control in §2 requires the decisions file to be written from trial 1's
printed report alone, with no inspection of the resulting split. That holds
for those four. It does not hold for D2, D4, D5 and D7, whose structures were
built in stage D and whose source code the author read in stage E before the
decisions files were written. D7's file also carries one rule that did not
come from its report at all: that every error escaping the parser must be the
package's own decode error, which comes from a defect stage E found. All
eight are run and reported; the four built documents are reported separately
and are not pooled with the clean four. The decisions files were committed at
2026-09-20T12:43:04-06:00, before any stage C trial executed, and every file
except D7's settles exactly the readings its trial-1 report printed.

---

## 11. Brownfield trial, pre-registered 2026-09-20 before it runs

Every trial in §3 hands mitosis an empty repository. §8 says this protocol
cannot speak to a repository with existing code that must be modified rather
than created, and that is the gap that matters most for using the tool on a
real project. This trial addresses it and nothing else.

**D9.** A repository holding the implementation D7 produced: 33 tracked
files, 29 Python modules, 152 passing tests. Its `docs/spec.md` is the TOML
**v1.0.0** document from the `toml-lang/toml` tag `1.0.0`, fetched whole. The
code was written against the toml.io main-branch text, which is the 1.1.0
draft, so the repository already implements a different and later revision of
the same specification. No Step can succeed by creating a new package; every
Step must read and edit code it did not write.

**The charter change.** D9's charter is the corpus charter with one section
added, stating that the package already exists, that most work edits a file
rather than creating one, that an existing test contradicting the document is
wrong and must be changed by whichever Step owns its file, and that a test
the document still requires must keep passing. Nothing else differs.

**Starting point, measured before the run.** 698 of the 709 cases in
`files-toml-1.0.0` pass. The eleven failures are recorded in
`conformance/D9-baseline.json` and span at least four existing modules:
six inline-table and datetime cases the 1.1.0 text permits and 1.0.0 forbids,
three omitted-seconds cases, one hex string escape, and two integers that
raise the wrong error type.

**H7 — mitosis can change code it did not write.** Predict the run reaches
exit 0, merges with zero conflicts, keeps the integrated suite green, and
raises conformance above the 698 baseline.

**Falsified if** conformance does not rise above 698, or the run cannot
produce a plan whose Steps edit existing files.

**Recorded as a separate failure** if it reaches 709 by deleting tests rather
than changing behaviour. The integrated suite's test count is recorded before
and after, and a drop is reported.

**What it still cannot establish.** One document, one language, one
repository, and a repository whose existing code mitosis itself wrote. A
codebase written by other people, with conventions mitosis has never seen,
remains untested.

---

## 12. Language trial, pre-registered 2026-09-20 before it runs

Every trial so far is Python. §8 says this protocol cannot speak to a
language mitosis has never built in. This trial addresses that and holds
everything else fixed.

**D10.** An empty repository whose `docs/spec.md` is the same Semantic
Versioning 2.0.0 document as D4, byte for byte. Its charter is the corpus
charter with the language clauses replaced: JavaScript against the Node
standard library alone, ES modules only, no build step and no transpiler,
source under `src/`, tests under `test/` using `node:test` and
`node:assert/strict`. Its acceptance runner is `acceptance.mjs`, which
selects one test by its exact top-level name and returns the same three
verdicts the Python runner returns.

D4 is therefore the control for D10 in the strictest sense available: one
document, one model, one charter shape, one oracle, and language as the only
deliberate difference.

**The acceptance runner was proven able to fail before use.** A passing test
returns 0, a failing property returns 1, a test whose module does not exist
returns 1, a missing file returns 4, and a test name that does not exist
returns 4. The first draft returned 0 for a name that did not exist, because
Node's TAP summary counts the file itself as a passing subtest; that draft
would have marked every unwritten property as satisfied. The runner now
matches the named test's own result line.

**H8 — the pipeline is not Python-specific.** Predict D10 reaches exit 0,
merges with zero conflicts, keeps its suite green, and scores at or above 44
of the 46 frozen semver cases, which is D4's 46 less a two-case margin.

**Falsified if** the run cannot produce a plan, or conformance falls below 40
of 46, or the split collapses to one Lane where D4 produced seven.

**What it still cannot establish.** One language, one document, one model,
and a language whose conventions are close to Python's. It says nothing
about a language with a compile step, a package manifest that Steps must
share, or a test runner that cannot select a single test by name.

**2026-09-20, D10 structure written, before its build.** The language trial
found a check that cannot fire outside Python, and it is recorded here before
the build so the finding is not shaped by the outcome. `shape.MANIFEST_NAMES`
is `__init__.py`, `index.ts`, `index.js`, `mod.rs` and `index.d.ts`. D10's
public interface is `src/index.mjs`, which is on none of those lists, so
`manifest_gaps` returns empty for this package and the refusal that stops a
package shipping an empty public interface cannot trigger. The empty result is
a false negative, not a pass.

The ownership happens to be correct without the check: the public-surface
Step owns `src/index.mjs` and carries after edges reaching all four module
Steps. So
the model got right what the program could not have caught.

The trial runs against the tool as it stands. `index.mjs` is not added to
`MANIFEST_NAMES` before D10 builds, because changing the checker between
registering a prediction and testing it is the fitting this protocol exists
to prevent. It is fixed afterwards, with a test that goes red when reverted,
and H8's result is reported knowing the manifest gate was inert for it.

This is the fifth check found this day that produced a plausible value while
verifying nothing, after inert acceptance tests, a manifest rule that could
not fire on its corpus, `pgrep -fc`, and an integrity digest blind to file
contents. The common shape is that none was visible in its own output.

**2026-09-20, after stage A.** H1 is withdrawn as unmeasurable, for the same
reason H3 was. It compares the cost of one structure stage against the cost of
one combined structure-and-brief dispatch at commit `261681c`. That commit
contains no plan-validity check of any kind: zero references to a lane-cycle
refusal or a manifest refusal, against five today. Four of its eight plans are
refused by today's tool, three for cycles where every Lane waits on another so
nothing can start, and one for a package whose public interface would ship
empty. A fifth put ten Steps in a single Lane and a sixth put seven in two.

The control is therefore cheaper in part because it does not check, and its
output is not the same deliverable. A ratio between the cost of a plan that
runs and the cost of a plan that does not is not a cost comparison, and the
median of 0.52 recorded against the pre-registered metric, and the 3.59 median
recorded for the full staged pipeline, are both withdrawn rather than
reported as findings.

The measurement that would answer the underlying question, whether the split
costs more than not splitting, was never in this protocol. It requires a third
arm: one agent handed the same document and the same charter, implementing it
serially in one repository with no decomposition, measured to the same
external conformance suite. Until that arm runs, this protocol says nothing
about whether mitosis costs more than the alternative a user actually has.

Recorded because the flaw is in the pre-registration, not in the data: the
control was chosen as the previous version of the tool rather than as the
alternative to using the tool, and the previous version's plans do not run.
