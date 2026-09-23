# The Manager's P(approved), record-only — pre-registration

Study 22, phase 3, surface 4 (`bench/PLAN-study22-system-one.md` §5: "Manager P(approved) — record-only,
forever unless benched; 36% of claimed successes are false"). Written 2026-09-23, **before any call**.

## Question

When the worker says it is done, can a number say whether it really is — well enough to be worth
recording beside the Manager's verdict? And how well does the Manager's own word do on the same rows?
Record-only either way: the plan forbids this number from ever gating (I1: it could only accept work).

## Corpus (built, not generated — US$ 0)

`build_corpus.py` joins the harness factorial's 552 solves (#453, one executor, deepseek-v3.2) to their
run homes. The population is every solve whose run **claimed success**: **385**. The label is the
harness's own pre-registered pass threshold — **oracle outcome ≥ 0.8** — so 246 true, **139 false
(36%)**, matching the figure the plan cites. 23 tasks; **only 10 have both classes**.

What the corpus holds of each solve is what `runs.jsonl` kept: the task and the final answer, each cut
at 2 000 characters by the run log. The production Manager sees them whole. This is a limitation of the
record, stated here, not a design choice.

## Arms

* **P_local** — a Noul on the local `qwen3:4b` (`LocalLogprobBackend`, decision-first, `temperature 0`,
  `num_ctx 8192` with `prompt_eval_count` checked on every call). State: the Manager's own user message,
  `Task:\n{task}\n\nWorker's result:\n{answer}`. Question, `key = "accomplished"`:
  > You are a strict reviewer. Given a task and a worker's proposed result, judge the result. Does the
  > worker's result accomplish the task?

  criteria — `true`: "the result correctly and completely accomplishes the task"; `false`: "the result is
  wrong, incomplete, or only claims to have done the work". Lints clean — checked before any call.
* **M_word** — the production Manager (`chimera.core.supervisor.Manager`, its shipped system prompt and
  parser) on the same message, model `openrouter/deepseek/deepseek-v3.2` — the model that wrote the
  answers, which is what a deployment without a separate review role runs (`review_model_for(roles) or
  req.model`). Its verdict is a word, APPROVED or REVISE; an abstention is recorded as one. Hosted; the
  run stops if the metered spend passes **US$ 2**.

## Metrics

* **Primary:** AUROC of P_local, true vs false successes, all 385, with a **task-clustered** bootstrap
  (resample tasks, 2000, seed 7).
* **Primary guard, within task:** the mean AUROC over the 10 tasks that have both classes, with the same
  clustered interval. A number that only recognises which *tasks* are hard earns AUROC between tasks and
  none within them — and within a task is what a record beside one review would have to be about.
* M_word: approve rate on true successes and on false ones (TPR / FPR), with Wilson intervals, and its
  discrimination (TPR − FPR); abstentions counted.
* Secondary: P_local at the pre-registered cut `p ≥ 0.5` in the same TPR / FPR form; latency; spend.

## Decision rule (fixed now)

* **Record it** — P_local AUROC ≥ 0.70 with the clustered lower bound > 0.5, **and** within-task mean
  AUROC ≥ 0.60 with its lower bound > 0.5: ship a record-only spec (`Mode.SHADOW`, escalation `ANNOTATE`)
  that writes the number to the decision log beside the Manager's verdict, off by default. It never
  changes a verdict.
* Anything else — **null**: no product code. The Manager stays a word.

## Prediction (written before running)

Null for P_local: AUROC around 0.60, and within-task near 0.5 — the 4B has no way to check a code
change it cannot run, and the claim of success is written into every answer. For M_word: approves most
of both classes (FPR above 0.7) — the same model grading its own answer, the self-report the harness
already measured at 61% agreement.

## What this cannot show

Nothing about other executors (one model wrote every answer); nothing about the Manager reviewing a
diff or a verifier's output (it is shown prose here, as in production); nothing past 2 000 characters of
task or answer.
