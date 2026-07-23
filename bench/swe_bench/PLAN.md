# SWE-bench Verified — execution scoping

Companion to [`PREREGISTRATION.md`](PREREGISTRATION.md) (registered 2026-07-20, **not yet run**). That
file fixes the *design*; this one answers *what it costs to actually execute it, what is already built,
and which decisions must be made — and pre-registered as amendments — before the first model call.*

Written 2026-07-23, immediately after [`bench/learning_lift`](../learning_lift/RESULTS.md) run 7
retracted run 6 and concluded that **no synthetic suite we author can measure the learning question**
(three attempts at a 40–60% control band all landed at 84–92%). SWE-bench is where that series goes
next. This document exists so we do not walk into the mirror-image failure.

---

## 1. The risk that decides whether this is worth running: the FLOOR

The learning-lift series died of a **ceiling** — the control passed ~90% of tasks, so there was nothing
for learning to add. SWE-bench's failure mode is the exact opposite and **we already have a local
precedent for it**.

[`bench/terminal_bench/RESULTS.md`](../terminal_bench/RESULTS.md) ran the same paired design on a real
external benchmark with a *competent* model (`deepseek-chat-v3.1`) and got:

| | pass rate | |
|---|---|---|
| baseline (bare, 1 attempt) | **7.5%** (3/40) | |
| chimera (scaffolded) | **2.5%** (1/40) | |
| paired Δ | **−5.0%** | 95% CI [−5.0%, +1.6%] |

**37 of 40 pairs had both arms failing.** McNemar only counts *discordant* pairs, so 37 of the 40 trials
contributed literally nothing. The benchmark was too hard for the model and the measurement collapsed —
the same way it collapses when a suite is too easy.

