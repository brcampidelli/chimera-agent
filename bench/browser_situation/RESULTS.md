# S11 — does the browser situation module hurt browsing? Results

*2026-09-26 (UTC) · 96 of 96 solves, 0 errors, 0 resubmits · US$ 0.062 by receipt, US$ 0.166 charged at the
dearest seen rate, of the US$ 0.95 cap · `deepseek-v4-flash-0731`, every step served by DeepInfra (pinned,
no fallbacks) · `PREREGISTRATION.md` committed before any paid call (c5ff9c0d) · `python -m
bench.browser_situation.run --report` reprints it from `results/solves/`.*

## Verdict: no harm detected — and, as registered, no benefit measurable here

| | OFF | ON |
|---|---:|---:|
| success | 45 / 48 | 44 / 48 |
| handovers | 0 | **0** |
| mean prompt tokens per solve | 26,022 | 24,042 |
| mean uncached prompt tokens | 10,576 | 10,469 |
| mean tool calls / steps | 3.46 / 4.31 | 3.35 / 4.21 |
| receipt per success | US$ 0.00072 | US$ 0.00068 |

- **Pairs (task, replica): 48.** b = OFF only: **2** (F1 r0, P1 r0). c = ON only: **1** (N2 r1). Exact McNemar
  p = 1.0.
- **Harm rule:** b − c = 1, under the registered 2; no task passed both OFF replicas and failed both ON.
  **No harm detected.**
- **Floor:** the OFF arm disagreed with itself on 1 of 24 tasks (N2), the same size as the difference
  between arms.
- **The arm reached the prompt:** `module_in_prompt` is true on 48 of 48 ON solves and on 0 of 48 OFF.

## What the failures are

- **S3 failed in all four solves**, as in step 3: one of 48 identical "Add to cart" buttons.
- **F1 r0 and P1 r0 (ON)** and **N2 r1 (OFF)** are wrong picks: each clicked, read the target page and reported
  a real code of the wrong element. None is a refusal, a stop or a handover.

## Against the predictions

| prediction | outcome |
|---|---|
| success equal within one net pair; S3 fails in both arms | **confirmed** (b − c = 1; S3 0/4) |
| 0 handovers | **confirmed** |
| prompt tokens ON/OFF between 1.05 and 1.20 | **refuted**: 0.924, and 0.990 uncached. The module's ~330 tokens a call are below the spread of the pages themselves (a solve on the encyclopedia page reads a 64 k-character list), so the ratio measures which solves took an extra step, not the module |
| tool calls within ±0.5 | **confirmed** (−0.11) |
| G2 and S1 pass in both arms | **confirmed**: 2/2 and 2/2 in each arm — the typing sentence did not turn the contact form or the search box into a refusal |

## The behaviours the rules name (descriptive, as registered)

| | OFF | ON |
|---|---:|---:|
| a `read` right after an action that already returned the list | 1 | 3 |
| a second look at the same page (list and text) | 1 | 3 |
| the same path twice in a row | 0 | 0 |
| navigations | 50 | 49 |

The pattern behind both counts is one sequence — `navigate, click, read, read_text` — seen three times with
the module (F2 r0, F3 r1, R1 r0) and once without (R3 r0). The rule "take the cheapest look, not both" names
exactly this, and it did not reduce it. At 1 against 3 in 48 this is not a finding in either direction; it is
a reason not to claim the rule works.

## What this shows and what it does not

- **Shown:** on 24 ordinary browsing tasks with this model, turning the module on did not cost success beyond
  the arm's own replica noise, did not stop a run, did not refuse a form with personal details the request
  gave, and did not raise the prompt cost by a measurable amount.
- **Not shown, by design:** any benefit. The failures the rules address occur 0–6 times in 240 control
  solves on this corpus, so nothing here could show them fixed.
- **Not shown:** walls on a real site (the live-page run covers the detector:
  `bench/browser_element_list/RESULTS-situation.md`), injection, other models, tasks that need a sign-in.
- **n:** 48 pairs resolve a harm of roughly 15 pp or more; smaller effects are indistinguishable from the floor.
