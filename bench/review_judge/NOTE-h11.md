# H11 on this bench — not run, and why

2026-09-25 · US$ 0 · no model calls.

Study 25 (`bench/PLAN-study25-system-prompts.md`, §9) lists **H11 — the review rubric raises precision
without collapsing recall** — and names this directory as its bench. After reading everything here,
the arm would repeat a measurement that already exists. The one part of the rubric that is new cannot
be resolved inside the US$ 1.00 cap this arm had. So nothing was spent, and this note records what
exists and what H11 would need instead.

## What H11 asks, in this bench's terms

This bench grades a **verifier**. The review comments are fixed inputs, each labelled correct or
incorrect by a human. The judge keeps or drops each one. For a filter like that:

- **precision** is the share of kept comments that are correct. Keeping everything gives the
  prevalence: 77.6% out of sample.
- **recall** is the share of correct comments kept, which is 1 − false rejection.

The plan's H11 row says "recall 15.1% today". That is the other recall: the pilot's rejection recall
on *incorrect* comments. Its confirmatory value is 17.0% out of sample (`RESULTS.md`, full slice). The
recall H11 promises not to collapse is the recall of *correct* comments: 92.4% for the shipped judge
out of sample.

## The study's review principles, applied to a verifier

| principle (§2.9, §7 S15) | what it becomes here | already measured |
|---|---|---|
| only issues the change introduced | reject what was there before the diff | arm E (pilot); inside arm C on the full slice |
| whose impact is shown — not praise, paraphrase or preference | reject a comment that asserts no defect | arm D (pilot); inside arm C |
| both together | | **arm C**, pilot and full slice, 814 items out of sample |
| evidence before the verdict | | arms C, D, E: a required `strongest_counterargument`, citing the diff, written before the verdict |
| that the author would fix | this is what the human label encodes. For the ~6 of 53 unfalsifiable pilot items it cannot be derived from the diff (the n8n#15057 pair) | a ceiling, recorded in `RESULTS.md` |
| finders report everything with confidence; never "only important"; findings first, P0–P3; an explicit "no findings" | there is no finder on this bench: the comments are inputs | cannot be measured here |
| a typed three-state verdict (confirmed / plausible / refuted) that quotes the line | a new output structure | **not measured** |

## H11, restated on the verdicts already on disk

`read_h11.py` → `results/h11_read.txt`. It makes no calls and is not pre-registered: it re-expresses
published verdicts in H11's terms. Wilson intervals as the runner computes them; the paired bootstrap
is resampled within label, with the seed and draws of `read_full.py`.

Out of sample (814 items, 632 of them correct):

| | keeps correct (recall) | precision of what is kept | catches bad |
|---|---:|---:|---:|
| keep everything | 100% | 77.6% | 0% |
| A — cautious, as shipped | 92.4% [90.1, 94.2] | 79.5% [76.4, 82.2] | 17.0% |
| C — introduced + no-defect | 56.5% [52.6, 60.3] | 84.0% [80.2, 87.2] | 62.6% |
| **C − A, paired** | **−35.9 pp [−36.8, −33.9]** | **+4.5 pp [+2.0, +7.1]** | +45.6 pp |

The pilot's 105 items are balanced, so their precision is reweighted to the slice's 74.1% prevalence:

| arm | keeps correct | precision at 74.1% |
|---|---:|---:|
| A (published counts) | 100% | 77.2% |
| B — neutral | 96.2% | 77.3% |
| E — introduced only | 73.1% | 76.6% |
| D — no defect only | 61.5% | 79.6% |
| C (published counts) | 61.5% | 81.7% |

**That is H11's question, already answered: the content of the rubric raises precision and collapses
recall.** Out of sample the trade is +4.5 points of precision for −35.9 points of recall. In the
pilot, the two grounds that bought precision (D, C) cost about 38 points of recall each. The one that
did not (E) lost both. Only the neutral stance (B) stayed near A on both axes, and its precision
moved by 0.1 point.

## Why the one new element was not run under the cap

The typed three-state verdict is the coarse form of "calibration instead of a verdict", which
`RESULTS.md` named as the next instrument. One call gives two operating points, declared in advance:
drop only `refuted`, or keep only `confirmed`. It is the only thing H11 could still add on this bench.
It cannot be answered for US$ 1.00:

- **Price.** `openrouter/deepseek/deepseek-r1`, the judge of every run here, is still served, at
  US$ 0.70/M prompt and US$ 2.50/M completion tokens. At the full runs' own token counts, an arm-A call
  costs US$ 0.0033 per item. A call that writes its evidence before the verdict (arm C's shape, which a
  quoting arm also needs) costs US$ 0.0051. US$ 1.00 buys about 115 pairs with both arms fresh, or
  about 195 new-arm items paired against the August verdicts.
- **What that scale can resolve, which this directory has measured.** Re-grading the same 105 items at
  temperature 0 flipped 8 of arm A's verdicts and 23 of arm C's, and moved C − A in J by 5.8 points
  with nothing changed but the run. Adapting §8's adoption rule to H11 (paired lower bound ≥ −2 pp on
  recall of correct comments) allows at most one discordant pair among the ~57 correct comments in a
  115-pair run. A's own replay flips about 8%.
- **The pilot items are in-sample.** Arm C was written from the misses on those 105 items, and any such
  rubric carries its grounds. So a cheap arm would need new items anyway. Pairing new items against the
  August verdicts breaks §8's same-session, pinned-endpoint rule, and the replay figures above show how
  much that costs.
- **The directory closed the prompt-variant road in writing, before seeing the outcome.**
  `PREREGISTRATION-grounds.md` says "This is the last variant", and `RESULTS.md` says "this is where
  prompt variants stop". A sixth wording on the pilot sample is what both documents refused.

## What H11 needs instead, and what it costs

1. **The verifier side: the three-state arm, out of sample, done properly.**
   - **Items:** the 814 out-of-sample items.
   - **Arms:** arm A re-run fresh, and the three-state arm, in the same session.
   - **Endpoint:** pinned. The gateway already sends `allow_fallbacks: false` when `provider_order` is
     set; the pin must be recorded in the manifest.
   - **Replay floor:** arm A re-run on about 200 items.
   - **Pre-registered:** the two operating points; precision and recall of correct comments at each;
     adoption means precision rises while the paired lower bound on recall of correct comments stays
     at or above −2 pp against A.
   - **Cost at today's price:** three-state arm ≈ US$ 4.2, fresh A ≈ US$ 2.7, floor ≈ US$ 0.7, so
     **≈ US$ 7.6**. That is about ten hours with the two arms in parallel, at the full runs' pace.
   - **Runner changes:** a three-state parser (with `unparsed` still kept apart from `call_failed`), an
     out-of-sample-only selector, and the provider pin in the manifest.
2. **The finder side: the principles that are the study's actual review rubric.** These are coverage
   and filtering as separate stages, never "only important", findings first P0–P3, and an explicit "no
   findings".
   - **What it needs:** a generation bench, in which a finder runs over the pull requests and its
     findings are matched to the labelled comments. aacr-bench can serve (Diff Level: 1017 comments on
     169 PRs, about 43k changed lines), or SWR-Bench, which the plan names. Neither harness exists here,
     and the matcher has to be built and validated before any arm.
   - **Rough order, an assumption rather than a quote:** about US$ 1.5 per finder arm per pass on r1,
     plus a matcher pass of similar size. That is roughly US$ 3–6 for two arms, before any floor.

Neither fits in US$ 1.00. Both need the owner's go-ahead: §9 says paid arms are "budgeted per PR".
