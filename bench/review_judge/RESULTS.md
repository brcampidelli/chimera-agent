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

---

# Reading the 41 both arms missed

Three readers, independently, over the 41 incorrect comments that survived both stances — each with
the comment, the diff, and the judge's stated reason in both arms. They were given four categories
and told to invent more if the data called for it. Two of them did, and converged on the same two.

| category | n | what it means |
|---|---:|---|
| **reasoning failure** | 15 | the diff contained the refutation and the judge did not use it |
| **not a defect** | 12 | the claim is TRUE and verifiable — and it is praise, wording, scope or refactoring |
| **unfalsifiable claim** | 6 | "consider adding error handling" — nothing to refute |
| **external knowledge** | 3 | refuting it needs API semantics no amount of repository context supplies |
| **questionable label** | 3 | the reader defends the comment with the diff in hand |
| **insufficient context** | 2 | the judge genuinely could not have known |

## The hypothesis that died

**Context is not the bottleneck.** Two of 41 — and one reader found zero in its 14, after opening the
FULL patch for every ambiguous case to check whether the 60-line window had hidden the evidence. It
had not: what lay outside was either irrelevant or *supported* the comment.

Diff size predicts nothing. The three shortest diffs in one batch (9 lines) contain its two crudest
errors; another batch's misses have diffs *larger* than average (2145 chars vs 1524). Buying more
context — the obvious next spend — would have bought almost nothing.

## The bottleneck is the rubric, and the rubric is ours

Both arms share this instruction, written by us:

> Reject only when you can point at the reason: the code the comment describes is not in this diff,
> or a line of the diff contradicts its central claim.

There is no ground for **"true, but not a defect"** — which is 12 of the 41. Praise is perfectly
grounded in the diff and no line contradicts it; under this rubric, approving was *obligatory*. The
judge followed the instruction.

Worse for suggestions ("add a deprecation warning", "wrap this in try/catch"): the absence of the
suggested thing is the comment's **premise**, not its refutation, so the rejection condition cannot
fire by construction. One justification says it outright — *"the comment suggests adding a
deprecation warning, but the diff shows no warning logic"* — reading the absence as confirmation.

**The judge is validating the premise where the header asked it to judge the conclusion.** The footer
won, in both arms, which is a second explanation for why the two stances agreed on 47 of 53.

## What is genuinely the judge

Fifteen reasoning failures, and they are not subtle. A `substr(0, npos)` call flagged as a throw risk
when the position is 0. A nil-check demanded on a pointer the same diff already dereferences two
lines earlier. A missing space reported on a line where the space is visible. An `override` in the
signature making "may silently fail" impossible — with the judge repeating the speculation as its
own finding.

In three cases the judge **hardened** the comment beyond what it claimed, to support approving: one
asserts "the diff shows a comma without a space" where the diff shows the opposite. That reads as
verdict first, reason second.

The justifications localise correctly — real symbols, real line numbers, no hallucinated positions.
But across all 82 of them (41 items × 2 arms) **not one cites a line AGAINST the comment.** In at
least six, the justification states the disconfirming fact and approves anyway: *"remains unchanged
in the diff"*, *"without indicating a functional defect"*, *"the diff shows no Azure-related context
to validate this claim"*. Not a perception problem. A decision-rule problem.

## The finding that limits the obvious fix

One reader measured the FORM of the comments rather than their content: **81% of the CORRECT comments
and 74% of the incorrect ones are phrased as suggestions** ("consider", "should", "please"). Form does
not separate the classes.

And the pair that makes it concrete: in n8n#15057, same file, same authoring model, two generic
suggestions — *"consider wrapping this in a try-catch"* is labelled **correct**, *"consider optimizing
the spread operator"* is labelled **incorrect**. The judge answered both the same way, and the same
answer is scored as a hit once and a miss the other time.

**So for the ~6 unfalsifiable items, the label is contextual human agreement, not something derivable
from the diff.** No judge — no prompt, no model, no context budget — can get those right from what
this bench shows it. That is a ceiling on the instrument, and it belongs in the record next to the
15.1%.

## What to change, and what it can be expected to buy

Not another prompt variant chosen after the fact — that is fitting, and this directory has already
said so twice. What the data points at, to be pre-registered before it runs:

1. **Split the question in two.** "Is the premise true?" and "is this a defect?" are collapsed into
   one, and the second is the one that matters. Add a third rejection ground: *the comment asserts no
   defect (praise, paraphrase, preference)*, and *what it reports is pre-existing rather than
   introduced by this diff*. Reaches the 12 "not a defect" and some of the 15.
