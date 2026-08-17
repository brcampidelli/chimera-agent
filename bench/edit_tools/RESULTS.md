# Results — edit-tool efficiency

**Registered before the run:** [`PREREGISTRATION.md`](PREREGISTRATION.md). Model: the weak tier
(`openrouter/qwen/qwen3-next-80b-a3b-instruct:free`). Date: 2026-08-17. One run, no re-rolls.

## Headline

**A counted, multi-file batch edit cuts edit calls. It does not cut tokens.**

| metric (arm B − arm A) | median | 95% CI (bootstrap) | registered criterion |
|---|---|---|---|
| **edit calls** | **−2.0** | **[−2.0, −1.0]** | **met** (≥2 at the median, CI excludes 0) |
| completion tokens | −33 | [−85.5, +70.0] | **not** significant — the CI includes 0 |

22 usable pairs out of 22 attempted; every run in both arms passed its verifier.

**Read the CI, not just the median.** The interval's upper end is **−1.0**: the true reduction may be
as small as one edit call per task. The criterion is met as written, and the effect is at its floor.

## What the arms were

- **A** — today's tool surface: `edit_file`, `apply_patch` (one file per call), `write_file`.
- **B** — A plus `edit_batch`: N edits across M files in one call, each declaring the occurrence
  `count` it expects, every path checked against the write region, all validated before a byte
  is written.

Nothing else differed.

## Per task

| task | Δ edit calls | Δ tokens | B used the tool |
|---|---|---|---|
| `rename_helper` | −3, −3 | −14, −43 | yes, yes |
| `rename_class` | −4, 0 | −952, **+3648** | yes, yes |
| `signature_swap_args` | −5, −2 | −1293, +88 | yes, yes |
| `signature_return_shape` | −2, −2 | +160, +284 | yes, yes |
| `constant_move_module` | −4, −2 | −580, +139 | yes, yes |
| `constant_split_two` | 0, 0 | −101, −36 | **no, no** |
| `param_add_default` | 0, 0 | +70, −4 | **no, no** |
| `param_thread_through` | −3, −1 | −405, −58 | yes, yes |
| `string_repeated_typo` | −2, −2 | +75, −279 | yes, yes |
| `string_prefix_change` | −2, −2 | −70, +31 | yes, yes |
| `import_relative_to_absolute` | −1, −1 | −30, −92 | yes, yes |

**The effect is conditional on the tool being reached for**, which is not the same as it being
available: in the 18 pairs where arm B called `edit_batch` the median Δ is −2.0; in the 4 where it
did not, it is 0.0. Availability is not usage, and the headline number already carries that dilution
rather than excluding it.

## Four things that qualify this result

### 1. A defect in our own suite, and it flatters us

`param_add_default` **violates the pre-registration's own rule**. The rule reads: *"Every task must
require touching ≥3 files. A single-file task cannot discriminate between the arms and would only
pad n."* That task's fixture spans three files, but the change only needs **one** — the new parameter
has a default, so the callers keep working untouched. Verified after the run by applying a
single-file reference solution: the test passes.

The `min_files` field counted files in the fixture, not files that must change. That is a
measurement bug, not a judgement call.

**It is left in.** Its two pairs contribute Δ=0, which *dilutes* the effect — so removing it would
make the headline stronger, and removing a task after seeing that it hurt is the exact post-hoc
exclusion this bench's pre-registration forbids. It is reported instead, and the rule is now known to
need a checker rather than a hand-set integer.

### 2. Tokens did not move, and the source claim does not replicate

The idea came from a competitor whose harness reports both fewer calls **and** fewer tokens. Here the
token difference is a wash: median −33 with a CI spanning −85 to +70. Their numbers were measured on
grok-4.5 and gemini-3.6-flash; ours are on a free-tier model. Either the saving is model-dependent or
it does not exist. What we can say is what we measured.

The pre-registration's disqualifier — *"a tool that cuts calls but raises tokens has not helped"* —
is not triggered: tokens did not rise either. The honest summary is **fewer calls, same cost**.

### 3. One run cost 5.6× the tokens

`rename_class` arm B rep1: **4441 completion tokens, 22 tool calls, 107 s** against arm A's 793 / 11
/ 27 s on the identical task — and it still took 3 edits despite calling the batch tool. The median
hides this completely.

One outlier in 22 is not a pattern, and it is named rather than smoothed because it is the shape of
the failure worth watching: when a batch does not anchor, the model can spiral into re-reading and
retrying, and the tool that was supposed to save a call has spent twenty.

### 4. Small integers, small n

Bootstrapping the median of small integers gives intervals that can look tighter than the evidence
warrants — validated in advance against five synthetic cases, including one where a true effect of
one call correctly **fails** the ≥2 criterion. 22 pairs is well past the 3 the pilot's variance
called for, but this is one model, one suite, and one seed per cell.

## Pilot (stage 1), for the record

Arm A only, 6 tasks × 3 repeats. 15/18 solved.

`import_module_moved` failed **3/3** and was excluded by the pre-registration's discard rule. Its
reference solution was executed by hand first and **passes** — the task is hard for this model, not
broken, so the exclusion is about the arm rather than about a bug in the fixture.

Pooled within-task sd: **0.82** edit calls. Required n: **3 paired observations**.

That figure was wrong when the runner first printed it. It computed the spread **across tasks**
(2.60) rather than **within** a task, and reported 13 per arm. Across-task spread is dominated by
task difficulty, which pairing already cancels; feeding it into a power calculation inflates n by
whatever the suite's difficulty range happens to be. Corrected before stage 2 ran, on the data
already collected, with no extra model calls — 13 → 3.

## What this changes

`edit_batch` ships **off by default** (`CHIMERA_EDIT_BATCH=1`). The measured gain is real but sits at
its registered floor, and every tool that is on adds a schema to every prompt for the whole run — a
cost this project has already watched swallow a gain elsewhere
([`bench/skillcard/RESULTS.md`](../skillcard/RESULTS.md): +16.7pp accuracy, not significant, at +300%
tokens).

Turning it on by default is a separate decision that would need the token question settled on more
than one model.

## Limitations, as registered

One model. One seed per cell. Fixture packages are not real repositories — a rename across four toy
modules is not a rename across django. The suite is authored by the project it evaluates. The metric
is a proxy for cost, not for quality: the pass gate is crude and nothing here checks the *shape* of
the resulting code.
