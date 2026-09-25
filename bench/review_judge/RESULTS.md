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

---

# Arms D and E — which ground carries the cost

Run 2026-08-20 · same judge, same 105 items, same diffs · US$ 1.05 for both, run in parallel

`PREREGISTRATION-grounds.md` split arm C's rubric in half: D keeps only *asserts no defect*, E only
*pre-existing rather than introduced here*. It predicted D at 45–60% recall / 25–40% false rejection
and E at 5–20% / 0–8%.

## Five arms

| arm | extra grounds | catches bad | destroys good | rejections |
|---|---|---:|---:|---:|
| A cautious | — | 15.1% | 0.0% | 8 |
| B neutral | — | 18.9% | 3.8% | 12 |
| E pre-existing only | 1 | 35.8% | 26.9% | 33 |
| D not-a-defect only | 1 | 54.7% | 38.5% | 49 |
| C both | 2 | 60.4% | 38.5% | 52 |

**Neither ground is adoptable**: both exceed the 20% false-rejection ceiling on their own.

**The E prediction was wrong on both numbers, badly.** Predicted 5–20% recall at 0–8% false
rejection; measured 35.8% at 26.9%. The reasoning behind that prediction — that "pre-existing" is
objective, because a diff shows what it changed — did not survive contact.

## The grounds are not independent

Of the 19 incorrect comments E catches, **17 are also caught by D**. D alone reproduces C's false
rejection exactly (20 of 52). Adding the second ground to D bought 3 more catches and cost nothing
further.

And when the reasons E gave for destroying correct findings are read: **only 4 of the 14 invoke E's
own ground**. The rest reject for other reasons entirely — one of them by CONTRADICTION, a ground
that already existed in arm A, where that same comment was approved.

So the rubric is not a menu of rules applied case by case. Widening it moved something global.

## What moved: the threshold, and — separately — the separation

Rejections scale with how permissive the rubric is, almost regardless of which ground was added:
8 → 12 → 33 → 49 → 52 as grounds go 2 → 2 → 3 → 3 → 4. That looks like a threshold shift, and a
threshold shift alone would be worthless: any judge can trade recall for precision by lowering its
bar, and the pilot could have had 60% recall on day one by rejecting half of everything.

Youden's J (TPR − FPR) separates the two effects. Zero is chance; a pure threshold move leaves it
flat:

| arm | TPR | FPR | **J** |
|---|---:|---:|---:|
| A cautious | 15.1% | 0.0% | **+15.1%** |
| B neutral | 18.9% | 3.8% | **+15.0%** |
| E pre-existing | 35.8% | 26.9% | **+8.9%** |
| D not-a-defect | 54.7% | 38.5% | **+16.3%** |
| C both | 60.4% | 38.5% | **+21.9%** |

Three findings, and they only appear at this level:

1. **A and B are the same judge at the same separation.** J of 15.1 and 15.0 — the neutral stance
   moved nothing at all. Arm B's inconclusive six discordant pairs now have a clean explanation.
2. **E made the judge WORSE than doing nothing new.** J drops to 8.9. It rejects more, and the extra
   rejections are barely better than coin-flips. An objective-sounding ground that the judge mostly
   does not use, while rejecting more overall, is the worst of both.
3. **C genuinely separates better** — J 21.9 against A's 15.1. The rubric change was not only a
   threshold move; the judge really did tell the classes apart better with both grounds present. It
   is still unusable at 38.5% false rejection, but the improvement is real and is the first evidence
   in this directory that anything we wrote made the judge *better* rather than merely louder.

## Verdict

By the pre-registered rule: no ground ships. And the pre-registration named what follows —

> If neither ground is adoptable, the answer is that a prompt cannot separate substantive suggestions
> from stylistic ones at this sample size, and the next move is a different instrument, not a fifth
> wording.

**That is the finding, and this is where prompt variants stop.** Five arms, US$ 2.60, and the honest
summary is: the judge's separation can be improved by rubric (15.1 → 21.9 J), no rubric tested puts a
usable operating point within reach, and one ground that looked objective made things worse.

