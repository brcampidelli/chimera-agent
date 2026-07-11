# Pre-registration — skill-card reading A/B (the M19-A1 default-flip gate)

**Registered BEFORE running the measurement.** This is the honest gate that decides whether Chimera
should read learned skill cards *by default* (`CHIMERA_SKILL_CARDS_READ` on, coupling card *reading*
to skill *evolving*). Today the mechanism ships wired but **OFF** — we do not flip a default by faith.

## What we're measuring

`chimera skillcard-bench --tasks hard` runs a paired A/B on the hard suite: each task solved **without**
injected skill cards vs **with** the top-k retrieved cards in context. It reports per-task pass/fail for
both arms (so a paired McNemar/Wilson comparison is valid) plus the token overhead of injecting cards.

- **Model:** a *goldilocks* model — `mistralai/mistral-small-3.2-24b` (via OpenRouter). This is the
  regime where scaffolding signal shows: a strong model tops out (no headroom for cards to help), a
  very weak one fails regardless. The free/default model is the wrong regime.
- **Cards:** the curated demo card set (`demo_cards()`), matched to the suite.

## Hypothesis

Injecting the top-k retrieved skill cards helps the goldilocks model on the hard suite — the cards
carry distilled Do / Avoid / Check hints — so **with-cards accuracy ≥ no-cards accuracy** (better, or
at least non-inferior), at a token overhead under +50%.

## Decision rule (registered)

Flip the `CHIMERA_SKILL_CARDS_READ` default to **ON** only if **both**:
1. the **paired** accuracy Δ (with-cards − no-cards) is **≥ 0 AND its 95% CI lower bound ≥ 0**
   (significantly non-negative — real, not noise), and
2. the token overhead is **< +50%**.

Otherwise the default stays **OFF**, and we report the number as-is.

## Honest expectation

The hard suite is small (~n = 12), so the paired CI will likely be wide. A directional-but-not-
significant result is the *expected* outcome and means **the default stays OFF** — we publish the
number (and any loss/retraction) either way. No re-rolling for significance (that would be p-hacking,
which this project exists to avoid).
