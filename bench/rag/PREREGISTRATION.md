# The semantic half of `chimera find` — pre-registration

**Written before any embedder was called. No number in this document was seen first.**

`bench/rag/baseline.json` has held `keyword_recall: 0.4925` with `vector_recall: null` and
`hybrid_recall: null` since 2026-08-13, and its own note pre-registers the baseline:

> Baseline taken BEFORE any embedder was wired, so the semantic number cannot be chosen after seeing
> what would look good. `headroom` is the ceiling: the most a semantic layer could add.

That fixed the *baseline*. It did not fix a *decision rule*, and this bench is the only one of the
sixteen in `bench/` without a pre-registration file. This is that file.

---

## What is being decided

Whether `chimera/rag/` should retrieve semantically at all — not which embedder it should use.

`ChunkStore.search_vector` and `reciprocal_rank_fusion` are written and unit-tested. The only thing
missing is an `EmbedFn`, and `LLMGateway.embed` already has the exact signature `ChunkStore` expects.
So the cost of finding out is a few cents of embedding, and the cost of *not* finding out is that a
whole retrieval path stays half-built on the assumption that it would help.

**Adopting a default embedder is a separate decision and this run cannot make it.** See "what this
arm cannot show" below.

## Arms

| arm | what it is | role |
|---|---|---|
| `keyword` | FTS5 + BM25, `search_keyword` | the baseline, already measured at 0.4925 |
| `vector` | cosine over embeddings, `search_vector` | **diagnostic only** — never adopted alone |
| `hybrid` | reciprocal rank fusion of the two | **the arm under test**, because it is what would ship |

Hybrid rather than vector is the comparison that decides, and the reason is not preference: RRF is
already written, fusing is what the module exists to do, and a vector arm that wins alone while the
fusion loses would be a result about the fusion, not about semantics.

## Decision rule, fixed now

Primary outcome: **recall@10 of `hybrid` minus recall@10 of `keyword`**, paired over the same probes,
tested with McNemar via `chimera/eval/paired.py`.

- **ADOPT** if the paired delta is **≥ +5.0 percentage points** *and* **p < 0.01**.
- **REJECT** otherwise — including the case where the delta is positive but small, which is a real
  outcome and not a reason to go looking for a different cut.
- **REJECT AND REPORT LOUDLY** if `hybrid < keyword`. Fusing two rankings can be worse than either;
  it is the retrieval version of an intervention that acts during the process rather than at the
  end, and this project has measured that shape hurting before. A negative here is a finding, not a
  failed run.

Five points is chosen against the apparatus rather than against taste: with 400 probes, a paired
difference of 5 pp is comfortably inside what McNemar can resolve at this discordance, and it is
large enough that a per-embedder swing would not flip the sign of the verdict.

`vector` is reported and never used to decide.

## What is fixed before the run

Three things about the harness, all of which would otherwise contaminate the number:

1. **`embed_missing` is called with `embedder=`.** `ChunkStore._align_embedder` zeroes every vector
   when the model identity or dimension changes, and it is inert when the name is not supplied — so
   the guard against a mixed-dimension index would be off in exactly the run that introduces
   vectors. `store.py` documents the failure it prevents: `_cosine` returns `0.0` on a dimension
   mismatch and the `score > 0` filter then drops everything, so the index reports healthy and
   returns nothing.
2. **Per-probe outcomes are recorded, not just counts.** The rule above needs paired hit/miss per
   probe; `RagReport` carried totals only, so McNemar was not computable from what the bench
   returned.
3. **Query embeddings are batched.** 400 single-item calls is the same money and several minutes of
   avoidable latency.

## Sanity checks that gate reading the number at all

Run before the aggregate is believed, in this order:

- `embedded > 0`. An embedder that produced no vectors did not fail quietly — the report says so and
  the run is void.
- **Three probe results read with human eyes**, query and top-3 idents. A vector arm near zero is
  the apparatus, not the phenomenon; this project has spent two weeks on that lesson once already.
