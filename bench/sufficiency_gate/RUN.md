# Running the sufficiency-gate bench

Read `PREREGISTRATION.md` first; it was committed before any gate call.

## What is new and what is reused

- **New calls:** 400 gate reads + 100 floor re-reads, all local (`qwen3:4b` through Ollama, the
  product's `LocalLogprobBackend`). US$ 0. Nothing networked beyond `localhost:11434`.
- **Reused, not re-called:** every generation, post-generation read and label, from
  `bench/verified_cascade/results/run/` (2026-09-27). The report halts unless its replay reproduces
  the published A = 33/400 and D = 21/398 wrong.

## Full run (queue it serially with any other Ollama work)

From the repository root, with Ollama serving `qwen3:4b`:

```bash
PYTHONUTF8=1 uv run --extra dev python bench/sufficiency_gate/run.py --collect --out bench/sufficiency_gate/results/run
PYTHONUTF8=1 uv run --extra dev python bench/sufficiency_gate/run.py --report  --out bench/sufficiency_gate/results/run
```

- **Duration:** 500 local calls. The smoke measured **2.27 s a call while the GPU was shared** with a
  cross-encoder run (≈ 19 min for the full run); alone, the verifier's local reads ran at p50 0.75 s
  (`verified_cascade` report), so ≈ 7 min. Budget 25 min.
- **Resumable:** every call is appended to `results/run/calls.jsonl` before the next; re-running
  `--collect` skips what is done. Never delete or move that directory while it runs.
- **Output:** `results/run/report.txt` and `report.json` — arms, the §5 verdict for C and C+D, false
  abstention, the random-gate and all-decline controls, AUROC, floor and the diagnostic sweep. The
  report prints `verdict withheld` if more than 5% of reads halt or the run is partial.

## Smoke (done, 2026-10-07)

`--collect --limit 17 --floor 3 --out bench/sufficiency_gate/results/smoke`: 20 calls, 0 halts, label
mass ≥ 0.998, replay control reproduced (A 33/400, D 21/398), 0/3 choice flips on the floor
(max |Δp| 0.047). Its verdict lines are wiring on 17 items, marked `PARTIAL`, and are not a result.
