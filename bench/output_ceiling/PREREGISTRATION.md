# Output ceilings: MCP 60 000 vs 20 000, and `keep_tail` — preregistration (P1-04)

Committed before anything is run. **No model call has been made**, and none is paid for by this file.
Both treatments ship OFF; this document is the condition for turning either one on.

## Question

Does a lower ceiling on what a tool puts in the model's context (MCP results at 20 000 characters
instead of 60 000, with the excess kept in a file) or keeping the head AND the tail of a command's
output (`CHIMERA_EXEC_OUTPUT_TAIL`) change how often an agent completes its task, relative to the
shipped behaviour?

## Arms (two independent comparisons, same items, same model, same prompt, same stub servers)

| comparison | OFF (control) | ON (treatment) |
|---|---|---|
| MCP ceiling | `CHIMERA_MCP_DEFAULT_MAX_OUTPUT_CHARS=60000` | `=20000` (excess written to `mcp-output/`) |
| Command tail | `CHIMERA_EXEC_OUTPUT_TAIL` unset | `CHIMERA_EXEC_OUTPUT_TAIL=1` |

**OFF against ON on THIS code, never against a baseline recorded earlier.** The comment in
`chimera/tools/clip.py` already says why: `code_interpreter`'s marker opens the truncation trap's
mask in `chimera.eval.scenario_traps`, and the central ceiling added in this change adds one more
marker site, so any older baseline was measured with another ruler (lessons §2g, §2d). Arm order is
permuted within each paired repeat.

## Items and seeds

* The same items in both arms of each comparison; paired by item.
* Items: the `scenario_traps` truncation trap for the tail comparison; for the MCP comparison,
  authored tasks whose answer sits (a) in the first 20 000 characters, (b) between 20 000 and
  60 000, and (c) in the last part of a long result. The verifier is binary and frozen before the run.
* **3 seeds per arm** (two alert, three decide; lessons §2x). The spread between seeds is printed
  next to the mean and compared to the sampling error of the difference (SE x sqrt 2) before any
  difference is attributed to the treatment.

## Control arm that reproduces a published number

Before any treatment is read, the OFF arm must reproduce a number already published by this
repository on the same items (the `scenario_traps` truncation-trap rate recorded in its RESULTS), on
the same GPU/provider route and batch settings. If it does not, there is no comparison to read: two
wrong numbers that agree are not a result (lessons §2aa, §2ae).

## Decision rule (registered now, not loosened afterwards)

Non-inferiority, absolute, in percentage points on task success: the treatment is adopted only if the
lower bound of the 95% interval of (ON minus OFF) — one difference per item, the item's completion
fraction over its 3 seeds in ON minus OFF, one-sample t (`mean_t_interval`) — is **above -5 pp** for
each comparison separately. Inside the margin but with an interval wider than the margin is "not shown", not
"equivalent". A result below the margin is published with the same prominence as one above it.

Also reported, never folded into the headline: tokens in context per run (the point of the change),
the number of times the model asked for the cut part (`offset`, `start_line`, or the saved file), and
the spans where `tool.output_clipped` fired, so an arm that never reached a ceiling reads as "did not
act" rather than "no effect" (lessons §2r).

## What is not claimed

The central ceiling in `ToolRegistry.run` is 20 000 characters, the same figure every tool with its
own cap already used, and changes nothing that was already bounded. It is not part of this
comparison; the comparison covers only the two options that change what the model sees on tools that
were already capped.

## `bench/PROTOCOL.md` §11–§14

- **§11, the interval.** Seeds of one item are correlated, so the unit is the item: one ON−OFF
  difference per item, one-sample t (`mean_t_interval` in `chimera/eval/proportions.py`). No
  bootstrap decides. Per (item, seed) pairs, Bonett-Price with exact McNemar
  (`bonett_price_paired`, `mcnemar_exact`) are printed beside it as a cross-check only.
- **§12, the margin.** Non-inferiority, one-sided, at −5 pp, declared here before the first call.
  The rule reads the two-sided 95% interval, stricter than the `1 − 2α` = 90% interval §12 requires as
  a minimum; `tost_mean(values, margin=0.05)` is printed beside it. A spanning interval is "not shown".
- **§13, the controls.** *Trivial-agent arm:* one arm that answers without calling any tool is scored
  by the same verifier on every item, once per seed, so the floor is known; an item the trivial arm
  passes is dropped and named. *Grader-hijack probe:* before any scored run, a probe writes a
  `conftest.py` and a decoy answer file into the task tree and the verdict must not move. *Random arm,
  attack scoring, detection probe, rule-withdrawn arm:* no selection mechanism, attack, judge or
  instruction rule is measured; not applicable. *Placebo arms:* the treatments add no text to the
  prompt — they change how much of a tool's own output is shown — so a format or same-length placebo
  has nothing to imitate; not applicable.
- **§14, model scope.** Both treatments *lower* what the model sees, which is the kind of
  simplification §14 covers. A verdict names the model it was measured on in the same sentence, and
  neither option becomes a default until a second model family is measured with this registration.
