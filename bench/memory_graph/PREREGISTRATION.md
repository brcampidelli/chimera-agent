# Pre-registration — is the graph memory layer worth anything?

**Registered 2026-08-27, before any arm was run.** Thresholds, slices, arms and predictions below
were fixed first; the numbers went into `RESULTS.md` afterwards.

## Why this exists

`chimera/memory/graph.py` (164 lines) landed on 2026-06-30 and it is not decorative. `_recall_graph`
in `chimera/cli/main.py` and in `chimera/server/manager.py` build a `MemoryGraph` from stored
memories, and `chimera/interface/session.py::recall_facts` appends its entity-linked facts to every
turn's prompt, for `chat`, `assist`, the TUI, the messaging gateway and the coding turn.

**And nobody ever measured whether it helps.**

- `chimera/eval/memory_bench.py` — the file whose job is measuring memory — contains zero mentions
  of the graph.
- `tests/test_memory_graph.py` tests extraction, persistence and a corrupt file. That is correctness
  of the module. A module can be perfectly correct and contribute nothing.
- `bench/` had no directory for it.

This is the repository's own recurring defect — capability that exists and nothing measures — so it
gets the same treatment the taint ledger and the memory poison defense got: a number, with its cost
printed beside it, and a null published if a null is what comes out.

## What the code actually does, read before the corpus was written

```python
def related_facts(self, query: str, k: int = 5) -> list[str]:
    for entity in sorted(self.entities()):
        if re.search(rf"\b{re.escape(entity.lower())}\b", lowered):
            for relation in self.relations_of(entity):
                ...
    return facts[:k]
```

Two properties follow, and they shape every slice below:

1. **There is no ranking.** Entities are walked in alphabetical order and the list is cut at `k`.
   The `k` triples that reach the prompt are the alphabetically first ones, not the relevant ones.
2. **There is no traversal.** Only entities named *in the query* contribute. A fact one hop away
   (`query → A`, `A uses B`, `B requires C`) is unreachable by construction.

A pilot on five hand-written facts (recorded here because it shaped the corpus, not because it is a
result) confirmed a third property: the heuristic extractor makes the **whole subject clause** the
entity, so `"The deployment pipeline for the mobile app depends on the shared build cache"` yields
the entity `"The deployment pipeline for the mobile app"` — which no natural question contains
verbatim, so the layer never fires on prose of that shape. The corpus therefore uses short
proper-noun subjects, the shape where the layer **can** fire. A corpus where it never fires would
measure nothing about whether firing helps, and the sensitivity itself is reported separately.

## Design

- **Same machinery as the product.** Both arms call `chimera.interface.session.recall_facts` — the
  function the CLI, the desktop and the coding turn all call — with a real `MemoryManager` over the
  real JSON `MemoryStore` (`CHIMERA_MEMORY_BACKEND` defaults to `json`) and a real `MemoryGate`.
  `k=3` is `ChatSession.memory_k`, the budget production actually ships. A bench with its own
  slightly different recall path measures a product nobody ships.
- **No model.** What is measured is **retrieval**: whether the facts an answer needs reach the
  prompt. Whether a model then uses them is the open half — the same honesty `memory_poison.py`
  keeps about susceptibility. Nothing here costs a cent or touches a network.
- **One generator, three slices.** The three slices come out of the same pools, the same templates
  and the same random stream, differing only in what an answer needs. A slice built by its own code
  path carries the signature of that path rather than of the property under test.

### The three arms

| arm | what it is | why it exists |
|---|---|---|
| `no graph (k=3)` | `recall_facts(..., graph=None, k=3)` | the production default with the layer off |
| `graph (k=3)` | `recall_facts(..., graph=graph, k=3)` | the shipped wiring |
| `no graph (k=6)` | `recall_facts(..., graph=None, k=6)` | **the budget-matched control** |

The third arm is the one that makes the result attributable. `recall_facts` *appends* up to `k`
graph facts to the `k` lexical ones, so the graph arm can put **twice** as many facts in the prompt.
Without a lexical arm at the same total budget, any lift the graph shows is unattributable: "the
entity index found the right fact" and "the prompt got bigger" produce the same number.

### The three slices (40 tasks each, per seed, one per distinct entity)

