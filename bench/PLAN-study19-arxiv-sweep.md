# Study 19 — the arXiv sweep of 2026-09-14, read against the three surfaces

Run 2026-09-14/15 at Bruno's request, over the same ~100 category listings as study 18, one day of
new listings later. Eleven agents by category, plus two sub-clusters (repair loops; test generation)
that the `cs.SE` agent spun off before it died on the account's **weekly rate limit** — `cs.SE`'s own
listing was re-swept on the 15th (see §7). Roughly **10,100 titles**, ~250 abstracts opened, ~20
papers read in full, every kept item checked against **this repository** before it was allowed on a
list. **US$ 0.**

Same reading order as study 18. The item list is last and short on purpose. What comes first is the
one finding that may touch every paired number this project has published, then the places where the
literature corrects our record, then what to build, then what not to.

---

## 1 · One finding that touches our own numbers, and three theses that held

### 1.1 Prefix caching changes the agent's trajectory — and we maximise it while never recording it

`2609.04748` ("Same Request, Different Answer"): with the provider-side prefix cache **off**, identical
agentic requests reproduce **bit-identically in 800/800 episodes** (bounding every other source of
nondeterminism at 0.5%). With the cache **on**, the trajectory changed in **36.2%** of episodes at
16-bit and **75.0%** at 4-bit; one server-level cache setting moves run-to-run divergence by 37.5 pp;
the cached and the recomputed path each reproduce 40/40 and still differ from each other on 14. Eighty
multi-turn tool-use episodes, two engines, four weight formats, batch size 1.

Two agents reached this from disjoint slices (`cs.LG` and `cs.DC`), and both checked the tree:

- `chimera/providers/prompt_cache.py` marks the last system message as a cache breakpoint **on
  purpose**, and its docstring says the byte-identical `WORKER_SYSTEM` prefix is shared across the
  whole worker fleet. That is the maximum-engagement configuration.
- `chimera/eval/replicated.py` — ICC(1), flip rate, `seeds_verdict` — **does not mention cache**.
- `bench/context_rot/run.py:169` and `bench/parallel_tools/run_backends.py:88` pin the provider route
  (`order`, `allow_fallbacks: False`) — **2 of ~50** bench directories. `gateway.py` pins nothing.
  `steplog.py` already records `provider` and `cached_tokens`, so the detector exists and is unused.

If this crosses the hosted API, it sits under every paired comparison we have published, including
the seed-noise floor §2m cost real money to measure. **The honest limit, stated by both agents:** it
was measured on self-hosted vLLM/SGLang. Nothing is verified for OpenRouter/DeepSeek. So the first
action is not a fix — it is `grep cached_tokens` over stored `runs.jsonl`, then one already-published
paired arm re-run with the cache pinned off, and the flip rate compared. Equal item-level divergence
kills the item; unequal divergence reopens the archive. **S, a few dollars.**

### 1.2 A judge only disagrees where our effects live

Three papers, three groups, one shape:

- `2609.12191` (GAUGE, 25 agents, six providers): decision disagreement **<1%** on far-apart reward
  pairs, **31%** on close pairs — and 57.5% of conversations a blind panel rated "satisfied" failed the
  customer's task.
- `2609.12439` (EMNLP 2026): the strongest anti-citation debiasing prompt takes "wins for the worse-cited
  answer" from 50.5% to **0%** — and converts human-validated moderate-gap decisions into **ties**
  before the strict endpoint. Decoupled judging recovers 96.5–100% of resolution.
- `2609.05401` (ROBORMBENCH, 2,390 trajectories, 21,673 verified paraphrases, 18 VLMs): paraphrasing
  the **instruction** without touching the judged artefact flips the verdict in up to **26.4%** of
  cases; score-crossing rate up to **0.607**. Anchored dedicated models: **0.034**.

What this says about us, item by item, is in §2. The thesis: "close arms" is the regime of **every**
factorial null we have published, and our judge-based numbers carry no resolution figure beside them.

### 1.3 Detection still does not work; the number that matters is the false-positive rate the abstract hides

Held from study 18, now with the abstracts doing the hiding in front of us:

- `2609.10854` (MCP metadata audit): "98.9% recall" — the agent computed the precision the abstract
  omits: **94/143 = 65.7%**, so **~60% of benign tools are flagged**.
- `2609.06835` (workflow anomaly detector): "sub-1% false positive" with **no n, no recall, and no
  description of how benign and anomalous workflows were generated** — the disjoint-classes shape.
- `2609.10892` (DriftNet): methodologically the best detector paper in the lot (task-split, one-shot
  test, residual errors read by hand) — and still trained on a corpus whose world-identity regularity
  the authors themselves report. 0.983 F1 on such a corpus is the form that does not transfer.
- `2609.09865` (CGMIA, leakage detection): AUC **0.95** where the leak was fabricated by fine-tuning,
  **0.62–0.65** on real pre-training leakage; evaluated only 50/50, FPR never reported alone — at 5%
  prevalence about **93%** of what it flags would be false.
- `2609.12001`: a deterministic resolver of obfuscated commands stalls at **52%** on the authors'
  released 64-case set. Our denylist measured **60%** bypassable on our own corpus. Two independent
  measurements now say lexical/deterministic command resolution stops near half.

