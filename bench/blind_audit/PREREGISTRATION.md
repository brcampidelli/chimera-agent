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
