# Results — the graph memory layer, measured for the first time

**Run 2026-08-27**, seeds 42/43/44, `k=3`, 120 paired items per slice — 40 per seed, one per
distinct entity, over three independently generated corpora (so 120 distinct items drawn from 40
distinct entity *names*; counted the conservative way). Deterministic and offline — no model, no
network, no cost, and no batching to keep matched because nothing is inferred. Reproduce with:

```bash
python bench/memory_graph/run_graph.py
```

Design, thresholds and every prediction below were fixed in [`PREREGISTRATION.md`](PREREGISTRATION.md)
**before** the first arm ran; the two changes made afterwards are recorded in its addenda, and
neither touched a slice, a threshold or a prediction. Raw numbers:
[`results/graph_ab.json`](results/graph_ab.json).

## The headline

| slice | no graph (k=3) | **graph (k=3)** | no graph (k=6) | graph − no graph | graph − k=6 |
|---|---:|---:|---:|---|---|
| `multihop` | 17.5% | **41.7%** | **79.2%** | **+24.2 pp** [+18.5, +24.2] ✓ | **−37.5 pp** [−39.9, −29.6] ✓ |
| `single` (control) | 100.0% | 100.0% | 100.0% | +0.0 pp | +0.0 pp |
| `traverse` (ceiling) | 0.0% | 0.0% | 0.0% | +0.0 pp | +0.0 pp |

✓ = 95% CI excludes zero (McNemar + Wilson on the discordant pairs, `chimera/eval/paired.py`).

**The graph helps, and it is the worst way to spend the prompt budget it costs.** It lifts multihop
recall by a large and significant +24.2 pp — and the same number of prompt slots handed to plain
keyword search instead (`memory_k = 6`, a one-character change) reaches **79.2%**, nearly double the
graph's 41.7%.

**Gate: FAIL** — not on the lift, which cleared its +5.0 pp bar nearly five times over, but on cost:
pooled injection precision 12.0% against a registered floor of 33%.

## Cost, printed beside the lift and not under it

| | multihop | single (control) |
|---|---:|---:|
| facts in the prompt, per turn | 3.0 → **6.0** | 3.0 → **6.0** |
| triples the graph injected, per turn | 1.69 | 1.84 |
| …of which the answer needed | 0.43 | **0.00** |
| injection precision | 25.1% | **0.0%** |

The layer doubles the memory block of every turn it fires on. On the control slice — an ordinary
question with one lexically pinned answer, which is what most turns look like — **every** fact it
adds is noise: 1.84 junk triples per turn, precision 0.0%. Pooled over the realistic mix that is
**12.0%**: roughly seven junk facts per useful one.

**What got worse and what did not.** No slice lost a single item (`baseline_only = 0` everywhere),
and that is structural rather than lucky: `recall_facts` *appends* graph facts to the keyword hits
and truncates nothing, so the treatment's fact set is always a superset of the baseline's and a
needed fact cannot be displaced. The bench asserts this per item and aborts if it stops being true
(P5, confirmed). The damage this layer can do is therefore **entirely** prompt noise — which is why
the noise is measured as its own axis instead of being left to show up in a pass rate that cannot
express it.

Say the weak part out loud: the control slice sits at **100% for every arm**, so it is at the
ceiling and its flat delta is worth nothing on its own — a control that cannot move is a control
that measures nothing, and this project has closed three learning-lift runs behind exactly that
mistake. It is kept because the *other* number it produces is the one that matters here: on the
turns where the layer has nothing to contribute, it contributes 1.84 facts anyway, all of them
noise. That is the cost measured where it is naked.

## Every pre-registered prediction, and how it came out

| | prediction | outcome |
|---|---|---|
| **P1** | multihop lift ≥ +5 pp, CI excludes zero | **confirmed** — +24.2 pp, CI [+18.5, +24.2], 29 discordant pairs all won by the graph |
| **P2** | the graph will **not** beat a budget-matched k=6 | **confirmed, and by a wide margin** — −37.5 pp, CI [−39.9, −29.6]. The layer's contribution is slots, not entities. |
| **P3** | `traverse` stays at 0% for every arm | **confirmed** — 0/120 on all three arms. `related_facts` does not walk an edge. |
| **P4** | control flat, injection precision ≈ 0 there | **confirmed** — 100% → 100%, precision 0.0%, 1.84 junk triples/turn |
| **P5** | McNemar `baseline_only` = 0 on every slice | **confirmed** — 0 everywhere |
| **P6** | the alphabetical cut throws away needed facts | **confirmed** — 78 on multihop, 63 on traverse |

Five of six were predictions of a null or a bound, and one of them (P2) is the load-bearing result.
One thing to say against myself: the registered analytic power put the multihop delta "on the order
of +25 to +40 pp" and it landed at **+24.2**, just under the band. The arithmetic behind that
estimate missed one matching fact per entity — the *incoming* bridge edge, `X uses E`, which also
contains the token `E` and so competes for the baseline's three slots. The direction and the order
of magnitude held; the band was slightly optimistic.

### P2 is the sentence that matters

`related_facts` and the keyword ranker anchor on the **same token**: the entity named in the query.
The graph does not find anything keyword search cannot; it returns a *differently ordered* slice of
the same candidates, in `k` extra slots. Given the same total budget, the ranker beats the ordering
that has no ranking at all. The graph arm did win 2 of 120 items the k=6 arm lost — the residual
entity-awareness — against 47 it lost.

