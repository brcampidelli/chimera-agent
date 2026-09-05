# Where does the model start getting it wrong — with the machine held still

**Written before any run of this design. No outcome of it was seen first.**

[`RESULTS.md`](RESULTS.md) spent US$ 5.07 and published no knee. The reason was not sample size and
not sampling noise — temperature was 0.0 throughout and the prompt was identical to the token. The
same cell returned **70%, then 13%, then 100%** across forty calls, and instrumenting the response
showed why: three consecutive calls to one slug came back from `Wafer`, `Inceptron` and
`DigitalOcean`. OpenRouter routes per call, and the pool behind
`openrouter/deepseek/deepseek-v4-flash-0731` has **30 endpoints** whose context windows span 262,144
to 1,310,720 and whose prices span 8.8x.

So the earlier design was measuring a rotating population and calling it a model. This one holds the
machine still.

## The manipulation

`provider: {order: [NAME], allow_fallbacks: false}` in `extra_body`, already verified working in
[`../parallel_tools/run_backends.py`](../parallel_tools/run_backends.py): asking for `Baidu`
returned `Baidu`, asking for `Wafer` returned `Wafer`. `allow_fallbacks: false` is not optional — with
fallbacks on, an arm silently becomes whatever answered, which is the confound itself wearing the
manipulation's name.

Two facts about the pool decide the shape below, read from the endpoints API on 2026-09-05:

- **28 of the 30 endpoints serve at least 800k**, so the length where the earlier run broke is
  reachable on almost all of them. `Reka` and `CoreWeave` serve 262,144 and are therefore not
  pinnable at the long end — the router answers `No endpoints found` rather than truncating, which is
  a clean refusal and is reported as an absent cell, never as a zero.
- **Input price spans $0.050 to $0.440 per M.** At 792k tokens that is 4 cents against 35 cents *per
  call*. Volume goes on the cheap end; the expensive ones appear only where the question needs them.

## Phase A — the gate, and it is the point of this design

Nothing is swept until one pinned cell is shown to reproduce.

`Baidu` ($0.050/M, already characterised in the backends bench at 63.5% batching), at the length that
broke — target 786k, which arrives at ~792k by the provider's own count. **n = 20, twice, separated
by at least two hours.**

- **PINNING IS NOT ENOUGH — STOP, NO CURVE** if Fisher exact between the two batches gives p < 0.05.
  Then a knee cannot be measured this way either, that is the finding, and the run ends here having
  spent under two dollars.
- **PROCEED** if p >= 0.05.

**What this gate can and cannot detect, stated before it runs.** It is powered for the *magnitude*
that killed the earlier run — 10/10 against 2/15 is Fisher p < 0.001 — and it is not powered for
drift of a few points. A pass therefore means *"not that disease"*, not *"stable"*, and the results
document must say so in those words rather than upgrading it in the retelling.

## Phase B — the ladder, on the pinned backend

Lengths **4k, 16k, 64k, 200k, 400k, 600k, 792k, 950k**. Two are new: 600k, to put a point between
400k and the break, and 950k, because the earlier sweep stopped at the compaction trigger rather than
at the model's limit and so could not see past it. n = 20 per cell.

**Interleaved, not blocked by length.** The order of calls cycles through the lengths so that a drift
in backend load over the hour cannot land on one length and be read as that length's behaviour. This
is the same discipline that settled the code-path question in the earlier run, applied to the sweep
itself.

Both probes are kept, and the separation is the reason this bench exists:

- **RULE** — three standing instructions given once, at the top, then buried under the padding: a
  fixed header line, a name prefix, no `print`. Scored by regex on the returned file.
- **FACT** — a marker word to echo back. Retrieval.

The earlier run found RULE degrading while FACT stayed at 100%, which is precisely the case a
needle-in-a-haystack probe reports as healthy. Whatever happens to that observation now, the two are
scored and reported separately and never averaged into one number.

### Decision rule, fixed now

Let `r(L)` be the RULE rate at length `L`, and `r0` the rate at 4k.

