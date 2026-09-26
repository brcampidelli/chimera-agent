# Pre-registration — which cross-family reviewer should `chimera review` pick by default?

**Registered 2026-09-26, before any model call.** Study 25, S15 follow-up (`bench/PLAN-study25-system-prompts.md` §2.9, §7 S15). The product is `feat/review-command` at `1108022a` (PR #630, not merged when this was written). Budget: **hard cap US$ 2.00**, expected about US$ 0.8.

## Why

`chimera review` picks its reviewer from a model family other than the author's (`chimera/review/family.py`). It reads the tier ladder strongest rung first. For the default configuration (`auto`, author `deepseek-v4-flash-0731`) that is `openrouter/z-ai/glm-5.3`.

`bench/review_seeded` (S15) measured that choice on 30 diffs of this repository, 20 of them carrying one hand-seeded defect:
- glm-5.3 reasoned to the 32,000-token completion ceiling and came back empty on 4 of 18 reviews (22%);
- it cost about US$ 0.054 per review, against US$ 0.0012 for deepseek;
- it took 130 s per finder call, against 52 s;
- deepseek found 17/20 seeded defects, and glm-5.3 11/11 of the reviews it completed, which is a difference McNemar could not resolve (p = 0.25).

So the rung the command picks runs away one review in five, at 44 times the price, for no recall the set could show. This bench measures three cheaper models from other families on the same set, and applies a selection rule frozen below.

## Setup

**Items, fixtures and harness: S15's, unchanged.** `run.py` imports `bench/review_seeded/run.py` rather than copying it.
- Fixtures: `bench/review_seeded/run.py --build` from git history. Their hash must equal S15's **`b2f9e29a5fd3`**. It does: checked when this file was written.
- Prompts: the product's finder and verifier, hashes **`3a480dab0478`** and **`ee9d780a9f4b`**, the same as S15's. Checked.
- The product's own `collect`, `review` and `CautiousVerifier` run each review, with the arm's model in both stages, as the command does.
- Scoring: S15's `hits` (seeded file, line within ±3), S15's `registered_order` (two seeded to one clean, on its own stream), S15's verified subset (the hits on a seeded diff, every anchored finding on a clean one, one finding per verifier call).

**Frozen prompt texts.** No new text. The finder and verifier are `tests/prompt_snapshots/review.finder.txt` and `tests/prompt_snapshots/review.verifier.txt`, byte for byte, at the hashes above. S15's pre-registration quotes the verifier in full.

**Arms.** Every arm is pinned to one OpenRouter provider with fallbacks off. Prices are that provider's, read from OpenRouter's endpoints listing (`/api/v1/models/<slug>/endpoints`) on 2026-09-26.

| arm | model | family | provider | US$/M in / out | why this one |
|---|---|---|---|---|---|
| **D** | `deepseek/deepseek-v4-flash-0731` | deepseek | DeepInfra | 0.06 / 0.18 | the reference: the author's own model, as in S15, same route |
| **L** | `openai/gpt-6-luna` | openai | OpenAI | 0.10 / 0.50 | the newest small model in the list (2026-09-22), from a vendor whose reasoning models bound their thinking |
| **Q** | `qwen/qwen3.7-flash` | qwen | Alibaba (its only provider) | 0.03 / 0.13 | the cheapest per token in the list |
| **M** | `mistralai/mistral-small-3.2-24b-instruct` | mistral | DeepInfra | 0.075 / 0.20 | no reasoning, so it cannot run away by construction; already the weak rung of three presets |

Two models from the list were left out:
- `z-ai/glm-5.3-flash` is the sibling of the model whose runaway motivates this bench. The catalogue also records it at 257 s against 72 s on a one-file probe. A third arm from the zhipu family would spend budget re-asking S15's question.
- `xiaomi/mimo-v2.6-flash` has the highest input price in the list and no measurement of any kind in this repository.

**Replicas and order.** Each item runs D₁ L₁ Q₁ M₁ D₂ L₂ Q₂ M₂, in S15's registered order, five items at a time. k = 2 for every arm, unless the pilot says it does not fit (below). The second replica halves the replica noise on each arm's point estimate, and its disagreement with the first is each arm's floor. At this project's ICC (0.706) it buys about 0.17 of an observation: the 20 seeded items, fixed by S15, are what bounds the resolution.

## Metrics

A **seeded review completes** when its status is `findings` or `no_findings`. It does not complete when it is `incomplete`, or when it halts (budget guard, stop rule, harness error).

- **Recall** (primary), per arm: seeded reviews that completed and show the planted defect, over seeded reviews that completed, pooled over both replicas. A review shows the defect when a finding hits the seed (seeded file, line within ±3) and the product's verifier did not drop it. That is what `chimera review` prints by default. Wilson 95% interval.
  - Reported beside it: finder recall (a hit located, before the verifier), recall with an incomplete review counted as a miss, and each replica on its own.
- **Incomplete rate**, per arm: reviews whose status is `incomplete`, over reviews that ran, both replicas, clean and seeded (60 when nothing halts). With a cause for each: reply cut at the ceiling, empty reply, unreadable reply, or call timed out (600 s).
  - **One exception, apparatus not model:** a finder call that raised a transport error other than a timeout (an HTTP error, "no endpoints", a dropped connection) is a failure of the pinned route. On a product route OpenRouter would try another provider. Such a review leaves both the incomplete rate and recall, and is counted and reported (`bench/PROTOCOL.md` §2).
- **False findings on clean diffs**, per arm: findings shown after the verifier on the 10 clean diffs. Computed per replica, scaled to ten diffs when some did not complete, then averaged over the replicas. A finding on a clean diff is presumed false (S15's caveat applies: one of these commits held a real defect). The count before the verifier is reported beside it.
- **Cost per review**, per arm: tokens × the pinned route's price, summed over every review that ran (including incomplete ones), over the number of reviews that ran. It includes the verifier calls.
- **Reported, not decided on:** finder latency (median), prompt, completion and cache-read tokens (§3), each arm against D paired on replica 1 with an exact McNemar test, and the floor.

## The frozen selection rule

**The chosen default is the cheapest candidate (by cost per review) with all three of:**
1. recall ≥ D's recall − 10 pp;
2. incomplete rate ≤ 5%;
3. false findings on the clean diffs ≤ D's + 2.

**If none qualifies,** the default reviewer falls back to the author's own family (D), with the reason written down.

Conditions on reading it:
- **Measured.** An arm counts only if the stop rule did not stop it and its replica 1 covered at least 16 of the 20 seeded items and 8 of the 10 clean items (reviews that ran and did not fail on the route). An unmeasured candidate cannot be chosen. If D is not measured, there is no decision.
- **The hand read validates the matcher.** A location hit can land near the seed without describing it (S15: 1 of 11 of glm-5.3's). For D and the chosen candidate, and any candidate within one seeded item of a threshold, every hit is read by hand against the seeded defect. A hit that does not describe it is removed. If that changes which candidate qualifies, the hand-read figure governs and both are reported. I will know the arm while reading.
- **Positive control (§2aa).** D's replica-1 finder recall must reproduce S15's published 17/20 within three items: [14, 20]/20. If it does not, the apparatus is not S15's, and no decision is read until an amendment explains why.

**What happens in product code** (shape chosen now, so the result cannot choose it):
- **A candidate wins.** `chimera review` gets a short ranked list of measured reviewers, consulted after the flag and the setting and before the tier ladder: the winner, then D's model. The reviewer is the first entry whose family differs from the author's and that the configured keys can call. The list holds two families, so every author has a cross-family entry. The ladder, the panel and the catalogue stay behind the list, unchanged, for users whose keys cannot call either entry. It is the list rather than a ladder rung because only a list says "this model was measured as a reviewer"; a rung was chosen for another job, and the rung the command reads today is the one that ran away.
- **None qualifies.** The default path returns the author's own model, with a note in the report that names this bench as the reason. The flag and `CHIMERA_REVIEW_MODEL` still name any reviewer.
- Tests change with it, and a sabotage shows each new test goes red.

## Instrument checks (before any call)

- `run.py --check` runs S15's check (every seed on an added line; oracle 20/20; ten lines off 0/20; silence 0/20, status `no_findings`), then asserts the fixtures and prompt hashes above. Passed when this file was committed.
- A dry run of the paid path with a fake gateway exercised, before this file was committed:
  - a reply cut at the ceiling;
  - transport errors tripping the stop rule;
  - an arm's budget guard;
  - the coverage condition;
  - the report and the decision.
- **Probe** (the first paid step, about US$ 0.001): one small call per route, straight to OpenRouter with its own cost accounting on. Does each pinned provider answer, and does tokens × the price above match what OpenRouter bills? A ratio off by more than 10% is recorded, and the price table is corrected in an amendment before the run.
- **Pilot** (the second paid step): the first three items of the registered order (two seeded, one clean), each arm once. It checks the interface (the finder's reply is JSON the product reads, and each route accepts the product's request) and measures cost per review. Three raw finder replies per arm are read by eye before any number (lessons §2e). **Pilot data are discarded**; the main run repeats those items.

## Predictions

- **P1.** D reproduces S15: finder recall in [14, 20]/20 on replica 1, and incomplete ≤ 1/60.
- **P2.** M (no reasoning) completes every review, and its recall is below D's by more than 10 pp. It shows more findings on clean diffs than D.
- **P3.** Q's recall is within 10 pp of D's; its incomplete rate is the uncertain part, 0–15%.
- **P4.** L's recall is within 10 pp of D's, with incomplete ≤ 5%. It costs 2–5 times as much per review as D.
- **P5.** At least one candidate qualifies. The winner is Q if its incomplete rate stays at or below 5%, otherwise L.

## Budget and stop rule

- **Guards per arm.** Every call is charged as it returns, and no call of an arm starts once that arm's running cost reaches its guard:

  | D | L | Q | M | total |
  |---:|---:|---:|---:|---:|
  | 0.15 | 0.75 | 0.25 | 0.10 | 1.25 |

  The worst overshoot is one call per worker in flight: five calls of at most US$ 0.016 (a 32k-token reply on L). With the probe and the pilot, the worst case is about US$ 1.45.
- **After the pilot, k is set by projection.** Take the pilot's cost per review × 60 for each arm. If every arm's projection fits its guard, k = 2. If not, guards may be moved between arms, so long as the pilot's spend plus the sum of the guards plus US$ 0.10 stays at or under US$ 1.90. If k = 2 still does not fit, the candidates run k = 1 and D k = 2. What was done is recorded in a dated Pilot section here before the main run.
- **Stop rule.** An arm whose finder calls raise a transport error (not a timeout) on more than 10% of its turns, after at least ten turns, starts no new turn. The other arms continue. The stopped arm is reported as not measured, never as failed.

## n, and what it can resolve

- **20 seeded items × 2 replicas.** One seeded item is 5 pp of recall. A Wilson interval at 85% on n = 20 is about [64%, 95%].
- **The 10 pp margin is two items.** The rule is a screen for gross inferiority, not a non-inferiority test with power. A candidate can pass it while being truly 15–20 pp worse than D. It can also fail it by two items of bad luck. The floor (replica disagreement) is printed beside it so the reader can see which.
- **The 5% incomplete bar allows at most 3 of 60 reviews.** A true 5% rate fails it 35% of the time; a true 10% rate passes it 14% of the time, and a true 15% rate 1.5%.
- **Clean diffs: 10.** "D + 2" over ten diffs is 0.2 extra findings per clean diff. The false-finding figure is a proxy, as in S15.

## What this cannot show

- **Self-preference, the reason the family rule exists.** No diff here was written by any reviewer model. The arms compare reviewers, not a reviewer grading its own family.
- **Other authors.** Every item is scored as if written by deepseek, the product default. The list's second entry exists for other authors, and is measured only as a reviewer of these diffs.
- **Product routing.** Each arm is pinned to one provider; a product call goes where OpenRouter sends it, possibly to a differently quantised route. L's price has three tiers on OpenAI (flex, standard, fast); the probe says which one served.
- **Determinism.** Four byte-identical T = 0 requests on one pinned route have produced four different replies (`bench/cache_confound`). L's route does not accept a temperature at all, so it samples.
- **Natural defects, other languages, larger diffs, other days.** S15's limits, unchanged.
- **Premium configurations.** The top rung of `premium` (claude-opus-5) is not an arm. If a candidate wins, the ranked list is consulted in every cost mode, so premium stops reviewing with opus by default on a measurement that did not include opus; `CHIMERA_REVIEW_MODEL` names it back.
