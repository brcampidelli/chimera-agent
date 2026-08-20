# Results — pilot, n=105

Run 2026-08-19 · judge `openrouter/deepseek/deepseek-r1` · seed 20260819 · US$ 0.38 · 100 minutes

**Read `PREREGISTRATION.md` first.** The sample, the metric, the decision rule and the conditions
for calling the result uninformative were all fixed there before the first model call.

## The number

| | |
|---|---|
| **Rejection recall** | **15.1%** — 8 of 53 incorrect comments caught · 95% CI [7.9%, 27.1%] |
| False rejection | 0.0% — 0 of 52 correct comments rejected · 95% CI [0.0%, 6.9%] |
| Overall rejection rate | 7.6% |
| Unparseable answers | 0 of 105 |
| Dropped (no diff) | 15 of 120 sampled |

**Verdict, by the rule fixed in advance: DOES NOT DISCRIMINATE.** The threshold was 30% recall to be
called weak-but-real, 60% to be called discriminating. This is 15.1%, and the upper bound of its
confidence interval (27.1%) does not reach the weak band either.

None of the four uninformative conditions fired: the answers parsed, and a 7.6% rejection rate is
not a constant — the judge does vary, it simply almost always approves.

## What this does and does not say

**It says**: on comments about a diff, with the diff in front of it, this judge accepts roughly six
of every seven false findings that frontier models produced. Its caution is real and one-sided — it
never rejected a correct comment in 52 tries — and that caution is where its recall went.

**It does not say** that fusion is broken. The judge's job in `chimera/fusion/engine.py` is to
compare several answers to the *same* question and report where they agree, contradict and miss;
this bench asks it to grade a single claim against code. They share the underlying capability —
telling a plausible-but-wrong assertion from a right one — which is why this is worth measuring, but
one is a proxy for the other and the proxy is now the only number we have.

**And it may be measuring the prompt.** The system prompt states the asymmetry deliberately — *"when
your evidence falls short, APPROVE"* — a rule taken from the open-code-review filter, whose authors
had already measured that removing a correct comment costs more than keeping a wrong one. A judge
told to approve under uncertainty, which then approves under uncertainty, has not been shown to lack
judgement. Separating the two needs a second arm without that instruction, and that arm needs its own
pre-registration: running variants until one scores well is fitting, not measuring.

## Per source model

Every arm is small (n≈15), so these are hints, not findings — and the pattern is one-directional
enough to note: comments written by Claude-4.5-Sonnet and Gemini-3-Pro were never rejected at all.

| comment written by | n | rejected |
|---|---:|---:|
| GLM-4.7 | 17 | 2 |
| Claude-Code/Claude-4.5-Sonnet | 16 | 0 |
| Deepseek-V3.2 | 16 | 1 |
| GPT-5.2 | 15 | 1 |
| (unnamed) | 15 | 2 |
| Qwen-Coder-480B | 14 | 2 |
| Gemini-3-Pro | 12 | 0 |

## Two defects in the harness, found while building it

Both were about measuring the wrong thing, and both would have produced a number rather than an
error:

1. **The diff was fetched from the pull request's current state.** 28 of 120 rows came back empty —
   and the silent half is worse than the drop: for rows that did resolve, the judge would have been
   shown today's version of a file and graded on a comment written about an older one.
2. **The dataset's field names invert the API's.** `pr_source_commit` is the PR's *base*, verified
   against `repos/…/pulls/N`. Compared the wrong way round GitHub answers with 300 unrelated files
   and the commented file is not among them; the right way round, 15 files and it is. That mistake
   alone took the usable sample from 9 to 105.

## Cost and what comes next

US$ 0.38 for 105 items — 76k prompt tokens, 130k completion (the judge is a reasoning model and
spends most of its tokens thinking). The full Diff Level slice, 1017 rows, projects to **US$ 3.66**.

The confirmatory run is worth doing, but not first. The cheaper question is whether the prompt's
approve-under-uncertainty instruction is producing this number, and that is a two-arm experiment on
this same pilot sample — pre-registered separately, with the threshold fixed before either arm runs.

---

# Arm B — the neutral stance, n=105 (same items, paired)

Run 2026-08-19 · same judge, same sample, same diffs · US$ 0.32 · 95 minutes

`PREREGISTRATION-arms.md` asked one question: was the pilot measuring the judge, or the prompt that
told it to approve under uncertainty? Arm B removes those three sentences and changes nothing else.

## Both arms

| | cautious (A) | neutral (B) |
|---|---:|---:|
| Rejection recall | 15.1% (8/53) | **18.9% (10/53)** |
| False rejection | 0.0% (0/52) | **3.8% (2/52)** |
| Overall rejection rate | 7.6% | 11.4% |
| Unparseable | 0 | 0 |

## The paired comparison, which is the point

| | count |
|---|---:|
| both arms caught it | 6 |
| only the cautious arm | 2 |
| only the neutral arm | 4 |
| **neither arm caught it** | **41** |
| discordant pairs | **6** |

Difference in recall, 95% CI: **[−4.5%, +9.1%]** — includes zero.

**This lands on the fourth uninformative condition, which was written down before the run: fewer
than 10 discordant pairs means McNemar has nothing to work with.** At n=105 this comparison does not
resolve the question, and 3.8 percentage points is not a result. Saying so is the whole reason that
condition was fixed in advance.

## What it does establish

**The instruction was not the bottleneck.** Removing "when your evidence falls short, APPROVE" moved
six items out of fifty-three, and **41 of the 53 incorrect comments survived both stances**. A prompt
that was holding the judge back would not leave that floor untouched.

So the pilot's 15.1% is a fact about the judge on this task, not an artefact of how it was asked. The
caveat `RESULTS.md` raised is answered — in the direction that makes the original number worse, not
better.

**The trade-off appeared, in the predicted direction.** False rejection went from 0 to 2 of 52. The
pre-registration named this exact possibility ("an instruction to weigh evidence carefully can raise
a model's willingness to reject correct findings"). Two items is far too few to call it, but it is
not in the direction that would have flattered the neutral arm.

## What would actually resolve it

Not more prompt variants — that road is fitting, and this arm already spent its one run. The floor of
41 missed comments is where the information is: they are on disk in both `details.jsonl` files, with
the judge's stated reason for each. Reading a sample of those, and asking whether the misses share a
shape (a kind of defect, a language, a diff too small to judge), is free and answers a question that
another US$ 3.66 of the full slice would not.
