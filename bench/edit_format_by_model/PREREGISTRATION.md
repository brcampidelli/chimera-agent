# Does the cheaper edit format differ by model family? — pre-registration

**Written before any arm ran. No outcome was seen first.**

The claim under test is the one that would justify per-model tailoring at all: that a harness should
give an OpenAI-family model patches and a Claude-family model whole blocks, because each is cheaper
in its own family. Chimera does neither — one system prompt, one tool schema, one edit format for
every model, with a single vendor branch anywhere in the codebase (an Anthropic `cache_control`
header). Before building the machinery to tailor, this measures whether there is anything to exploit.

Today's parallel-tools census is what makes the question worth asking rather than assuming: it
measured a **23-point spread between model families** in how often they put two tool calls in one
turn — `gemini-3.8-flash` and `deepseek-chat-v3.1` never did it across 414 steps, while
`deepseek-v4-flash-0731` did it 23.3% of the time. Families demonstrably differ in how they drive a
tool-calling API. Whether that difference reaches *cost of an edit* is what this run answers.

---

## Two facts from this repository that shape the design

**Pass/fail is saturated and cannot be the outcome.** `bench/edit_tools/RESULTS.md`, on this same
task set: *"22 usable pairs out of 22 attempted; every run in both arms passed its verifier."* An
outcome at ceiling in both arms discriminates nothing, and choosing it would produce a run that
looks careful and measures noise.

**The prior from this repo is that edit-surface changes move calls, not money.** The same bench found
`edit_batch` cut edit calls by a median of 2.0 with a CI excluding zero, and moved completion tokens
by −33 with a CI of [−85.5, +70.0] — not significant. So the honest expectation going in is that
format changes cost counts rather than tokens, and the primary outcome is set to **the thing that is
actually paid for**.

## Arms — forced, not suggested

| arm | tool surface | the agent must |
|---|---|---|
| **P — patch** | `write_file` denied | edit in place with `edit_file` / `apply_patch` |
| **W — whole** | `edit_file`, `apply_patch` denied | rewrite whole files with `write_file` |

Forced via `CHIMERA_TOOL_DENYLIST` rather than nudged in the prompt, because a nudge measures how
obedient a model is and this is meant to measure which format is cheaper *for it*. Reading tools
(`read_file`, `list_dir`, `grep`, `glob`) and `run_shell` are identical in both arms.

## Models

Three families, one cheap model each, all three present in the census that motivated this:

- `openrouter/deepseek/deepseek-v4-flash-0731` — DeepSeek (and the shipped default)
- `openrouter/z-ai/glm-5.3-flash` — Z-AI
- `openrouter/google/gemini-3.8-flash` — Google

## Tasks

The 11 tasks `bench/edit_tools/run_ab.py` uses — its 12 minus `import_module_moved`, which that
bench's own pre-registration excluded because the control arm failed it 3/3. The exclusion is
inherited rather than re-decided, so it cannot be a choice made after seeing anything here.

11 tasks × 2 arms × 3 models = 66 runs.

## Primary outcome and decision rule, fixed now

**Completion tokens to a passing verifier**, per (task, arm, model). Verdict is the existing
`_verdict` — our pytest, on a test restored from pristine, so an agent that edits the test into
passing scores nothing.

The question is an **interaction**, not a main effect. A format that is cheaper for every model is a
better default, not a reason to tailor.

- **BUILD per-model tailoring** if the sign of (W − P) **differs between at least two families**,
  *and* each of those two within-family medians differs by **≥ 20%** of that family's median tokens.
- **REJECT** if the same arm is cheaper in all three families, whatever the margin: that is a
  finding about the default, not about tailoring.
- **REJECT** if the differences are inside ±20%: a branch per family is a maintenance liability that
  ages with every model release, and a small effect does not pay for it.

Secondary, reported and never used to decide: pass rate (expected at ceiling — if it is not, that
changes what the token figures mean and is reported before them), edit calls, tool calls, wall clock.

## Gates before the aggregate is believed

