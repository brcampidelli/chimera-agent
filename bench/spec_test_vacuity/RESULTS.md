# Results — half of the generated spec tests pass on the buggy code they were written to catch

Run 2026-09-11 · 30 `fix_*` tasks of `bench/local_lift`, the production extractor and generator
on the default model (`openrouter/deepseek/deepseek-v4-flash-0731`, temperature 0) · US$ 0.25 ·
prereg `PREREGISTRATION.md` (committed before the first call) · raw `results/2026-09-11.jsonl`,
table `results/2026-09-11-report.md`.

## The numbers

- Instrument: the task's hidden test fails on the buggy base in **30/30** tasks.
- The generator produced a module on **20/30** tasks (see below for the other ten).
- **122 test functions; 58 pass on the buggy base — 48%** (Wilson 95% [0.39, 0.56]). A test that
  passes on the code the task exists to fix could not have detected the bug; it is vacuous with
  respect to that task, and under the shipped verifier its PASS counted as `evidence="verifier"`
  like any other.
- Tasks where **every** generated test passes on the buggy base: **0/20**. Each module had at
  least one function that fails on the base, so the module-level exit code the shipped verifier
  reads would have been red on every one of these workspaces had the worker changed nothing.

Per task, between 1 of 5 (`fix_average`, `fix_flatten`) and 4 of 5 (`fix_remove_suffix`) of the
functions pass on the base; `fix_index_of` 4/6, `fix_chunk_list` 5/8, `fix_parse_query` 4/7.

## The prediction, and which half held

Registered: *30–60% of generated functions pass on the buggy base* — **held at 48%**. Registered:
*at least 1 in 5 tasks has an all-passing module* — **did not hold: 0 of 20**. The generator
writes one test per requirement and the requirements come from the task prompt, which names the
bug; at least one requirement per task is the bug itself, and its test fails on the base. The
vacuity is at the function level: half of what the module asserts is true before and after the
fix, and a verdict that counts those as evidence is half air.

## What the gate does with this

`SpecTestVerifier` now takes the attempt's pre-change snapshot (`base_snapshot`, handed in by the
verify-or-revert loop) and classifies each test by its two outcomes: **discriminating** (fails
before, passes after — the evidence), **vacuous** (passes on both — excluded and counted),
**regression** (passes before, fails after — fails the attempt), **failing** (fails on both —
unchanged). If no test is discriminating the verifier **abstains**, as it already does when nothing
runnable was generated, so the caller falls back to its other gates instead of accepting a green
made of air. Without a snapshot the behaviour is byte-identical to before. On this corpus the
gate would have excluded 58 of 122 functions from every verdict and abstained on none of the 20
modules.

## Ten tasks with no module — recorded, not explained away

Two tasks (`fix_divisors`, `fix_validate_rows`) had **no requirements extracted**, so the
generator was never called. Eight had requirements and the generator returned **nothing** —
`generate` keeps a reply only if it contains `def test`, and these contained nothing. A re-probe
of `fix_percentile` after the run produced a nine-test module (881 characters, 4,064 completion
tokens, most of them reasoning). The empty reply is route- or run-dependent, not a property of the
task, and the shipped path treats it as a silent abstain. Named here as a follow-up: the
generator should retry once on an empty reply and record `finish_reason`, because the reasoning
model behind the default tier can spend its whole completion budget thinking
(`bench/blind_audit/corpus.py::WORKER_MAX_TOKENS` records the same trap on the delegation path).

## The eight, asked again — what the provider said, and whether a retry fixes it (registered addendum, same day)

`probe_empty.py`, registered in `PREREGISTRATION.md` before any call: the same eight tasks, the
same model and extractor, requirements re-extracted once and shared by two arms — `shipped` (one
call, no `max_tokens`, as `generate` was) and `retry` (the new generator: an explicit 16,000-token
budget, one retry on a reply with no test in it, the budget doubled after a `length` finish).
Every reply's `finish_reason` and completion tokens recorded, which the shipped path never did.
US$ 0.08; the reasoning model's time was the cost (88 s to 35 min per task). Raw
`results/2026-09-11-empty.jsonl`, report `results/2026-09-11-empty-report.md`.

