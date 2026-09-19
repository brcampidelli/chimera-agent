# Study 20 — "System One" decision models, calibrated-decision RL, and what a harness can actually use

Run 2026-09-18/19 at Bruno's request, over six links: TypeSafe AI's "System One models and Jev" launch,
the Synthszr glossary entry on "Reinforcement Learning for Calibrated Decisions", MIT CSAIL's "Teaching
AI models to say I'm not sure", LangChain's "Building a harness with Jev", arXiv 2601.13284
(calibration-aware RL for decision-making LLMs), and the arXiv search for the RL-for-calibrated-decisions
neighbourhood (105 results). Six agents: four on the sources (every page, the `langchain-typesafe`
source and its PR history, both papers in full, ~40 calibration papers), two on **this repository**
(a map of every decision point; a feasibility read of a local classifier against the labels we hold).
**US$ 0.** Reports in the session scratchpad (`study20/A1..A6`); this file is the synthesis.

Same reading order as studies 18/19: what the sources say once the marketing is removed, then where
they correct or confirm our own record, then the shortlist verified against the tree, then what not to
build. The question Bruno asked — *can this give the terminal and the desktop a decision capability,
a classifier, or a base layer that optimises any LLM we run in our harnesses?* — gets a direct answer
in §5.

---

## 1 · The sources, once the marketing is removed

### 1.1 Jev is an API, not a technique — and the calibration claim has no independent number behind it

**What it is.** TypeSafe AI (US$ 40M seed, CEO Diogo Almeida — 4th author of the InstructGPT paper; "co-invented
RLHF" is the site's phrase, not the record) launched `jev-1.13.0` on 2026-09-15: `POST /v1/systemone`,
a `state` (text/JSON, ≤ 32k tokens) plus typed questions — **Noul** (P(yes)), **Choice** (≤ 255 options,
`probabilities` + an unpublished `confidence` statistic), **Score** (2–10 ordered levels, expectation over
levels) — answered in one request, questions independent of each other. US$ 0.042/MTok input, output
free, no streaming, no seed, no temperature, no logprobs, no batch endpoint. Early access by waitlist,
US-hosted, API-only, not on OpenRouter (0/447 models), present on the Vercel AI Gateway. "English is the
primary training language… Other languages… are handled but not equally well"; Portuguese is not
mentioned anywhere in 109 doc pages.

**What "RLCD" is.** Everything the vendor says fits in two sentences: "Reinforcement Learning for Calibrated
Decisions", "0.8 should occur about 80% of the time". No paper, no architecture, no parameter count, no
ECE/Brier/reliability plot anywhere (grep over the whole docs: 0 hits). The CEO on HN: "architecture is
close to the chest". The org's GitHub keeps forks of `vllm` and `LLaDA` (diffusion LMs) — an indication,
not evidence.

**What the vendor's own evals show, read carefully.** 711 cases across four workflows; "accuracy" is
agreement with the average of GPT-6 Astra and Fable 5.1 (88 documents re-referenced to Opus 5 where
Fable refused), not ground truth. Mean accuracy: **Jev 67.8%**, Sonnet 5 67.8%, Opus 5 73.1%, "sol"
74.1%. The "193.6× faster / 444.6× cheaper" pairs are against different comparators (Sonnet 5 for time,
Opus 5 for cost — reconstruction from the eval data; the page does not say). Jev is off the chart on
cost and latency and **5–6 points below the top of the vendor's own accuracy axis**.

**Independent evidence (all 16–18 Sep, all small).** Lindfors (24 Norwegian documents, 192 Noul answers):
Jev **ECE 0.116 / Brier 0.086** with one wording of the questions and **ECE 0.040 / 0.089** with another;
**DeepSeek V4.1 Flash with verbalized probabilities on the same sample: ECE 0.096 / Brier 0.075**. The
calibration advantage of RLCD did not appear. backnotprop (150 poker spots against a solver): Jev 63%
overall, 38% on contested spots, **under a one-line rule (72%)**; 16 identical requests spread 0.59–0.65.
agentjournal: rows with `confidence ≥ 0.90` correct **72.2%** on a synthetic task; JNLI 83.7% (Japanese
works). Latency is the one claim every third party confirmed: **p50 0.32 s, p95 0.61 s**; 200–300 ms per
~1,200-token call. Nobody has reproduced RLCD; three open-source "alternatives" reproduce the *interface*
only (mini-jev: letters-as-logits on a frozen Qwen3-4B — same accuracy as JSON, 1.4–4× faster, and
"not calibrated probabilities").

**The contract.** MCA §2.3: the customer may not "(f) publish benchmarks or performance information about
the Services", "(b) … train a model to imitate the output", "(h) … conduct any security or vulnerability
test". Telemetry ("summary statistics and classifications … learnings") may be processed "without
restriction". `jev-latest` "moves when a new release ships". For a project whose method is *published,
pre-registered, sabotage-checked benches*, (f) and (h) are a wall, not a footnote.

### 1.2 The LangChain harness is a thin gate — and the code says less than the post

`langchain-typesafe 0.0.1a2` (MIT, public source, 17 commits, largely agent-written and human-reviewed).
`AutoModeMiddleware` sits in `wrap_tool_call`: **after** the model emits a tool call, **before** it runs.
It sends the classifier the **last 30 messages of any role** (tool outputs included), the tool call
**with unredacted arguments**, and the docstring — nothing of the runtime (cwd, taint, plan). One
Noul question, `is_risky`; **`p ≥ 0.5` blocks** and returns an error `ToolMessage` to the model, which
continues (a retry oracle that hands the attacker the probability). No "ask" band: "This middleware
refuses risky calls. It does not request approval." The threshold was `0.2` and configurable, became a
constant `0.5` in a commit titled "cr" with no body. The default true/false criteria are dead code — the
post's own snippet sends the question without them. Any classifier error (5xx, 429, 30 s timeout, state
> 32k) **kills the whole run** — no retry, no fallback. The post measures **nothing**; it repeats the
vendor's "200×/400×". A competing open PR (#40556, by the post's co-author) sends **only user messages**,
redacts credential-shaped argument keys, and tests that injected tool output never leaves the process —
that design lost the review ("we don't do this redaction anywhere else").