## What a different instrument would mean

Not a fifth wording. The candidates the data actually points at:

- **Two calls instead of one.** The single call collapses "is the premise true" and "is this a
  defect"; asking them as separate calls, with the second seeing the first's answer, is a structural
  change rather than a rhetorical one.
- **Calibration instead of a verdict.** A confidence score would let the operating point be chosen
  after measuring, on the curve, rather than being baked into the wording — which is what every arm
  here has been doing blind.
- **A larger sample before any of it.** At n=105 with 53 incorrect comments, a 10-point difference
  in recall is roughly the width of the confidence interval. The full slice is 1017 rows and US$ 3.66;
  after five arms at US$ 0.5, the cheap thing has stopped being cheap relative to what it buys.

---

# The full slice — arm C out of sample (the pre-registered read)

Runs 2026-08-20 · same judge (`openrouter/deepseek/deepseek-r1`), same prompts, temperature 0 · arm A
8.3 h, arm C 11.7 h · read 2026-09-24 from the files on disk, no model calls, US$ 0 ·
`read_full.py` → `results/full_read.txt`

`PREREGISTRATION-full.md` fixed three things before either arm ran: every result reported three
times — over all rows, over the **105 items arm C's rubric was written against**, and over the rest;
the out-of-sample figure as the headline; and a clause — *if C's advantage over A shrinks materially
between the two, the rubric was fitted to the pilot's misses and the reading of the last five arms
changes, retroactively.*

Both arms finished on 2026-08-20. Their numbers went into two commit messages (5787f481 for A;
26af6e42 for C, inside an unrelated PR) and never into this file, and the in/out split — the thing
the run existed to measure — was never computed. The second message was headed "arm C out of
sample", but the C − A it quoted (+9.4) was over all 919 rows, and subtracted from rounded rates; the
exact all-rows figure is +9.3. This section is the read that was registered.

## Before any number

- **The guard.** `read_full.py` rebuilds both `summary.json` files from `details.jsonl` — thirteen
  fields each, all identical — and reproduces the paired interval this file published for the pilot's
  arm C ([+30.5, +48.4]). Nothing below is read unless both hold. Both hold.
- **`row_id` is 0 on every row.** The dataset has no `__index__` field, so the runner's id is a
  constant. Items are keyed by content (repo, PR, path, lines, comment, label) instead, and every key
  is unique.
- **The in-sample mark is the pilot's 105, exactly.** The runner's `in_pilot` flag matches the items
  graded in the three pilot arms still on disk, and in the pilot's own arm A and arm C files, which
  the full run overwrote in place and which survive in git (3ba341bf, 029e89f6).

## Survival

| | planned | graded | dropped (no diff) | incorrect | correct |
|---|---:|---:|---:|---:|---:|
| all | 1017 | 919 | 98 | 235 | 684 |
| in sample | 105 | 105 | 0 | 53 | 52 |
| out of sample | 912 | **814** | 98 | 182 | 632 |

All 105 in-sample items survive; the out-of-sample half is **814 of 912**, not 912. Fifteen of the 98
dropped rows are pilot draws that had no diff in the pilot either — never graded, never read by
anyone designing a prompt, so out of sample by the pre-registration's own definition (checked against
the local dataset cache, which is not committed). The 700-row condition does not fire: 919 survived,
and 814 out of sample alone.

## Both arms, three ways

Wilson 95%. J = rejection recall − false rejection, in points.

