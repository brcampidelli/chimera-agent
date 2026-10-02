# Useful context of the 1M-window models: glm-5.3-flash and glm-5.3 — pre-registration

**Written 2026-09-27, before any paid call of this design. No outcome of it has been seen. Not run
until the owner approves the budget below.**

**Budget decision, 2026-09-29 (the owner, in the session), recorded before any call:** `glm-5.3-flash`
only, to 900k, capped at **US$ 8**. `glm-5.3` is **not** approved and is not run; its column stays
here as the registration for whenever it is. Nothing in the design below changed after this
decision. The conservative rule for unmeasured models that "Why these two" says was being proposed
shipped as #684 (`AgentConfig.unmeasured_context_tokens`, 64k); what this run measures is what would
replace it for this model.

Owner-assigned (2026-09-27, task 5: "256k context and bigger models, without the tasks stalling").
The same bench, task, items, grader, gates and decision rule as [`PREREGISTRATION.md`](PREREGISTRATION.md)
and [`PREREGISTRATION_luna.md`](PREREGISTRATION_luna.md). Only what is listed here changes.

## Why these two

- **`z-ai/glm-5.3`** is the `top` rung of the default cost ladder and the escalation model without
  an OpenRouter key (`verified.escalation_model`).
- **`z-ai/glm-5.3-flash`** is a `mid` model with the same window at a tenth of the price, which
  makes measuring to 1M affordable.
- Both advertise 1,048,576 tokens, and both have no `useful_k`. Until they are measured, the
  context budget uses the conservative rule for unmeasured models, which is being proposed in the
  PR on limits.

Left out: `gpt-6-sol`, `gpt-5.5` and `claude-opus-5`, which bill US$ 2–5 per M, and double past
272k for the OpenAI models. That is a separate decision, with its own money.

## What changes

Selected with `run.py --profile glm53flash` and `--profile glm53`. The `v4flash` and `luna` profiles
render byte for byte as before: both committed reports reproduce identically, checked.

| | glm53flash | glm53 |
|---|---|---|
| route | `Sail Research`, fp8, no fallbacks | `Baidu`, fp8, no fallbacks |
| why the route | the cheapest fp8 endpoint serving 1,048,576 tokens on the listing read 2026-09-27 | the same |
| prices / M (in, cached, out) | 0.045 / 0.0285 / 0.60 | 0.3556 / 0.06604 / 1.1176 |
| temperature | 0.0, as the v4-flash run (both take one) | 0.0 |
| ladder | 4k … 256k, **512k, 900k** | 4k … 256k, **512k** |
| tier check | a row billed outside 0.8–1.35× the quote is mis-routed | the same |
| filler corpus | **`chimera/` + `tests/`** | the same |

**Why the corpus grows.** Past ~256k the `chimera/` sources run out, and the filler repeats
itself: 23 repeated tool results at 512k and 144 at 1M, measured at US$ 0. Internal repetition is a
confound of its own. With `tests/` added (1,030 files instead of 383), there is 1 repeat at 512k
and 2 at 900k out of ~890 units, with 0 corpus collisions and the grader self-test passing. The 900k
top rung leaves room under the 1,048,576 window for the output.

**Catalogue price note.** The catalogue lists glm-5.3 at 1.40/M. The live listing's cheapest route is
0.3556/M. The catalogue price is a separate fix, not part of this measurement.

## n and budget (the owner approves before any call)

- **Pilot per model:**
  - `P000–P019` at 4k, and `P000–P005` at the top rung.
  - Gate: ≥ 18/20 at 4k.
  - It recalibrates chars-per-token from the top-rung rows. This is the same rule as the luna run.
- **n rule:** the largest multiple of 18 under the approved budget, capped at 72. The projection is
  linear in prompt tokens from the pilot's cost.
- **Estimate** (≈ 1.92M tokens per item for glm53flash, ≈ 1.02M for glm53, plus the replay):

  | model | per item | n = 72 | n = 54 | proposed cap |
  |---|---:|---:|---:|---:|
  | glm53flash (to 900k) | ~US$ 0.087 | ~US$ 6.3 | ~US$ 4.7 | **US$ 8** |
  | glm53 (to 512k) | ~US$ 0.36 | ~US$ 26 | ~US$ 19.6 | **US$ 22** (n = 54) |

## What the answer changes (adopted in a separate PR)