### 1.3 The papers: a decision token after reasoning is extraction, not decision — and RL makes it worse

**arXiv 2601.13284** (AWS/USC; Findings of ACL 2026; no code). Closed-option decisions, Qwen3 1.7B/4B/8B,
500 training examples per task. GRPO wins **+2.8 pp** accuracy over SFT on average and leaves the
decision-token probability pinned at ~1: **94.6–99.9%** of 64 samples per query above 0.99 (Table 2);
ECE **10.7–24.4** vs SFT-without-reasoning **3.5–11.9**. The diagnosis is the transferable part:
swap the reasoning trace for one of the *opposite* label and the decision follows it in **92–100%** of
cases, still at p ≈ 1 (Table 3) — the token after `</think>` extracts the trace; it does not decide.
Their fix (GRPO + λ·CE on the decision token, uniform target when wrong, λ = 0.001) cuts ECE by at most
**8.42** points vs GRPO and beats SFT in **3 of 12** cells. Rigor: one run per cell, no seeds, no CIs,
eval n undeclared, checkpoint **selected on the test set**. The ACL camera-ready adds the number that
matters for a gate: **AUROC of the post-reasoning logprob under GRPO reaches 56** (chance is 50);
base 65–72; SFT 66–84. And Table 5: **isotonic regression on a labelled set takes GRPO's ECE from 12.20
to 4.80 without retraining** — better than their method in 3 of 6 cells.

**RLCR** (Damani, Puri et al., MIT; ICLR 2026; code + Qwen2.5-7B checkpoints public). Reward =
`1[correct] − (q − 1[correct])²` with `q` a **verbalized** number after an `<analysis>` block. ECE
**0.37 → 0.03** in-domain (the "up to 90%" of the press release), **0.46 → 0.21** out of domain (−54%);
plain RLVR takes AUROC **0.54 → 0.50** — RL with a binary reward destroys the little discrimination the
base had. The same prompt without the reward: ECE 0.34 — prompting does not calibrate a 7B post-RL
model. A peer-reviewed follow-up (DCPO, ICML 2026) measures RLCR **losing accuracy on AIME24, 40.0 →
32.8**, where the MIT release says "no loss". The Synthszr glossary entry is an unsourced newsletter
definition that names neither TypeSafe nor MIT; not citable.

