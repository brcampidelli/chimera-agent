# Working plan from study 16 — eight axes, one gate, and what each can and cannot show

Written 2026-09-07 after the eight-axis literature sweep (memory: `chimera-study-16-eight-axis-sweep`).
Every claim about Chimera below was checked against the code today, by file and line, because the
sweep itself produced three apparatus errors — a grep that declared a gap where a prompt clause
existed, and two silent fetch truncations, one of which became a confident false statement. The
lesson of the day, in every context it appeared in: **the instrument fails quietly and the number
comes out looking like a result.** This plan is ordered so that the instrument is fixed first.

## 0. The gate: a reporting protocol, before any axis

**Why first.** Six independent papers (2602.11619, 2606.20695, 2512.06710, 2608.14711, 2607.02577,
2608.22331) plus two of our own measurements (`bench/loopsbench` 25% flip between identical runs;
`bench/retry_lift` +6% / −4% on the same comparison) say a single-run agent number is a sample, not
a measurement. 2606.20695 quantifies the consequence: **seven of ten recent multi-agent architectures
report headline effects below their own noise floor.** Every item below is unreadable until this exists.

**What exists.** `chimera/eval/paired.py` — `PairedResult`, `compare_paired`, `run_paired_experiment`,
`format_report`. Paired McNemar/Wilson over one run per arm.

