# Results — H11: a typed three-state verdict, out of sample

Run 2026-09-25 · judge `openrouter/deepseek/deepseek-r1` pinned to **Novita** (fp8), no fallbacks ·
814 out-of-sample items · temperature 0, reasoning at the model's default · **US$ 7.88** in all,
pilots and probe included (cap US$ 9.00) · three blocks, about 2.1 hours of calls

**Read `PREREGISTRATION-h11.md` first**, including its three amendments. The items, both prompts, the
two operating points, the metrics, the floor, the controls and the adoption rule were committed
before the first paid call (747415ad); every amendment was committed before the calls it governs.
The numbers below are `read_h11_three.py` → `results/h11/main/read.txt`, over
`results/h11/main/rows.jsonl`.

## Decision

**NOT ADOPTED at either operating point.** No uninformative condition fired.

| operating point | Δ precision (T − A) | Δ recall of correct (T − A) | rule |
|---|---:|---:|---|
| drop only refuted | −0.5 pp [−1.5, +0.5] | **+1.6 pp [−0.2, +3.5]** | precision does not rise |
| keep only confirmed | +0.8 pp [−0.5, +2.2] | **−3.5 pp [−5.9, −1.1]** | precision does not rise, and recall falls past −2 pp |

Paired bootstrap over 813 items (631 correct, 182 incorrect), resampled within label, seed 20260819,
10,000 draws. The McNemar–Wilson intervals agree: [−0.2, +3.1] and [−5.4, −1.1].

## Before any number: the guards and the controls

- **The statistic reproduces a published number, which is the positive control.** Fed the August arms
  A and C, the bootstrap returns `read_h11.py`'s +4.5 pp [+2.0, +7.1] exactly. On these 814 items an
  effect of that size is visible to this method. The rule does not fire on null deltas.
- **The rows are the registered items.** 814, unique, out of sample by the August rows' own keys; the
  replay arm on exactly the first 200 of the seeded order; 1,828 calls, every one served by Novita;
  prompt hashes `60a2749ed8c9` (A, the published one) and `5548fe16010b` (T).
- **Fresh arm A reproduces the published arm A (§2aa).** It keeps 93.7% of correct comments (August
  92.4%) and catches 17.6% of incorrect ones (August 17.0%). Both land inside the registered bands,
  and 761 of 814 verdicts (93.5%) match, on a different route and a different day.
- **Replay floor.** Arm A asked twice about the first 200 items in the same session flipped **13 of
  200 verdicts (6.5%)**: 9 of 159 correct, 4 of 41 incorrect. A2 − A: Δ precision −1.2 pp
  [−3.1, +0.5], Δ recall −1.9 pp [−5.7, +1.9]. The adoption rule does not fire on the replay.

## Both arms

| | precision of kept | recall of correct | catches incorrect | keeps |
|---|---:|---:|---:|---:|
| keep everything | 77.6% | 100% | 0% | 100% |
| **A — cautious, as shipped** | 79.8% [76.7, 82.5] | 93.7% [91.5, 95.3] | 17.6% | 91.1% |
| T, drop only refuted | 79.3% [76.3, 82.0] | 95.2% [93.3, 96.6] | 13.7% | 93.2% |
| T, keep only confirmed | 80.6% [77.5, 83.3] | 90.2% [87.6, 92.3] | 24.7% | 86.8% |

What T answered:

| | confirmed | plausible | refuted |
|---|---:|---:|---:|
| correct comments (632, one `unparsed`) | 569 (90.0%) | 32 (5.1%) | 30 (4.7%) |
| incorrect comments (182) | 137 (75.3%) | 20 (11.0%) | 25 (13.7%) |

**The middle state stayed almost empty.** T called 6.4% of comments *plausible*. The design rested on
that state: it was meant to hold the uncertain cases, so that *keep only confirmed* would drop them
and *drop only refuted* would keep them. With so little in the middle, the two operating points
bracket arm A closely. *Drop only refuted* is a slightly more lenient A: it rejects 6.8% of comments
against A's 8.9%. *Keep only confirmed* is a slightly stricter one. Three out of four incorrect
comments came back *confirmed*, the same failure `RESULTS.md` found in arms A and B: the judge
checks the comment's premise against the diff, where it should check whether the comment names a
defect.

**Separation did not improve.** The AUC of T's three ordered states is 0.575, against 0.556 for A's
binary verdict: Δ +1.9 points [−1.2, +5.1] (S1). ΔJ is −2.3 at *drop only refuted* and +3.7 at *keep
only confirmed*. Both are smaller than the −6.8 points that re-running A alone produced on the 200
replay items. Where A and T
disagree, it is mostly on the same few items: of T's 55 *refuted*, 36 are items A also rejected.

## Secondary

- **S3 — the quote is grounded, and grounding does not discriminate.** 701 of 706 *confirmed* quotes
  and 43 of 55 *refuted* quotes are lines of the diff shown; 10 *refuted* answers left the quote empty
  ("the code is not in this diff"), as instructed. The precision of *confirmed* with a found quote is
  80.5%, near the 77.6% prevalence. The model quotes real lines, and that does not mean it judged them.
- **S4 — unparsed counted as kept** changes nothing: the same intervals to the first decimal.
- **S5 — where the answers came from.** The route returned `content` empty on **353 of 814 A calls
  (43%)**, 305 of 814 T calls (37%) and 89 of 200 A2 calls. The answer was then read from the end of
  the reasoning field (Amendment 1). The verdicts on the two sides of that split look alike: A rejects
  9.8% from `content` and 7.6% from reasoning; T refutes 6.3% and 7.5%. Amendment 3's re-read moved
  two verdicts in all: one A `unparsed` → approve, and one T `unparsed` → confirmed. Both were
  final answers that were not valid JSON (`'\0'`; a regex full of backslashes).

