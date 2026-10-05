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

**An LLM grader reads one item per call** (2026-09-15, study 19 item A4, arXiv 2609.09696). An
auditor's recall went **50% on one document → 60% on a small batch → 2.8% on a large one**, and it
failed by *confident fabrication* — verdicts for items it had not read — not by degradation. The
census before any re-grade: every model-scoring call site in `bench/` (the blind audit, the
governance judge, the review judge, the recognition probe, the three `judge_blind` runners, the
fusion panel) and in `chimera/` (`envelope_verify`, `strong_verify`, `rubric`, `supervisor`,
`checklist`, `spec_test`, `verifier_select`, `draft`, `continuous`) sends **one item per call**;
batched grading never entered the apparatus, so there is no stored round to re-grade at batch 1
and the paper's regime cannot have produced any number we hold. The two places a model reads
several items in one prompt — the fusion judge over N candidates and the hierarchy's synthesis
over N envelopes — are comparisons by design, and the shape the paper describes would surface there
as propagation or omission, which `judge_blind_prose` measured at 0/180 with N = 2 and N = 3. The
rule this leaves: a grader that batches is a registered deviation, and it re-grades a sample at
batch 1 from the stored item before its number is read.

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

## 10. Every multi-agent arm has a single-agent arm at equal cost

A mode that runs more than one agent on a task — hierarchy, crew, lifecycle, fusion, a reviewer, a
Manager — is compared to **one agent given the same budget**: the same number of model calls, or
the same US$, stated in the pre-registration with which of the two it holds. Where the multi-agent
arm buys independent attempts, a second control resamples the single agent the same number of
times. A comparison against a single agent that made fewer calls measures the extra calls, not the
structure.

The instance is ours: `bench/hierarchy` and `bench/hierarchy_multistep` compared the hierarchy to
one call, and read as a quality-neutral token story; `bench/hierarchy_equal_calls` gave the single
agent the hierarchy's calls and the hierarchy did not beat it — `pass^3` −26.7 pp [−36.2, −4.2] on
thirty tasks, a 3B backbone on every role, read as a direction because the point estimate sits
inside the flip floor. That bench held *calls*, not tokens: re-reading the documents, the single
agent spent 13,611 tokens per task against the hierarchy's 1,962, about 7×, so the multi-agent arm
was the cheaper one in tokens and equal-token comparison is still unrun.
The papers agree for the same reason: at equal calls a Planner-Executor-Critic team did not beat
one agent (arXiv 2609.04217, 0.769 vs 0.754, p = 0.80), and debate tied or lost to self-consistency
at matched budget (2609.35875). As of 2026-10-02 only `hierarchy_equal_calls` meets this rule;
`fusion_paired` registered it and stopped at ceiling, and crew and lifecycle have no bench. A
multi-agent mode without such an arm is *unmeasured*, whatever it scored against one call — the
policy that follows from this is `docs/multi-agent-policy.md`.

## 11. The interval is chosen by the data's shape, never by habit — and never a percentile bootstrap under N = 100

*Added 2026-10-05, study 30 (S30-34).* A percentile bootstrap over a handful of tasks looks rigorous
and is not: arXiv 2609.35815 (evalstats) measures it covering **88%** at a nominal 95% with N < 100,
and on paired binary data no bootstrap variant reaches nominal even at N = 100. Six of our readers
used one, four of them at N = 7–34. So the method is fixed by what is being compared, and every one
of them is a closed-form function in **`chimera/eval/proportions.py`** — one home, inside the
mutation gate, which `tests/test_stats_helpers_have_one_home.py` keeps the only home:

| comparing | interval | function |
|---|---|---|
| one proportion | Wilson | `wilson` |
| two independent proportions | Newcombe hybrid score | `newcombe_unpaired` |
| two proportions on the **same** items | Bonett-Price adjusted Wald; exact McNemar for p | `bonett_price_paired`, `mcnemar_exact` |
| a mean of per-task differences (continuous) | one-sample t | `mean_t_interval` |
| two independent groups of such means (an interaction between strata) | Welch t | `welch_t_interval` |
| a median of small integers | binomial order statistics | `median_interval` |
| a difference of two independent estimates (e.g. ΔTPR − ΔFPR) | MOVER | `mover_difference` |
| one AUROC | Hanley-McNeil | `auroc_hanley_mcneil` |
| an equivalence or non-inferiority claim | TOST on the `1 − 2α` interval (§12) | `tost_paired`, `tost_unpaired`, `tost_mean` |

The paired row is the one that bit us. Until this amendment `chimera/eval/paired.py` printed a Wilson
interval on the discordant pairs scaled by the observed share of discordant pairs, as if that share
were known. Its yes/no test was roughly calibrated; the interval it printed covered a real difference
**41–88%** of the time and returned `[0, 0]` when the arms agreed on every pair
(`tests/test_the_paired_interval_covers_the_difference.py`). Bonett-Price is what it prints now. The
two sweeps of 2609.35815 disagreed on whether the paper attaches the Bonett-Price name to paired
*binary* or paired *continuous* data; the choice here does not rest on either reading but on our own
coverage simulation, in which Bonett-Price held 93% or more in every cell and the old interval fell
to 41%.