**What changes.** *(Shipped 2026-09-08 as `chimera/eval/replicated.py` — see PR #376. Written here
first as "extend `PairedResult`"; built instead as a sibling module, because `PairedResult` is
constructed positionally by twenty bench scripts and four eval modules and every one of them keeps
its numbers only if the class does not move.)*
- `ReplicatedArm` takes **k runs per task per arm** and reports: `pass^k`, per-task flip rate,
  ICC(1) across runs, and — the published fix from 2606.20695 — **mechanism-active pass^k**: score
  only the trials where the mechanism under test was *logically active* (§2r, "reportar quanto ela
  agiu", as a protocol). Zero active trials is `None` and prints `NOT MEASURED`, never 0%.
  `compare_replicated` pairs two arms on per-task `pass^k` through the **untouched** `compare_paired`.
- A standing rule in every `PREREGISTRATION.md`: **three seeds decide; two alert; one is a sample.**
  A +18pp p=0.012 result vanished at the second seed in 2606.20695; our own +6%/−4% is the same shape.
- One caveat 2608.22331 adds that nobody else measured: **prompt-perturbation noise is 11×–58×
  larger than rerun noise at temperature 0.** So the floor must be published from
  semantics-preserving perturbations, not from reruns alone. `pass^k` over reruns *understates* it.

**Cost.** Code only; re-scoring `bench/loopsbench` p5+p6 is free. **What it cannot show:** nothing
about any effect — it is the ruler, and it has to exist before the ruler is pointed at anything.

---

## 1 + 7. Loop and self-healing — our two recovery policies are the two losing baselines

**The fact in the code.** After a failed attempt the retry gets *generic* feedback — manager prose
plus verifier output, joined at `chimera/core/autonomous.py:1263-1264`. Escalation to a stronger
model is unconditional from attempt 2 onward (`:886-887`): `if index > 1 and self.escalate_worker`.
There is no classification of *what kind* of failure happened, and no targeted path at all: the only
step-targeted feedback is `_fault_hint` (the first tool observation starting with `error:`), and
`verify.py`'s missing-program detection turns a broken *verify command* into an abstention, not a
failure. (Corrected 2026-09-08 while building item 1+7 step 1: the earlier text claimed a build-error
route that does not exist in the file.)

**What the literature measured.** 2606.01416, 100-task fault injection, **matched recovery budget**:
recovery targeted at the failure class **98.8%** · retry-only **94.5%** · full replanning **93.8%** —
targeted wins at every budget and the gap is **widest at one recovery attempt**. We ship exactly the
two losing arms. 2607.19338 adds that unconditional escalation is the baseline a budget-calibrated
router beats at **35% of its recovery cost**. 2605.08563 adds that retries are **not independent**:
the IID model overestimated pass@3 by **17.4 pp**.

**Step 0 — free, today: the retry-contamination probe on our own receipts.** The desktop's
`runs.jsonl` (`%APPDATA%\app.chimera.desktop\data\`, 88 receipts, 21 with ≥2 attempts) already holds
attempt-conditional outcomes:

| attempt | after the previous one failed |
|---|---|
| 2nd | 4 succeeded / 17 failed (19%) |
| 3rd | **0 succeeded / 11 failed (0%)** |

Under independence at p≈0.19 the third attempt should recover ~2 of 11. It recovered none. n=11 —
suggestive (p≈0.10), not decisive — but it is the direction 2605.08563 predicts, it cost nothing,
and it says `max_attempts=3` is paying for a third attempt this install has never seen succeed.
**Publish as a probe with the n stated**, then fit T\* properly when the k-run protocol produces more.

**Step 1 — a failure classifier, then class-targeted recovery. Size M.** After a failed attempt,
classify before feeding back. Classes we can already detect from what `Attempt` carries: build
error (exists), failing test (verifier output), hollow success (`diff_productive is False` — the
diff gate already computes it), tool-skip / result-ignore / fabrication (2607.04686's taxonomy, all
visible in `tool_names` + the transcript), timeout. Route each to a *specific* recovery — the failing
test's assertion, the diff that was reverted (`bench/retry_lift`'s I1, `--diff-feedback` -- I2 is `--stagnation-fuzzy`; neither
got a clean run), a forced-action prompt for hollow success — instead of one generic retry.

**Step 2 — the matched-budget sweep. Size M, real spend.** Arms: retry-only (today) · replan-on-
stagnation (today) · class-targeted (step 1), at recovery budget 1, 2, 3. Use 2606.27009's
methodology to make it affordable: **generate each trajectory once, replay every policy over the
identical drafts, cache the judge** — strictly paired at low cost. Add 2603.01548's deterministic
control (reroute on tool failure, LLM only for "no feasible path": matched ReAct at **93% fewer
control-plane calls**).

**Registered expectation.** Targeted ≥ retry-only at budget 1, or the paper does not transfer to
coding tasks. **What it cannot show:** anything at n=8 — this needs the k-run protocol and a venue
with a middle (§4 below).

---

## 2. Tool calling — the benches measure the adapter, and the obvious gap must stay open

**The fact in the code.** Tool schemas leave through `chimera/tools/registry.py:51
to_openai_schema(compact=)`; `compact` calls `schema_compact.compact_schemas`, which prunes prose and
annotations *without changing the JSON shape*. Schema-constrained decoding: **absent** (341 files,
8 hits, none about decoding). `tools/defer.py` and `mcp_defer.py` exist and ship off.

**Step 1 — interface preflight. Size S, ~1 day, no spend.** 2609.03966: changing *only* the serving
adapter moves the same model between **0.00 and 0.96** on BFCL v4, and a 2×2 over chat template ×
parser puts both main effects at exactly zero. Add a test in `chimera/providers/` that sends a
known tool-inducing prompt through each configured adapter and asserts **emitted == parsed**. Then
**re-read `bench/tool_defer`** (−26% tokens, completion 60%→50%, p=0.125) and `bench/parallel_tools`
with the preflight passed and a perturbation floor attached — today neither has an honest denominator.

**Step 2 — the readout-vs-input test. Size S, one day, pre-registered.** 2606.16364: on real BFCL
failures the model attends most to the *correct* tool 80% of the time and picks wrong anyway;
reordering / duplicating the gold tool recovers **≤23%** of failures, readout-side fixes recover
59–91%. **Prediction to register: reordering and duplicating the gold tool in our catalogue recovers
≤23%.** If it holds, we stop spending on retrieval and ordering — which is where `defer.py` and the
"crowded harness" intuition would have sent us.

**Step 3 — TSCG at the API boundary, A/B on the weakest FUSION member first. Size S/M.** 2605.04107:
JSON schema is a machine-parsing format, not an LLM-reading one; a deterministic schema→language
compiler with **no model access** takes Phi-4 14B from **0% to 84.4%** at 20 tools, and its companion
2605.26165 publishes the null that bounds it — **+20.5 pp at an 8K budget, ≤1 pp at 32K**. The hook
is exactly `to_openai_schema`. Register: gain on the smallest panel member, ~nothing on
deepseek-chat-v3.1.

**Do not build:** schema-constrained decoding. 2606.25605 — with JSON-schema constraints and tool
calling both on, several open models **stop invoking tools entirely** (the grammar mask makes
tool-call tokens unreachable); 2608.13959 — grammar constraints negative on abstention in 4 of 6
cells, worst **−29.5 pts**. If it is ever tested: **constraint ON, on a subset, paired, before any
full run** — it acts during generation, so a pre-hoc estimate measures the opportunity, never the
damage (§2r, §2v).

---

## 3. Context — compaction is dead code, and that may be correct

**The fact in the code.** `ContextBudget` triggers at a **fraction** of the window
(`context_budget.py:43 DEFAULT_TRIGGER = 0.8`, `window_tokens()` from a hand-checked catalogue). The
CLI defaults `context_budget=None`; the desktop sends 0.6, which on the default 1,310,000-token
window means compacting at **786,000 tokens**. `bench/compaction`: **0 compactions in 137 traced
runs**, median peak 6,826 tokens. The summariser is written, tested, and has never run in anger.

**What the literature says.** 2606.00408: the gain from dropping stale context follows an
**asymmetric inverted-U** against the model's own capability and **collapses when the model is
saturated**. `bench/context_rot` found **no knee** from 4,587 to 953,392 tokens once the provider
endpoint was pinned. We are, very likely, in the flat part. 2608.31057: calibration gains do not
transfer to held-out tasks, and equal token budgets hide unequal delivered context.

**Step 1 — measure before touching the code. Size S.** Compaction on/off × two model tiers (a weak
one, the production one), paired, on a venue where context *actually grows* — ArbiGraph long chains
(§ venues). **Registered expectation: a null at the production tier**, and "compaction is correctly
off for our tier" is a better sentence than the shrug we have.

**Step 2 — instrument before any keep-policy. Size S, no spend.** Split the trace into
*stored state / delivered context / management cost / outcome* (2608.31057). Without it a future
compaction A/B compares two things measured differently (§2g).

**Step 3 — if compaction is ever enabled: absolute budgets and constraint pinning, together.**
Fraction-of-window is the wrong shape at 1M+ windows. And 2606.22528 (ConstraintRot): prohibited
actions rise **0% → 30%** after compaction, 0% when the constraint survives the summary; **our taint
ledger and write posture live in context.** Pinning is moot while compaction never fires — build it
*with* any re-enablement, never as a separate item.

---

## 4. Sub-agents — multi-agent loses when the repair budget is held fixed

**The fact in the code.** `bench/hierarchy`'s single-agent arm is real but on **single-shot reading
of ~1–2k-token documents** (70% vs 80%, n=10, not significant, and the hierarchy cost **47% MORE
tokens**). The delegation "savings vs counterfactual" in `orchestration/receipts.py:54-58` is a
`counterfactual_tokens` **estimate**, flagged as such — not a measured single-agent arm with the same
repair budget. On coding tasks the question has never been asked.

**What the literature measured.** 2609.04898 (RefactorBench, environment fixed): single agent
**86%** vs sub-agents **66%**, and **no task passes under delegation that fails under retrieval**.
2609.03718 (info access *and repair budget* fixed): single 96.4% vs multi 88.2%; execution-feedback
repair 71.8→96.4, scripted reflection adds nothing. 2607.16133: under infinite relay bandwidth
MAS ≡ SAS, so all MAS advantage lives in the compression trade-off, and gains **reverse for stronger
models** — FUSION is the strong regime. 2608.00243: a 3-judge panel vs one judge, +8.5 to −4.4 pp,
one reliable loss. The clean win, 2607.28430 (+29.8 pp), names its condition: **task difficulty**.

**Step 1 — RefactorPlatform's question on our own coding path. Size M.** `sequential_write` crew vs
one agent, **same tasks, same information access, same repair budget**, k runs each. The one number
to publish: **how many tasks pass under delegation and fail under the single agent?** Registered
expectation: ~0, as they found. If so, `sequential_write` is dead weight on that suite and the
`counterfactual_tokens` estimate has been flattering it.

**Step 2 — the capacity-asymmetry config. Size S to change, M to measure.** 2607.07548: scaling the
**decomposer** = +11 EM, scaling the **executor** = +2.6 EM; a 1.7B executor matches frontier at 37%
fewer tokens. Point FUSION at `classify_task` + the decomposer only; run worktree workers on a cheap
model. The only change in the sweep with a measured prior on **both** accuracy and the token bill.

**What it cannot show:** anything before ICC exists (2512.06710: n=8–32 resamples). Gate on §0.

---

## 5. Guardrails — the "100%" is honest offline and unmeasured live

**The fact in the code.** `bench/injection` runs the ledger against **stub tools with no model in
the loop**; its `undefended` arm (recomputed today: `block_rate=0.000`) is "no ledger", not "the
model alone". So the 100% attack-block is **purely the ledger, measured in isolation** — which is
honest. What is unmeasured is the **live path**, where a model may refuse before the ledger sees a
call, and 2608.08641 shows the guard's credited share on such a path runs "from none of it to
essentially all of it".

**Step 1 — the live split. Size S, some spend.** Add a live arm: model + ledger, and record for
every blocked attack *who* blocked it (a ledger block replaces the response, so the counts are
disjoint — free to recover). Report guard-only / model-only / both.

**Step 2 — authorization-equivalence pairs. Size S, hours.** 2608.29942: holding the committed
action and its effect fixed and changing only whether a value came from the user or a legitimate
tool result shifts the verdict toward "attack" in **24/24** cases. Add matched rows where the *same*
write draws its value from the user vs from a tool result. **Registered prediction: we escalate
both** — in which case the query-string rule measures provenance while claiming authority.

**Step 3 — `bench/memory_poison`: replace the regex with behavioural replay. Size M.** 2608.11392:
"a presence check is not a safety check" — a rule surviving compaction as degraded residue leads
to the prohibited action **+34 / +57 points** more often. We published that bench as a failure with
no code change; this is the honest place to fix a gate.

**Watch:** 2607.13083 (Phantom Guardrails) — a self-improving harness enabled a guardrail for a
provably nonexistent failure class in **15/60** runs. We are a self-evolving agent that can add
guardrails. Any skill-card or rule that *adds* a restriction needs a suppression-independent
acceptance test.

---

## 6. Hooks — audit, do not build

The published research is red-team: 2609.03884 compromised **all seven** evaluated harnesses (up to
92.5%, Defender 0% recall) — through hooks that bind **shell commands** to runtime events. Ours are
in-process Python (`Agent.run` callbacks, the governance wrapper); the specific exploit does not
apply. 2606.21856 states our rule verbatim — governance "should be enforced by execution hooks rather
than entrusted to the LLM", **+48.9 pp** — and 2605.13471 names the sleeper-channel shape (untrusted
input persisting as memory / skill / **cron job** and firing later through a different surface).

**One audit, size S:** the hook-*update* trust path, and the cron and skill-card ingress as sleeper
channels. No construction.

---

## 8. Proactive questioning — the clause exists; the detector does not

**The fact in the code.** `chimera/core/agent.py:65-80`, inside `DEFAULT_SYSTEM_PROMPT`: *when the
request has no technology, no audience and nowhere for the result to live, ask the questions that
actually block you, at most three, and stop.* Backed by a paired observation (the bakery request
that produced five files nobody asked for). Delivery exists: `governance/pending.py` — durable
question, silence refuses. **What does not exist is detection as a separate pass** — the model
decides inside the same generation that would otherwise start building.

**What the literature measured.** 2603.26233 (underspecified SWE-bench Verified): an
uncertainty-aware scaffold that **decouples underspecification detection from execution** reaches
69.40% with well-calibrated question spend. 2606.27669: detection and clarification are distinct
capabilities that do not co-occur; searching instead of asking often loses to guessing. And the
pair that decides the design: 2509.09309 (N=761, N=571 preregistered) — **unsolicited** help raises
user self-threat and lowers acceptance; 2510.18880 (N=163, blinded) — **solicited** context-seeking is
welcomed even when it defers the answer. **The trigger condition is the experiment.**

**Step 1 — an underspecification detector as a pre-loop gate. Size M.** A separate, cheap pass that
scores ambiguity before `AutonomousAgent.run` starts, delivering through `pending.py`, **flag-gated
and off by default** (recall the learn→use disconnect: skill cards shipped off and the wire sat cut).
Metrics from 2603.00187 / 2607.00711: Average Turns to Clarify, Key Question Coverage, turn-discounted
question rate. Venue: the underspecified SWE-bench Verified variant.

**What it cannot show:** the annoyance cost, which is a user study, not a bench. Report the question
rate beside the success rate and say the cost half is unmeasured.

---

## Venues — the band problem has two real answers now

`bench/learning_lift` failed three times to author a suite in a 40–60% band (always 84–92%), and
`bench/terminal_bench` floored at 7.5%/2.5%. Two external suites change that:

| venue | status | what it gives | limit |
|---|---|---|---|
| **ArbiGraph** (2607.20764) | `pavelgolikov/ArbiGraph`, **MIT**, Python, pushed 2026-09-07 | difficulty as **dials** (topology JSON, distractors, value type), exact automatic verification, math / GSM / Python-tracing | needs our own runner (theirs assumes local GPU); a lift here is about **context retention**, not repo work |
| **Harness-Bench** (2605.27922) | `Qihoo360/harness-bench`, 97★, 5 MB, **no license file** | **106 sandboxed offline tasks**, model/budget/protocol fixed while the harness varies — the venue for the 2^k factorial (2605.05716) | no license ⇒ run privately, cite, do not redistribute; verify task quality before trusting |
| **LoopsBench** (2608.00267) | running; 8-task pilot | third-party ruler with a middle (1/8 → 12.5%) | 2h budgets, US$3/task; 2 of 8 tasks ungraded once |

**The scaffold A/B**, as a 2^k factorial over diff gate / stagnation detector / progress ledger /
escalation (2605.05716: all-in is consistently suboptimal, 56.3% submodularity violations), belongs
on Harness-Bench — repeatable, with a deterministic oracle, and it **dissolves the band problem** instead
of solving it. **Offline is not free**: the agent's model calls cost the same as anywhere (measured
2026-09-08: US$ 0.54 per solve), only the grading is. And the factors must act inside one attempt:
the progress ledger and diff-feedback fire only on a retry, so under `--max-attempts 1` they are inert
and belong to the recovery sweep (item 1+7 step 2). The pre-registered design
(`bench/harness_bench/PREREGISTRATION.md`) is repo-map x checklist x planner, 2^3 full, k=3.

---

## Sequence and cost

| # | item | size | spend | depends on | registered expectation |
|---|---|---|---|---|---|
| 0 | k-run protocol: pass^k, mechanism-active, ICC, 3 seeds | S | none | — | — |
| 1 | retry-contamination probe from the 88 receipts | S | none | — | 3rd attempt ≈ 0 recovery (observed 0/11) |
| 2 | interface preflight + re-read `tool_defer` / `parallel_tools` | S | none | — | some adapter shows emitted ≠ parsed |
| 3 | live guard/model split + authorization-equivalence pairs | S | small | — | we escalate both |
| 4 | readout-vs-input test (reorder/duplicate gold) | S | small | 2 | ≤23% recovery |
| 5 | stand up ArbiGraph runner; calibration ladder to ~50% | M | small | 0 | a topology that lands in 40–60% |
| 6 | compaction on/off × tier on ArbiGraph long chains | S | small | 0, 5 | null at production tier |
| 7 | failure classifier + class-targeted recovery | M | none to build | — | — |
| 8 | matched-budget sweep: retry / replan / targeted / deterministic | M | real | 0, 5, 7 | targeted ≥ retry at budget 1 |
| 9 | RefactorPlatform question on `sequential_write`, same repair budget | M | real | 0 | ~0 tasks rescued by delegation |
| 10 | 2^k scaffold factorial on Harness-Bench | L | real | 0, 2 | at least one component negative-marginal |
| 11 | underspecification detector, off by default | M | small | 0 | question rate up, success up on the underspecified variant |
| 12 | TSCG on the weakest FUSION member | S/M | small | 2 | gain on the small model, ~0 on deepseek |
| — | hook-update / sleeper-channel audit | S | none | — | — |

**Not on the list, on purpose:** schema-constrained decoding (two measured nulls); a general hook
surface (red-team literature, we are on the defended side); uncertainty-gated retry (2608.14659:
harmful in every configuration); the sqlite-vec semantic memory (gated behind a flat-file baseline —
2609.00006 found none of eleven production harnesses uses embeddings for code retrieval).

**The one caveat on the whole plan.** 2605.28591: a model trained on documents *describing* benchmark
structure shifts by >20 pp, and the shift **persists on responses with no verbalised awareness**. Every
public venue above is subject to it, pre-registration does not control for it, and paired arms are
equally contaminated (§2aa). The cheapest test, and the only one that can invalidate work already
published: run a pilot twice, canonical framing vs re-skinned as an ordinary repo request, and check
whether the difference exceeds the noise floor from §0. It is item 0.5, and it should run before 10.
