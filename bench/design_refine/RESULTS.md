# Results: Design refinement that sends only the target screen (P1-06)

**Verdict by the pre-registered rule: NOT ADOPTED.** `LEAN_REFINE = False` in `chimera/design/propose.py`; the code stays, the numbers are in its docstring. Measured on `deepseek-v4-flash-0731` and `glm-5.3-flash` (two families, section 14), 30 briefs x 3 repetitions x 2 arms, temperature 0.2, 2026-11-16. Raw per-call records: `results/{ds,glm}/*.jsonl`, `results/report.txt`.

Two amendments were made after the pilot and before any scored call (PREREGISTRATION.md): JSON mode off and reasoning capped, identically in both arms. Gates, margins and predictions were not touched.

## Correction: first-answer and read counts

The earlier report used `attempts` after a read reset and counted every multi-call run as a read.
Recomputed from the preserved receipts: a first-answer edit requires `calls == 1`; reads use the
recorded `read_rounds`. The previous Gate 1 and read-count claims below have been corrected. The
registered gates and raw rows are unchanged; the decision remains **NOT ADOPTED**.

## Gates (per model)

| | DeepSeek | GLM |
|---|---|---|
| **Gate 1**: first-attempt `edit_html`, B - A >= -5 pp | **+3.33 pp** (A 18/90, B 21/90) BP95 [-9.0, +15.5], per-design t [-13.8, +20.4]; pass | **-8.64 pp** (A 18/81, B 11/81) BP95 [-19.1, +2.2], per-design t [-21.1, +3.8]; **fail** |
| **Gate 2**: input tokens -50%, all and 3-screen | **-64.2%** all; 3-screen **-58.4%**; pass | -51.9% all; 3-screen **-39.4%**; **fail** |
| **Gate 3**: ruler guard on the lean guide | `valid1` +3.3 pp, contrast -2.4 pp, **overflow +22.9 pp** (30.1% -> 53.0%, McNemar p=0.0009, BP95 [+10.0, +34.7]); **fail** | `valid1` +3.3, contrast +1.8, overflow -7.0 pp; pass |

Both models must pass all three (section 14); neither does.

## Tokens per adjustment (provider-reported prompt tokens, all calls of the adjustment)

| screens | DeepSeek A -> B | reduction | GLM A -> B | reduction |
|---|---|---|---|---|
| 3 | 401,585 -> 166,946 | 58.4% | 368,258 -> 223,218 | 39.4% |
| 4 | 434,033 -> 176,617 | 59.3% | 469,645 -> 204,938 | 56.4% |
| 5 | 636,000 -> 183,235 | 71.2% | 531,785 -> 231,305 | 56.5% |
| all | 1,471,618 -> 526,798 | **64.2%** | 1,369,688 -> 659,461 | **51.9%** |

## The ruler (generation of one screen, 30 briefs x 3, both guides)

| | DeepSeek A / B | GLM A / B |
|---|---|---|
| `valid1` (first answer applied) | 92.2% / 95.6% | 40.0% / 43.3% |
| contrast: share of measured artboards under AA | 3.6% / 1.2% (n=83) | 0.0% / 1.8% (n=57) |
| overflow | 30.1% / **53.0%** (n=83) | 26.3% / 19.3% (n=57) |

Overflow is high in both arms (about a quarter to a half of generated screens spill their own box): that is a finding about the generator, not about this change.

## Mechanism and predictions

- Read round (arm B asked for a source): DeepSeek 0 of 60 named and 1 of 30 ordinal requests; GLM 0 of 57 named and 15 of 24 ordinal requests. **P3 was partly wrong**: the named prediction held, but neither ordinal share reached 70%. Reads cost one extra call and are included in the token totals above.
- P1 (3-screen reduction 42-58%): DeepSeek 58.4% (above the 58% upper bound), GLM 39.4% (below). P2 (A success 55-85%): **wrong**, A was 20% and 22%.
- Why `success1` is low in both arms: in 66 of 90 DeepSeek arm-A runs the first answer did not match the proposal schema (most often `assumptions` not a list, then `html` validation) and was fixed on the re-ask; the metric counts only the first answer, as registered. The edit itself was usually right (80 of 90 A runs ended with an applied `edit_html`).
- Collateral operations on another screen: 0 in both arms (P5 untestable).
- Named vs ordinal (descriptive, strata of 30-60 pairs): named +15.0 pp (DeepSeek) and +1.8 pp (GLM); ordinal -20.0 pp (DeepSeek) and -33.3 pp (GLM); these descriptive differences do not establish that the read round caused them.
- Corpus: DeepSeek 30/30 designs (132 generation tries); GLM 27/30 (3 dropped for both arms, 133 tries), so GLM's refinement n is 81 pairs.

