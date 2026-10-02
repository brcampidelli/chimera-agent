# Results — explorer census

2026-10-02. Pre-registration: `PREREGISTRATION.md` (commit `9afe1be5`, before the census ran;
Amendment 1 in `e04cc044`, after the registered reading and before the amended one). Data:
`results/census_registered.json`, `results/census_amended.json`. **US$ 0** — offline, no model call.

## The decision

| reading | verdict |
|---|---|
| **as registered** | **VOID** — 428 of 757 non-halted traces failed the alignment or append-only check (57% > 25%) |
| Amendment 1 (**post-hoc**) | **DO NOT RECOMMEND** — the pooled saving misses its bar; labels: *proxy population*, *token-only* |

**The explorer does not go on the Code screen.** It stays where it is: opt-in in the run launcher
and on the CLI (`chimera explore`), with S12's contract behind `CHIMERA_EXPLORER_CONTRACT`, off. No
product change ships with this bench.

The registered verdict is VOID, and that is the registered result. Why it voided is the apparatus:
every one of the 349 prompt "drops" was into the loop's closing call at `max_steps`, which carries no
tool schema (`tools_offered: false`) and is smaller by it, and all 79 misalignments were tool calls the
model emitted on those same closing calls, which never ran and were never named. Counting only the
calls that were offered tools, 757 of 757 traces align and are append-only (PREREGISTRATION,
"The registered reading"). Amendment 1 changes those two checks and nothing else. Its verdict is
post-hoc; a reader who holds the registered verdict as final should read it as not made. Both
verdicts say the same thing to the product: nothing changes.

## The amended reading (post-hoc), conservative cell: block 1000 tokens, re-reads charged

757 usable solves of `chimera solve`'s worker loop, SWE-bench Verified django, four models (A 196,
D 192, G 203, Q 166); 47 halted rows are out of every denominator (PROTOCOL §2).

| condition of the frozen rule | result | holds |
|---|---|---|
| regime share (D ≥ 3 reads in the opening phase, ≥ 3 calls after it) ≥ 25% | **40.4%** (306/757) | yes |
| median saving within the regime ≥ 20% | **21.1%** | yes |
| pooled saving over all usable solves ≥ 10% | **9.8%** | **no** |
| (label) cache-weighted input-dollar saving, pooled | **0.6%** | → *token-only* |

The pooled condition fails by 0.2 pp. It is not loosened: the threshold was frozen before any number
was read, and a saving that lands on its bar in the conservative cell is not one to build a screen on.

### All four cells (pooled token saving · median per solve · pooled input $ · solves where it costs more)

| cell | all 757 solves | the regime, 306 solves |
|---|---|---|
| B 300, no re-reads | 19.4% · 19.0% · 11.7% · 36 | 34.3% · 34.7% · 22.7% · 0 |
| B 300, re-reads | 13.9% · 8.6% · 6.0% · 112 | 27.7% · 27.0% · 16.4% · 7 |
| B 1000, no re-reads | 15.5% · 12.0% · 8.2% · 104 | 29.8% · 29.4% · 19.3% · 3 |
| **B 1000, re-reads (decision)** | **9.8% · 1.3% · 0.6% · 194** | **22.9% · 21.1% · 12.6% · 22** |

The optimistic corner (a 300-token block, no file opened twice) would have passed every condition.
The decision cell was fixed at the conservative corner before the run, because a contract block with
findings and gaps is not 300 tokens and an agent that edits a file has to open it.

### Per arm (decision cell)

