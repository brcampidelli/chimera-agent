# Results — the shipped auditor passed 19 of 23 summaries that dropped the critical finding; the blind form caught 19 of 23, and cries wolf on half of the rest; told what a slice is and asked one question, the same auditor caught 23 of 23 — and 20 of 23 when the finding is a clause, where the two-call form behind it added nothing

Run 2026-09-11 · 23 real worker outputs (the production mid model on ten-document review tasks),
one critical sentence planted in the head, in the middle (where `_distill` cuts), or not at all —
69 envelopes built by the production `build_envelope` · two arms, three replications, two auditors
· prereg `PREREGISTRATION.md` (five dated amendments, each before the calls it concerns, and
one registered addendum for the third arm) · raw `results/2026-09-11-{mistral,deepseek}.jsonl`,
reports `results/2026-09-11-*-report.md`. The third arm (`shipped_dropped_only`, 207 calls, the
weak auditor) and the clause plant (addendum 2: a fourth position and the pipeline arms, ≈ 420
calls) are read in their own sections below.

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

**Stopped after nine rows** (amendment 5, same day): at one replication on eight items, four
workers, the run produced nine verdicts in about an hour — 40 to 883 seconds per row, the
reasoning over 24k characters or the route's throttling, the log cannot say which — and was
stopped so this file could be written. The nine rows, as they are:

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

What nine rows can say, and no more: on both `middle` envelopes the reasoning auditor **did** say
DROPPED under the shipped prompt (once with INVENTED and CONTRADICTION marked too — the
three-in-one verdict the weak auditor also gives), where the weak auditor said it on 4 of 23; it
also failed a `none` envelope under the shipped prompt, and the blind form both missed one
`middle` and flagged one `none`. If the first of those held over the corpus, the adherence would
be a property of the weak tier and not of the prompt — that is the sentence a completed run would
test, and it is not tested here. What is settled is that the weak-tier auditor is the one
production uses, and its number stands.

Cost of the nine rows: US$ 0.03.

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
and CONTRADICTION cannot occur on the production path; the spot-check prompt still graded them
when this section was written. The addendum below is what became of that clause.

## The third arm — the same auditor, told what the summary is, asked one question (registered addendum, same day)

The addendum in `PREREGISTRATION.md`, written after the sections above and before any call.
Checked again first: the spot check runs only when `evidence_refs` is non-empty, `build_envelope`
is that field's only writer, and it fills it only on the `_distill` path. No worker-written
summary reaches the spot check anywhere in the package, so two of its three checks graded a
failure that cannot occur. `shipped_dropped_only` is the production `verify(force_spot=True)`
with the two-call recovery off (so the call count matches the `shipped` rows) and one change:
the system prompt says the summary is a mechanical slice — *nothing in it was written by anyone,
so it cannot invent or contradict; what it can do is omit* — and grades `DROPPED` alone, with one
sentence naming the omitted result. Same 23 items × 3 positions, same weak auditor, three
replications, 207 calls, US$ 0.085. The `shipped` and `blind` rows are the ones above.

| arm | middle (the plant was cut) | head (nothing cut) | none (no plant) | discrimination | calls | tokens/item |
|---|---:|---:|---:|---:|---:|---:|
| `shipped` (three checks) | 4 / 23 | 5 / 23 | 0 / 23 | +0.17 | 1 | 5,463 |
| `blind` (two calls) | 19 / 23 | 13 / 23 | 11 / 23 | +0.35 | 2 | 8,782 |
| `shipped_dropped_only` | **23 / 23** | 6 / 23 | 5 / 23 | **+0.78** | 1 | 5,454 |