### 1.4 The cheap deterministic check keeps beating the smarter judge

- `2609.11646` (GeneralQPP): 24 non-lexical score-shape features predict retrieval failure at
  **AUROC 0.856 in 2 ms**, against a local Qwen3.5 judge at 0.649 — and the honest number is the
  leave-one-domain-out **0.706**, which is what to expect on our run history.
- `2609.07162`: nine LLM monitors average **60.9%** where a **20-line membership check does 100%**;
  a single-trace monitor is formally capped at ½ + ½·TV, and non-interference, sandbagging and
  eval-awareness are **2-safety hyperproperties** that only two executions can witness. Two frontier
  judges certified the authors' earlier benchmark as sound while a sign test found directional bias
  (p = 2.7e-5) that invalidated three of their findings.
- `2609.11957`: syntax + binary-existence preflight on shell commands catches **half of invalid
  commands at zero false positives** (the flag check is what produces all 10% of FPs); anchored edit
  appliers mis-apply silently **1 in 8,320**, line-anchored ones corrupt **99.1%** under a one-line
  shift.

---

## 2 · Where the literature corrects our own record

Ordered by how much of what we have published it touches.

| # | ours | the correction | source | cost to settle |
|---|---|---|---|---|
| 1 | Every paired arm, seed floor §2m | Cache state is a confound we neither pin nor record (§1.1) | `2609.04748` | S, few US$ |
| 2 | `harness_bench` "null with power — simplify" | The aggregate may hide **opposite-sign strata**: repo tasks −9.0 pp vs contest tasks +23.7 pp, p = 0.003 by label permutation. Our factorial has **no task-type field** anywhere (`write_arms.py`, `read_results.py`, `results/`). Per-solve rows are in `~/hb-driver.jsonl`. With 23 tasks the honest expected outcome is "~11 per stratum, no power" — which still has to replace "simplify" in `RESULTS.md`, because that word reads as universal today | `2609.11987` | S, US$ 0 |
| 3 | `judge_blind_prose` "source-faithful and unbiased, 0/180" | A bias number with **no resolution number beside it**. 0% bias is exactly what destroying discrimination buys — the judge stops separating and returns ties. Add tie/abstain rate and resolution retention on known-gap pairs. If resolution holds under blinding, our 0/180 gets **stronger**, not weaker | `2609.12439` | S |
| 4 | Every judge-scored bench | Report judge disagreement **as a function of arm separation**; refuse a reading whose arms fall in the close band. Our ICC(1) 0.527 (panel) and 0.706 (arms) predict disagreement blows up exactly where our effects are | `2609.12191` | S, votes already stored |
| 5 | Batched grading, §2t/§2z | An LLM auditor's recall goes **50% single-doc → 60% small batch → 2.8% large batch**, failing by *confident fabrication*, not degradation. We already know batch size changes bf16 generation; this is an independent second reason. Re-grade one stored round at batch 1 from the stored final | `2609.09696` | S, US$ 0 |
| 6 | "Model X scored Y" in every RESULTS | The score belongs to the **serving route**, not the model id: pipeline config alone moves scores by >80 pp across 8 benchmarks; one system moved 77.38 → 82.54 between routes, CI [0.11, 10.60]. We record the **price** of `deepseek-chat-v3.1`'s two routes and not which one produced a number | `2609.08765`, `2609.10494` | S — check whether the route is in the run JSON; if not, record it |
| 7 | pass@k anywhere, §2k | pass@k for k > n is **not identified** — counterfactuals with n=16 leave failure at k=1,000 ambiguous by factors of **1.5 to >2,600**. Our 17.4 pp "IID overestimates pass@3" probably *understates* the ambiguity. `retries.py`'s ad-hoc bracket can be replaced by the exact identified interval (a small LP on per-task counts) | `2609.09245` | S, documentary + one function |
| 8 | Design effect, k=3 buys 1.24 | External ICC **0.530 [0.500, 0.560]** matches ours — but dependence **varies by answer form**. Our pooled ICC is an average over families that behave differently; stratify `bench/design_effect/measure.py` by task family | `2609.06386` | S |
| 9 | Panel = 1.46 independent votes | Two cautions. (a) Vote-agreement correlation and accuracy-over-items correlation are correlations over **different index sets** and, absent geometric restrictions, neither bounds the other (a theorem, by analogy from signal/PnL correlation) — compute both from the stored panel logs. (b) `2609.11428` measures the same phenomenon at n=17,055 (7 models → **2.58 effective**) and adds a per-model **reliability selector** that recovers 67.7% of the plurality→oracle gap, which we do not have | `2609.09588`, `2609.11428` | S |
| 10 | `SE·√2` for two-arm differences, §2x | We use **Newcombe** (`anytime.py::proportion_diff_ci`), not Wald — the paper's only exact comparison is against Wald, so the elliptical interval is **not adopted**. What remains worth an hour: run their exact enumeration on **our** (n, p) grid and see whether Newcombe under-covers where we live. Both assume independent binomials; our designs are paired | `2609.10865` | S, then closed |
| 11 | 2PL IRT by JML (`bench/irt`) | Wald tests on ability differences need **covariance clustered by task root**; unclustered SEs are too small. The 3PL guessing floor does **not** transfer (a random patch does not pass a test suite; our floor is ~0 and the 2PL is right) | `2609.09372` | S |
| 12 | Taint is write-once | **Verified, not a gap**: the only `_tainted = False` is the constructor; `publish_tainted()` is monotonic. The paper shows deletion leaves leakage unchanged and instructed forgetting collapses under elicitation (Leak@probes = 1.00). Corroboration; reopen only if someone adds a "clear taint" affordance | `2609.04875` | — |
| 13 | Report coverage as a trouble signal | On **5,851 real developer sessions** (355,942 tool calls), the agent's self-report covers ~1 action in 11 — and that ratio **does not** depend on whether the session needed human correction. A null with power that explains from outside why claim-vs-diff stalls at 0.6643. The new signal: reports drift toward the **declared plan** as execution diverges from it — measure claim↔plan vs claim↔diff on our traces | `2609.12205` | S measure |