| arm | model | regime share | pooled tokens, all | median in regime | pooled input $, all | cache-read share |
|---|---|---|---|---|---|---|
| A | deepseek-v4-flash (the default when the bench ran) | 30.1% | **6.7%** | 17.5% | **1.3%** | 0.86 |
| D | deepseek-v4.1-flash | 22.9% | 2.8% | 10.8% | −7.0% | 0.88 |
| G | gpt-6-luna (**today's default**, `chimera/config.py`) | 59.1% | 21.0% | 25.0% | 6.7% | 0.93 |
| Q | qwen3.7-flash | 50.0% | 17.1% | 23.1% | 9.8% | 0.60 |

The regime is a property of the model as much as of the task: G reads before it acts in 59% of
solves, D in 23%. Per arm n is 166–203, so these are descriptions, not comparisons.

**The arm that matters most is a post-hoc subgroup.** The default model changed after this bake-off:
it is now G (`gpt-6-luna`). On G alone the conservative cell clears every bar
of the rule — regime 59.1% ≥ 25%, median in regime 25.0% ≥ 20%, pooled 21.0% ≥ 10% — and the input-$
saving (6.7%) is over the *token-only* line. The frozen rule pools the four arms and was not written
per model, so this does not change the verdict; it changes what the next measurement should be
(below). On the previous default (A) the explorer would save 6.7% of tokens and 1.3% of dollars.

### The shape of the traces (histograms, PREREGISTRATION "Readings")

- `read_file` calls in the opening read-only phase: 0 → 177 · 1 → 139 · 2 → 126 · 3 → 92 · 4–5 → 121 ·
  6–9 → 72 · 10+ → 30. **Median 2; median phase 4 calls.**
- calls after the phase: 1–2 → 10 · 3–9 → 117 · 10–19 → 172 · 20–29 → 279 · 30+ → 179.
- median single read: 678 tokens.

### Secondary files (tool sequences only, no tokens)

| file | runs | share with ≥ 3 reads before the first action | histogram 0 / 1 / 2 / 3 / 4–5 / 6+ |
|---|---|---|---|
| `directive_boundary` (questions and directives, small fixture) | 120 | 25.8% | 9 / 40 / 40 / 30 / 1 / 0 |
| `unattended_claims` pilot fixtures | 20 | 35.0% | 4 / 4 / 5 / 0 / 2 / 5 |

## Predictions — held?

| # | prediction | result | verdict |
|---|---|---|---|
| 1 | regime share ≈ 50%, inside 30–70% | 40.4% | **held** (inside the range, under the point) |
| 2 | median saving in the regime ≈ 25%, inside 10–40% | 21.1% | **held** |
| 3 | pooled saving ≈ 15% | 9.8% | **missed** — lower |
| 4 | input-$ saving under 10% and under half the token saving | 0.6% against 9.8% | **held** |
| 5 | small fixtures: share with ≥ 3 reads ≤ 25% in each file | 25.8% and 35.0% (n = 20) | **missed** — both above |

Prediction 5 missing matters for the reading: small interactive turns reach three reads before acting
about as often as the SWE-bench solves do (26–35% against 40%). What they lack is the long tail after
the reading that the saving lives on, and the secondary files cannot price that.

## Honest reading

**The regime exists, and it is not where the law's number lives.** 40% of the solves read three or
more files before their first action, and those solves hold 42% of all prompt tokens. Inside them the
conservative saving is about a fifth (21.1% median), against a median (D−1)/D of 75% for the same
solves. The gap is the mechanism the law does not have: `bench/hierarchy_multistep` sends every
document on every turn and nothing else, while a coding loop **keeps reading after it starts solving**
(the median opening phase is 4 calls and 2 reads; most context arrives later, from tests, shell output
and second looks), and it must **open again the files it edits**, which hands part of the saving back.
The law is an upper bound for a loop that only reads; this census is what a loop that also works
leaves of it.

**The dollar saving is nearly nothing.** What the explorer takes out of a later prompt is its oldest
part, which the provider has already cached: 86–93% of these prompts were cache reads (Q 60%), billed
at a quarter (A) to a thirtieth (D) of the input price. The explorer's own calls are paid in full. Pooled, the
conservative cell saves 9.8% of tokens and 0.6% of input dollars; on arm D it costs 7% more. This is
the caveat `bench/hierarchy_multistep` wrote and could not reproduce on its route (zero cache reads
there); here the cache is on and it eats the win, as PROTOCOL §3 says it can.

**What is still real.** Tokens are context-window pressure and latency, not only money: in the regime
the main loop carries 21–29% less on every later call. In the regime the conservative cell pays 21–23%
fewer prompt tokens (median and pooled), most of it on the long tail of solving calls. That would
matter for a model with a short useful context, and it is the explorer's honest pitch — not cost.

## The recommendation

1. **Do not offer the explorer on the Code screen now.** The frozen rule says no. Pooled over the
   models measured, the conservative saving is 9.8% of tokens and 0.6% of dollars before any quality
   cost, and a toggle whose benefit is that small is a choice nobody can make well.
2. **Keep it where it is**, opt-in in the run launcher and the CLI. The run launcher's long multi-file
   runs are the closest thing to the regime the census found.
3. **What would reopen this**, each a separate pre-registration:
   - a census of **attended** turns, which needs an extractor over this machine's transcripts that keeps
     the tool sequence and per-call tokens — no stored projection has them (`stop_gate`'s drops both);
   - the explorer on a **cheaper model** than the main loop, which is what its role is for: the census
     priced it at the main model's size and rate, its most conservative side;
   - **first in line:** a **paired live A/B on today's default model (G)**, which reads before it acts
     in 59% of solves and is the one model whose post-hoc slice clears every bar here. Pre-registered
     before it runs, reading quality first (resolution, paired), tokens and dollars second — the only
     thing that can say whether a `path:line` block solves as well as the files it replaced, and the
     only way the G slice stops being post-hoc.

### Exploratory, not a verdict

If the agent delegated **only** when it was about to read three or more files (which it cannot know in
advance), the pooled saving would be 9.6% of tokens and 5.0% of input dollars (A 7.4% / 5.9%). Gating
by regime does not rescue the pooled number; it only stops the explorer costing money on short phases.
Computed after the amended reading, for the record; no rule reads it.

## Facts checked against the code on the way

- The review said the explorer is "offered only in /api/runs and `chimera explore`". On the server that
  is not quite so: `CodeTurnRequest` inherits `explorer: bool = False` from `CodeSeams`
  (`chimera/api/code_api.py`), so a Code turn accepts it. On the desktop it is so: only
  `RunLauncher.tsx` sends `explorer`; the Code screen never does.

## What this cannot show

Quality (whether a delegated phase still resolves); the attended population; distinct files (D counts
reads); which calls an agent offered the tool would really delegate; and the explorer on its own,
cheaper model. All five are in PREREGISTRATION.md, "What this cannot show".

## Reproduce

```bash
python bench/explorer_census/census.py                  # Amendment 1 → results/census_amended.json
python bench/explorer_census/census.py --as-registered  # the VOID   → results/census_registered.json
uv run pytest -q tests/test_explorer_census.py           # the arithmetic, on hand-summed traces
```
