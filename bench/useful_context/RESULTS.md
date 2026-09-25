# No knee up to 128k: the default model holds an agent's context to the top of the ladder

Run 2026-09-25 against [`PREREGISTRATION.md`](PREREGISTRATION.md) and its post-pilot addendum.
**566 calls (26 pilot + 540 main), US$ 1.3625 measured, 0 errors, 0 mis-routed rows** — every row was
answered by `DeepInfra`. Tables reproduce from the committed rows with
`python bench/useful_context/run.py --report bench/useful_context/results/main.json`
(output in `results/main-report.txt`, numbers in `results/main-summary.json`).

## The answer, under the registered rule

| | |
|---|---|
| DeepInfra's served window for this model | **1,048,576** tokens (max output 384,000, fp8) |
| Useful length | **≥ 128k** — the 128k cell, median **128,109** provider tokens. The top of the ladder, so a **lower bound** |
| Proposed trigger, `floor(0.8 × useful, to 1,000)` | **102,000** provider prompt tokens |
| The same as a `context_budget` fraction of the catalogue's 1,048,000 window | **≈ 0.122** (threshold = window × fraction × 0.8) |
| Today | Code screen compacts at **503,040** (0.6 of 1,048,000, × 0.8); `chimera solve` never |

## The gates

| gate | result |
|---|---|
| grader self-test (74 synthetic answers) and corpus collisions | PASS, 0 collisions |
| pilot 4k gate (≥ 18/20) | **20/20** |
| positive control, main 4k (≥ 90%) | **90/90** |
| floor: 4k replayed byte-for-byte | **0/90 discordant**, Newcombe [−4.1, +4.1] pp → FLOOR GATE PASS; **90/90 byte-identical answers** at T = 0 |
| routing (≤ 5% mis-routed) | 0/566 |
| halts | 0 |

## Per length (n = 90 items, the same items at every length)

| length | median tokens | ok | accuracy | Wilson 95% | fact_ok | rule_ok (single-service answers) | breakage | forgetting |
|---:|---:|---:|---:|---|---:|---:|---:|---:|
| 4k | 4,325 | 90 | 1.000 | [0.959, 1.000] | 90 | 90/90 | 0 | 0 |
| 16k | 16,232 | 90 | 1.000 | [0.959, 1.000] | 90 | 90/90 | 0 | 0 |
| 32k | 32,281 | 90 | 1.000 | [0.959, 1.000] | 90 | 90/90 | 0 | 0 |
| 64k | 64,214 | 89 | 0.989 | [0.940, 0.998] | 89 | 90/90 | 0 | 1 |
| 128k | 128,109 | 87 | 0.967 | [0.907, 0.989] | 87 | 88/88 | 2 | 1 |
| 4k replay | 4,319 | 90 | 1.000 | [0.959, 1.000] | 90 | 90/90 | 0 | 0 |

The curve, 4k → 128k: **1.000, 1.000, 1.000, 0.989, 0.967.** Every cell arrived within 3% of its
target except 4k, which the addendum predicted would land a little under 4,000 and landed at 4,325.
The addendum had the direction backwards: the 4k transcript is prose-heavy at 3.77 characters per
token, so assembling it at 4.08 produces **more** tokens than intended, not fewer. The cell is reported
at its realised count, as registered.

## Paired against 4k (Newcombe method 10, 95%; exact McNemar)

| length | both ok | long only | 4k only | neither | Δ | 95% CI | p | margin −10 pp |
|---:|---:|---:|---:|---:|---:|---|---:|---|
| 16k | 90 | 0 | 0 | 0 | 0.0 | [−4.1, +4.1] | 1 | within |
| 32k | 90 | 0 | 0 | 0 | 0.0 | [−4.1, +4.1] | 1 | within |
| 64k | 89 | 0 | 1 | 0 | −1.1 | [−6.0, +3.1] | 1 | within |
| 128k | 87 | 0 | 3 | 0 | −3.3 | [−9.3, +1.3] | 0.25 | within, **by 0.7 pp** |

