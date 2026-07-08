# Results — hierarchy paired A/B

> Predictions were registered in README.md before any run. Whatever happens —
> win, loss, or mush — goes here as measured.

## Run 1 — 2026-07-08, deepseek-chat-v3.1 (same model both arms), n=10

Command: `BENCH_MODEL=openrouter/deepseek/deepseek-chat-v3.1 python bench/hierarchy/run_paired.py`
(raw output in `results/paired.json`)

| metric | single-agent | hierarchy |
|---|---|---|
| pass rate | 70% | 80% |
| paired Δ | — | **+10.0%**, 95% CI **[−5.9%, +10.0%]** — **not significant** |
| discordant pairs | 0 | 1 (hierarchy won the only one) |
| total tokens | 15,788 | 23,195 |
| median tokens | 1,604 | 2,338 |
| token "reduction" | — | **−46.9%** (i.e. the hierarchy cost MORE) |

### Predictions — held?

| # | prediction | verdict |
|---|---|---|
| 1 | token reduction ≥ 30% | **FAILED** — the hierarchy cost 47% MORE tokens here |
| 2 | non-inferior quality (no significant loss) | **HELD** — quality went up +10pp (not significant, but no loss) |
| 3 | negative control: classifier falls back on all local_lift coding tasks | **HELD** — asserted in `tests/test_hierarchy_ab.py::test_negative_control_local_lift_all_fall_back` |

### Honest reading — why prediction 1 failed (and what it actually teaches)

This suite is **single-shot** reading over **small** documents (each ~1-2k
tokens). In that regime the single-agent baseline is already token-optimal: one
call carries all documents once. The hierarchy, forced on, pays a per-worker
system-prompt overhead **plus** a synthesis call, with nothing to amortize it
against — so it can only cost more. The literature's token win comes from a
different regime this bench does not exercise:

- **Multi-STEP agentic loops**, where the full context is re-sent every ReAct
  step — there, scoping each worker to its slice compounds across steps.
- **Large per-part context**, where a worker seeing only its document saves a
  lot versus an orchestrator carrying every document through its reasoning.

Single-shot small-doc Q&A has neither, so the fan-out overhead dominates. That
is a real, honest limit of the *mechanism forced on*, and it is exactly why the
`chimera orchestrate` **command** does not force it: its profitability gate and
deterministic classifier would route these tasks to the single-agent path (the
bench bypasses both, on purpose, to measure the raw mechanism). **The token
economy is a claim about guarded, multi-step orchestration — not about
single-shot reading — and this run keeps us honest about that.**

What the run *does* show positively: quality is non-inferior (a small,
non-significant lift) under aggressive per-document context scoping — the
minimal-context handoff did not lose information. The cost result is reported
as-is; no re-roll, no suite swap to manufacture a win.

### Follow-up worth running (not done here, to avoid moving the goalposts mid-run)

A companion suite with (a) large per-part documents and (b) a multi-step
agentic variant would locate the crossover where scoping's token win actually
appears. That is a *new* registered experiment, not a re-roll of this one.
