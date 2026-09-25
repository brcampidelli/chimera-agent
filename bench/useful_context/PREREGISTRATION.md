# At what length does the product default stop holding an agent's context? — pre-registration

**Written 2026-09-25, before any paid call of this design. No outcome of it has been seen.**

Study 25, prerequisite for arm H8 (compaction). The number this produces is the one the
`CHANGELOG.md` entry *"Compaction has never fired"* names as missing: *at what context length this
model starts getting the task wrong.*

## Why this exists

Compaction has fired in **0 of 137** traced runs (`CHANGELOG.md`; `bench/compaction/PREREGISTRATION.md`,
both amendments). The cause is the shape of the trigger, in `chimera/core/context_budget.py`:

- `threshold = window × fraction × 0.8`, where `window` is the model's **advertised** window. For
  `deepseek-v4-flash-0731` the catalogue says `context_k=1048` today (it said 1,310 until #346). The
  Code screen sends `fraction=0.6`, so it compacts at **1,048,000 × 0.6 × 0.8 = 503,040** tokens.
- `chimera solve` sends `context_budget=None`: no compaction at all.
- The largest context ever observed in a trace is **64,067** tokens; the median peak is 6,826.

A trigger set from the advertised maximum is about the marketing. This bench measures the model.

## What the pinned endpoint serves (read before designing)

OpenRouter endpoints listing for `deepseek/deepseek-v4-flash-0731`, fetched 2026-09-25 (public, no key):

| endpoint | context | max output | quant | $/M in | $/M cached | $/M out |
|---|---:|---:|---|---:|---:|---:|
| **DeepInfra** | **1,048,576** | 384,000 | fp8 | 0.060 | 0.015 | 0.180 |

The listing has 31 endpoint rows from 29 providers: 28 serve 1,000,000–1,048,576, two (`Reka`,
`CoreWeave`) serve 262,144, and **only one (`Cloudflare`) serves the 1,310,720 the catalogue used to
advertise**.
So the pinned endpoint does not constrain this ladder (it stops at 128k, below); it does say that
"1.31M" was never a property of the model on 29 of 30 routes.

## What is already known, and what this adds

`bench/context_rot/RESULTS_pinned.md` (2026-09-05): on a pinned backend the model held three
formatting rules and echoed one marker **200/200 from 4,587 to 953,392 tokens**, and `DeepInfra`
scored 10/10 at 792k. That probe was deliberately simple — rules stated once, a marker echoed
**verbatim** (a literal match), padding as alternating "For reference, here is <path>" / "Noted" turns.

The literature says the easy probe is the wrong one to size a window from:

- **RULER** (arXiv 2404.06654, grade C): near-perfect vanilla needle-in-a-haystack, yet almost all 17
  models fall below their 4k-derived threshold before their claimed length.
- **NoLiMa** (arXiv 2502.05167, grade C): with minimal lexical overlap between needle and question
  (a one-hop association), 11 of 13 models fall below 50% of their short-context score at 32k.
- **"Context length alone hurts"** (arXiv 2510.05381, grade C) and Chroma's *Context Rot* report
  (grade D): degradation with length even when retrieval is perfect.

This design adds the three things `context_rot` left out: an **agent-shaped** transcript (tool
calls with real tool results), a needle reachable only by **association** (no literal match), with
**distractors of the same form**, and an answer that needs the association **and** a rule stated at
the start. Papers are data here, not verdicts; their numbers are not this model's.

## The item

One item is one conversation, rendered at every length. Exact texts are frozen in `items.py`
(`TASK`, `RULE_LEAD`, `RULES`, `OTHER_RULES`, `WHERE`, `PROGRESS`, `QUESTION`); the shape:

1. **System**: the product's `DEFAULT_SYSTEM_PROMPT`, verbatim. **Tools**: the product's own schemas
   for `read_file`, `grep`, `list_dir`, `glob` (the read-only four; the full registry would put
   ~20 schemas in a prompt that must fit in 4k).
2. **Opening user message**: an audit task, then *"Rules for the whole session:"* and three rules.
   One is the **target rule**, a naming format for "our services", from six:
   `SCREAMING_SNAKE_CASE`, `svc:` prefix, `@ops` suffix, `camelCase`, `[brackets]`, `dots.for.hyphens`.
   Each is stated with an example on a name that is never an answer (`night-auditor`). The target
   rule's position in the list (1st/2nd/3rd) varies. The other two rules are ordinary session rules
   and are never graded. The rule sentence, verbatim for the snake variant:

   > Every time you write the name of one of our services -- in prose, in a list, or as a one-word
   > answer -- write it in SCREAMING_SNAKE_CASE: night-auditor becomes NIGHT_AUDITOR.

3. **Filler**: assistant tool calls and their results, produced by running the product's own
   `ReadFileTool` and `GrepTool` on this repository's `chimera/**/*.py` (351 files over 1.5 kB),
   in an order drawn from the item's seed: full reads of small files, windowed reads
   (`start_line`/`max_lines`) and greps for identifiers. At least 8 units at 4k, ~100 at 128k.
   The same item reads the same files in the same order at every length, just further.
4. **Five runbooks**, each a `read_file ops/runbooks/<service>.md` call whose result says where one
   invented service's machines are, **by a landmark**:

   > Where it lives: the hardware sits in a facility within sight of the Brandenburg Gate.

   They sit at **10/30/50/70/90% of the filler** (by characters). The **target** runbook is at
   **10%, 50% or 90%** (balanced); the other four slots hold distractors — other services, other
   landmarks, other countries. Chance of naming the right service by guessing among five: 20%.
5. **Tail**: an assistant progress message, then the question:

   > Quick question before you carry on: which of our services is hosted in Germany? Answer from
   > what you have already read, without calling any tool, and reply with the service name only.

   The question shares no word with the needle ("host", "run", "service" and every country are
   absent from the needle sentences by construction). Answering needs **the association**
   (Brandenburg Gate → Germany), **the retrieval** (which runbook said it), and **the rule** from the
   opening message (write it `MEADOW_FLARE`, not `meadow-flare`).

**Independence from the filler.** Service names (in kebab, snake, camel and dotted forms) and
landmarks are checked against every filler file before any call (`run.py --check`: **0 collisions**).
The filler cannot answer the question.

**Balance.** Items cycle through the 18 cells *rule (6) × target depth (3)*; the rule's list
position cycles within them. Names, landmarks, the two ordinary rules and the filler order are drawn
per item from a seed derived from its id. Pilot items (`P000…`) and main items (`M000…`) are different
items.

## The ladder

**4k (control), 16k, 32k, 64k, 128k** — targets in provider-counted prompt tokens (system and tool
schemas included), assembled by a chars-per-token estimate and **reported at the provider's count**.

The brief suggested 256k as well. It is left out **for n**: at DeepInfra's $0.06/M a 256k cell costs
~$0.015 per item, so 256k at the n the rule needs (≥ 54, table below) is ~$0.85 alone, and the rest of
the ladder would then run at an n where one stray failure decides the answer. Production has never
exceeded 64,067 tokens, so resolution at 16k–64k is worth more to the trigger than a point at 256k.
**Above 128k this bench measures nothing**; `context_rot` measured a simpler probe flat to 953k.

## The call

- `openrouter/deepseek/deepseek-v4-flash-0731`, `provider: {order: ["DeepInfra"], allow_fallbacks: false}`.
  Every row records the provider that answered; a row from any other is dropped and counted.
- `temperature=0.0`, `max_tokens=8000`, the model's default reasoning (the product sends none either
  way for agent turns), tools as above, `tool_choice` unset (as the product sends).
