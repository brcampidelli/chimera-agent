# Fusion receipts — "selective fusion with receipts"

Chimera's reasoning core blends a **panel** of models (panel → judge → synthesizer). Fusion buys
quality but costs more tokens, so the honest question is never "is fusion good?" but "**was it worth
it, here?**". Receipts answer that with numbers instead of a claim.

Every fusion run can be priced into a **receipt**: what each advisor (panel member), the judge, and
the synthesizer cost — each at *its own* model's rate — plus whether selective mode short-circuited
the panel. Persist the receipts and you get a publishable **cost × quality curve**.

## Try it

```bash
# Show the itemized per-advisor cost of one run:
chimera fuse "Explain CAP theorem simply" --show-cost

# Append each run's receipt to a JSONL, then summarize the curve:
chimera fuse "..." --receipt runs.jsonl
chimera fuse "..." --receipt runs.jsonl --selective
chimera fusion-receipts runs.jsonl
```

`fusion-receipts` reports the **fusion rate** (how often the full panel actually ran vs. a selective
short-circuit), the mean/total cost over the runs that had a known price, and — when receipts carry
a pass/fail quality signal — the pass rate and the **dollars per passing answer**.

## Honesty rules (by construction)

- **Tokens are measured; dollars are estimated.** Token counts come from the provider; the dollar
  figure is computed at approximate public **list price**, so a receipt is an estimator, not a bill.
- **Unknown model → unknown cost, never zero.** If any stage runs a model with no price on file, the
  receipt's total is `None` (`unknown`), so a missing price can't masquerade as "free". Prices are
  overridable in code (`chimera.fusion.set_price`).
- **Per-advisor attribution.** The panel cost is broken out *per model* (`receipt.advisor_costs`), so
  you can see which advisor earned its keep — the substance behind selective fusion, not a slogan.

## Why this exists

The field moved toward routing/cascades (spend more only when the stakes justify it), and away from
always-on fusion. Receipts are what let Chimera fuse **selectively and prove it paid off** — the
cost×quality curve is the evidence, published including the runs where fusion did *not* help.
