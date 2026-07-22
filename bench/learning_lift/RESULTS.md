# Does accumulated learning actually help? — first measurement

Design, task order, metric and predictions were fixed in [`PREREGISTRATION.md`](PREREGISTRATION.md)
and pushed **before any model call**. Read the power caveat there before reading the null below.

## The run

| | |
|---|---|
| Date | 2026-07-22 |
| Model | `openrouter/mistralai/mistral-small-3.2-24b-instruct` |
| Suite | the 30 `fix_*` tasks, committed order, halves of 15 |
| Arms | `cold` (fresh agent home per task) vs `learning` (one home across all 30) |
| Grading | pristine test restored before every verdict |
| Timeout | 240s per task |

```
cold      1st half 100.0%   2nd half  86.7%   Δ -13.3%   overall  93.3%
learning  1st half  86.7%   2nd half  73.3%   Δ -13.3%   overall  80.0%

difference-in-differences (PRE-REGISTERED PRIMARY):  -0.0%
skills kept by the learning arm:                     31
grading integrity:                                   no arm modified its own test
```

## Verdict: null, and **uninformative** — the design hit a ceiling

The pre-registered primary metric is **exactly zero**. Both arms degraded by the same 13.3 points
across halves, so accumulation produced no detectable improvement.

The validity check *passed* — 31 skills were kept, so this is not the "no learning occurred to
measure" case the pre-registration warned about. Learning happened. It just did not show up.

**But the result cannot be read as evidence against learning, because there was no room to detect
it.** The `cold` arm scored **100% on the first half**: every one of the 15 tasks passed with no
learning at all. A benchmark whose control arm is already perfect cannot measure an improvement.

This connects to a finding from the same week: the `local_lift` re-run showed these tasks are far
easier for this model than the published record claimed (24 tasks failing both arms, not the 85
asserted — see [`../local_lift/RESULTS.md`](../local_lift/RESULTS.md)). The suite this bench borrowed
was chosen for *transfer opportunity*; it turned out to also be saturated.

The ceiling risk was named before the run, not after. That it materialised this severely was not
anticipated.

## The uncomfortable secondary reading — and it is post-hoc

The learning arm was **worse overall**: 80.0% vs 93.3%.

| | count | tasks |
|---|---|---|
| `cold` passes, `learning` fails | **6** | `fix_chunk_list`, `fix_clone_config`, `fix_most_common`, `fix_transpose`, `fix_group_by`, `fix_title_case` |
| `learning` passes, `cold` fails | **2** | `fix_pagination`, `fix_insert_pos` |

Exact McNemar, two-sided: **p = 0.29**. **Not significant.** With 8 discordant pairs this is entirely
compatible with noise, and it must not be reported as "learning hurts".

**This analysis was not pre-registered.** The pre-registration fixed the DiD as the primary metric;
comparing overall rates after seeing the data is exactly the freedom pre-registration exists to
remove. It is reported because omitting it would be worse, and it is labelled because it is worth
less than the primary metric by construction.

That said, the direction is worth a look rather than a shrug: **31 skills for 30 tasks** is roughly
one minted per task. If those cards enter the context of later tasks, they may be adding noise rather
than signal — which is the *negative transfer* the project's own README cites EvoAgentBench for as
the failure mode of ungated experience-encoding. This bench cannot distinguish that from noise. A
suite with headroom and a larger n could.

## What this does and does not establish

**Does:** the project now has a first measurement of a quantity that previously had none. The
machinery runs, skills accumulate, and grading integrity held on both arms.

**Does not:** it does not show that learning helps, and it does not show that it does not. n=30 in
halves of 15 was pre-registered as underpowered, and the ceiling makes it weaker still.

The README's claim — *"it gets better the more you use it"*, shipped in seven languages — remains
**unevidenced**. That is now a measured absence rather than an unexamined assumption, which is a
smaller gap but still a gap.

## Next experiment, and why

Same design, different suite: one where the `cold` arm lands around **40–60%**, so improvement has
somewhere to go, and a larger n so 8 discordant pairs are not the whole signal. The `fix_*` family
was chosen for shared structure and that reasoning still holds; it simply needs to be hard enough to
leave headroom. Until then, the honest statement is that the flywheel is built, gated, and
**unproven**.

Raw grid: [`results/learning.json`](results/learning.json).
