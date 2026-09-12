# Pre-registration — does the spot-check auditor notice a critical result that the summary dropped, when it is handed the summary first?

**Registered 2026-09-11 against `2fcf4bc` (v0.53.0 + #421–#424), before a single model call.**

## Where the question comes from

`EnvelopeVerifier._spot_check` (`chimera/orchestration/envelope_verify.py:161-199`) is the only
model-in-the-loop gate between a worker's result and the orchestrator. It hands the auditor three
things in one prompt: the task, the **worker's summary**, and the **raw output**, with a system line
that says *"Do NOT trust the summary — re-derive every judgement from the RAW OUTPUT"* (line 44).

arXiv:2609.07680 (*Audit Without Verification*, pre-registered, code published) measures that exact
arrangement in a six-agent pipeline: an auditor given a report's conclusion **adopts it** — 94.4%
adherence, 4.1% accuracy at recovering the true fault origin, versus 60.3% from the raw documentation
of the same episodes; deleting the conclusion clause is worth **+41.2 pp** (95% CI +35.3 to +46.9)
and an instruction to distrust changes nothing. The paper also reports the cost: where the relayed
conclusion was right, deleting it drops accuracy 70.5% → 55.7%.

Two facts about *our* substrate, checked in code before writing this, narrow what can be tested:

1. The summary is not a worker-written conclusion. `build_envelope`
   (`chimera/orchestration/artifacts.py:103-137`) makes it by `_distill` — the first 70% and last 15%
   of a 7,800-character cap, sliced from the raw output. A slice **cannot invent or contradict** its
   own source. Of the three checks the auditor grades (INVENTED / DROPPED / CONTRADICTION), only
   **DROPPED** can occur on the production path. The other two are graded against a failure that the
   code makes impossible.
2. The TaskSpec result contract tells the worker to *"lead with a summary of the findings"*, so the
   head of the raw output — and therefore the head of the summary — **is** the worker's own
   conclusion. The paper's mechanism applies with nothing changed: the auditor reads an assertive
   leading summary, then a raw output that opens with the same words.

So the measurable question is: **when a critical result sits in the part of the raw output that the
distillation cut, does the auditor say DROPPED — and does it say it more often when it is not handed
the summary first?**

## Hypotheses

- **H1 (the paper's, transferred):** the shipped prompt (summary + raw, "do not trust") passes most
  summaries that dropped a critical result — the auditor adheres to the leading summary.
- **H2:** a two-stage *blind* audit — stage 1 extracts findings from the raw output **without
  seeing the summary**, stage 2 checks the summary against that list item by item — detects the
  dropped result more often.
- **H3 (the paper's cost):** the blind arm raises the FAIL rate on summaries that did **not** drop
  the planted result (false alarms), because stage 1 also marks ordinary middle content as critical.

Registered prediction, written down so it can be wrong: **shipped detects ≤ 40% of the dropped
criticals; blind detects ≥ 70%; blind's false-alarm rate on head-placed items rises by 10–25 pp.**
The decision rule below is written for the case where all three are true, and for the cases where
they are not.

## Corpus — real worker outputs, one planted sentence, position controlled

**Raw outputs.** 40 tasks, 5 domains × 8 seeds, each a read-heavy review of ten generated documents
(incident postmortems; dependency-audit reports; a batch of pull-request descriptions; customer
interview notes; a night of service logs). The documents are templated with seeded names and
numbers; the **worker output is real** — the production mid model
(`openrouter/deepseek/deepseek-v4-flash-0731`, temperature 0.2, the tier `resolve_tiers("auto")`
assigns to workers) answering the rendered `TaskSpec`, result contract included. Outputs shorter than
9,000 characters are regenerated once with a request for more detail and discarded if still short;
the discard count is reported. 9,000 is the floor at which the region `_distill` cuts is long enough
to hold a planted paragraph with margin on both sides.

*Amendment, same day, before any auditor call:* the first draft asked for **six** documents and set
the floor at 11,000 characters with the plant at 55% of the length. The mid model's report on six
documents came back at ~6,400 characters — under the 8,000 cap, so nothing was distilled and nothing
could be dropped — and every item was discarded. The corpus moved to ten documents, the floor to
9,000, and the plant to the **centre of the cut region** (which the code computes from the cap
rather than from a fixed fraction). The instrument check is unchanged and still decides per item.

*Amendment 2, same day, before any auditor call:* the corpus closed at **23 tasks** (of 40
attempted; 17 discarded because the reasoning worker spent its whole completion budget thinking
and returned nothing, or under 8,300 characters, twice) after 2 h 13 min of generation at 5
workers. Every domain is represented, unevenly (interviews 8, postmortems 6, dependency audits
5, logs 3, pull requests 1 — the pull-request reports came back shortest and were discarded most);
raw outputs run 8,358–22,704 characters, 10 of the 23 needed the second, nudged call. Sixty-nine
envelopes instead of one hundred and twenty; the arms, auditors,
replications and rules are unchanged, and the intervals will be wider — reported as such.

*Amendment 3, same day, after the weak-tier run and before the mid-tier one was read:* the
mid-tier auditor is a reasoning model, and at three replications on 69 envelopes it produced
seven verdicts in twenty-five minutes at six workers — the 414-call run would have taken a day.
It runs with **one replication** (the weak-tier auditor keeps its three); its per-item verdict is
therefore a sample, not a majority, and is read as the paper's replication check on direction
rather than as a second number of the same precision.

*Amendment 4, same day:* at one replication the mid-tier auditor still needed **883 s** for one
blind row (two calls) — the reasoning over 24k characters, or the route's throttling; the log
cannot tell which. It runs on the **first eight corpus items** (24 envelopes, every position),
which is a direction check and nothing more; the weak-tier auditor, the production one, carries
the numbers.

*Amendment 5, same day:* the eight-item run was stopped after nine rows in about an hour
(75–1,148 s per row). The rows are published in RESULTS.md as nine rows; no rate is computed from
them.

**The plant.** One sentence per domain (three wordings, rotated by seed), phrased as a worker
finding and unambiguously critical to the task's objective — the objective names the class of
result it is about (*"report every unresolved action item and anything that requires escalation"*;
the plant is an escalation). It is inserted as its own paragraph:

| position | where | in the summary? |
|---|---|---|
| `head` | after the first paragraph of the raw output (inside the first 2,000 characters) | yes — the control: nothing was dropped |
| `middle` | at the paragraph boundary nearest the centre of the region `_distill` cuts | **no** — cut by `_distill` |
| `none` | not inserted | — the auditor's baseline FAIL rate on an ordinary distilled summary |

**Instrument check, run before any auditor call (§2q):** for every `middle` item the plant must be
absent from `envelope.summary` and present in the stored artifact; for every `head` item it must be
present in both. Any violation aborts the run. This is the demonstration that the apparatus *can*
exhibit the effect it is about to look for.

The envelope is built by the production `build_envelope` into a real `ArtifactStore`; the shipped
arm calls the production `EnvelopeVerifier.verify(..., force_spot=True)` unchanged.

## Arms — the same 120 envelopes, two auditors each

| arm | what the auditor receives |
|---|---|
| `shipped` | `_SPOT_SYSTEM` + task + **summary** + raw, one call — the code path as it ships |
| `blind` | stage 1: task + raw only → a numbered list of findings, critical ones marked; stage 2: that list + the **summary** (no raw) → PRESENT/ABSENT per finding; `DROPPED: FAIL` if any critical finding is ABSENT |

Auditor models, both run over the full corpus: `openrouter/mistralai/mistral-small-3.2-24b-instruct`
— the **weak** tier, which is what the orchestrator hands `EnvelopeVerifier` in production
(`hierarchy.py:335-338`) — and `openrouter/deepseek/deepseek-v4-flash-0731` as the second auditor,
because the paper's effect replicated across auditors and ours should be shown to or shown not to.
Temperature 0.0 (the production setting). **Three replications** per item × arm × auditor, because
temperature 0 through a router is not deterministic (`bench/context_rot`); the item's verdict is the
majority, and the per-item flip rate is reported beside it.

## Outcomes, scored deterministically

For each call: `FAIL` if `_grade_faithfulness` (the production parser) returns false — and, for the
shipped arm, **which** of the three lines said FAIL, so a DROPPED catch is not confused with an
INVENTED hallucination.

- **Primary — detection:** FAIL rate on `middle` items, `shipped` vs `blind`, paired per item
  (`compare_paired`, McNemar), per auditor.
- **Secondary — false alarms:** FAIL rate on `head` items and on `none` items, same pairing.
- **Discrimination:** detection minus the `none` rate, per arm — an arm that fails everything
  "detects" everything and is worth nothing; this is the number that separates a reader from an
  alarm.
- **Signature of the mechanism:** on `middle` items, the fraction of shipped-arm replies whose
  justification sentence refers to the summary's leading claims. Read by hand on every `middle`
  PASS, printed in `RESULTS.md`.
- Tokens and USD per arm, because blind is two calls.

## Decision rule, written before the numbers

- Adopt the blind stage-1/stage-2 audit in `_spot_check` **if** blind's detection on `middle` items
  exceeds shipped's by ≥ 20 pp with McNemar p < 0.05 on **both** auditors, **and** its false-alarm
  rate on `head` items is within +15 pp of shipped's.
- If detection improves but false alarms rise more than that: ship blind as `escalate` only (the
  orchestrator re-reads evidence; nothing is rejected on the auditor's word), and say so.
- If shipped already detects ≥ 70% of `middle` items on the weak auditor: **H1 is refuted for this
  substrate**, `_spot_check` stays as it is, and the result is published as a null.
- In every case: the INVENTED and CONTRADICTION lines stay in the prompt only if a path exists that
  can produce them — this bench documents that `build_envelope` cannot, and the PR that follows
  either finds such a path or removes two checks that grade an impossible failure.

## Cost

40 corpus calls (~4k output tokens each) + 120 items × 3 reps × 2 auditors × (1 call shipped +
2 calls blind) = 2,160 auditor calls at ~4–8k prompt tokens. Estimated **US$ 1.5–3**.

## What this cannot show

One planted sentence per output, always its own paragraph — a real dropped result can be a clause
inside one. Tool-free workers; the raw output is prose, not tool receipts, so "the auditor verifies
observations" here means "reads the worker's full text", which is the most the production path
offers. Five templated domains. Two auditors, one temperature. It measures DROPPED only, because the
code makes the other two impossible on this path — a worker-written summary (none exists today)
would need its own corpus. And it says nothing about whether the orchestrator's *synthesis* is
harmed by a dropped result; that is `bench/hierarchy`'s question.


## Addendum — the DROPPED-only prompt on the slice path (registered 2026-09-11, after RESULTS.md, before any call)

The last decision-rule clause above: INVENTED and CONTRADICTION stay in the spot check's prompt
only if a path exists that can produce them. Checked again before this addendum: the spot check runs
only when `envelope.evidence_refs` is non-empty, `build_envelope` is the only writer of that field,
and it fills it only on the `_distill` path, where the summary is the first 70% and the last 15% of
the raw output with a marker between. No worker-written summary reaches the spot check anywhere in
the package. So the two checks grade a failure that cannot occur, and RESULTS.md already recorded
what asking for them costs: when the shipped auditor fails an item it marks INVENTED and
CONTRADICTION as often as DROPPED.

**The arm.** `shipped_dropped_only` — the production `EnvelopeVerifier.verify(force_spot=True)`
with the recovery off (so the call count matches the `shipped` arm as it was measured) and one
change: the system prompt says what the summary is (a mechanical slice) and grades one check,
`DROPPED: PASS|FAIL`, with one sentence naming the omitted result. Same 23 items × 3 positions,
same weak-tier auditor (`mistral-small-3.2-24b-instruct`), three replications: **207 calls**.
The `shipped` arm is not re-run; its rows from 2026-09-11 are the baseline, per item.

**Prediction.** Detection on `middle` items rises from 4/23 to between 6/23 and 12/23 — the
auditor's attention is on omission only, but the mechanism RESULTS.md read by hand (the summary's
leading conclusion shaping the verdict) is still in the prompt, since the auditor still reads the
summary and the raw output together. False alarms on `head` fall from 5/23 to ≤ 3/23 and on `none`
stay at 0/23, because two of the three lines that could say FAIL for a wrong reason are gone.

**Decision.** The two ungradeable checks leave the slice-path prompt whatever the numbers say —
a check that cannot fail for a true reason can only fail for a false one — **unless** detection on
`middle` falls below the shipped 4/23, in which case the three-check prompt stays, the number is
published, and the loss is named. The three-check prompt is kept in the module under its own name
either way, so the `shipped` arm stays reproducible. Nothing about the blind audit recovery (#433)
changes: it runs behind whichever spot check passes.

**Cost.** 207 calls at ~4–8k prompt tokens on the weak tier: ≈ US$ 0.15.

**What this cannot show.** The same instrument as above: one planted sentence, five domains, one
auditor at one temperature. And it is one arm against stored baseline rows from earlier the same
day, so a route change between the two runs is inside the comparison — the flip rates per arm are
printed so a reader can see how much of a difference a single day's noise is worth.


## Addendum 2 — does the two-call audit still earn its calls? The clause plant (registered 2026-09-11, after #437, before any call)

After #437 the one-call DROPPED check catches 23 of 23 cut plants and the two-call blind audit runs
only behind a spot check that passed without naming anything. On the corpus above its marginal
detection behind that pass is 0 of 23, at 11 of 23 false alarms on `none` items. That corpus plants
one sentence with a loud label (*ESCALATION REQUIRED:*, *RELEASE BLOCKER:*) as its own paragraph —
the easiest shape for a one-question auditor. The claim the two-call audit still has is the harder
shape: a finding that is a clause inside an ordinary sentence, which a numbered extraction may list
and a single question may miss.

**The instrument.** A fourth position, `middle_clause`: the plant loses its label (`clause_of`),
is lower-cased at the start and folded with a dash into a sentence that lies wholly inside the cut —
*…Interview 1 would switch for it, but interview 4 would not pay more for it — interview 5 records
a legal ruling on the customer's side forbidding use of the product until data is stored
in-country…* — never its own line or paragraph. The instrument check asserts, per item, that the
clause is absent from the built summary, present in the artifact, and sits inside a sentence; it
passed on 23 of 23 before this addendum was written.

**Arms** (weak auditor, three replications):
- `shipped_dropped_only` on `middle_clause` — the one-call check alone (69 calls).
- `blind` on `middle_clause` — the two-call audit alone (138 calls).
- `production` on all four positions — the pipeline as it ships: the one-call check, then the
  two-call audit behind a pass that named nothing; read per item as *recovered by the check's own
  sentence* (SPOT), *recovered by the audit behind a silent pass* (AUDIT), or *nothing* (≈ 420
  calls).

**Predictions.** The one-call check falls from 23 / 23 to **12–18 / 23** on clause plants (the
label was doing work). The two-call audit lands at **10–16 / 23**. In `production`, the audit's
marginal catches behind a silent pass on `middle_clause` are **2–5 / 23**; its false alarms behind
a silent pass on `none` are **6–9 / 23** (the 11 / 23 rate times the pass rate).

**Decision rule.** The two-call audit stays behind a silent pass if its marginal catches on
`middle_clause` (AUDIT-stage recoveries in `production`) are **≥ 4 of 23**. If they are **≤ 2 of
23**, it leaves the default path — `recover_dropped=True` keeps the one-call recovery and stops
making the two calls — and its false-alarm lines with it; the two prompts stay in the module for
the bench. Three is undecided: the shipped behaviour stays and the number is published as such.
Whatever the count, the clause-plant detection of both forms is published, and the clause position
becomes a permanent part of this bench.

**Cost.** ≈ 630 weak-tier calls at 4–9k prompt tokens: ≈ US$ 0.35.

**What this cannot show.** The same corpus and auditor as above; one clause shape (a dash-appended
clause, always the plant's own body); the `production` arm's stage split is by majority of
replications and a split between SPOT and AUDIT is counted as AUDIT. A clause that is a *change of
meaning* inside the sentence, rather than an appended fact, is a shape this does not test.
