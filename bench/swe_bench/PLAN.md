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
| `datasets>=2.19` | Already a dependency. `swebench` is **not** — it is the one package to add. |
| Repo checkout at `base_commit` | **Provided by the harness's prebuilt instance images** (§3.3) — this was expected to be the biggest build item and is not. |

**The whole scoring half is done, and the workspace half turns out to be donated by the harness.** What
is missing is the glue that produces the predictions.

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

### 3.3 The per-instance runner — the actual build

`build_solve_command` produces the argv for *one* instance. Everything around it is missing — but a
source-level audit of `swebench` 4.1.0 found that **the expensive piece is already solved by the
harness itself**, which changes the build materially.

**The workspace problem is solved by reusing the official instance images.** The naive approach —
`git clone` + `git checkout base_commit` per instance per arm — is both slow (re-cloning large repos
100+ times) and **subtly unsafe**: the harness's own setup script does more than a checkout, and for a
reason. From `make_repo_script_list_py` (`test_spec/python.py`):

```bash
git clone -o origin --single-branch https://github.com/{repo} {dir}
cd {dir} && git reset --hard {base_commit}
git remote remove origin                          # the agent cannot see future commits
# delete tags newer than base_commit's timestamp
git reflog expire --expire=now --all && git gc --prune=now --aggressive
# assert zero commits reachable after base_commit, else exit 1
```

Skip any of that and the agent can reach the actual fix through the reflog or a tag, and the whole run
is garbage. Rather than re-implement this correctly, **use the prebuilt instance image**
(`swebench/sweb.eval.x86_64.{instance_id}`, `__` → `_1776_` in the tag): the repo is already at
`base_commit` in `/testbed`, history already sanitized, conda environment already installed. Chimera
runs *inside* that container. This deletes the hardest item from the build and removes a whole class of
contamination bug.

So the remaining build is:

1. **Fetch** — `SWE-bench/SWE-bench_Verified` → the JSONL shape `load_instances` expects, printing the
   difficulty × repo distribution that §3.1 needs to freeze a slice. *(small)*
2. **Run both arms inside the instance container** — baseline `--no-plan --no-manager --max-attempts 1`,
   treatment `_DEFAULT_FLAGS --max-attempts 3`; both with `--no-remember --no-collect
   --no-evolve-skills` for Q1 hygiene. Needs the chimera wheel installed into the container — the
   `terminal_bench` adapter already does exactly this and is the template. *(medium)*
3. **Extract the patch** — `git diff` in `/testbed` after each solve → `predictions.jsonl`. *(small,
   now that the schema is confirmed — see §3.4)*
4. **Grade** — the official harness over each arm's predictions → report JSON → the existing
   `chimera swe-bench-compare`. *(infra, not code)*

### 3.4 The harness: confirmed invocation, and two documented bugs to route around

Verified against the source of `swebench` 4.1.0, **not** the docs — which are wrong in two places that
would each have cost a debugging session.

**Install.** `pip install swebench` (4.1.0). Requires **Python ≥ 3.10** — the README badge saying 3.8+
is stale. Our project is `>=3.11`, so no conflict. Run it **from WSL2**: the classifier says "OS
Independent" but evaluation is Linux containers throughout, and the official Windows guide is Docker
Desktop + WSL 2. **`x86_64` only** — `arch` is hardcoded in `make_test_spec` with no autodetection and
no CLI flag; arm64 is labelled experimental.

**Invoke.**

```bash
python -m swebench.harness.run_evaluation \
    --dataset_name SWE-bench/SWE-bench_Verified \
    --predictions_path preds.jsonl \
    --run_id <arm>_<date> \
    --max_workers 8 \
    --cache_level env
```

`--predictions_path` and `--run_id` are the only required arguments. `--namespace` defaults to
`swebench`, which is what makes it **pull prebuilt images** rather than build locally — leave it alone
(passing `--namespace none` switches to a local 3-layer build, and combining a namespace with
`--force_rebuild` raises). `sb-cli` does **not** replace this; it is for cloud evaluation of *private*
splits only. The `--parallelism` flag shown in the evaluation guide **does not exist**; it is
`--max_workers`.

**Bug 1 — `--report_dir` does not work.** It is parsed and the directory is created, but it is never
passed to `make_run_report`. The report always lands in the **current working directory**, named
`{model_name_or_path with "/" → "__"}.{run_id}.json`. Workaround: `cd` into the output directory before
invoking, or glob `*.{run_id}.json`.

**Bug 2 (ours, latent) — the report shape.** The aggregate report emits **`resolved_ids` /
`unresolved_ids` lists**, never a per-instance map. Our `parse_report` accepts both shapes, so it works
— but the per-instance branch is dead code for this file and must not be relied on. (A per-instance map
*does* exist, at `logs/run_evaluation/{run_id}/{model}/{instance_id}/report.json`, one file each.)

**Predictions schema** (constants in `swebench/harness/constants/__init__.py`) — `.json` or `.jsonl`
only:

```json
{"instance_id": "sympy__sympy-20590", "model_name_or_path": "chimera-baseline", "model_patch": "diff --git a/...\n"}
```

