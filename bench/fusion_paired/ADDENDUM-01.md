# Addendum 01 — two apparatus defects found before the first model call

**Written 2026-08-28, before any cell of this experiment ran.** The pre-registration says changes go
in an addendum with a reason; this is that. Nothing in the question, the arms, the seeds or the
adoption criterion moves. What moves is the apparatus, in two places where the runner as committed
would have measured something other than what the design names.

Both were found by reading the runner against `chimera/config.py` and against the runners of
`bench/local_lift`, not by running anything.

---

## 1. "The best single model" was never operationalised — and the default would have been the cheap one

The design says arms B and C spend the same budget on **the best single model**. The runner passed no
`--model`, so those arms would have inherited `settings.default_model`, which is
`openrouter/deepseek/deepseek-chat-v3.1` — while arm A convenes a panel of
`claude-opus-5` / `gpt-5.5` / `gemini-3.1-pro-preview`.

That is not the registered comparison. It is **frontier panel against a cheap model**, and it would
have moved both criteria at once: criterion 1 (the +5.0 pp floor) would have measured model tier,
and criterion 3 (the token ratio ≤ 2.0) would have compared prices of different classes of model.
It is the same family as `§2g` — two things measured different ways, and the difference reports the
apparatus.

**Operationalised now, as a declaration and not as a selection:**

> The single model for arms B and C is **`openrouter/anthropic/claude-opus-5`** — the model the
> shipped configuration already trusts to write the final answer (`_DEFAULT_SYNTHESIZER`), and
> `_DEFAULT_PANEL[0]`.

It is a **declaration**, not a measurement, and the direction of the error is stated here rather than
discovered later: if Opus 5 is not in fact the strongest panel member on this corpus, arm B is
understated and the bias runs **in favour of fusion**. So a null (B ≥ A) would be conservative and
survives the doubt; a **win for A is the outcome at risk**, and adopting on it requires the model
choice to be measured first rather than declared.

**A screen, not a selection, guards the gross case.** The pilot runs arm C once per panel member. A
member that solves **zero** pilot tasks is excluded from ever being the single model — that is a
broken ruler, not a weak model (`§2e`: the ruler written against one model that lies when the model
changes). The screen may **not** promote a member on a good showing: five tasks at one seed cannot
resolve a real difference, and letting it try would be a forking path. The rule is absolute and it
is written here before the numbers exist.

## 2. Nothing stopped one cell from teaching the next

Every runner in `bench/local_lift` passes a hygiene set — `--no-remember --no-collect
--no-evolve-skills --no-skill-cards` — and this one passed none of it. The loop runs
`for task → for seed → for arm`, so arm A would solve a task, write a long-term memory fact and
possibly propose a learned skill, and then arms B and C would solve **the same task** with that in
reach.

The contamination is not random. It runs **with the arm order**, so it would have credited whichever
arm ran last — here B and C — and nothing in the recorded rows would have said so. This project has
seen exactly this shape before: the wiring is what was untested, not the mechanism.

Fixed: the same four flags, on every arm, plus a per-run `CHIMERA_HOME` so the usage receipts read
back belong to this experiment and not to whatever else the machine did.

---

## Recorded, not changed: what arm A actually is

Two facts about the shipped route, written down because a reader would otherwise assume otherwise,
and because criterion 3 is a price ceiling:

- `fusion_mode` defaults to **`selective`** — a probe of the first two panel members, escalating to
  the full panel, judge and synthesiser only on disagreement. Arm A is therefore the **shipped**
  fusion, not "every turn fused". That is the thing worth measuring: it is what a user gets.
- `--fuse` routes **deep-reasoning turns**; the rest of arm A's turns run on `default_model`. Arm A
  is a mixed route by construction, arm B is frontier throughout, and the token ratio is what keeps
  the budgets comparable — not flag symmetry.

`A vs B` remains well-posed and stays the primary comparison: roughly three frontier generations on
each side, one arm spending them on a diverse panel with a judge, the other on repeated samples of
one model with the test as the gate.

## The pilot, restated

One seed, not three. Seeds exist to separate a difference from a variance in the **result**; the
pilot is not a result and its own output says so. It buys three things: the gross-failure screen
above, a measured per-cell cost for every arm, and proof that the apparatus runs end to end before
900 cells depend on it.