- **Pass rate is read first.** A cell where an arm fails is a cell whose token figure is the cost of
  failing, not the cost of the format.
- **Both arms are checked for the tool they were denied.** An arm that still called the denied tool
  did not run the condition it is labelled with, and the pair is void.
- **A run that used zero edit tools of any kind** did not do the task the way the arm describes, and
  is inspected rather than counted.

## What this cannot show

- **One model per family, one size tier.** All three are "flash"-class. A frontier model in the same
  family may behave differently, and nothing here transfers to it.
- **Small fixtures.** `tasks.py` says it itself: *"a rename across four toy modules is not a rename
  across django."* Whole-file rewriting is cheapest exactly when files are small, which biases arm W
  upward relative to a real repository. Stated rather than corrected — correcting it means a much
  larger corpus.
- **One repeat per cell.** This is a screening run: it can find a large sign flip between families
  and cannot resolve a subtle one. If it finds nothing, "no effect this size" is the claim, not "no
  effect".
- **Nothing about prompt or schema tailoring.** Only the edit format is manipulated. A family
  difference in how a system prompt should be worded is a separate question with a separate ruler.

## Cost

66 `chimera solve` runs on flash-tier models. Estimated under two dollars, and reported as measured
rather than estimated once the run is done.

## Result

`bench/edit_format_by_model/RESULTS.md`.

---

## Amendment: one gate was measuring the wrong thing

Made while the grid was still running, and recorded with what had already been seen.

The gate *"both arms are checked for the tool they were denied"* voided a pair when the denied tool
appeared in the run's `tool_names`. That list records what the model **asked for**, not what ran:
`agent.py` appends the name at line 729 and calls `_run_tool` at line 733, and a denied tool is
absent from the registry, so `_run_tool` returns `"error: unknown tool"` and nothing executes.

So a denied name in that list is the manipulation **working** — the model reaching for the format it
was refused — and voiding those pairs would discard exactly the runs where the condition bit
hardest. The gate now voids on two things only: the verifier failed, or the arm never used the tool
it was supposed to.

**What had been seen when this was found.** A dry run of `analyse.py` over the first 15 of 66 rows
printed one family's figure: `deepseek-v4-flash-0731`, 4 usable pairs, W cheaper by 9.0% — below the
20% margin, so "reject" on that partial slice. That is stated because the alternative is asking a
reader to trust that a correction made mid-run was not steered by a number.

The correction rests on a fact about the instrument that is checkable in the source and has nothing
to do with any outcome. The **decision rule is unchanged**. And `analyse.py` now prints the verdict
under *both* gates, so whether the correction moved the answer is visible rather than asserted.

---

## Amendment: a timed-out run was scoring as a free success

Found at row 29 of 66, while the grid was still running.

Two rows carried `completion_tokens = 0` with `usd = 0.0000` and `tool_calls = 0`. Both had
`exit = 124` — killed at the 900-second timeout, so no receipt was ever written. And one of them,
`glm-5.3-flash / P / signature_swap_args`, was scored **`passed = True`**: `_verdict` runs pytest
over whatever the process had already written to the workspace, and by then the edits were done.

A run whose price was never recorded cannot appear in a table of prices, and a timeout entering the
median as a zero-cost success would drag the arm that timed out downward — the exact opposite of the
truth about it. So a third void is added: **`exit == 124` or zero recorded tokens**, whatever the
verdict says.

Voided on the measurement rather than on the verdict, and by pair as the others are.

**What this costs, stated rather than discovered later.** Voiding timeouts removes the tasks an arm
could not finish inside fifteen minutes, which are the hardest ones — so the surviving comparison is
between tasks both arms completed, and it understates any difference that shows up only under
pressure. Two of the first 29 rows, about 7%.

**What had been seen when this was found.** Three individual rows from the progress line
(`glm-5.3-flash W signature_swap_args 6968 tokens`, `P signature_return_shape 3781`, and the two
zeroes above). No aggregate has been computed since the previous amendment, and the decision rule is
again unchanged.
