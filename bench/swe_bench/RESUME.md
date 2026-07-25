# Resume note — SWE-bench run 1, PAUSED mid-treatment (2026-07-24)

Bruno paused to step away from the computer. State is fully preserved on disk; nothing is wasted.

## Where it stopped
Q1 paired A/B, 19 django-easy instances (`bench/swe_bench/results/slice.jsonl`), model
`deepseek-chat-v3.1`, deterministic, gold-validated slice.

| arm | state | file |
|---|---|---|
| **baseline** | **19/19 complete** (9 patches, 10 empty) | `results/run1/predictions_baseline.jsonl` |
| **treatment** | **9/19 done** (5 patches, 4 empty); stopped after django-11179 | `results/run1/predictions_treatment.jsonl` |

**10 treatment instances remain:** 11239, 11299, 11433, 11451, 11490, 11555, 11603, 11820, 11880, 11951
(saved as `results/slice_treatment_remaining.jsonl`). No grading has run yet.

Budget: OpenRouter key limit $20, **~$13.6 left**. Finishing = 10 treatment solves ≈ $2.5. Comfortable.

## How to finish (append, don't redo — the runner overwrites predictions_<arm>.jsonl with "w")
1. Run treatment on ONLY the remaining 10, into a temp out dir, then append to the main file:
   ```
   BENCH_ARM=treatment BENCH_SLICE=bench/swe_bench/results/slice_treatment_remaining.jsonl \
     BENCH_OUT=bench/swe_bench/results/run1_rem BENCH_MODEL=openrouter/deepseek/deepseek-chat-v3.1 \
     BENCH_TIMEOUT=900 uv run --extra dev python bench/swe_bench/run_swe.py
   cat bench/swe_bench/results/run1_rem/predictions_treatment.jsonl \
     >> bench/swe_bench/results/run1/predictions_treatment.jsonl   # now 19 lines
   ```
2. Verify predictions_treatment.jsonl has 19 unique instance_ids.
3. Grade BOTH arms with the official harness (images already pulled from gold-20 + probe):
   ```
   cd bench/swe_bench/results/run1
   for arm in baseline treatment; do
     /tmp/swebench-venv/bin/python -m swebench.harness.run_evaluation \
       --dataset_name SWE-bench/SWE-bench_Verified --predictions_path predictions_${arm}.jsonl \
       --run_id run1_${arm} --max_workers 6 --cache_level env
   done   # reports land in run1/ as chimera-<arm>.run1_<arm>.json (--report_dir is broken; cd first)
   ```
4. Paired A/B: feed both reports' resolved_ids to the pooled paired estimator over the 19 shared ids:
   `chimera swe-bench-compare` OR `chimera/eval/swe_bench.py` compare_arms + `chimera/eval/paired.py`.
5. Write RESULTS.md (the public number, with CI, the empty/infra accounting, and cost per arm).

## Environment (must be up before resuming)
- **Docker Desktop engine running** and reachable from WSL (`wsl -e bash -lc 'docker info'`). If the
  machine slept, Docker Desktop may need reopening from the tray until the engine is green.
- venvs on /tmp (survive normal reboots but NOT `wsl --shutdown`): `/tmp/ciwsl-venv` (chimera),
  `/tmp/swebench-venv` (harness), `/tmp/swe-django-ref` (django reference clone). If any is gone,
  rebuild: harness = `uv venv /tmp/swebench-venv --python 3.11 && uv pip install --python
  /tmp/swebench-venv/bin/python swebench`; the django ref re-clones itself on first run_swe.py call.
- Run everything via `MSYS_NO_PATHCONV=1 wsl -e bash <script>`, `export PATH="$HOME/.local/bin:$PATH"`.

## What the partial already hints (NOT a result — no grading yet)
Patch RATES so far: baseline 9/19 (47%), treatment 5/9 (56%). This reverses the n=3 probe (where
treatment lagged) — small-sample variance dissolving. But patch≠resolve; the grade decides. The probe
also showed deepseek resolves easy django well alone (2/3), so expect a CEILING-limited paired result
(little headroom for the scaffold) — the pre-registered honest reading if Δ is small/ns.

## Pre-registration status
`PREREGISTRATION.md` + Amendment 1 (slice, model, verify-policy, Q1-only) committed. django-10097
dropped from the slice (gold-grading failure) and committed. This run is Q1 only; Q2 (absolute % of
full Verified with a strong model) remains deferred.
