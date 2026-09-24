# M4 — a catch-all option on a Choice: results

*2026-09-23 · US$ 0 · local `qwen3:4b` through the product path · 3 arms × 1,000 frozen commit subjects, 0 errors ·
`PREREGISTRATION.md` and the runner were committed before any arm ran · `python -m bench.other_rule.run --read` reprints it.*

## Verdict: the catch-all as shipped hurts — narrow it or leave it out, as the skill says, now with the number

| arm | macro-F1, 4 named classes | accuracy on the 678 named items | recall feature / fix | named items answered `other` | `other` items kept as `other` | accuracy, all 1,000 |
|---|---:|---:|---:|---:|---:|---:|
| **current** (broad `other`) | 0.236 | 0.192 | 0.222 / 0.224 | **78%** (530) | 0.876 | 0.412 |
| **narrow** `other` | 0.346 | 0.358 | 0.469 / 0.342 | 58% (394) | 0.820 | **0.507** |
| **none** | **0.426** | **0.674** | 0.761 / 0.833 | 0% | 0 *(by construction)* | 0.457 |

**Primary, macro-F1 against current** (paired bootstrap over items, 95%):
- narrow **+0.110** [+0.069, +0.162];
- none **+0.191** [+0.159, +0.226].

Both intervals exclude zero.

**Paired control:** the current arm, re-measured today, reproduces `bench/decide_interface`'s published 0.412 exactly.

## Against the predictions

| prediction | outcome |
|---|---|
| (c) none > (a) current on the primary by ≥ 0.10 | **confirmed** — +0.191 |
| (b) narrow lands between them | **confirmed** — 0.236 < 0.346 < 0.426 |
| (a) keeps > 80% of the `other` items | **confirmed** — 87.6% |
| (b) keeps fewer | **confirmed** — 82.0% |

## What it means

- **What the broad catch-all does.** Almost four in five items that belonged to a named class were answered `other`, so the model uses the catch-all as the default. The vendor's advice to put `other` in every Choice is exactly the shape that failed here.
- **What the two alternatives cost:**
  - **Leaving it out** is best for the named classes, but items truly outside the set, a third of this population, must land on a named class.
  - **Narrowing it** keeps 82% of those and still almost doubles named-class accuracy, which makes it the best on all 1,000.
- **Which to pick.** It depends on how much of the population is out of set: with few out-of-set items, leave it out; with many, narrow it. Either beats the broad one.

**Decision (the registered rule):** the skill keeps "narrow it or leave it out" and gains the number and the price. No product default changes, since the only product Choice with a catch-all is the demo in `bench/decide_interface`.

## What this cannot show

- Other models.
- Tasks other than this easy-to-state one.
- Wordings other than these three.
- Whether a narrow catch-all helps a Choice whose out-of-set share is small; here it is 32%.
