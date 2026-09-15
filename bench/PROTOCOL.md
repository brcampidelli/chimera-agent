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

**The route is part of the apparatus too, and "cache off" does not exist on a hosted one**
(2026-09-15, `bench/cache_confound`). Nothing we had published recorded which provider served a
solve or whether the cache answered; both are on every attempt receipt now (`provider`,
`cache_read_tokens`), and a measurement pins the route (`CHIMERA_PROVIDER_ORDER`, fallbacks off).
What the probe could not do is the paper's contrast: on a hosted route anything that defeats the
cache changes the bytes, and byte-identical requests are cacheable by definition — a nonce defeats
only the cross-run share of the system prefix (~6k tokens of a run's ~230k). And the baseline the
contrast needs is absent: **four byte-identical T = 0 requests, pinned to one route, three of them
served 99.3% from the same warm cache, produced four different first responses.** So a bench does not
assume T = 0 reproduces on a hosted route, does not attribute replica noise to sampling alone, and
does not let a component with its own temperature (the planner's hard-coded 0.2) sit upstream of a
"T = 0" worker without saying so — the first version of that probe did, and its cell was void.

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

⚠️ **The paraphrase half of this rule went unrun for two days after it was written, and running it
moved a number.** `bench/perturbation_floor` (2026-09-13) measured both halves of the governance
judge's floor in one session: **replay 20/20 = 1.000, paraphrase 23/26 = 0.885**, and one of the
three moves is an attack whose **BLOCK became a REVIEW because two spaces were added between shell
tokens**. A rule that is written and not executed is the §2t shape pointed at ourselves — so a
bench citing this section now says which half it ran.

And the direction was not the expected one: on the *ambiguous* corpus the paraphrase floor (0.902)
sits at or above the replay floor (0.886). Where the judge is already uncertain, rewording adds
nothing; where it is certain, rewording is where its instability lives. **A floor measured only on
hard items would have found nothing.**

⚠️ **The perturbation has to be verified before it is trusted.** Two of the four rewrites written
for that bench were discarded after their output was read: quoting the trailing argument turned the
redirection `0>&1` into a literal, and reversing what looked like a flag bundle turned
`find -name … -delete` into `find -eman … -eteled`. Neither fails loudly — each asks the judge about
a *different* command and reports the disagreement as a floor, inflating it, in the direction that
agrees with the paper. Print every (original, rewritten) pair before spending.

**A bias number is read beside a resolution number** (2026-09-15, `bench/judge_blind_prose`
item A3). 0% bias is also what a judge that stops choosing scores — arXiv 2609.12439 measured tie
rates going from under 1% to 31% under debiasing while the bias metric improved — so a judge bench
that reports "unbiased" reports, in the same table, how often the judge *committed*: chose one
candidate, hedged between two, or dropped the question. On the prose corpus that number was 180/180
and the 0/180 became a *resolved* null; without it the verdict could not have said so.

**A judge-scored comparison stores per-judge votes and reports disagreement in the band its arms
fall in** (arXiv 2609.12191). Judge disagreement is largest where two arms are close, which is
exactly where our effects live (ICC(1) 0.527 on the panel, 0.706 across arms). A reading whose arms
sit inside the band where the judges disagree with each other is a reading of the judges. As of
2026-09-15 **no bench in `bench/` stores per-judge votes over two arms**, so this is a rule for the
next one: keep the votes, print disagreement against separation, refuse the close band.

## 6. Anything added to a prompt has a placebo arm

An intervention that adds text — a skill card, a lesson, a checklist, a warning — is compared not
only to *nothing* but to **equally long irrelevant text** in the same slot (arXiv 2606.06454,
labels-only and placebo arms). Without it, "the card helped" and "more tokens in that position
helped" are the same number.

## 7. A split groups by whatever repeats, or it measures the repetition

Our corpora are built by running **the same task many times** — arms, replicas, seeds. A classifier
given a random split over that structure sees the same task on both sides and can score by
recognising the task instead of by the thing being predicted, and nothing in the number says so.
Any bench that fits a model **holds out the repeated unit whole** (task, item, tool root — whatever
the corpus repeats) and reports the grouped figure as the result. A random-split figure may appear
beside it, labelled as **the size of the leak**, never as performance.

The instance is measured and it is ours: in `bench/false_success`, one TF-IDF over the agent's own
completion claim scores **0.9342 random-split and 0.5996 leave-one-task-out** on the same 385 items
with the same hyper-parameters — because 11 of the 23 tasks have a label that never varies. The
published band this bench set out to reproduce (0.83–0.95, arXiv 2606.09863) lands inside the
leaky number and nowhere near the grouped one.

Two corollaries that the same bench paid for:

- **The gap is the diagnostic, not an embarrassment.** Reporting both numbers is what identifies
  the shortcut; reporting one leaves it invisible either way (Bee §2u).
- **The leaky arm is also the power check.** The same fitter reaching 0.93 when the task leaks is
  what proves a grouped 0.60 is the signal's ceiling and not an underfit model — the §2aa idea
  (one arm must reproduce a known-high number) applied to a classifier.

## 8. A replica is priced before it is bought

Repeated runs of the same task are correlated, so `k` runs are not `k` observations. Measured on
this project's own factorial with the shipped `icc1`: **ICC(1) median 0.706** across the eight arms
(0.53–0.77), against the 0.530 the source paper reported — ours is higher, and a correlation
imported from someone else's corpus would have understated it.

At that correlation, **three runs of a task carry 1.24 independent observations**: the second run
adds 0.17 and the third adds 0.07. Any bench choosing a `k` states what the `k`-th run is worth at
its own measured ICC, and `format_replicated_report` now prints it beside the k.

The rule that follows is about **where** the money goes, not about using fewer replicas — replicas
are how the noise floor is measured at all (§2x: one run is a sample, two alert, three decide), and
at k=1 there is no floor. So: **replicate a subset deep enough to measure the floor, and spend the
rest on more tasks.** `bench/design_effect/RESULTS.md` works it through on a real budget — 552
solves bought 28.6 effective observations per arm as 23 tasks × 3, where 8 tasks × 3 plus 45 × 1
would have bought 54.9 and still had a floor.

⚠️ **And the correction this rule does NOT license.** A design effect widens an interval only where
the interval was computed over the correlated trials themselves. Our replicated comparison pairs
arms on **per-task `pass^k`** — one observation per task however many times it ran — so no interval
in `eval/replicated.py` is inflated, and the sweep item that said otherwise was refuted by reading
the code (`RESULTS.md` §2). Check which unit the `n` counts before discounting it. One site is
genuinely un-clustered and is named there rather than silently corrected with a number measured on
a different population.

## 9. A claim about the binarised view is not a claim about the experiment

§7 says a split groups by whatever repeats. This is its sibling and it cost a retraction the same
day §7 was written.

**Thresholding a continuous outcome is choosing an instrument.** Whatever is then found — which
items are constant, which discriminate, how much power there is — is a property of *that* instrument.
It transfers to the original experiment only if the original used the same cut, and that has to be
checked in the analysis code rather than assumed from the corpus.

The instance is ours and it is embarrassing in the useful way. `bench/irt` binarised the factorial's
oracle score at 0.8 to fit a 2PL, found eleven of twenty-three tasks constant, and published that it
"reframes #453's null — the instrument had twelve live items". **`read_results.py` computes its main
effects on the CONTINUOUS score over all 23 tasks and prints `n_tasks=23` on every line.** Nine of
the eleven vary continuously; 042 spans 0.150–0.740 and 047 has fifteen distinct values. The twelve-
item instrument was the new bench's, not the factorial's.

Nothing in the fit changed — it still fails its shuffle control. What changed is every sentence that
read the binarised count as a statement about the experiment that did not binarise. So: **before
citing a derived view against an earlier result, open the earlier result's analysis and check which
DV it used.** One command would have done it.

## Standing rules this file collects rather than adds

- **Pre-register before the first call**, with the number the paper predicts written down so it can
  be wrong; amendments are dated and kept, never overwritten (`bench/blind_audit` carries two).
- **One run is a sample, two alert, three decide** (`replicated.py::seeds_verdict`); the dispersion
  is compared to the sampling error of the difference before the training is blamed (§2x).
- **Mechanism-active scoring**: an intervention reports how often it acted; zero active trials is
  *not measured*, never 0% (`ReplicatedArm.active`, §2r).
- **The instrument check runs first**: a corpus that cannot exhibit the effect is discarded before
  the first scored call (§2q; `bench/blind_audit/corpus.py::instrument_check` is the shape).
- **And the GRADER runs first too** (§2c #4), which this file required and `harness_bench` did not
  do: `bench/harness_bench/preflight.py` checks that every module a task's grader shells out to
  actually imports in the grading interpreter. It found one task whose `pytest` check failed on
  `No module named pytest` in **24 of 24 runs**, costing 0.25 of 0.95 available weight and making
  the task unpassable — read afterwards as a hard task. A missing tool does not fail loudly; it
  fails as a low score (`bench/harness_bench/DEAD-TASKS.md`).
- **`n` agreeing panel members are not `n` confirmations.** Measured on our own three models over
  50 items: 34 unanimous where 16.5 were expected, ICC(1) +0.527, **1.46 independent votes of 3**
  (`bench/panel_correlation`). A bound or a fraction over votes that share an input says so, or
  does not claim the denominator.
- **Every paired report reproduces a published number on at least one arm** before its difference
  is read (§2aa).
- **Nulls are published**, in the same file the positive would have gone in.
