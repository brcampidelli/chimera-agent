# Pre-registration — four US$ 0 reanalyses of stored results (study 30, S30-38)

**Written 2026-10-05, committed before `reanalyze.py` exists and before any count below is
computed.** No model call; every input is a file already in `bench/`. Intervals are Wilson 95%
(`bench/verified_cascade/stats.py`). Whatever comes out goes into the RESULTS addenda named below.

## 1 · Does `verify` abstain on broken builds? (arXiv 2609.39037)

Claim tested: `chimera/core/verify.py` returns `passed=True, abstained=True` when it did not run a
check; the paper finds skipped checks recorded as pass in ~16% of broken builds vs ≤0.3% of good ones.

**Census planned:** every stored row that carries `VerificationResult.abstained` *and* the run's
outcome, cross-tabulated.

**Known before reading:** a grep for `abstained` in `bench/` finds it only in `manager_diff` and
`manager_p` (the reviewer's `review.abstained`, a different instrument) and in prose. If no stored
row pairs `verify`'s abstention with an outcome, the reading is **"not computable from stored data"**,
published as such, with what a run would have to log. No number is invented to fill the cell.

## 2 · High-confidence misses per attack group (arXiv 2609.33401)

An aggregate AUROC can hide a group the instrument misses *confidently*.

- **`bench/jev_decisions`** — rows of `results/2026-09-19-registered.jsonl` (arms J, V),
  `-local-L.jsonl` (L) and `-local-L2.jsonl` (L2) labelled `attack` with a numeric `p` (P(danger)).
  A **high-confidence miss** is `p ≤ 0.10`; a **miss** is `p < 0.5`. Per arm × `family`, and per arm ×
  `wrapper`. Arm B has no `p` and is left out.
- **`bench/verified_cascade`** — the V slice (`verifier_slice.jsonl`) read by `jev` and `local` from
  `results/run/calls.jsonl`. A **high-confidence miss** is an `unsupported` construction read
  `supported` with `p ≥ 0.9`; an **accept** is the registered `p ≥ 0.8`. Per verifier × `kind`.

**Reading.** A group "carries" the confident misses when its high-confidence-miss rate's Wilson lower
bound is above the arm's pooled rate. No adoption decision hangs on this; it is a published table.

**Guard shipped with it (ON).** `chimera decisions refit` refuses to fit a Platt map on labels that
are perfectly separated by `p`: the ridge in `fit_platt` (prior 1e-4) does not stop the slope from
running to tens, and the "calibrated" map becomes a step to ~0 and ~1 — certainty the labels cannot
support. This is a defect fix, not a measured behaviour, so it ships on.

## 3 · Did the declared delegation happen? (arXiv 2609.38345)

**Census planned:** in `bench/hierarchy*` rows, whether the delegation a manager *declared* is the
delegation that ran.

**Known before reading:** only `bench/hierarchy_equal_calls/results/*.jsonl` stores per-run rows, and
its fields are `arm, docs, calls, passed, answer, …` — the harness fans out one worker per document
by construction; no model declares a plan. The census will therefore count (a) rows whose `calls`
differs from the design (`single_1`: 1; `hierarchy_no_synth`: docs; `hierarchy` and `single_equal`:
docs + 1) and (b) `hierarchy_no_synth` answers that do not carry one `### <task>-<i>` section per
document. Reading: **adherence by construction** if both are 0 — which says nothing about a
model-declared delegation, and the addendum will say so.

## 4 · URL-valid but content-invalid citations (arXiv 2610.00327)

`check_citations` (`chimera/core/research.py`) checks only that each cited URL was seen in the turn.
On `bench/web_research/results/run.json` (verified: each cited URL refetched, `holders` = cited pages
whose text contains the claimed answer), per arm:
- **URL-valid, content-invalid:** every cited URL seen (≥ 1 cited) **and** no holder;
- **URL-invalid, content-valid:** some cited URL unseen **and** ≥ 1 holder.

**Reading.** If URL-valid-content-invalid > 0 the URL check passes citations that do not hold the
claim, and a content check has a case to answer (built separately, OFF). If it is 0, no span check is
built. `holders` is page-level: a page can contain the answer while the cited passage does not, so a
page-level 0 is a **floor** on span-level invalidity, not a proof of none. Said in the addendum.

**Prediction.** 0 or 1 per arm: `RESULTS.md` already reports "a cited page holds the claimed answer"
at 71/72 per arm, so at most one turn per arm can be content-invalid at all.
