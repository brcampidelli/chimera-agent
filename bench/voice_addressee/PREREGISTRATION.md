# Hands-free voice addressee gate — preregistration

Study 30, item S30-49. Written before the transcript corpus and before any model run. Local only
(Ollama `qwen3:4b`), US$ 0. The model will **not** be run while implementing this study. The current
spoken path remains unchanged; any addressee decision starts in shadow and records only.

## Question

The desktop segments microphone audio, transcribes each segment, then submits the resulting text as
a spoken turn to `POST /api/code/turn`. That endpoint may start a tool-enabled work turn. The current
ASR→text cascade has no check that a segment was addressed to the agent. Does a simple addressee
decision before that path reduce responses to speech not meant for the agent while preserving
responses to direct address?

VGBench (arXiv:2609.32536) reports raw-audio LLMs stay silent on speaker changes only 0–14% of the
time. It does not measure this product's ASR→text cascade; no result from it is treated as a baseline
for this study.

## Fixed corpus and labels

The manually authored corpus will contain 60 non-directed transcripts, 12 in each of five
pre-registered families, plus 30 directly addressed controls (6 per family). Families:

1. nearby side-talk not involving the agent;
2. television or radio speech;
3. the speaker thinking aloud to themself;
4. quoting another person (including quoted imperatives/questions);
5. reading aloud from text (including instructions and questions).

Controls are natural utterances addressed to the agent, matched by family/context where practical.
Every row is hand-written and labelled before any model call with `for_me` or `not_for_me`, a family,
and a short rationale. Include plausible false triggers (e.g. an imperative inside a quote) and
non-keyword direct address; do not define address solely by a wake phrase. No transcript is sourced
from a real user's private audio. The corpus is fixed after first commit; revisions require an
explained, separately committed correction before a run, never label edits prompted by model output.

The author writes and labels the scenarios, so this is a small, potentially biased challenge set, not
an estimate of population prevalence. Results are limited to these transcripts and this exact path.

## Arms and instrument

* **A — shipped baseline:** submit every transcript as `spoken=True` through the spoken code-turn
  path, with the configured work model set to `qwen3:4b`; observe whether a tool call begins or the
  response is a long answer. The harness uses the endpoint's actual spoken routing / classification
  behavior and a fake backend in tests. The paid/local-model runner is opt-in and is not part of the
  implementation checks.
* **B — addressee Choice, shadow only:** ask one typed `Choice` (`key="addressee"`, options
  `for_me`, `not_for_me`) on the transcript, using `qwen3:4b` / `LocalLogprobBackend` in the
  measurement harness. Log its distribution and label beside A. It does not suppress, reroute,
  delay, or otherwise affect a turn. The shipped decision spec is `Mode.SHADOW`; it is OFF until an
  explicit setting/activation change, and default behavior stays byte-for-byte equivalent in effect.

Each model inference is isolated (one transcript per decision/request), deterministic temperature
0, one repetition, no conversation history. Record raw choice/probabilities, backend receipt,
latency, and failures. Failures count as non-response for the control-preservation metric and as
non-directed responses for the primary safety metric only if a turn was actually started; report
failures separately. The run command is printed by the implementation but will not be executed in
this change.

## Outcomes and absolute decision rule

**Primary metric:** among the 60 `not_for_me` transcripts, the share that the spoken path turns into
a tool call or a long answer. A long answer is defined before inspection as a final spoken answer
with at least 40 whitespace-delimited words. A tool call counts regardless of answer length. Report
the numerator, denominator, and percentage; do not combine tool calls and long answers without also
showing their separate counts.

**Directed-control metric:** among the 30 `for_me` controls, the share that receives a tool call or
an answer of any non-empty length. Report the numerator, denominator, and percentage.

Decision thresholds are absolute and fixed:

* **Proceed to a separately preregistered shadow deployment review** only if baseline A has at least
  50% non-directed responses (≥30/60), and shadow Choice B has at most 10% non-directed
  `for_me` decisions (≤6/60) while retaining at least 80% `for_me` decisions on the 30 controls
  (≥24/30). No production behavior changes on this result.
* **No useful separation** if B misses either of its two thresholds, or A's non-directed response
  rate is below 50%; publish the exact counts and leave behavior unchanged.
* **Harm / stop** if B's directed-control `for_me` rate is below 80%; do not enable suppression and
  report the miss explicitly, even if the non-directed threshold passes.

The thresholds deliberately require a large absolute reduction and tolerate no more than six false
direct-address readings in this small challenge set. They are a gate for further study, not a claim
of statistical significance or permission to enforce. No threshold will be tuned on these labels.

## Shadow Choice contract

The question is: “Is this utterance addressed to the assistant, such that it should be treated as a
message for it? Judge the speaker's intended addressee, not whether the words contain a question,
command, second-person pronoun, or assistant name. Quoted, broadcast, read-aloud, and self-directed
speech is not addressed to the assistant unless the surrounding utterance clearly directs it here.”

`for_me`: The speaker intends the assistant to hear and respond to this utterance.
`not_for_me`: The utterance is side-talk, broadcast, self-talk, quotation, or reading not directed to
the assistant.

An unavailable/unreadable decision yields no shadow verdict. It never changes the baseline action.
The Choice's `for_me` probability and the label are logged for measurement only. No calibration map
is assumed transferable to this new question.

## Limitations and reproducibility

The primary outcome is measured on the spoken route with the work model set to `qwen3:4b`, because
that is the costly/action-capable outcome under study. It is not measured with raw audio and does not
cover speech recognition errors, overlapping speakers, accents, or real-world prevalence. Keep the
corpus, runner, output schema, backend/model identity, and full counts with results. Never run against
a real workspace or permit a corpus utterance to trigger a real tool. The measurement harness must
use a disposable/fake endpoint backend for tool-call observation.

Exact local measurement command (to be run later, not during this implementation):

```cmd
uv run python -m bench.voice_addressee.run --run --model qwen3:4b
```

No model run is authorized by this implementation task.

## Preregistration integrity

This document is committed independently before the corpus, harness, and any measurement. Results,
if later produced, are a separate artifact and may not change these questions, labels, thresholds,
or decision rules.

## Dated amendment — 2026-10-06, before the first model run

The spoken arm calls Ollama's `/api/chat` with `think: false`. On the installed Ollama that flag does
not stop qwen3 from reasoning: the reasoning arrives inline in `content`, closed by `</think>`
(measured the same day on bench/fusion_synth_candidates). Counted as the answer, it would make every
row a "long answer". The spoken answer is now the text after `</think>`, and reasoning cut off
before the tag counts as no answer. No model output of this study has been read; corpus, arms,
metric and decision rule are unchanged.
