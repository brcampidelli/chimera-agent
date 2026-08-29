# Results — stages 1–2, n=40. Stage 3 is not run, by the rule fixed in advance.

Run 2026-08-28 · 40 logic-typed GSM8K items × a 3-model panel · seed 20260828 · prereg
`59e4d76c60ce`.

**Read `PREREGISTRATION.md` first.** The arms, the headroom gate, the adoption criterion and the
conditions for calling the item closed were fixed there before the first model call.

## The numbers

| arm | |
|---|---|
| `oracle` — the gold answer is in **some** panel answer | **100.0%** (40/40) |
| `member:gemini-3.1-pro-preview` | 100.0% (40/40) |
| `member:gpt-5.5` | 97.5% (39/40) |
| `member:claude-opus-5` | 95.0% (38/40) |
| **`vote_answer`** — a strict majority over the extracted number | **97.5%** (39/40) |
| **`vote_text`** — the shipped vote, a majority over prose similarity | **0.0%** (0/40) |

Extraction: **100.0%** of panel answers carried the registered `ANSWER:` line, so the ruler read
every arm the same way.

## 1. The shipped logic vote fires on nothing

`vote_text` **fired on 0 of 40 items** — and that number is reported separately from correctness on
purpose, because 0% correct has two opposite readings and only the firing rate says which.

`majority()` returning `None` does not mean the vote was wrong. It means the branch **declined**, and
the turn fell through to judge → synthesiser. So the finding is **inert, not harmful**: the
task-typed aggregation never does the thing it was built for, and every logic turn still pays for
the two calls the branch exists to avoid.

The mechanism is the one reproduced in the pre-registration before this ran: `majority()` clusters
with `difflib` over the **whole answer**. Three panelists reasoning their way to the same number in
their own words are three clusters of one, so no cluster ever holds a majority. The same clustering
in the other direction can elect a minority answer, since three answers differing in one digit are
one cluster whose representative is the **longest** member.

`chimera/fusion/task_type.py` opens by saying the branch exists so *"a correct minority answer must
not be averaged away by a synthesizer"*. Measured on its own target corpus, it neither prevents that
nor does anything else.

Counting the number instead of the prose takes it from 0.0% to **97.5%** — 39 discordant pairs to 0,
CI [+80.0%, +97.5%]. That comparison is really "the branch fires" against "the branch never fires",
and it should be read that way rather than as an accuracy improvement.

## 2. Stage 3 is not run: the headroom is 2.5 pp, below the registered 3.0 pp gate

**oracle − vote_answer = +2.5 pp**, 95% CI **[−1.5, +2.5]** — one item out of forty, not
significant. One single item in this sample holds a correct answer that the fixed vote does not
select, so there is nothing left for a validator to find.

By the rule fixed in advance, **stage 3 is not paid for**, and the honest outcome of the item is the
clustering fix alone. Union + validator is not warranted **on this corpus**.

## What this does NOT say — and the part that limits it

**The corpus is at ceiling.** `oracle` is 100.0% and one panel member is 100.0% on its own. The
headroom could not have been large here whatever the aggregator did, so *"no headroom"* is a
statement about **GSM8K against frontier models**, not about aggregation in general. The gate did
what it was for — it refused to spend on a comparison that could not discriminate — but a null it
produces is a null about the instrument as much as about the hypothesis.

This is the second corpus in this session to saturate, after `local_lift`, and the third pattern of
its kind in the project after the learning-lift series' three attempts at a 40–60% band. The
constraint is consistent enough to plan around: **frontier models do not leave headroom on authored
or classic suites**, and any experiment that needs headroom has to buy difficulty it did not choose.

Narrower limits, also registered in advance: this is arithmetic with one numeric answer, which is
the vote branch's whole domain and nothing else — it says nothing about the judge → synthesiser path
on open-ended work, where most fused turns go and where "the union of the distinct answers" is not
even well defined. And the panel is cached deliberately, so this measures **aggregation** and cannot
say whether a different panel would do better.

## An apparatus defect this run caught in itself

The provenance stamp did its job on its own harness. The cached panel carries **three different
`prereg` values** across 40 rows: `25087f1e96d0` (13 rows), `59e4d76c60ce` (24) and `MISSING` (2).
Nothing about the design or the prompt changed between them — the pre-registration was amended
mid-run to add `vote_answer`, and then a branch checkout removed the file from the working tree
while the run was still going.

The panel answers are unaffected: the prompt, the models and the sampling were identical throughout,
and the amendment changed the analysis rather than the data collection. The rows are comparable, and
the point is that this had to be **checked** rather than assumed — which is only possible because
the stamp was there.

Two smaller ones, fixed here: the report block still read a `free["vote"]` key that the `vote_text`
/ `vote_answer` split had removed, and the patch that should have updated it asserted nothing and
silently did nothing. The run reached the end of stage 2, printed every arm, and then raised
`KeyError: 'vote'` — which is the good version of that failure. A test now reads the source for the
stale key.
