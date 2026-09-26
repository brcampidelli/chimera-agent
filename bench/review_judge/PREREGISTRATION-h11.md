# Pre-registration — H11: a typed three-state verdict, out of sample

**Registered 2026-09-25, before any call.** Study 25, wave 3, arm H11 (`bench/PLAN-study25-system-prompts.md`
§2.9, §7 S8/S15, §9). The design brief is `NOTE-h11.md`, "What H11 needs instead", item 1. The owner
approved the spend. **Hard cap US$ 9.00**, pilot included; expected about US$ 7.

## Why this, and only this

`NOTE-h11.md` showed that H11's question — does the review rubric raise precision without collapsing
recall? — was already answered for the rubric's *content*: arm C, out of sample, bought +4.5 pp of
precision for −35.9 pp of recall of correct comments. The one element the study's principles add that
this directory has never measured is a **structure**: a typed three-state verdict — confirmed,
plausible, refuted — that quotes the line it rests on. One call then gives two operating points,
declared here, instead of one baked into the wording. That is what this run measures.

In this bench's terms, for a filter over fixed, human-labelled review comments:

- **precision** — the share of kept comments that are correct (keeping everything gives the
  prevalence, 77.6% out of sample);
- **recall of correct comments** — the share of correct comments kept (1 − false rejection).

## Items

- **The 814 out-of-sample items** of the Diff Level slice: the graded rows of the August full-slice
  run that were not in the pilot draw arm C's rubric was written against (632 correct, 182 incorrect).
  `run_h11.py` selects them with `run_judge.everything` and `attach_patches` and **refuses to run**
  unless the selection equals the August arm A rows' own out-of-sample keys, exactly.
- Items are processed in one seeded order (`random.Random(20260819).shuffle`). The **first 200** of
  that order carry the replay arm (159 correct, 41 incorrect), so the floor is measured first.

## Model and route

- **`openrouter/deepseek/deepseek-r1`**, the judge of every run in this directory.
- On 2026-09-25 OpenRouter lists **one** provider for it: **Novita** (fp8; US$ 0.70/M prompt, US$ 2.50/M
  completion; 16k completion ceiling). Every call is pinned to it with
  `extra_body={"provider": {"order": ["Novita"], "allow_fallbacks": false}}`, the pin is written into the
  manifest, and every row records the provider that served it.
- The August runs recorded no route. A reference measured on an unknown route is a reference to
  re-measure (§2ae), which is why arm A is run fresh here rather than read from disk.
- Temperature 0.0. **Reasoning at the model's default** — `thinking=` is not passed, as in August.
  No `max_tokens` (`CHIMERA_COMPLETION_CEILING=0`), as in August, when the ceiling did not exist.
  Completion cache off. One item per call.

## Arms

Both fresh, same session, same pinned endpoint. **Each item launches all its arms at the same time**,
so the arms of one item share the same minutes and the same route; items run several at a time.

- **A — the shipped cautious judge.** `run_judge.system_prompt("cautious")`, sha `60a2749ed8c9`, the
  published arm A's; called through `run_judge.ask` itself, so the prompt, the user message and the
  verdict parser are the published ones. `--dry-run` checks the hash and that the user message is the
  one `ask` sends, on every item.
- **A2 — arm A again**, on the first 200 items only: the replay floor.
- **T — the three-state verdict**, sha `5548fe16010b`. The same user message as A, byte for byte. The
  system prompt, frozen here:

```
You are reviewing a code-review comment, not the code. Decide whether the comment identifies a REAL defect in the diff shown.

Give one of three verdicts:
  - confirmed: a line of the diff shows that the defect the comment reports is there;
  - refuted: the code the comment describes is not in this diff, or a line of the diff contradicts its central claim;
  - plausible: neither of those — the diff does not show the defect, and nothing in it contradicts the comment.

Quote the one line of the diff your verdict rests on, copied exactly as it appears. Leave the quote empty only when the code the comment describes is not in this diff.

Answer with JSON and nothing else, with the keys in this order:
{"quote": "<one line of the diff, verbatim>", "reason": "<one line, the evidence>", "verdict": "confirmed" | "plausible" | "refuted"}
```

**What differs from A, and why that much.** The header is A's. The two grounds for *refuted* are A's
two rejection grounds, word for word. What changes is the answer: three states and a quoted line
instead of approve/reject, with the evidence written before the verdict. A's stance paragraph goes
with it: its last sentence — *"When your evidence falls short, APPROVE"* — is A's rule for the
uncertain middle, and here the middle is a state of its own. The cost asymmetry that paragraph argued
for moves to the operating point, chosen after the call. The pilot's arm B measured what removing
the stance does on its own: J 15.1 → 15.0 on the 105. So a difference here is read as the structure's,
with that measurement as the reason, not as proof.

