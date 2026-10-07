# S30-54 — decision readout invariance on the local decider: results

**Run.** 2026-10-06, local `qwen3:4b` through the shipped `LocalLogprobBackend`, US$ 0.

- **Requests.** 2,656 in all: 2,282 rows over the 231 public JevBench items at the pinned commit
  `2fa63fa`, and 660 rows over the 55-item governance corpus.
- **Data.** Every row is in `results.jsonl`; the full printed report is in `report.txt`.
- **Report-step fix.** The first full run crashed in the report step after every row was recorded:
  the baseline was being McNemar-tested against itself. That line was fixed, and the report was
  re-run over the same rows with no new model call.

**The control reproduces.** On the JevBench rows, the baseline reads 0.615, with 23 items unread.
The September measurement (`bench/jevbench_local`) read 0.619, with 24 unread.

## JevBench (231 items; unread counts as wrong)

| arm | accuracy | unread | ECE | McNemar vs baseline (b / c, p) |
|---|---:|---:|---:|---|
| baseline (today's rendering) | 0.615 | 23 | 0.224 | — |
| **C_numeric** (prefix-free digit ids, prefilled readout) | **0.688** | **0** | 0.151 | 23 / 40, p = 0.043 |
| letters + K-rotation (one rotation) | 0.693 | 0 | 0.168 | 12 / 30, p = 0.008 |
| letters + K-rotation, averaged | 0.680 | 0 | **0.132** | 18 / 33, p = 0.049 |
| two-call label swap | 0.693 | 0 | 0.170 | 12 / 30, p = 0.008 |
| `neutral()` (Choice items only, n = 139) | 0.727 | 0 | 0.146 | 6 / 24, p = 0.001 |
| definition only (n = 139) | 0.683 | 0 | 0.162 | 10 / 22, p = 0.050 |

The label-swap test agrees with itself on only **64.9%** of items. Swapping which option sits under
which letter changes the answer on a third of them, so the letters are being read, not only the
definitions.

## Governance (55 items, the production `DANGER` question)

| arm | accuracy | errors | ECE | McNemar vs baseline (b / c, p) |
|---|---:|---:|---:|---|
| baseline | 0.673 | 18 | 0.201 | — |
| **C_numeric** | **0.873** | **7** | 0.087 | **1 / 12, p = 0.003** |
| letters + K-rotation, averaged | 0.745 | 14 | 0.104 | 0 / 4, p = 0.125 |
| `neutral()` | 0.709 | 16 | 0.084 | 0 / 2, p = 0.5 |
| definition only | 0.636 | 20 | 0.072 | 3 / 1, p = 0.625 |
| label swap | 0.691 | 17 | 0.082 | 1 / 2, p = 1.0 |

Label-swap agreement on governance is only **43.6%**. The single-rotation flips against baseline
were 24, 14 and 10 for rotations 0, 1 and 2.

### Negation (binary rewording of the same governance items, n = 55)

- The affirmed wording scored **0.636**; the negated wording, inverted back, scored **0.491**,
  which is chance for a yes/no question.
- The negated wording loses against the baseline (11 / 1, p = 0.006).
- 10 of the 55 mapped answers disagree between the two wordings.
- ECE: 0.323 affirmed, 0.444 negated.

## Verdict, by the registered rule

The registration says that **no rendering becomes the default on this run alone.** An arm is
eligible for a separately reviewed proposal only if it meets every condition below.

| condition | C_numeric |
|---|---|
| JevBench unread no higher than baseline | **met** (0 vs 23) |
| JevBench accuracy not lower | **met** (0.688 vs 0.615) |
| governance errors and unread not higher | **met** (7 vs 18; 0 unread) |
| governance flip count strictly lower | **not established**: a single-rendering arm has no rotation flips of its own to compare, and the registration does not say which flip count applies to it |

So C_numeric is the strongest arm on both corpora. It is the clear candidate for a separately
reviewed proposal, but this run does **not** make it eligible. Even with that settled, shipping
stays blocked until a governance calibration map is refitted and validated for its exact instrument
hash, and the safety owners approve it. The default is unchanged.

**On negation.** The local decider loses about 15 points when the same question is asked in the
negative, landing at chance. This is the evidence the item asked for. Raising the `negated` lint
from warning to error is a separate change.

## What this cannot show

- One local 4B model at Q4_K_M.
- The governance corpus is small (55 items), so its intervals are wide.
- The hosted Jev and other backends were not measured. This run is about how the shipped *local*
  backend reads options, not about any vendor.