A bootstrap may still appear **beside** a closed-form interval, labelled as a cross-check, or above
N = 100 where the registration says why. It does not decide. And the drift this rule ends is
measured: three of six copies of Newcombe's paired interval had dropped his continuity correction to
phi and printed **0.0182–0.2892** on his own worked example, where the paper prints 0.0112–0.2954.
`bench/interval_reread` re-read 56 published intervals printed by the retired methods: all 56
reproduced first, two crossed their criterion — the `harness_bench` checklist × tercile interaction
(correction published there) and `learning_lift` run 6's "significant" transfer, already retracted.

## 12. "No difference" is a claim with a margin, declared before the run

*Added 2026-10-05, study 30 (S30-34).* An interval that spans zero is **not** evidence of equivalence
(arXiv 2610.00047): it is what a bench too small to see anything also prints. A pre-registration that
expects "the same", "no worse", "non-inferior" or "safe to simplify" declares, **before the first
call**, the margin it would accept and which of the two it claims — equivalence (both sides) or
non-inferiority (one side) — and the reading is a TOST: the `1 − 2α` interval inside the margin
(`tost_paired`, `tost_unpaired`, `tost_mean`). Without a declared margin the only honest summary of a
null is its interval and what it could have seen; "the factor does nothing" is not available.

`bench/chat_history` is the model: it declared −10 pp before running and missed it by 0.2 pp.
`bench/harness_bench`'s "simplify" was not — it read three spanning intervals as a null with power,
and is re-read against a margin it never declared in `bench/interval_reread`.

## 13. The controls a number needs before it means anything

*Added 2026-10-05, study 30 (S30-34).* Each is a way a score arrives without the capability being
present. A pre-registration says, for each that applies to its bench, that it runs the control or why
the control cannot move its number.

- **A trivial-agent arm.** An agent that does nothing, or returns the most common answer, is scored
  by the same grader (ABC, arXiv 2507.02825: a do-nothing agent scores 38% on one airline benchmark).
  The floor it reaches is the zero of the scale.
- **A random arm at matched cost** for any selection mechanism — a router, a pruner, a reranker
  (arXiv 2609.05933 for pruning multi-agent teams). For routing specifically, the random arm matches
  the **share** of traffic each model receives, not just the total spend (arXiv 2608.14641), or the
  comparison measures the mix.
- **A grader-hijack probe.** The task tree a model can write to must not be able to change the
  verdict: a probe writes a `conftest.py` (or the grader's equivalent) that forces a pass, before
  any scored run, and the bench shows the verdict did not move (BenchJack, arXiv 2605.12673). This is
  §1's wall, for the grader.
- **Attack success read on the arguments, not the tool name.** A governance or injection bench that
  counts an attack as succeeding when a sensitive *tool* is called reports what the paper calls
  identity scoring — 21.7% where the true rate, read on whether the payload reached the arguments,
  was 1.2% (arXiv 2609.32691). The predicate states what the attack had to put where.
- **A detection probe beside any counterfactual judge score** (arXiv 2610.00111): when a judge is
  shown an altered item and its score moves, a second probe asks whether it noticed the alteration;
  a score that moved without detection is read as the judge's sensitivity to surface, not to content.
- **Format-only and same-length placebo arms** for anything that adds a skill, card or lesson (arXiv
  2607.02595), extending §6: one arm carries the same text re-formatted to the intervention's shape
  without its content, one carries irrelevant text of the same length.
- **A rule-withdrawn arm for instruction-following** (Harness-IF, arXiv 2608.11727): the score
  counts only rules that go **against** the model's default, and an arm with the rule withdrawn shows
  how often the model does the thing anyway. A rule the model already follows measures nothing.

## 14. A component is removed only on evidence from two model families

*Added 2026-10-05, study 30 (S30-34).* One harness change moved two models in opposite directions
(57.1 → 30.2 and 49.2 → 60.3; arXiv 2610.00917), and a scaffold lift measured on a weak model was
+11.6 points and about nothing on strong ones (2609.20804). So a verdict that recommends **removing**
or **defaulting off** a component — "simplify" — names the model it was measured on in the same
sentence, and does not ship as a default until a second model **family** (not a second size of the
same family) has been measured with the same registration. `bench/harness_bench`'s nulls are labelled
"measured on deepseek-v3.2" for this reason; this rule makes that the requirement rather than the
courtesy.

## What a pre-registration written after 2026-10-05 must contain

§11–§14 are enforced at the file level: `tests/test_a_preregistration_answers_the_protocol.py`
fails on a `PREREGISTRATION*.md` that does not name each of **§11**, **§12**, **§13** and **§14** —
the interval it will read, the margin (or "no equivalence claim"), the controls that apply and those
that do not with the reason, and the model scope. Answering "not applicable, because …" is an answer;
silence is not. The registrations written before this date are listed, frozen, in
`bench/PREREGISTRATIONS-before-protocol-11.txt`; that list may shrink, never grow.

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
