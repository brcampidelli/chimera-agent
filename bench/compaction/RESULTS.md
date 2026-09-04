# The arms were not run, because compaction has never fired

Measured 2026-09-04. **Zero compactions in 137 real runs.**

[`PREREGISTRATION.md`](PREREGISTRATION.md) fixed a paired rule for a question one layer down: does a
rule-form summary preserve standing instructions better than the structural note? The apparatus
calibration answered a question one layer *up* first, and the answer makes the arms a measurement of
a path production does not take.

## What the census says

`traces.jsonl` records `compactions` per run and `compacted` per step. Over every run this install
has traced:

| | |
|---|---:|
| runs | 137 |
| runs with **≥1 compaction** | **0** |
| steps marked compacted | **0** |
| peak context, median | 6,826 tokens |
| peak context, maximum | 64,067 tokens |

## Why it never fires

Two independent reasons, and both are in the code rather than in the corpus:

1. **On the CLI, compaction is off.** `context_budget` defaults to `None`, and `Agent` builds no
   `ContextBudget` without one. `chimera solve` therefore has no compaction at all unless the
   operator passes `--context-budget`.
2. **On the Code screen it is on, and unreachable.** `Conversation.tsx` sends `context_budget: 0.6`,
   and the trigger is that fraction of the model's *advertised* window. Measured for the shipped
   default:

   ```
   deepseek-v4-flash-0731   window 1,310,000   0.6 -> compacts at 786,000 tokens
   ```

   The largest context any traced run reached is 64,067 — a factor of twelve short.

## The finding this leaves, and the measurement it asks for

**A fraction of the advertised window is the wrong shape for the trigger**, and it has been quietly
getting wronger. When windows were 8k and 128k, "0.6 of the window" and "as much as the model can
still use well" were roughly the same number. Advertised windows have since grown by an order of
magnitude; the *useful* window has not moved with them.

The survey in `~/.claude/refs/harness-engineering.md` puts it plainly, from four independent sources:
degradation within a single pass begins **before** the window fills, and a model advertising over
200k can degrade from around 50k. This corpus's maximum, 64,067 tokens, is already past that mark —
and nothing compacted, because the trigger is looking at 786,000.

That is a claim from literature, not from this repository, and it is the next thing to measure rather
than a reason to change the trigger now. The measurement is: **at what context length does this model
start getting the task wrong?** A trigger set from that number is a trigger about the model; a
trigger set from the vendor's advertised maximum is a trigger about the marketing.

## The summariser ships, off

`chimera/core/summarise.py` is written and tested — a rule-form summariser built on the split the two
papers `context_budget` already cites: compactors retain 17% of injected session constraints
(arXiv 2608.11242), and rule-form items survive far better than facts (arXiv 2608.11392). It asks for
standing instructions and forbids inference; on any failure it degrades to the structural note, and
it carries that note *alongside* the summary rather than instead of it.

`AgentConfig.summarise_compaction` is **False**. Turning it on would add a model call per compaction
to a path nothing takes — a cost with no effect to weigh against it. `bench/compaction/run.py` runs
the pre-registered arms when someone has a reason to.

## Why the arms were not run, and why that is not a moved goalpost

Nothing about the hypothesis was seen. The pilot in `run.py --pilot` prints whether a compaction
fired and what replaced the span, and **deliberately not** whether the convention survived; the
outcome column does not exist in that mode. What was learned is that the apparatus needs a
`context_budget` around **0.0025** to compact at all on this model — a configuration no surface uses
— and at that point the run would measure a synthetic setting rather than the product.

The pre-registration already named this shape of problem in its own terms: *"a probe that puts the
constraint in the task cannot exhibit the effect — both arms pass and the run measures nothing."*
Running under a budget three hundred times lower than production is the same failure wearing a
different coat.

`bench/fusion_paired` declined a 900-cell run on the same grounds after its pilot showed the corpus
saturated. This is that precedent, applied.

## What this census could not show

- **137 runs from one install**, weighted towards short probes and short conversations. A person
  working a full day in one Code conversation would reach further — though twelve times further is a
  great deal of conversation.
- **Only runs with a trace.** A run started without `trace_path` writes nothing here.
- **It says nothing about whether a summary would help**, only that nothing currently reaches the
  place where it would.