SWE-bench Verified is harder than that Terminal-Bench slice. Frontier models with task-specific
scaffolds sit around the tens of percent; a weak model alone plausibly lands in the **low single
digits**. On 50 instances that is 1–3 resolves per arm and **near-zero discordant pairs**. The
pre-registration predicted this honestly ("probably NOT significant… both arms fail most of the time
(floor)"), but predicting a dead measurement does not make it a live one.

> **The honest framing: moving to SWE-bench does not automatically fix our measurement problem. It
> risks trading a ceiling for a floor.** Everything in §3 exists to buy the floor back.

## 2. What already exists (more than expected)

| Piece | State |
|---|---|
| `chimera/eval/swe_bench.py` | **Built + 11 unit tests.** `SWEInstance`, JSONL loader, `build_solve_command`, `parse_report` (accepts both report shapes), `report_to_trials` (fixes the id set), `compare_arms`. |
| `chimera/eval/bench_ab.py` | **Built.** The paired McNemar + Wilson estimator behind every published number. |
| `chimera swe-bench-compare` | **Built.** CLI: two reports + the instance JSONL → Δ with CI. |
| All `solve` flags the prereg names | **All present**, including `--collect/--no-collect` (declared as a paired flag), `--no-remember`, `--no-evolve-skills`, `--repo-map`, `--progress-ledger`, `--replan`, `--checklist`, `--verify`, `--max-attempts`. |
| `PREREGISTRATION.md` | **Committed 2026-07-20**, Q1/Q2 kept apart, stopping + reporting rules fixed. |
| Docker | CLI 29.3.0 installed, **daemon not running**; 559 GB free on C:. |
| `datasets>=2.19` | Already a dependency. |

**The whole scoring half is done.** What is missing is the half that *produces* the reports.

## 3. What is missing — and the design decisions inside it

### 3.1 Dataset selection — the decision that determines whether §1 kills us

The pre-registration names **Verified-Mini**. Having now inspected both datasets, the mini is the wrong
instrument for this project, for a reason visible in the schema:

| | rows | columns | has `difficulty`? |
|---|---:|---:|---|
| [`MariusHobbhahn/swe-bench-verified-mini`](https://hf.co/datasets/MariusHobbhahn/swe-bench-verified-mini) | 50 | 12 | **no** |
| [`SWE-bench/SWE-bench_Verified`](https://hf.co/datasets/SWE-bench/SWE-bench_Verified) | 500 | 13 | **yes** |

The mini is curated to preserve the *difficulty distribution* of the full set — which is precisely what
we do **not** want, because that distribution is what puts a weak model on the floor. The full Verified
carries the human-validated `difficulty` annotation, so we can build a slice deliberately:

**Proposed slice (an amendment to pre-register before running):** from the full 500, take the
**easiest difficulty stratum**, and within it **group by repository** so the same codebase recurs.
That buys two things at once:

- **Off the floor** — the easy stratum is where a weak model has a nonzero base rate, which is the only
  regime where a paired A/B has discordant pairs to count.
- **Transfer becomes possible** — repo-grouping is the SWE-bench analogue of the recurring families
  from learning-lift run 6/7, except the repetition is *real* (django's idioms actually recur) rather
  than authored by us. **This is the whole reason the learning question can live here.** The mini's 50
  instances spread across many repos would reproduce the transfer-poverty of runs 1–5.

**The honesty constraint that comes with it:** a difficulty-stratified, repo-grouped slice is **not**
SWE-bench Verified and must never be reported as a Verified score. The pre-registration's Q1/Q2 split
already handles this — Q1 (the paired thesis test) runs on the slice, Q2 (the absolute scoreboard
number) is the only thing entitled to the name, and it needs the full 500 and a strong model. **Q2 is a
separate, much more expensive run and should not be bundled into this one.**

*Open question for the fetch script to answer, not to guess:* the exact `difficulty` value labels and
their counts, and the per-repo instance counts within the easy stratum. The script must print both
before any slice is frozen.

### 3.2 `--verify` and test leakage — a correctness bug waiting to happen

The pre-registration says the treatment arm passes `instance.test_cmd` as `--verify`, giving the agent
executable ground truth. **Neither dataset has a `test_cmd` column.** The tests that grade an instance
live in `test_patch`, which by construction does **not** exist in the repo at `base_commit`.

So there is an obvious-looking "fix" that must be refused: synthesizing a `test_cmd` from
`FAIL_TO_PASS`. That would hand the agent the exact hidden tests it is graded on — **test leakage**, and
the resulting number would be worthless. `SWEInstance.test_cmd` already defaults to `""` and
`build_solve_command` already omits `--verify` when it is empty, so **the honest behaviour is the
current default**; the risk is someone "helpfully" filling the field in later.

That leaves a real trade-off to decide explicitly:

| Option | Verify signal | Leakage | Note |
|---|---|---|---|
| **(a) No `--verify`** | none | none | Honest, and the current default. But the treatment arm loses verify-or-revert — one of the strongest weak-model lifters — so it tests a *weaker* Chimera than the local_lift result did. |
| **(b) `--verify` on the repo's *existing* tests at `base_commit`** | regression-only | none | Legitimate: a real developer runs the existing suite. Catches breakage (the `PASS_TO_PASS` spirit) without ever seeing `FAIL_TO_PASS`. Costs a per-repo test command. |
| **(c) `--verify` on `FAIL_TO_PASS`** | full | **yes** | **Never.** Must be explicitly forbidden in the amendment so it cannot be reintroduced as a convenience. |

**Recommendation: (b)**, falling back to (a) where a repo's suite is too slow. Either way the choice
goes in the amendment *before* the run, because it changes what is being measured.

### 3.3 The per-instance runner — the actual build (nothing exists)

`build_solve_command` produces the argv for *one* instance. Everything around it is missing:

1. **Fetch** — `MariusHobbhahn/...` or `SWE-bench/SWE-bench_Verified` → the JSONL shape `load_instances`
   expects, printing the difficulty/repo distribution for §3.1. *(small)*
2. **Per-instance workspace** — `git clone` the repo, `git checkout base_commit`, fresh per instance
   **per arm** (the prereg requires both arms start from the same base commit in a fresh checkout).
   Needs a local git cache or this re-clones large repos 100+ times. *(medium)*
3. **Run both arms** — baseline `--no-plan --no-manager --max-attempts 1`, treatment `_DEFAULT_FLAGS
   --max-attempts 3`; both with `--no-remember --no-collect --no-evolve-skills` for Q1 hygiene. *(small)*
4. **Extract the patch** — `git diff` the workspace after each solve → `predictions.jsonl` in the
   harness's schema. **Nothing in the repo does patch extraction today.** *(medium — the format must
   match the official harness exactly)*
5. **Grade** — run the official SWE-bench harness in Docker over each arm's predictions → report JSON →
   feed to the existing `chimera swe-bench-compare`. *(infra, not code)*

Pieces 2 and 4 are the real work; 1, 3 and 5 are glue.

### 3.4 The prereg's own reporting rules need plumbing

Two of the five stopping/reporting rules currently have nothing that satisfies them:

- **Rule 3 — infra-failure accounting.** Instances lost to Docker/network/rate-limits must be reported
  *separately* from genuine agent failures. The runner has to record *why* each instance produced no
  patch, or an outage silently becomes a capability result. **This is exactly the class of bug that
  killed learning-lift run 7a** (a swallowed timeout made latency collapse look like capability
  collapse) — that lesson transfers directly and should be built in from the start, not retrofitted.
- **Rule 4 — cost reporting.** Total tokens and USD per arm. Needs to be captured per solve.

### 3.5 Environment

The harness is Docker/Linux. Docker Desktop's daemon is currently stopped and its WSL integration is
off (`docker` is not on PATH inside WSL). Since every Python invocation in this project already runs
through WSL, **enabling Docker Desktop's WSL integration is the path of least resistance** — otherwise
the runner straddles two environments. Disk is not a constraint (559 GB free vs ~5 GB for a 50-instance
subset per the mini's dataset card; the full 500 is ~130 GB).

## 4. Cost

Unlike the learning-lift runs (tiny synthetic files), SWE-bench instances carry real repositories: large
contexts, multi-file navigation, `--max-attempts 3` on the treatment arm. **A per-instance cost probe on
2–3 instances must precede any committed run** — extrapolating from synthetic-suite costs would be
meaningless. The prereg already requires reporting cost per arm; measuring it first also protects
against discovering the price after spending it.

## 5. Decisions needed before anything is built

1. **Slice**: full Verified filtered to the easy stratum + grouped by repo (recommended, §3.1), or the
   mini as originally registered? The former is the only version where the *learning* question can be
   asked afterwards.
2. **`--verify` policy**: (a), (b), or (c)-forbidden-forever (§3.2). Recommendation: (b) with (a) as
   fallback, (c) explicitly banned in the amendment.
3. **Model**: the goldilocks pick. `mistral-small-3.2-24b` (the learning-lift model) is almost certainly
   floor-bound here; `deepseek-chat-v3.1` already produced a 37/40-both-fail floor on Terminal-Bench.
   This needs a deliberate choice and probably a cheap probe, not an inherited default.
4. **Scope of this run**: Q1 (paired thesis, slice) only — with Q2 (the absolute Verified number,
   full 500, strong model) explicitly deferred as a separate funded run.

All four are design choices that change what is measured, so each goes into a pre-registration amendment
**committed before the first model call** — the same discipline that let the learning-lift series retract
its own headline result without embarrassment.

## 6. Sequencing

```
probe    : fetch dataset -> print difficulty x repo distribution -> pick slice
           cost probe on 2-3 instances (real tokens, real USD)
amend    : commit the prereg amendment (slice, --verify policy, model, Q1-only)
build    : runner (clone/checkout cache -> both arms -> git diff -> predictions.jsonl)
           + infra-failure and cost accounting from the start (rule 3/4)
grade    : official harness in Docker -> report JSON -> `chimera swe-bench-compare`
publish  : RESULTS.md with the table, CIs, failure accounting and cost — win, loss or null
then     : flip --remember/--collect/--evolve-skills back on = the learning-lift question,
           on an instrument that can actually discriminate
```

**Nothing above has been executed.** Per the pre-registration's own rule, if the run is abandoned for
cost or a broken harness, that fact gets recorded here rather than the files quietly disappearing.
