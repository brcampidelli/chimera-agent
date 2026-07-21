# Pre-registration — SWE-bench Verified

**Written and committed BEFORE any model call of this run.** Its purpose is to bind our hands:
everything below is decided while we cannot yet know the outcome. The commit that introduces this
file is the timestamp that matters. No re-running to chase significance; the result is published
whatever it says, as the Terminal-Bench null already was.

## Why this run exists

Every review of this project lands on the same gap. Chimera's one statistically-significant result
(`bench/local_lift`: 9% → 15%, +6pp, n=100) is a lift on a **self-authored** 100-task Python suite
that the README itself says is *"NOT SWE-bench, does not generalise to real repos"*. The one
external benchmark run (`bench/terminal_bench`) was a **published null** on a 40-task slice.

So the honest position today is: *the methodology is rigorous and there is no number on a
scoreboard anyone else uses.* This pre-registration points the same methodology at the benchmark
that actually ranks coding agents.

## Two questions, deliberately kept apart

They are different claims and conflating them is how benchmark marketing goes wrong.

### Q1 — the thesis (paired A/B)

> Does driving a **weak** model through Chimera's loop beat the same model answering alone, on the
> same SWE-bench instances, from the same base commit?

This is the project's central claim, tested where it is hardest: real repos, multi-file fixes,
graded by the project's own tests.

### Q2 — the scoreboard (absolute)

> What percentage of SWE-bench Verified does Chimera resolve?

This is what a reader comparing agents wants, and Q1 **does not answer it**. A lift from 4% to 8%
is a real answer to Q1 and an unimpressive answer to Q2. Both will be reported, side by side,
without letting the better one stand in for the other.

## Design (fixed now)

| | Q1 — paired A/B | Q2 — absolute |
|---|---|---|
| **Dataset** | SWE-bench **Verified-Mini** (curated slice), all instances in the slice | SWE-bench **Verified**, full 500 |
| **Model** | one cheap/weak model, fixed before the run (the *goldilocks* criterion from `local_lift`: weak enough to fail some instances alone, capable enough that a loop can recover some) | the strongest model we are willing to pay for, stated in RESULTS |
| **Arms** | **baseline** = `--no-plan --no-manager --max-attempts 1`; **treatment** = `--repo-map --progress-ledger --replan --checklist --max-attempts 3` (`_DEFAULT_FLAGS` in `chimera/eval/swe_bench.py`) | treatment arm only |
| **Pairing** | identical `instance_id` set, each arm from the same base commit in a fresh checkout | n/a |
| **Hygiene** | `--no-remember --no-collect --no-evolve-skills` on both arms — no cross-instance learning, so instance *k* cannot be helped by instance *k−1* | same |
| **Grading** | **SWE-bench's own harness**: `FAIL_TO_PASS` must pass and `PASS_TO_PASS` must stay green, run in the official Docker image. Never the agent's self-report, never our own parser deciding "looks fixed". | same |
| **Statistic** | paired McNemar + Wilson 95% CI on the delta (`chimera/eval/bench_ab.py`, the same code behind every other published number) | Wilson 95% CI on the pass rate |
| **Attempts** | single run per instance per arm. No best-of-N. | single run |

## Registered predictions

Written before the run, so a wrong prediction is on the record rather than quietly rewritten:

- **Q1:** small positive delta, **Δ ≈ 0 to +6pp**, and **probably NOT significant** at Verified-Mini
  sizes — for the same reason the n=15 local_lift run was inconclusive: McNemar only counts
  *discordant* pairs, and on hard instances both arms fail most of the time (floor) which contributes
  nothing. If the CI includes zero, that is the finding.
- **Q2:** **low single digits to low double digits**, i.e. far below the leaderboard leaders, which
  use frontier models and task-specific scaffolds. Chimera's claim is about *lift on a weak model*,
  not about topping SWE-bench.
- If either result contradicts the prediction — including a **negative** delta — that is the finding
  and it stands.

## Stopping and reporting rules

1. **One run.** No re-rolling, no "the API was flaky, let's redo it" without saying so in RESULTS.
2. **Every number ships with its CI**, including the losses.
3. **Failure accounting is published**: instances lost to infrastructure (Docker, network, rate
   limits) are reported separately from instances the agent genuinely failed. An infra failure is
   not scored as a model failure, and the count of them goes in the table.
4. **The cost is reported** — total tokens and USD per arm. A lift bought at 10× the price is a
   different result from a free one, and the reader gets to judge that.
5. If the run is abandoned (too expensive, harness broken), **this file stays** and RESULTS.md
   records that it was not completed. A pre-registration that quietly disappears when the result
   looks bad is worse than none.

## What is NOT claimed

- Verified-Mini is a **slice**, not full Verified. A Q1 result on the slice is not a Verified score,
  and will never be labelled as one.
- SWE-bench instances are Python. Nothing here generalises to other languages.
- The absolute number (Q2) depends heavily on the model. It measures *Chimera + that model*, and the
  model is named in the result.

## How to run it

The adapter and the boundary are already in the repo — deliberately, the dataset and the Docker
grading are **not**, because the verdict must come from SWE-bench's own test-based harness:

- `chimera/eval/swe_bench.py` — builds the `chimera solve` argv per instance and parses the official
  evaluation report into the pass/fail trials the A/B consumes (unit-tested).
- `chimera/eval/bench_ab.py` — the paired statistic.

Steps: fetch the dataset as JSONL → run both arms per instance → run the official SWE-bench
evaluation → feed the report to the adapter → write `RESULTS.md` next to this file with the table,
the CIs, the failure accounting and the cost.

**Status: registered, not yet run.** The run needs paid model calls and a Docker host; nothing in
this file has been executed. When it is, the result appears here — win, loss, or null.
