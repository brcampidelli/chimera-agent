# The protocol every bench under this directory follows

Written 2026-09-11 from six papers of the arXiv sweep (`PLAN-study17-arxiv-sweep.md`, item C7) and
from this project's own accidents, which are cited where they happened. A `PREREGISTRATION.md`
that does not say which of these it satisfies, and how, is not finished. The rules are numbered so
a pre-registration can point at them.

Every rule below has the same shape: a way a number comes out looking like a result when the
instrument could not have produced one. The question that opens each of them is the one from the
lessons file — *what could this apparatus not show?*

## 1. Probe the wall before scoring anything

The agent under test must be shown unable to reach the reference solution, the grader, the hidden
tests and the network the task forbids — **by a probe that tries, before the scored run**, not by
an assumption in the harness (SaltBench, arXiv 2609.11076: the wall is tested by breach probes
before any scored run). `bench/swe_bench/PLAN.md §3.2` bans test leakage; nothing before this rule
tried to leak. A bench that cannot run the probe says so in its pre-registration.

*Where it stands:* the probe is a pre-registration requirement from this date; `harness_bench`
carries the first one when it runs.

## 2. A halt is not a failure

A trial that stopped short — budget cap, wall-clock timeout, provider outage, harness error — is
neither a pass nor a fail. It leaves every denominator; a task whose every run halted is
**unmeasured** and named, never scored 0%; a task unmeasured in either arm leaves the pairing
(`chimera/eval/replicated.py`, `ReplicatedArm.halted`, from this date). The learning-lift series
paid for the absence of this rule once (run 7a, a swallowed timeout read as capability loss) and
`bench/fusion_paired` again (two of arm A's misses were the clock).

## 3. The cache is part of the apparatus

Prompt caching moves cost by 36–75% (arXiv 2609.04748) and changes latency, and a provider caches
what it decides to. A local bench either **fixes** cache state (cold every trial, or warm every
trial, and says which) or **reports** the cache-read tokens per arm beside the totals, as
`bench/hierarchy_multistep` does. A cost comparison that cannot say what was cached is a comparison
of two unknowns.

## 4. The interface is measured before the model is

A tool bench measures the adapter first and the model second (study 16). Before any tool-calling
number is read, a preflight shows that the provider **returns tool calls in the shape the adapter
parses** — an empty `tool_calls` with a `finish_reason` of `stop` is an adapter reading, not a
refusal (arXiv 2609.03966, interface censoring). `tests/test_the_adapter_returns_the_tool_call_it_was_given.py`
pins the shapes; a live bench states which preflight it ran.

## 5. A judge is read only after its own floor

An LLM judge is a ruler, and a ruler is measured before it measures: the same verdicts **replayed
the next day** (arXiv 2609.04198) and under a **semantics-preserving paraphrase** of the item
(2609.09703, and 2608.22331's 11–58× finding that paraphrase noise exceeds rerun noise) give the
judge's self-agreement, which is the floor under every difference it reports. `bench/review_judge`
reports its floor; a bench that scores with a model states the floor it measured or says it has
none.

## 6. Anything added to a prompt has a placebo arm

An intervention that adds text — a skill card, a lesson, a checklist, a warning — is compared not
only to *nothing* but to **equally long irrelevant text** in the same slot (arXiv 2606.06454,
labels-only and placebo arms). Without it, "the card helped" and "more tokens in that position
helped" are the same number.

## Standing rules this file collects rather than adds

- **Pre-register before the first call**, with the number the paper predicts written down so it can
  be wrong; amendments are dated and kept, never overwritten (`bench/blind_audit` carries two).
- **One run is a sample, two alert, three decide** (`replicated.py::seeds_verdict`); the dispersion
  is compared to the sampling error of the difference before the training is blamed (§2x).
- **Mechanism-active scoring**: an intervention reports how often it acted; zero active trials is
  *not measured*, never 0% (`ReplicatedArm.active`, §2r).
- **The instrument check runs first**: a corpus that cannot exhibit the effect is discarded before
  the first scored call (§2q; `bench/blind_audit/corpus.py::instrument_check` is the shape).
- **Every paired report reproduces a published number on at least one arm** before its difference
  is read (§2aa).
- **Nulls are published**, in the same file the positive would have gone in.