### 1.4 The calibration facts a hosted harness has to live with

- Post-trained frontier models are not calibrated on token probabilities (GPT-4 TR: "post-training hurts
  calibration significantly"); in structured JSON output logprobs **saturate above 0.999** (VERDI); on
  Qwen3.5 answer-token logprobs are **anti-calibrated** (AUROC 0.32–0.49).
- Hosted reasoning routes mostly return **no logprobs** (Azure/OpenAI except GPT-6 Astra; Anthropic never;
  Gemini withdrew at 3.1 Pro). **OpenRouter drops `logprobs` silently** unless
  `provider.require_parameters` is set — the "data vanishes, nothing complains" family (§2r/§2ad).
- Verbalized confidence beats logprobs on RLHF'd models (Tian 2023: −50% relative ECE); a prompt recipe
  (over-confidence advisory + self-debate) takes AECE **8.0 → 4.1** on GPT-4o and **9.1 → 3.3** on
  Sonnet-4.5 at cost ≈ 0 — single-author preprint, weight accordingly.
- LLM judges: ECE from **11.8** (Qwen3-235B) to **47.4** (Llama-3.3-70B) on JudgeBench; and **ten samples
  at T = 0.7 worsen ECE for all six judges** (GPT-4o 39.3 → 47.1). Agreement between samples is error
  correlation, not certainty — our own panel carries **1.46 independent votes in 3** (ICC 0.527).
- Ollama returns logprobs on its OpenAI-compatible route from **v0.12.11**; local is where the signal
  exists.
- Every ECE reading needs Brier and AUROC beside it: a constant at the base rate has ECE ≈ 0 and AUROC 0.5
  (a linear probe with ECE 0.025 and AUROC **58** exists in the literature); ECE with n < ~200 distinct
  items is noise (Roelofs 2022); in-domain calibration does not transfer (RLCR 0.03 vs 0.21).

### 1.5 A third-party loop test (added 2026-09-19): the router helps a cheap model, skips steps in chains, and never fills an argument

A Brazilian creator's test (YouTube `QWap6zTgIH8`, "JEV: modelo criado pelo co-criador do ChatGPT merece o
hype?", on the Vercel AI SDK evals; no repository or `n` published — a **hypothesis source**, not a number)
is the only evidence so far of Jev used as a **tool router inside an agent loop**: scenarios with
deliberately similar tool names; Gemini Flash, Fable 5.1, GPT-6 Astra, Opus 5, Qwen 27B, DeepSeek V4.1 Flash,
each plain and with Jev choosing the tool. Reported: the cheap model (Gemini Flash) gains accuracy and its
cost falls by more than half; the strong model (Fable 5.1) is as good alone; reasoning tokens fall for
every model; and two trade-offs — in chained flows (find the contact, then schedule) the router **skips
steps or picks out of order**, and the **mean number of steps rises**. Both fit what §1.3–1.4 predict for a
classifier that sees the state and not the plan, and the first fits our own #514 (`thinking: false` for
spoken turns: first token 2.7–8.7 s vs 7.4–19.9 s at equal answers). The demo's hidden simplification:
`execute_tool(selected_tool)` ignores its `kwargs` — the tools are mocks without arguments. Every Chimera
tool has arguments (`path`, `command`, `content`), and the expensive and dangerous half of a tool call is
the argument, not the name; a router picks the name and the LLM still fills the rest.