| | arm | rejection recall | false rejection | reject rate | J |
|---|---|---:|---:|---:|---:|
| all | A | 17.9% (42/235) [13.5%, 23.3%] | 7.5% (51/684) [5.7%, 9.7%] | 10.1% | +10.4 |
| all | C | 62.6% (147/235) [56.2%, 68.5%] | 42.8% (293/684) [39.2%, 46.6%] | 47.9% | +19.7 |
| in sample | A | 20.8% (11/53) [12.0%, 33.5%] | 5.8% (3/52) [2.0%, 15.6%] | 13.3% | +15.0 |
| in sample | C | 62.3% (33/53) [48.8%, 74.1%] | 34.6% (18/52) [23.2%, 48.2%] | 48.6% | +27.6 |
| **out of sample** | **A** | **17.0% (31/182) [12.3%, 23.2%]** | 7.6% (48/632) [5.8%, 9.9%] | 9.7% | +9.4 |
| **out of sample** | **C** | 62.6% (114/182) [55.4%, 69.3%] | **43.5% (275/632) [39.7%, 47.4%]** | 47.8% | +19.1 |

No uninformative condition fires in any subset: 0 unparsed answers and 0 failed calls in both arms,
reject rates between 9.7% and 48.6%, and at least 17 discordant pairs on every paired comparison.

## C − A, paired over the same items

ΔTPR and ΔFPR use `chimera/eval/paired.py` (McNemar, Wilson on the discordant pairs), the method
`PREREGISTRATION-arms.md` and `-rubric.md` fixed. **No pre-registration here specified a paired
method for J**, so ΔJ uses a paired bootstrap over items, resampled within label, seed 20260819,
10,000 draws, percentile interval — chosen for this read, not in advance. A normal-approximation
interval on the same pairs agrees with it within 0.5 points in every row.

| | ΔTPR | ΔFPR | **ΔJ** |
|---|---:|---:|---:|
| all | +44.7 [+40.9, +45.4] · 106 vs 1 | +35.4 [+33.4, +36.3] · 248 vs 6 | +9.3 [+1.9, +16.9] |
| in sample | +41.5 [+27.0, +44.6] · 23 vs 1 | +28.8 [+15.1, +32.0] · 16 vs 1 | +12.7 [−6.4, +31.8] |
| **out of sample** | +45.6 [+41.6, +45.6] · 83 vs 0 | +35.9 [+33.9, +36.8] · 232 vs 5 | **+9.7 [+1.7, +17.8]** |

Points; "106 vs 1" is the discordant pairs, caught (or rejected) by C only against A only. ΔJ is
computed before rounding, so it can differ by 0.1 from the J column above. Out of sample, C keeps
every one of A's 31 catches and adds 83.

## The three primary comparisons, out of sample

1. **A's rejection recall: 17.0% (31/182), Wilson [12.3%, 23.2%].** The number for "how well does
   Chimera's judge catch a plausible-but-wrong finding". By the first pre-registration's rule it does
   not discriminate: the upper bound does not reach the 30% weak band.
2. **C − A in Youden's J: +9.7 points, paired bootstrap [+1.7, +17.8].** The interval excludes zero:
   the rubric change separates the classes better on 814 items nobody read while writing it.
3. **C's false rejection: 43.5% (275/632), Wilson [39.7%, 47.4%]** — the whole interval above the
   20% ceiling. Not lower than in sample (38.5% in the pilot, 34.6% in this run's re-grade of the same
   items), so the opposite-direction sign of fitting the pre-registration named is absent.

## Predictions

| | predicted, out of sample | measured, out of sample | |
|---|---|---:|---|
| A recall | 12–20% | 17.0% | inside |
| C recall | 45–60% | 62.6% | **above** |
| C false rejection | 32–45% | 43.5% | inside |
| C − A in J | +2 to +7 points | +9.7 points | **above** |

Two inside, two above, and both misses are high — the direction of every missed prediction in this
directory (arm C's recall in the pilot, arm E's recall and false rejection). The arm C commit message
already noted the pattern from the all-rows numbers; the out-of-sample figures do not change it.

## The clause, applied as written

The pre-registration named the in-sample value it meant — the pilot's **+6.8**, the "in sample
(known)" column of its table — and gave the reading: *"If it holds at +6.8 or better, the improvement
is real and general; if it collapses to near zero, arm C was a description of 41 specific comments."*

