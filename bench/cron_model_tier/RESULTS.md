# Results — may a cron job run on the cheap tier? (P1-03 acceptance)

Run 2026-11-16 · 8 synthetic jobs × 2 arms × 3 seeds = 48 cells · prereg `PREREGISTRATION.md`
(committed `920d2210` before the first paid call) · raw answers in `results/journal.jsonl`.
Reproduce: `python bench/cron_model_tier/run_bench.py` then `--report`.

**Scope, once and plainly:** these are 8 *synthetic* jobs with invented frozen data, one completion
each, no tool loop. The verdict is about **these 8 jobs**, not about the 21 in production.

## Verdict by the registered rule (cheap adopts iff 3/3 seeds fully correct)

| job | current (v3.1) | cheap (v4-flash) | adopt cheap? |
|---|---:|---:|---|
| activation_weekly | 3/3 | 3/3 | **YES** |
| churn_report | 0/3 | 0/3 | no |
| support_triage | 0/3 | 3/3 | **YES** |
| health_alert | 2/3 | 3/3 | **YES** |
| health_quiet (`NOTHING_NEW`) | 3/3 | 3/3 | **YES** |
| newsletter_metrics | 3/3 | 3/3 | **YES** |
| reactivation_segments | 0/3 | 3/3 | **YES** |
| cost_digest | 0/3 | 3/3 | **YES** |

**7 of 8 jobs adopt the cheap tier; `churn_report` does not.** The prediction "at least 5 of 8" held.

Pooled cells fully correct (Wilson 95%): `current` **11/24 = 45.8%** [28%, 65%]; `cheap` **21/24 =
87.5%** [69%, 96%]. A "YES" is 3 of 3: its per-run success lower bound is 0.44. This is the
owner's operational gate, not a proof of equivalence.

## What the numbers do not say what they seem to

**1. The current model was the worse one, and that is the headline.** `current` is not a
reference at 100%; it miscounts. On `support_triage` it reported 4 high-priority tickets (there are
5) and 5–6 over-48h (6); on `reactivation_segments` it was off by one or two in every seed; on
`churn_report` it never got the remaining MRR or the churn rate right (3 of 12 is 25.0%, it said
20.0 / 21.7 / 22.8). Predicted failures on denominators (`newsletter_metrics`) did **not** happen
(3/3 both arms); the prediction about "worst p95 over ALL services" also held up (cheap 3/3).
The flag "reference imperfect" (§5) applies to **five** jobs: churn_report, support_triage,
health_alert, reactivation_segments, cost_digest.

**2. Two of the `current` failures are my template, not arithmetic.** `cost_digest` was 0/3 for
`current` solely because it wrote the amount (9.41) in the "Most expensive day" line instead of the
date; its three numeric fields (29.44, 4.21, 39.53) were right in all seeds. `health_alert`
seed 43 wrote `3` where the names go. The template did not say "date" or "names" for those labels
firmly enough and a script is not allowed to guess. The registered rule stands unchanged — and it
does not change the adoption verdict either way, because `cheap` followed the format 3/3 — but a
real job's template needs to say what the line holds. Numeric-only fields (post-hoc, not the
registered criterion): `current` 57/72, `cheap` 63/72.

**3. `churn_report` failed on `cheap` for a different reason in 2 of 3 seeds.** Seeds 42 and 43
returned an **empty reply with `finish=length`**: the model spent all 1500 completion tokens before
writing anything (v4-flash reasons before answering). Seed 44 answered, with the right MRR figures
(209.10 remaining is correct) and a wrong churn rate (26.32; the truth is 25.0). So "does not
adopt" here means *budget-sensitive*, not only *wrong*: a production job with a smaller
`max_steps`/token budget could fail the same way. `max_tokens` 1500 was registered in advance and
not raised.

**4. The cheap tier is 4× cheaper per token and only ~17% cheaper per job.** It writes ~12× more
completion tokens (mean 609 vs 50, hidden reasoning), and takes ~3× longer (4.4 s vs 1.5 s).

| arm | total cost | mean completion tokens | mean latency | finish |
|---|---:|---:|---:|---|
| current | US$ 0.00578 | 50 | 1.5 s | 24 stop |
| cheap | US$ 0.00478 | 609 | 4.4 s | 22 stop, 2 length |

On a few-thousand-token job the price difference is cents a month either way; the case for the
cheap tier here is accuracy on these counts, not cost.

## Cost

Whole bench US$ **0.0106** (the pilot included), against the US$ 3.00 cap. No cell excluded, no
retry changed an outcome (an API error would be scored a failure; there were none).

## Limits (also in the prereg, repeated because they decide how to use this)

* Synthetic data and jobs; the VPS was not reachable. Each of the 21 production jobs needs its own
  pairing on its own frozen source before `model=` is set.
* One completion with the source inlined: the real cron reads it through tools. A job can pass
  here and fail there.
* Template-bound scoring; commentary numbers are unchecked; three seeds do not exercise
  day-to-day data shape.
* `current` and `cheap` are one model family; errors are probably correlated.

## What this changes

Nothing in the default. `model` / `effort` / `max_steps` stay opt-in per job (as shipped in #1287);
no job's configuration is touched by this PR. The result supports trying `model=deepseek/
deepseek-v4-flash-0731` on jobs shaped like `activation_weekly`, `support_triage`,
`newsletter_metrics`, `reactivation_segments`, `cost_digest` and the health-check/`NOTHING_NEW`
pair — **after** repeating this harness on their real frozen data — and says to keep a larger
completion budget for any job that asks for a chain of divisions (`churn_report`).
