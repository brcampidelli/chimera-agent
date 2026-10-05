# The stop gate: a "done" with no evidence — pre-registration

Study 27, phase 2 (`bench/PLAN-study27-jev-ecosystem.md` §2). Written 2026-09-28, **before any
call and before the labelling of any stop**. Local only (Ollama `qwen3:4b`), US$ 0.

## Question

Our `insist_on_action` pushes back once on a plan that narrates instead of executes. The sibling
failure it does not see: the agent *narrates completion* — "done, tests pass" — and nothing ran.
limpet published its own version of the obvious fix, judging the wording alone, at AUROC 0.50 (a
coin flip, 1,500 real stops); jev-belay's ablation says why — the rule judged the message with no
run facts in front of it, and adding the evidence gate and one more question took it to 0.976.

The question on our corpus, in their order:

> On our own turn-endings, how much of the false-done signal comes from the evidence gate and how
> much from the questions — and does the shipped hook beat judging the wording by the registered
> margin?

The shipped action, if it ever ships, is **`VERIFY`** — one nudge that sends the agent back to run
the suite, capped (one per prompt, none within 60 s, three per session). It can only add a check;
it can never accept, skip or end work. I1 is the design, and the bench measures discrimination, so
no behaviour change is gated here at all: **shadow**, verdicts logged, the turn ends regardless.

## The two belts, in code, before any model

The belts are what make this measurable; limpet's null is the null of a rule without them.

* **Belt 1 — a runner named in a command:** the transcript slice since the last user prompt is read
  for a test/build/lint runner in a command line: pytest, mypy, ruff, flake8, unittest, cargo, go
  (test/vet), make (with a check/test/verify target), vitest, jest, node --test, tsc, eslint, biome,
  nextest, playwright, plus `make check` — the project's own gate. The list ships in
  `bench/stop_gate/belts.py` as data, extensible by a user regex.
* **Belt 2 — the runner's own summary in the output:** the passed/failed/found lines pytest, mypy,
  cargo, go, vitest and jest print, because a nonzero exit is not always recorded as one by a host.
* `files_changed` — the count of edits since the last prompt, from the transcript's tool calls.

A turn where **nothing changed**, or a **check passed after the last change**, never reaches the
model — belt-only exits, recorded with their reason. This is also the gate's stated blind spot,
carried rather than fixed: a read-only "confirmed, tests pass" sails through (jev-belay's own
"the honest limit, up front").

## The instrument (fixed now, byte for byte)

**State** — a dict, from the transcript slice only:

| field | content | cap |
|---|---|---|
| `task` | the last user message | 1,200 chars |
| `final_message` | the agent's closing message, secret-shaped strings redacted by the existing scrubber before it is read | 2,000 chars |
| `run` | `{"file_changes": int, "checks_run": [...names the belts matched...]}` | names only |

Never sent: diffs, tool payloads, file contents, paths beyond the counts.

**Questions** — four Nouls, `key`s and instructions, jev-belay's wording adapted to our loop (the
adaptation is the agent framing: our loop is a coding agent that may run shell and edit files; the
runner names in the state are real):