- **Against that value, the edge did not shrink.** +9.7 out of sample against +6.8 in sample. The
  clause does not fire, and the reading of the five earlier arms stands.
- **That is a statement about point estimates.** The interval [+1.7, +17.8] excludes zero — the
  improvement is real — but it does not exclude values below +6.8, so "at least as large as in the
  pilot" is not established.
- **Against this run's own re-grade of the 105 (+12.7), out of sample is 3.0 points lower**, and the
  interval on that difference is [−17.6, +24.0]. With 105 items on one side, this comparison cannot
  see a shrinkage much smaller than 20 points. It is recorded as what the instrument cannot show, not
  as evidence that nothing shrank.

"Materially" was never given a number; the document's own anchor (+6.8) is what is applied, and the
within-run comparison is printed beside it. Where that 3.0 comes from: not from catching fewer bad
comments out of sample — C's recall is 62.3% in sample and 62.6% out — but from rejecting more good
ones, 34.6% in sample against 43.5% out.

## Not pre-registered: the same 105 items, graded twice

The in-sample items were graded in the pilot and again in this run — same judge, same prompt
(identical `prompt_sha`), same diff cache, same verdict parser, temperature 0. What changed was the
run: chimera 0.48.0rc7 → rc9, and the runner's retry and concurrency code, which touches neither the
prompt nor the verdict parser.

| | pilot | this run | same verdict |
|---|---:|---:|---:|
| A recall | 8/53 (15.1%) | 11/53 (20.8%) | 97 of 105 |
| A false rejection | 0/52 (0.0%) | 3/52 (5.8%) | |
| C recall | 32/53 (60.4%) | 33/53 (62.3%) | **82 of 105** |
| C false rejection | 20/52 (38.5%) | 18/52 (34.6%) | |
| C − A in J | +6.8 | +12.7 | moved **+5.8** |

Arm C changed its verdict on 23 of 105 items with nothing changed but the run. One re-run is one
difference, not a variance, so this sets no noise floor. What it does show:

- **The pilot's +6.8 was one draw** of a quantity that read +12.7 the second time. "The edge held, and
  grew" — +9.7 against +6.8 — compares a replication with a single noisy reference.
- **The intervals in this directory model which items were drawn, not which answer the judge gave.**
  McNemar and Wilson treat each verdict as fixed. Arm C's own J moved +5.7 points on re-run — the size
  of the differences the arms D and E section ranked by (D +16.3, C +21.9, E +8.9, one run each at
  n=105).
- The pilot's "never rejected a correct comment in 52 tries" for arm A is 3 of 52 on the same items
  the second time.

## What the trade costs at the natural prevalence

The pilot was balanced, and there arm C's trade read as nearly one for one: +24 bad comments caught,
20 good ones destroyed. The slice is 74% correct comments. Out of sample, C catches **83** more
incorrect comments than A and rejects **227** more correct ones — **2.73 correct findings destroyed
per extra catch** (in sample, balanced: 22 against 15, 0.68). The rates carry the same trade; the
prevalence is what makes it this expensive in use.

## Verdict

- **The judge as shipped (A): 17.0% rejection recall out of sample, [12.3%, 23.2%], at 7.6% false
  rejection. It does not discriminate.** Inside the predicted band. This is the confirmatory number,
  and the answer to "how good is the judge" stops being provisional.
- **Arm C separates better out of sample** — +9.7 J, interval excluding zero, on items nobody read
  while writing the rubric. The rubric change generalises; it was not a description of 41 comments.
- **Arm C does not ship.** 43.5% false rejection out of sample, the whole interval above the 20%
  ceiling, and 2.73 correct findings destroyed per extra catch at the slice's own prevalence.
- **The retroactive clause does not fire**, and the reading of arms A–E stands. The re-grade adds a
  caveat the clause did not anticipate: J differences of about six points at n=105 are the size of
  what re-running arm C alone produced.
