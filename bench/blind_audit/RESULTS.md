# Results — the shipped auditor passed 19 of 23 summaries that dropped the critical finding; the blind form caught 19 of 23, and cries wolf on half of the rest

Run 2026-09-11 · 23 real worker outputs (the production mid model on ten-document review tasks),
one critical sentence planted in the head, in the middle (where `_distill` cuts), or not at all —
69 envelopes built by the production `build_envelope` · two arms, three replications, two auditors
· prereg `PREREGISTRATION.md` (two dated amendments, both before the first auditor call) · raw
`results/2026-09-11-{mistral,deepseek}.jsonl`, reports `results/2026-09-11-*-report.md`.

The instrument check passed on all 69 envelopes before any auditor was asked: every `middle`
plant was absent from its summary and present in the stored artifact; every `head` plant was in both.

## The production auditor — `mistral-small-3.2-24b-instruct` (the weak tier, what `hierarchy.py:335` hands the verifier)

| arm | `middle` — the plant was cut | `head` — nothing was cut | `none` — no plant |
|---|---:|---:|---:|
| `shipped` (summary + raw, "do not trust") | **4 / 23** said DROPPED | 5 / 23 | 0 / 23 |
| `blind` (extract from raw alone; compare to summary alone) | **19 / 23** | 13 / 23 | 11 / 23 |

Per item, majority of three replications; 414 calls, US$ 0.29.

- **Detection** (the primary): 0.17 → 0.83, paired Δ **+0.65**, Newcombe 95% [+0.34, +0.72],
  discordant 17 — 16 for the blind form, 1 for the shipped one. Registered: shipped ≤ 40%
  (**held**, 17%), blind ≥ 70% (**held**, 83%).
- **False alarms:** `head` 0.22 → 0.57 (+0.35 [+0.05, +0.47]); `none` 0.00 → 0.48
  (+0.48 [+0.23, +0.48]). Registered: a rise of 10–25 pp. **Did not hold — the rise is 35–48 pp**,
  and it is the number that decides what ships (below).
- **Discrimination** (middle FAIL − none FAIL): shipped +0.17, blind +0.35. The blind form is
  twice the reader; it is also an alarm.
- **The paper's signature, read by hand.** Every one of the 19 shipped-arm PASSes on a `middle`
  item carries the same sentence — *"The summary accurately reflects the raw output without any
  inventions, omissions, or contradictions"* — nineteen times, for nineteen different summaries
  that each cut a sentence saying a data exposure needed escalation, a release was blocked, an
  on-call had to be paged. That is what 2609.07680 measured at 94.4% adherence: an auditor handed
  the conclusion relays it. And when the shipped auditor *does* fail an item, it marks INVENTED
  and CONTRADICTION as often as DROPPED (11 / 10 / 10 across the replications) — two checks that
  cannot be true of a slice of its own source, which says the three lines are one verdict wearing
  three names.

## The second auditor — `deepseek-v4-flash-0731` (the mid tier)

**Stopped after seven rows** (amendment 5, same day): at one replication on eight items, four
workers, the run produced seven verdicts in forty minutes — 40 to 883 seconds per row, the
reasoning over 24k characters or the route's throttling, the log cannot say which — and was
stopped so this file could be written. The seven rows, as they are:

| item | arm | position | verdict | failed on | seconds |
|---|---|---|---|---|---:|
| `postmortems-1` | `blind` | head | PASS | — | 883 |
| `postmortems-1` | `shipped` | head | PASS | — | 842 |
| `postmortems-1` | `blind` | middle | PASS | — | 1130 |
| `postmortems-1` | `shipped` | middle | FAIL | INVENTED, DROPPED, CONTRADICT | 135 |
| `postmortems-1` | `blind` | none | FAIL | DROPPED | 297 |
| `postmortems-1` | `shipped` | none | FAIL | INVENTED, DROPPED, CONTRADICT | 117 |
| `postmortems-5` | `shipped` | head | PASS | — | 1148 |
| `postmortems-5` | `shipped` | middle | FAIL | DROPPED | 75 |
| `postmortems-5` | `shipped` | none | PASS | — | 783 |

What seven rows can say: on the two `middle` envelopes the reasoning auditor **did** say DROPPED
under the shipped prompt (with INVENTED and CONTRADICTION marked too — the same three-in-one
verdict the weak auditor gives), where the weak auditor said it on 4 of 23. If that held over the
corpus, the adherence would be a property of the weak tier and not of the prompt; that is the
sentence a completed run would test, and it is not tested here. What is settled is that the
weak-tier auditor is the one production uses, and its number stands.

Cost of the seven rows: US$ 0.03.

## What ships, by the rule written before the numbers

The registered rule: adopt the blind form **in place of** the spot check only if detection rose
≥ 20 pp on both auditors *and* false alarms rose ≤ 15 pp. Detection rose 65 pp; false alarms rose
35–48 pp. **The second condition fails**, so the blind form does not become a gate. The rule's
second clause was written for exactly this: *ship it as escalate only — the orchestrator re-reads
evidence; nothing is rejected on the auditor's word.*

The shape that shipped is a **recovery**, not a rejection. When the spot check passes, the blind
audit runs behind it (two more calls, only where the spot check already ran); the `[CRITICAL]`
findings it marks ABSENT from the summary are appended to the summary under a labelled heading,
bounded to six lines of 300 characters, and the synthesis reads them. `VerifyOutcome.recovered`
carries them; `passed` never moves on the audit's word. On this corpus that would have put the
cut sentence back in front of the synthesiser on 19 of 23 envelopes, at the price of a few extra
lines on 11 of 23 envelopes that had dropped nothing — lines that are, by construction, sentences
from the worker's own raw output, restated. A false alarm here costs tokens in the synthesis, not
a rejected result.

Recorded beside it, from the pre-registration's fourth clause: `_distill` is a slice, so INVENTED
and CONTRADICTION cannot occur on the production path; the spot-check prompt still grades them.
Left as they are, with this sentence in the file, because a worker-written summary would need them
and no path produces one today.

## What this cannot show

One planted sentence per output, always its own paragraph; tool-free workers whose raw output is
prose; five templated domains, unevenly represented (interviews 8, postmortems 6, dependency audits
5, logs 3, pull requests 1); 23 items where 40 were registered, because the reasoning worker spent
its budget thinking on 17 of them. Two auditors, one temperature. DROPPED only — the code makes
the other two impossible on this path. And nothing about whether the synthesis *uses* the
recovered lines well; that is `bench/hierarchy`'s question, and `bench/hierarchy_equal_calls`
has just measured that on a weak backbone the synthesis is where needles are lost.