The explainer that travelled with the video mixes verified facts with claims the primary sources do not
carry, listed here so they are not repeated: "co-inventor of ChatGPT" (verified: 4th of 20 authors of the
InstructGPT paper); "when it says 90% the empirical accuracy is 90% … exactly 850 of 1,000" (a promise
with no published reliability plot; measured by third parties: **72%** correct in the ≥ 0.90 bin, ECE
0.116/0.040 depending on the question's wording, equal to a verbalized DeepSeek on the same sample — and
"exactly" is false by definition, calibration being a group property with sampling noise); "RLCD uses
Brier or KL" (TypeSafe never published its objective — Brier is what MIT's **RLCR** uses; the two are being
conflated); "zero hallucination: impossible to invent a tool outside the catalogue" (true of the schema;
the vendor's own FAQ: "it can't invent a category outside that list, but it can choose the wrong one");
`JevClient.predict(prompt=…, response_model=…)` with a `.confidence` (pseudo-code — the API is `state` +
`questions`; **Noul has no `confidence`**, and Choice's `confidence` is an unpublished statistic); the
threshold guide 0.60–0.70 / 0.80–0.85 / 0.95+ (hand-set numbers over an opaque function, and a Choice's
mass depends on how many options there are — "0.85" over 5 tools is not "0.85" over 40; thresholds come
from a ROC on a labelled set at the false-allow/false-refuse one tolerates, which the vendor's own docs say:
"test with your own data"); and "marginal confidence → escalate to the LLM for chain-of-thought" (§1.3
measures the opposite: a decision after reasoning is extraction — escalate to **another verification** or
to the human, not to more thinking by the same model). What the explainer gets right and is worth keeping:
a **per-tool risk matrix with three bands** (auto / escalate / human) — our C1, with `p` measured and the
band edges read off a ROC rather than a table; and the latency figures, the one claim every third party
confirmed.

---

## 2 · Where this corrects, confirms, or sharpens our own record

1. **Our judge decision (2026-09-12, reaffirmed after #488) is the literature's decision too.** The
   Behavioral Credibility Trilemma (2605.25739, 540 configurations, d = 1.10–5.35): a policy whose
   autonomy is gated by its own confidence inflates that confidence on tasks below the approval bar;
   the resolutions are commitment and **separation of roles** — the one who decides is not the one who
   reports confidence. Our kernel keeps the judge a library and the taint ledger decides on provenance;
   LangChain's design sends the assistant's own prose and tool outputs into the classifier. Keep ours.

2. **"Judge yields to framing" (#488, 4/10 easy and 8–9/14 ambiguous flipped to ALLOW) now has a
   mechanism behind it.** A decision token emitted after a trace follows the trace (92–100% under
   swap). A persuasive sentence before the action *is* a trace. This is why the kernel's docstring rule
   — strip unverifiable claims before asking — is the right layer, and why any classifier we add must
   be measured **with and without** that stripping (A2 §F.3).

3. **Our sample-agreement signals are the ones the literature measured as harmful for judges.**
   `RoutedBackend._agree_or_escalate` (`router.py:200-222`), the selective early stop
   (`engine.py:459-467`, `min(ratio) ≥ 0.8`) and `RouteRecord.agreement ∈ {0, 1}` all treat k-sample
   agreement as confidence; `router.py:157` calls it "free confidence, no logprobs". JudgeBench: ten
   samples worsen ECE for every judge listed; our ICC 0.527. Not wrong to use for *routing* (it is
   cheap), wrong to read as a probability without discounting the panel's correlation.

4. **The decision points, counted.** A5 mapped **44** (A1–A14 governance, B1–B8 fusion/routing, C1–C8
   orchestration, D1–D17 Code screen/autonomous loop, E1–E4). **Deterministic rules dominate**; **12 are
   LLM-decided**, all parsed by regex/JSON to an enum or a verbalized float; **none asks for or receives
   a probability**. Three floats exist (`Selection.score`, `RubricResult.overall`, `StrongVerifier.score`),
   verbalized, compared to a fixed **0.6**, never calibrated; the strong verifier has **no bench at all**.
   Logprobs: **zero in the package** (one comment, `router.py:157`), but `LLMGateway.complete(**kwargs)`
   already forwards to `litellm.completion` — `logprobs=True` reaches the provider today; only
   `_normalize` (`gateway.py:948-983`) never reads `choices[0].logprobs`.

5. **A default pattern nobody had written down.** Decisions that **execute** fail closed (REVIEW without
   an approver refuses; silence refuses; `no_plan` does not run). Decisions that **judge quality** fail
   open (spot check unreadable = faithful; strong verifier unreadable = 1.0; checklist error = `[]`;
   `_lineage()` error = clean). A calibrated probability would replace two implicit defaults with one
   threshold and a stated cost. Abstention is typed in five places — and always triggered by a **parse
   failure**, never by model uncertainty.

6. **The label bottleneck is ours, not the model's.** The human approval stream exists and is durable
   (`ApprovalCard.tsx:136-142` → `POST /api/approvals/{id}` → `<home>/approvals/history.jsonl`, with
   `outcome` and `seconds_to_answer`, median **7.9 s**). On this PC it holds **43 lines: 24 approved,
   1 refused, 18 timeout** — it measures compliance, not danger (the card itself cites arXiv 2606.05647:
   9/16 approved the malicious change the monitor had flagged). The line carries no `tool/lineage/rule/
   run_id`; `audit.jsonl` has no timestamp or request id; the desktop never writes ALLOW events
   (`audit_allows` exists, unused on the Code surface); governance ships `off`. Production base rate of
   "attack": 242 taint refusals in 1,397 tool calls, **zero observed attacks**. A probability calibrated on
   an authored corpus (24/55 = 44% attacks) is a probability about the corpus.

7. **Corpora we already hold that can carry a calibration number** (A5 §4, A6 §A): `review_judge`
   aacr-bench — **2,145 human labels** (1,505 correct / 640 incorrect findings), five rubric arms already
   measured (arm C: recall 60.4% / false-reject 38.5%, J 21.9, no usable operating point — exactly what a
   probability + ROC threshold is for); `harness_bench → false_success` — **547 solves, 139 false
   successes**, continuous oracle (per-task split mandatory: random 0.934 vs within-task 0.600);
   `governance_judge` ambiguous — 35 items, both sides, replay 0.85 (small); `blind_audit` — 69 envelopes
   with constructed truth; `test_gate_two_sided` — 98 patches / 154 tests. What does not exist: any
   `(proxy, reward)` log for PROBE, any corpus for the strong verifier, any natural label for the fusion
   judge.

---

## 3 · The shortlist — verified against the tree, cheapest first

### Tier A — infrastructure that makes every later number possible (hours, US$ 0, product decision)

**A1. Record the join, record the ALLOWs, record whether the logprob came.** Three small changes that are
the precondition of everything below and cost no model call: (i) `pending._record` (`pending.py:250-291`)
writes `tool`, `lineage`, `sources`, `rule`, `run_id` and the audit hash beside `outcome`; (ii) the Code
surface turns `audit_allows` on so the audit holds the negatives the approval stream lacks; (iii)
`CompletionResult` gains `logprobs: list[dict] | None` filled from `choices[0].logprobs` in `_normalize`
(2 lines), plus a receipt flag saying whether it **came** — with OpenRouter dropping the parameter in
silence, a pipeline that "uses logprobs" can be reading `None` forever (§2r). Then `Verdict.confidence:
float | None` (`policy.py:27`), which already flows to `audit.jsonl` and the Security screen with no
frontend change; `RouteRecord.agreement` (`route_log.py:41`) is already `float | None` and only ever
receives 0/1 — the continuous majority fraction is one line. **S.** Nothing here changes behaviour.

### Tier B — measure first (a day each, cents)

**B1. A probability for the judge we already have, paired on the corpora we already have.** Arms on the
same items: (B) the current one-word judge (deepseek-v4-flash-0731; published 9/9 · 0/10 easy, 13/14 ·
5→3/20 ambiguous, 61/64 OATS, 4/10 · 8–9/14 framing flips); (V) the same hosted model asked for a
**verbalized** probability with the over-confidence advisory + self-debate recipe, Platt-scaled on a
held-out slice; (L) a **local** Qwen3-4B/8B via Ollama ≥ 0.12.11, **decision-first, no reasoning**,
probability read from `top_logprobs` renormalized over {BLOCK, REVIEW, ALLOW}, isotonic on the same
slice (the paper's Table 5 route: 12.20 → 4.80 without training); (L′) the same local model **after**
a short reasoning — the paper's central claim, replicable in a day: L′'s AUROC should collapse toward
the 56 they measured. Metrics per arm: catch at **fixed false-refuse ≤ 0.10** (threshold chosen on the
ROC of the *training* slice, never 0.5 by hand), framing-flip rate under the four wrappers
(`bench/perturbation_floor/run_wrappers.py`) measured with and without the docstring's stripping,
AUROC, Brier (primary), ECE with its simulated floor (n ≈ 117 → floor ~0.05–0.08; below that nothing is
legible), replay and paraphrase floors (B: 0.85 / 0.90), latency p50/p95 from `evaluate` to `Verdict`,
cost per decision and **per governed turn** from a real session's `audit.jsonl`. **Verdict written before
running (A2 §F.3):** an arm earns a request to Bruno only if, on the ambiguous corpus, catch ≥ B **and**
marginal false-refuse ≤ 1/20 **and** flip rate ≤ half of B, with ECE reported. Otherwise the result is
"a cheaper judge with the same defects" and the library decision stands with a third arm measured. ~US$
0.10. Note the corpus is 117 items with both sides: Wilson intervals beside every rate, three repetitions
per cell (§2x), model version pinned in the artefact.

**B2. The one corpus large enough to calibrate on human labels: aacr-bench (2,145).** The review judge
pilot ended at J 21.9 with no operating point; a probability + threshold is the missing piece, and here
n permits ECE. Same arm shape as B1 (verbalized + Platt; local decision-first logprob + isotonic;
placebo TF-IDF + LR), split **by repository/PR** (§7 — random split is the leakage size), Brier and
AUROC primary, a reliability table with n per bin, and the 2×2 at the false-reject Bruno tolerates. This
is also where "does a local calibrated classifier beat a hosted judge on a real decision?" gets its first
honest number — on a decision we do not yet ship, so nothing is at risk. ~US$ 1 (1,017 Diff-Level lines
were projected at US$ 3.66 in the pilot). **A6's registered expectation for local embedders: tie with
the lexical placebo, below the judge on subtle items.**

**B3. Close the "semantic > lexical?" question on false success at US$ 0.** Re-run `bench/false_success`
with one arm added — a local embedder (potion-8M and bge-small, MIT) over the claim — same leave-one-
task-out split, same 385 items. Registered prediction: AUROC by task **0.58–0.64**, indistinguishable
from TF-IDF (0.5996) and the length ceiling (0.6023); random split ≥ 0.90 (leakage). Only earns a
sentence if it clears the original bar (≥ 0.75, IC ≥ 0.65, +0.10 over `self_report`), which nothing has.
Half a day.

**B4. A "System One" tool router inside our own loop, measured with an oracle (added 2026-09-19).** The
video's hypothesis, pre-registered: on the 23 `harness_bench` tasks (deterministic oracle, noise floor
0.073 already measured, k = 3), the current loop × a loop where a cheap **decision-first** model — local
Qwen3-4B read by logprob over the tool names, or the hosted voice model asked for a verbalized choice —
picks the **tool name** and the strong model fills the arguments. Metrics, paired by task: oracle pass,
cost, **steps**, and **skipped steps on the chained tasks** (the two trade-offs the video reports), with the
effect read against the replica noise floor. Registered prediction: with `deepseek-v4-flash` as the
executor, a tie inside the floor and +10–20% steps; with a weak executor, a gain. ~US$ 30 (the factorial's
cost). Jev itself stays out of any published arm under MCA §2.3; a vendor arm enters only with the letter.

### Tier C — design, only after B1/B2 return numbers

**C1. A two-threshold REVIEW band in the kernel.** `p ≥ τ_hi` → REVIEW (card), `p ≤ τ_lo` → ALLOW,
between → today's rule path with an audit line; `τ` from the ROC at the false-refuse Bruno tolerates.
Attach at `Verdict.confidence` + `TrustKernel.evaluate` (`kernel.py:182-189`). Changes behaviour in three
places (A5 §6.1): only the band pays the human cost; the 0.25 → 0.15 instability becomes a band width
instead of a flipped verdict; a wrapper should *lower* p, not cross a word. **Still a library** until B1
says otherwise — this item exists to make the judge decision reversible by a number, not to reverse it.

**C2. Accept the success claim by uncertainty, not by attempt index.** The strong verifier (D9) fires on
`index > 1` today (`autonomous.py:1176`) and has no bench. Combine what the receipt already holds —
claim∩diff overlap (0.6643), `verified/abstained/none`, `P(APPROVED)` from the Manager once logprobs
exist — into `p(true_success)` and fire D9 when p is in the uncertain band. Measured on the 547 solves,
leave-one-task-out, by **false successes caught at the same number of D9 calls** "index > 1" spends
(random ≈ 42, claim-text 47, overlap 48 at 30% budget). The bar is the one already registered in
`bench/claim_vs_diff/PREREGISTRATION.md` §8.

**C3. Conformal risk control per argument role.** Rahman 2026 (2607.24343): a failure in one rare
high-risk field (recipient, credential, command) is averaged away by the benign arguments around it —
our kernel already judges per action/argument (`render_action`), so the per-role coverage guarantee
maps directly. It buys a guarantee, not a better signal; needs a stable labelled set and re-calibration
on every route/model change (exchangeability). After B1.

**C4. A typed-decision contract of our own, with two backends and an optional third.** `Noul / Choice /
Score → distribution` as a small module behind the kernel, the envelope verifier, the Manager and the
route log: backend *local-logprob* (Ollama, decision-first, isotonic on our slice), backend
*hosted-verbalized* (recipe + Platt), and — **only** after a PT-BR calibration test with a real key and a
written answer from TypeSafe on §2.3(f)/(h) — Jev as a third backend behind a flag, pinned to a version,
fail-closed **per call** (refuse this call, keep the run), never the only layer, never fed tool output
or the assistant's own prose. The interface is what transfers from Jev; the vendor is optional.

---

## 4 · Do **not** build these

- **Jev as a dependency of any decision path.** Closed, three days old, English-first, US-only, alias that
  moves, contract that forbids publishing the numbers we would need to justify it, and no independent
  evidence of better calibration than a verbalized frontier model on the same sample (Lindfors: 0.116 /
  0.040 vs 0.096). A third backend behind a flag is the ceiling, and only after §3 C4's two conditions.
- **A classifier fed the last 30 messages with unredacted arguments.** That is the LangChain design and
  the exact surface #488 measured as framing-sensitive; the trilemma paper gives the theorem. Only the
  user's instruction and the rendered action authorize; tool output and assistant prose never enter the
  question.
- **Reading a decision-token logprob after reasoning as confidence** (AUROC 56 under GRPO; 92–100% trace-
  following). Decision-first or not at all.
- **k-sample agreement read as a probability for a judge** (ten samples worsen ECE for every judge in
  JudgeBench; our ICC 0.527). Keep it for cheap routing; discount it by the measured panel correlation
  before it becomes a `p`.
- **Training a calibrated model (RLCR/DCPO/2601.13284) in this repository.** The project's stated stance
  is inference-time, no weights (`reranker.py:13-14`); RLCR costs GRPO with 32 responses/prompt on 20k+15k
  examples and its OOD ECE is "often high in an absolute sense" by the authors' own words; the 2601 method
  beats SFT in 3/12 cells with no seeds. If Bruno ever wants it, it is a Bee-side experiment: test the
  **public RLCR checkpoints** against Table 1 first (§2aa), three seeds after.
- **A local calibrated classifier for governance as a product feature now.** Buildable (A6: potion-8M + LR
  < 1 ms expected; bge-small ONNX 5–20 ms ≈ the harness's 16.4 ms; all MIT/Apache/BSD), **not calibratable**:
  55 two-sided items, a 24:1 human stream, zero observed attacks, no production prior. Its honest home is
  B2 (a real corpus, a decision we do not ship) and, for governance, the A6 §D design (~24 h, < US$ 1)
  with its non-adoption rule — where the registered expectation is a tie with the lexical placebo.
- **`observe` as the "record-only" surface for any of this.** `observe` applies BLOCK (`profile.py:21-23`).
  A record-only surface would be new; it stays Bruno's call, as on 2026-09-12.

---

## 5 · The direct answer to the question asked

**Can Chimera have this "decision capability"?** It already has the shape — 44 typed decision points,
most deterministic, a governed judge kept as a library, typed abstention, approval cards — and what it
lacks is not a model but a **number on each decision**: no probability, no calibration metric, no
logprob plumbing, no join between "asked" and "what happened". That gap is closable in hours (Tier A),
and it is the same gap on the terminal and on the desktop, because both go through `TrustKernel`,
`EnvelopeVerifier`, the Manager and the route log.

**As a classifier?** Yes, as an *interface*: typed question over program state → distribution → threshold
chosen on a ROC → REVIEW band to the card. The literature and our own #488 say what must **not** enter
the question (tool output, the model's own prose, an unverifiable framing sentence), and the papers say
how to read a model for it (decision-first, renormalized, isotonic on our slice; verbalized + recipe on
hosted routes). Whether any backend beats the one-word judge we have is **B1's** job, on corpora we
already own, for cents.

**As a base layer that optimises any LLM we run?** Not by training — that is out of the project's stance
and the measured gains do not survive out-of-domain. By **measurement plumbing**: once every LLM decision
carries `p` and a receipt says whether `p` was real, the same Wilson/McNemar/noise-floor apparatus we
already run on outcomes applies to decisions, and a model swap on the route becomes a calibration
re-check instead of a surprise. That is the layer worth building; Jev is one possible backend of it, and
today the least verifiable one.

**Suggested order:** A1 (hours) → B1 (a day, ~US$ 0.10) → B3 (half a day, US$ 0) → B2 (a day, ~US$ 1)
→ B4 (~US$ 30, only if B1 shows a decision-first backend worth routing with) → C1/C2 only with B1/B2
numbers in hand → C3/C4 later. The Jev key and the §2.3 letter can be requested
in parallel at zero cost; they gate nothing above.

---

## 6 · Never cite, and why

- "193.6× faster, 444.6× cheaper", "up to 200× faster inference and 400× lower cost", "0% hallucinations"
  — vendor multipliers against different comparators; "0%" is "not empirical" by the vendor's own text.
- "Calibration error reduced by up to 90%" — the in-domain best case (0.37 → 0.03); OOD is −54%, and a
  peer-reviewed follow-up measures an accuracy loss the release denies.
- "Reducing ECE scores up to 9 points" (2601.13284 abstract) — the table's maximum is 8.42; no seeds.
- The Synthszr glossary — no author, date, or source.
- "Platt scaling on 200+ examples cuts ECE 30–60%" and "flag 17–23% … catch 71–88%" — found in a vendor
  blog and an unsourced snippet; no paper behind either.
- Any Jev number published under the MCA — the authors carry the contractual risk; we do not add ours.

---

## 7 · Coverage, honestly

Read in full: the launch post and FAQ, 109 doc pages (30 read, the rest grepped), the legal pages
(terms, MCA, DPA, privacy), the vendor eval site and its data files, the HN thread (494 comments, CEO
replies), four independent tests, `langchain-typesafe` 0.0.1a2 source + tests + PR #40545 history + PR
#40556, both papers (arXiv HTML and ACL PDF), the 105-result arXiv listing (8 kept), ~40 calibration
papers/posts for §1.4, and — in this tree — `chimera/governance/*`, `chimera/fusion/*`,
`chimera/orchestration/*`, `chimera/core/{agent,autonomous,supervisor,strong_verify,verify,checklist}.py`,
`chimera/api/{code_api,plan_gate,app}.py`, `chimera/providers/gateway.py`, and every `bench/*/RESULTS.md`
named above. Not read: `bench/cascade` results (exists, not opened); `csail.mit.edu` (403; the same text
read on `news.mit.edu`); one nearhere.events comparison (Cloudflare wall); the `awesome-jev` second-hand
items (listed, not opened). Not done: any call to the Jev API (no key), any latency measurement of a local
embedder (none exists in the repo; A6's figures are expectations to be measured in the pilot).
The YouTube test in §1.5 was read from a written summary; the video's data and `n` are not published
and no repository was found for it.