2. **Make the counter-evidence decisive, not optional.** A required `strongest_counterargument` field
   written BEFORE the verdict — the judge already writes the disconfirming fact spontaneously in six
   cases and approves regardless, so the gain is in making that sentence bind.
3. **Record the full reason.** `ask()` truncates at 200 characters and all three readers flagged it:
   they judged the first sentence, not the reasoning. If auditing justifications is the point, the
   truncation removes the evidence.

**Honest ceiling:** even if all three work perfectly, ~6 unfalsifiable items and 3 external-knowledge
ones are out of reach, and 3 more have labels a careful reader would contest. That is 12 of 53 that
nothing on our side can convert — a realistic best case near 75%, not 100%.

---

# Arm C — the rubric change, n=105 (same items, paired against A)

Run 2026-08-20 · same judge, same sample, same diffs · US$ 0.52 · ~110 minutes

`PREREGISTRATION-rubric.md` predicted 40–55% rejection recall and named one risk: that 81% of the
CORRECT comments in this slice are also phrased as suggestions, so a judge newly licensed to reject
preferences would be handed a rule that fits four fifths of the good ones too.

## The result

| | arm A (cautious) | arm C (split rubric) | paired |
|---|---:|---:|---|
| Rejection recall | 15.1% (8/53) | **60.4% (32/53)** | **+45.3 pp**, CI [+30.5, +48.4], significant |
| False rejection | 0.0% (0/52) | **38.5% (20/52)** | **+38.5 pp**, CI [+26.1, +38.5], significant |
| Overall rejection rate | 7.6% | 49.5% | |

Discordant pairs: 26 on the recall comparison (25 caught only by C, 1 only by A) and 20 on false
rejection. This comparison resolves, which arm B's six pairs did not.

**Net: +24 bad findings caught, −20 good findings destroyed.**

## Verdict, by the rule fixed in advance

**False rejection above 20% at any recall is a trade-off, not a gain. Both numbers are reported and
neither is an improvement.** The rubric in arm C does not ship.

The automatic label printed by the runner says "DOES NOT DISCRIMINATE", but that is the FIRST
pre-registration's rule (recall ≥ 60% *and* false rejection ≤ 20%) applied mechanically. The accurate
reading is different and worth stating plainly: the judge discriminated a great deal more, at a price
that was forbidden before the run.

## What it establishes, in both directions

**The reading's diagnosis was right.** The rubric was the bottleneck. Twenty-four of the 41 comments
that survived both earlier stances were caught the moment the judge was allowed to say "true, but not
a defect". A prompt-shaped explanation for a 15.1% floor is now evidence rather than a hypothesis.

**And the naive fix does not work.** Nearly one correct finding is destroyed per extra false positive
caught. The pre-registered risk was not a formality — it happened, in the shape and roughly the
proportion it was written down in.

**The prediction was wrong, high.** 40–55% predicted, 60.4% measured. The reading underestimated how
much the rubric was holding back, and said nothing about the magnitude of the cost — only its
direction. Recorded here because `PREREGISTRATION-rubric.md` is committed and timestamped, which is
what makes the error checkable rather than deniable.

## What the three arms say together

| | recall | false rejection |
|---|---:|---:|
| A — cautious | 15.1% | 0.0% |
| B — neutral | 18.9% | 3.8% |
| C — split rubric | 60.4% | 38.5% |

The axis is real and it is steep. What A and B measured was not the judge's ceiling; what C measured
is not a usable operating point. Somewhere between the two rubrics there is a boundary that separates
"praise and paraphrase" from "a suggestion that happens to be a real finding" — and this bench now
has the instrument to find it, plus the labelled sample to check it against.

## Next, and it needs its own pre-registration

C changed two things at once: it added a ground for *asserts no defect* AND a ground for
*pre-existing rather than introduced here*. The second is objective — a diff shows what it changed —
while the first is where the false rejections almost certainly come from, because a suggestion about
naming and a suggestion about a real bug look identical in form.

Splitting those two grounds into separate arms is the obvious next experiment, cheap at US$ 0.5 an
arm. It must be pre-registered before it runs: at three arms deep, the temptation to keep varying the
prompt until one lands under 20% is exactly the fitting this directory has refused twice.

**And the ceiling from the reading still stands**: ~12 of the 53 cannot be converted by any rubric —
6 unfalsifiable, 3 needing API semantics, 3 with labels a careful reader contests. Arm C's 32 caught
is already 78% of the 41 that are reachable at all.