| slice | what the answer needs | why |
|---|---|---|
| `multihop` | **two** stored facts about the entity named in the query | more than `k=3` reliably holds when the entity has 5–7 edges. Where the graph *should* win. |
| `single` | **one** fact, pinned lexically by naming its object's head noun | the control. The graph has nothing to add, so what it adds is cost. |
| `traverse` | the bridge fact **and** a fact about the entity it points at | the mechanism ceiling. Registered as a predicted null. |

`multihop` and `traverse` use the **identical** query — `"Give me the full picture on {E} before I
touch it."` — so the only thing that differs between them is what an answer needs. That wrapper
names the entity and nothing else that appears anywhere in memory, which is the choice **least**
favourable to the graph: a question sharing vocabulary with unrelated facts would let them crowd the
lexical top-k and make the graph look better, not worse.

### Corpus, fixed here

40 entities (single-token proper nouns), 4–6 object edges each plus exactly one edge pointing at
another entity, over a pool of 24 object phrases with unique head nouns, plus 60 noise facts from a
disjoint subject pool. ≈260 facts per seed. Seeds **42, 43, 44** — two seeds alert, three decide.
Each seed is a fresh corpus, because the corpus is the thing that varies in reality.

### Guards that run before any number is read

- **Round-trip.** Every generated fact must come back out of `extract_relations` unchanged, or the
  grader would be comparing a lossy reconstruction against the original and calling the difference a
  miss. In the execution path, not in prose.
- **Reachability, both directions.** An unlimited-budget lexical arm must recall every needed fact
  on `multihop`/`single` — a task whose reference is unreachable at any budget is impossible by
  construction and would report 0% for every arm while looking like a finding. On `traverse` the
  same arm must **not** reach the second hop, or the slice is not testing a hop.
- **Additivity.** `base ⊆ treatment` on every task. The whole telemetry is defined by it.
- **Activation.** Below, and it aborts the reading rather than colouring it.

## The activation abort — rule 3, and it is not a threshold, it is a refusal

