# Results — B4: a "System One" tool router inside our own loop

*Closed 2026-09-23. 414 of 414 cells (23 tasks × 2 arms × 3 executors × k = 3), **US$ 27.33** in the scored solves. Pre-registration `PREREGISTRATION.md`, committed before any scored solve, with six amendments, each committed before the data it could have been bent to fit. Raw: `results.json` (primary), `results-sensitivity.json` (§5).*

## The verdict in one line

**The router makes the agent worse on every executor, and worse the better the executor is.** Oracle score falls by **−0.087 / −0.194 / −0.307** (weak / strong / Sol), every 95% CI over tasks excludes zero, and task pass rate falls from **61% to 26%** on `deepseek-v3.2` and from **74% to 30%** on `gpt-6-sol`. The cost and step "savings" are real and they are the same event read from the bill: the router tells the loop to stop, and the work does not get done. **Not recommended for any executor; the flag stays opt-in, as registered.**

## 1. The table

| executor | floor (replica SD) | score off → on | **Δ score** [95% CI] | pass@0.8 off → on | cost | steps | router's share of cost |
|---|---:|---|---|---|---:|---:|---:|
| weak `gpt-oss-20b` | 0.108 | 0.296 → 0.209 | **−0.087** [−0.168, −0.014] | 4% → 0% | −4.8% (n.s.) | **−19.1%** | **37.3%** |
| strong `deepseek-v3.2` | 0.068 | 0.776 → 0.582 | **−0.194** [−0.304, −0.092] | 61% → 26% | **−52.9%** | **−31.8%** | 8.0% |
| Sol `gpt-6-sol` | 0.027 | 0.872 → 0.565 | **−0.307** [−0.434, −0.191] | 74% → 30% | **−53.8%** | **−35.7%** | 0.9% |

Paired by task over all 23; "floor" is the control arm's own replica-to-replica SD on this executor, these tasks and this grading environment — measured here, not imported (§4 of the registration). Relative to that floor the score effect is **0.8×** (weak), **2.9×** (strong) and **11.5×** (Sol).

## 2. What the router did (§2r — an intervention reports how much it acted)

| executor | router calls | narrowed to one tool | `ANSWER` (stop and reply) | fallbacks |
|---|---:|---:|---:|---:|
| weak | 267 | 254 | 8 (3.0%) | 5 |
| strong | 1,221 | 1,134 | 74 (6.1%) | 13 |
| Sol | 546 | 501 | 37 (6.8%) | 8 |

Positive control: every `on` solve narrowed at least once. Negative control: **0** control solves carry a router receipt, on all three executors. The picks are what a person would expect (`read_file` first, then `run_shell`, `list_dir`, `write_file`), so the router is not choosing nonsense — it is choosing *one* tool from a *shallow* view, and `ANSWER` from that same view ends the loop.

**The mechanism, in one sentence:** the more often the router says `ANSWER`, the larger the damage — 3.0% → 6.1% → 6.8% of steps across weak → strong → Sol, the same order as the score loss. A shallow reader that sees the task and the last output cannot tell "done" from "one step from done", and a better executor had more steps left to take.

## 3. Against the registered predictions

| | prediction | outcome |
|---|---|---|
| P1 | strong: score ties inside the floor | **refuted** — −0.194, 2.9× the floor, CI excludes zero |
| P2 | strong: steps **+10% to +20%** | **refuted in sign** — **−31.8%**. I predicted the router would pick cheap tools and the loop would take more of them; it ends the loop instead |
| P3 | weak: a **gain** above the floor | **refuted** — a loss (−0.087, CI excludes zero), smaller than the weak executor's own replica noise (0.108) |
| P4 | router < 10% of cost; arm cost not lower by more than the floor | **half refuted** — 8.0% / 0.9% on strong / Sol but **37.3%** on weak (a cheap executor makes the router expensive by comparison); and cost *is* much lower (−53%) on both paid executors, because the work is not done |
| P5 | chained `011`: no better, plausibly worse | **confirmed** — equal on strong and Sol (0.170 = 0.170), **0.113 → 0.000** on weak |
| S1 | Sol: the cost effect **inverts** (on costs more) | **refuted** — on is **−53.8%**. My account was that the router destroys cache reuse and that lost cache outweighs saved steps; the measured account is simpler and worse for the router: on Sol the `on` arm stops so early (7.9 steps against 11.6) that it never accumulates the context a cache would have served. The saving is not cheaper context, it is **absent work** |
| S2 | Sol: steps fall, by less than 37% | **confirmed** — −35.7% |
| S3 | Sol: score effect no better than strong's; control above 0.714 | **confirmed** — −0.307 against −0.194; control 0.872 |

