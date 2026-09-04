# Does a summarised compaction keep what a note drops? — pre-registration

**Written before either arm ran. No outcome was seen first.**

`compact()` has accepted a `summarise` callable since it was written. No production caller has ever
passed one, so every compaction in the product replaces the dropped span with `_structural_note`:

> 21 earlier messages were removed to free context. Tools used in that span: write_file. Re-read any
> file you need rather than relying on memory of it.

That is a deliberate choice with its own defence, in the note's docstring: *"claiming to preserve
content we never read would be worse than saying plainly that N exchanges happened. The agent can
re-read a file; it cannot un-believe a fabricated summary."* This bench does not overturn that by
conviction. It measures it.

---

## What is lost, and the trap in measuring it

`RunState` restores the task, the plan and the open file; the system message survives verbatim. So a
constraint written in the *task* is already preserved, in both arms, and a probe that puts one there
**cannot exhibit the effect** — both arms pass and the run measures nothing. Verified before
designing anything: with `RunState.task` set to the convention it survives compaction; with it unset
it does not.

The production case is the second one. On the Code screen a fresh `Agent` is built per request, so
`run_state.task` holds *this* turn's message — a convention established in turn one of a
conversation lives only in the history being compacted, with nothing to restore it.

That is what the two papers `context_budget.py` already cites describe: compactors retain 17% of
injected session constraints (arXiv 2608.11242), and rule-form items survive far better than facts
(arXiv 2608.11392). The summariser under test is built on the second of those — it is asked for
standing instructions in imperative form, explicitly not for a narration of what happened.

## Arms

| arm | what replaces the dropped span |
|---|---|
| **A — note** (control) | `_structural_note`: how many messages went, which tools ran |
| **B — note + rules** (treatment) | the same note, **plus** the standing instructions of that span |

B contains A. That is deliberate and it constrains what a negative can mean: B cannot lose by
dropping information, only by **misleading** — a fabricated or garbled rule the agent then follows.
That is precisely the risk the note's docstring names, so it is the risk this design leaves exposed
rather than engineered away.

## The probe

A multi-turn conversation, on one workspace:

1. **Turn 1** establishes a convention in plain words and asks for a first small file.
2. **Turns 2–5** ask for more small files — enough traffic to force a compaction.
3. **Final turn** asks for one more file, without restating the convention.

Outcome: **does the final file honour the convention?** Checked mechanically against the file on
disk, never by reading the model's prose about what it did.

Conventions are chosen to be checkable without judgement (a required first line, a required suffix,
a forbidden construct) and varied across conversations so a model cannot luck into one.

`context_budget` is set low enough that a compaction fires within the conversation. That is the same
mechanism production uses, triggered sooner; a run in which no compaction fired is **void** and
reported as such rather than counted as a pass.

## Decision rule, fixed now

Primary outcome: the paired difference in "final file honours the convention", A against B, over the
same conversations, tested with McNemar via `chimera/eval/paired.py`.

- **ADOPT** if the paired delta is **≥ +15 percentage points** *and* **p < 0.05**.
- **REJECT** otherwise.
- **REJECT AND REPORT LOUDLY** if B < A. The summariser costs a model call per compaction; being
  *worse* than a free structural note would be the outcome the current design was chosen to avoid,
  and it would be a finding rather than a failed run.

Fifteen points rather than five: this intervention is not free. It adds one model call per
compaction, on the agent's own backend, and it replaces an honest count with something the agent
will believe. A small gain does not pay for that.

n = 30 paired conversations. A pilot of 6 runs first, and only to confirm the apparatus — that a
compaction fires and that the convention lands in the dropped span. **The pilot's outcomes are not
read**; if they were, the rule above would be a rule chosen after seeing data.

## Gates before the aggregate is believed

- **Compaction fired** in both arms of a pair, or the pair is void.
- **Three summaries read with human eyes**, against the span they compress. A fabricated rule is the
  failure this design is most exposed to and it will not show up in an aggregate.
- **A convention that the control also honours is not evidence.** If A passes at ceiling, the probe
  is too easy and the run cannot discriminate; that is a finding about the probe, reported as one.

## What is also measured, because the gain is not the whole story

- **Cost**: extra model calls, extra tokens, and added wall-clock per compaction.
- **Fabrication**: how often the summary states a rule that was never said. Counted by hand over a
  sample, and reported even at zero.
- **The other direction**: conversations where A honoured the convention and B did not, listed
  individually rather than netted away.

## What this cannot show

- **One model.** Today's parallel-tools census measured a 23-point spread between model families in
  how they use a tool-calling API; nothing here transfers across families without measuring there.
- **Synthetic conventions.** A stated rule about a first line is not a real user's mid-run
  correction, and it is the easy end of what compaction destroys.
- **A forced-low context budget.** Compaction fires earlier than production, so the dropped span is
  shorter and easier to summarise than a real one. This biases *towards* the treatment, and the
  bias is stated rather than corrected — correcting it would cost a very long run per pair.
- **Nothing about long-horizon drift.** One compaction per conversation. The failure that shows up
  after five is a different question.

## Result

`bench/compaction/RESULTS.md`, with the per-pair outcomes and the fabrication count.

---

## Amendment: the arms were not run

Written after calibrating the apparatus and **before** reading any outcome. The pilot mode prints
whether a compaction fired and what replaced the span; it does not print whether the convention
survived, and that column does not exist in it.

Calibration found that with `context_budget` at 0.04 no compaction fired in a six-turn
conversation — and 0.04 is already **fifteen times lower** than the 0.6 the Code screen sends.
The reason is the shape of the trigger: it is a fraction of the model's *advertised* window, and
the shipped default advertises **1,310,000 tokens**. So the Code screen compacts at 786,000, the
calibration ran at 41,920, and a six-turn conversation reaches 4,200. Compacting at all on this
model needs a fraction around **0.0025** — a factor of 240 below what production sends.

That prompted the question one layer up, which `traces.jsonl` answers for free: **zero compactions in
137 runs.** The behaviour these arms compare is not one production reaches.

Running them anyway would measure a configuration no surface uses, which is the failure this document
already named in its own terms — *"a probe that puts the constraint in the task cannot exhibit the
effect"* — wearing a different coat. `bench/fusion_paired` declined a 900-cell run on the same
grounds after its pilot showed the corpus saturated.

The rule above stands unchanged for whoever has a reason to run it. `bench/compaction/run.py` is the
harness; `RESULTS.md` records the census and the measurement it asks for instead.