- **A KNEE IS PUBLISHED AT L** if `r0 - r(L) >= 30` percentage points with Fisher exact p < 0.05
  against 4k, **and** every length below L is within 10 points of `r0`. The knee is the smallest such
  L.
- **NO KNEE BELOW 950k** if no L qualifies. That is a real result and gets written as one: it says
  the trigger cannot be set from this probe below the length tested, not that the model is fine.
- **NO CLAIM** for any cell with fewer than 15 usable rows after the gates below.

## Phase C — is the knee the model's, or the machine's

The knee length plus the two lengths bracketing it, repeated on three more pinned backends chosen
from the cheap end: `OpenInference` ($0.050), `Relace` ($0.065), `Sail Research` ($0.065). n = 20.

- **THE KNEE IS A PROPERTY OF THE BACKEND** if the knee position differs between backends, or if
  `r` at the knee length spreads at least 20 points across them.
- **THE KNEE IS A PROPERTY OF THE MODEL** if all four agree on position and spread under 20 points.

This phase is the one that decides whether the number is usable in the product at all. A compaction
trigger read off a single backend is a number about a machine the user may never be routed to.

## Gates before any aggregate is believed

- **Every row carries the backend that answered, and it must equal the one asked for.** A row where
  they differ is dropped, counted, and named — it means `allow_fallbacks` did not hold and the arm is
  contaminated.
- **Prompt length is the provider's own count**, never the padding estimate. A cell whose realised
  length drifts more than 2% from its target is reported at its realised length.
- **Errors are counted separately and never scored as failures.** A refusal, a rate-limit and a wrong
  answer are three different things, and folding them together is how a rate becomes meaningless.
- **The failing answers are read with human eyes** before any explanation is offered for them. The
  earlier run's "failures" were correct, complete functions that had forgotten the rules — which is a
  different phenomenon from broken output, and only reading them showed that.

## What this cannot show

- **One model, one probe, one task shape.** Three standing formatting rules under a long prompt. It
  does not measure reasoning, retrieval beyond one marker, or multi-turn behaviour.
- **Pinning fixes the name, not the machine.** A backend may itself front several hosts, change
  quantisation, or reconfigure between batches. If Phase A passes, that says those did not vary
  enough to matter over two hours — not that they cannot.
- **It cannot rehabilitate the earlier sweep.** Those rows have no backend and never will.
- **Prices, windows and pool membership were read on 2026-09-05** and are the router's, not a
  contract.
- **A knee found here is this backend's knee at this date.** Phase C is what says how far that
  generalises, and its answer may well be "not at all" — which is itself the useful result, because
  it would mean no single trigger is right for a slug.

## Cost

Phase A is about 40 calls x 792k x $0.050/M, roughly **$1.60**. Phase B is about 160 calls and 60.5M
tokens, roughly **$3.10**. Phase C is about 180 calls on cheap backends, roughly **$7.50**. Estimated
**under $13 total**, reported as measured — the earlier run pre-registered "under two dollars" and
spent $5.07, so this figure is written as an estimate that has been wrong before.

## Result

`bench/context_rot/RESULTS_pinned.md`, including the phase that stopped it if Phase A fails.

---

## Amendment: Phase C needs an anchor that does not depend on Phase B finding a knee

Written after Phase A batch 1 and before batch 2, the ladder, or any cross-backend call.

**What had been seen.** One batch: 20 calls pinned to `Baidu` at 792,019 tokens, **20/20 RULE and
20/20 FACT**, zero errors, zero rows where the answering backend differed from the one asked for,
US$ 0.79. Nothing about the outcome the Phase A rule decides — that rule compares two batches, and
the second does not exist yet. Nothing about the ladder, and nothing about any other backend.

**The gap.** Phase C above is defined as *"the knee length plus the two lengths bracketing it"*. If
Phase B returns NO KNEE BELOW 950k — a result the rules above explicitly allow — then Phase C has no
anchor and is undefined. That is the branch where the original question is still open, so it is
exactly the branch that must not be left without a plan.