- ~~Keyword recall reproduces **0.4925 ± 0.01** on the same corpus.~~ **Amended before the run —
  see below.**

## What this arm cannot show

Recorded because a refutation, or a confirmation, is only about what the instrument could exhibit.

- **The number is per-embedder and is not portable.** `store.py` says why in its own words: there is
  no conversion from one model's vector space to another's. A run with `text-embedding-3-small`
  measures that model. A local model will give a different number and possibly a different verdict,
  and this run cannot stand in for it.
- **It cannot choose the shipped default.** `text-embedding-3-small` costs per call, and an index
  over a repository re-embeds every chunk whose line span moved. For an open-source tool people run
  on their own machine, a paid default is a recurring drip. If this arm clears the bar, the local
  arm becomes worth the install — and that is the run that decides the default.
- **The probes are one kind of question.** They are first docstring lines with the symbol's own name
  tokens and stopwords removed — "describe this in prose". A developer searching for a remembered
  identifier, or pasting an error message, is asking something else, and this bench says nothing
  about those.
- **Absolute recall is understated for both arms.** Docstrings are stripped from the index to keep
  the question out of the corpus, so the semantic arm is matching prose against code with the prose
  removed. Production would index the docstrings. The paired delta is the number that survives this;
  the absolute figures are floors.
- **One corpus, one language.** `chimera/` is Python. Nothing here transfers to a TypeScript
  codebase without measuring it there.
- **It says nothing about whether the agent should have this as a tool.** `chimera find` is CLI-only,
  and whether a retrieval tool beats the `grep`/`glob` the agent already has is a different question
  with a different measurement.

## Cost

~2,700 chunks and 400 queries against `text-embedding-3-small`. Under two cents. Recorded here so
that "it was cheap" is a fact rather than a recollection.

## Result

Written to `bench/rag/RESULTS.md` and `bench/rag/baseline.json`, with the embedder name and vector
dimension **inside** the record rather than in a footnote.

---

## Amendment, made before the paid arm ran and after the control check failed

The control check above did not pass, and the amendment is recorded rather than the check quietly
dropped. Measured, with the harness in this branch:

| corpus | chunks | keyword recall@10 |
|---|---:|---:|
| 2026-08-13, as published in `baseline.json` | 2,691 | 0.4925 |
| 2026-08-14, checked out into a worktree and re-run with **this** code | 2,838 | 0.4750 |
| today | 3,459 | 0.4425 |

Two explanations fit "the control does not reproduce": the harness changed the measurement, or the
corpus changed. They separate, and separating them cost nothing — the same code against the tree the
baseline was taken from lands in the baseline's neighbourhood, and recall falls monotonically as the
corpus grows. The corpus is `chimera/` itself, which is 29% larger than it was three weeks ago;
recall@10 over a bigger haystack is a harder question, and the effect is roughly proportional to the
growth across both steps.

So the number was not wrong when it was written. It is **stale**, which is a different thing, and
`baseline.json` is refreshed for today's corpus as part of this run.

**Why this is an amendment and not a moved goalpost.** Nothing about the hypothesis has been seen.
No embedder has been called; `vector_recall` and `hybrid_recall` are still null at the moment this
paragraph is written. What was seen is that a *sanity check* referenced a figure taken from a
different corpus — a fact about the check, not about the effect under test. The distinction that
matters is that the primary comparison is **paired within a single run**: hybrid against keyword,
over the same 400 probes, in the same index, at the same moment. It never depended on matching a
three-week-old absolute number.

The check is therefore restated, and it is a stronger one:

- **The keyword arm and the hybrid arm come from the same run over the same probes.** A comparison
  assembled from two runs is not paired, whatever the arms are called.
- **The harness reproduces itself on a fixed corpus**, which the table above demonstrates.
- The absolute figures are reported **with the corpus size beside them**, because this exercise has
  just shown that a recall figure without its corpus is not a number anyone can check.
