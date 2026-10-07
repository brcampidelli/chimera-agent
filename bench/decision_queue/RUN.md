# How to run `bench/decision_queue`

Registered in `PREREGISTRATION.md` (and its Amendment 1). Local only, US$ 0.

## Before

* Ollama running at `127.0.0.1:11434` with `qwen3:4b` pulled (`ollama pull qwen3:4b`).
* **Nothing else on the GPU.** The runner reads `nvidia-smi` before each sweep and refuses to start a
  sweep on a GPU with another compute process or utilisation >= 10% (control 1(b)). Run it when no
  other bench, training job or agent is using the GPU or the same Ollama server — another client of
  the same server is a hidden extra level of concurrency.
* `python bench/decision_queue/run.py --check` passes (no calls).
* Between sweeps the runner waits up to 5 min for the GPU to drain its own load (Amendment 2);
  add that to the duration in the worst case.

## The command

From the repository root (Windows PowerShell or WSL, whichever has Ollama on `127.0.0.1`):

```bash
uv run --extra dev python bench/decision_queue/run.py --run \
  --out bench/decision_queue/results/2026-10-07-run.jsonl
uv run --extra dev python bench/decision_queue/run.py --report \
  bench/decision_queue/results/2026-10-07-run.jsonl
```

(Use the date of the run in the file name.) 880 measured calls + 6 warm-up, written one per line as
they come, so a run that dies keeps what it measured — but the report only reads a complete one.

## Expected duration

**8–15 minutes** on an idle RTX 5070 Laptop GPU. If Ollama serves one decision at a time, the wall
time is the sum of the service times whatever c is: 886 calls at the quoted 0.3–0.75 s per call is
4.5–11 min, plus model load. If the first cell (sweep A, c = 1, 110 calls) has not finished in
**5 minutes**, the GPU is not idle or the server is throttling — stop it (Ctrl+C), find what is
loading the GPU, and start over; the worst case under contention is 30 s per call (the backend's
timeout), which is hours.

## After

* The runner unloads `qwen3:4b` (`keep_alive: 0`) **only if it was not loaded when the run
  started**, so it never pulls the model out from under another job. Check `curl
  127.0.0.1:11434/api/ps` and `nvidia-smi` afterwards.
* Commit the `.jsonl` under `bench/decision_queue/results/` and write `RESULTS.md` from `--report`,
  control first: an `UNREADABLE` decision is published as that, not re-run until it reads.