### P6, and the one-line change it points at

```python
for entity in sorted(self.entities()):   # alphabetical
    ...
return facts[:k]                          # cut at k, still alphabetical
```

On `multihop` the graph's candidate set contains **both** needed facts for every single item — that
is structural, since the candidate set for a query naming `E` is every relation of `E`. The arm
still failed **70 of 120**. So on this slice **100% of the graph's loss is the `facts[:k]` cut, and
none of it is retrieval.** It has the answer in hand and throws it away in alphabetical order.

`traverse` fails for the opposite reason and the distinction is the point of having both slices: the
bridge fact is in the candidate set (and gets cut 63 times), but the second hop is *never* in it. A
ranking fixes `multihop`; only traversal would touch `traverse`.

## Noise, and why the paired design earned its keep

| | multihop delta | multihop baseline rate |
|---|---:|---:|
| run 1 (ids unpinned) | +26.7 pp | 13.3% |
| run 2 (ids unpinned) | +25.8 pp | 22.5% |
| **run 3 (ids pinned — published)** | **+24.2 pp** | 17.5% |

The first two runs were, at a fixed seed, supposed to be identical and were not. The cause is worth
recording: `MemoryManager.add` mints `uuid.uuid4().hex`, and the keyword ranker breaks score ties on
`(-score, item.id)`. The neutral query ties *every* fact of the named entity on score, so those
random ids **are** the baseline arm's choice of which three facts to keep.

Read the two columns. The absolute rate moved **9.2 pp** between two runs of the "same" measurement;
the paired delta moved **0.9 pp**. Both arms share the tie-break inside a run, so pairing conditions
it out — which is the entire argument for `chimera/eval/paired.py`, here with a number under it. An
unpaired A/B on this bench would have been reporting the uuid stream.

The ids are now seeded from the corpus seed, so a seed fully determines a run (asserted by a test,
and two consecutive runs are byte-identical). The tie-break stays arbitrary-and-different *across*
seeds, which is what a real store looks like.

**Seed dispersion.** Per-seed multihop deltas: `{42: +27.5, 43: +20.0, 44: +25.0} pp`, sd **3.82 pp**
against a sampling error of the difference (`SE·√2`) of **5.53 pp**. The spread is *below* what
sampling alone predicts, so there is nothing here to blame the method for. Three seeds, not two —
two would have produced a difference and no estimate of variance.

## The graph actually ran

97.5% of turns had at least one graph triple reach the prompt (1.69–1.84 per turn). The registered
abort — refuse to publish a reading below 50% activation — did **not** fire, so this null on
`traverse` and this lift on `multihop` are readings of a layer that was switched on, not of one that
sat the run out. `run_graph_bench` raises rather than returning a report when that is not true, and
a test breaks the guard on purpose to prove it is not inert.

## A claim in the pre-registration that does not survive the result

The registration says the neutral wrapper — *"Give me the full picture on E before I touch it"* — is
"the choice **least** favourable to the graph". That is half true and the half it misses matters.

It is true that a query naming nothing but the entity lets *only* that entity's facts match, which
hands the lexical arm a clean candidate pool. It is false that this is uniformly unfavourable to the
graph: because nothing in the query distinguishes one of `E`'s facts from another, every one of them
**ties on score**, and the lexical arm cannot rank the needed ones up. A question that says what it
wants — *"what does Orion depend on?"* — would give the ranker a term to work with and shrink the
graph's lift.

So the two slices bracket a query-specificity axis rather than sampling one point on it:

- **unspecific** (`multihop`, "tell me everything about E") — the graph is worth +24.2 pp, and a
  bigger `k` is worth +61.7 pp;
- **specific** (`single`, the head noun named) — the baseline is already at 100% and there is
  nothing to win.

Read the +24.2 pp as the favourable end for this layer, not as a floor. The registration overclaimed
in the graph's favour and this is the correction, recorded here rather than edited into the file
that was supposed to be fixed beforehand.

## What this does not say

- **Nothing about answer quality.** This measures retrieval: whether the needed facts reach the
  prompt. Whether a model uses them needs a live model and is not measured here.
- **Nothing about prose-shaped memory.** The extractor turns a long subject clause into an entity no
  question names verbatim (`"The deployment pipeline for the mobile app"`), so the layer never fires
  on memories written that way. This corpus uses short proper-noun subjects — the shape where it
  *can* fire. Activation on naturally-written memory is a separate, unmeasured question, and it can
  only be lower than the 97.5% here.
- **Nothing about the SQLite/FTS backend or semantic recall.** The default backend is `json` and no
  embedder was configured, so the baseline is the pure-Python IDF keyword path.

## What to do with this

Three options, and the number that speaks to each:

1. **Rank `related_facts` instead of cutting alphabetically.** P6 says 100% of the multihop loss is
   the cut. This is the cheapest change with the largest headroom on the slice the layer exists for.
2. **Raise `memory_k` and drop the layer.** P2 says k=6 alone reaches 79.2% against the graph's
   41.7%, at the same prompt cost and with none of the noise.
3. **Add traversal.** P3 says the layer is 1-hop. Nothing else touches `traverse`.

Doing (1) and (2) at once is the obvious experiment and this bench can measure it: the arms already
exist. **The decision to keep, fix or retire the layer is Bruno's** — this file reports what it does,
including the part where it does help.