Six refuted or half-refuted, three confirmed, and two of the refutations are mine about *direction*: I expected more steps and I expected the router to cost more on an expensive model. Both were wrong for the same reason — I modelled the router as changing *how* the loop works; it changes *when the loop stops*.

## 4. Decision

- **The flag stays opt-in, and is not recommended for any executor.** The registered rule recommended it for weak executors only if P3 held; P3 was refuted.
- **Cost per unit of work is not a reason to use it.** On Sol, US$ per point of oracle score is lower with the router (0.20 against 0.23), but that is a lower price for a smaller, incomplete deliverable with `--max-attempts 1`. Whether a cheaper, shorter attempt plus a retry beats one full attempt is a different experiment, and it is the only one that could rescue this idea (§6).
- **No product default changes.** `chimera/core/tool_router.py` remains in the tree because the bench needs it to be reproducible and because its tests hold two properties worth keeping (it narrows and never decides; it reads a shallow context on purpose) — a future deep-router experiment starts from them.

## 5. Sensitivity: the four cells that failed twice

Amendment 4 made a cell that fails twice **missing**, not retried until it succeeds (retrying until success selects for the fast solves). Four cells failed twice *before that rule existed* and completed on a third attempt: `042` strong-on r0, `043` weak-on r2, `086` strong-off r2, `040` weak-off r1 — two `on`, two `off`, strong and weak. Recomputed with the four missing:

| executor | Δ score primary | Δ score, four cells missing |
|---|---:|---:|
| strong | −0.194 [−0.304, −0.092] | −0.203 [−0.318, −0.096] |
| weak | −0.087 [−0.168, −0.014] | −0.086 [−0.168, −0.013] |
| Sol | −0.307 | −0.307 (none of the four were Sol) |

Nothing moves outside the third decimal; the primary stands.

## 6. The apparatus record — every halt and its cause

The registered stop rule (more than 5% of solves rc≠0 or receiptless) fired **six** times. It was never loosened; each halt was investigated and amended:

| # | cause | amendment |
|---|---|---|
| 1 | registered weak executor `mistral-small-3.2-24b` **rate-limited upstream** (429, shared pool) | 1 — `gpt-oss-20b`, chosen before any weak result was read; 12 weak solves discarded |
| 2 | provider **timeout** at 600 s killed the CLI after grading → scored solve with no receipt; **and** the harness exits 0 when the command it ran died | 2 — resume requires a receipt; timeout 1800 s |
| 3 | at 1800 s a hung call ate the wrapper's 2400 s budget | 3 — timeout 900 s |
| 4 | 4 timeouts **within 4 seconds** — one outage across all arms in flight | 3a — recorded as an outage, not a rate |
| 5 | 2 timeouts **within 1 second** | 4 — freeze after two failures; a relaunch continues the queue |
| 6 | same | 5 — the retry is spent inside the run; only a cell failing twice counts |

Two more defects were found that the stop rule could not see, both of the "nothing gives an error" family:

- **The Sol arm was never running.** `--max-usd` refuses fail-closed on a model with no price and `gpt-6-sol` shipped the day of the run; every Sol solve died in ten seconds. Fixed with a catalogue row, not by dropping the cap.
- **A receipt that said "cost unknown" counted as a free, finished solve.** The driver summed `usd or 0.0`. Fixed: an unpriced round makes the cell missing.

One Sol solve cost **US$ 2.28** against a `--max-usd 2.0` ceiling (`086`, on, r1): the cap is checked before each call and charged after it, so the last call can overshoot by one call. Reported, not corrected — it is the documented semantics of the ceiling.

## 7. What this cannot show (§2q)

- A **deep** router, one that reads the conversation — the design choice under test was the shallow one, on purpose.
- **Retries.** Every solve is `--max-attempts 1`. A cheaper short attempt plus a retry is the one configuration in which the router's cost per point could turn into a better pass rate, and it is not measured here.
- A **better router model**: one router (`deepseek-v4-flash-0731`), named in advance.
- Comparison of absolute pass rates with #453: the grading environment differs (`pytest` and `node` installed here, §4 of the registration).
- Anything about `gpt-6-sol` beyond the day it shipped, on OpenRouter's standard routing.