## Operating points, declared in advance

| operating point | kept by T | compared with A's |
|---|---|---|
| **drop only refuted** | confirmed + plausible | approve |
| **keep only confirmed** | confirmed | approve |

## Metrics

- **Primary, at each operating point:** precision of kept comments and recall of correct comments, T
  and A, over the items where both arms gave a readable verdict. Differences T − A are **paired**:
  bootstrap over items, resampled within label, seed 20260819, 10,000 draws, percentile 95% —
  `read_h11.py`'s `paired_precision`, draw for draw, with recall read off the same draws. Δ recall is
  also printed with `chimera/eval/paired.py`'s McNemar–Wilson interval, as `read_full.py` does; the
  rule uses the bootstrap.
- **Floor:** A2 against A on the 200 — verdict flips, and Δ precision and Δ recall with the same
  bootstrap.
- `unparsed` (a model answer the parser could not read) and `call_failed` (a call that never completed
  after three attempts) are counted apart, per arm, always.

**The parser** (`run_h11.parse_three`) reads, in order: the reply's JSON object; a `"verdict": "<state>"`
pair when the object does not load (an unescaped `"` in a quoted code line); a bare state word only
when exactly one of the three appears in the reply. Anything else is `unparsed`. The way it read each
answer is kept in the row.

## Adoption rule

At an operating point, **adopt** when all three hold:

1. the paired 95% lower bound on Δ precision (T − A) is **above 0**;
2. the Δ precision point estimate is **larger than the replay floor's** |Δ precision (A2 − A)|;
3. the paired 95% lower bound on Δ recall of correct comments (T − A) is **≥ −2.0 pp**.

H11's three-state verdict is adopted if it passes at **either** operating point and no uninformative
condition fires. The rule cannot fire on null deltas (checked in `read_h11_three.py`). Adoption means
it is reported as the candidate form for the verifier (`S8`/`S15` L1) with its operating point; the
coordinator adopts it in a separate PR. **No product code changes here.**

## Positive control and controls

- **The statistic reproduces a published number, and that number is the positive control.**
  `read_h11_three.py` refuses to read anything unless its bootstrap, fed the August arms A and C out of
  sample, returns exactly `read_h11.py`'s +4.5 pp [+2.0, +7.1] for C − A in precision. An effect of that
  size, on these 814 items, with this method, is one this instrument has already shown it can see.
- **Arm A reproduces the published arm A (§2aa).** Fresh A is compared with the August A on the same
  814 items. It reproduces if it keeps 88.4–96.4% of correct comments and catches 9–25% of incorrect
  ones (the published 92.4% and 17.0%, widened for a different route). If it does not, the paired
  comparison still stands — both arms are fresh — but no sentence compares T with the August numbers.

## Uninformative conditions

- `unparsed` or `call_failed` above 10% of the 814 in either arm;
- fewer than 700 paired items;
- T answers one state to more than 98% of items;
- **the adoption rule, applied to A2 against A, passes** — a rule a re-run can pass decides nothing.

## n, and what it can resolve

n is fixed by the design: all 814, 632 of them correct. The paired standard error of Δ recall is about
√(p_d / 632), where p_d is the share of correct comments on which T and A disagree:

| p_d | 95% half-width on Δ recall |
|---:|---:|
| 0.04 | ±1.6 pp |
| 0.06 | ±1.9 pp |
| 0.10 | ±2.5 pp |
| 0.20 | ±3.5 pp |

So above p_d ≈ 0.07, the −2 pp bound needs a true Δ recall above zero. **Keep only confirmed** passes it
only if T confirms nearly every correct comment A keeps, which the predictions below do not expect. On
precision, the August C − A of +4.5 pp resolved at ±2.5; a gain much under 2 pp is below what these 814
items can separate from zero.

## Predictions

| | predicted |
|---|---|
| T's shares: confirmed / plausible / refuted | 55–80% / 10–35% / 5–15% |
| drop only refuted: Δ precision · Δ recall of correct | −1.0 to +2.0 pp · −3.0 to +3.0 pp |
| keep only confirmed: Δ precision · Δ recall of correct | +1.0 to +6.0 pp · −35 to −10 pp |
| separation, AUC of T's three ordered states − AUC of A's binary verdict | +2 to +8 points |
| replay flip rate (A2 against A) | 5–12% |
| fresh A: keeps correct · catches incorrect | 88–96% · 10–25% |
| **decision** | **not adopted at either operating point** — the structure moves the threshold more than the separation |

