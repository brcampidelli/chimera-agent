# Pre-registration — does `prune` keep a safety fact that was mentioned in passing?

**Registered 2026-09-11 against `2fcf4bc` (v0.53.0 + #421–#424), before the script was run.**
Deterministic and offline: no model, no key, US$ 0.

## Where the question comes from

`MemoryManager.prune(max_items)` (`chimera/memory/manager.py:153`) keeps the highest-value
memories under a budget and deletes the rest. Value is `chimera/memory/value.py::value` — a
weighted sum of recency (position in the store), specificity (`len(content)/200`, capped at 1),
kind (`persona` 1.0 / `semantic` 0.8 / `episodic` 0.5 / `working` 0.2), curation (has a key) and
reliability (source in `{user, hermes, openclaw}`). `persona` items are never pruned. The module's
own docstring states the thesis it was built on: *forgetting under a fixed budget is decided at
consolidation time, before future queries are known* (2606.12945).

**Nothing measures it.** `prune(` has no caller under `bench/` or `chimera/eval/`;
`tests/test_memory.py` checks that it removes the right count, not what it keeps.

arXiv:2609.05767 (*Will My Assistant Remember My Allergy?*, slice 15 of the 2026-09-11 sweep)
measured the class of policy this is: published eviction benchmarks compress a prompt that already
contains the future question, and hide the question and the benefit vanishes. PA-Bench, n = 100: an
allergy mentioned in passing survives to the question that needs it **0–1%** at a 20% budget against
**97%** with full memory, and *"the cause is the budget, not the scorer"* — no training-free policy
ranks the fact high enough. The item that gets evicted has a profile: early, short, incidental,
unkeyed. That is the lowest score `value()` can give.

## What is measured

`prune` is deterministic, so this is not a sample — it is a table. One **fact** whose loss would
matter (*"the customer is allergic to penicillin"*, ~40 characters) is written into a store among
**N distractors**, the store is pruned to a budget, and the question is whether the fact is still
there. There is no query and no scorer: `prune` never sees one, which is the whole point of the
paper and of the module.

The fact's profile is varied on every axis `value()` reads:

| axis | levels |
|---|---|
| position | first of N (mentioned early), middle, last (mentioned just now) |
| kind | `episodic` (an incident in the conversation), `semantic` (a stated fact) |
| key | none (in passing), keyed (the writer curated it) |
| source | `chimera` (the agent wrote it down), `user` (the person did) |

The distractors are what a working store holds: seeded lengths between 60 and 400 characters,
kinds drawn 60% `semantic` / 30% `episodic` / 10% `working`, 40% keyed, sources 70% `chimera` / 30%
`user`. Three seeds for the distractor draw; the fact's own text never changes.

Budgets: **20%** (the paper's) and **50%**. N = 50 and N = 200.

## Outcome

For every cell (position × kind × key × source × budget × N × seed): **kept** or **evicted** — read
from the store after `prune`, not from a dry run. Reported as the full table, plus the survival
rate over the profile the paper describes (early, unkeyed, agent-written, episodic) and over its
opposite (just now, keyed, user-written, semantic).

Also reported: the only mechanism the module offers that guarantees survival — `persona`, which
`prune` skips — so the finding comes with the instruction that already exists
(`chimera memory add … --persona`).

## Registered prediction

The in-passing profile survives a 20% budget in **0 of 6** cells (3 seeds × 2 N). The opposite
profile survives in **6 of 6**. Key and source each move survival more than position does, because
curation and reliability together weigh 0.25 against recency's 0.30 spread over N positions.

## Decision rule

None of this changes code by itself; it is the number the module has been missing. If the
in-passing profile is evicted as predicted, the finding is published and `prune`'s docstring names
the profile it evicts; a change to the value model is not made here — a scorer that ranks "allergy"
high is exactly the query-aware policy the paper says does not exist without training, and
inventing one on the strength of one fact would be tuning to the probe.

## What this cannot show

It is one value function against one constructed store; it says nothing about recall, about
consolidation (`consolidate` merges and deletes originals, a different eviction), or about what a
model does with a fact that survived. It cannot say the policy is wrong — a budget has to evict
something — only what it evicts first.
