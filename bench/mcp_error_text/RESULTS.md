# S30-58 — MCP error text: results

Run on 2026-10-06 with local Ollama `qwen3:4b`: 30 scenarios, 3 repeats, seed 3058, `think: false`,
US$ 0. Every arm sees the product's fenced observation (`fence_observation`), as the amendment
registered. Raw trials are in `results/run.jsonl` (270 rows).

| arm | overall recovery | recoverable errors | control errors stopped |
|---|---:|---:|---:|
| A — as today | 58/90 (64.4%) | 13/45 | 45/45 |
| B — one-line fence note | **72/90 (80.0%)** | **27/45** | 45/45 |
| C — strip "next step" advice | 45/90 (50.0%) | 0/45 | 45/45 |

Paired against A (90 scenario-repeat blocks, bootstrap over the 30 scenario clusters, 10,000
resamples):

| | difference | 95% CI | control regressions |
|---|---:|---:|---:|
| B fence | **+15.6 pp** | +3.3 to +28.9 | 0 |
| C strip | −14.4 pp | −27.8 to −3.3 | 0 |

## Verdict, by the registered rules

- **B, fence note: meets both.** It loses no control error that A stopped (0 mismatches; 45/45
  against A's 45/45), and it improves primary recovery with an interval that excludes zero. Under
  the registration it **can be recommended**. Following the owner's standing rule that a setting the
  bench recommends becomes the default, it flips in a separate change.
- **C, strip: not adoptable.** It keeps every control stop but makes recovery worse.

## Why strip fails, read from the observations

The strip removes the whole advice clause, and in these errors the advice clause is also where the
**name of the tool** lives. For example, "The report is not available yet; run `refresh_report` in
your terminal." becomes "The report is not available yet;". With the tool's name gone, the model
stops on all 45 recoverable errors. This matches the paper's own finding that naming the tool is
what restores recovery. A strip that kept tool names would be a different instrument and would need
a new registration.

## What this run cannot show

- These are synthetic scenarios modelled on the paper's categories, with a stub MCP server. The
  paper's public BFCL scenarios were not on disk.
- Exploratory, with no confirmatory power claimed. The result covers one local 4B model, so a
  hosted model may weigh the note differently.
- Whether the note changes anything when the error text is benign and the model already recovers.