Paired on the same items, shipped → dropped-only: **middle** 0.17 → 1.00 (Δ +0.83, Newcombe
[+0.55, +0.83], discordant 19 for the new prompt, 0 for the old); **head** 0.22 → 0.26 (Δ +0.04,
[−0.08, +0.11], not significant); **none** 0.00 → 0.22 (Δ +0.22, [+0.03, +0.22]). Flip rate on
`middle`: **0 of 23** — every replication of every cut plant said DROPPED. Every FAIL line the
arm wrote is `DROPPED` (69 of 69 on middle), and read by hand the sentence under it names the
planted finding in substance: *the release blocker — cronlib 1.12.7 with a public RCE exploit and
no patched version*, *14 consecutive failed writes acknowledged to clients*, *the customer's legal
team has ruled the product cannot be used until every export is signed*. On `head`, the one
false alarm read in full names the plant itself as omitted while it sits in the summary — the
auditor missing a sentence in an 8,000-character head, which is the false-alarm class this
instrument can see. On `none` the five FAILs name results from the worker's own middle text
(*three customers use a competitor for part of the flow*; *the on-call should be paged now*)
that the cut did remove; whether they are critical is not something a plant-only corpus can
grade, so they are counted as false alarms, as registered.

**Against the registered predictions.** Detection was predicted to rise to 6–12 of 23; it rose to
23 of 23 — the direction right, the size wrong by the whole remaining range. False alarms on
`head` were predicted to fall to ≤ 3; they stayed at 6 (5 before, inside noise). False alarms on
`none` were predicted to stay at 0; they rose to 5. Two of three predictions failed and are
published as such. The mechanism read by hand in the first run — the auditor adopting the
summary's leading conclusion — is still in the prompt (it still reads the summary), so what the
sentence *nothing in it was written by anyone* removed was not the summary's voice but the
auditor's reason to defer to it: a slice has no author to agree with.

**What ships, by the two registered rules.** The addendum's rule: the ungradeable checks leave
the slice-path prompt unless detection falls below 4 of 23 — it rose to 23, so `_SPOT_SYSTEM` is
the DROPPED-only prompt and the three-check one is kept under its own name for this bench's
`shipped` arm. Then the first registration's second clause, applied to this arm's numbers: false
alarms on summaries that dropped nothing rose by 22 pp, more than the 15 pp that clause allows a
gate, so *nothing is rejected on the auditor's word* — a `DROPPED: FAIL` on the slice path is a
**recovery**: the sentence the auditor wrote is appended to the summary under the same labelled
heading (one call), `passed` stays true, no re-ask. That is a deviation from the addendum's own
sentence *nothing about the blind audit recovery changes*, and it is stated as one: the addendum
registered the prompt and not the consequence, and the consequence that stood — a spot FAIL
escalates to a re-ask and a second FAIL drops the result — was harmless while the check caught
4 of 23 and would, at 23 of 23, re-ask a worker for a cut the distillation made and reject a
correct output when the re-ask is cut the same way. The two-call audit still runs, but behind a
spot check that passed *without naming anything*; when the one-call check has named the omission
its two calls are not made. `recover_dropped=False` restores the gate as it was before #433.

**What this changes about the section above.** The two-call blind audit was shipped as the
recovery because the one-call auditor missed 19 of 23. On this corpus the one-call check now
misses none, and after it passes, the two-call audit's marginal detection is **0 of 23** at a
false-alarm rate of 11 of 23 on `none` items — so its remaining job is the content of a recovery
when the one-call check wrote no sentence. Whether it is worth its two calls at all was the
follow-up this arm named, measured below rather than assumed.

## Addendum 2 — the clause plant: does the two-call audit still earn its calls? (registered, same day)

The plant above is one labelled sentence in its own paragraph — the easiest shape for a
one-question auditor. A fourth position, `middle_clause`, drops the label, lower-cases the body
and folds it with a dash into an existing sentence wholly inside the cut: *…interview 4 would not
pay more for it — interview 5 records a legal ruling on the customer's side forbidding use of the
product until data is stored in-country; the other nine interviews never mention it…* The
instrument check passed on 23 of 23 (clause absent from the summary, present in the artifact,
inside a sentence) before any call. Three arms on the weak auditor, three replications,
US$ 0.30: the one-call check alone and the two-call audit alone on the clause position, and the
pipeline as it shipped after #437 — one call, then the two-call audit behind a pass that named
nothing — on all four positions, read per item by which stage changed the summary. (Those rows
were recorded under the arm name `production` and are relabelled `production_2call` in the
file, because the name would otherwise mean two pipelines; `production` now names what ships.)

