# Pre-registration — should a gated task route to the cheap tier by default?

**Written and committed BEFORE a single model call of this experiment.** Nothing below may be
edited after the first run; changes go in an addendum with a reason, as `local_lift`,
`memory_graph`, `fusion_paired` and `fusion_aggregate` do. The commit that introduces this file is
the timestamp that matters.

## The question

> When a task carries an **executable gate** — a command that exits 0 or does not — does routing it
> to the cheapest capable model deliver the same verdict as routing it to a frontier one, at a
> fraction of the price? And is the gap small enough to make cheap the **default**?

This is a question about a *policy*, not about a model. The policy under test is one sentence:
**"a task with a verify command goes to the flash tier."**

## Why this is asked now, and what the prior evidence is worth

Four projects were run end to end through the app — a landing page, a physics game, a credit
calculator and a browser desktop — each with an executable gate written before any model ran. In the
three whose gate discriminated, the cheapest arm that passed was always a *flash*:

| project | dearest that passed | cheapest that passed | ratio |
|---|---|---|---:|
| game | `kimi-k3` $1.1735 | `glm-5.3-flash` **$0.0112** | **105×** |
| finance | `glm-5.3` $0.3144 | `deepseek-v4-flash` **$0.0062** | **51×** |
| desktop | `grok-4.6` $0.3549 | `qwen3.7-flash` **$0.0055** | **65×** |

**That evidence is not enough to change a default, and the reasons are specific.** One run per
model, no seeds, and a different model set per project — so model and project are confounded and no
column is comparable to another. It is a reason to *measure*, and this file is the measurement.

## The statistical shape, which is the part most easily got wrong

This is **not** a superiority test. Nobody expects the cheap tier to beat the frontier one, and a
design that asks "is cheap better?" answers "no" and teaches nothing.

It is a **non-inferiority** test: the cheap tier is adopted if it is *not meaningfully worse*, and
"meaningfully" has to be a number fixed before the data exists. Accepting the null — "we found no
significant difference, so they are the same" — is the failure this framing exists to prevent: with
a small n, no difference is found because none *could* be found.

**Registered margin: 10 percentage points.** The cheap tier may lose up to 10 pp of pass rate; the
adoption test is that the **95% CI lower bound of the paired delta does not cross −10 pp**. A wide
interval therefore fails, which is the correct behaviour for an underpowered run.

## Design, fixed now

**Arms.** Same tasks, same fork, same gate, same loop. Only the model differs.

| arm | what it is |
|---|---|
| **A — flash** | the cheapest capable tier: `deepseek/deepseek-v4-flash-0731` |
| **B — frontier** | the ceiling reference: `x-ai/grok-4.6` |
| **C — shipped** | today's default, `deepseek/deepseek-chat-v3.1`, unchanged |

C is not decoration. Without it, a win for A would be measured against a model nobody runs, and the
decision on the table is *"change the default"* — which needs the default in the comparison.

**Seeds: three** (42, 43, 44). Two alert, three decide.

**Statistics.** McNemar on the discordant pairs, Wilson interval, via `chimera/eval/paired.py`.
Paired per (task, seed).

**Cost is measured, never priced from a table.** Read from the receipts on the attempts of
`runs.jsonl`, joined by workspace and scoped to the run's start instant — the join alone adds the
previous run of the same cell, which is how a pilot in this repo once read 86,983 tokens where the
truth was 43,219.

## The corpus is the binding constraint, and it decides whether this runs at all

A non-inferiority test on a corpus where **every** arm passes proves nothing: the margin is
satisfied by a ceiling, not by the models. This project has hit that ceiling four times —
`local_lift` at 100% for three frontier models, GSM8K at 100% oracle, and the learning-lift series'
three attempts at a 40–60% band that all landed at 84–92%.

So the run is **staged**, and stage 2 is gated on stage 1:

- **Stage 1 (cheap).** Arm C alone, one seed, over the corpus. Report its pass rate.
- **The gate.** If arm C's pass rate is **above 90%** or **below 20%**, STOP. Above, and the corpus
  cannot show a loss; below, and it cannot show a win, and either way the money spent on stage 2
  buys a number that was determined before the models were chosen.
- **Stage 2.** All three arms, three seeds, only if stage 1 lands inside the band.

**Corpus.** The four project briefs already written and their gates — `site`, `jogo`, `financas`,
`so-estudante` — plus every task in `bench/local_lift/tasks.py` whose gate the shipped model fails
in stage 1. Four is too few to answer this and is stated as such: the four exist to *calibrate the
band*, and the `local_lift` slice supplies n. If stage 1 leaves fewer than **20** discriminating
tasks, the run stops there and reports that the corpus, not the models, was the limit.

## Adoption criterion — absolute, and fixed before the number exists

The default routes gated tasks to the flash tier only if **all three** hold:

1. **A − B ≥ −10.0 pp**, and the **95% CI lower bound of that paired delta is above −10.0 pp**;
2. **A − C ≥ −5.0 pp** on the same terms — a change of default may not be worse than the default it
   replaces by more than half the margin allowed against the ceiling;
3. **measured cost ratio B/A ≥ 10×** — under ten, the saving does not pay for the risk of the two
   points above, and the honest outcome is to leave the default alone.

Absolute, never multiplicative on the accuracy side. A multiplicative criterion has misfired in this
repo before (`pass@k > pass@1 × 1.5` printed *"no useful tail"* for 52.3% → 72.9%).

## What would refute the bet

- **A loses more than 10 pp to B** ⇒ the flash tier is not interchangeable on gated work, and the
  four-project observation was a small-sample artefact.
- **The CI is too wide to decide** ⇒ underpowered, reported as such, and NOT reported as "no
  difference found". This is the outcome most likely to be misread and it is named here in advance.
- **A ≈ B but the ratio is under 10×** ⇒ true and not worth acting on.

## What this experiment CANNOT show — registered before it runs

- **Only gated code tasks.** Every task here is decided by a command exiting 0. A task with no
  executable gate is judged by a model reading prose, and nothing measured here transfers to it —
  which is the majority of what a person asks an assistant.
- **Three models, not a tier.** "Flash" is a marketing word covering models with different
  training. A result for `deepseek-v4-flash` is not a result for every cheap model, and the
  adoption, if it comes, names the model.
- **Nothing about latency.** The four projects showed the cheapest arm was often the SLOWEST
  (`deepseek-v4-flash` took 1,496s where `grok-4.6` took 174s). A default that saves money and
  triples wall-clock is a different trade from the one this file measures, and it is not measured
  here.

## A dead arm is not a failed arm

Measured during the four projects: one arm produced **0 tokens in 21 seconds** and no file, and the
same model worked when re-probed alone. A crash and an incapacity produce the same row and only one
is evidence. Every cell records its exit code and the tail of its output; a cell that crashed enters
the table **with the reason** and is excluded from the pass-rate denominator, and the count of
exclusions is reported beside the result.

## Provenance

Every row records the Chimera version, the model slug, the seed, and the SHA of this file.