**The fix, and the reason it is the right one.** Phase C runs at **792k regardless of what Phase B
finds**. That length is not arbitrary and is not chosen from this run's data: it is where
[`RESULTS.md`](RESULTS.md) recorded 70%, then 13%, then 100%, and that contradiction is the thing
this whole design exists to explain. A cell that needs explaining does not stop needing it because a
different backend behaved well.

**What that reframes.** `Baidu` being stable would be consistent with the earlier instability without
explaining it — the failing batches ran before the provider field existed, so the machine that served
them is unrecoverable, and it need not have been this one. Under this amendment Phase C stops being
only *"does the knee move between backends"* and becomes the sharper question: **is there a backend
that fails at 792k?** If one of the four comes back near 13% while `Baidu` is at 100%, that is a
direct account of the earlier contradiction, and a stronger result than a knee.

**What it still cannot do.** It cannot identify which backend served those batches — that is gone.
Finding a backend that fails at this length would make the earlier numbers *explicable*, not
*attributed*, and the results document must keep that distinction rather than let a plausible
mechanism harden into a claim about rows that carry no provider.

---

## Amendment 2: Phase C spends its budget on breadth, not depth

Written after Phase B and before any cross-backend call.

**What had been seen.** Phase A: 40/40 RULE and 40/40 FACT on `Baidu` at 792,019 tokens, two batches
two hours apart, Fisher p = 1.0 — the gate passes. Phase B: the eight-length ladder on `Baidu`,
**160/160 RULE and 160/160 FACT** from 4,587 to 953,392 tokens, zero errors, zero rows answered by a
backend other than the one asked for, US$ 3.04. By the pre-registered rule that is **NO KNEE BELOW
950k**. Nothing about any backend other than `Baidu` had been observed.

**The instrument was checked before this was written**, because a perfect result and a broken ruler
look identical. Today's scorer was fed a synthetic answer of the exact shape `RESULTS.md` describes
for the real failures — a correct, complete median function with no header line and no name prefix —
and it returns `rule=False` with `header` and `prefix` both false. Those are precisely the two flags,
and the only two, that the 13 real failures in `rows_replica.json` recorded. The instrument can
exhibit the effect, so 200/200 is a measurement rather than a silence.

**The change.** Phase C was written as three backends at n = 20. It runs instead as **eight backends
at n = 10**, at 792k, for the same money.

**Why, and it is a power argument rather than a preference.** The effect being hunted is enormous —
a backend near 13% against `Baidu` at 200/200. Fisher exact against 20/20 gives **p = 0.00040 at
n = 5** and **p < 0.00001 at n = 10**. Depth past ten buys no ability to see that effect. What the
budget can still buy is coverage: the pool has **28 endpoints serving this length**, and three of
them is 11% of it. Testing three deeply and finding nothing would license the sentence *"no backend
fails"* on a sample that could not have found one outside its three. That is the same error this
bench exists to stop making, one level up in the design.

Costed at 792,019 tokens per call against each endpoint's published input price: eight backends at
n = 10 is **US$ 5.11**, three at n = 20 is US$ 2.85, and the Phase C budget above was US$ 7.50.

**The eight, chosen to span the price range** rather than to be cheap, because price is the only
visible proxy for a different serving stack: `OpenInference` ($0.050), `Relace`, `Sail Research`,
`AkashML` ($0.065), `DigitalOcean`, `DeepInfra` ($0.080), `Wafer` ($0.100), `Together` ($0.140).
`Baidu` is not repeated — it has 200 calls already.

**Decision rule for Phase C, restated for this shape.** Over backends returning at least 8 usable
rows:

- **A BACKEND FAILS AT THIS LENGTH** if its RULE rate is below `Baidu`'s with Fisher p < 0.05. Each
  such backend is named with its rate, never pooled into an average.
- **THE POOL AGREES AT THIS LENGTH** if no backend qualifies. Written with the coverage attached —
  *eight of twenty-eight* — because the remaining twenty are unmeasured, not shown to be fine.
- **A backend that refuses, rate-limits or returns fewer than 8 usable rows is reported as absent**,
  with the reason, and never as a zero.

**What it still cannot do**, unchanged from Amendment 1: it cannot attribute the earlier batches to
any backend. Those rows carry no provider and never will.
