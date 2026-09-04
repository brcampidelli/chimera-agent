# The semantic half of `chimera find` — results

Run 2026-09-04 against the rule fixed in [`PREREGISTRATION.md`](PREREGISTRATION.md), including its
amendment. Embedder: **`openrouter/openai/text-embedding-3-small`, 1536 dimensions**. Corpus:
`chimera/` at `85b30ff`, **3,459 chunks**, 400 probes, recall@10.

## Verdict: ADOPT

| arm | recall@10 |
|---|---:|
| `keyword` — FTS5 + BM25 | 0.4425 |
| `vector` — cosine, diagnostic only | **0.4100** |
| `hybrid` — reciprocal rank fusion | **0.5050** |

Paired over the same 400 probes, `hybrid` against `keyword`:

```
both hit         a = 168
keyword only     b =   9
hybrid only      c =  34
both miss        d = 189
                     ----
discordant            43

paired delta   +6.25 pp     95% CI [+3.2, +8.3]
McNemar exact  p = 1.7e-04
```

Against the rule as it was written: delta ≥ +5.0 pp ✓ · p < 0.01 ✓ · hybrid not below keyword ✓.

## The finding that was not obvious, and is the reason the arms were fixed in advance

**Semantic retrieval on its own is WORSE than keyword here — 0.4100 against 0.4425.** Every point of
the win comes from the fusion, not from the embeddings.

Had the pre-registration named `vector` as the arm under test — the intuitive choice, since the
embedder is the thing being added — this run would have read as a refutation and the retrieval path
would have been abandoned. Had it been left unfixed, the same numbers would have supported either
story depending on which was written afterwards. The two rankings are wrong about *different*
probes, and RRF is what converts that into recall neither has alone.

## What the fusion costs, measured rather than assumed

`b = 9`. Nine probes that keyword found alone, the hybrid lost. Fusing is not free: a strong keyword
hit can be pushed out of the top ten by vector results that are individually plausible and
collectively wrong. The net is +34/−9, and the −9 is in the table because a gain reported without
its loss is a gain reported by somebody who did not look.

## The corpus moved, and the published baseline was stale

`baseline.json` held `keyword_recall: 0.4925` from 2026-08-13. The same harness, today, measures
0.4425 — and the reason is not the harness:

| corpus | chunks | keyword recall@10 |
|---|---:|---:|
| 2026-08-13, as published | 2,691 | 0.4925 |
| 2026-08-14, in a worktree, re-run with **this** code | 2,838 | 0.4750 |
| today | 3,459 | 0.4425 |

`chimera/` is 29% larger than it was three weeks ago, and recall@10 falls as the haystack grows,
roughly in proportion across both steps. The old number was right about an older corpus. It is
refreshed here, with the chunk count beside it — a recall figure without its corpus size is not a
number anyone can check, which is the whole lesson of this table.

## Three harness defects fixed before the run

Each would have contaminated the result rather than failing loudly:

1. **`embed_missing` was called without `embedder=`**, so `_align_embedder` — the guard that zeroes
   every vector when the model identity or width changes — was inert in exactly the run that
   introduces vectors. `store.py` describes the failure it prevents: `_cosine` returns 0.0 on a
   dimension mismatch and the `score > 0` filter then drops everything, so the index reports healthy
   and returns nothing.
2. **`RagReport` carried totals only.** The decision rule is paired, and a paired test needs the
   pairs. Per-probe hit/miss is now recorded for all three arms and feeds `chimera/eval/paired.py`
   directly.
3. **Query embeddings were one call per probe**, inside the loop — 400 round-trips for the same
   money.

## Cost

62 embedding calls, 3,859 texts (3,459 chunks + 400 queries). Roughly **two cents** at
`text-embedding-3-small` pricing — an estimate from the text volume, not a figure read off an
invoice, and labelled as such.

## What this does not decide

Restated from the pre-registration because a result travels further than the document that framed it:

- **It does not choose the shipped embedder.** The number is per-embedder and vector spaces do not
  convert. A local model (`ollama/nomic-embed-text`) would give a different figure and possibly a
  different verdict. For an open-source tool people run on their own machine, a paid default is a
  recurring drip — so the local arm is the one that should decide `settings.embed_model` for the
  index, and it is now worth the install for a measured reason.
- **It does not say the agent should have this as a tool.** `chimera find` is CLI-only and the agent
  cannot reach it. Whether retrieval beats the `grep`/`glob` the agent already has is a different
  question needing a different measurement.
- **The probes are one kind of question**: a docstring's first line with the symbol's own name tokens
  and stopwords removed. Probe 200 of this run reads *"Who words person who runs"*, which is what
  that transformation does to some docstrings — all three arms miss it, and no retriever should be
  blamed. A developer pasting an error message or half-remembering an identifier is asking something
  this bench does not model.
- **Absolute recall is a floor for both arms.** Docstrings are stripped from the index to keep the
  question out of the corpus; production would index them. The paired delta is what survives that.
