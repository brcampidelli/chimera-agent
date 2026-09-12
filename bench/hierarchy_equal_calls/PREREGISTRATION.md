# Pre-registration — at an equal number of calls, does the hierarchy beat one agent that re-reads?

**Registered 2026-09-11 against `c8b8e74` (main after #425), before a single model call.**

## Where the question comes from

`bench/hierarchy` and `bench/hierarchy_multistep` compare one single-agent call against the
orchestrator (one worker per document, then a synthesis) on the same model and report tokens
honestly: +47% single-shot, −66.5% multi-step. Neither holds the **number of calls** equal — the
hierarchy makes D + 1 calls where the baseline makes 1 — and both are at ceiling on quality (100% vs
100%), so they can say what the hierarchy costs and nothing about whether it *answers better*
than a single agent given the same compute.

arXiv:2609.04217 (slice 16 of the 2026-09-11 sweep, `bench/PLAN-study17-arxiv-sweep.md` item A8):
with total LLM calls fixed and a single frozen backbone, a planner–executor–critic team scored
0.769 against a single executor's 0.754 on ALFWorld, **p = 0.80**, while spending 1.8× the
evaluation calls; leave-one-in showed all realised value was the executor's, and the planner and
critic prompts evolved to empty. SimTIO (2609.05740) at an equal simulation budget: not
significant. 2609.04898: a single agent with retrieval beat sub-agents on code. The desktop
Orchestration tab routes tasks to exactly this machinery, and its counterfactual line has never
been measured against a single agent given the same calls.

## The instrument, and why it is not the two benches above

Both existing suites are at 100% on the mid model, so they cannot exhibit a quality effect (§2q).
This bench uses the **same ten read-heavy tasks** as `bench/hierarchy`
(`chimera.eval.hierarchy_ab.synthetic_tasks`, 2–4 documents each, planted facts, deterministic
all-needles grading) on a **weak backbone for every role**, chosen so the single call is off the
ceiling: `openrouter/meta-llama/llama-3.1-8b-instruct` (weak, 128k context). The backbone is frozen
across arms, as in the paper — every call in every arm is the same model, so the comparison is
about the *arrangement of calls*, not about model strength.

*Amendment, same day, after a one-task pilot and before the registered run:* the published
grader `hierarchy_ab.check` wants the first figure of each fact **and the word after it**,
verbatim — `"3.1 requires"` for *"Alpha 3.1 requires Python 3.12"*. The pilot's single call answered
*"**Alpha 3.1** · Requires: Python 3.12"* — the right value, a different phrasing — and failed all
four needles; so did every other arm. The mid model in `bench/hierarchy` wrote the sentences back
verbatim; an 8B model does not, and a grader that scores that difference is scoring style (§2l).
This bench grades **values**: for each planted fact, its last word and its last figure must appear
(`value_check`); the verbatim verdict is recorded beside it, never used. The pilot trial is kept
in `results/pilot.jsonl` and is not part of the run.

**Instrument check, first:** the single-call arm must land between 20% and 85% pass@1 over
10 tasks × 3 runs. Above 85% the ceiling is back and the run is void (reported, not scored); below
20% the model cannot do the task at all and the comparison is between two failures.

*Amendment 2, same day, after the first registered run:* on the 8B backbone the single call scored
**0.97 pass@1** — the ceiling, the run is **void by the rule above** and is kept as
`results/2026-09-11.jsonl` / `2026-09-11-8b-report.md`, not scored. What it showed anyway is
recorded in RESULTS.md as an observation, not a finding. The second run uses
`openrouter/meta-llama/llama-3.2-3b-instruct` on every role, nothing else changed.

## Arms — every call on the same backbone

| arm | calls per task | what |
|---|---|---|
| `single_1` | 1 | all documents inline, the full question — the existing baseline |
| `single_equal` | D + 1 | the baseline call, then D re-reads: *"here is your previous answer; re-read the documents and revise it so every part of the question is answered with the exact values"*; the last answer is graded |
| `hierarchy` | D + 1 | `HierarchicalOrchestrator.run_prepared` with one worker per document and the synthesis, `fuse_final=False`, spot check off — the shipped arm of `bench/hierarchy` |
| `hierarchy_no_synth` | D | leave-one-in: the D workers, and the answer is their summaries concatenated — no synthesiser call |

D is the task's document count (2–4), so `single_equal` and `hierarchy` make exactly the same
number of calls on every task.

Three runs per task per arm (`ReplicatedArm`, `pass^k` and flip rate), temperature 0.3 on every call.

## Outcomes

- **Primary:** `hierarchy` vs `single_equal`, paired on per-task `pass^3` (`compare_replicated`),
  with the noise floor beside it — the paper's comparison.
- **Secondary:** `single_equal` vs `single_1` (does re-reading help at all — the paper's "extra
  compute" question); `hierarchy` vs `hierarchy_no_synth` (leave-one-in on the synthesiser);
  `hierarchy` vs `single_1` (the comparison the existing benches make, now off the ceiling).
- Tokens per arm, measured, and the number of calls, counted.

## Registered prediction

The paper's: **`hierarchy` does not beat `single_equal`** — the paired difference on `pass^3` has
a 95% interval that includes zero and a point estimate inside the noise floor. `single_equal` ≥
`single_1` by a small margin. `hierarchy_no_synth` ≤ `hierarchy` by a small margin (the
synthesiser recovers some needles the concatenation buries, but the workers carry the value).

## Decision rule

None of this changes code. It gives the Orchestration tab's counterfactual line a number: if the
prediction holds, the tab's preview says *"at the same number of calls, one agent that re-reads
the documents does as well on tasks like these"*, and the token saving is reported as the reason
to orchestrate, not the answer quality. If the hierarchy wins by more than the noise floor with an
interval that excludes zero, that sentence is not written and the number is published as the case
for the split.

## Cost

10 tasks × 3 runs × (1 + (D+1) + (D+1) + D) calls ≈ 10 × 3 × 11 ≈ 330 calls on an 8B model with
~3k-token prompts: **≈ US$ 0.30**.

## What this cannot show

Read-heavy extraction on synthetic documents with planted needles; one weak backbone; no tools;
no multi-step feedback (the paper's ALFWorld has an environment that answers back, this does not).
It says nothing about `IsolatedCrew` on writing tasks, which the tab also routes to and which no
gradable corpus here can measure without the ceiling problem `bench/fusion_paired` hit.


## Addendum — the verbatim synthesis arm (registered 2026-09-11, after RESULTS.md, before any call)

RESULTS.md named the follow-up: on the 3B backbone the workers' summaries carried the values (0.57
pass@1 concatenated) and the synthesis over them did not (0.27). A fifth arm, `hierarchy_verbatim`,
is the `hierarchy` arm with one sentence appended to the synthesis system prompt
(`HierarchyConfig.synthesis_verbatim`): *carry every figure, name, version, path and identifier from
the summaries into the answer exactly as written*. Same backbone, same ten tasks, three runs, D + 1
calls — the only difference is that sentence.

**Prediction:** `hierarchy_verbatim` recovers at least half the gap — pass@1 ≥ 0.42 — and the
per-task table moves in one direction. **Decision:** if `hierarchy_verbatim` beats `hierarchy` by
≥ 20 pp pass@1 with no task moving the other way, the sentence becomes part of `_SYNTH_SYSTEM`
(no flag); if it is within the noise, the flag stays off and the null is published; if it is worse,
the flag is removed.


## Addendum 2 — the verbatim sentence on the production synthesiser (registered 2026-09-11, after #435, before any call)

#435 adopted `synthesis_verbatim=True` on the strength of one backbone, a 3B model on every role,
and said so: *the production synthesiser is the top model, and what the sentence costs there is
unmeasured*. This addendum measures it. Same ten tasks, same three runs, same 3B workers
(`llama-3.2-3b-instruct`, the summaries the synthesis reads are then the ones already measured), and
the synthesis call alone on the production top tier, `claude-opus-5` (`--synth-backbone`). Two
arms, `hierarchy` (the prompt without the sentence) and `hierarchy_verbatim` (with it); nothing
else differs. The synthesis call's own completion tokens and price are recorded per trial.

**What the instrument can show.** The summaries carry the values on about 57% of runs
(`hierarchy_no_synth`, 0.57 pass@1), so a synthesiser that keeps everything it is given lands near
that ceiling in both arms; the question is not whether the strong synthesiser needs the sentence
but what it costs there — in tokens, and in tasks moved the wrong way.

**Predictions.** Both arms land within the noise floor of the workers-alone ceiling (pass@1
0.45–0.65), and the difference between them is inside the floor with no task moving consistently
in either direction (no task 3/3 in one arm and 0/3 in the other). The sentence adds **≤ 15%** to
the synthesis call's completion tokens.

**Decision.** The default stays `True` unless `hierarchy_verbatim` loses ≥ 2 tasks (3/3 → ≤ 1/3)
with none gained on the strong synthesiser, in which case the default is moved to `False` for the
top tier and the number published; a token cost above +15% is published as the price and does not
move the default on its own.

**Cost.** 60 synthesis calls on `claude-opus-5` at ~2k prompt and ~0.5k completion tokens ≈ US$ 1.4,
plus 3B worker calls ≈ US$ 0. **≈ US$ 1.5.**

**What this cannot show.** Ten tasks, a 50% flip rate: the size of any difference is a noise-floor
sentence; direction and the token count are what is read. One strong synthesiser; the tasks'
answers are the planted figures, which is the case the sentence was written for.


## Addendum 3 — the corpus goes from ten tasks to thirty (registered 2026-09-12, before any call)

Every number in this file, in `bench/hierarchy` and in `bench/hierarchy_multistep` was measured on
the same ten read-heavy tasks, and RESULTS.md said what that costs: at three runs the flip rate is
20–50%, so a 30–50 pp difference on `pass^3` is the size a task moves with nothing changed, and
every verdict is a noise-floor sentence. Twenty tasks of the same shape are added to
`chimera.eval.hierarchy_ab.synthetic_tasks` — two to four documents, one or two planted facts per
document under `## Key items`, a question naming the documents, every figure two or more digits
and unique within its task so a value from the wrong document never passes for the right one.
The first ten are unchanged and in their order; a run that wants the old corpus takes them.

**The instrument check, before anything is read.** On the twenty new tasks alone, `single_1` on the
3B backbone lands inside the registered 20–85% band. If it does not, the corpus is not an
instrument for this backbone and the run stops there with that number.

**The run.** The five arms of this bench, the 3B backbone on every role (the run whose direction
RESULTS.md read), thirty tasks, three runs: 450 trials, `results/2026-09-12-3b-30.jsonl`.

**Predictions.** With thirty tasks the per-arm flip rate falls to **≤ 35%** for every arm (it was
20–70% on ten); the four directions the ten-task run read hold on the twenty new tasks and on the
thirty — `hierarchy` below `single_equal`, `hierarchy_no_synth` above `hierarchy`,
`hierarchy_verbatim` above `hierarchy`, `single_equal` not above `single_1` by more than the
floor; and the primary comparison (`hierarchy` against `single_equal` on `pass^3`) has a Newcombe
interval that excludes zero **and** a point estimate larger than the noise floor for the first
time.

**Decision.** Nothing in code moves on these numbers — this addendum is the instrument, and what
it decides is which sentence the Orchestration tab and the two benches may quote: if the primary
comparison clears the floor, *at the same number of calls one agent that re-reads the documents
does better on tasks like these* is a measured claim rather than a direction; if it does not, the
sentence stays a direction and the corpus size stays in the caveat. A direction that reverses on
the new twenty is published as a reversal.

**Cost.** 450 trials on the 3B backbone, unpriced in the catalogue and under US$ 1 by the 8B run's
receipts; two to three hours of wall clock.

**What this cannot show.** Same shape, same filler, same needle grader: thirty tasks of one kind
are not thirty kinds of task. One weak backbone.
