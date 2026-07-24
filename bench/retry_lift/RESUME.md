# Resume note — retry-lift run 1b (session restart 2026-07-24)

Bruno restarted Claude mid-run. This is how to pick up without re-explaining anything.

## State when the session ended
- Run **1b** was live: `bench/retry_lift/run_retry.py`, 3 arms × 40 hard tasks × 3 seeds = 360 solves.
- **~255 of 360 solves done** (through seed 2 complete, into seed 3). The background process was a
  child of the old session and almost certainly **died on restart**.
- The runner only writes `results/retry.json` at the very end, so the in-progress run left **no JSON**.
  Its stdout was salvaged to **`results/run1b_stdout.log`** (committed) — that is the only record of
  the ~255 completed solves. Do not delete it.

## Partial numbers at last read (n=80/arm, seeds 1–2)
```
control  pass 61.3%  A 67.5%  R 42.6%   stall=4
i1       pass 67.5%  A 67.5%  R 51.9%   diff=77 stall=4
i2       pass 65.0%  A 55.0%  R 36.4%   stall=4

I1 vs control (paired n=80): +6.2%  CI[-4.8%,+15.4%]  ns   retry-lens n=43: 34.9% vs 48.8%
I2 vs control (paired n=80): +3.8%                    ns   retry-lens n=35: 28.6% vs 31.4%
```
Read `results/run1b_stdout.log` with `python bench/retry_lift/parse_partial.py <log>` to recompute.

## Two things already learned (hold regardless of how it finishes)
1. **I1 shrank from +12.5% (n=40) to +6.2% (n=80)** — landed inside the pre-registered band (+3..+10pp),
   CI still includes 0, exactly as predicted. Direction positive, underpowered.
2. **I2 is trending "inert, not refuted":** its stall count EQUALS control (4 vs 4), so approximate
   matching found the same stalls as exact. If it finishes tied, the §7 gate reports the i2 arm as
   *the control arm* and its delta as noise — a real finding, but not about the idea.

## How to finish (the honest option)
"Seed" here is just a **repetition index** — the runner seeds no RNG; variation is model
nondeterminism. So the ~105 missing solves are recoverable cheaply:

**Preferred — run just the remaining repetition and merge:**
1. Run seed 3 only: `BENCH_SEEDS=1` won't help (it starts at seed 1). Instead run the whole thing
   again capturing stdout, OR add a one-off script that runs 40 tasks × 3 arms for a single seed and
   appends to a fresh log. Simplest: re-run `run_retry.py` with `BENCH_SEEDS=1` into a NEW out dir,
   treat it as "seed 3", and merge the three seeds' rows in analysis from the two logs.
2. Merge `run1b_stdout.log` (seeds 1–2) + the new seed-3 log, parse all rows, run the pooled paired
   estimator over n=120/arm.

**Cleaner if cost allows — just re-run all 3 seeds fresh** (BENCH_SEEDS=3) so one `retry.json` holds
the whole run with the validity gates computed in-process. ~360 solves ≈ US$3.5; key has headroom
(limit raised to $10, ~$4.7 left after run 1a+1b partials).

Decide with Bruno which. Either way: **do not report a 2-seed result as final** — pre-registered n is
3 seeds, and stopping at 2 because a partial looked fine is the optional-stopping error this project
was bitten by twice (runs 3 and 6).

## Launch recipe (WSL, the working env)
```
BENCH_TIMEOUT=480 BENCH_SEEDS=3 BENCH_OUT=".../bench/retry_lift/results" \
  uv run --extra dev python bench/retry_lift/run_retry.py
```
Env: `UV_PROJECT_ENVIRONMENT=/tmp/ciwsl-venv`, run via `MSYS_NO_PATHCONV=1 wsl -e bash <script>`,
`export PATH="$HOME/.local/bin:$PATH"`. Model = mistral-small-3.2-24b (the bench default).

## Budget
OpenRouter key `chimera-learning-lift-5usd`, limit raised to $10. Spent so far this line of work
≈ $5.3 (probe + run 1a partial + run 1b partial). Check before a fresh 360:
`curl -s -H "Authorization: Bearer $K" https://openrouter.ai/api/v1/auth/key`.