| arm on `middle_clause` | caught | flip | calls |
|---|---:|---:|---:|
| one-call DROPPED check (`shipped_dropped_only`) | **20 / 23** | 0 / 23 | 1 |
| two-call blind audit (`blind`) | 17 / 23 | 5 / 23 | 2 |

Paired, one-call → two-call: 0.87 → 0.74, Δ −0.13, Newcombe [−0.20, +0.05], discordant 5 — the
two-call form caught **1** item the one-call check missed and missed **4** it caught.

| `production_2call`, by position | recovered by the check's own sentence | recovered by the two-call audit behind a silent pass | nothing |
|---|---:|---:|---:|
| head (nothing cut) | 6 | 4 | 13 |
| middle (sentence plant) | 23 | 0 | 0 |
| none (no plant) | 5 | **11** | 7 |
| middle_clause | 20 | **0** | 3 |

Behind the one-call check's three silent passes on clause plants, the two-call audit recovered
the plant on **0 of 3** items — and read replication by replication, 0 of 9: the lines it did
append on those items name other things (*the on-call engineer needs to be paged due to the
widespread connection-refused issue*; six interview one-liners). Behind silent passes on
summaries that dropped nothing it appended lines to **11 of 23**, and to 4 of 23 where the plant
sat in the head. Two calls per passing spot check, for that.

**Against the registered predictions.** One-call on clause plants predicted 12–18 of 23: **20**,
above the range again (the second time this prompt beat its own prediction upward). Two-call
predicted 10–16: 17, one above. The audit's marginal catches predicted 2–5: **0**. Its false
alarms behind a silent pass on `none` predicted 6–9: **11**. Two of four inside or at the edge,
two outside, all published.

**What ships, by the registered rule.** Marginal catches ≤ 2 of 23 → the two-call audit leaves
the default path. `EnvelopeVerifier(blind_audit=False)` is the default: a spot check that passes
without naming anything is one call and the summary is left as it was; a `DROPPED: FAIL` is
still the auditor's sentence appended (`recover_dropped`, unchanged). `blind_audit=True` runs the
two-call form behind a silent pass — the pipeline that shipped between #437 and this addendum —
and the two prompts stay in the module for the bench and for a corpus that shows otherwise. The
clause position is a permanent part of this bench.

**What this says about #433, plainly.** The two-call audit was the right answer to the
three-check auditor that missed 19 of 23; it was measured honestly and shipped as a recovery by
its own rule. The one-question prompt that replaced that auditor the same day made it
redundant on both plant shapes this bench can build, and its cost — two calls and a 48% chance
of growing a clean summary — was never worth paying behind a check that misses 3 of 23. The
mechanism the paper describes (the auditor adopting the summary's voice) is still real; what
changed is that a slice has no voice to adopt once the prompt says so.

## What this cannot show

One planted sentence per output, always its own paragraph; tool-free workers whose raw output is
prose; five templated domains, unevenly represented (interviews 8, postmortems 6, dependency audits
5, logs 3, pull requests 1); 23 items where 40 were registered, because the reasoning worker spent
its budget thinking on 17 of them. Two auditors, one temperature; the third arm on the weak
auditor only, against baseline rows from earlier the same day (a route change between the runs
sits inside the comparison; the flip rates per arm are printed for that reason). DROPPED only —
the code makes the other two impossible on this path. One clause shape — a dash-appended fact
at the end of a sentence; a clause that changes a sentence's meaning is not tested. And nothing about whether the synthesis *uses* the
recovered lines well; that is `bench/hierarchy`'s question, and `bench/hierarchy_equal_calls`
has just measured that on a weak backbone the synthesis is where needles are lost.