## Secondary, registered and not decided on

- **S1 — separation:** AUC of T's ordered states (refuted < plausible < confirmed) against A's binary
  verdict, paired bootstrap on the difference.
- **S2 — rejection recall and Youden's J** per operating point, for continuity with `RESULTS.md`.
- **S3 — is the quote in the diff?** Whitespace-collapsed, diff markers dropped; per state, and the
  precision of *confirmed* by whether its quote is found. A check on grounding, not a filter.
- **S4 — sensitivity:** an `unparsed` answer counted as kept (fail-open) in both arms.

## The pilot, before the main run

- **20 of the pilot's 105 in-sample items**, 10 correct and 10 incorrect, seeded; arms A and T only.
  Those items are not among the 814, so nothing the pilot shows can be fitted to the items that decide.
  **The pilot's rows never enter the read.**
- **What it measures:** cost per call, latency, parse rate, the route. At least 10 raw T answers are read
  by eye before the parser is trusted. The token-based cost is checked against OpenRouter's own record of
  the pilot's generations.
- **What it may change,** in an amendment committed before the main run: the parser (never the prompt),
  the number of items in flight, timeouts. If T is `unparsed` on 3 or more of 20, the arm stops there and
  the amendment says why.

## Budget and stop rule

- **Cap US$ 9.00**, pilot included. Cost = tokens × the provider's listed price, as each row lands.
1. **Before the main run:** the projection from the pilot's cost per call (814 × (A + T) + 200 × A, plus
   the pilot) must not exceed US$ 9.00. `run_h11.py --run` aborts if it does.
2. **During it:** once the spend reaches **US$ 8.75**, no new item starts. The read is then over a
   seeded-random prefix of the order, labelled incomplete, and the uninformative conditions apply.
3. **After 100 items:** `call_failed` or `unparsed` above 10% in A or T stops the run (a `STOP` file the
   driver will not run past).
4. **Any call served by another provider** stops the run.

**Blocks.** The driver runs under `timeout 3000` in WSL, starts no new item after 2300 s, waits for the
items in flight, and on relaunch skips every item already in `rows.jsonl`. An item's arms are written
together or not at all.

## What this cannot show

- **Other models, routes or days.** One model, one provider (fp8), one session.
- **The finder side.** The comments are fixed inputs; coverage, "never only important", P0–P3 and an
  explicit "no findings" need a generation bench (`NOTE-h11.md`, item 2).
- **Whether the quoted line is the right one** — only whether it is in the diff.
- **T's own replay floor.** Only A is replayed; the floor is A's.
- **Which operating point a product should use.** The rule names one if it passes; the cost asymmetry
  behind it is a product decision.
- **The label ceiling.** About 6 of the pilot's 53 incorrect comments are unfalsifiable from the diff
  (`RESULTS.md`); no verdict form reaches those.
- **Structure against content.** T keeps A's grounds; whether the three-state form would rescue arm C's
  grounds is a different arm.

## Amendment 1 — 2026-09-25, after the first pilot, before any main-run call

**What fired.** The pilot clause above: arm T came back `unparsed` on 4 of 20. So did arm A, on 7 of 20,
the same published prompt and parser that had 0 of 919 unparsed in August. Every one of those 11
answers was an **empty `content`** with `finish_reason: stop` and a full bill of completion tokens.
The format was not failing: all 16 readable T answers were well-formed JSON (two inside a ```json
fence), every one of them quoted a line that is in the diff, and the parser read all 16 correctly.
Read by eye, all 20, before this amendment was written.

**What it is.** An interface defect (PROTOCOL §4), found by a probe of four direct `litellm` calls on
two of the empty items (US$ 0.013, not through the gateway, rows not kept): in one of the four,
`content` was `None` and the whole output — reasoning, then `Output: {…}`, then the final
`{"reason": …, "verdict": "approve"}` inside a ```json fence — was filed under `reasoning_content`,
with the provider's `reasoning_tokens` (1249) larger than its `completion_tokens` (1088). On this route
the model sometimes never closes its reasoning, and the provider then puts the answer where the
gateway does not read. The August route, whatever it was, did not.

**The change — the harness, not the prompt and not the parser.**

- When a reply's `content` is empty, the answer is the **last JSON object carrying a `verdict` key in
  the reasoning field** (`run_h11.answer_in_reasoning`), handed to the arm's own parser as if it had
  been the content. Scanning from the end takes the model's last stated answer; a restatement of the
  requested format (`"approve" | "reject"`) is not valid JSON and is never taken. Nothing is
  re-sampled: re-asking until `content` is non-empty would select on the form of the answer.
- Same rule, both arms, and only on empty `content`. `run_judge.ask` still parses arm A.
- The reasoning field is read by wrapping `litellm.completion` inside the bench process, because the
  gateway's `CompletionResult` does not carry it. No product code changes.
- Every call records `answer_from` (`content` | `reasoning` | `none`), the reasoning's length and, when
  recovery ran, its last 1500 characters, so each recovered answer can be audited against its source.
- **New secondary S5:** per arm, where the answers came from and the verdicts on each side of that
  split. Not decided on.
- The probe's `usage.cost` equalled tokens × the listed price on all four calls (e.g. 756 prompt / 1326
  completion → US$ 0.0038442), so the token-based cost stands; each row now also carries `billed_usd`.