`useful_k` for each model is the useful cell's median / 1000, labelled a lower bound if it is the
top rung. If a model's answer is **not resolved** or **below 16k**, nothing is written.

## Predictions

- **P1:** both 4k controls pass.
- **P2:** glm53flash holds to 256k, and drops outside the margin somewhere in 512k–900k.
  Confidence low. This is the first rung of this bench past 256k.
- **P3:** glm53 holds to 512k (a lower bound).

## Addendum — 2026-09-29, after the glm53flash pilot, before its main run

**Pilot (`results/glm53flash_pilot.json`):** 26 rows, **20/20 at 4k** (gate ≥ 18: PASS). At the 900k
top rung: 4 correct (P002–P005), 1 wrong (P001, `rule_forgotten`), 1 **timeout** (P000, 953 s, counted
as an error and never as a zero). Every row came from the registered route (`Sail Research`); no row
was flagged by the tier check. **US$ 0.1161.** As registered, the pilot's outcomes do not enter the
analysis.

**A first launch that produced nothing, recorded so it is not lost.** The first pilot ran with the
runner's default of 6 workers and was stopped by hand after ~10 minutes with no item finished and no
results file. The runner prints a line only when an item's calls all return, and each of P000–P005
holds a ~800k-token call, so the silence was the six concurrent large calls failing and retrying. The
error text was not captured, so the cause is a hypothesis: provider-side timeouts under concurrency.
That attempt's spend was **not recorded**. It is bounded by its US$ 1.50 cap, and the OpenRouter
dashboard is the place to read it. Two single-call probes made before the relaunch cost about
US$ 0.0227 (one 4k call, one 900k call), both answered correctly.

**Registered recalibrations and fixed parameters:**

- **chars per token = 4.35**, the median of `est_chars / prompt_tokens` over the five 900k rows that
  returned (4.342–4.368), rounded to 0.01. At the default 3.8 the 900k rung realised only ~786k
  provider tokens (12% under its label). At 4.35 it projects to ~900k, and the filler grows from ~754
  to ~865 units. A sample of the corpus at 4.35 finds 2 repeated calls in 864 for item M000, the same
  figure this document already reports for 900k; the full sample is published with the results.
- **n = 72.** The rule is the largest multiple of 18 under the approved budget, capped at 72.
  An item is the ladder plus the 4k replay, 9 calls and ~2.19M prompt tokens at 4.35. At the quoted
  input price (no cache credit, the ceiling) that is ~US$ 0.099 per item and **US$ 7.10** for 72. At
  the pilot's measured effective price (US$ 0.0291 per M prompt tokens, because 785,998 of 785,999
  tokens of each 900k call were served from cache) it is ~US$ 4.6. Both are under US$ 8.
- **Main-run cap: US$ 7.50**, so that pilot, probes and the unrecorded first attempt stay inside the
  approved US$ 8.
- **Workers: 3.** An execution parameter, not a design one: 6 workers produced the silent failure
  above, 2 workers ran the pilot with one timeout in 26 calls. Errors are counted per rung and
  published; the runner's stop rule (errors above 10% of rows after 30) is unchanged.

**A confound to carry into the reading, not to hide.** Almost the whole 900k prompt is served from
the provider's cache, and every row carries `cached_tokens`. Prefix caching can change a hosted
model's output relative to an uncached call (the route and cache go on the receipt since #484), so
the reading is of *this route with its cache behaviour*, which is how it will be used. If the errors
concentrate at the top rung, that is a result about the rung, and it is reported as one.

## Amendment — 2026-09-29, the main run stopped by hand after 3 items; the pilot's 900k reading retracted

