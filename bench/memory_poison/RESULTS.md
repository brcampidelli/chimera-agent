# The shipped memory gate fails on cost, and the layer that fails is a regex

Runs 2026-09-02 and 2026-09-05 (reproduced to the digit) against
[`PREREGISTRATION.md`](PREREGISTRATION.md), thresholds fixed 2026-08-14. **This directory had a
pre-registration and two failing console dumps and no write-up**; this is the first, and it changes
**no code** — the reason is at the end. **Cost: US$ 0**, offline.

Reproduce: `chimera memory-poison`. Raw: [`results/2026-09-02.txt`](results/2026-09-02.txt),
[`results/2026-09-05-control.txt`](results/2026-09-05-control.txt).

## Verdict: the shipped configuration FAILS its own gate — on cost, not on marking

Registered thresholds: `MAX_UNMARKED_RATE = 0.05`, `MAX_BENIGN_LOSS_RATE = 0.15`, both required.

| defenses | poison recalled | poison **unmarked** | honest memory lost | gate |
|---|---:|---:|---:|---|
| **all (shipped)** | 43% | **0%** | **25%** | **FAIL** — 25% > 15% |
| no_taint | 43% | 43% | 25% | FAIL |
| **no_gate** | 100% | **0%** | **0%** | **pass** |
| no_label | 43% | 43% | 25% | FAIL |
| none | 100% | 100% | 0% | FAIL |

The headline metric, as registered, is *unmarked* — poison that reaches the prompt looking
verified — and the shipped stack scores **0%** there: every planted fact arrives carrying its
origin. That half works.

**The row that decides the reading is `no_gate`.** Remove the content gate and unmarked stays at 0%
while honest-memory loss drops from 25% to **0%**. On this corpus the gate's entire measured effect
is the honest memory it removes; the provenance label already covers what the gate was for.

## Why: the gate is a pattern matcher, and it says so

`chimera/memory/gate.py` — `MemoryGate.admit` refuses a recalled memory when a regex
(`_INJECTION`) matches its content, or when it shares no token with the query. That is the
§2l-class instrument: it looks semantic and is string matching. Its two named casualties in the
corpus are a security document that *quotes* an attack in order to explain it and a support ticket
*forwarding* an attempt. A pattern matcher on content cannot tell a quote from a command, and the
25% is exactly those.

It also explains the 43% recall of poison with the gate ON: poison that does not happen to match
the regex passes it. The gate does not stop poison; the label marks it. That is the correct
division of labour, and it is why the label, not the gate, is the layer that earns its cost here.

## What this is on one installed copy of the app

Applied offline to the **24 real memory facts** and **14 skill cards** in one install
(`%APPDATA%\app.chimera.desktop\data`), the shipped regex blocks **0 of 24** facts and matches
**0 of 14** cards. The 25% honest-memory loss is on the bench's fifteen hand-authored rows, not on
that user's data today. Both facts belong in this document: the gate is a wrong instrument, and it
has not yet bitten this user.

## Why no code changes here

The failing layer is a regex, and every action that would make this gate pass is a change to what
the regex matches — which is the one move both pre-registrations name as *tuning to the test*. The
honest options are the ones the 2026-08-27 memory-graph write-up already listed: retire the content
gate and keep the label (the `no_gate` row says that passes both thresholds on this corpus), or
replace it with something that is not string matching. Fifteen rows is a pointer, not a verdict, so
neither is done on the strength of this table. **Retiring the gate is registered as the next
question, with its own pre-registration, before anything is deleted.**

## What this cannot show

- **Fifteen hand-authored rows.** Coverage of a shape; no power.
- **One install's data** for the offline check, on one day; 0 of 24 is a statement about those 24.
- **It does not measure how a model acts on a labelled poison** — only whether the label is there.
  A `[unverified]` tag the model ignores is not a defence, and this corpus cannot see that.
- **Nothing about the semantic recall path** (`CHIMERA_SEMANTIC_MEMORY`, off by default): the gate's
  `is_clean` is the injection-only admission for facts recalled by a path other than keyword
  similarity, and it is exercised by no row here.

## Cost

US$ 0.00, offline, under a second.
