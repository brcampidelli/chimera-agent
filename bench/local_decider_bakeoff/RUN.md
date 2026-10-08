# How to run the local bake-off

The design is `PREREGISTRATION.md`, committed before any model answered. This file is the exact
procedure. Everything heavy lives outside the repository, under the session scratchpad
(`$SP` below):

```
SP=C:/Users/brcam/AppData/Local/Temp/claude/C--Users-brcam-Desktop-Desenvolvendo-Projetos-Agent-AI/113de395-42b3-45ce-a32b-def6bb191e16/scratchpad
$SP/bakeoff/weights/clef-flash-gguf/Cloudflare_clef-flash-Q4_K_M.gguf     6,040,541,344 B
$SP/bakeoff/weights/intern-decision-2b/                                    4,426,562,832 B of safetensors
$SP/bakeoff/weights/eikos-4b/                                              9,319,821,024 B of safetensors (HF, BF16)
$SP/bakeoff/weights/eikos-4b-gguf/Eikos-4B-Q8_0.gguf                       converted here (see below)
$SP/s1q/llama/llama-server.exe                                             llama.cpp b11457 (5ad1c5d), win-cuda-12.4-x64
$SP/intern/.venv                                                           torch 2.9.1+cu128, transformers 5.14.1
$SP/intern/vendor                                                          Intern-Decision @ 3572c8a
$SP/jevbench-clone                                                         jevbench @ 2fa63fa
```

Every file's sha256 is in `weights.sha256` (relative to `$SP/bakeoff/weights`); each arm checks its
own before its server starts. The downloads are pinned in `$SP/bakeoff/download.py`.

## `/v1/systemone` in the existing build

b11457 already carries the decision-model support of llama.cpp PR 29831: `llama-server-impl.dll`
has the `/v1/systemone` route and the decision types `laya`, `openjev`, `lev`, `kev`, `nimble`,
`clef`, `pplx-decider`, and the converter in the same tree has `conversion/clef.py`. No newer
build was downloaded. Eikos and Intern are not decision types of llama.cpp: Eikos is served as a
plain Qwen3.5 GGUF and read by letter logprobs; Intern runs on its vendor's torch code.

## Conversion (done once)

```
bash $SP/bakeoff/convert_eikos.sh        # convert_hf_to_gguf.py --outtype q8_0, CPU only, ~3 min
```

## One arm

```
bash bench/local_decider_bakeoff/run_arm.sh clef-q4     # or intern-2b, eikos-4b
bash bench/local_decider_bakeoff/run_arm.sh clef-q4 --smoke   # guards 1–3 and the raw smoke only
```

`run_arm.sh` runs `run_model.py` under `uv run` from the main checkout with `PYTHONPATH` on this
worktree. The script:

1. checks the weight hashes;
2. waits up to 15 min for the GPU to hold **no** compute process and enough free memory, else exits 3
   — an idle Ollama unloads itself after its keep-alive; nothing is ever killed;
