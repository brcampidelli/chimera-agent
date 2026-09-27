# bench/verified_cascade

Does a **decision gate** (a Choice `supported / unsupported / declined`, read by `typesafe/jev-1.13` through
OpenRouter's Decisions API, or by the local `qwen3:4b` backend Chimera ships) make a cheap model's
**grounded** answers safer to send than the cheap model alone, at a price and a hand-off rate worth paying?
It is phase 2 of `chimera/fusion/cascade.py` (whose lexical gate `bench/cascade` found passing a
confident wrong answer), and a test of OpenRouter's "Jev-verified cascade" cookbook recipe at a size that
can answer it.

**Status (2026-09-27): pre-registered and frozen (Amendment 0), harness dry-run only, no model called,
no money spent.** Read `PREREGISTRATION.md` first; Amendment 0 at its end records the owner's decisions
(cap US$ 20, admission stop US$ 18, full coverage of L and C) and the item manifest.

| file | what |
|---|---|
| `PREREGISTRATION.md` | the design, the frozen adoption rule, the gates, the budget, the predictions, Amendment 0 |
| `build_items.py` | fixes the excerpt pool and the skeleton (`--check`); `freeze` builds the items, the V slice and the manifest (`freeze --check`) |
| `freeze.py`, `common.py` | the freeze, and the helpers every script shares (normalisation, the number check, retrieval) |
| `check_items.py` | the validator: counts, key facts, NCR/NCP leakage, number-check self-test, duplicates, sources, manifest |
| `harness.py` | the pinned instruments (drafting prompt, decision, grader rubric), the call log, the spend ledger, the policies |
| `backends.py` | the live backends (gateway, Decisions API, local Ollama) and the fake one `--dry-run` uses |
| `run.py` | the stages S0–S6 with their gates, resumable, spend-capped |
| `report.py`, `stats.py` | the registered metrics (exact McNemar, Holm, Newcombe, clustered bootstrap, verifier AUROC/ECE) |
| `power.py` | the exact-McNemar power numbers §6 quotes |
| `results/excerpts.jsonl`, `skeleton.jsonl`, `sources.json` | the pool (365 chunks), 144 gold slots, source hashes |
| `results/questions_ans.jsonl`, `questions_ncp.jsonl` | the authored questions (144 ANS, 112 NCP) |
| `results/items.jsonl`, `verifier_slice.jsonl`, `freeze_report.txt`, `manifest.json` | the frozen 400 items, 1,230 V triples, and their hashes |

## Run plan

The paid run is **not** run yet. It will be driven from the **Chimera desktop app through its MCP server**
(`chimera mcp desktop`, shipped in 0.62.2/0.62.3), one stage at a time, reading each stage's gate before
the next. The same harness runs from a terminal:

```bash
python bench/verified_cascade/check_items.py                      # must print OK
python bench/verified_cascade/run.py --dry-run --out /tmp/vc-dry  # the whole pipeline on a fake backend, no network
python bench/verified_cascade/report.py /tmp/vc-dry

# the paid run, stage by stage (OPENROUTER_API_KEY in the environment, Ollama serving qwen3:4b)
python bench/verified_cascade/run.py --stage s0 --out <dir>
python bench/verified_cascade/run.py --stage s1 --out <dir>       # ... s2, s3, s4, s5, s6
python bench/verified_cascade/report.py <dir>
```

Every call is appended to `<dir>/calls.jsonl` before the next one is made; a stage that stops (a gate, the
admission stop, a crash) resumes where it stopped. `<dir>/gates.json` holds each gate's verdict, and a
stage refuses to start while an earlier gate is failed or missing.
