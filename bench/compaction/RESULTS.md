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

---

# The arms, run — the summary keeps what the note drops: 25/30 against 6/30 (study 19, B1, 2026-09-15)

Run against [`PREREGISTRATION.md`](PREREGISTRATION.md) and its Amendment 2 (a **mechanism test at
a forced-low budget** — `context_budget=0.0025`, the fraction the calibration measured as the one
that fires on this model; production still never compacts, see the census above). Model
`deepseek-v4-flash-0731`, 30 paired conversations, one compaction per conversation in both arms
(60/60 fired; 0 void, 0 halted). **US$ 0.084.** Raw: `results-2026-09-15.jsonl` (one line per
conversation, the summaries included); reader `read.py`.

## The registered outcome

| arm | final file honours the turn-one convention |
|---|---:|
| **A — note** (control) | **6/30 = 0.20** |
| **B — note + rules** | **25/30 = 0.83** |

Paired: **+63 pp**, 19 pairs moved A→B and **none** the other way, exact McNemar **p = 3.8 × 10⁻⁶**,
Wilson interval on the discordant pairs [+0.42, +0.63]. **Registered decision: ADOPT** (≥ +15 pp and
p < 0.05). Per Amendment 2 that means the Code screen sends `summarise_compaction` beside
`context_budget` (`Conversation.tsx`, `SUMMARISE_COMPACTION = true`, pinned by `Code.budget.test.tsx`);
the seam itself (`CodeSeams.summarise_compaction`, default off) is on every coding request.

By convention (B / A honoured, of 5 each): copyright header **5/1**, `AUTOR` constant **5/0**,
`# fim` end marker **3/1**, `bee_` function prefix **3/0**, future import **4/0**, no `print()`
**5/4**. The last row is the one the registration warned about — *a convention the control also
honours is not evidence* — the control is at 4/5 there because not printing is the model's habit,
not its memory. Excluding it, the note keeps **2/25** and the summary **20/25**.

**Cost, the other half of the decision.** The treatment conversations cost **no more** than the
control ones (US$ 0.0381 against 0.0456 over 30 each): the summariser's call is small and the
compacted prompt it leaves behind is shorter. Wall-clock is not reported as a cost — the detached
session had a pseudo-terminal, so `CHIMERA_HOST_EXEC=ask` prompted on 21 attempted host commands
and refused each after its 120 s timeout, exactly as the headless run refused them at once; the
seconds column carries that and the dollars do not.

> **Correction, 2026-09-26: the dollars above were taken with the wrong ruler.** `run.py` summed
> `result.usd` per turn, and the summariser's own call was never in it: it went to the backend
> beside the loop's `_step`, so neither `AgentResult` nor the usage log saw it. The summariser now
> charges the run it compacts (`chimera/core/summarise.py`, branch
> `fix/plan-gate-and-summary-on-the-bill`). The treatment arm made **30 summariser calls**, one per
> compaction, and none of them is in its US$ 0.0381.
>
> Not re-measured; bounded. At the model's list price (US$ 0.022/M in, 0.32/M out), with every
> call's input at the summariser's 12,000-character cap, 4 characters per token and 400 output
> tokens a call, the 30 cost at most **US$ 0.0059**, which puts the treatment arm at **≤ 0.0440
> against 0.0456**. Read against the 30 summaries this file's results record, even at 2 characters
> per token the bound is US$ 0.0053 (≤ 0.0434). **The conclusion survives**: overturning it needs
> more than US$ 0.0075 across the 30 calls, over US$ 0.00025 each. The number printed above does
> not: it measured the arm without its most distinctive call.

## The fabrication count, and what it found instead

All thirty treatment summaries were read against the span they compress (the registration asked for
three). **Fabricated rules: 0/30** — nothing in a summary states something the span did not contain.
But only **1/30 is what the summariser was built to produce**: a standing instruction in imperative
form (`function-prefix/strings`: "Create final.py with a bee_ string helper …"). The rest:

| what the model returned as "standing" | n | carries the convention |
|---|---:|---:|
| a **code block** — the last file written, echoed verbatim | 23 | 14 (the header, the constant, the import, the prefix is *in* the file) |
| narration or self-talk ("I'll check the current files…") | 3 | 1 (mentions `# fim` while complaining it could not verify it) |
| **raw tool-call markup** (`<｜DSML｜invoke name="exec_command">…`) | 2 | 0 |
| `NONE` (note only) | 1 | — |

So the gain the primary outcome measures is mostly **echo, not rule**: the summary carries an earlier
file, and the convention rides along inside it. That is not the mechanism arXiv 2608.11392 describes
and the prompt asks for, and it is the honest description of why 25/30 happened. It is also why the
two rows this cannot explain are listed: `author-constant/money` and `copyright-first-line/lists`
honoured the convention although their echo did not carry it — five of the nine echoes that lacked
the token still ended in an honouring file, and nothing here says why.

**The two tool-markup rows are a defect, fixed and tested.** A summary is believed, and a prompt that
carries raw tool-call syntax is a call waiting to be made. `rule_summariser` now replaces such a reply
with the structural note (`_leaks_tool_markup`, the DSML envelope and the `<tool_call>` /
`<function_call>` tags), pinned by a test that fails when the guard is removed. The count above is of
the run *before* the guard; with it, those two conversations would have received the note alone.

## What this does and does not say

- **Mechanism, not production.** At `0.6` of a 1.31M-token window the Code screen compacts at 786k
  tokens and no traced run has reached 64k; the summariser will do exactly nothing to today's
  conversations. What it decides is which behaviour a conversation gets *when* compaction fires —
  and that the note alone loses the convention 24 times in 30.
- **A forced-low budget shortens the span.** Registered as a bias toward the treatment: a dropped
  span of four messages is easier to echo than a real one of two hundred, and an echo of one file
  is not a summary of a day's work. The 25/30 is an upper bound on what a long conversation would
  keep.
- **Synthetic conventions, one model, one venue.** A first-line header is the easy end of what
  compaction destroys; "not that directory" was not tested.
- **Replication of the direction, from a run this bench killed.** The first launch died at pair 15
  on the shell's timeout with everything held in memory; its log kept the outcomes of 14 complete
  pairs: note **1/14**, summary **10/14**, 10 discordant one way and 1 the other. Same direction,
  same model, an hour apart; recorded because the run happened, not counted because it did not
  finish.
- The runs were served by three routes (`Relace` 138 turns, `StreamLake` 90, `Baidu` 12 — on the
  receipt since #484), which the arms shared by interleaving and which nothing here separates.
