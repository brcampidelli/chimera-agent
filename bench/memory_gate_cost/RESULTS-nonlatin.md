# The memory gate dropped 77–87% of recalled Russian facts and 60–70% of Chinese ones; one registered prediction missed

Run 2026-11-14 against [`PREREGISTRATION-nonlatin.md`](PREREGISTRATION-nonlatin.md), written (with the
corpus, the runner and the decision function) before the first execution. **Cost: US$ 0**, offline,
deterministic, under a minute. Reproduce: `python bench/memory_gate_cost/run_nonlatin.py`. Raw:
[`results_nonlatin/summary.json`](results_nonlatin/summary.json) and one line per cell in
[`results_nonlatin/rows.jsonl`](results_nonlatin/rows.jsonl). Corpus `items_nonlatin.jsonl` sha256
`3fb104718a7ea7b4d22d05976f7758fbf06f27683f06944feab724601013da03`.

## Verdict as registered: **not confirmed** — because prediction P1b was wrong, not because the fix failed

| registered reading | result |
|---|---|
| P1 recovery (P1a corrected ≥ 27/30 in all 12 cells **and** P1b legacy `ru` < 3/30 in all draws) | **fails on P1b**; P1a holds |
| P2 poison (unmarked 0/30, blocked set identical) | holds |
| P3 cost (non-inferior within 5 pp on irrelevant pairs) | holds |
| `confirmed` = P1 and P2 and P3 | **false** |

The decision function printed `instrument_exhibits_defect: false` and `confirmed: false`. I report
that as printed. The threshold was written before the run and is not loosened here.

## Recovery: recalled facts that reached the prompt, per stratum and draw (30 each)

`ungated` is the ceiling (what recall found before any gate): **30 / 30 in all 12 cells.**

| stratum | draw | legacy | corrected | legacy dropped by the gate | `ascii_visible` |
|---|---|---:|---:|---:|---:|
| ru | 1 | 6 / 30 (Wilson 9.5–37) | **30 / 30** (88.6–100) | 24 | 6 |
| ru | 2 | 4 / 30 | **30 / 30** | 26 | 4 |
| ru | 3 | 7 / 30 | **30 / 30** | 23 | 7 |
| zh | 1 | 11 / 30 | **30 / 30** | 19 | 11 |
| zh | 2 | 12 / 30 | **30 / 30** | 18 | 12 |
| zh | 3 | 9 / 30 | **30 / 30** | 21 | 9 |
| ja | 1 | 13 / 30 | **30 / 30** | 17 | 13 |
| ja | 2 | 17 / 30 | **30 / 30** | 13 | 17 |
| ja | 3 | 19 / 30 | **30 / 30** | 11 | 19 |
| pt | 1 | 30 / 30 | 30 / 30 | 0 | 30 |
| pt | 2 | 29 / 30 | 30 / 30 | 1 | 29 |
| pt | 3 | 30 / 30 | 30 / 30 | 0 | 30 |

Paired legacy → corrected, exact McNemar p from 2.9e-08 (ru, draw 2) to 9.8e-04 (ja, draw 3); the
Bonett-Price interval for the gain excludes 0 in every ru, zh and ja cell (smallest: ja draw 3,
+15.8 to +53.0 pp). `pt` is a control and shows no change (p = 1.0; the one dropped row in draw 2 is
the accent defect, a fact whose only shared token was a manufactured fragment).

**The mechanism identity held for every row**: the legacy gate admitted a recalled fact if and only
if query and fact shared an ASCII token of two or more characters, and `ascii_visible` equals legacy's
recovered count in every cell. So the instrument is sound; what it measured is *how much ASCII these
sentences carry*.

## What missed, and why it is not hidden

- **P1b (legacy `ru` < 3 / 30): missed — 6, 4 and 7.** I predicted that Cyrillic documentation has no
  ASCII token to share. It does: command-line flags, tool names and product words (`--taint`, `MCP`,
  `Chimera`) are written in Latin letters inside Russian sentences, and where the query picked one of
  them the legacy gate could see it. The defect is therefore *partial*, not total, even in the script
  furthest from ASCII. The registered clause reads this as "the corpus did not exhibit the defect";
  that reading is wrong about the mechanism, since 77–87% of recalled Russian facts were still lost
  and the identity check held. Both statements are published; the registered verdict stays `false`.
- **P1c (legacy `zh`, `ja` below 50% in every draw): half missed.** `zh` held (11, 12, 9 of 30);
  `ja` did not: 13 / 30 (43%) in draw 1, **17 / 30 (57%)** and **19 / 30 (63%)** in draws 2 and 3.
  Japanese documentation mixes more Latin words than I assumed.
- **P1d held** (legacy `pt` 30 / 29 / 30; corrected not lower).

Everything else registered held: P1a (12 of 12 cells at 30 / 30), P2a, P2b, P2c, P3 and P4.

## The English population: nothing moved (identity, as registered)

| | legacy | corrected | ungated |
|---|---:|---:|---:|
| `doc_random` lost | 0 / 90 | 0 / 90 | 0 / 90 |
| `doc_security` lost | 0 / 30 | 0 / 30 | 0 / 30 |
| poison, config `all`: recalled / **unmarked** | 25 / **0** of 30 | 25 / **0** of 30 | 30 / 0 |
| poison, config `no_taint`: recalled / unmarked | 25 / 25 | 25 / 25 | 30 / 30 |
| poison blocked by the injection regex | `p_override_1`, `p_override_2`, `p_persona_1`, `p_persona_5`, `p_selfmod_1` | the same five | — |

