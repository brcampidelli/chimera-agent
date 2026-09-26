# Pre-registration — the answer a route files as reasoning, seen through the product gateway

**Registered 2026-09-26, before any call.** Study 25, the interface finding of H11. Budget cap
**US$ 0.30**; worst case of the plan US$ 0.14 (below), plus one runaway if the route ignores
`max_tokens` (about US$ 0.10 each, as H11 priced its one).

## Why this exists

`bench/review_judge/RESULTS-h11.md` (S5): `openrouter/deepseek/deepseek-r1` pinned to Novita returned
`content` empty with `finish_reason: stop` on 353 of 814 calls of one prompt and 305 of 814 of
another, the answer filed under the reasoning field. `LLMGateway._normalize` read `content` only. The
fix on this branch (`fix/answer-filed-as-reasoning`) adds `CompletionResult.reasoning` and
`answer_in_reasoning`, a warning naming the provider, a reader for structured callers
(`answer_at_end_of_reasoning`: the object of the caller's schema that ENDS the reasoning), and the
hosted decision backend's use of it behind `CHIMERA_ANSWER_FROM_REASONING` (off).

The offline tests pin the fix against the wire shape H11 recorded, parsed by LiteLLM 1.99's own
OpenRouter code. What they cannot show is whether the live route still does it, on this backend's
prompt, in the field LiteLLM names — the interface is measured before the model is (PROTOCOL §4).

## Design

- **Model and route:** `openrouter/deepseek/deepseek-r1`, `extra_body={"provider": {"order":
  ["Novita"], "allow_fallbacks": false}}`, no fallback models. Reasoning at the model's default
  (`thinking` not passed), as H11 and as the hosted backend.
- **Request:** the hosted decision backend's, exactly — `HostedVerbalizedBackend.system_text(DANGER)`
  (the governance question), the state as the user message, temperature 0.3, `max_tokens` 2000.
  Through the product gateway, `complete` or `stream_complete`, with a 300 s request timeout.
- **Items:** `bench/governance_judge/corpus_ambiguous.py`, in its fixed order. The first **20** on
  the batch route, the first **6** of those again on the stream route. 26 calls, 6 in parallel.
- **What is recorded per call** (`probe.py`): the raw LiteLLM message's `reasoning_content` length,
  its `provider_specific_fields` keys and whether `provider_specific_fields["reasoning"]` equals it;
  per streamed chunk, whether the delta carried `reasoning_content`; the gateway's
  `answer_in_reasoning`, `finish_reason`, provider, reasoning length; for a flagged call, what the
  reader recovers, the reading it gives, and the last 400 characters of the reasoning for audit.
  Captured by wrapping `litellm.completion` inside the probe process; no product code changes.

## Metrics

- **M1 — the field.** On batch calls that carried reasoning: `reasoning_content` is a non-empty
  string, and the raw `reasoning` under `provider_specific_fields` equals it. On the stream: the
  deltas carry `reasoning_content`.
- **M2 — the rate.** Flagged calls over answered calls, per route, with a Wilson 95% interval.
- **M3 — recovery.** Of the flagged calls, how many the reader recovers, and whether each recovered
  object is the last thing in the stored reasoning tail (read by eye).
- **M4 — the other empties.** `content` empty and not flagged (a real empty, or `length`).

## Predictions

- **P1.** M1 holds on every batch call with reasoning and on every stream call.
- **P2.** At least one flagged call in 26. The rate on this prompt is not predicted to match H11's
  37–43%: the decision prompt is short and asks for one line, where H11's asked for a verdict on a
  diff. Any rate from 5% to 60% is consistent with the claim that the route does this.
- **P3.** The reader recovers ≥ 90% of the flagged calls, each the model's final object.
- **P4.** Some calls end on `length` at 2000 tokens (the module docstring of `decisions/hosted.py`
  saw reasoning spend budgets of 400–600); none of those is flagged.

## n, and what it can resolve

26 calls is a confirmation, not a rate estimate: at a true 40% the Wilson interval on 20 batch calls
is about ±20 pp. It can show that the route files answers as reasoning on this prompt, that the field
is where the gateway reads it, and that the reader takes the final object. It cannot separate 30%
from 50%.

## Decision rule

| outcome | what happens |
|---|---|
| M1 fails (the reasoning is somewhere else) | the gateway's reader is wrong: an **Amendment** here, the reader fixed and re-tested offline, before this branch is reported |
| M1 holds, ≥ 1 flagged, P3 holds | the fix is confirmed on the live route; the setting stays **off** (below) |
| M1 holds, 0 flagged | the route did not do it on this prompt today; reported as a null for this prompt, the offline tests stand on H11's recorded shape |
| a recovered object is not the model's final answer | the end-of-reasoning rule is wrong; reported, and the setting stays off |

**The setting's default is not decided here, whatever the outcome.** Turning
`CHIMERA_ANSWER_FROM_REASONING` on changes what a decision reads on about four calls in ten on such a
route; whether a recovered reading is the same instrument as a re-asked one needs a paired run on the
same items (choice agreement and Δp, against a replay floor), which this probe is not.

## Stop rule and budget

No new call starts once the running spend reaches **US$ 0.25**; the cap is **US$ 0.30**. Spend is
OpenRouter's billed `usage.cost` where the batch response carries it, else tokens × the listed price
(US$ 0.70/M prompt, US$ 2.50/M completion). Worst case of the plan: 26 × (600 × 0.70 + 2000 × 2.50)
/ 10⁶ = US$ 0.141. A timeout or provider error is a halt (PROTOCOL §2) and leaves the denominators.

## What this cannot show

- The rate on any other prompt, model, provider or day (one session, one fp8 provider).
- Whether the product default, `deepseek-v4-flash-0731` on DeepInfra, does the same. The agent
  loop's closing-call probe of 2026-09-25 (`chimera/core/agent.py`, `_EMPTY_CLOSE_NUDGE`) saw
  "stop, ~330 completion tokens, empty content" on it, which is this shape or a model that wrote
  nothing; that probe did not record the reasoning field.
- Anything about calibration: see the decision rule.

## Protocol items

§2 (halts leave the denominators), §3 (route pinned, fallbacks off; cache: temperature 0.3, so the
gateway's exact-match cache is not used), §4 (this is the interface measurement). No judge, no
comparison between arms, so §5's floor and McNemar do not apply.

## Amendment 1 — 2026-09-26, after the registered run, before any further call

**What the registered run showed** (its rows are kept in `results/`, unchanged, and are the run this
pre-registration decides on). M1 held: `reasoning_content` on 20/20 batch calls, the raw `reasoning`
under `provider_specific_fields` equal to it on 20/20, and `reasoning_content` deltas on 6/6 streams.
13 of 26 calls were flagged. **P3 failed:** the reader recovered 7 of the 13, each the model's final
object (in two of them a draft object with a different `p` stood just before the final one, and the
final was taken). The other six end in **prose** — a markdown explanation naming the verdict
("justifies human review", "**Not BLOCK:**") — with no object at the end. The run kept only the
last 400 characters of each reasoning, so it cannot say whether an object of the schema appears
EARLIER in those six, followed by the prose.

**Why that matters.** If it does, the end-of-reasoning rule is stricter than the content path on this
prompt (the content path reads the first object and the option word anywhere), and a lenient rule
would recover those calls; if it does not, the model wrote no JSON at all and the rule loses nothing.
It decides what the coordinator is told about the rule, not the setting (which stays off either way).

**The follow-up, fixed here.** The same 20 batch calls, same request, route and settings, once more
(`probe.py --amendment1 --prior-usd 0.06212`), keeping the **whole** reasoning of each flagged call
in `results/amendment1/rows.json`. For each flagged call: does the end rule recover it; is there an
object with both keys anywhere (H11's rule, `last_object_anywhere`); how many characters follow that
object; does the last option word after it restate the object's verdict; is there any `{` at all.
Read by eye as well: every flagged reasoning's last object and what follows it.

**Reading.** Reported as counts, no test. If most unrecovered flagged calls carry an earlier object
followed by prose that restates its verdict, the report says the end rule loses answers on this
prompt and names the lenient alternative (last object, only when the prose after it restates the same
option) as a candidate for its own measurement; if they carry no object, the end rule stands as is.

**Budget.** Spent: US$ 0.0621. This follow-up: 20 calls, worst case US$ 0.108, so ≤ US$ 0.171 in all;
the stop at US$ 0.25 and the cap at US$ 0.30 stand (the prior spend is counted by `--prior-usd`).
