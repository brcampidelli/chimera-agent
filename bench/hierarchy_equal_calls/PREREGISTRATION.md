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

**Instrument check, first:** the single-call arm must land between 20% and 85% pass@1 over
10 tasks × 3 runs. Above 85% the ceiling is back and the run is void (reported, not scored); below
20% the model cannot do the task at all and the comparison is between two failures.

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