3. starts the arm's own server(s) and waits for `/health`;
4. prints the raw smoke (3 JevBench, 3 governance items);
5. runs JevBench-231 → `results/<arm>/jevbench.jsonl`, then guard 5 (easy ≥ 0.90, else exit 6);
6. runs governance `registered` (559 requests) and `urgency4` (220) → `results/<arm>/governance-*.jsonl`;
7. stops its servers; for clef-q4 it then runs the hosted control (no GPU, ≈ US$ 0.02, key from the
   environment or the main checkout's `.env`, never printed) → `results/clef-q4/hosted-registered.jsonl`;
8. writes `results/<arm>/run-meta.json` (load time, per-instrument durations, VRAM, other GPU processes).

Every answer passes guard 4 (exact labels, finite, sums to 1 ± 0.01); one violation exits 4. A
rerun resumes: JevBench skips written ids, governance skips written rows.

### Servers each arm starts and stops

| arm | process | command | health | endpoint |
|---|---|---|---|---|
| clef-q4 | llama-server | `llama-server.exe -m …/Cloudflare_clef-flash-Q4_K_M.gguf -ngl 99 -c 8192 -np 1 --host 127.0.0.1 --port 8090` | `:8090/health` | `:8090/v1/systemone` |
| intern-2b | Intern sidecar | `$SP/intern/.venv/Scripts/python.exe bench/local_decider_bakeoff/intern_server.py --vendor $SP/intern/vendor --checkpoint …/intern-decision-2b --port 8765` | `:8765/health` | `:8765/v1/decisions` |
| eikos-4b | llama-server + sidecar | `llama-server.exe -m …/Eikos-4B-Q8_0.gguf -ngl 99 -c 8192 -np 1 --host 127.0.0.1 --port 8091`, then `python bench/local_decider_bakeoff/eikos_server.py --checkpoint …/eikos-4b --llama http://127.0.0.1:8091 --port 8766` | `:8091/health`, `:8766/health` | `:8766/v1/systemone` |

Server logs: `$SP/bakeoff/logs/<arm>/`. They are stopped by the runner on exit, also on failure
(terminate, then kill after 30 s).

### Expected durations (estimates, not measured — no smoke could run; see below)

| arm | load | JevBench (231) | governance (559 + 220) | total |
|---|---:|---:|---:|---:|
| clef-q4 | ~1 min | ~5–10 min | ~5–10 min | ~15–25 min |
| intern-2b | ~1–2 min | ~3–6 min | ~5–10 min | ~10–20 min |
| eikos-4b | ~1 min | ~3–6 min | ~8–15 min (two forwards a request) | ~15–25 min |

The ceiling is the GPU wait (15 min per arm) plus about an hour for the three arms.

## Everything, serially

```
bash bench/local_decider_bakeoff/run_all.sh
```

Runs the three arms one after another (a failing arm does not stop the next; exit codes in
`$SP/bakeoff/logs/exit-codes.txt`), then `verdict.py --governance-reports` into `report.md`: the
registered readout, the adoption rule condition by condition, and `bench/jev_decisions/report.py` on
each governance file (urgency4 joined with the registered unwrapped rows, its baseline).

## Smoke status at registration time

The GPU was held by the coordinator's Ollama queue (`llama-server.exe` of Ollama, pid 92820, ~4 GB)
for the whole preparation, so **no smoke was run**: per the pre-registration a model is measured
only with the GPU to itself. The first raw outputs will be the ones the runner prints in step 4.

## Amendment 1 — clef-q4 JevBench rerun (2026-10-07)

The first clef-q4 run served `/v1/systemone` with the default ubatch of 512. In decision mode the
whole prompt has to fit in one ubatch, so every JevBench prompt above 512 tokens (68 hard items, up
to 4,032 tokens) came back as HTTP 500. See `PREREGISTRATION.md`, Amendment 1. The rerun is:

```
bash $SP/bakeoff/rerun_clef_jevbench.sh     # -> bench/local_decider_bakeoff/rerun_clef_jevbench.sh
```

The script runs `run_model.py --arm clef-q4 --clef-jevbench-rerun` (code in `rerun_clef.py`):

1. Keeps the ub512 file as `results/clef-q4/jevbench.ub512.jsonl`.
2. Starts the server with `-b 8192 -ub 8192`, stepping down to 6144 and then 4608 only if it fails to
   load.
3. Runs the smoke; a halt in it aborts with exit 7.
4. Runs JevBench-231 in full and applies the guards (exit 6 or 7).
5. Runs reuse check A: the 120 easy/original rows must reproduce the ub512 rows.
6. Runs reuse check B: the 55 governance items must reproduce repetition 0.
7. Reruns governance in full only if check A or B fails.
8. Writes `results/clef-q4/rerun-amendment1.json` and regenerates `report.md`.

The estimated duration is about 2–5 minutes, plus about 3 minutes if governance has to be rerun. It
uses the GPU on its own.

From Amendment 1 on, every arm also aborts with exit 7 on a smoke halt or when more than 2% of JevBench
halts.
