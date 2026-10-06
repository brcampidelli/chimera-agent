# premise_capitulation

Study 31, item G31-07. This is a **measurement-only** probe: it makes no product change, executes no proposed action, and adds no generic verification nudge. Read `PREREGISTRATION.md` before running; `corpus.jsonl` is the frozen set of 20 false/true premise twins.

## Local run

Prerequisites: Ollama is running locally and has `qwen3:4b` available. The runner will not start Ollama, pull a model, use a hosted endpoint, or retry failures. It makes 120 local generations (40 items × 3 seeds) at temperature 0.2. An unavailable endpoint is reported as a failed generation per registered seed; it is not replaced or dropped.

```bash
python -m bench.premise_capitulation.run
```

The default endpoint is `http://localhost:11434/api/generate`; `--url`, `--model`, `--corpus`, and `--output` can be supplied for controlled use. Do not change corpus, prompt, settings, or scoring after inspecting model answers; register an amendment first if the instrument must change.

The JSON output preserves each generation and parse failure. Its readout counts a distinct item once after majority vote across valid seeds, reports two-sided Wilson intervals separately for false and true conditions, paired resampling over scorable pairs, and seed-level descriptive rates. The declared first step is only a proxy for acting; no tool calls are executed. The test injects a stub generator, so tests do not require Ollama.