- **One call per (item, length)**, no agent loop: the loop's own retries and tool calls are variance
  that has nothing to do with length. The transcript is agent-shaped; the call that answers is one
  completion.
- Each item's calls run in an order drawn from its seed (no length always first); items run in
  parallel (6 workers). A call that raises is retried twice (15 s, 30 s); if it still fails it is a
  **halt**: it leaves the denominators and the pairing for that length (PROTOCOL §2), never a zero.

## Grading (deterministic, `items.grade`; self-test passes on 74 synthetic answers before any call)

- **ok** (the primary outcome): the answer contains the expected string exactly (case-sensitive,
  token-bounded — `MEADOW_FLARE`, `svc:meadow-flare`, …) **and names no other of the five services**.
- Otherwise one kind, so the drop can be read by cause:
  - **format breakage** — `tool_call` (answered with a tool call), `truncated` (hit `max_tokens`),
    `empty`, `no_service` (answered without naming any of the five: refusal, off-task, continuation);
  - **forgetting** — `rule_forgotten` (right service, wrong format), `wrong_service` (one distractor),
    `several` (hedged between services).
- Also recorded: `fact_ok` (right service in any format) and `rule_ok` (the named service written in
  the rule's format), so rule and retrieval are read separately, never averaged.

**Primary** counts every failure. The breakage/forgetting split is reported beside it per length, plus
a secondary *forgetting-only* accuracy that drops breakage rows — a model that stops answering at 128k
is not useful there either, so the primary does not excuse breakage, but the report must say which one
it was.

## Floors and gates (before any aggregate is believed)

1. **Replay floor.** Every main item's 4k request is sent **twice** (byte-identical). The 4k-vs-replay
   discordance is the drop the rule would see with no change in length. **FLOOR GATE:** if the Newcombe
   lower bound of (replay − 4k) is below −10 pp, the rule cannot pass a null at this n and the useful
   length is reported as **not resolved**, not as 4k.
2. **Positive control.** Main-run 4k accuracy must be **≥ 90%**. If not, the task is too hard and
   **no length is read**; an amendment fixes the task and the main run is repeated on new items.
3. **Pilot gate.** ≥ 18/20 at 4k in the pilot, or the task is amended before the main run.
4. **Routing.** More than 5% mis-routed rows voids the run.
5. **Raw outputs read by eye**: at least 5 per length, plus every failure, before any explanation.
6. **Grader self-test** passes and **corpus collisions = 0** (`--check`), before any call.

## Decision rule (fixed now)

For each length L > 4k, over the items valid at both L and 4k: the paired difference
`Δ_L = acc(L) − acc(4k)`, its **Newcombe method-10 95% interval** (Wilson marginals, φ with the
`AD − BC − n/2` correction), and the **exact McNemar** p.

- **L is within margin** if the interval's lower bound is **≥ −10 pp**.
- **Useful length** = the largest L such that **every tested length ≤ L** is within margin (a fixed
  sequence: 16k, then 32k, … — a length after the first failure is not read, so there is no
  multiplicity to correct). It is reported as that cell's **median provider-counted prompt tokens**.
- If 16k already fails: useful length **< 16k**; the trigger rule would give ~3k, below the size of
  the product's own opening prompt, so **no trigger is proposed** and the finding is that compaction
  cannot rescue this task shape.
- If every length passes: useful length **≥ the 128k cell** — a lower bound, reported as one.

**Proposed trigger** = `floor(0.8 × useful length, to 1,000)` provider prompt tokens, so the
compaction lands before the drop and leaves room for the summary. For the coordinator it is also
expressed as the `context_budget` fraction that yields it: `useful / window` (since the code's
threshold is `window × fraction × 0.8`).

### Precomputed: what the rule does at the candidate n

Lower bound of Δ, by the outcome table (computed with `run.newcombe_paired` before any call):

| n | 4k perfect, L loses 0 / 1 / 2 / 3 / 4 | no true effect, noise b = c = 3 / 5 / 6 | noise + net loss (b=2, c=4) |
|---:|---|---|---|
| 54 | −6.6 / −9.8 / **−12.5** / −15.1 / −17.6 | **−10.9** / −12.6 / −13.2 | **−14.7** |
| 72 | −5.1 / −7.5 / −9.6 / **−11.5** / −13.4 | −8.3 / −9.8 / **−10.3** | **−11.3** |
| 90 | −4.1 / −6.0 / −7.7 / −9.3 / **−10.9** | −6.8 / −7.9 / −8.4 | −9.2 |

(bold = outside the margin). Read plainly: at n = 72 a length is declared useful when it loses at most
two items net against 4k with little noise, and a truly flat length still passes with up to ~10
discordant pairs of noise. The rule is strict by design: an unresolved length counts as not useful,
which errs toward compacting early — the cheap side, since a compaction costs tokens and a lost rule
costs a wrong run.

## n

Plan §8's table gives ≈160 paired items for a 10 pp effect at p_d = 0.20; at ~248k tokens per item
(the ladder plus the replay) that is ~US$ 2.40 and over the budget. So n is set by the budget, **after
the pilot measures the cost per call**, by this rule written now:

> n = the largest multiple of 18 whose projected main-run cost is ≤ US$ 1.50 − pilot spend − US$ 0.10
> reserve, capped at 162. The projection uses the pilot's measured cost per call at 4k and at 128k,
> linear in prompt tokens between them.

The expected answer is **n = 72**. It is written into an addendum below, with the pilot's cost, before
the main run. The chars-per-token used to assemble renders is recalibrated once from the pilot: the
median of `est_chars / prompt_tokens` over the pilot's 128k rows, rounded to 0.01, also in the addendum.

## Pilot

Items `P000–P019` at 4k, and `P000–P005` at 128k: 26 calls, cap US$ 0.10. It checks, and only checks:
that DeepInfra accepts the tool-shaped transcript and answers in `content` (PROTOCOL §4 preflight);
the 4k gate; the cost per call; the realised token counts; reasoning length against `max_tokens`; and
whether answers come back as tool calls. **Its outcomes do not enter the main analysis** and do not
move the rule, the ladder or the margin. If it shows a design defect, an amendment is written and
committed before the main run.

## Predictions (written so they can be wrong)

- **P1.** The 4k control passes (≥ 90%).
- **P2.** No length up to 64k falls outside the margin (the product's observed range is safe).
- **P3.** The useful length is the top of the ladder (≥ 128k), i.e. `context_rot`'s flatness survives
  a harder, association-based, agent-shaped probe. Confidence moderate: NoLiMa predicts the opposite
  by 32k for most models, and this probe is built in its image.
- **P4.** Where failures happen, `wrong_service` (the association/retrieval) outnumbers
  `rule_forgotten`, and the middle depth (50%) fails more than 10% or 90%.

## Two-sided outcomes, reported per length

Accuracy (Wilson 95%), `fact_ok`, `rule_ok`, breakage and forgetting counts by kind, median
provider-counted prompt tokens, cost, median reasoning tokens, median latency, cache-read share,
answers given as tool calls (over-calls), refusals.

## Stop rule and budget

- **Budget: US$ 1.50 in total**, pilot included; enforced in code per call (a call that would pass the
  invocation's cap is not sent and is recorded as a halt).
- Stop if more than 10% of calls error after 30 calls.
- Halts are listed by item and length in the results, never scored.

## PROTOCOL.md, rule by rule

- **§1 wall**: no tool executes; the model sees only the rendered request and cannot reach the grader
  or the answer key. Nothing to probe.
- **§2 halts**: excluded from denominators and pairing, listed.
- **§3 cache and route**: route pinned; cache state not controlled (a hosted route cannot be made
  cold), so **cache-read tokens are recorded per row and reported per length**. Items share nothing
  after the second message, so only the system prefix and the replay can hit.
- **§4 interface**: the pilot is the preflight; a tool-call answer is detected and counted as its own
  kind, never read as an empty answer.
- **§5 judge**: none; deterministic grader with a self-test.
- **§6 placebo**: nothing is added to a prompt as an intervention; the manipulated variable is filler
  length.
- **§7 split**: nothing is fitted.
- **§8 replicas**: k = 1 per (item, length), n spread over items; the 4k replay measures the floor.
- **§9 binarised view**: the outcome is binary natively.
- **§2aa known number**: no published number exists for this probe; the ≥ 90% positive control and the
  replay floor play that role.

## What this cannot show

- **One model, one endpoint, one day.** Nothing transfers to another family, or to another of the 30
  endpoints behind this slug (`context_rot` found two of eight behaving differently).
- **One task shape.** A lookup that needs one association, one retrieval and one naming rule. Not
  multi-step reasoning, not code editing, not aggregation, not a rule introduced mid-conversation.
- **Depth matters and is only sampled.** The target sits at 10/50/90% of the filler; the useful
  length of a needle at 30% or 70%, or of one fact 2k tokens from the question versus 100k, is read
  only descriptively (the per-depth table), not decided.
- **Nothing above 128k.**
- **A single completion, not a loop.** In production the agent could re-read the runbook with a tool;
  here it is told not to. That makes this a measure of what the context holds, which is what a
  compaction trigger needs, not of what the agent would eventually get right.
- **Reduced tool set and T = 0.** Production sends ~20 tool schemas and temperature 0.2.
- **The trigger is derived by a stated rule from one task shape.** It is a floor under which this
  shape is safe, not a guarantee for every task.

## Result

`bench/useful_context/RESULTS.md`, with `results/pilot.json` and `results/main.json`.

---

## Addendum — 2026-09-25, after the pilot and before the main run: n and calibration

Written from `results/pilot.json`. Nothing below changes the rule, the ladder, the margin or the texts;
it fills in the two numbers the registration said the pilot would fix.

**What the pilot checked.** 26 calls, **0 errors, 0 mis-routed** (every row answered by `DeepInfra`),
29.5 s wall. The tool-shaped transcript is accepted and the answer arrives in `content`; no answer came
back as a tool call; the longest completion was 192 tokens (reasoning included), far from
`max_tokens`. Pilot gate: **20/20 at 4k — PASS.** Cost measured: US$ 0.000229 per 4k call and
US$ 0.00715 per 128k call (which arrived at 118,354–120,012 provider tokens), US$ 0.0475 in all;
OpenRouter's reported cost agrees with the computed one. DeepInfra served 1,024 cached tokens on some
rows (the system prefix), so the cache is live on this route and is reported per row as registered.

**What the pilot did not show: a drop.** 6/6 at ~119k. The brief asked the pilot to confirm a drop
worth measuring, and it did not. By the registration the ladder does not move on pilot outcomes, and
there is a reason beyond the letter: 6/6 has a Wilson lower bound of 61%, so it cannot exclude a drop
several times the margin; the main run at the n below can. If every length passes, the result is the
lower bound the registration already names ("≥ the 128k cell"), not a knee.

**Calibration.** Median `est_chars / prompt_tokens` over the six 128k rows = 4.079 → **`--cpt 4.08`**.
(At 4k the ratio is 3.77 — the head is prose-heavy — so the 4k cell will arrive a little under 4,000;
it is reported at its realised count as registered.)

**n.** Linear in prompt tokens between the two measured points: US$ 6.008e-8 per token, so one item
(4k twice, 16k, 32k, 64k, 128k = 248,000 tokens) projects to **US$ 0.01479**. Available for the main
run by the rule: 1.50 − 0.0475 − 0.10 = US$ 1.3525. Multiples of 18: 90 → US$ 1.331 (fits),
108 → US$ 1.597 (does not). **n = 90.** The registration expected 72; the rule decides, and at 90 the
precomputed table says the rule tolerates three net losses against 4k with no noise, and a flat length
still passes with six discordant pairs each way.

**Invocation cap:** `--cap 1.44`, so pilot plus main cannot pass US$ 1.4875. A call that would cross
it is not sent and is recorded as a halt.
