# Results — `prune` evicts a short fact mentioned early, whatever else is true about it

Run 2026-09-11 · 288 cells, deterministic, US$ 0 · prereg `PREREGISTRATION.md` (committed before
the script ran) · full table in `results/table.md`, cells in `results/table.json`.

## The number

At the paper's **20% budget**, the fact *"the customer is allergic to penicillin"* survives
`MemoryManager.prune` in **7 of 144** cells — and every one of the seven is the profile *written
last, `semantic`, keyed* (user-written 6/6; agent-written 1/6). Every other profile, including a
keyed, user-written, semantic fact written **first**, is evicted **0/6**.

| axis (20% budget, everything else pooled) | kept |
|---|---|
| position: first / middle / last | 0/48 · 0/48 · 7/48 |
| kind: episodic / semantic | 0/72 · 7/72 |
| key: none / keyed | 0/72 · 7/72 |
| source: agent / user | 1/72 · 6/72 |

The paper's profile — early, incidental, unkeyed, agent-written, episodic — is evicted **0/6**, as
registered. Its opposite survives **6/6**, as registered. At a 50% budget the picture loosens to
42/144, still nothing written first survives (0/48), and *middle* survives only when keyed and
user-written and semantic (6/6).

## What was wrong in the prediction

The registered prediction said *key and source each move survival more than position does*. **Wrong
at 20%: position decides everything.** A fact written first has recency ≈ 0 under `rank()`'s
position proxy, and no combination of the other four factors buys back the 0.30 that recency spans.
Curation and source separate the survivors only among facts written last. The prediction was an
argument from the weights (0.15 + 0.10 vs 0.30); the table is the measurement.

## Why, in the value function's own terms

`value()` (`chimera/memory/value.py`) is a weighted sum of five factors, and this fact scores low
on **three of them by being what it is**:

- **recency** (0.30): the position in the store — mentioned early means near zero;
- **specificity** (0.20): `len(content) / 200` — a 40-character fact scores 0.2 of the possible
  1.0. The model reads *longer* as *more specific*, so the short sentence that matters most is
  taxed for being short;
- **kind** (0.25): `episodic` 0.5 against `semantic` 0.8 — an incident is worth less than a
  statement.

There is no factor for *what the fact is about*, and there cannot be one without a query — which
is the paper's point, stated in the module's own docstring: forgetting under a budget is decided
before future queries are known. A budget must evict something; this is what it evicts first.

## What survives, and the instruction that already exists

`persona` items are never pruned (6/6 at 20%). The mechanism that keeps a fact through any budget
is the one the CLI already offers — `chimera memory add "…" --persona` — and it is a decision the
writer has to make at write time, which is exactly the "auditable episodic store alongside; an
interface that asks rather than invents" the paper recommends. Nothing here changes the value
model: a scorer that ranks "allergy" high would be the query-aware policy the paper says does not
exist without training, tuned to one probe.

**What changed in code:** `prune`'s docstring names the profile it evicts and points at `persona`;
a test pins the eviction so a change to the value model that alters it has to say so.

## What this cannot show

One value function, one constructed store profile, one fact. Nothing about recall, about
`consolidate` (which merges clusters and deletes the originals — a different eviction, worth its
own table), or about what a model does with a fact that survived.
