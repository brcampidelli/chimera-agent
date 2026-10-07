# S30-61 — the local-Ollama replication

Pre-registration: `PREREGISTRATION.md` plus **Amendment 1** (100 pristine runs on `qwen3:4b`, four paired
classes) and **Amendment 2** (smoke findings). Local only, US$0. Needs Ollama with `qwen3:4b`.

## Full run

Two steps. Only the first calls a model; it is resumable (a finished run directory is skipped).

```cmd
uv run --extra dev --extra desktop python bench/wire_reconcile/run_ollama.py generate --out bench/wire_reconcile/results/ollama
uv run --extra dev --extra desktop python bench/wire_reconcile/run_ollama.py inject --runs bench/wire_reconcile/results/ollama --json bench/wire_reconcile/results/ollama.json
```

**Expected duration: about 4 hours** for `generate` (100 runs; smoke: 73–235 s per run, mean 152 s, with
qwen3 thinking on — the spread makes 3–6 h plausible). `inject` takes seconds.

`inject` prints, per class, `detected / trials` with the two-sided 95% Wilson interval, the pooled fault
rate, the protocol failures (never replaced), and every clean-copy false positive with its reconcile fields
— a real run can make gateway calls that are not steps (retry, compaction, loop-breaker ending), and those
are exactly what this replication exists to surface. The enablement rule (≥100 clean with 0 FP, ≥100 per
class with Wilson lower bound ≥ 0.90) is in the pre-registration and is not loosened here: 100/100 in a class
gives a lower bound of 96.3%, 99/100 gives 94.6%.

## Smoke (2026-10-07, not data)

`generate --limit 4 --max-calls 20`: 4 runs, 10 model calls, 10 min 8 s; each run had one wire record per
step (2, 3, 3, 2). `inject` over those 4 runs:

```
clean: 0/4 (0.0%; 95% Wilson 0.0%–49.0%) false positives
omission: 4/4 (100.0%; 95% Wilson 51.0%–100.0%) detected
fabrication: 4/4 (100.0%; 95% Wilson 51.0%–100.0%) detected
altered copy: 4/4 (100.0%; 95% Wilson 51.0%–100.0%) detected
pooled faults: 12/12 (100.0%; 95% Wilson 75.8%–100.0%) detected
protocol failures (not replaced): 0 []
```

Plumbing only; n=4 says nothing about the rates.
