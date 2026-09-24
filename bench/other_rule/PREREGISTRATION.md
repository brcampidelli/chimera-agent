# M4 — does a catch-all option help or hurt a Choice? Pre-registration

*2026-09-23, written before any arm was run. Study 24, item M4 (`bench/PLAN-study24-jev-practice.md`).*
*Local only (`qwen3:4b` through the shipped `LocalLogprobBackend`), US$ 0.*

## Why

**Two rules that disagree:**
- Our skill (`skills/system-one-design`, Do 3) says: give a catch-all ("other") "a narrow criterion or leave it out".
- The vendor's documentation says: put "other" in every Choice.

Neither was ever measured. The one bench we have points at a cost. In `bench/decide_interface` the catch-all took **258 of 343 feature subjects and 177 of 228 fix subjects**. Accuracy was 0.412, against a majority baseline of 0.343.

## Items

`bench/decide_interface/results/items.jsonl` — frozen: 1,000 commit subjects with the conventional prefix cut off, the prefix being the answer key.

| truth | items |
|---|---:|
| feature | 343 |
| other | 322 |
| fix | 228 |
| docs | 90 |
| test | 17 |

The file is read at its committed bytes; the runner refuses it if its sha256 differs.

## Arms

All three arms keep the same instructions and the same criteria for the four named classes. They differ only in the catch-all:

- **(a) current** — `other`: "anything else: a benchmark, a refactor, a build or release chore, a dependency bump". This is the shipped wording, **re-measured today**, not read from the old answers.
- **(b) narrow** — `other`: "only when the subject plainly names a benchmark, a refactor, a build or release chore, or a dependency bump — never a subject that adds or corrects something".
- **(c) none** — four options, no catch-all.

## Outcomes

- **Primary: macro-F1 over the four named classes, on the 678 items whose truth is a named class.**
  - A named-class item answered `other` counts wrong.
  - Reported as the paired difference (c) − (a) and (b) − (a), with a 95% bootstrap CI over items.
- **Secondary:**
  - accuracy on those 678 items;
  - recall of `feature` and of `fix`;
  - on the 322 `other` items, the share answered `other` in (a) and (b). This is the catch-all's reason to exist, so its cost is reported beside its gain;
  - accuracy over all 1,000, where arm (c) can never be right on an `other` item, by construction.

## Predictions

- **(c) > (a) on the primary by ≥ 0.10.** The catch-all absorbs most named items today.
- **(b) lands between (a) and (c).**
- **(a) keeps most of the `other` items** (> 80% answered `other`), and **(b) keeps fewer** — the price of narrowing.

## Decision rule

- **If (b) or (c) beats (a) on the primary with its CI excluding zero:** the skill keeps "narrow or leave it out" and gains the number. It also names the price: out-of-set items then land on a named class.
- **If neither does:** the sentence is rewritten to say the measurement found no difference.
- **If (a) wins:** the sentence is reversed.
- **No product default changes.** The only Choice in the product with a catch-all is this demo.

## What this cannot show

- other models;
- other tasks, since one easy-to-state task is not the family;
- a catch-all on a Choice whose population has few out-of-set items;
- wording other than these three.