Every length is within the margin, so by the fixed sequence the useful length is the 128k cell and the
answer is a lower bound. Two things the table says that the verdict alone does not:

- **The margin nearly binds at 128k.** One more loss there would have put the lower bound at about
  −10.9 pp (precomputed table, n = 90, four net losses) and made the useful length 64k.
- **The failures rise with length — 0, 0, 1, 3 — but no step is significant** (p = 0.25 at 128k). The
  4k replay reproduced every answer byte for byte, so this route at T = 0 is close to deterministic
  and the losses are not replay noise; which items fail is still a sample. Whether this is the start
  of a knee just above 128k is exactly what this ladder cannot see.

## The four failures, read by eye

| item | length | target depth | kind (frozen grader) | what happened |
|---|---:|---:|---|---|
| M054 | 64k | 10% | `wrong_service` | asked for Greece (Acropolis, runbook at 10%); answered `TALLOW_GATE`, the runbook at **90%** (Wawel Castle, Poland). No reasoning emitted. Rule applied. |
| M014 | 128k | 90% | `wrong_service` | asked for Austria (Schönbrunn, runbook at 90%); answered `[fennel-queue]`, the runbook at **10%** (Wawel Castle, Poland). No reasoning emitted. Rule applied. |
| M055 | 128k | 50% | `no_service` | answered **`QUIVER_SORTER`** for `QUILL_SORTER`. Its reasoning found the right runbook, the right association (Hallgrímskirkja → Reykjavík → Iceland) and the right format, and **corrupted the name while copying it**: *"The service was quill-sorter → QUIVER_SORTER."* |
| M087 | 128k | 10% | `tool_call` | answered with a tool call despite *"without calling any tool"*. No reasoning emitted. **The tool's name and arguments were not recorded** (the runner kept only the count) — a gap in the record, stated rather than guessed. |

What reading them changes, and what it does not:

- **The early rule was never forgotten.** `rule_ok` is 100% at every length, including both wrong
  answers and the corrupted one — each is written in the item's format. The drop is retrieval,
  association and copying, not the constraint stated at the start.
- **M055 is misfiled by the frozen categories.** The grader could not find any of the five names, so it
  filed the row under `no_service`, which the registration counts as *format breakage*. By eye it is
  neither a refusal nor off-task: it is a copy error on an arbitrary string, closer to forgetting. The
  primary outcome is unchanged (a failure either way); the split is reported both ways: **frozen:
  breakage 2, forgetting 1 at 128k; by eye: breakage 1 (the tool call), forgetting/copying 2.**
- **Both wrong services came from the opposite end of the filler** (target at 10% → picked 90%, and the
  reverse), and both picked a Wawel Castle runbook. Two cases; descriptive only.
- **Three of the four failures emitted no reasoning.** Across the 360 rows at 16k–128k, 20 skipped
  reasoning, and 3 of those 20 failed, against 1 of the 340 that reasoned. Not registered, not a
  finding — a hypothesis for whoever measures `thinking=False` at length.

The registered **secondary**, forgetting-only accuracy (breakage rows dropped): 64k 89/90, 128k 87/88.

## Predictions, scored

- **P1** 4k control passes — **right** (90/90).
- **P2** nothing up to 64k outside the margin — **right** (64k: Δ −1.1 pp, CI [−6.0, +3.1]).
- **P3** useful length at the top of the ladder — **right, narrowly** (lower bound −9.3 pp against −10).
- **P4** `wrong_service` outnumbers `rule_forgotten` — **right** (2 against 0); the middle depth fails
  more — **wrong** (at 128k one failure at each depth; at 64k the one failure was at 10%).
- And one that was not written down but was expected from the literature: **NoLiMa's drop by 32k did not
  appear** on this model and this shape (90/90 at 32k). Why is not visible from here; see the limits.

## Two-sided outcomes

