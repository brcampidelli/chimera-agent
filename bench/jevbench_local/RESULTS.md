# Our local decision backend on the public JevBench — results

Run 2026-09-23 against PREREGISTRATION.md (written first; amendment 1 before the last 90 items). Ollama 0.32.0,
`qwen3:4b@Q4_K_M`, RTX 5070, US$ 0. 231 items per arm, pinned JevBench commit `2fa63fa`, scored by JevBench's own
`score_task` / `ece_top_label` / `brier_score`. Rows in `results/ctx.jsonl` and `results/ship.jsonl`;
`python -m bench.jevbench_local.read --jevbench <clone>` reprints every number below.

**This is not a leaderboard entry.** It measures accuracy and two calibration functions on the 231 public items
only; the JevBench Score also weighs speed and cost and the 308 sealed items.

## The numbers (arm A_ctx, the primary)

| | accuracy | Wilson 95% |
|---|---:|---|
| **all 231** | **143/231 = 0.619** | [0.555, 0.679] |
| original (72) | 59/72 = 0.819 | [0.715, 0.891] |
| easy (48) | 48/48 = 1.000 | [0.926, 1.000] |
| hard (111) | 36/111 = 0.324 | [0.244, 0.416] |
| noul / choice / score | 0.635 / 0.604 / 0.667 | |
| chance baseline | 0.318 | |

References over the same 231 items (JevBench v1.4): **Jev 1.13.0 0.866**, **SemIf/OpenJev (a fine-tuned 4B)
0.810**; Jev makes 0.741 on the hard tier.

Calibration, raw (no map exists for these questions), over the valid items: **top-label ECE 0.218** (hard alone
0.465), **Brier 0.495**.

## Against the predictions

| prediction | result | |
|---|---|---|
| A_ctx accuracy 0.70–0.80, loss concentrated on `hard` | 0.619 — the interval tops out at 0.679 | **refuted** (too high); the loss IS on `hard` |
| raw ECE above 0.15 | 0.218 | confirmed |
| A_ship loses ≥ 10 points on `hard`, over half of the hard prompts hit the ceiling | Δ **0.0 pp**, 0 of 111 truncated | **refuted** |

## Where the points go

**The hard tier's reasoning families are near zero.** multi_hop 3/18, temporal_numeric 1/15, long_policy 3/19,
probability 2/10, tradeoff 0/6, while judge_hard 12/17, trap 6/8 and routing_hard 4/5 hold. A 4B model read
decision-first — no reasoning trace, by design (arXiv 2601.13284: the token after a trace is extraction) — cannot
do arithmetic over a policy it has to read. That is what the instrument is and what it costs; the fix, if one is
wanted, is a different backend for those questions, not a tweak to this one.

**24 items had no reading, and 9 of them had the right label written.** All 24 wrote a label the schema allows;
the backend refused to read it because two options share their first token (`deny_…`, `sep_…`,
`pay_subject_to_…`) — and in `coding` / `coding_agent` one option is a prefix of the other, so neither can ever
be read (study 21 A4). 20 of the 24 are on the hard tier, whose labels are long snake_case phrases. Counted as
registered, they are wrong. Counting the written label (exploratory, not the result): 152/231 = 0.658. **This is
a fact about the product:** a `Choice` whose options collide on the first token is unreadable by the local
backend, and the linter does not say so today.

**The linter would refuse 49 of the 231 questions** — someone else's questions, bypassed for the measurement;
reported because it is a fact about the linter, not about the model.

## A_ship against A_ctx

Identical accuracy on every item (0 lost, 0 won), and not one A_ship prompt was shorter than its A_ctx twin. The
longest prompt in the whole set is **3 905 tokens**, under the default context this Ollama gives `qwen3:4b`.
The decision rule (a context PR if A_ship loses ≥ 5 points on `hard`) does **not** fire. Two notes that are not
decisions: if the default is the 4 096 the `spot_noul` bench hit, the margin is 191 tokens (not measured here —
no prompt reached it), so a longer state would still truncate, silently; and the raw ECE still moved (0.218 → 0.221) with the same answers, because a different
`num_ctx` changes the numerics of the same greedy read (§2ae: the ruler includes the configuration).

## What follows

* Per the decision rule: accuracy 0.619 and raw ECE 0.218 go into the `chimera decide` documentation as the
  independent number, beside the references — a separate small PR (the command reference is generated).
* Not decided here, proposed: the question linter gains a warning for options that collide on their first token
  (unreadable by the local backend). A crude proxy — options sharing the first word before `_` — flags 41 of the
  231 items (29 hard) and covers 22 of the 24 unread ones; the real check has to use the tokenizer, and its
  false-flag rate on readable items has to be measured before it ships.
