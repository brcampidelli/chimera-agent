# Pre-registration — does the fusion panel beat spending the same budget on one model?

**Written and committed BEFORE a single model call of this experiment.** Nothing below may be
edited after the first run; changes go in an addendum with a reason, as `local_lift` and
`memory_graph` do. The commit that introduces this file is the timestamp that matters.

## The question

> Fusion routes a turn through a panel of three architecturally different models, a judge, and a
> synthesiser. **Does that beat giving the same token budget to the best single model**, on the same
> tasks, from the same starting state, judged by the same executable gate?

This is the project's central bet, and it has never been measured against the obvious alternative.

## Why now, and why this shape

The comparative study found the literature split down the middle of our own architecture:

| stage | verdict in the literature | source |
|---|---|---|
| **panel** — generate N diverse candidates | **supported** | Barrel of Monkeys, SWE-bench Verified n=500: pool coverage 80.8%, selected 66.2% vs 62.8% for the best single member |
| **judge** — an LLM deciding code correctness | **contradicted** | 8 judges, n=374: agreement with test execution κ = 0.21 (Java) / 0.10 (Python); GPT-4 misses half the wrong programs |
| **synthesiser** — aggregating models of unequal quality | **contradicted at equal cost** | Self-MoA, CRUX (code) n=800: six samples of the *best* model beat six mixed by **+5.6 pp**; quality coefficient 4.55 vs diversity 1.42 |

And two more findings point the same way. Aider measured, in 2024, that adding Claude 3 Opus to
GPT-4o **lowered** GPT-4o's contribution from 17.0% to 15.3%, because plausible-and-wrong answers
eclipse implausible-and-right ones. Our own `bench/review_judge` **failed our own judge**: 15.1%
against a registered threshold of 30%.

**The experiment the literature has not run** is the one with the budget held equal. Barrel of
Monkeys pooled candidates that already existed and never paid to generate them; Self-MoA controls
cost but is not SWE-bench and has no executable gate. Nobody has asked our question.

## Design, fixed now

**Corpus.** `bench/local_lift/tasks.py` — the existing **100** tasks, unchanged, reused deliberately
rather than authored for this. Each has a pytest that must fail on the untouched workspace, and
`assert_discriminating` proves that **before any model call**: a task whose test already passes
scores a hit for an arm that did nothing.

**Arms.** All three run the same loop, the same workspace fork and the same pytest gate. They differ
only in what produces the candidate.

| arm | what it is |
|---|---|
| **A — fusion** | the shipped panel → judge → synthesiser, one attempt |
| **B — repeat** | the best single model, **three** samples, the gate keeps the first that passes |
| **C — single** | the best single model, one sample |

**C is not decoration.** A vs B alone cannot separate *more diversity* from *more computation*;
B vs C is what says whether repetition helps at all on this corpus. It is the same control Self-MoA
used, and without it a win for A would be unattributable.

**Primary comparison: A vs B.** Secondary: A vs C, and B vs C.

**Seeds: three.** Two alert, three decide. Reported with the dispersion beside the sampling error of
the difference (`SE·√2`), so a spread that is ordinary sampling is not read as instability.

**Statistics.** McNemar on the discordant pairs, Wilson interval, via `chimera/eval/paired.py`.
Aligned per task and per seed; a task both arms pass carries no signal and is reported as such.

## Adoption criterion — absolute, and fixed before the number exists

Fusion stays the recommended route for this task class **only if all three hold**:

1. **A − B ≥ +5.0 pp** — an absolute floor, never a multiplicative one;
2. the **95% CI of the paired delta excludes zero**;
3. the **measured token ratio A/B ≤ 2.0**.

Criterion 3 exists because a win bought at four times the price is not the same finding as a win,
and this project has been burned by a multiplicative criterion before (`§2c #5`: `pass@k > pass@1 ×
1.5` printed "no useful tail" for 52.3% → 72.9%).

## What would refute the bet, stated now

- **B ≥ A** at the point estimate across three seeds ⇒ the panel's diversity does not pay on this
  corpus, and the Barrel-of-Monkeys result does not transfer to a setting where the budget is paid
  rather than pooled.
- **B ≈ C** ⇒ repetition is not the mechanism either, and any A-vs-C gap is about the panel rather
  than about sampling.
- **A > B but ratio > 2.0** ⇒ the panel works and is not worth its price as a default; the honest
  outcome is a documented opt-in, not a default.

## What this experiment CANNOT show — registered before it runs

Every task here has an **executable gate**. That is what makes the comparison clean, and it is also
the condition under which the literature says the judge matters least: when a test decides, an LLM
judge has little left to do. So a null result here is a null **for gated code tasks** and says
nothing about fusion on prose, where no gate exists and where the panel may be earning its keep.

Refuting the bet on this corpus would not refute it in general, and this file says so *before* the
number arrives rather than after — the failure mode named in `§2q`.

Secondarily: the judge is measured here only through its effect on the final answer. This is not a
replacement for `bench/review_judge`, which measures the judge directly and already failed it.

## Cost, and the stop

The harness runs a **pilot** first — a handful of tasks across all arms and seeds — reports the
**measured** tokens and dollars, and **stops**. The full run requires `--full` and an explicit
decision by a person.

The pilot exists because the alternative is an estimate, and an estimate of this is a guess: the
prior bench's journal records outcomes and not cost, so there is no measured tokens-per-solve to
extrapolate from. Measuring five is cheaper than being wrong about nine hundred.

**Full run size:** 100 tasks × 3 arms × 3 seeds = **900 solves**.

## Provenance

Every row records the Chimera version, the model slugs of every arm, the seed, and the SHA of this
file — so a result can never be read against a design it was not run under.
