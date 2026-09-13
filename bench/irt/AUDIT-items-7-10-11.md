# Items 7, 10 and 11 — audited against the tree, and what each one actually was

**2026-09-13.** Three of the study-18 shortlist's remaining items, closed by reading our own code
rather than by building. Recorded here rather than left implicit, because "we did not build it" and
"there was nothing to build" are different sentences and only one of them is true for each.

## Item 11 — `2609.05339`, memory survives an upgrade: **already built, and tested**

The item asked for three things. All three already hold:

| prescription | where it is |
|---|---|
| store the embedding model id per stored vector | `rag/store.py::_record_embedder` writes `embedder:dimension` into `meta` |
| full re-embed when the embedder changes | `rag/store.py::_align_embedder` — *"Drop every stored vector when the embedder that would produce them has changed"* |
| keep the raw evidence beside the summary | `chunks` stores `text TEXT` alongside `vector BLOB` |

And the memory store persists **no vectors at all** — `memory/semantic.py` caches them in-process,
keyed by content, so an embedder change re-embeds from the fact's own text on the next start. There
is no orphaned vector for the defect to live in.

`tests/test_a_vector_from_another_embedder_is_dead_weight.py` pins it, and its docstring records the
same failure the paper describes, found and fixed here before the sweep named it: *"Changing embedder
made the index return nothing, permanently, without one line in a log."*

## Item 10 — `2609.06815`, typed skill cards: **already built**

The paper's contrast is typed fields against a single markdown string, **+8.5 points** clean. Our
cards are already typed: `evolution/learned_skill.py` renders exactly five labelled slots —

```python
_CARD_FIELDS = ("Trigger", "Do", "Avoid", "Check", "Risk")
```

— and `card_retrieval.py` injects them as those labelled lines, one block per card, never as a blob.
The `SKILL.md` format on disk carries YAML frontmatter with named keys for the same reason.

## Item 7 — `2609.10969`, evidence lineage: **named and priced, deliberately not built**

This one is real and is the only one of the three that is not already done. `2609.10969` (2,880
scenarios) measured cross-model voting over **shared** evidence approving 62.9% of unsafe proposals
against 22.9% with independent sources — a **40.9 pp source effect** against an **11.3 pp model
effect**. Our fusion panel varies the model and shares the evidence, so the finding applies to us.

**But the item's own prescription does not translate.** "Record the evidence each panel member saw"
records a constant here: every member is asked the same task with the same context, by construction
(`fusion/engine.py`). There is no lineage to record until members *can* differ, which is a design
change to fusion and not a logging one.

**And the measurement that would justify that change cannot be made for free.** The honest version of
this item is the one `bench/design_effect` used for replicas: measure *our own* correlation rather
than import someone else's. That needs the panel's per-member answers, and `fusion/receipts.py`
keeps per-advisor **cost keyed by model** — not the answers. Measuring our panel's agreement
therefore needs a fresh paid run, and this session's authorised budget was for item 6 and was spent
on it (US$0.0045 of US$30).

So item 7 closes as: **the finding applies to us, the prescription does not, and the prerequisite is
a measurement nobody has funded.** The cheapest next step, if it is ever wanted, is to persist a
digest of each panel member's answer on the receipt — enough to measure agreement later, without
storing the content — and that shape should be decided by the measurement it is for, not guessed at
the tail of a session.

## Why this file exists

Five of the seven items in this batch ended by correcting our record rather than implementing the
item: #5 (the interval inflation was not in our code), #8 (the proxy holes were already closed and
tested), #11 and #10 (already built), #12 (the method does not survive our scale). Two produced code:
#6 (the missing floor) and #9 (the MULTILINE fence bug).

The list was built by reading ~5,000 titles and "checking against the tree". **The checking was
shallower than the items claimed.** That does not make the list worthless — it produced item 6, which
found a real defect in a real gate — but it means every remaining item starts with an audit of our
own code, not with an implementation. Written down so the next person starts there too.