**The first pilot is void.** Its rows are kept in `results/h11/pilot-void/` as the record of the defect
and are never read. A **second pilot** runs on the same 20 items with the change in place; if either
arm is still `unparsed` (or `answer_from: none`) on 3 or more of 20, the arm stops there, with no
further amendment.

**Budget.** Spent before the second pilot: US$ 0.1745 (void pilot) + US$ 0.0129 (probe) = US$ 0.187,
passed to the main run as `--prior-usd` and counted against the cap. The void pilot's per-call costs —
A US$ 0.00339, T US$ 0.00534 (T writes longer, 1918 completion tokens against A's 1154) — project the
whole run at about US$ 0.19 + 0.17 (second pilot) + 814 × 0.00873 + 200 × 0.00339 ≈ **US$ 8.14**, under
the cap. The projection that gates the run is recomputed from the second pilot. The stop at US$ 8.75
stands.

**Observed and not acted on.** In the void pilot, T answered `confirmed` to 14 of its 16 readable
answers, `refuted` to 2 and `plausible` to none. The prompt stays frozen and the predictions stay as
registered.

## Amendment 2 — 2026-09-25, after the second pilot, before any main-run call

**The second pilot passed its gate.** Same 20 items, Amendment 1's harness: `unparsed` 0/20 in A and
0/20 in T, `call_failed` 0, every call served by Novita. The answer came from the reasoning field on
**11 of 20 A calls and 7 of 20 T calls**. All 18 recovered answers were read against the last 1500
characters of their reasoning: each is the final JSON the model wrote; where a draft and a final
both appear, the final was taken, and in no case did draft and final disagree on the verdict. Every
T answer is well-formed; 19 of 20 quotes are found in the diff.

**One parser change, from that reading.** Two T answers quoted a code line indented with **literal tab
characters**, which strict `json.loads` rejects; the pattern fallback read both verdicts correctly,
but the quote kept its escapes (so S3 marked one as not found), and the recovery path uses the same
decoder — a tab-indented final answer in the reasoning would have been skipped for an earlier draft
or for nothing. All three decodes now accept control characters inside strings (`strict=False`):
`parse_three`, its quote unescaping, and `answer_in_reasoning`. Re-parsing every stored T answer of
both pilots with the amended parser moves **no** verdict. Nothing else changes; the prompt stays frozen.

**Cost and the gate on the run.** Measured on the second pilot: A US$ 0.00327 per call (1106 completion
tokens), T US$ 0.00556 (2008); OpenRouter's billed `usage.cost` equals tokens × price on all 40 calls.
Spent before the main run: US$ 0.1745 (void pilot) + 0.0129 (probe) + 0.1766 (second pilot) =
**US$ 0.364**. Projection: 0.364 + 814 × (0.00327 + 0.00556) + 200 × 0.00327 ≈ **US$ 8.21**, under the
US$ 9.00 cap; `run_h11.py --run` recomputes it and aborts above the cap. The stop at US$ 8.75 stands:
if T writes longer than in the pilot, the run ends early over a seeded-random prefix whose first 200
items carry the replay floor.

**Pace.** Median seconds per call: A 45, T 93 (max 240). The main run keeps 12 items in flight (up to 36
calls at once, all three arms of an item together), in blocks under `timeout 3000` that start no new
item after 2300 s.

**Observed and not acted on.** In the second pilot T answered confirmed 16, plausible 2, refuted 2. The
second pilot's rows (`results/h11/pilot/`) feed the cost projection only; they are in-sample and never
enter the read.
