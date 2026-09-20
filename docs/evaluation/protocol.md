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
| D7 | TOML v1.0.0, a named subset | toml.io | the toml-test suite valid and invalid cases | high; large and interdependent |
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