Two more that correct **habits** rather than numbers:

- **Frozen corpus, loose memory** (`2609.12766`, 216,000 rows): memory that sees the future inflates
  recall by 2.6–5.2 points **and hides 33–48% of the damage memory actually causes**. Rule: version
  memory state with the corpus in any replay over `runs.jsonl`.
- **Compaction benches that keep the question in the prompt** (`2609.05767`): hide the question until
  after compression and an incidental fact survives **0–1%** of the time vs 97% with full memory. A
  compressed cache is an inference-reuse mechanism, not a persistence layer.

---

## 3 · The shortlist — cheap, new, verified against our code

Ranked by (what it changes) × (how little it costs). Everything here was checked against the tree by
the agent that proposed it; where the agent could not check, it says so.

### Tier A — measure first, US$ 0 or near it

| # | item | surface | the concrete step | number behind it | effort |
|---|---|---|---|---|---|
| A1 | **Cache confound probe** (§1.1) | bench | `grep cached_tokens runs.jsonl`; re-run one published paired arm pinned vs unpinned; compare flip rate | 36.2% / 75.0% divergence; 0/800 with cache off | S |
| A2 | **Factorial by task family** (§2 #2) | `harness_bench` | Declare the partition, re-read `hb-driver.jsonl`, print per-family effects and the interaction CI; write the honest "no power" if that is what comes out | −9.0 vs +23.7 pp, p = 0.003 (theirs) | S |
| A3 | **Resolution beside bias** (§2 #3, #4) | `judge_blind_*`, fusion judge | Tie rate + resolution retention on known-gap pairs; disagreement stratified by arm separation from stored votes | 31% vs <1%; ties from debiasing | S |
| A4 | **Batch-1 re-grade** (§2 #5) | any LLM grader | Re-grade one stored round at batch 1 from the stored final; recall flat = clean | 50% → 2.8% | S |
| A5 | **Route in the artefact** (§2 #6) | all benches | Confirm/record the resolved provider route per call | >80 pp from pipeline config | S |
| A6 | **The 64-case obfuscation corpus** | `bench/denylist_bypass` | Run our denylist + capability narrowing on `2609.12001`'s released set — the first out-of-sample test of our 60% | resolver 52% | S |
| A7 | **Wrapper-invariant perturbation arm** | `bench/perturbation_floor`, governance judge | Add content-invariant wrappers ("educational course" framing etc.) around the item, not the command; compare to the replay floor | 19.9% flips vs 0.5% floor (GPT-4o-mini); prompt rewrite cut it 10× | S |
| A8 | **Surface-feature audit of our own corpora** | `judge_blind_prose`, `governance_judge` | A 6-feature logistic classifier over the corpus; abort if it beats chance — the `false_success` leak (0.93 → 0.60) as a construction pre-condition | TruthfulQA classifier well above chance | S |
| A9 | **Profit-clause escalation probe** | production prompt, `bench/false_success` | Same tasks ± the business-mandate sentence; measure escalation rate, not accuracy | +6.8 pp dismissals, −13.9 pp escalations, 3,600 paired trials, 8 models | S |
| A10 | **Regret over significance, retrospectively** | `bench_ab.py`, `paired.py`, PROTOCOL | Re-score archived adoption decisions by regret on held-out items; if the significance rule already sits near the ex-post optimum in our regime, the item dies | 552 real A/Bs: t-test regret ≈ deciding at random; only 7.6% significant | S |
| A11 | **Report↔plan vs report↔diff** (§2 #13) | false-success detector | Compute both similarities on stored traces; a signal that does not exist in the tree | 5,851 sessions | S |
| A12 | **Our own α/β for the repair loop** | verify / diff-gate | From `runs.jsonl`: per iteration, P(pass→fail) and P(fail→pass). The stopping threshold `α/(α+β)` then comes out measured, not imported | blind loop: β = 29.3%/iter, green state half-life 2.0 iterations | S |

### Tier B — small code, real gap

| # | item | surface | the concrete step | number behind it | effort |
|---|---|---|---|---|---|
| B1 | **Wire the compaction summariser** | `core/context_budget.py`, `core/summarise.py` | The rule-preserving summariser is written and **passed by no production caller**. Wire it; measure per surface whether user-stated constraints survive compaction | omit constraints → **87%** boundary crossings; preserve → **0%** (1,800 trajectories, full factorial) | S/M |
| B2 | **Shell preflight** | `chimera/tools/shell.py` | Syntax (`bash -n`/`shlex`) + binary existence only — the oracle-exact half; leave the flag check out (it is all the FPs). Measure FPR on our logged commands first | 95.8% @ 10.0% FPR overall; syntax+binary: half the errors at **0 FP** | M |
| B3 | **Bilateral diff-gate** | verify, spec-test generator | Run the generated test against the **pre-patch** tree and reject a test that already passes there; compute TPR/TNR of generated tests over our labelled patch history before letting them govern; keep the round-0 patch when the test does not qualify | mis-aligned tests: **−3.9 pp** vs no test (SWE-bench Verified, n=500); Base→Gold quality 22.2% → 62.2% | M |
| B4 | **FTR vs FDR as two metrics** | spec-test bench | Separate "the input reaches the fault" from "the assertion detects it"; add the false-alarm side (run the generated oracle against the correct tree and count false failures) — the side the paper never measured | FTR 0.32–0.62 vs FDR 0.027–0.080; 233,300 implementations | S |
| B5 | **Retention regression for the evolution loop** | `auto_evolve` | Tasks a prior release passed, re-run after N skill additions; count regressions. Never measured | the question is `2609.04280`'s (no numbers yet); memory-migration asymmetry +9.91/−13.28 pp by direction (`2609.05339`) | M |
| B6 | **Eviction restore-counterfactual** | `bench/memory_prune`, `memory/manager.py::prune` | Force-inject the gold fact at read time and re-run the reader; classify each failure recoverable / irreversible. If restoring recovers ~0, pruning is not what costs us and the semantic-recall plan loses its justification | irreversible share 0.60–0.73 at 80k, **1.00** at 8k | S/M |
| B7 | **Approval cadence policy** | desktop asks | Batch or defer low-consequence asks by the four criteria (consequence, user activity, continued value, need for confirmation); measure exposure (the 88.5%/60.4% metric) before shipping | deferring **did not cost trust** (n=41, within-subject) | M |
| B8 | **Decoy corpus for the secret gate** | gitleaks / `redact.py` | Plant credential-lookalikes to number **over-redaction**; add mutation-localisation as a chain test | 14/14 types redacted, 9/9 decoys kept | S |
| B9 | **Reviewer-family axis + inertia test** | `bench/review_judge` | Same-model vs cross-family reviewer; count how many items the reviewer actually changes (a reviewer that changes nothing reads as "safe" today) | self-review false-rejects **35%** vs cross-family 2% (n=100, one domain, solo author) | M |
| B10 | **Tool-equivalence in scoring** | tool/skill benches | Audit whether our tool-selection score admits functional equivalents; one-to-one annotation inflated fine-tuning gains by 30–47% | 67.9% of sub-queries admit alternatives (7,360 queries) | S audit |
| B11 | **Retrieval sufficiency gate** | agent recall | 24 score-shape features, 2 ms, offline; expect ~0.71 on a new domain | 0.856 / 0.706 LODO | S |

### Tier C — protocol rules (a sentence each in `bench/PROTOCOL.md`)

- **Second execution or say so** (`2609.07162`): mark which metrics are 2-safety hyperproperties and
  cannot be certified from one trace.
- **Stratified allocation for rare events** (`2609.04420`): when the class of interest is rare and
  costly, equal — not proportional — allocation is the default; hand-picked "representative" clusters
  have no unbiasedness guarantee (exact identity).
- **Bimodality before means** (`2609.09257`, 299 repeated trainings): a configuration can pass a mean
  test decisively while its 5% quantile sits an order of magnitude below chance. Check replicas for
  bimodality before reading an average; add an *out-of-present-scope* metric (outputs naming a
  tool/skill no longer in the deployed catalogue).
- **Attacker effort is an axis** (`2609.04495`): "60% bypassable" becomes "≥60% at the effort we
  spent"; ASR scales with attacker search compute.
- **Attempt vs completion** (`2609.09404`): report injection *attempted* separately from *completed*;
  we measure only completion, with attempt = 1.0 by construction.
- **pass@k only for k ≤ n** (`2609.09245`).
- **Memory versioned with the corpus** (`2609.12766`); **compaction benches hide the question**
  (`2609.05767`).
- **Holm within family** (`2609.12227`) for the multiplicity the evolution loop already carries.
- **Holdout relative to the disclosed corpus, not the date** (`2609.10357`): a temporal cut removes
  memorisation of the window, not familiarity with the domain — relevant to any SWE-bench claim.
- **Empirical-null FDP instead of Bonferroni** (`2609.06509`) is worth a pre-registered simulation at
  our m ≈ 10 — and nothing more until it wins there.
- **Sequential e-process for monitors** (`2609.05752`, Ramdas): valid at every n; a candidate to replace
  "pre-registered n + Wilson at the end" where we *watch* rather than gate — after a false-alarm test
  across a model change on a history where nothing changed.

---

## 4 · Do **not** build these — each is a measured failure or an unmeasured claim

| what | why not | source |
|---|---|---|
| Narrow the egress rule by "does the query carry tainted text" | Our own probe (2026-09-14): `_tainted_span` answers "does the text contain the fetched content"; a URL is shorter than its page, so attack **and** benign rows all read `False`. Not a narrowing — a deletion; `asr_exfil` returns to 0.500 | `bench/injection/RESULTS.md` |
| Any injection/anomaly **detector** as the layer that holds | §1.3; FPR hidden in every abstract that has one | `2609.06835`, `2609.10892`, `2609.10854` |
| Rewriting the command matcher expecting it to hold | 60% (ours) and 52% (theirs) — resolution of obfuscated commands stops near half | `2609.12001` |
| Governance scaffold as a capability gain | 24→40% on one model, **0** on another; n = 5–25, single trials; below our noise floor | `2609.05531` |
| Cleaning taint by sanitisation/redaction | leakage unchanged after deletion; instructed forgetting collapses (1.00); source redaction still acts in 80% | `2609.04875` |
| Confidence-gated verification ("call the expensive check only when unsure") | toy, one model, one run, **no noise floor on any accuracy**; mechanism sign flips between benchmarks; the always-on ablation beats it in the authors' own appendix | `2609.12489` |
| MIA-based contamination detection | AUC 0.62–0.65 on real leakage; ~93% false at 5% prevalence; needs a shadow fine-tune we cannot do on closed models | `2609.09865` |
| Mutation score as a verify gate | Â₁₂ ≈ 0.5 against statement/branch/random; costs far more, separates nothing | `2609.09315` |
| A blind repair loop (no tests inside) | β = 29.3%/iteration for the strongest tested model; the steering-vector "fix" is a volume knob, not a discriminator | `2609.10123` |
| Reward-hacking "immunisation" | +5.2 under hacking vs −4.7 on clean rounds; per-channel effects not individually significant | `2609.04665` |
| Adopting "bash beats typed tools" as a fact | direction is plausible (21.8–24.5 pp, 19–72% fewer tokens) but **no CI, no seeds**; needs a paired run whose control reproduces a published number | `2609.11999` |
| Replacing Newcombe with the elliptical interval | compared only against Wald, which we do not use; conservative by construction where we lack power | `2609.10865` |
| Simulated participants as a substitute for our human studies | population-prevalence query with **no persona** beat compromised synthetic panels 9/9; the certificate measures the elicitation contract, not the individual | `2609.07305` |
| Vol-controlled sizing as a drop-in for the 2%/5% rules | maxDD halved on 60/40 ETFs with 5 bps costs — but walk-forward covers only hyper-parameters, the method choice knew the period, and the authors cite the contrary evidence; nothing transfers to intraday WIN/WDO or a 25-position copy book without a paired backtest on our own trades | `2609.07946` |
| Seasonal commodity strategies | a costed, out-of-sample null: none of 18 paired Sharpe tests survives Holm | `2609.12227` |

---

## 5 · What arrived as corroboration, and is filed as such

Not new work, but the record should say the outside world agrees:

- **Paraphrase floor** (#463, BLOCK→REVIEW on two spaces) ← `2609.05401` at three orders of magnitude
  more n; `2609.08236` (wrapper flips 19.9% vs 0.5% floor, 90% human-confirmed as judge error).
- **Correlated panel** (#466, ICC 0.527) ← `2609.06386` (0.530 [0.500, 0.560]), `2609.11428`
  (2.58 effective of 7), `2609.05444` (peer-review collusion: no detector above F₁ 0.322; the defence
  that works is randomised assignment).
- **Diff-gate / verify** ← `2609.10123` (third independent validation: the cost of not having a
  gate is β ≈ 0.29 per blind iteration), `2609.04909` (~12% of inspected repairs pass the full
  developer suite and are still semantically wrong — the gate's blind spot, now with a number).
- **Content-anchored edits** ← `2609.11957` (line-anchored: 99.1% corruption under a one-line shift).
- **Learning-lift closure** ← `2609.12742` (GEPA +4.9 pp "does not separate from between-run variance",
  SkillOpt +0.1 pp).
- **Task-identity leak in `false_success`** ← `2609.11449` (detector-defined precision does not
  transfer across prevalence: predicted 0.955, measured 0.183).
- **`auditor reads the worker's conclusion` (#425)** ← `2609.05318` (conclusion-passing chains: excess
  error constant to depth M², so at depth 2–3 chaining buys nothing — a linear-regression model, kept as
  analogy with a number).
- **Multi-agent = single agent at equal cost** (study 17) ← `2609.05933` (thesis right, abstract has no
  number — marketing by the house rule).
- **Sidecar bound to 127.0.0.1** ← `2609.07115` (Ollama in the wild: 0.43–2.90% of exposed hosts
  upgrade in place over a year — a security fix reaches almost nobody unless the updater forces it,
  which routes back to the signing key).

---

## 6 · Never cite, and the shapes that produced them

- **"96% detection accuracy"** (`2609.11028`, BenchShield) without the base rate of reward hacking in
  the 31,000 public runs — 22 authors, and the number that decides is missing.
- **"−56.97 GPU-hours"** (`2609.12216`) — simulated hours; 2 authors, no affiliation, 10 pages. Only
  the 30/240 crash-recovery failure count is usable.
- **"Experiments on live routing networks show"** (`2609.10181`) — no rate, no n, no baseline.
- **"Substantially better"** (`2609.09854`) — no effect size in the abstract.
- **Table VI of `2609.09315`**: branch and statement columns identical before/after in 20/20 rows while
  mutation moves — two thirds of the table were not measured, and the printed justification is
  inverted (§2z + §2t in one table).
- **CoTT's abstract** (`2609.12489`) claims it "outperforms prior baselines across the reported
  metrics"; its own Appendix E shows the always-transductive ablation winning.
- **The DeepSeek "unchanged" row** in `2609.08149` (49.98 → 49.11 → 49.93): inside the authors' own
  noise estimate; not evidence that anti-hacking is free.
- **`2609.05975`**: n = 15 per condition, one simulated firm, "validated against the failure it was
  built to solve".

The pattern from study 18 held and sharpened: in this lot the papers with honest numbers were mostly
solo or two-author, unaffiliated, 6–25 pages, and the two largest teams (12 and 11 authors) published
abstracts with **no effect, no CI and no cost**. Author count is not evidence in either direction.

---

## 7 · Coverage, honestly

- **~10,100 titles** across the eleven slices (with cross-listing overlap): agents/LLM 1,897 ·
  ML/stats 1,117 · security/logic 452 · HCI/IR 362 · systems 371 · statistics 371 · econ/q-fin ~150 ·
  theory ~923 · vision/robotics 1,720 · pure maths 2,492 · remainder ~250.
- **`show=2000` truncates.** One agent saw "848 of 1079" on a day and paged with `skip=2000`; the
  others did not report paging. Counts above are what was seen, and may be under the listing totals.
- **`cs.SE` died mid-flight** on the account's weekly rate limit. Its two sub-clusters (five
  repair-loop papers, five test-generation papers, all read in full) completed; the listing itself was
  re-swept on 2026-09-15 — **214 of 214** for `cs.SE`, 23 of 23 for `cs.PL`, no truncation, 236 unique
  titles, 81 abstracts — see §9.
- **`cs.CE`** (computational engineering/finance) was listed by the owner and taken by nobody. Noted,
  not filled.
- **Overlap with studies 17/18:** the `recent` window covers ~08–14/09 and 137 ids from it were already
  mined; four strong candidates (`2609.04217`, `2609.07680`, `2609.06500`, `2609.08472`) were excluded
  as already in the record. New territory begins around `2609.117xx`.
- **Redundant slice:** the "remainder" agent found nine of its ten listings were subsets of others
  (`q-fin.EC` is an alias of `econ.GN`; `econ/new ⊂ econ.*`, etc.). Its coverage report is the useful
  output.
- Slices that returned **clean nulls with counts**: `cs.MM` + `cs.GR` (77, zero), `cs.CV` + `cs.SD` +
  `eess.AS/IV/SP` (1,158, zero), `cs.AR` + `cs.ET` (112, zero), `math.PR` + `cs.CG` + `cs.DM`
  (316, zero), all of pure maths except two mis-filed items.

---

## 8 · Suggested order

1. **A1** — the cache probe. Everything paired depends on the answer, and it is a grep plus one arm.
2. **A2, A3, A4, A5** — re-readings of data already on disk; US$ 0; each either closes or reopens a
   published sentence.
3. **A6, A7** — the two out-of-sample tests of our own security numbers.
4. **B1** — the summariser that exists and is not wired.
5. **B3 + B4** — the bilateral diff-gate and the two test metrics, together (same code path).
6. **A9, A10, A11, A12** — probes over stored traces.
7. Tier C rules into PROTOCOL as each Tier A item lands, not as a batch.

Nothing in this plan spends model money except A1's single arm and, later, B1/B3's paired runs.

---

## 9 · Addendum — `cs.SE` + `cs.PL`, re-swept 2026-09-15 after the rate-limit death

`cs.SE/recent` showed **214 of 214** and `cs.PL/recent` **23 of 23** — neither page truncated, so no
paging was needed here. 236 unique titles, 81 abstracts, 8 full texts. The `export.arxiv.org` API
returned 429 on the first call (the same limit that killed the first runner); the agent switched to
`/abs/` pages at 3.5 s intervals and saw no further 429. Twelve ids already present in this repo's
`PLAN-*.md` / `RESULTS.md` were excluded by grep before ranking. US$ 0.

### 9.1 What changes the ranking above

- **A2 gains a second partition.** `2609.13890` (DATS, 614 contest problems, 5 topologies, 4
  backbones, per-item McNemar with Holm): the collaboration effect is **+2.4 pp in the easy tercile
  and +21.1 pp in the hard one**. Not a contradiction of our null — a *moderator*: if our 23 tasks sit
  near the ceiling, the null is what it predicts. Stratify by the bare arm's score tercile as well as
  by task family before the word "simplify" stays in `RESULTS.md`. Caveat: one LLM sample per cell;
  the "5 seeds" are the router's cross-validation over a cache.
- **`2609.09218`** (4 scaffolds × 3 models × 15 τ-bench tasks, single trial): sign tests n.s.,
  single-trial noise of the order of the spread — **consistent with our null**, and it names the axis
  our arms already declare: who owns the critical decision, scaffold or model.
- **No paper in this slice measures repo-map/checklist/planner-style scaffolding with a noise floor.**
  Eleven "improvements" were found without one (RepoNav, XAgent, EnvPilot, AttnCompress, CrossCoder,
  GraphAHA, Code2Skill, FORGE, Ecdysis, TRAIL, GuardedAct — the last on **five** scenarios); none counts.

### 9.2 New items, ranked (verified against the tree)

| # | item | surface | what the agent found in our code | number behind it | effort |
|---|---|---|---|---|---|
| S1 | **Fabrication after a tool failure depends on whether the failure is *signalled*** (`2609.14758`) | tool results, right-hand chat | Audited: `shell.py` returns `[exit N]` + explicit truncation markers; `http.py`/`files.py`/`scrape.py`/`web.py` return `error:` on every failure; the byte cap (10 MiB) ≫ the char cap (20k), so no silent truncation. **We are in the 0.0% regime by construction** — except `http.py`'s soft-404 (`[200]` + an error page) and the right-hand finding that the model **fabricated the output of a refused command**, a *signalled* failure, which contradicts the paper's 0.0% and says the number is the model's, not the architecture's | 0.0% dishonest with `status:error`; **45.3%** with `status:ok` + unusable payload; a one-sentence `retrieval_status` requirement takes 14.10% → **0.87%** (92 improve, 1 worsens, of 688; held-out split chosen before the defence) | S — one prompt line + a regex; re-run the right-hand probe with it |
| S2 | **An adversarial issue yields a correct *and* malicious patch in 51.7%, and the test gate cannot see it** (`2609.15963`, SWEADV: 750 issues × 3 models on SWE-bench Verified) | verify, governance; PassaPro flows driven by tickets | `policy.py` reads only `Scope.COMMAND`/`ANY_TEXT` — **no rule reads a diff**; the taint ledger marks *fetched* content, not the task text. Two steps: a task from an external tracker enters `tainted`; a deterministic diff rule ("new dangerous sink with non-literal argument": `subprocess`/`os.system`, `pickle.loads`, `yaml.load`, md5/sha1 on a password, path join with input) → REVIEW, never BLOCK | post-repair Semgrep 31.6–39.6%, Bandit 10.4–13.2%, LLM judge 55.4%, combined 65.9%; injection labels are deterministic-by-diff plus a dynamic trigger harness | M; measure FPR on our accepted-patch history first — over ~10% and it goes the way of B2's flag check |
| S3 | **SWE "shortcutting" and the number its abstract hides** (`2609.06780`) | `bench/swe_bench` | Our `run_swe.py` already sterilises git history (single-branch, no tags, `gc --prune`) **with an assertion** (`rev-list --all ^base == 0`) — the git route is closed. The **network** route has no assertion: `CHIMERA_SANDBOX=auto` falls back to the host *with* network when bwrap is missing on WSL, and the pre-registration does not say which sandbox ran | exploration in 45.1–82.4% of trajectories; an originality instruction cuts it to 4.0–10.7% "maintaining strong core task performance" — **Table 1 shows Pass@1 falling 4.4–13.3 pp in 4 of 5 models**; exploration labelled by a 3-judge LLM majority with no human validation | S — grep steplogs for `curl|wget|pip install|git (clone|fetch)`; then one sentence in `PREREGISTRATION.md` naming the sandbox |
| S4 | **MCP registry drift: 40.6% of multi-version servers change what they advertise silently** (`2609.14119`, 21,643 servers) | `integrations/mcp_client.py` | The client calls `list_tools()` each session and trusts it; nothing stores a manifest digest. Store `sha256(names + input schemas + endpoint)` per configured server; on reconnect, "N tools changed since last session" → REVIEW. Matters most for `npx -y mcp-remote https://…` entries | silent drift ↔ high-severity finding OR **2.96 [2.56, 3.42]**; 4.2% redirected the remote endpoint under the same identity; scanner precision self-corrected 11.14% → ~7.6% on 414 hand-labelled findings | S |
| S5 | **The security blind spot of the test gate, with two numbers** (`2609.10548`, `2609.10762`) | `core/verify.py`, `strong_verify.py` | A deterministic L2/L3 layer (Bandit HIGH + a calibrated source→sink co-occurrence score) as a REVIEW signal on security-flavoured tasks — cheaper than the `StrongVerifier` LLM grade, which both papers say adds little | **170 confirmed silent failures** in 1,030 traces (omission 48.2%, introduction 30.6%, inadequacy 21.2%); LLM reviewer roles intercepted **none**; **14.53%** of 654 scanner-clean samples exploitable at runtime in Docker; CWE-338/916 invisible to both scanners. Purposive sampling — not a population estimate | S to measure (Bandit over stored accepted patches), M to wire |
| S6 | **A memory curator that probes the environment before writing** (`2609.11060`) | `memory/manager.py`, `memory/gate.py` | Our write path only tags provenance (`[unverified: learned from untrusted content]`, `manager.py:271-274`) — it never checks a claim against the workspace. Add a read-only probe (grep/ls/read) for claims about files/symbols before persisting; measure on `bench/memory_poison` / `memory_prune` with the "memory versioned with the corpus" rule | CLBench pass **39% → 73%**, agent cost $3.38 → $1.68, queries 8.8 → 4.7; APEX 18/18 comparisons positive; **5 seeded paired runs, 95% CI**. CLBench is 40 questions | M |
| S7 | **Reviewer habituation to agent PRs: behaviour drifts, language does not show it** (`2609.06213`, 11,429 reviews, 400 repeat reviewers, 207 days) | desktop approvals; `bench/review_judge` (B9) | (a) the desktop keeps **no approval history while governance is off** — record (timestamp, item, decision, time-to-decide) so drift is measurable at all; (b) B9's inertia test must count **changed decisions**, not comment language: four lexical metrics do not move (F1 0.485 < majority), and Granger says approval changes *before* language | approval **30.5% → 36.6%** (Wilcoxon p = 8.6e-8, d = 0.25); human-PR control flat in the same months; "agent code got better" declared as not excluded | S |
| S8 | **"Stochastic deputy": a tenant parameter in the tool schema is an out-of-scope read served 26/26** (`2609.14780`) | MCP catalogue; VPS helpers | The catalogue already applies the principle for `read_only` (enforced by the Supabase server in the URL, not by a hint). What is left out is **project scope**: the Supabase MCP lets the model choose `project_id` among the account's three projects, and `chimera_sql.py <ref>` on the VPS has the same shape. Pin `project_ref` in the catalogue URL | with the parameter validated by entitlement, **26/26** out-of-scope reads served; with the parameter **removed from the schema**, no signature can express the read | S — one URL parameter |
| S9 | **Same verdict, different actions on identical reruns** (`2609.13582`) | `eval/replicated.py` | Our replicas compare **verdicts** (ICC, flip rate); add an action-divergence metric (same verdict, different tool-call set) | 43/43 groups emit different order sets across 5 identical runs (8B, T=0.7); in 22/43 the benchmark gives the same failure verdict for materially different behaviour | S — closes on its own if A1 (cache pinned, T=0) yields byte-identical action sets |
| S10 | **Quantised artefacts "broken on arrival"** (`2609.05881`) | the local-executor choice (study 13) | Before any local A/B: the released 15-task smoke (`quantcheck`) + an independent conversion as arbiter. The §2c #7 family — runs to completion with no error | **5 of 305** official Ollama artefacts silently defective (0/164, 0/15 on two backends); two with surface statistics inside the healthy range | S, preflight |
| S11 | **SkillSeam** (`2609.13321`) | `skills/`, `evolution/card_retrieval.py` | Alias and generic-trigger perturbations over our collection, counting routing conflicts and loaded tokens — B5's regression budget from the other side. Check `card_retrieval.py` first: if retrieval is top-k by embedding rather than trigger routing, the alias mechanism does not apply | synonym alias: non-canonical routes 0/32 → 15/32; generic triggers: conflicts 3/32 → 30/32, loaded tokens 3.7×; n = 32 per probe, one system, no seeds | S |

### 9.3 Corroboration from this slice (filed, not worked)

- **Tests pass ≠ correct, under a different oracle**: `2609.13839` — 19/68 real Qiskit transpiler
  fixes (28%, Wilson 19–40%) repair failures invisible to the equivalence oracle; double-coded.
- **Route/surface moves the score** (§2 #6): `2609.08861` — API scores 3.4 pp above the chat
  interface and 2.1 pp more consistently; for ChatGPT the API↔interface gap exceeds GPT 5.3→5.4.
- **§2p, count distinct items**: `2609.10962` — 68.8% of BFCL v4 raw rows and 85.6% of UltraTool are
  exact name+description repeats; only 48.8% of a random MCP-server sample completes the handshake.
- **Design effect with a third level**: `2609.15122` — template × generation × in-program checks;
  auditing deeper per program can *raise* estimator error when template heterogeneity dominates.
- **Skill maintenance is human**: `2609.05677` — 254 substantive edits across 5 repos, 100%
  human-authored or merged; the pre-registered "rule-likeness" axis failed its reliability gate.
- **Repeated runs change the interpretation**: `2609.09182` — the stronger config changed the reading
  of the table in 41/50 questions.
- **Evidence fusion ≠ conclusion** (§2z family): `2609.13299` — a coordinator recognised a
  novelty-driven rejection and summarised it as "measured closure".

### 9.4 Never cite, from this slice

- `2609.06780`: "maintaining strong core task performance" — Table 1 has −13.3 pp Pass@1.
- `2609.11264` (GuardedAct): 87.4% recovery, −79.7% collateral — **five** scenarios.
- `2609.05571`: "93.50% vs 93.00%" — 0.5 pp, no floor.
- `2609.15877`: "96% accuracy" — precision validated by the code's own authors, no recall, commit n
  unstated.
- `2609.09769`: "7 additional issues" — no floor, no run count.

### 9.5 `cs.PL`, honestly

Nothing on shell-language parsing this week. The lead for turning the lexical denylist into a
structural check remains `2609.11957`'s syntax preflight (B2). Two neighbours: ShellVis
(`2609.11000`, live shell programming over a filesystem overlay — "execute in an overlay, read the
effects, decide" *is* structural, but the paper is qualitative), and BPFence (`2609.13930`, CCS'26:
stateful policies with formal semantics compiled to eBPF; seven case studies, only overhead measured;
Linux/kernel — the VPS sidecar's shape, not the desktop's; L).

### 9.6 Order, updated

S1 and S3 are greps over what is on disk; S4 and S8 are one-line pins; S2 and S5 are the same
verify layer (diff-scoped, deterministic) and should be designed together; S6 and S7 first require
recording what is not recorded today (memory probes; approval log). A2 now carries two partitions —
task family and difficulty tercile — and both go in before "simplify" is kept or removed.