| length | median reasoning tokens | median latency | cache-read share | cost of the cell | tool-call answers | refusals |
|---:|---:|---:|---:|---:|---:|---:|
| 4k | 42 | 2.3 s | 0.52 | US$ 0.0151 | 0 | 0 |
| 16k | 58 | 3.5 s | 0.07 | US$ 0.0845 | 0 | 0 |
| 32k | 64 | 4.0 s | 0.04 | US$ 0.1699 | 0 | 0 |
| 64k | 63 | 5.3 s | 0.02 | US$ 0.3425 | 0 | 0 |
| 128k | 72 | 8.1 s | 0.01 | US$ 0.6873 | 1 | 0 |

Cost is linear in prompt tokens at US$ 6.0e-8 per token: a single 128k call is US$ 0.0077, a single 4k
call US$ 0.0002. The cache served 1,024 tokens (the system prompt and tool schemas) on most rows, up to
~4.8k where one item's renders at two lengths share their opening, and — at 4k only — the whole prompt
on 69 of 180 rows, because the 4k call and its replay are byte-identical and whichever ran second read
the first one's cache. So the cache share is half the prompt at 4k and 1% at 128k. It did not move the
answers: the replay's answers are byte-identical to the originals, cached or not.

## What this means for the trigger

The proposal is **102,000 provider prompt tokens**, by the rule registered before the run. Two readings
of it, both true:

- **Against today's 503,040 it is five times lower**, and it is set from the model rather than from the
  window; it applies equally to `chimera solve`, which today never compacts.
- **It is still above everything production has done.** The largest context ever traced is 64,067, so
  on the observed workload a 102k trigger would still almost never fire. That is not a defect of the
  number: it says that on this shape the model does not need compaction below 128k, and compaction's
  job in production is to prevent the provider's hard wall and whatever lies past 128k, not to rescue
  64k runs.

It is a lower bound because the ladder stops at 128k. `context_rot` measured a simpler probe flat to
953k on `Baidu` and 10/10 at 792k on `DeepInfra`; this bench cannot say whether the harder shape's
trend (0, 0, 1, 3) turns into a knee at 256k or stays flat to the window. The next measurement that
would move the trigger up is the same 90 items at 256k (about US$ 1.39 on this route) and 512k (about
US$ 2.77); nothing here licenses raising the trigger above 102,000 without it.

No product code was changed. Adopting a trigger is the coordinator's call, in a separate PR.

## What this cannot show

- **One model, one endpoint, one day.** `DeepInfra` only; `context_rot` found two of eight endpoints
  behind this slug behaving differently at length.
- **One task shape.** A lookup needing one association, one retrieval and one naming rule. Not
  multi-step reasoning, not editing, not aggregation, not a rule given mid-conversation.
- **The needle stands out from the haystack.** The five runbooks are prose among Python sources; a
  needle that looks different from its surroundings is easier to find than NoLiMa's prose-in-prose, and
  that is one candidate reason the literature's 32k drop did not appear. A transcript of mostly prose
  (docs, tickets, chat) is untested.
- **Depth is sampled, not decided.** The target sat at 10/50/90% of the filler; per-depth rows are
  descriptive (29–30/30 everywhere). A needle 2k tokens from the question against one 100k away is a
  different quantity, and this design fixes relative depth, not absolute distance.
- **Nothing above 128k.**
- **One completion, not a loop**, with four read-only tool schemas instead of ~20, and T = 0 instead of
  the product's 0.2. In production the agent could re-read the runbook with a tool.
- **The trigger is derived from one shape.** It is a length under which this shape is safe, not a
  guarantee for every task.

## Cost

| | calls | input tokens | output tokens | measured |
|---|---:|---:|---:|---:|
| pilot | 26 | — | — | US$ 0.0475 |
| main | 540 | 22,453,478 (873,472 cached) | 39,692 | US$ 1.3150 |
| **total** | **566** | | | **US$ 1.3625** of the US$ 1.50 cap |
