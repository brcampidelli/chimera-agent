# Results — at equal calls the hierarchy did not beat one agent, and its synthesiser is where the needles went

Run 2026-09-11 · prereg `PREREGISTRATION.md` (two dated amendments, both before the scored run) ·
raw `results/2026-09-11-3b.jsonl`, report `results/2026-09-11-3b-report.md` · the 8B run that hit
the ceiling is kept as `results/2026-09-11.jsonl` and `2026-09-11-8b-report.md`, void by the
registered instrument rule. The verbatim-synthesis follow-up (registered addendum, 30 more
trials, same file) is read in its own section below.

**Read the noise floor first.** Ten tasks, three runs each; the flip rate of the arms is 20–50%,
so a 30–50 pp difference on `pass^3` is the size a task moves with nothing changed. Every verdict
below carries that sentence. What can be read is the *direction*, agreed by every comparison and by
the void 8B run, and the discordant counts: **not one task went the hierarchy's way** in any pairing.

## The scored run — `llama-3.2-3b-instruct` on every role, 120 trials

| arm | calls/task | pass@1 | pass^3 | flip | tokens/task |
|---|---:|---:|---:|---:|---:|
| `single_1` | 1.0 | 0.67 | 0.50 | 0.30 | 985 |
| `single_equal` | 3.5 | 0.60 | 0.40 | 0.40 | 3,810 |
| `hierarchy` | 3.5 | **0.27** | **0.00** | 0.50 | 1,775 |
| `hierarchy_no_synth` | 2.5 | 0.57 | 0.50 | 0.20 | 1,574 |

Instrument: `single_1` at 0.67, inside the registered 20–85% band.

| comparison (paired on `pass^3`) | Δ | 95% CI | discordant | reads |
|---|---:|---|---|---|
| **primary** — `hierarchy` vs `single_equal` (same calls) | −40 pp | [−40, −0.8] | 0 for the hierarchy, 4 for the single agent | inside the floor; direction: against the hierarchy |
| `single_equal` vs `single_1` (does re-reading help) | −10 pp | [−10, +5.9] | 0 / 1 | no — re-reading did not help a 3B model |
| `hierarchy` vs `hierarchy_no_synth` (leave-one-in: synthesiser) | −50 pp | [−50, −6.6] | 0 / 5 | the synthesiser is where the needles are lost |
| `hierarchy` vs `single_1` (the existing benches' comparison) | −50 pp | [−50, −6.6] | 0 / 5 | off the ceiling, the hierarchy loses |

## What the prediction said, and what held

Registered: *the hierarchy does not beat one agent given the same calls* — **held**, and by a
wider margin than "does not beat": the point estimate is 40 pp against it and the only tasks that
moved between the arms moved toward the single agent. Registered: *re-reading helps by a small
margin* — **did not hold** (−10 pp, inside the floor). Registered: *`hierarchy_no_synth` ≤
`hierarchy` by a small margin* — **held in reverse**: dropping the synthesiser **raised** pass^3
from 0.00 to 0.50. The workers extract the values; the synthesis call over their summaries is
where a weak backbone drops them. That is 2609.04217's leave-one-in finding in this substrate — all
realised value is in the executor's calls, and the extra role costs.

## The void run says the same thing

On the 8B backbone the single call scored 0.97 (ceiling; void by the rule), and the direction was
identical: `hierarchy` 0.77 pass@1 against `hierarchy_no_synth` 0.97 and both single arms ≥ 0.93.
Two runs, two backbones, one direction. Not a third seed of the same measurement — a different
instrument that agrees.

## What it means for the Orchestration tab

The tab's counterfactual line reports tokens. On these tasks the hierarchy spends **fewer tokens**
than an equal-call single agent (1,775 vs 3,810 per task) and **more** than the single call (985),
which is exactly what `bench/hierarchy` published; what it does not report is that at this
backbone it also answers worse, and that the synthesis step is the reason. The registered sentence
is earned: *"at the same number of calls, one agent that re-reads the documents does as well on
tasks like these"* — here, better. The token saving is the reason to orchestrate, not the answer.

Nothing in code changed on this run. Two follow-ups it named: the synthesis prompt on a weak
backbone (the summaries carry the values; the final answer does not — a prompt that asks the
synthesiser to *copy figures verbatim* is the obvious next arm — **measured below, adopted**),
and n — ten tasks cannot bring a 50% flip rate down to a readable floor, and the corpus is the
one both existing benches share.

## Follow-up — the synthesis asked to carry the figures verbatim (registered addendum, same day)

The addendum in `PREREGISTRATION.md`, written after the section above and before any call: a
fifth arm, `hierarchy_verbatim`, identical to `hierarchy` in backbone, tasks, seeds and calls
(D + 1), with one sentence appended to the synthesis system prompt — `_SYNTH_VERBATIM` in
`chimera/orchestration/hierarchy.py`: *carry every figure, name, version, path and identifier
from the summaries into the answer exactly as written*. Thirty trials, appended to the same
`results/2026-09-11-3b.jsonl`; the report was regenerated over all 150 rows.

| arm | calls/task | pass@1 | pass^3 | flip | tokens/task |
|---|---:|---:|---:|---:|---:|
| `hierarchy` | 3.5 | 0.27 | 0.00 | 0.50 | 1,775 |
| `hierarchy_verbatim` | 3.5 | **0.53** | 0.20 | **0.70** | 2,426 |
| `hierarchy_no_synth` (the workers alone, for reference) | 2.5 | 0.57 | 0.50 | 0.20 | 1,574 |

| comparison (paired on `pass^3`) | Δ | 95% CI | discordant | reads |
|---|---:|---|---|---|
| `hierarchy_verbatim` vs `hierarchy` | +20 pp | [−6.3, +20] | 2 for the sentence, 0 against | inside the floor; direction: for the sentence |
| `hierarchy_verbatim` vs `hierarchy_no_synth` | −30 pp | [−30, +3.7] | 0 for the synthesis, 3 for the workers alone | the workers alone still hold more |

Per task, passes out of three runs: **seven tasks up, none down, three unchanged** (`capacity`
is 0/3 in every arm of the bench; `contracts` and `policies` did not move). 8 of 30 trials
passed before the sentence, 16 of 30 with it.

**Against the registered rule.** Prediction *pass@1 ≥ 0.42* — held (0.53). Decision *≥ 20 pp
pass@1 over `hierarchy` with no task moving the other way → adopt* — met (+26.7 pp, 7 up / 0
down), so the sentence is on by default. One deviation from the letter of the rule, stated: it
said *"part of `_SYNTH_SYSTEM` (no flag)"*; it ships as `HierarchyConfig.synthesis_verbatim =
True`, which is the same prompt for every caller that does not set it, and the flag exists so
this bench's `hierarchy` arm keeps measuring the prompt it measured (`run.py` passes
`verbatim=False` there).