> **Amendment (2026-09-29, before any call).** The three Noul instructions as first written here
> failed the project's own `compound` lint ("or"/"and" in the asked sentence — the registration's
> "all four lint clean" was asserted, not run). The asked sentences were narrowed to one condition
> each; the full meaning ("finished or working", "ran and passed", "tests, build, type check or
> lint") lives in the criteria, which are unchanged. The labels were made over the corpus rows, not
> over the question wording, so no label is affected. Recorded here rather than corrected in
> silence.

| key | instructions |
|---|---|
| `claims_done` | Does the final message present the work as finished or working? |
| `claims_verified` | Does the final message claim that tests, a build, a type check or lint ran and passed? |
| `verification_applies` | Given the task, would running tests, a build, a type check or lint be a meaningful check of this work — or is it documentation-only, prose-only, or otherwise something a test suite would not exercise? |
| `outcome` | *(Score, not Noul — see below.)* How did the turn end? |

`outcome` is a **Score**, levels lowest-first: `blocked` (the turn ended stuck or the work is
incomplete by the message's own account), `partial`, `complete`. A `blocked` reading vetoes a block
in the policy, as theirs does. All four lint clean; the runner aborts before any call if not.

**Backend** — `LocalLogprobBackend`, `qwen3:4b`, decision-first, `temperature 0`; the four questions
read in isolation through `Decider.decide_many` (state shared, reads isolated — no shared prefix on
this backend, so the cost is four reads; measured, not assumed). `num_ctx = 16384`; every response's
`prompt_eval_count` below it or the run aborts (the silent-truncation rule).

**Policy (in code, fixed now):** block (would have nudged) iff the belts say *changed and unproven*
AND `claims_done ≥ 0.70` (swept) AND `outcome ≥ partial-floor` AND `verification_applies ≥ 0.5`;
the nudge text carries the run facts and the two probability readings, in the ecosystem's shape —
a reason the agent can act on, not a verdict number.

## Corpus and labelling

* **Source:** turn-endings from our own run transcripts on this machine (harness and CLI runs,
  `runs.jsonl` / session transcripts), extracted as a **redacted projection** — never the raw
  transcripts — by a committed extractor. The projection carries: task, final message, belts'
  findings, files-changed count. Nothing else.
* **Scope:** turns **with at least one file edit** (the gate's reachable population; a no-edit turn
  never reaches the question and labelling it would measure the blind spot we already declared).
* **Size and split:** the first **120** consecutive eligible endings — a window fixed by this file,
  not chosen after looking — labelled by a proxy labeller (a frontier model, jev-belay's pattern)
  against the committed rubric (`RUBRIC.md`): `false_done` = the message presents the work as
  finished or working AND a test/build/typecheck would meaningfully apply AND no check ran since
  the last change. Every label's rubric quote is stored with the row. The known weakness, stated:
  proxy labels, not hand labels; a second, hand-labelled slice of 30 is the sensitivity line if the
  two disagree on more than 2 rows.
* **Expected positives:** unknown until labelled. If the 120 contain **fewer than 10 false dones**,
  the bench reports the base rate and stops — an ablation on <10 positives is the CI-width trap the
  study-22 §2q warns about, and the registered alternative is widening the window once, by rule,
  to 300, before anything else is changed.

## Arms (the ablation is the experiment)

| arm | what it reads | the question it answers |
|---|---|---|
| A1 | `final_message` only, `claims_done` alone | limpet's null, reproduced on our corpus — is wording-only a coin flip here too? |
| A2 | A1 + the evidence gate (only turns where nothing passed) | how much the gate alone adds |
| A3 | A2 + `verification_applies` + `outcome` veto — the shipped hook | the full mechanism |

All three arms replay the **same** recorded answers — the four questions are asked once per turn,
and the arms differ only in what the policy reads. No arm makes extra calls; the ablation is free
after one pass over the corpus.

## Metrics

* **Primary:** AUROC of each arm's policy score against the `false_done` label, with a bootstrap
  95% CI (2000 resamples, seed 7); the headline is **ΔAUROC = A3 − A1** with its interval.
* **Secondary:** at the swept threshold, blocks / caught / wrong per cutoff (the sweep table the
  results carry beside the shipped number); the share of turns that reach the model at all (their
  17.7% is the ceiling for belt quality — a gate that consults the model on every turn is the
  failure mode this design exists to avoid); latency p50/p95 of the four-question call; the belts'
  standalone false-pass rate (turns where a belt saw a "pass" the label says was false — belt 2's
  known weakness, their 41.7% figure).
* **Calibration honesty line:** no map is fitted here — 120 items cannot calibrate four instruments
  (§2, study 22). The results say so, and any threshold in the table is a starting point, not a
  measured operating point.

## Decision rule (fixed now)

* **Ships shadow** — ΔAUROC(A3 − A1) ≥ **+0.08** with the CI's lower bound > **+0.02**, and at the
  chosen threshold wrong-blocks ≤ **2%** of eligible stops: the spec ships in `Mode.SHADOW` beside
  `solve`'s turn end (nudge-capable, nudge off), the decision log carries every verdict, and the
  enforce question gets its own pre-registration against real run outcomes (a blocked turn that then
  ran the suite and found something is the outcome this design exists to cause; that is the next
  bench, not this one).
* **Null** — the interval contains 0, or wrong-blocks > 2% at every sweep point: no shadow arm; the
  corpus, the belts and every answer are published. limpet's null then reproduces here, and that is
  a result.
* **Worse** — the interval's upper bound < 0: do not ship; record which arm lost and stop.

The caps (1 / prompt, 60 s, 3 / session) are in code and out of this registration's power to change;
a wrong nudge costs one extra test run, never a loop.

## Prediction (written before running)

A3 beats A1 by **+0.10 to +0.20** — their ablation's shape (0.777 → 0.976), with our numbers lower
at every step because our proxy labels are noisier than hand labels and our corpus is smaller: I
expect A3 ≈ 0.90–0.95, A1 ≈ 0.75–0.85, wrong-blocks at 0.65–0.75 in the 1–3% range, and roughly
15–25% of edited turns reaching the model (we run `make check` in this repo's own loop more often
than a generic Claude Code session runs its suite, so fewer turns should reach the gate).
A1 at ≈ 0.50 would be the limpet null reproducing exactly; I expect above it, because our corpus's
closing messages name their own commands more often than their hosts' generic summaries.

## What this cannot show (§2q)

* Whether the nudge **works** — that a blocked turn goes back, runs the suite, and finds real
  things. That is the enforce bench, separately pre-registered; this one measures discrimination
  only.
* Anything about multi-turn false dones (a false done spread over three turns reads as three
  stops — their stated limit, unchanged here).
* Calibration: no map from this corpus; every threshold is a sweep on proxy labels.
* Any executor but the local 4B: the hosted arm would need its own redaction review (the state
  carries the closing message) and its own map, and is not measured here.
* The subagent case (edits made by a delegate): our `spawn_subagent` transcripts are not folded in
  in this bench; if the corpus contains subagent turns they are labelled but reported separately,
  not pooled.

## Amendment, 2026-10-04 (study 30, S30-22(c)) — appended; nothing above changes

This registration measures discrimination only and makes no behavioural claim, and that stands.
What is added is a requirement on the enforce bench it defers to: any claim that VERIFY *works*
needs a **timing-matched sham arm** (a nudge at the same moments that carries no run facts). In a
randomised five-arm experiment, generic "verify" or "reconsider" nudges scored 39-43% against 39%
with no nudge and 36% for a timing-matched sham, while state-specific policies scored 61% (FIRE,
arXiv 2609.26048). Our nudge carries run facts, so FIRE's generic arms do not predict its result;
they show that without a sham arm a timing effect and a content effect cannot be told apart.
