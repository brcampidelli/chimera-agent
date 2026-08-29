# Pre-registration — union + validator instead of the vote, on the fusion panel

**Written and committed BEFORE a single model call of this experiment.** Nothing below may be edited
after the first run; changes go in an addendum with a reason, as `local_lift`, `memory_graph` and
`fusion_paired` do.

## The question

> When the panel has answered, the aggregator picks the answer. Today a logic-typed task with a
> strict majority returns **the majority**. Does taking the **union of the distinct answers and
> validating each one** beat that — and by how much, at what price?

## Why, and what is already known

`chimera/fusion/engine.py` aggregates a panel two ways. For a task typed `logic` with a strict
majority it returns that majority and skips the judge and the synthesiser entirely
(`_aggregate`, the `vote` branch). Everything else goes judge → synthesiser.

Three things say the vote is the weaker half:

- **The pool holds more than the selection takes.** Barrel of Monkeys, SWE-bench Verified n=500:
  pool coverage **80.8%**, selected **66.2%**. Fourteen points sit in the pool and never get chosen.
  A strict majority is the most brittle selector of all — it picks by popularity, and a correct
  minority answer is discarded by construction.
- **This project already has the mechanism and never wired it to the panel.**
  `chimera/fusion/verifier_select.py` (`VerifierSelector`, Weaver-lite: *score each candidate, take
  the top one*) is injected into `SelfConsistency` and reachable only through one CLI flag on
  `best-of`. The panel — the place with the most candidates and the most to lose — votes.
- **Our own judge is weak at ranking and was never asked to verify.** `bench/review_judge` measured
  15.1% rejection recall against a registered 30% floor. Ranking several answers is a harder question
  than checking one; the validator asks the easier one, once per candidate.

**Measured free, before any of this: 856 of 1319 GSM8K test items (64.9%) classify as `logic`.**
The vote path is not a rare branch on its own target corpus, so there is something here to change.

## Design, fixed now

**Corpus.** GSM8K test (`bench/llm_benchmarks/datasets.py`, pinned URL), restricted to the items
`classify_task_type` calls `logic` — the vote branch's actual domain. A fixed-seed sample of N.
Ground truth is the number after `####`; scoring is exact match after numeric normalisation.

**The panel is generated ONCE and every aggregator reads the same answers.** That is the whole point
of the design: panel sampling noise is removed by construction, so a difference between aggregators
is a difference between aggregators. Same items, same ruler, same n.

| stage | arm | cost |
|---|---|---|
| 1 | generate the panel, cache to `panel.jsonl` | paid once |
| 2 | `oracle` — is the gold answer in ANY panel answer? | **free** |
| 2 | `vote_text` — `majority()`, today's shipped logic path | **free** |
| 2 | `vote_answer` — the same vote, counting the extracted NUMBER | **free** |
| 2 | `member_i` — each panel member alone | **free** |
| 3 | `judge_synth` — today's other path, on the cached panel | paid |
| 3 | `union_validator` — the proposal, on the cached panel | paid |

`oracle` is the ceiling and `member_i` is the floor. Between them is everything any aggregator could
ever win, and both are free once the panel exists.

### Why `vote_answer` exists, measured before this file was committed

`majority()` clusters answers by **text similarity** (`difflib` over the whole answer) and returns
the **longest** member of the winning cluster. On a logic task — where the answers differ by a
single character — that is backwards in both directions, and both were reproduced in the shipped
code before any of this ran:

| panel | what the shipped vote returns |
|---|---|
| two panelists say **42**, one says **41**, same wording | **41** — the minority answer |
| three panelists all reach **72** by different reasoning | **no majority** → pays judge + synthesiser |

The first is the failure the branch was built to prevent: `chimera/fusion/task_type.py` opens by
saying voting is used so *"a correct minority answer must not be averaged away"*, while the
implementation can **elect** one. The second pays two extra model calls to re-derive an answer three
models already agreed on.

So `union_validator` **must not** be compared against `vote_text`. Fixing a clustering bug and
adding a validator are two different interventions, and measuring them together would credit the
validator with the fix — the `§2g` error, in the form where it flatters the thing being proposed.
The registered baseline for criterion 1 is **`vote_answer`**, and `vote_text` is reported beside it
as the size of the bug.

## The stop, and why it is where it is

**Stage 3 may not be paid for until stage 2 has printed the ceiling.** If `oracle` is close to
`vote`, the pool holds nothing the vote is missing and no selector can help — the item closes at the
price of the panel alone. This is the rule `§2k` was written for: before building a loop around a
verifier, measure how much there is to reject.

The gap `oracle − vote_answer` is registered here as the **headroom** — measured against the
FIXED vote, never the broken one, and the decision rule is absolute:

- **headroom < 3.0 pp** → stage 3 is not run. There is nothing left for a selector to find, and
  the honest outcome of the item is the clustering fix alone.
- **headroom ≥ 3.0 pp** → stage 3 runs, and the criterion below applies.

## Adoption criterion — absolute, and fixed before the number exists

`union_validator` replaces the vote for logic-typed tasks only if **all three** hold, measured
against `vote_answer`:

1. **union_validator − vote_answer ≥ +3.0 pp** — an absolute floor, never a multiplicative one;
2. the **95% CI of the paired delta excludes zero**;
3. **damage ≤ gain**: the count of items the vote got right and the validator got wrong is not
   larger than the count it turned the other way.

Criterion 3 is there because an aggregator that wins on net while destroying more than it saves is a
different thing from one that only adds, and the net alone cannot tell them apart. This project has
already shipped a decoding intervention that showed +12.9 pp on the headline metric while silently
destroying 35 cases (`§2r`); the paired damage count is what caught it.

## Activation — how much the change acted

Printed beside the delta, never under it: **the share of items where `union_validator` and `vote_answer`
return different answers**. If they differ on fewer than **5%** of items, the intervention barely
acted and the delta is noise on a handful of cells, not a result. An intervention that does not
report how much it acted reads "on and inert" as "did not help".

## What would refute the bet

- **vote_answer ≥ union_validator** ⇒ popularity beats verification on this corpus, and the Weaver result
  does not transfer to a three-model panel of frontier models.
- **union_validator ≈ oracle** ⇒ the validator is capturing the headroom and the vote was the
  bottleneck — adopt.
- **union_validator wins but damage > gain** ⇒ it is trading, not adding, and the honest outcome is
  an opt-in with the trade written on it.

## What this experiment CANNOT show — registered before it runs

- **It is arithmetic with one numeric answer.** That is the vote branch's domain and nothing else.
  It says nothing about the judge → synthesiser path on open-ended work, which is where most fused
  turns actually go, and where "the union of the distinct answers" is not even well defined.
- **The panel is cached**, deliberately. So this measures **aggregation** and cannot say whether a
  different panel would do better — that is `fusion_paired`'s question, not this one.
- **The validator is a model.** A validator that scores every candidate equally selects by tie-break
  order, which is `member_0`. The report prints the score spread for exactly this reason: a flat
  spread means the validator did not discriminate, and the arm must be read as `member_0` in disguise
  rather than as verification that did not help.

## Provenance

Every row records the model slugs, the seed, the sample, and the SHA of this file.
