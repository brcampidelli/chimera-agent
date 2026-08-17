# Pre-registration — edit-tool efficiency

**Written before any task exists and before any run.** Committed first, deliberately.

## Why this bench exists

There are sixteen directories in `bench/` and **not one measures the thing that writes the code.**
Fusion, cascades, hierarchy, skill cards, memory poisoning, retries, context — all measured. The
tools that actually edit files: never. For a project whose public claim is "honest, published
benchmarks", that is the most conspicuous hole in the set.

It also gates a decision. A competitive read found a **counted, multi-file batch edit** in
`razzant/ouroboros` (`ouroboros/tools/edit_ops.py:573`) whose own harness reports resolving a task in
**1 edit call against 9–11** for surgical-replace-only. Adopting that on the strength of their table
would be importing a result measured on **grok-4.5 and gemini-3.6-flash** into a project whose
population is OpenRouter's weak tier. This bench is the thing that makes the decision ours.

## The question

Holding the task suite and the verifier fixed, does adding a **counted multi-file batch edit** to the
tool surface reduce the *edit calls* and *completion tokens* a run spends, on **our** models?

## The primary metric, and the one we refuse to use

**Primary, paired per task:**

1. **edit-tool calls** — entries in `AgentResult.tool_names` belonging to the edit family
   (`edit_file`, `apply_patch`, `write_file`, and `edit_batch` in arm B).
2. **completion tokens** — `AgentResult.completion_tokens`.

**Registered non-metric: pass rate.** We commit *now* to not reporting pass rate as evidence either
way, because the competitor's own data shows it cannot discriminate: **63/63 and 16/16 tasks passed
in every configuration they tested**. A metric that is saturated in both arms measures nothing, and
reaching for it afterwards — when the real metric disappoints — is the move this file exists to
forbid. Pass rate is recorded as a *gate*: a task that fails in either arm is excluded from the
paired comparison and the exclusion is published with its count.

Secondary, recorded but not headline: wall-clock, `prompt_tokens`, `usd` (None-safe), and the count
of edits that left the workspace in a state the verifier rejected.

## Two stages, because fixing n first is how the last five bets failed

A review of our five never-run pre-registrations found the same defect in all five: **the design
could not detect the effect it was looking for, and nobody checked before writing it down.**
`context_curve` demanded disjoint Wilson intervals for a 3pp effect — arithmetic says that never
happens, at any n we could afford. `role_routing` demanded 10 discordant pairs at n=41 for an effect
the literature puts at +2.1pp, which yields about one. Their "inconclusive" branches were not risks;
they were the expected outcome.

So this bench does not fix n up front.

### Stage 1 — pilot (n=6 tasks, arm A only)

Measure the **variance** of edit calls per task on our weak tier. Publish it. Nothing else.

Then compute, from that measured variance, the n needed to detect the effect at 80% power — and
publish the required n **whatever it is**, including the case where it is larger than we can afford.
"We cannot afford to answer this" is a legitimate, publishable result and is strictly better than an
underpowered run dressed as a finding.

### Stage 2 — the A/B (only if stage 1 says a run is affordable)

Paired, same tasks, same model, same verifier, same seed. One run. No re-rolls.

## Arms

- **A (control)** — today's tool surface: `edit_file` (single occurrence, or `replace_all` with no
  cardinality guard), `apply_patch` (hunks, **one** file per call), `write_file`.
- **B (treatment)** — A plus `edit_batch`: N edits across M files in one call, each declaring the
  occurrence `count` it expects, validated across every file before a single byte is written.

Nothing else differs. Same system prompt, same `max_steps`, same sandbox.

## Models — ours, not theirs

The weak tier resolved by `cost_mode` (OpenRouter free), because that is the population this project
serves and the one its weak-model-lift thesis is about. A second, stronger tier runs only if stage 2
produces a signal in the weak tier, and is reported separately — never pooled. Pooling tiers is how a
strong-model result gets published as a general one.

## The suite — the anti-cherry-pick rule, fixed now

12 tasks, each on a self-contained fixture package. Fixed before any task is authored:

- Every task **must** require touching **≥3 files**. A single-file task cannot discriminate between
  the arms and would only pad n.
- The verifier is **pytest on a test file the agent cannot edit** (excluded by write region). Not
  byte-exact comparison against a reference: several correct solutions exist for a rename, and
  scoring only ours would measure obedience, not capability.
- The 12 come from **six families, two each**: rename a symbol across modules; change a signature and
  every caller; move a constant and update readers; add a parameter with a default; correct a string
  repeated in N files; change an import path. Families fixed now so the mix cannot be tuned after
  seeing which arm likes what.
- A task is **discarded before any measurement** only if arm A cannot solve it in 3 of 3 warm-up
  attempts — it is then unsolvable rather than informative, and the discard is published with the
  task spec.
- No task is authored by reading `edit_ops.py`. Their fixtures are known and copying them would
  measure how well our tools match their bench.

## Analysis plan (fixed now)

- Paired per task: median and mean difference in edit calls, with a **bootstrap 95% CI** (counts are
  small integers and skewed; a normal-theory interval would be wrong here).
- The registered criterion is an **absolute** reduction of **≥2 edit calls at the median, with a CI
  excluding 0**. Absolute, not a ratio: a ratio flatters the arm that starts higher, and our arm A
  baseline is unknown until stage 1.
- Completion tokens judged the same way, and reported even when it disagrees with the call count.
  **A tool that cuts calls but raises tokens has not helped** — the schema rides in every prompt for
  the rest of the run, which is a cost this project has already measured biting elsewhere
  (`bench/skillcard/RESULTS.md`: +16.7pp accuracy, not significant, at +300% tokens).
- No post-hoc task exclusion. No swapping models. No adding arms.

## What we publish — decided before we know it

All of it. Specifically committed in advance:

- a **null** (CI includes 0) — the honest read being "we could not show it at this n on this suite";
- a **negative** (the batch tool costs more) — which kills a roadmap item, and saying so is worth
  more than the item was;
- a **stage-1-only** outcome: the required n is unaffordable, so the question stays open and the
  batch tool stays unbuilt. This is the outcome we consider most likely and we are saying so now.

## Known limitations, acknowledged now

- **The suite is authored by the project it evaluates.** Mitigated by the family rule and the
  a-priori spec, not eliminated.
- **One seed per task per arm.** Run-to-run variance is invisible; a task that flips on luck is noise
  we cannot see. Registered as a limitation, not discovered as one.
- **Fixture packages are not real repositories.** A rename across 4 small files is not a rename
  across django. This bench measures tool ergonomics, and says nothing about repository scale.
- **The metric is a proxy for cost, not for quality.** Fewer calls is better only if the result is
  equally correct, which the pass gate checks crudely and nothing here checks finely.
- Arm B's tool does not exist yet. It will be written **after** stage 1, so that its design cannot be
  tuned to a variance we have already seen.