`injected` per turn is the number of graph triples that actually reached the prompt (the treatment's
fact set minus the baseline's — derived from the production path, not recomputed). If the graph
injected nothing in more than half the `multihop` turns, `run_graph_bench` **raises** instead of
returning a report. A null measured over turns where the layer never switched on reads "did not
help" and means "was never on". Those are different sentences and this bench is not allowed to
confuse them.

`MIN_ACTIVATION_RATE = 0.50`.

## Thresholds — absolute, fixed before the first run

| threshold | value | why |
|---|---|---|
| `MIN_LIFT` | **+5.0 pp** | absolute, on the `multihop` paired delta. Never multiplicative: a multiplicative bar (`treatment > baseline × 1.5`) punishes exactly the arm that already does well, which this project has already been burned by. |
| `MAX_CONTROL_DROP` | **−2.0 pp** | the `single` control must not fall. |
| `MIN_INJECTION_PRECISION` | **0.33** | of the triples the graph puts in the prompt, at least one in three must be a fact the answer needed. Below that it is paying two junk facts for every useful one. |

`GraphBenchReport.gate()` requires **all** of: lift ≥ +5.0 pp, the difference CI excluding zero,
control ≥ −2.0 pp, pooled injection precision ≥ 0.33. Failing any of them is a result, not a reason
to move the number beside it.

## Predictions, written before running

**P1 — `multihop` lift.** The graph beats the k=3 baseline by ≥ +5 pp with a CI excluding zero.
Analytic power, computed before the run: the neutral query matches exactly the named entity's
`e+1` facts; the baseline's 3 slots are filled by an effectively arbitrary tie-break, so
`P(pass) = (e−1)/C(e+1,3)` ≈ **20%** at `e=5`. The graph adds a *differently ordered* 3, so the
union covers up to 6 of ≤7 — an expected delta on the order of **+25 to +40 pp**, five to eight
times the bar. This probe is not underpowered; a null here would be a real null.

**P2 — and it will NOT beat the budget-matched control.** `graph (k=3) − no graph (k=6)` will be
**≤ 0**. The prediction is that the layer's contribution is *slots*, not *entities*: both arms
anchor on the same token, and a lexical arm given the same total budget should cover the entity's
facts at least as well. If this comes out significantly **positive**, entity anchoring adds
something beyond budget and P2 is wrong — which is the outcome that would most change what we do.

**P3 — `traverse` stays at 0% for all three arms.** `related_facts` does not traverse. If any arm
closes this slice, my reading of the code above is wrong and everything derived from it is suspect.

**P4 — the control does not move, and that is structural.** `recall_facts` appends and truncates
nothing, so the treatment's fact set is a superset of the baseline's and a needed fact can never be
displaced. Expect `single` delta ≈ 0 and injection precision on that slice ≈ 0 — roughly three junk
facts per turn, which is the layer's cost in its naked form.

**P5 — McNemar's `baseline_only` cell is 0 on every slice.** Same reason as P4. If it is not, the
wiring changed under the bench and the whole reading is void, not merely surprising.

**P6 — the alphabetical cut throws away needed facts.** `alpha_cut_losses` > 0 on `multihop`: the
graph will *hold* facts the answer needed and drop them at `facts[:k]` because they sort late. This
is the number that separates "the graph has nothing to offer" from "the graph has it and there is
no ranking", and they point at completely different work.

## What would refute the hypothesis

The hypothesis is "the graph layer improves what reaches the prompt". It is refuted by **any** of:

- `multihop` delta < +5.0 pp, or its 95% CI including zero, at pooled n = 120;
- the budget-matched arm matching or beating the graph arm (P2 confirmed) — which refutes the
  stronger claim that the layer is an *entity-aware retriever* rather than a bigger `k`;
- the control slice falling.

A refutation on the second bullet with a pass on the first is the awkward and most likely case, and
it has a name: the layer works, and a one-character change to `memory_k` would work better and
cheaper. That is a decision for Bruno, not a number to re-run until it flips.

## What this does NOT measure

- **Whether a model uses the facts.** Retrieval only. Needs a live model, and that is the open half.
- **Prose-shaped memory.** The corpus uses short proper-noun subjects, because the extractor turns a
  long subject clause into an entity no query ever names verbatim. The graph's activation rate on
  naturally-written memory is a separate question and is not answered here.
- **The SQLite/FTS backend.** The default is `json`, which is the pure-Python IDF keyword path. FTS
  ranks differently and could change the baseline.
- **Semantic recall.** No embedder is configured, so the baseline is lexical. Semantic recall would
  bridge paraphrases and is a different comparison.
- **Multi-turn accumulation.** One question per turn, no conversation.

## What this does NOT license

- **Deleting `MemoryGraph` on a null.** The decision to retire a layer is Bruno's. This bench reports
  retrieval on one corpus shape and says so.
- **Widening the corpus until the lift appears.** The slices, pools, templates and sizes above are
  what runs. If the number comes out flat, the number comes out flat.
- **Moving `MIN_LIFT`.** It is registered so it can be acted on. The actions it points at are: add a
  ranking to `related_facts` (P6 says whether that is where the loss is), add traversal (P3 says
  whether that is where the ceiling is), or raise `memory_k` and drop the layer (P2 says whether that
  is the cheaper equivalent).

## Addenda — changes made after registering, recorded rather than quietly applied

Neither touched a slice, a template, a corpus size, a threshold or a prediction.

**1. The relation pool was too small to build the corpus at all (before any number existed).** Each
entity draws one relation per object edge plus one for the bridge edge, without replacement — up to
7 — from a pool of 6. The very first execution died on `ValueError: Sample larger than population`.
`belongs to` and `is part of` were added, both verified by the round-trip guard. No arm had run.

**2. The item ids are now seeded (after runs 1 and 2, before the published run).** Two runs at the
same seeds were supposed to be identical and were not: `MemoryManager.add` mints
`uuid.uuid4().hex`, and the keyword ranker breaks score ties on `(-score, item.id)`. The neutral
query ties every fact of the named entity on score, so the random ids *are* the baseline arm's
choice of which three facts to keep. Measured: the multihop baseline read 13.3% and 22.5% on two
runs of the "same" measurement, while the paired delta moved from +26.7 pp to +25.8 pp.

The ids are now drawn from the corpus seed, so a seed fully determines a run. This changes what is
*reproducible*, not what is *measured*: the tie-break is arbitrary in production too, and it stays
arbitrary-and-different across seeds. Both pre-pinning runs are published in `RESULTS.md` beside the
final one, because a run-to-run spread of 9.2 pp on the absolute rate against 0.9 pp on the paired
delta is the strongest argument for the paired design in this repository and deleting it would have
been the cheap thing to do.

```bash
python bench/memory_graph/run_graph.py
```