**What happened.** The main run (n = 72, 3 workers, US$ 7.50 cap) was stopped by hand after 3 items
(27 rows, US$ 0.1366 recorded). Each of the three items answered at every rung from 4k to 512k and
**timed out at 900k** (953–955 s against the runner's 900 s timeout). At that rate the registered stop rule
(errors above 10% of rows after 30) would have ended the run after the fourth item; it was stopped one item
early, before more top-rung calls were sent, for the reason below.

**The cause, read from the rows.** Every 900k call that returned was a **cache hit**, and every one that was
not a cache hit timed out:

| run | item | seconds | prompt tokens | cached |
|---|---|---:|---:|---:|
| pilot | P000 | 953 | — | — (timeout) |
| pilot | P001–P005 | 293–377 | 783k–788k | all but one token |
| main | M000–M002 | 953–955 | — | — (timeout) |

The pilot's five fast 900k answers were served from a cache warmed by the **first pilot launch**, the one
stopped by hand with nothing recorded: it had already sent those exact prompts, and the provider finished
them after this side gave up. So an uncached 900k prompt on this route takes longer than 900 s to prefill,
and the pilot could not show it.

**Retracted:** the pilot's "4 correct, 1 wrong, 1 timeout at 900k in ~300 s" is a reading of a cache the
aborted launch warmed, not of the rung. It never entered the analysis (pilot outcomes do not), but it was
reported, so it is retracted here with the same prominence.

**Spend that is not in the files.** A call this side abandons at the timeout can still be processed, and
billed, by the provider; the runner records a cost of zero for it. That applies to the six calls of the
first pilot launch, to P000, and to M000–M002, each up to ~0.79M input tokens (≈ US$ 0.035 uncached, per
attempt). The recorded total (pilot US$ 0.1161, probes ≈ US$ 0.0227, main US$ 0.1366) is therefore a
**lower bound**; the provider's activity page is the only complete record.

**Before any relaunch, one of these, registered here first:**

1. **A timeout the top rung can meet** (e.g. 2,400 s for the 900k call only), with **no retry** at that rung,
   so a slow prefill is waited for once rather than sent up to three times.
2. **Drop the 900k rung** and keep 512k as the top (every rung up to it answered, in 174–207 s). That is a
   change to the ladder, so the reading would be a lower bound at 512k.

Neither is chosen here; the owner decides, because both change what the approved budget buys. The three
items already run stay in `results/glm53flash_main_stopped.json` and are not merged into a relaunch.

## Amendment — 2026-09-29, the owner chose option A; written before the relaunch sends a call

- **The top rung (900k) waits up to 2,400 s, once, and is never retried.** Every other rung keeps 900 s and
  two retries, exactly as before. Set in the `glm53flash` profile only (`TOP_TIMEOUT`); the `v4flash`, `luna`
  and `glm53` profiles are unchanged. The results file now carries `timeout`, `top_timeout` and `retries`, so
  the run's configuration is read from the artifact.
- **What does not change:** items (`M000–M071`), n = 72, chars per token 4.35, the grader, the gates, the
  decision rule, 3 workers, and the stop rule (errors above 10% of rows after 30).
- **Budget.** The approved total is US$ 8. Recorded so far: US$ 0.2754. Unrecorded, at most: ten abandoned
  top-rung calls (the six of the first pilot launch, P000, M000–M002) at ≈ US$ 0.036 each, so ≈ US$ 0.36. The
  relaunch's cap is **US$ 7.00**, so the whole study stays under US$ 7.64 even if every abandoned call was
  billed. Projection: ≈ US$ 0.047 per item for the rungs to 512k (measured on M000–M002) plus ≈ US$ 0.036 for an
  uncached 900k prefill, ≈ US$ 0.083 per item, **≈ US$ 6.0** for 72.
- **How this can still fail, stated before it does.** If an uncached 900k prefill outlasts 2,400 s too, every
  top-rung call is an error, 1 in 9 rows, and the stop rule ends the run at the fourth item. That reading is
  published as it is: this route does not serve a 900k prompt within 40 minutes. Four items answer nothing
  about the lower rungs either, so going on to option B (512k as the top) is then a new decision for the owner.
- The relaunch writes `results/glm53flash_main.json`; the stopped run's three items are not reused.

## Amendment — 2026-09-30, option A did not reach the cause; option C was probed and fails too

**The relaunch under option A** (`results/glm53flash_main.json`, 36 rows, US$ 0.3166 recorded) ran four items
and was ended by the registered stop rule (4/36 errored). Every rung from 4k to 512k answered in all four;
**every 900k call errored after 302–303 s**, not after the 2,400 s this side now waits.

**What that showed.** Our timeout was never the one that fired. A 4k call with `timeout=1` failed after 1 s
with *"Connection timed out after 1.0 seconds"*: the client honours what it is given. The 900k calls fail
with a different message, *"A Timeout Occurred"*, always at ~300 s. The cut is on the server side (the router
or the provider), and it also explains the earlier 953–955 s errors: three attempts of ~300 s each plus the
15 s and 30 s pauses between them. The pilot's five fast 900k answers were cache hits that began answering
inside those 300 s (see the amendment above). **The prediction written for option A** ("if an uncached 900k
prefill outlasts 2,400 s…") named the wrong mechanism, and is corrected here rather than left standing.

**Option C, probed once** (owner's choice, 2026-09-29; `results/glm53flash_stream_probe.json`): the same
uncached 900k prompt (pilot item P000 at 4.35 chars/token, never sent before), **streamed**. It failed after
302.9 s with **zero chunks** received, the router reporting `error_type: timeout`. So this route sends nothing
while it prefills, streamed or not, and an uncached 900k prompt cannot be served on it at all.

**Reading for the 900k rung: not measurable on this route.** It is not a finding about the model's useful
context; it is a finding about this endpoint's server-side limit (~300 s to the first byte).

**What is left, for the owner to decide:** option B, 512k as the top rung, reading the answer as a lower bound
at 512k. Every call to 512k answered in both main runs (174–207 s). Spend so far: pilot US$ 0.1161, probes
≈ US$ 0.0227 + the stream probe, main runs US$ 0.1366 + US$ 0.3166 recorded; the abandoned top-rung calls
(at most ~14 by now) are the part the files cannot show.

## Amendment — 2026-09-30, the owner chose option B; written before the relaunch sends a call

**A correction first, with the same prominence as the figure it corrects.** The amendment above says the
option-A run recorded **US$ 0.3166**. Its file, `results/glm53flash_main.json`, sums to **US$ 0.1811** over its
36 rows. US$ 0.3166 is what the runner *printed*. The difference is a defect of the runner:

- When the stop rule fires, the thread pool still waits for the items already running. Those calls are sent
  and paid for, and the runner's counter includes them, but their rows were dropped.
- In that run, the stop rule fired after the fourth item, with three items in flight (M003, M005 and M006).
  The gap, US$ 0.1355, is three times the measured cost of one item up to 512k (US$ 0.045). That is
  consistent with their lower rungs answering and their 900k calls failing.

**Fixed in `run.py` before this relaunch:**

- The rows of items that finish after the stop rule are written to `after_stop_rows`. They sit beside the
  analysed rows and are never among them.
- The final file carries `runner_usd`, the runner's own count.

Covered by `tests/test_the_useful_context_runner_keeps_what_it_spent.py`. Each of its three guards was
removed in turn, and a test failed each time. The earlier results files are not rewritten.

**The count of abandoned calls is also corrected.** The earlier amendments said "at most ~14". A 953 s timeout
is **three** attempts of ~300 s, not one, so the number of calls sent to the 900k rung and never answered is
larger. At most:

| where | calls |
|---|---:|
| first pilot launch | 18 |
| pilot P000 | 3 |
| stopped run M000–M002 | 9 |
| calls in flight when that run was killed | 3 |
| option-A run, errored | 4 |
| option-A run, items in flight | 3 |
| stream probe | 1 |
| **total** | **41** |

The first pilot launch is six prompts, up to three attempts each in its ~10 minutes. Uncached, each call is
≈ US$ 0.041 (900k tokens at US$ 0.045 per M).

**Spend so far:**

| | US$ |
|---|---:|
| recorded in the files (pilot 0.1161, stopped run 0.1366, option A 0.1811, probes ≈ 0.0227) | 0.4565 |
| counted by the runner but dropped from the option-A file | 0.1355 |
| worst case, if every abandoned call was billed (41 × 0.041) | ≈ 1.68 |
| **total, worst case** | **≈ 2.27** |

The provider's activity page remains the only complete record.

**What option B changes.** Profile `glm53flash512`; the `glm53flash` profile stays as it was, since it produced
the three files above.

- **Ladder: 4k … 256k, 512k.** The 900k rung is dropped, because this route cannot serve it (see above).
- **No top-rung timeout.** Every rung, 512k included, waits 900 s with two retries. That is the regime in which
  all 512k calls of both earlier runs answered, in 174–207 s. The server's ~300 s cut to the first byte leaves
  about 90 s of margin at 512k. A 512k call that meets it is an error, counted and published, never a zero.

**What does not change:**

- **Items: M000–M071, n = 72.** The same balanced set, so the 18 cells stay complete.
- **Rendering and grading:** chars per token 4.35; the grader, the gates and the decision rule.
- **Execution:** 3 workers; the stop rule (errors above 10% of rows after 30).

**Budget:**

- **Projection:** ≈ US$ 0.045 per item, measured on seven items across the two earlier runs, so ≈ US$ 3.3 for 72.
- **Cap for this run: US$ 4.50.** The whole study then stays under ≈ US$ 6.8, below the approved US$ 8, even in
  the worst case above.

**Items already sent.** Some of these prompts were sent before, and a prompt the provider still holds in cache
could change timing or even output (#484).

- **Which ones:** M000–M002 and M004 were sent at every rung up to 512k, in one or both earlier runs. M003, M005
  and M006 were in flight when the option-A run stopped, so they probably were too; their rows were not kept.
- **What the earlier rows show:** no cross-run reuse. Each item sent twice at 512k showed the same cached count
  both times, hours apart: M000 36,864 of 508,244; M001 12,288; M002 44,032. That is the shared system prompt and
  tool schemas, not the transcript.
- **Rule, fixed now:** if more than 5% of 512k rows have `cached_tokens` above half their `prompt_tokens`, the
  reading is published twice, with and without those rows.

**Predictions, written having seen outcomes.**

- **What has been seen:** seven answered 512k calls on four of these items (M000, M001, M002, M004), all
  correct, plus their lower rungs. Those rows are not merged into this run. They are said here because they
  inform the prediction below.
- **P2-B:** glm53flash holds within the margin at 512k, so `useful_k` is written as a **lower bound of 512**.
  Confidence moderate: seven correct calls on four items is not 72 items across 18 cells.
- **P1** is unchanged: the 4k control and replay pass.

**If it fails.** If the stop rule ends this run too, the file is published as it stands, with its
`after_stop_rows` and `runner_usd`, and the next step is again the owner's.

The relaunch writes `results/glm53flash512_main.json`.

## Result — 2026-09-30, the option-B run was ended by the stop rule; the cause is upstream rate limiting

`results/glm53flash512_main.json`: 40 analysed rows over five items, plus 24 `after_stop_rows` from the three
items in flight (M004, M006, M007). The spend is in the file for the first time: **US$ 0.1325** over the
analysed rows and **US$ 0.1701** counted by the runner (`runner_usd`). The difference is the in-flight items'
US$ 0.0376.

**Every error was the same one:** HTTP 429, *"z-ai/glm-5.3-flash is temporarily rate-limited upstream"*, after
three attempts each. That makes 14 of 64 calls, at every length from 64k to 512k, starting on the fourth item.
None was a timeout, and none was a wrong answer counted as an error. The first three items answered at every
rung. The stop rule fired at 7/40 errored rows, as registered. This is a finding about the route's capacity on
that morning, not about the model or the length.

**The cache rule was met.** Of the four 512k calls that answered (M000, M001, M002, and M006 after the stop),
three were served almost entirely from the provider's cache:

| item | cached tokens | of prompt tokens |
|---|---:|---:|
| M001 | 513,203 | 513,204 |
| M002 | 513,598 | 513,599 |
| M006 | 508,968 | 508,969 |

These are prompts sent in the earlier runs. Those runs showed 12,288 and 44,032 cached tokens for M001 and M002,
so the provider now keeps a 512k prefix for hours. More than 5% of 512k rows are cached above half, so any
reading of these items is published twice, with and without them, as registered.

**Seen, and not analysed** (the run did not reach n): every 512k call that answered was correct, including
M000, whose 512k call was not cached. Three rungs of M000 (16k, 64k, 128k) were `no_s…`.

The next step is the owner's, as written above.

## Amendment — 2026-09-30, the owner chose to relaunch option B pressing the route less; written before it sends a call

- **One worker instead of three** (`--workers 1`). Three concurrent calls of up to ~512k tokens each is where
  the 429s began, on the fourth item.
- **A call the route refuses for load waits 60 s, then 120 s**, before its two retries. The profile's
  `RATE_LIMIT_WAIT` applies to HTTP 429 only. Any other error keeps 15 s and 30 s, as does every other profile.
  The results file records it as `rate_limit_wait`. Covered by
  `tests/test_the_useful_context_runner_waits_out_a_busy_route.py`; removing each of its three guards made a
  test fail.
- **Nothing else changes.** Same profile `glm53flash512`, the same M000–M071, n = 72, chars per token 4.35, the
  same grader, gates and stop rule. The cache rule from the result above still applies. By now M000–M007 have
  all been sent before, and those are the prompts the provider may still hold.
- **Budget.**
  - Recorded so far: US$ 0.4565 in the files, plus US$ 0.1355 that the option-A runner counted and dropped,
    plus US$ 0.1701 for the stopped option-B run. That is **US$ 0.7621**.
  - The worst case for abandoned 900k calls is unchanged, ≈ US$ 1.68: the option-B run abandoned none, and a
    429 is refused before any work is done.
  - This run's cap is again **US$ 4.50**, so the study stays under ≈ US$ 6.95 in the worst case, below the
    approved US$ 8.
- **Time.** One item is about 7.5 minutes of calls in sequence, so ~9 hours for 72, plus any waits.
- **The stopped option-B file stays as it is.** It is not merged into this one. The relaunch writes
  `results/glm53flash512_slow.json`.
- **If the stop rule ends this one too,** the route cannot carry the run even at one call at a time. That
  reading is published as it is, and the next step is again the owner's.

## Amendment — 2026-09-30, the relaunch was stopped by hand; the owner chose another route (option 2)

**The one-call-at-a-time relaunch** (`results/glm53flash512_slow.json`) was stopped by hand at 09:50, during its
third item.

- **What it wrote:** 16 rows over two items, US$ 0.0371.
- **Errors:** four, every one the same upstream 429, now after waits of 60 s and 120 s.
- **Why it was stopped:** the stop rule was already certain. After the fourth item there would be at least 4
  errors in 32 rows, 12.5% against the 10% threshold, whatever items 3 and 4 answered. Letting it run would
  only have spent ~40 minutes and ~US$ 0.09 on a route the owner had set aside.
- **What stopping by hand costs:** the runner's final write, so the file has no `runner_usd`. The item in
  flight (M002) is not in it either, up to ≈ US$ 0.046 of unrecorded spend.

**Reading for this route: Sail Research cannot carry this run on 2026-09-30**, at three calls or at one. This is
a finding about that endpoint's capacity, not about the model.

**The owner's choice: measure the same model on another route.** The route is chosen by the rule this study
already registered — the cheapest fp8 endpoint serving 1,048,576 tokens — with Sail Research excluded:

| | |
|---|---|
| listing | read 2026-09-30, 13:49 UTC, 33 endpoints |
| route | **Novita**, fp8, context 1,048,576 |
| prices / M (in, cached, out) | 0.084 / 0.0168 / 0.28 |
| uptime, last 30 min | 98.6% |
| temperature, tools | both supported |
| profile | `glm53flash512_novita`: the `glm53flash512` profile with this provider and these prices, including the 429 wait |

**Why still 512k, not 900k.** The owner's last choice about the ladder was option B, 512k as the top. Novita
serves the full window, but going back to 900k roughly doubles the tokens per item and does not fit the budget
at a useful n. That would be a separate decision.

**A different endpoint is a different measurement.** Another host's fp8 build of the same weights may read
differently (see "What this cannot show"). So this route runs **the registered pilot first**, as every model in
this study did, and its outcomes do not enter the analysis:

- **Items:** `P000–P019` at 4k, and `P000–P005` at 512k. Gate: **≥ 18/20 at 4k**.
- **Chars per token:** recalibrated from the 512k rows with the same rule. The tokenizer is the model's, so ≈ 4.35 is
  expected.
- **Execution:** 2 workers, pilot cap **US$ 0.40** (projected ≈ US$ 0.27).
- **What it tells:** whether this route serves 512k at all. If it cannot, the study stops there.
- **Cache:** the provider cache is per route, and Novita has never seen these prompts, so no row can be a
  cross-run cache hit.

**The main run follows only if the pilot passes.** Before it sends a call, a short addendum fixes three things:

- **n:** by the registered rule, the largest multiple of 18 under the budget, capped at 72, projected from the
  pilot's measured cost.
- **Its cap.**
- **Its worker count:** 2, or 1 if the pilot met any 429.

**Budget.**

| | US$ |
|---|---:|
| recorded in the files: 0.4565 + 0.1355 (option A, dropped) + 0.1701 (option B) + 0.0371 (relaunch) | **0.7992** |
| unrecorded: the relaunch's in-flight item | ≤ 0.046 |
| unrecorded: abandoned 900k calls, worst case | ≈ 1.68 |
| **worst case so far** | **≈ 2.53** |
| pilot cap | 0.40 |
| **left for the main run under the approved US$ 8** | **≈ 5.07** |

At ≈ US$ 0.087 per item on this route (≈ 1.02M prompt tokens at 0.084/M, uncached, plus output), the rule gives
**n = 54 (≈ US$ 4.7)**, not 72. Reaching 72 would need about US$ 1.6 more, which only the owner can approve.

- The pilot writes `results/glm53flash512_novita_pilot.json`.
- Every earlier file stays as it is and is not merged.
- The predictions above (P1, P2-B) stand for this route unchanged.

**Addendum, 09:59 — the pilot's first launch was stopped after 13 seconds, before any outcome.**

- **What was wrong:** it was started without `--cpt 4.35`, so it rendered at the runner's default of 3.8 chars
  per token. Its "512k" rung would have been ≈ 447k provider tokens, and the pilot exists to show whether this
  route serves 512k.
- **What it cost:** nothing was printed or written. Up to two calls may have been in flight, ≈ US$ 0.075 of
  unrecorded spend at worst. The worst case so far becomes ≈ US$ 2.61, which leaves ≈ US$ 4.99 after the
  pilot's cap. The n rule still gives 54.
- **What changes:** the pilot is relaunched with `--cpt 4.35`, the value already registered for this model and
  tokenizer, so every rung is the size its label says. The recalibration rule stays: `est_chars /
  prompt_tokens` is still measured on the 512k rows, and the main run uses whatever it gives.

## Addendum — 2026-09-30, after the Novita pilot, before its main run

**Pilot (`results/glm53flash512_novita_pilot.json`):** 26 rows, **US$ 0.2217**, and `runner_usd` agrees.

- **Gate:** 20/20 at 4k (≥ 18: **PASS**).
- **The route served 512k.** Five calls read 510,861–512,948 prompt tokens and answered in 23–53 s, against
  ~200 s on Sail Research. None was cached, none was refused with a 429, and each was billed at exactly the
  quoted price (ratio 1.00).
- **Outcomes,** as registered, do not enter the analysis: 4 correct at 512k, 1 `rule_forgotten`.

**One apparatus defect, found in the pilot and fixed before the main run.** P005's 512k call came back after
23.6 s with `finish_reason` "stop" but nothing inside it:

| field | value |
|---|---|
| content | none |
| prompt tokens | **0** |
| completion tokens | 0 |
| cost | none |

The grader scored it `empty`, a wrong answer. A route failure would have counted as the model forgetting, which
is exactly what this study's rule forbids: a failed call is an error, never a zero.

- **The fix in `run.py`:** a reply with 0 prompt tokens now raises `EmptyResponse`, so it is retried like any
  failed call, and recorded as an error if it persists. It is never graded.
- **Tests:** covered by `tests/test_the_useful_context_runner_does_not_grade_an_unread_prompt.py`. Removing each
  of its two guards made a test fail.
- **No earlier result is affected.** No row of any earlier results file, published or stopped, has this shape
  (all ten scanned). The pilot's row stays as recorded, since pilot outcomes are not analysed.

**Fixed for the main run:**

- **Chars per token: 4.36.** The median of `est_chars / prompt_tokens` over the five 512k rows that read a
  prompt was 4.342–4.361, rounded to 0.01.
- **n = 54,** by the registered rule. One 512k call cost US$ 0.043 on this route. An item (the ladder plus the
  4k replay, ~1.02M prompt tokens) is ≈ US$ 0.086. The worst case spent so far is ≈ US$ 2.61, plus this pilot's
  US$ 0.2217, so ≈ US$ 2.83. That leaves ≈ US$ 5.17 under the approved US$ 8, which buys 60 items at most; the
  largest multiple of 18 is 54. Projection: **≈ US$ 4.64**.
- **Cap: US$ 5.00,** so the study stays under ≈ US$ 7.83 even in the worst case for every unrecorded call.
- **Workers: 2.** The pilot met no 429, and the rule written for this route says 2 in that case.
- **Items:** M000–M053, profile `glm53flash512_novita`. The stop rule is unchanged.
- **Output:** `results/glm53flash512_novita_main.json`.
- **Predictions:** P1 and P2-B stand unchanged.

## What this cannot show

- Other task shapes; the limits of `PREREGISTRATION.md` apply.
- Other routes of the same model. A different endpoint and quantization may read differently.
