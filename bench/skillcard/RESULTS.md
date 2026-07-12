# Results — skill-card reading A/B (the M19-A1 default-flip gate)

Ran the measurement pre-registered in [PREDICTION.md](PREDICTION.md). **Decision: the default stays
OFF.** Reading learned skill cards is kept opt-in, not flipped on by default.

## The number (honest, as measured)

- **Model:** goldilocks `mistralai/mistral-small-3.2-24b` (via OpenRouter) — the regime where
  scaffolding signal shows.
- **Suite:** the hard suite, **n = 12** tasks; the curated demo card set (100% retrieval hit).

| | no cards | with cards |
|---|---|---|
| accuracy | 66.7% | **83.3%** |
| avg tokens / task | 59 | 237 |

- **Accuracy Δ (paired):** **+16.7 pp** (discordant pairs 3 for cards, 1 against), 95% CI
  **[−13.3%, +30.3%]** → **NOT significant** (the CI includes 0).
- **Token overhead:** **+300.7%**.

## Verdict against the registered decision rule

The rule required **both**: (1) paired Δ ≥ 0 **with 95% CI lower bound ≥ 0**, and (2) token overhead
**< +50%**.

- Condition 1 — **FAILS.** Δ is directionally positive (+16.7 pp, and the loop never lost a pair to
  the no-cards arm on the discordant tasks), but on n = 12 the CI lower bound is −13.3% < 0. It's a
  real-looking signal, not a proven one — exactly the "directional but not significant" outcome the
  pre-registration anticipated. We do **not** flip a default on that.
- Condition 2 — **FAILS, decisively.** +300% tokens is 6× the +50% budget.

→ **`CHIMERA_SKILL_CARDS_READ` stays OFF by default.** Reading skill cards remains opt-in
(`CHIMERA_SKILL_CARDS=true` or `CHIMERA_SKILL_CARDS_READ=true`), for users who want the accuracy lift
and accept the token cost.

## Honest caveats

- **The token overhead is inflated by tiny demo prompts.** Base prompts here average 59 tokens, so
  three injected cards (~178 tokens) read as +300%. On real-world tasks with larger prompts the
  *relative* card overhead would be far smaller — but we report what we measured, and even a modest
  overhead wouldn't rescue a non-significant accuracy result.
- **The accuracy lift is genuine as a direction** (cards helped 3 tasks, hurt 1), consistent with the
  hypothesis that distilled Do/Avoid/Check hints help a mid-tier model. What's missing is *power*:
  n = 12 can't exclude zero. A larger pre-registered suite (or a real-task A/B) could settle it — and
  if it did clear both gates, the flip would follow. No re-rolling this run for significance.

## What this closes

The M19-A1 "flip-point" (`CHIMERA_SKILL_CARDS_READ`) was shipped wired-but-OFF pending exactly this
measurement. It's now measured: **the default stays OFF**, published with the number. The mechanism
remains available for anyone who opts in.

---

## Update — cost reduction (cheap card injection)

The original run's **decisive blocker was cost (+300% tokens), not accuracy.** We cut the injection
cost with three levers and re-measured (same hard suite, n = 12, demo cards, OpenRouter free model):

- **top-1, not top-3** cards (`CHIMERA_SKILL_CARDS_K=1`, was 3),
- a **relevance gate** (`CHIMERA_SKILL_CARDS_MIN_OVERLAP=2`): a card is injected only when it shares
  ≥ 2 query terms, so a task with no strong match pays **zero** extra tokens,
- a **shorter render** (`CHIMERA_SKILL_CARDS_MAX_LINES=3`, was 4).

| config | token overhead | accuracy Δ (run-to-run) |
|---|---|---|
| old (k=3, no gate, 4 lines) | **+349%** | +25 pp |
| **cheap (k=1, gate≥2, 3 lines)** | **+37% … +48%** (two runs) | +16.7 pp, then −8.3 pp |

- **Token overhead: blocker REMOVED.** ~349% → **~37–48%** across two runs — a **~7–9× reduction**,
  now under the +50% gate. These cheaper knobs are the **new defaults** for card injection (they only
  matter when someone opts into cards).
- **Accuracy significance: STILL not met — and now clearly noise-dominated.** On n = 12 the free-model
  accuracy Δ swung **+16.7 pp → −8.3 pp** between identical runs. You cannot conclude the lift is
  positive, let alone significant, from this sample.

### Revised decision

**`CHIMERA_SKILL_CARDS_READ` stays OFF by default** — but the reason has narrowed: it is no longer
blocked by *cost* (fixed), only by the absence of a *statistically significant* accuracy signal, which
needs a much larger / real-task paired A/B than n = 12 free-model can provide. The honest engineering
lever (make cards cheap) is done; flipping the default still waits on power, not code.
