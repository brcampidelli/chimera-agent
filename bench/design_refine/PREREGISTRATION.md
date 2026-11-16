# Pre-registration: does a Design refinement that sends only the target screen lose anything?

**Registered 2026-11-16, before any paid call.** Audit item P1-06 (`study/auditoria-2026-11/PLANO-FINAL.md`; findings a13-03, a13-04, a13-10). Owner's ceiling for this item: **US$ 5.00**; the spend ledger in `run.py` stops the run at a conservative **US$ 4.00**, and the expected real spend is under US$ 1.

## Why

Every Chimera Design refinement quotes the HTML source of **every** artboard of the design (each up to 24 KB) plus the operations guide, including the legacy `node.*` operations the HTML prompts tell the model not to use (`chimera/design/propose.py`, `refine` and `html_sources`; `prompts.HTML_OPERATIONS_GUIDE`). A design of five screens therefore costs on the order of 20 thousand input tokens per adjustment to change one element of one screen (the audit's estimate, from reading, not measured: this bench measures it). The HTML rules are also written in three places (`HTML_GENERATE`, `HTML_REFINE`, `HTML_DESIGN_TURN_NOTE`).

The change under test, **arm B**:

1. `refine` quotes the source of the **target** artboard in full and, for every other artboard, only its id and name. The target is read from what the request already carries: the clicked HTML element, the selected elements, the comments attached to elements, or an artboard the request names (exact, case-insensitive name or id). When the target cannot be found the model gets no source and may answer `{"read": ["<artboard id>"]}` with no operations; the sources it names are sent in **one** follow-up call. A design with a single HTML artboard sends it, as before.
2. The operations guide leaves out the element (`node.*`) operations and the element schema for a document that has no legacy elements (generation and refinement alike).
3. The rules every HTML prompt repeats (tweak syntax, the script block, how to write the artboard) are defined once in `chimera/design/prompts.py` and composed into the prompts that use them. **No text of `HTML_GENERATE` changes**; this is checked byte for byte by a test.

**Arm A** is `b53dd4fe` (`origin/main` at registration), byte for byte: its prompt file is read from git by `run.py` and its sha256 is checked (`8b858d4e…4d04629`) before every run.

## The ruler comes first (a13-10)

Nothing in the repository measures whether a Design prompt change makes the model write worse HTML. So the first deliverable is the ruler, `bench/design_refine/` (this directory), and it has two parts:

**R1, generation.** 30 briefs (`briefs.py`, frozen here), each asking for one screen, generated with both guides: arm A (the baseline prompts) and arm B (`propose.propose`, with the lean guide). Metrics per generated artboard:

| metric | definition | instrument |
|---|---|---|
| `valid1` | the model's **first** answer parsed, validated and applied in a dry run (`attempts == 1` and `ok`) | `ProposalOutcome` |
| `contrast` | share of **measured** artboards with at least one text element under WCAG AA | `chimera.design.html_check` (the product's checker); an artboard it declines to read is *not measured* and leaves the denominator |
| `overflow` | share of valid artboards whose content box is larger than the artboard's box (width; and height for `fixed`) | Chromium render of the PNG export's page, `scrollWidth` / `scrollHeight` against `clientWidth` / `clientHeight` |

**R2, refinement.** 30 multi-screen designs (10 of 3 screens, 10 of 4, 10 of 5; `briefs.py`) built by each model with arm A's generator, one refinement request each (`briefs.refinement`: six small edits, two of every three naming the screen, one of every three giving only its place, "the last screen"). Metrics per refinement:

| metric | definition |
|---|---|
| `success1` | **an `artboard.edit_html` applied on the first attempt:** the proposal applied cleanly in the dry run on the model's first answer (`attempts == 1`), contains at least one `artboard.edit_html` on the target artboard, and contains no `artboard.set_html` on it |
| `tokens_in` | provider-reported prompt tokens summed over **every** call of the adjustment (a re-ask and arm B's read round included) |
| `collateral` | operations on an artboard other than the target (reported) |
| `read_rounds` | how often arm B asked for a source (reported; the mechanism's activity, §2r) |

## Design

- **Items.** 30 briefs × 3 repetitions × 2 arms, for each of **2 model families**: `openrouter/deepseek/deepseek-v4-flash-0731` (DeepSeek) and `openrouter/z-ai/glm-5.3-flash` (Zhipu). The models write their own corpus (each refines designs it generated) at the product's temperature, 0.2.
- **"Seeds".** The gateway has no seed the routes honour, and the product pins `temperature=0.2`. The three "seeds" of the plan are three **independent repetitions** of each (design, arm), each a fresh sample. They are not replicas of a deterministic call, so the dispersion between them is read as sampling variance of the model, not as seed variance of a trainer.
- **Pairing.** Arms are paired on (design, repetition). Order alternates by `(brief index + repetition) % 2`, so neither arm is always first.
- **Pilot.** The first two briefs of the R1 run are the cost sample: after them the spend per call is measured and the total projected; if the projection passes the ceiling, `n` is cut **in this file** (an amendment, dated) before continuing. The pilot items are part of the data.
- **Provider.** Not pinned (the lanes of these slugs cost cents); every record keeps the provider that answered.
- **Corpus failures.** A screen is regenerated up to 4 times when the first-proposal fails validation; a design that still has no screen is dropped and listed in `corpus_failed.jsonl`, and the dropped count is reported with the result. A dropped design is dropped for both arms.
- **Halts** (a provider exception) are kept and counted separately; a halt leaves its pair out of the paired statistics (PROTOCOL §2) and is reported.

## Decision rule (written before the data)

**Gate 1, refinement keeps working.** Per model: the pooled paired difference in `success1`, arm B minus arm A, over the 90 pairs, has a point estimate of **at least −5.0 percentage points** (the plan's rule, literally).

**Gate 2, it sends less.** Per model: the total of `tokens_in` of arm B is at least **50% below** arm A's, over all designs **and** in the 3-screen stratum alone (the plan says "three or more screens", so the smallest design must clear it too).

**Gate 3, the ruler's guard on the lean guide.** Per model, on R1: `valid1` of arm B is at most 5.0 pp below arm A's, and the `contrast` share and the `overflow` share of arm B are at most 5.0 pp above arm A's (point estimates).

| result | what happens |
|---|---|
| all three gates hold **in both models** | adopt: `LEAN_REFINE = True` stays on in `chimera/design/propose.py` |
| Gate 2 holds, Gate 1 or 3 fails in one model | **not adopted**: `LEAN_REFINE = False`, the code stays, the number goes in its docstring; the result goes to the owner with both models' numbers |
| Gate 2 fails | the mechanism did not save what the audit estimated; reported, and `LEAN_REFINE = False` |
| Gate 1 fails in both models | a null with the interval stated; the plan's "does not fall more than 5 pp" is not met and the change is not adopted |

Nothing here is loosened after the data. A retraction is published with the prominence of the claim (AGENTS.md).

## Predictions (written so they can be wrong)

- **P1.** Arm A sends about 3× the input of arm B on 5-screen designs and 1.8–2.4× on 3-screen designs; the pooled reduction is 55–70%, and the 3-screen stratum is **between 42% and 58%** (it is the one at risk of missing Gate 2).
- **P2.** `success1` of arm A is between 55% and 85%; B − A is within ±5 pp.
- **P3.** Arm B asks for a source (`read_rounds`) in at least 70% of the ordinal requests and in at most 15% of the named ones.
- **P4.** `valid1` of the generation arms is within ±5 pp of each other; the node-operations removal does not change contrast or overflow.
- **P5.** Collateral operations (edits on a screen that was not asked) are rarer in arm B than in arm A.

## PROTOCOL.md, rule by rule

**§11 (the interval).** Two proportions on the same items: the pooled readings print the Bonett-Price interval and the exact McNemar p (`bonett_price_paired`, `mcnemar_exact`). Because the three repetitions of one design are not independent (§8, the project's ICC is 0.706 for replicas), the **cluster-aware reading is the per-design mean difference over the 30 designs with the one-sample t interval** (`mean_t_interval`), printed beside it; if the two disagree about whether the interval excludes zero, both are reported. Token totals: the per-design difference with `mean_t_interval`, and the ratio of totals as the point estimate. A percentile bootstrap is not used.

**§12 (margin).** The plan's rule is a point-estimate rule (−5 pp). With 90 pairs and a discordance near 20% the half-width of the paired interval is about 8 pp, so **non-inferiority at 5 pp cannot be shown at this n**, and no equivalence is claimed. The report says "not worse by the plan's point rule" when Gate 1 holds, and says "shown non-inferior at 5 pp" only if the 95% Bonett-Price lower bound is above −5 pp. The smallest effect the run can see is printed beside the result.

**§13 (controls).**
- *Trivial-agent arm:* applies. `run.py --selftest` shows that an answer with no operations, a whole `artboard.set_html`, an edit on another artboard and a re-asked answer each score **0** on `success1`. A do-nothing model cannot pass Gate 1.
- *Instrument check before the first paid call (§2q):* applies and is run: the contrast instrument flags `#cccccc` on white and passes `#111111`; the overflow instrument flags a 1200 px child in a 390 px artboard and a taller-than-fixed artboard, and does not flag a clean one or a long `fill` page; targets of the 30 requests rotate over at least 4 positions. All pass (`--selftest`) before any call.
- *Grader hijack probe:* not applicable. The graders are deterministic functions of the proposal; the model writes no file the grader reads.
- *Placebo / same-length arm:* not applicable. Arm B **removes** text and adds one short sentence that says how to read a source; the question is whether sending less loses accuracy, not whether an instruction helps. The one added sentence is covered by `read_rounds`, which says whether it acted.
- *Random arm, counterfactual judge, rule-withdrawn arm:* not applicable (no selection mechanism, no judge, no rule asked of the model that it would follow anyway).
- *Mechanism-active scoring (§2r):* `read_rounds` and the share of designs where the omitted sources were really omitted are reported; an arm whose mechanism never acted is "not measured", not 0%.

**§14 (model scope).** B removes context, which is the kind of change §14 names, so it is **not** adopted from one model: Gate 1, 2 and 3 must hold in the DeepSeek **and** the Zhipu family, a second family, not a second size. A result is labelled "measured on deepseek-v4-flash-0731 and glm-5.3-flash". This bench does not say anything about the model the product defaults to (`gpt-6-luna`) or about Anthropic models; if the owner's default is one of those, the numbers here are indicative only.

**§15 (perturbations).** The contrast B vs A is read once under each of: **model family** (2), **request kind** (named / ordinal), **screen count** (3 / 4 / 5) and **arm order** (A first / B first). It is "resolved" in a stratum when the point rule above holds in that stratum; a contrast that holds pooled and not in a stratum is reported as **not robust** with every reading beside it. Strata with 10 designs are descriptive, not decided on.

## What this cannot show

- **Pixels.** Contrast and overflow are checked on the generated artboards; whether a refinement *looks* better is not measured, and no judge reads a rendering.
- **The product's default model and the live app.** The calls use the same prompts, validators and dry-run apply the API uses, but not the HTTP layer, the desktop's selection state or a person's clicks; `html_selection` and comment targeting are covered by unit tests, not by this bench.
- **A design that grew over many sessions.** The corpus is built in one pass; real designs have comments and rewrites.
- **Tokens as dollars.** Token counts are the provider's; the report prints US$ with a conservative price and an estimate at the live index.
- **Whether the ordinal read round is the right protocol.** It is one protocol; a tool the model can call for a source would be another, and is not tested.

## Cost

Calls: R1 360 generations; corpus about 30 designs × 4 screens × 2 models plus retries; R2 360 refinements plus read rounds. At the conservative price (US$ 0.10 / 0.40 per M for DeepSeek, 0.10 / 1.30 for Zhipu) the projection is US$ 2–3 and at the live index under US$ 0.50. **Stop:** the ledger at US$ 4.00, below the owner's US$ 5.00.