## Registered perturbations (§15)

These are descriptive paired point estimates, B − A, not new adoption thresholds. A stratum meets
the registered point rule when its difference is at least −5 pp; small strata do not establish
non-inferiority. The pooled DeepSeek result does not hold in the four-screen stratum, and GLM
does not hold in the five-screen or A-first stratum, so the contrast is **not robust**.

| Model | 3 screens | 4 screens | 5 screens | A first | B first |
|---|---:|---:|---:|---:|---:|
| DeepSeek | +20.00 pp (30) | **−13.33 pp (30)** | +3.33 pp (30) | +8.89 pp (45) | −2.22 pp (45) |
| GLM | −3.70 pp (27) | −3.70 pp (27) | **−18.52 pp (27)** | **−15.00 pp (40)** | −2.44 pp (41) |

The ruler measured overflow and attempted contrast on every valid artboard: 173 DeepSeek and
137 GLM. A contrast explicitly marked unmeasurable is excluded from the contrast denominator;
a missing measurement record or field leaves Gate 3 inconclusive rather than passing on a subset.

## What was not asked and was found

On both routes the shipped design path **fails at default settings**: with `response_format=json_object` DeepSeek returns an empty body cut at the ceiling (4 of 4 pilot calls), and uncapped reasoning exhausted the 8,000-token budget (1 valid of 8 pilot calls). Both are product defects for these slugs, independent of this change, and are why the bench runs with JSON mode off and reasoning capped.

## Reading

Sending only the target screen saves what the audit estimated (about 60% of input on DeepSeek, 52% on GLM) while the first-attempt `edit_html` point estimate fell 8.64 pp on GLM, beyond the registered 5 pp margin, but the interval is about +-12 pp, so **non-inferiority at 5 pp is not shown** (section 12). The registered rule bundles two changes, the sources and the guide, and fails on the guide's side effect on generation (DeepSeek overflow) and on GLM's 3-screen stratum. A new registration that separates the two is the next step; it is not read into this result.

Scope (section 14): two cheap families, one temperature, the product's prompts without its HTTP layer. Nothing here is about the product default model.

## Cost

Conservative ledger (prices above any quote seen; pilots included): **US$ 2.80**. At the live index of 2026-11-16 (DeepSeek 0.06/0.18, GLM 0.0399/0.0784 per M tokens) the measured calls come to about **US$ 0.57**, plus about US$ 0.15 for the discarded pilots. Owner ceiling US$ 5.00.

## Integration and historical limits

Arm A's historical baseline was commit `b53dd4fe`. The disabled product path here is checked
against that baseline; it must be compared byte for byte with the then-current main, including
#1302, during integration. The runner now explicitly selects the lean variant for arm B and leaves
the product default off; this correction does not retroactively change historical model calls.
The original runner checked the spend ledger between items but did not reserve in-flight calls,
serialize concurrent processes or persist unknown costs, so the historical US$ 2.80 tally is not
evidence that the US$ 5 ceiling was enforced. New calls use a strict per-call reservation and
durable pending-cost marker; these safeguards have not been exercised with a paid call.

The original `spend.json` remains untouched. Because its historical total cannot establish every
pending or failed cost, future collection refuses a nonempty legacy ledger until it is reconciled
against provider records. After reconciliation, an operator may create `results/spend-v2.json`
with `{"usd": <verified total>, "historical_usd": <verified total>, "accounting": "strict-v1"}`;
the verified total must be no lower than the original ledger and include any charges not in it.
An unresolved `pending-call.json` must also be reconciled before resuming. This is a human
reconciliation of external billing evidence, not a change to the original receipts.
