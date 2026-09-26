# Results — which cross-family reviewer `chimera review` picks by default

2026-09-26. Pre-registered in `PREREGISTRATION.md` (`e54591e5`), amended three times before the runs each amendment governs (`44128b09`, `0ff411d5`, `f3a77ea1`). Product `e44ca6c0` on top of `feat/review-command`; prompt hashes `3a480dab0478` (finder) and `ee9d780a9f4b` (verifier), fixtures `b2f9e29a5fd3`, the same as S15's. `results/report.txt` is the runner's own output. Every figure below comes from it, or from a hand read of `results/run.json`.

**Spend: about US$ 0.21** against a US$ 2.00 cap:

| part | US$ |
|---|---:|
| probes | 0.0001 |
| pilot 1 (discarded) | 0.0143 |
| M's pilot 2 (discarded) | 0.0010 |
| main run (D, L, Q, and M until it stopped) | 0.1947 |
| M's re-run | 0.0006 |

## Decision

**`chimera review` now reviews with `openrouter/openai/gpt-6-luna` by default, and with the default model when the author is from OpenAI.**
- L is the only candidate that meets the frozen rule, so it is the cheapest one that does.
- It meets the rule by the matcher, and by the hand read.
- The product change is the shape registered before the run (below).

## The numbers

k = 2 per arm, 30 items (20 seeded, 10 clean), every arm pinned to one OpenRouter provider with fallbacks off. Every call was served by the pinned provider. Wilson 95% intervals.

| | **D** deepseek-v4-flash-0731 (reference) | **L** gpt-6-luna | **Q** qwen3.7-flash | **M** mistral-small-3.2 |
|---|---:|---:|---:|---:|
| route | DeepInfra | OpenAI | Alibaba | Parasail |
| reviews run / failed on the route | 60 / 0 | 60 / 0 | 60 / 0 | stopped |
| **incomplete** | **0/60** [0.0, 6.0] | **0/60** [0.0, 6.0] | **3/60 = 5.0%** [1.7, 13.7] | not measured |
| **recall** (a seed hit, shown after the verifier) | **34/40 = 85.0%** [70.9, 92.9] | **39/40 = 97.5%** [87.1, 99.6] | **27/38 = 71.1%** [55.2, 83.0] | not measured |
| recall, hand read | 33/40 = 82.5% [68.1, 91.3] | 38/40 = 95.0% [83.5, 98.6] | not read (see below) | — |
| finder recall, before the verifier | 35/40 | 39/40 | 28/38 | — |
| recall with an incomplete counted as a miss | 34/40 | 39/40 | 27/40 = 67.5% | — |
| replica 1 / replica 2, shown | 18/20 · 16/20 | 20/20 · 19/20 | 14/19 · 13/19 | — |
| **clean diffs: findings shown, per 10 diffs** | **9.5** | **8.5** | **13.0** | — |
| clean diffs, before the verifier | 10.5 | 8.5 | 14.7 | — |
| verifier drops, of all findings it checked | 3/60 | 0/62 | 5/60 | — |
| **cost per review** | **US$ 0.00102** | **US$ 0.00101** | **US$ 0.00117** | — |
| tokens in / out | 182,828 / 279,201 | 185,741 / 83,659 | 207,975 / 491,829 | — |
| cache-read tokens | 55,040 | 54,597 | 34,304 | — |
| finder call, median | 32 s | **10 s** | 68 s | — |
| floor: replicas disagree on a shown hit | 6/20 | 1/20 | 2/18 | — |

- **Q's incomplete reviews:**
  - two were cut at the 32,000-token ceiling after 260–280 s, with an empty reply;
  - one closed a string early by writing `\\"index\\"`, which is malformed JSON from the model, not a parser defect.
- **Paired on replica 1, a shown hit (reported, not decided on):**
  - L against D: only D 0, only L 2, exact McNemar p = 0.5;
  - Q against D: only D 3, only Q 0, p = 0.25.

  Neither difference is resolved.
- **Positive control (§2aa).** D's replica-1 finder recall is 18/20, against S15's published 17/20. **Reproduced.**

## The rule, applied

Thresholds from D: recall ≥ 85.0% − 10 pp = **75.0%**; incomplete ≤ **5%**; clean findings ≤ 9.5 + 2 = **11.5** per ten diffs.

| | recall ≥ 75% | incomplete ≤ 5% | clean ≤ 11.5 | measured | qualifies |
|---|---|---|---|---|---|
| L | 97.5% ✅ | 0% ✅ | 8.5 ✅ | ✅ | **yes**, US$ 0.00101 a review |
| Q | 71.1% ❌ | 5.0% ✅ (at the bar) | 13.0 ❌ | ✅ | no |
| M | — | — | — | ❌ stopped twice | cannot be chosen |

**The hand read** was registered for D, the chosen candidate, and any candidate within one seeded item of a threshold.
- **L:** 38/40. One location hit is not the defect. On 5b3ae2bb, replica 2 reports that JSON's `NaN` passes validation, three lines from the seed. The seed is a Python file with NUL bytes being accepted.
- **D:** 33/40. On 8844b221, replica 1 reports a possible `AttributeError` on the seeded line. The seed is an inverted condition.
  - One borderline hit is counted, as S15's hand read counted the same one: on d8662bd6, replica 2 reports that `alpha * decisions` can exceed 1 and crash, not that the correction is inverted.
- By hand the threshold is 72.5%, and L's 95.0% still clears it. **The hand read changes nothing.**
- **Q** is two seeded items short of the recall threshold, so it is outside the one-item band, and it fails on clean findings regardless. A hand read can only remove hits.

## Predictions

