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

---

# Amendment 1 — the four deferred decisions, fixed before any model call

**Committed before the first `chimera solve` of this run.** The gold dry-run (3 django instances,
reference patches, 3/3 resolved, report parsed correctly) validated the Docker + harness + report +
parser pipeline at zero model cost. These four choices were left open in `PLAN.md §5`; they are fixed now.

## Decision 1 — Slice: django/django, `<15 min fix` stratum

From the full Verified 500, the slice is **django/django instances in the easiest difficulty stratum
(`<15 min fix`)** — 92 instances, one codebase. This is the plan's §3.1 recommendation made concrete:
- **Off the floor:** the easy stratum is where even a mid model has a nonzero base rate (the floor that
  killed terminal_bench's measurement is a hard-difficulty artefact).
- **Transfer real, not staged:** one repository means django's own idioms recur across instances — the
  transfer-possible setting the learning-lift series could only fake.
- **Modern prebuilt images:** django versions 3.0–5.0 all have prebuilt eval images (gold run confirmed
  v5.0 pulls work).

The **exact instance-id set is frozen by the probe** (below) and written to `results/slice.jsonl`
before the paired run; no instance is added or dropped after seeing a result.

## Decision 2 — Model: `openrouter/deepseek/deepseek-chat-v3.1`

Chosen over the weaker mistral-24b of the prior runs. **This is a deliberate change of what the run
measures, stated plainly:** deepseek-chat-v3.1 is *competent*, not weak, so this is less a "lift a weak
model" test (the project's central thesis) and more "does Chimera's scaffolding help a competent model
on real repos." The trade-off, decided with the repo owner: a competent model has a much lower chance of
the terminal_bench floor (37/40 both-fail) that makes a paired A/B measure nothing, and it yields a
better absolute Q2 number. The honest cost is thesis purity — if deepseek already resolves an instance
alone, there is less headroom for the scaffold to add, which will show up as a smaller Δ. Reported as-is.

## Decision 3 — `--verify` policy: option (a), no verify (honest default)

The treatment arm runs **without `--verify`**. Neither dataset ships a `test_cmd`, and synthesizing one
from `FAIL_TO_PASS` would hand the agent its own hidden grading tests (leakage). Option (b) — verify on
the repo's *existing* tests at base_commit — is legitimate but needs a per-version test command and adds
run time; deferred. Option (c) — verify on `FAIL_TO_PASS` — is **forbidden, permanently**. So the
treatment arm's scaffolding is `--repo-map --progress-ledger --replan --checklist --max-attempts 3`
without executable ground truth, which tests a *weaker* Chimera than local_lift did — noted, not hidden.

## Decision 4 — Scope: Q1 only (paired thesis), Q2 deferred

This run answers **Q1** (does Chimera's scaffolding beat baseline on the same instances) on the django
easy slice. **Q2** (absolute % of full Verified 500 with a strong model) is a separate, much more
expensive run and is **not** bundled here. The slice result is never labelled a "Verified score."

## Hygiene carried from the main design
Both arms: `--no-remember --no-collect --no-evolve-skills`, fresh sanitized checkout per instance
(single-branch clone, `reset --hard base_commit`, remote removed, future work gc'd unreachable — the
harness's own anti-leakage recipe), pass@1 (one patch per instance, no best-of-N), graded ONLY by
SWE-bench's official harness in Docker.

**Status: amendment committed. The cost probe (3 instances, both arms, real deepseek calls) runs next
to fix the slice size against observed per-instance cost, then the paired run.**