Only `instance_id` is validated, but `model_name_or_path` is *de facto* required — the report filename
and log paths are derived from it (`KeyError` without it). An empty `model_patch` is not an error; the
instance lands in `empty_patch_ids`, which is **exactly the bucket §3.6's infra-failure accounting
needs to read.**

**Free dry-run before spending anything:** `--predictions_path gold` runs the reference patches. If gold
does not come back ~100% resolved, the setup is broken and no model call should be made yet.

**Disk:** official figures are for the full 500 — `cache_level=env` (default) ≈ 100 GB, `base`/`none`
≈ 120 GB, `instance` ≈ **2 TB** (never use it on a subset without checking disk first). For ~50
instances there is no official number; a reasonable estimate is 30–60 GB with `env` + prebuilt pulls.
Against 559 GB free this is comfortable, but the estimate is unverified and the run should watch disk.

### 3.5 Two submission rules that constrain the design

The [official submission checklist](https://github.com/SWE-bench/experiments/blob/main/checklist.md)
states requirements that our design must satisfy or explicitly disclaim:

- **No `PASS_TO_PASS`, no `FAIL_TO_PASS`, and no `hints_text`.** The first two are §3.2's leakage ban.
  `hints_text` is new here and worth pinning down: the dataset ships it, and our `_INSTRUCTION` uses
  only `problem_statement` — so we are already clean, and that must stay a deliberate constraint rather
  than an accident. (The harness enforces the test half mechanically: at eval time it `git checkout`s
  the base version of every file the `test_patch` touches and deletes new ones *before* applying the
  patch, so an agent that edits a graded test file has those edits discarded. Reassuring, given this
  project has already caught an agent rewriting a verification test.)
- **pass@1** — "does not attempt the same task instance more than once", and any selection among
  attempts must not use SWE-bench evaluation or its tests. Chimera's `--max-attempts 3` is an *internal*
  verify-or-revert loop that emits **one** patch, judged without any SWE-bench artifact, so it is
  pass@1 in the sense that matters. But this is exactly the kind of claim that should be stated in
  RESULTS rather than assumed — and if we ever run the pipeline N times and pick a winner, that is
  `Best@k` and must be labelled so.

### 3.6 The prereg's own reporting rules need plumbing

Two of the five stopping/reporting rules currently have nothing that satisfies them:

- **Rule 3 — infra-failure accounting.** Instances lost to Docker/network/rate-limits must be reported
  *separately* from genuine agent failures. The runner has to record *why* each instance produced no
  patch, or an outage silently becomes a capability result. **This is exactly the class of bug that
  killed learning-lift run 7a** (a swallowed timeout made latency collapse look like capability
  collapse) — that lesson transfers directly and should be built in from the start, not retrofitted.
  The harness helps: it separates `empty_patch_ids` and `error_ids` from `unresolved_ids`, so the
  report already distinguishes "produced nothing" from "produced a patch that failed". The runner still
  has to record the *reason* for each empty patch on our side.
- **Rule 4 — cost reporting.** Total tokens and USD per arm. Needs to be captured per solve.

### 3.7 Environment

Docker Desktop's daemon is currently **stopped**, and its WSL integration is **off** (`docker` is not on
PATH inside WSL). Since every Python invocation in this project already runs through WSL, and the
harness's own Windows guidance is Docker Desktop + WSL 2, **enabling WSL integration is the path of
least resistance** — otherwise the runner straddles two environments. Host must be `x86_64` (§3.4).
Disk is comfortable: 559 GB free against an estimated 30–60 GB for a 50-instance slice.

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
setup    : Docker Desktop daemon + WSL2 integration; pip install swebench (>=3.10)
gold     : run the harness with --predictions_path gold on the candidate slice.
           costs ZERO model calls and proves the whole grading path end to end.
           if gold does not come back ~100% resolved, stop — the setup is broken.
probe    : fetch dataset -> print difficulty x repo distribution -> pick slice
           cost probe on 2-3 instances (real tokens, real USD)
amend    : commit the prereg amendment (slice, --verify policy, model, Q1-only)
build    : runner (chimera inside the prebuilt instance image -> both arms ->
           git diff /testbed -> predictions.jsonl) + infra-failure and cost
           accounting from the start (rule 3/4)
grade    : cd into the output dir (--report_dir is broken), run the harness per arm,
           feed both reports to `chimera swe-bench-compare`
publish  : RESULTS.md with the table, CIs, failure accounting and cost — win, loss or null
then     : flip --remember/--collect/--evolve-skills back on = the learning-lift question,
           on an instrument that can actually discriminate
```

The **`gold` step is the cheapest risk reduction available** and should run before anything else: it
exercises Docker, the image pulls, the predictions format, the report location and our parser, for the
price of zero model calls. Given that the docs were wrong about the report location and about a flag
that does not exist, validating the pipeline against a known-good input first is not optional.

**Nothing above has been executed.** Per the pre-registration's own rule, if the run is abandoned for
cost or a broken harness, that fact gets recorded here rather than the files quietly disappearing.
