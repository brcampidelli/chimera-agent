# Results — at equal calls the hierarchy did not beat one agent, and its synthesiser is where the needles went

Run 2026-09-11 · prereg `PREREGISTRATION.md` (two dated amendments, both before the scored run) ·
raw `results/2026-09-11-3b.jsonl`, report `results/2026-09-11-3b-report.md` · the 8B run that hit
the ceiling is kept as `results/2026-09-11.jsonl` and `2026-09-11-8b-report.md`, void by the
registered instrument rule.

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

Nothing in code changes on this bench. Two follow-ups it names: the synthesis prompt on a weak
backbone (the summaries carry the values; the final answer does not — a prompt that asks the
synthesiser to *copy figures verbatim* is the obvious next arm), and n — ten tasks cannot bring
a 50% flip rate down to a readable floor, and the corpus is the one both existing benches share.

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
feedback. A 50% noise floor: the direction is consistent across four comparisons and two
backbones; the size is not a number to quote. Nothing about `IsolatedCrew` on writing tasks.