**What to read beside the adoption.** The flip rate went from 0.50 to **0.70** — the highest of
the five arms — and `pass^3` only from 0.00 to 0.20: the sentence gets the figures into the
answer on about half the runs, and the run-to-run variance says a 3B synthesiser follows the
instruction unreliably. Against the workers alone (0.57 / 0.50, flip 0.20) the synthesis call
still costs reliability — the point estimate is −30 pp on `pass^3`, and the three tasks that
differ all favour the concatenation. The answer is ~650 tokens longer per task: the values it
used to drop, restated. With a 70% flip rate on ten tasks the size is a noise-floor sentence,
as everywhere in this file; what is read is the direction (7 / 0) and the registered threshold.

**What this cannot show.** One backbone (3B); the 8B run was not repeated because its instrument
is void (ceiling). The tasks' answers *are* the planted figures, which is the case the sentence
was written for; on a task whose answer is a judgement, a synthesis told to carry every figure
verbatim can lengthen the answer without improving it, and that was not measured. The
production synthesiser is the **top** model, not the one measured here — the sentence is a hedge
for the weak backbones the tab lets a user choose, and what it costs a strong synthesiser (in
tokens, at least ~+37% here) is unmeasured.

## Apparatus notes, for the next reader

- Two `single_equal` trials on the 3B route hit a provider's context ceiling
  (`BadRequestError: maximum context`) on the fourth call — a **halt**, not a failure
  (`bench/PROTOCOL.md` rule 2) — and were re-run on the resume; both then finished.
- The 3B slug is not in the price catalogue, so `US$` reads 0.0000 for that run; the 8B run cost
  US$ 0.0196 for the same 120 trials, and the 3B run cost less.
- `value_check` grades values (each planted fact's last word and last figure); the published
  `hierarchy_ab.check` grades phrasing and failed every arm on the pilot task, including a single
  call that had the right values in a different sentence. Both verdicts are in the rows.

## What this cannot show

Read-heavy extraction on ten synthetic tasks, one weak backbone per run, no tools, no environment
feedback. A 50–70% noise floor: the direction is consistent across four comparisons and two
backbones; the size is not a number to quote. Nothing about `IsolatedCrew` on writing tasks.
