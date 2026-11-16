# Proportionality block — pre-registration

**Written 2026-11-16, before any paid call of this bench.** Item P1-17 of
`study/auditoria-2026-11/PLANO-FINAL.md` (audit finding a03-02). Committed first; the numbers below
are not loosened afterwards. If the result kills the claim, that is the result.

## 1 · The question

The base prompt of the Code agent (`DEFAULT_SYSTEM_PROMPT`, `chimera/core/agent.py`) says "Your job is
to DO the task" and that a final answer which only tells the user what they "can" or "should" do is
a failure. It has no sentence for the opposite case: a question whose answer is already in what the
agent can read. The audit's claim was that this costs steps and output tokens on read-only questions.
That was **a hypothesis read from the prompt text, not a measurement** (the verifier withdrew it as a
finding).

The change under test is one block, `PROPORTIONALITY_BLOCK`, appended after the base prompt when
`AgentConfig.proportionality` (`CHIMERA_PROPORTIONALITY`) is on. It is the whole difference between
the arms. The block says: match the size of the answer to the size of the question; answer a
question that can be answered from what can be read, directly, and stop; do not look for something
to change; **a request to change, add, fix or remove something is still a task**.

## 2 · Why this prompt is dangerous to touch

The same paragraph carries the "bakery exception" (`faz um site pra minha padaria`), a narrow
carve-out that exists because the unconditional "DO the task" produced five files nobody asked for.
A sentence that pushes the agent toward answering instead of acting can reopen the opposite failure:
an edit task answered with an explanation. So the rule below is a **two-sided** gate, and the second
side (edits, controls) can veto the first.

## 3 · Corpus (frozen; `corpus.py`)

| family | n | source | what is measured |
|---|---:|---|---|
| read | 12 | questions about three real modules of this repository (`read_fixture/src/`, copies from origin/main 2026-11-16), one fact each, regex checker written from the source | steps, output tokens, answer correct |
| edit | 11 | `bench/edit_tools/tasks.py`, all tasks but `import_module_moved` (that bench excluded it: control arm 0/3) | pytest passes on a test restored from pristine |
| control | 2 | the bakery request (empty workspace) and a specific one-file request | bakery: asked a question and wrote no file; specific: file written |

Not mined from conversation history: those carry the owner's personal data. Consequence: the
questions are short and written in one voice; this corpus cannot say how the block behaves on a
long, vague, real conversation (§7).

## 4 · Design

