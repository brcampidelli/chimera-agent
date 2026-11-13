# Pre-registration — does `.rgignore` shrink what a code search puts in the context, without losing the answer?

**Registered 2026-11-12, before any model call. No model has been called for this file.** This is
item P1-16 of the 2026-11 audit plan. The `.rgignore` and `bench/INDEX.md` ship in the same change;
the paired run with a model is deliberately NOT run now, and nothing below has a reading yet.

## Hypothesis

A search for a symbol in this repository returns, besides the definition, lines from bench data
(`results*.jsonl`, `results*/`), the generated desktop schema (`api-schema.ts`, 26,802 lines),
`openapi.json` and the lockfiles. Those lines are context the agent reads and pays for, and they
can displace the file that matters. With `.rgignore` they are not returned. Prediction: **input
tokens per task fall, and accuracy does not fall.** If tokens do not fall, the file is a tidiness
change with no measured effect and is reported that way.

The file counts are already known and are the control's anchor. On `origin/main` 90b49c57, before
the `.rgignore`: `rg --files | wc -l` = **7346** without it and **3882** with it. The change that
adds the file also adds six files that `rg` lists (this registration, `bench/INDEX.md`,
`scripts/gen_bench_index.py`, two tests, `chimera/api/bridge_transport.py`), so on that tree the
same counts are **7352** and **3888**; the run checks the pair for the tree it runs on, with
`--no-ignore-dot` for the first. `rg --files bench | grep -c -E 'PREREGISTRATION|RESULT'` is 286
on `origin/main` both ways (287 once this file exists), and `rg -l to_openai_schema chimera tests`
returns the same 16 files both ways.

## Design

- **Two arms, same tasks, same model, same seeds.** Control: the search tool runs `rg` with
  `--no-ignore-dot` (so `.rgignore` is not read). Treatment: `rg` as it runs by default with the
  file present. Nothing else differs: same system prompt, same tool list, same temperature.
- **Model:** one, the product default `openrouter/deepseek/deepseek-chat` through the gateway,
  pinned by exact route. A different model is a different registration.
- **Seeds:** 2 first. **Two seeds alert, three decide** (`bench/PROTOCOL.md`; `replicated.py::seeds_verdict`):
  a verdict is not written from two. If the two seeds disagree on the sign of the token change or on
  any lost task, a third is run before anything is concluded.
- **The ten standard tasks.** Each asks where something is defined and has one correct file,
  checked by a string match on the path in the final answer. Gold answers were read from the source
  now, not from any model:

  | id | question | gold |
  |---|---|---|
  | T01 | Where is the class `LLMGateway` defined? | `chimera/providers/gateway.py` |
  | T02 | Where is `check_bind` defined? | `chimera/server/bind.py` |
  | T03 | Where is `read_discovery` defined? | `chimera/api/bridge_discovery.py` |
  | T04 | Where is `seeds_verdict` defined? | `chimera/eval/replicated.py` |
  | T05 | Where is the class `MessageGateway` defined? | `chimera/server/gateway.py` |
  | T06 | Where is `tost_paired` defined? | `chimera/eval/proportions.py` |
  | T07 | Where is `answer_at_end_of_reasoning` defined? | `chimera/providers/thinking.py` |
  | T08 | Where is `is_big_task` defined? | `chimera/api/big_task.py` |
  | T09 | Where is the class `_ReexportsAreReadOnly` defined? | `chimera/cli/main.py` |
  | T10 | Where is `iter_sse` defined? | `chimera/cli/code_service.py` |

  They are fixed and are not replaced after a reading. Several of these names also occur in bench
  result files and in the generated schema, which is the case the hypothesis is about; that choice
  is declared here and limits the claim (see §14).
- **Metrics.** (1) Input tokens per task, as the provider reports them. (2) Accuracy: task correct
  or not. Recorded per task and per seed; the unit of analysis is the task, so n = 10 pairs.

## Adoption criterion

Adopt the claim "`.rgignore` shrinks search context without losing the answer" only if **both**
hold on the pre-declared reading: (a) the paired change in input tokens per task is negative, and
the interval named in §11 below excludes zero; (b) **no task that the control answered correctly in
a seed is answered wrongly by the treatment in the same seed.** A single lost task is a refutation
of (b), reported with the same prominence as a win. Tokens falling while a task is lost is not an
adoption; it is the finding that the file hid something the agent needed.

## §11 The interval

Per-task paired difference in input tokens (treatment minus control), 10 tasks, median over seeds.
The interval is a **t-interval on the paired mean of the log-ratio**, with the sign-test count of
tasks that fell reported beside it; no percentile bootstrap, which is not used under N = 100. Accuracy
is a count of lost and gained tasks, not an interval: ten tasks cannot carry a Wilson interval that
would mean anything, and the registration does not pretend otherwise.

## §12 The margin

**No equivalence claim.** With ten tasks a TOST cannot reach a margin worth declaring, so the
registration claims one direction only (tokens fall) plus a hard count (zero lost tasks). It does
not say "accuracy is unchanged"; it says "no task was lost in these seeds." A null on tokens is
reported as its interval and what ten tasks could have seen, never as "the file does nothing."

## §13 The controls

- **The control arm reproduces a known number before any model call.** It must return the control count
  for `rg --files` (7352 on the registered tree) and the treatment the other (3888). If either differs, the apparatus is
  not the one registered, and nothing is run.
- **Placebo arm: does not apply.** Nothing is added to a prompt; the only difference is which files
  a tool returns. The nearest equivalent is the control above.
- **Invariant:** a call whose answer equals the gold path must be scored correct in both arms; the
  scorer is run on the gold answers first and must give 10 of 10.
- **Contamination:** the control arm uses `--no-ignore-dot`, not a checkout without the file, so the
  working tree is identical.

## §14 The model scope

One model, one route, one repository state, ten definition-lookup tasks. The claim, if adopted, is
about **this repository's search results for this model on lookups by name**. It does not extend to
other models, to searches by behaviour, or to repositories without large data files. A second model
family is a separate registration (`bench/PROTOCOL.md` §14).

## §15 Perturbations

This registration claims **one configuration only**: one model, one seed set, one task order. Task
order is fixed T01 to T10.

## What this file does not do

It does not run the model, and it does not state a result. The run, if it happens, writes
`bench/rgignore_context/RESULTS.md` from this file's registered readings, and any change to the
tasks, the model or the criteria above is an amendment committed before the run that uses it.