This reproduces the 2026-10-05 reading to the row (P2a, P2b, P2c), so the change did not move what it
was not meant to move. It also restates why that reading could not see this defect: with an English
fact and an English query, legacy and corrected are indistinguishable.

## The cost side: irrelevant facts offered directly to the gate

| `doc_random`, 90 facts, each with another item's query | legacy admitted | corrected admitted |
|---|---:|---:|
| shift 1 (**decided on**) | 67 / 90 | 6 / 90 |
| shift 2 (descriptive) | 68 / 90 | 10 / 90 |
| shift 3 (descriptive) | 66 / 90 | 4 / 90 |

(Every query carries the frame "what do the docs say about …", so the legacy floor was met on `the`,
`do`, `what`: it admitted irrelevant facts about three times in four.) **P3 holds by a wide margin:**
on the success "blocked", baseline = legacy, 61 pairs improved and 0 got worse; the `1 − 2α`
Bonett-Price interval for the change in blocking is **+57.8 to +74.8 pp**, so non-inferiority against
−5 pp is met and the gate got *stricter* in English, not looser.

The price shows up in Han. Irrelevant pairs admitted by the corrected gate, shift 1, 30 each:

| stratum | draw 1 | draw 2 | draw 3 |
|---|---:|---:|---:|
| ru | 0 | 0 | 1 |
| ja | 0 | 1 | 0 |
| pt | 0 | 3 | 1 |
| **zh** | **10** | **6** | **13** |

`zh` is 20–43% (P4 registered "may be higher" and set no threshold). The legacy gate admitted 0–1 of
these only because it could not read the fact at all, which is not a virtue. The cause is the
per-character segmentation: two unrelated sentences share a common character. It is cheap, it is what
recall already does, and it is the honest cost of reading Chinese at all; a real segmenter (jieba-style
dictionary, or a character-bigram floor) would shrink it and is a separate decision.

## What this does and does not establish

- The gate was dropping most recalled non-Latin facts (ru 77–87%, zh 60–70%, ja 37–57% depending on
  draw), in silence, and now drops none of them. This is measured on a deterministic corpus of the
  project's own translated documentation, in three draws that agree.
- **Accented Portuguese was not the headline.** The old gate saw `autentica` + `o` and mostly admitted;
  one row in 90 changed. The accent defect matters for matching *quality*, not for loss.
- **Not shown:** that the override regex reads non-English attacks (it does not; the label covers
  them), anything about the semantic recall path (needs an embedder; unit-tested only), real users'
  memory, and Chinese precision beyond the `zh` row above. See the registration's *What this cannot
  show*.

## Amendment to the registration's accounting

P1b was a wrong number written before the run, and it is kept wrong. For the next registration on this
population: a script-specific stratum should state its prediction for `ascii_visible` from a count on
the corpus (`items_nonlatin_builder.py` can print it without running any gate), because that is the
quantity that decides how total the legacy defect looks.

> Provenance (2026-11-14): the pre-registration, corpus and builder were committed alone in 42298cb1, then this run was repeated from that commit; `results_nonlatin/summary.json` came out identical to the first run (timestamps aside).

---

## Second reading, 2026-11-14 (after the independent review's four corrections)

The review found four defects in the first version of the change: (1) a semantic hit had no similarity
floor once the lexical one was skipped (`SemanticIndex.search` now keeps only cosine >= 0.2, an
unmeasured, per-index-configurable floor); (2) one-character Latin fragments and digits (`s` of
"What's", a lone `1`) counted as relevance again (kept only for Han, kana and hangul); (3) the code
index and its query normalized differently (`ộ_cache` was unfindable by its own name; both now use
`fold_for_index` / `identifier_tokens`); (4) existing code indexes kept the old Han-unspaced text
(`ChunkStore` rebuilds the FTS table on open when `meta.fts_normalization` is older). Items 1, 3 and 4
are outside what this bench runs (it exercises the keyword path on facts, not the semantic layer or the
code index); item 2 is inside it.

The runner was executed again, unchanged, on the unchanged corpus (same sha256). **Every
recovery number is identical** (12 of 12 corrected cells at 30 / 30; legacy as in the table above; P1b
and P1c still missed exactly as reported; `ascii_visible` identical), and so are the poison rows
(`all` unmarked 0 / 30; the same five blocked; `no_taint` 25 / 25). What moved, because the corrected
gate no longer lets a lone `s` or `1` count:

| irrelevant pairs admitted, `doc_random` | first reading | second reading |
|---|---:|---:|
| shift 1 (decided on), corrected | 6 / 90 | **5 / 90** |
| shift 2, corrected | 10 / 90 | 9 / 90 |
| shift 3, corrected | 4 / 90 | 3 / 90 |
| shift 1, legacy | 67 / 90 | 67 / 90 |

The P3 interval for the change in blocking went from +57.8 / +74.8 pp to **+59.0 / +75.8 pp**;
non-inferiority holds as before and the registered verdict is unchanged: `confirmed: false` (P1b).
Non-Latin irrelevant-pair counts are unchanged (zh 10 / 6 / 13). No prediction was edited.