- Arms: **OFF** (`proportionality=False`, today's prompt) and **ON**. Nothing else differs.
- Model: `openrouter/deepseek/deepseek-chat-v3.1` on both (US$ 0.25 / 0.95 per M tokens in/out).
- Agent: assembled as `chimera agent` assembles it (`build_right_hand`, `attended(...)`,
  `turn_context=True`), `max_steps=25` as a window. Fresh workspace copy and fresh `CHIMERA_HOME` per run.
- Replicas: **3 per (item, arm)** — "two alert, three decide" (§2x of the BEE lessons; the
  repository's replicated-run rule). Temperature is the product's default; a hosted T=0 does not
  reproduce (memory: cache-confound), so replicas are real draws, not copies.
- Runs: (12 + 11 + 2) × 2 × 3 = **150**. A pair is (item, replica); arm order inside a pair and pair
  order overall are shuffled once with seed `20261116`.
- Spend: the runner tallies `AgentResult.usd` per run and **stops before starting a pair once the
  tally reaches US$ 4.50**. The ceiling of the item is US$ 5.00. If the cost of the first 4 pairs
  projects past that, n is reduced by dropping replicas from the end of the shuffled order, never by
  dropping an arm or a family. Fewer than 24 read pairs or 22 edit pairs completed = **inconclusive**.

## 5 · Metrics and the adoption rule

All differences are ON − OFF, paired on (item, replica), and every interval is a **cluster bootstrap
over items** (10,000 resamples, seed 20261116; the replicas of one item are correlated, so the
replicas are not independent draws).

**Primary, on the read family:**
- P1: mean paired Δ of `steps`.
- P2: mean paired Δ of `completion_tokens` (the output tokens).

**Gates:**
- G1 (the answer survives): read correctness ON ≥ OFF − 5 pp.
- G2 (the edits survive): edit pass rate ON ≥ OFF − 5 pp, paired point estimate.
- G3 (the bakery survives): ON's count of "asked a question and wrote no file" ≥ OFF's, and ON's
  count of "specific file written" ≥ OFF's. Any drop is a veto; the counts are small (3 each) and the
  failure it guards is the one that was measured once.

**ADOPT iff all of:** P1 and P2 each have a mean Δ < 0 whose cluster-bootstrap 95% interval lies
entirely below 0 **and** a point estimate of at least −10% of the OFF mean (a block worth a sentence
in every prompt); and G1, G2, G3 hold. Otherwise **NOT ADOPTED**: the flag stays off and
`PROPORTIONALITY_BLOCK`'s docstring and `RESULTS.md` carry the number.

**What a pass on G2 does and does not mean.** With 33 pairs a 5 pp margin is under two runs; a pass
means "no drop beyond 5 pp was measured", with an interval of roughly ±15 pp, not "the same". It is
reported with that interval.

Secondary, recorded and not decisive: tool calls, prompt tokens, US$ per run, `stopped_reason`
counts, how many ON runs changed no file on an edit task.

## 6 · Predictions (written before the first run)

- The fixture questions need at least one read call whichever the prompt; the room for fewer steps is
  small. I expect P1 Δ in [−0.6, 0] steps and P2 Δ in [−120, 0] tokens, and I give **P(ADOPT) ≈ 0.3**:
  the likelier outcome is an effect inside the interval's width.
- Edits: pass ON − OFF within ±1 pair of zero.
- Bakery: unchanged.
- Cost: ≈ US$ 2.5 for the 150 runs (deepseek-v3.1 at the prices above, ~10 steps on an edit task).

## 7 · What this bench cannot say

- It cannot say how the block behaves on a real long conversation, a vague question, or a question
  about code the agent has not seen: the corpus is three modules and eleven toy packages.
- One model. Harness effects flip sign across models (arXiv 2610.00917); a null or a win here is
  about deepseek-chat-v3.1 only.
- The correctness checker is a regex on the final answer: it can pass a wrong answer that mentions
  the right token. It is symmetric across arms.
- Question wording was written by me, once, and not tuned; there is no held-out set.

## 8 · Cost and ceiling

Predicted US$ 2.5, ceiling **US$ 5.00** for the whole item, admission stop at US$ 4.50. The key is
read from the working directory's `.env` by the product's settings and is never printed.

## 9 · Added later: answers to `bench/PROTOCOL.md` §11–§14

*Added 2026-11-16, after the pre-registration commit and while the runs were being collected; the
only analysis seen when it was written was a 5-pair look at the first rows. It does not change the
hypothesis, the corpus, the metrics, the 10% effect floor, the gates or the adoption rule. It changes
one thing, and says so: the interval estimator in §5.*

- **§11 (the interval).** §5 said "cluster bootstrap over items". §11 forbids a percentile bootstrap
  under N = 100 (it covers ~88%), and here the clusters are 12 and 11. Replaced by the closed form:
  each item contributes one number (the mean of its replicas' deltas) and the interval is
  `mean_t_interval` of `chimera/eval/proportions.py` over those. "Interval lies entirely below 0" in
  the adoption rule now reads that t interval. If the deltas show no spread at all the interval is
  undefined (`±inf`) and the condition is not met.
- **§12 (a "no worse" claim needs a margin).** G2 and G1 are non-inferiority claims with a declared
  margin of **−5 pp**, one-sided. The adoption rule keeps the point-estimate reading written in §5
  (that is what the plan asked for). `report.py` also prints the TOST (`tost_mean` on per-item
  differences) beside it. It will probably not show non-inferiority at 11 items, and a pass on the
  point estimate is then reported as "no drop beyond 5 pp measured, not shown non-inferior".
- **§13 (controls).** Trivial-agent arm: not applicable, the read checker is a regex on a fact in a
  file and a do-nothing agent cannot produce it, and the edit verdict is a pytest that fails on the
  untouched fixture (verified: every edit fixture's test fails before the edit). Random/selection arm,
  grader-hijack probe, attack success, counterfactual-judge probe, placebo arms: not applicable (no
  selection mechanism, no LLM judge, no attacker). The edit verdict restores the test file from
  pristine before running it, which is the wall for the grader. A same-length placebo arm (an
  irrelevant sentence of the block's length) is **not run**: this bench measures the sentence's
  content, and the missing placebo is a stated limit: an effect could be the length, not the words.
- **§14 (removal needs two model families).** The bench measures adding a block, not removing one.
  NOT ADOPTED leaves the flag off, which is today's behaviour, so nothing is removed; the verdict is
  worded "measured on deepseek-chat-v3.1" and no second family is claimed.
- **Entry point.** `run.py` does not pin the five 2026-11-10 switches back to their legacy values:
  it measures the block on the product as it ships. Their effective values are written to
  `switches.json` next to the results.
