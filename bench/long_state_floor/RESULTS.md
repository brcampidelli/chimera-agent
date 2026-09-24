# M3 — does our local backend give the same number twice on a long state? Results

*2026-09-23/24 · US$ 0 · `qwen3:4b@Q4_K_M` through the shipped instrument (`num_ctx` 16384, temperature 0) · 30 longest
JevBench hard-tier states (2,302–3,905 prompt tokens) × 5 byte-identical rounds in shuffled order · `PREREGISTRATION.md`
and the runner committed before any replay · `python -m bench.long_state_floor.run --read` reprints it.*

## Verdict: no flip — but the number moves up to 0.036 on a long state, so the floor is published

| | prediction | outcome |
|---|---|---|
| argmax flips across the 5 rounds | 0 / 30 | **0** of the 22 items read |
| maximum \|Δp\| of the first-round argmax label | ≤ 0.02 | **0.036** — refuted |
| median \|Δp\| | — | 0.003 |
| items above 0.02 | — | 5 of 22 |
| items that did not move at all | — | 4 of 22 |
| items unread in some rounds only | — | **0** |
| items unread in every round | — | 8 — the first-token collisions, deterministic |

**Where the movement is.** It sits in the **middle of the scale**:

| item | p range over 5 rounds |
|---|---|
| `long_policy-08` | 0.391–0.428 |
| `multi_hop-05` | 0.606–0.640 |
| `long_policy-11` | 0.654–0.682 |
| `long_policy-04` | 0.623–0.651 |

Items near 0.9 move by 0.02 or less. The requests are byte-identical and greedy, so what changes between rounds is the order, and with it the prompt prefix Ollama can reuse from the previous call (study 21 saw prefix reuse flip 5–6 of 777). That is the likeliest cause, not an isolated one: an arm that disables prefix reuse would be needed to prove it.

**A reader bug, fixed before this was written.** The first read listed 8 items as "unread in some rounds only". They are unread in **every** round: `deny_…`, `tier2_…`, `privacy_and_security` and friends, the first-token collisions the backend refuses to read. So they are deterministic, not variance. The reading rule did not change; the label did, in its own commit.

## Against the practitioners' claim

**The claim was that Jev varies between identical runs.** This bench does not measure Jev, and says so.

**What it measures is our instrument, and the answer has two parts:**
- **Labels:** our instrument does not change its answer on replay, even on long states, with 0 flips.
- **The number:** it does move, by up to 0.036 in the middle of the scale, which the record had said it would not. Our "0 flips in 55×5" was about labels on short states. It never measured the number's jitter on long ones.

## What the registered rule sends where

**Published beside the two nulls it could affect.** Both RESULTS pages now carry the floor in a note: `bench/spot_noul` (AUROC 0.655) and `bench/manager_p` (0.644 overall / 0.525 within task). Their nulls were read without a floor of their own on long states.

A floor of ≤ 0.036 in `p` can reorder two items whose `p` differ by less than that. It cannot manufacture or erase an AUROC gap of the size those benches report. So the nulls are expected to stand; each re-read is owed in its own follow-up.

**For the skill,** its new check ("ask the unchanged question again before reading a wording change") now has a local number behind it: 0.036 on long states.

## What this cannot show

- Jev, or any hosted route.
- Other models or quantisations.
- Temperature above 0.
- Short states: those are `bench/jev_decisions` §3.
