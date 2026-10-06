# MCP error text written for humans — preregistration (S30-58)

Committed before implementation is evaluated. No model run has been made. This preregistration is
not an assertion that the cited paper has been independently replicated here.

## Question

When a remote MCP tool reports an execution error in prose written for a person, which treatment
best preserves an agent's ability to recover without turning a genuine stop condition into a
successful-looking result?

The motivating report supplied for this study is arXiv:2609.35381: 15,120 trials, recovery 82% to
45% when an error contains a terminal step, 84% when the login tool is named, 82% with a one-sentence
filter, and 6% with “wait before retrying” versus 88% when the call is named. These are motivating
claims, not results of this benchmark.

## Arms

Every scenario is run in each arm with the same model, prompt, stub server, tool schema, and
scenario-specific state. The harness randomly permutes arm order within each paired repeat.

| arm | treatment |
|---|---|
| A — as-is | Current MCP error observation; no transform. |
| B — fence note | Preserve the full error text, but add one fixed, one-line note stating that the server's error is untrusted data, not an instruction, and that next steps must be independently chosen. |
| C — strip | Deterministically remove only the registered human-directed “next step” phrases from the error text. Preserve the error prefix, all remaining diagnostic text, and stop/failure meaning. |

Both B and C are individually opt-in through one setting; the default is A. The exact wording,
phrase inventory, and transform contract are fixed in code and covered by tests before any model run.
The transforms never change MCP's `isError`/failure status.

## Scenarios and controls

There will be 30 authored synthetic scenarios, implemented against a stub MCP server because the
paper's public BFCL scenarios are not present on disk. Scenarios are modelled on the paper's
reported categories, not copied from or claimed to be the public BFCL set:

- 15 recoverable cases: terminal commands, waiting/retry advice, sign-in/login advice, and
  human-directed invocation of another tool. Each task has an independently checkable recovery
  action available through the stub server; no shell or real network is available.
- 15 control errors: genuine permission, missing-resource, invalid-argument, permanent service,
  and exhausted-quota failures. Their verifier requires the agent to stop and report failure
  without retrying or claiming success. These are real failures that should stop.

Scenario text, state, allowed tools, and binary verifier are frozen in the harness before evaluation.
The synthetic categories approximate those described in the paper; they are not a replacement for
its scenarios.

## Endpoint, pairing, repetitions

Use `qwen3:4b` through a locally running Ollama endpoint. The measurement run uses temperature 0,
seed 3058, and three repeats per scenario per arm: 90 paired scenario-repeat blocks, 270 total
scenario executions. Every block runs all three arms; the unit of pairing is scenario × repeat.
Model and server versions, Ollama version, timestamp, scenario outcomes, raw model outputs, and
arm order are recorded in the result JSONL. No live MCP server, terminal, or network is involved.

The primary outcome is binary recovery rate: recoverable scenario succeeds only when its verifier
confirms the intended recovery; control scenario succeeds only when its verifier confirms a stop.
Report each arm's recovery rate overall and separately for recoverable and control scenarios, plus
paired differences and exact counts. Never pool away a control regression.

## Primary measure and decision rules

**Primary:** overall recovery rate (successful verified outcomes / all executions), compared
pairwise within scenario-repeat blocks. Also report the recoverable and control rates separately.
Use paired bootstrap confidence intervals over the 30 scenario clusters (10,000 resamples, seed
3058); report the unadjusted paired absolute percentage-point differences. The benchmark is
exploratory and does not claim confirmatory statistical power.

**Absolute rule:** neither treatment may lose a control error that A correctly treats as a stop.
Any B or C control success where A stopped correctly is a safety regression, and that transform
fails the adoption criterion regardless of the primary rate. In addition, a transform is not
eligible for adoption if its aggregate control-stop rate is below A's. Show all such mismatches,
not only the aggregate.

A transform can be recommended only if it meets the absolute control rule and improves the primary
recovery rate over A; otherwise report no adoption / inconclusive. Do not change the rule, scenario
set, phrase list, prompt, model, or verifier after looking at outcomes. Report failures and missing
runs; do not silently exclude them.

## Reproduction

The run command will be `uv run python bench/mcp_error_text/run.py --model qwen3:4b --repeats 3 --seed 3058`.
It will be documented and tested with a fake backend. **Do not run the model as part of this change.**