## Predictions

| | predicted | measured | |
|---|---|---:|---|
| T confirmed / plausible / refuted | 55–80% / 10–35% / 5–15% | 86.8% / 6.4% / 6.8% | above / **below** / inside |
| drop only refuted: Δ precision · Δ recall | −1.0 to +2.0 · −3.0 to +3.0 pp | −0.5 · +1.6 | inside · inside |
| keep only confirmed: Δ precision · Δ recall | +1.0 to +6.0 · −35 to −10 pp | +0.8 · −3.5 | below · **above** |
| Δ AUC, T − A | +2 to +8 points | +1.9 | below (just) |
| replay flip rate | 5–12% | 6.5% | inside |
| fresh A keeps correct · catches incorrect | 88–96% · 10–25% | 93.7% · 17.6% | inside · inside |
| decision | not adopted | not adopted | right, for the wrong reason |

The decision prediction held, but its stated reason did not: *"the structure moves the threshold
more than the separation"*. The structure moved neither. Every missed band misses in one direction:
the model used *confirmed* even more than predicted, and the middle state even less. The prediction
for *keep only confirmed* expected a recall loss like arm C's (−10 to −35 pp). The loss was −3.5,
because there was almost nothing in *plausible* to drop.

## Cost, two-sided

- **Arm T costs 56% more per call than A:** US$ 0.00508 against 0.00326, from 1,820 completion tokens
  against 1,104 on average (medians 1,357 and 935). It buys no measurable precision.
- **One T call ran away** on the quote itself: 39,443 completion tokens over 28 minutes. The
  reasoning looped on how to escape a diff line holding `"` and `\n` inside the JSON `quote` field,
  and ended with empty content (the run's one remaining `unparsed`). OpenRouter billed it US$ 0.00.
  The token count prices it at US$ 0.099. Asking for the line verbatim inside JSON is a cost of the
  structure, not of the model.
- **Spend:** tokens × the listed price, US$ 7.877. That is US$ 7.513 for the main run, 0.177 for the
  second pilot, 0.1745 for the void pilot and 0.013 for the interface probe. OpenRouter's billed
  `usage.cost` equals the token price on every call except that runaway, so billed spend is about
  US$ 7.78.

## What happened to the apparatus on the way (the amendments, briefly)

1. **Empty `content` on this route (Amendment 1).** The first pilot lost 7 of 20 A answers and 4 of 20
   T answers to `content: None`, `finish_reason: stop`. That was the published prompt and parser, which
   had 0 of 919 unparsed in August. A four-call probe showed why: on Novita, deepseek-r1 sometimes never
   closes its reasoning, and the provider files the whole output, final JSON included, under
   `reasoning_content`. The harness now takes the last JSON object with a `verdict` from the reasoning
   when `content` is empty, for both arms, and hands it to each arm's own parser. The first pilot is
   void (`results/h11/pilot-void/`, never read).
2. **Literal tabs in quoted code lines (Amendment 2).** Strict JSON rejects them. The decodes now
   accept control characters. Re-parsing both pilots moved no verdict.
3. **A final answer that is not valid JSON (Amendment 3).** The recovery was stricter than the content
   path it stands in for, so the read now re-reads every empty-content call from its stored reasoning
   tail with a lenient recovery. That moved two verdicts.

The driver process also hung at interpreter exit after each block's work was written; it was ended
by hand between blocks. Nothing was in flight by then.

## What this cannot show

- **Other models, routes or days.** One model, one fp8 provider, one session. 43% of A's answers came
  through the reasoning field, a path the August run never used. That fresh A still reproduces August
  within a point or so is reassuring, but it does not make the two routes one instrument.
- **T's own replay floor.** Only A was replayed. T's variability is inferred, not measured.
- **Whether a different wording of the middle state would be used.** The prompt was frozen, and
  *plausible* was barely used. A version that makes the middle easier to choose is a new arm, with its
  own pre-registration. After arms B–E, `RESULTS.md` already records how that road goes.
- **The finder side.** The comments are fixed inputs (`NOTE-h11.md`, item 2).
- **Structure against content.** T keeps A's grounds. Whether three states would help arm C's grounds
  is untested.
- **The label ceiling.** About 6 of every 53 incorrect comments cannot be refuted from the diff
  (`RESULTS.md`), whatever form the verdict takes.

## What it establishes

- **H11, on the verifier side: a null, with power.** On 813 paired out-of-sample items, the typed
  three-state verdict with a quoted line does not raise precision at either declared operating point.
  The intervals exclude any precision gain above +2.2 pp. The instrument already resolved arm C's
  +4.5 pp on these items.
- **The shipped judge's number is stable.** 17.6% of incorrect comments caught and 93.7% of correct
  ones kept, fresh, on a pinned route, against 17.0% and 92.4% in August.
- **The failure is upstream of the verdict form.** Three incorrect comments in four are *confirmed*
  with a real quoted line. The judge validates the premise, as `RESULTS.md` found for arms A and B. A
  different answer format does not reach that. A different question (arm C) did, and at a price this
  directory has already measured.
- **An interface finding worth its own look.** On this route, 37–43% of deepseek-r1's answers
  arrive in `reasoning_content` with `content` empty. `LLMGateway._normalize` reads only `content`.
  Any Chimera path that sends a reasoning model to such a route would get an empty answer with
  `finish_reason: stop` about four times in ten, and no error. Not fixed here: this arm changes no
  product code.