| | predicted | measured | |
|---|---|---|---|
| P1 | D reproduces S15: finder recall in [14, 20]/20, incomplete ≤ 1/60 | 18/20, 0/60 | inside |
| P2 | M completes every review; recall more than 10 pp below D's; more clean findings | not measured | untested |
| P3 | Q's recall within 10 pp of D's; incomplete 0–15% | 13.9 pp below; 5.0% | **recall wrong**, incomplete inside |
| P4 | L's recall within 10 pp of D's, incomplete ≤ 5%, at 2–5× D's cost per review | 12.5 pp above, 0%, **0.99×** | **cost wrong** |
| P5 | at least one qualifies; Q if its incomplete stays ≤ 5%, otherwise L | L; Q's incomplete was 5.0% but it failed on recall and clean findings | **branch wrong** |

The cost miss has a mechanism. L's output price is 2.8× D's, but it wrote 3.3× fewer completion tokens (83,659 against 279,201), so a review costs the same. The runaway S15 measured on glm-5.3 is the same mechanism in the other direction.

## Against the reviewer the command picked before

S15 measured `z-ai/glm-5.3`, the top rung of the `auto` ladder, on the same set (other session, DeepInfra):

| | glm-5.3 (S15) | gpt-6-luna (here) |
|---|---:|---:|
| incomplete | 4/18 = 22% | 0/60 |
| cost per review | US$ 0.054 | US$ 0.0010 |
| finder call, median | 130 s | 10 s |
| recall over completed reviews | 11/11 | 39/40 |

These two columns come from different sessions and are not a paired comparison. Only the first three rows differ by more than the sets could hide.

## What the runs found in the product and the bench

- **Product defect, fixed (`e44ca6c0`).** The finder's JSON repair from `78c8eeae` read backslashes one at a time. So a reply holding both a valid `\\[` and a stray `\]` (a quoted regex) stayed unreadable, and the review came back incomplete.
  - Found by reading M's pilot reply by eye.
  - A test fails before the fix, and putting the old repair back turns only that test red.
  - Of 121 stored finder replies (S15's two runs and both pilots), exactly one reads differently after the fix.
- **M cannot be served the product's request by most of its routes.** The gateway always sends the 32,000-token completion ceiling.
  - OpenRouter removes DeepInfra and Venice (16,384 max) at "Filter by Context Length", and Mistral's EU route as a regional surcharge.
  - Parasail's shared pool is the only route left. It answered `429 … upstream_provider_shared_pool` often enough to stop M under the registered rule twice: 2/10 turns at five workers, then 8/10 at one worker.
  - **Unverified and worth checking:** Parasail's listing has no `tools` parameter. So a *tool* call to mistral-small at that ceiling may have no route at all, and mistral-small is the weak rung of the `cheap`, `balanced` and `auto` presets.
- **A misleading error.** When an upstream provider's shared pool rate-limits, the gateway reports "Every configured provider key is rate-limited … add another key". The key was fine; the provider's pool was not, and OpenRouter's reply says so (`limit_source`, `is_byok: false`).
- **A test that compared floats as text.** `test_the_json_form_is_one_object_in_the_declared_schema` compared the JSON report with `json.dumps` of the same data, as strings.
  - With the new reviewer's price, a fake review costs US$ 0.00004. pydantic writes that as `0.00004`, and `json.dumps` as `4e-05`.
  - The test now compares parsed values, which is what it asserts: the report round-trips.

## The product change

The shape registered before the run: a ranked list of measured reviewers, consulted after the flag and the setting and before the tier ladder.
- `chimera/review/family.py` gains `MEASURED_REVIEWERS = (gpt-6-luna, deepseek-v4-flash-0731)`. `choose_reviewer` takes it as `measured=` and reports the choice with source `"measured"`. The first entry whose family differs from the author's, and that the configured keys can call, reviews.
- `chimera/cli/review_cmd.py` passes the list. The ladder, the panel and the catalogue stay behind it, unchanged, for keys that call neither entry.
- `chimera/providers/catalog.py` gains a row for gpt-6-luna (mid, 0.10 / 0.50), so a review's cost is known from a cold start rather than printed as unknown.
- `tests/test_a_default_cannot_be_a_model_that_may_vanish.py` now counts the measured reviewers as defaults.
- **No flag.** The task was to change the default, and `CHIMERA_REVIEW_MODEL` or `--reviewer-model` still name any reviewer, glm-5.3 included.

## What this cannot show

- **Self-preference, the reason the family rule exists.** No diff here was written by any reviewer model.
- **Other authors.** Every item is scored as written by deepseek. The list's second entry, the default model, reviews gpt-6-luna's work; it was measured as a reviewer of these diffs, not of OpenAI's.
- **M.** It was never measured, so "cheapest qualifying" is among D, L and Q. M was the one arm that could have changed the decision: it cost about US$ 0.0003 a review before it stopped. What M did complete points the other way: 1 of 7, then 1 of 2, completed seeded reviews showed the seed. That is not a measurement.
- **Product routing.** L was pinned to OpenAI's standard route. In the product, OpenRouter may serve it from Azure, or from OpenAI's flex or fast tier, at half or double the price.
- **Determinism.** L's route takes no temperature, so it samples. Its replica floor here was the lowest of the three (1/20), and D's was 6/20 against 1/19 in S15.
- **Premium configurations.** The list is read in every cost mode, so `premium` stops reviewing with claude-opus-5 by default, on a measurement that did not include opus.
- **Natural defects, other languages, larger diffs, other days.** S15's limits, unchanged. Each seed is one line, hand-written and visible from the diff.
- **n.** Twenty seeded items. The rule screens for gross inferiority. L cleared its recall bar by 22.5 points, by the matcher and by hand; a margin of two items would not have meant much.