| task | reqs | shipped | shipped finish / tokens | retry | attempts | retry finishes / tokens |
|---|---:|---|---|---|---:|---|
| `fix_merge_settings` | 6 | module | stop / 2,064 | module | 1 | stop / 3,275 |
| `fix_count_words` | 7 | module | stop / 2,813 | module | 1 | stop / 3,141 |
| `fix_rotate_list` | 8 | **nothing** | **length / 131,072** | module | 1 | stop / 2,464 |
| `fix_collect_items` | 7 | module | stop / 8,068 | module | 1 | stop / 12,514 |
| `fix_first_value` | 10 | module | stop / 6,813 | module | 1 | stop / 14,536 |
| `fix_insert_pos` | 5 | module | stop / 2,495 | **nothing** | 2 | length / 16,000, length / 32,000 |
| `fix_percentile` | 7 | module | stop / 11,371 | **nothing** | 2 | length / 16,000, length / 32,000 |
| `fix_title_case` | **0** | — | | — | 0 | |

**What the empty reply is.** Every reply with no test in it finished on `length` — five of
five. With no budget, the one such reply ran to **131,072 completion tokens**, the provider's
ceiling, and returned nothing: the reasoning did not converge, and the call cost about twenty-five
times a normal generation (the run's spend went from US$ 0.003 to US$ 0.027 on that task). So the
sentence the first run wrote — *the model can spend its whole completion budget thinking* — was
right about the mechanism and wrong about the budget: there was no budget, and the model spent
everything the provider allows. The empty reply is a **runaway**, and it is a property of the
draw, not the task: `fix_insert_pos` converged in 2,495 tokens on one call and ran past 32,000 on
the next two.

**Against the registered predictions.** *`shipped` returns nothing on at least 4 of 8 again* —
**failed**: 1 of the 7 that had requirements (the eighth, `fix_title_case`, had no requirements
extracted this time, a different silent abstain, recorded). *The empties are `length` on the
majority* — **held**, 5 of 5. *`retry` returns a module on at least 6 of 8* — **failed**: 5 of 8
(5 of 7 with requirements), against 6 of 7 for the unbounded single call. The retry arm did not
beat the call it replaces on this probe. Its two failures are two draws each that passed the
budget — four runaways in nine draws, against one in seven for the unbounded arm; whether an
explicit `max_tokens` changes the route or the reasoning (§2ad: the API's semantics are part of
the experiment) or this is seven-against-nine chance cannot be told at this n, and is not claimed.

**What ships, by the registered rule.** The rule said the retry and the budget ship either way,
and what the numbers decide is the sentence. The sentence: the empty reply is a runaway that
costs 131k tokens for nothing when nothing bounds it; the budget bounds that cost (16k, then 32k
on the retry — 48k at worst against 131k) and the retry gives a second draw; the `finish_reason`
is recorded on the generator (`last_finish_reason`, `last_attempts`) and the verifier's abstain
names it — `spec-test: no runnable tests generated (2 attempts, finish_reason=length)` — so the
silent abstain the first run found is silent no longer. What is **not** claimed: that the retry
recovers more modules than the unbounded call. On this probe it recovered fewer, and the file
says so. Named as follow-ups: both doubled retries also ran away, so a fresh draw at the same
budget may be the better second attempt; and a run-level cap on runaway spend is a cost guard
the whole default tier needs, not only this generator.

## What this cannot show

Only tasks with a pre-existing workspace; the `create X` half of the corpus cannot be vacuous by
construction. One generator model, one temperature. The probe: eight tasks, one day's routes,
seven and nine draws — a runaway rate per arm is a count here, not a rate. The **pass-after** half of ExecCritic's rule
is not measured: the corpus has hidden tests, not reference solutions, so nothing here says how
many of the 64 base-failing tests are *wrong* rather than *discriminating* — a test that fails
before the fix and still fails after a correct fix is a bad test, and this bench cannot tell it
from a good one.
