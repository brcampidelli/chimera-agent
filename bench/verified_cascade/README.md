# bench/verified_cascade

Does a **decision gate** (a Choice `supported / unsupported / declined`, read by `typesafe/jev-1.13` through
OpenRouter's Decisions API, or by the local `qwen3:4b` backend Chimera ships) make a cheap model's
**grounded** answers safer to send than the cheap model alone, at a price and a hand-off rate worth paying?
It is phase 2 of `chimera/fusion/cascade.py` (whose lexical gate `bench/cascade` found passing a
confident wrong answer), and a test of OpenRouter's "Jev-verified cascade" cookbook recipe at a size that
can answer it.

**Status (2026-09-26): pre-registered, nothing run, no money spent.** Read `PREREGISTRATION.md` first.

| file | what |
|---|---|
| `PREREGISTRATION.md` | the design, the frozen adoption rule, the gates, the budget, the predictions |
| `build_items.py` | fixes the excerpt pool and the item skeleton from the repo's own EN/PT docs; US$ 0, stdlib, offline (`--check` rebuilds and compares) |
| `power.py` | the exact-McNemar power numbers §6 quotes |
| `results/excerpts.jsonl` | 365 chunks (181 EN, 184 PT) |
| `results/skeleton.jsonl` | 144 gold slots, each with its ANS and NCR excerpt sets |
| `results/sources.json` | sha256 of every source file, and of the English version each PT file translates |

**Next, before any paid call:** the questions, reference answers, key facts and NCP questions are written
against the skeleton and frozen as Amendment 0 (`PREREGISTRATION.md` §3.3). Then S0 (US$ 0) and S1.
