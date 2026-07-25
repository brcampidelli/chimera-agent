# SWE-bench Verified — the Chimera scaffold on real django bugs

Two runs, both pre-registered in [`PREREGISTRATION.md`](PREREGISTRATION.md) before any model call, on
the same frozen 19-instance slice, graded only by the official `swebench` 4.1.0 harness in Docker.
**Run 1 is a null and stays published. Run 2 reverses it — and forces me to retract the mechanism I
had claimed.**

| | baseline | chimera | paired Δ | 95% CI |
|---|---|---|---|---|
| **Run 1** (`max_steps=8`) | 36.8% (7/19) | 36.8% (7/19) | **+0.0%** | [−8.5%, +8.5%] |
| **Run 2** (`max_steps=30`, +`--require-diff`) | 42.1% (8/19) | **57.9% (11/19)** | **+15.8%** | [−1.9%, +15.8%] |

Neither Δ is statistically significant. But they are different kinds of non-significant, and the
difference matters: run 1 was an exact tie with one discordant pair each way; **run 2 was 3–0 — every
instance the baseline solved, Chimera solved, plus three more.**

---

## Run 2: the discriminating run

Registered as Amendments 2 and 3 after run 1's null was traced to two faults **of our own** — a
scaffold tested without its strongest mechanism, and a step budget of 8 against a 250 MB repository.

```
n=19 django "<15 min fix" | deepseek-chat-v3.1 | pass@1 | max_steps=30 on BOTH arms
  baseline      42.1%  (8/19)    --no-plan --no-manager --max-attempts 1
  chimera+gate  57.9%  (11/19)   --repo-map --progress-ledger --replan --checklist
                                 --max-attempts 3 --require-diff
  paired Δ +15.8%   95% CI [-1.9%, +15.8%]   -> not significant
  discordant: chimera-only 3 (11133, 11451, 11951) / baseline-only 0
  concordant: both resolved 8 | both failed 8
```

**Why the 3–0 is worth noticing, and why it is still not proof.** Noise usually splits between arms;
a clean sweep does not. Under the null, three discordant pairs landing on the same side has
probability 1/8 ≈ 12.5% — suggestive, not conclusive, and the CI crosses zero by 1.9 points. The
pre-registration predicted exactly this shape ("Δ +5 to +20 pp, quite possibly not significant at
n=19"), so the honest label is **the registered one: not significant**.

### The scaffold only pays off once the agent can move

```
run 1 (8 steps):   baseline  7/19  |  scaffold   7/19   ->  Δ  +0.0%
run 2 (30 steps):  baseline  8/19  |  scaffold  11/19   ->  Δ +15.8%
```

Extra steps alone bought the **baseline** one instance. The same scaffolding that was worth *nothing*
at 8 steps is worth *three* at 30. That reads naturally: at 8 steps the agent barely finishes
navigating django, so planning, re-planning and a checklist have nothing to orchestrate; given room to
act, the scaffolding starts to pay. **Run 1's headline null measured a starved agent, and the starving
was our configuration error.**

### Precision, not just activity

| | patches | resolved | precision when it edited |
|---|---|---|---|
| run 1 baseline | 9/19 | 7 | 78% |
| run 1 scaffold | 8/19 | 7 | 88% |
| **run 2 baseline** | 14/19 | 8 | **57%** |
| **run 2 chimera+gate** | 16/19 | 11 | **69%** |

Raising the step budget made both arms *act* far more (patch rate 47% → 74%) and act *worse*
(precision 78% → 57% for the baseline): the newly-attempted instances are the harder ones, and most of
those attempts are wrong. The scaffolded arm degrades less (69% vs 57%), which is where its three
extra resolutions come from — **not from editing more often, but from editing better**.

---

## Retraction: my mechanism trace was wrong

Amendment 2 traced run 1's empty patches to a specific chain: with no verifier `ok = approved`, the
Manager judges the answer *text*, and the diff-gate computes `diff_productive` but spends it on
telemetry — so confident prose passes while the file is untouched. I called it "a product defect,
independent of any benchmark" and predicted the gate would drop the empty rate to ≤5/19.

**The prediction came true and the explanation was wrong.** The empty rate did fall — but it fell just
as far in the **baseline**, which has no gate and no scaffold:

```
empty patches:  run 1 baseline 10/19  ->  run 2 baseline  5/19   (only max_steps changed)
                run 1 scaffold 11/19  ->  run 2 gated     3/19
```

The decisive evidence is instance-level. Of the four instances where the run-2 baseline failed to edit,
the gate converted **one** — `11133`, and that one is not a clean test, because there the baseline had
*timed out* rather than declined to edit. On the three genuine cases (`11299`, `11555`, `11820` — real
empties, finished in 155–209 s with budget to spare), the gate rejected the attempts, forced the
retries, burned 2–3× the time, and **still produced nothing**.

So: forcing a retry does not make a model edit when it does not know what to edit. **The empty patches
were a step-budget problem, not a commitment problem**, and the residue left after fixing the budget is
capability-limited. Per Amendment 2's pre-committed rule, this retraction ships as prominently as the
claim did.

**What survives.** The defect itself is real and independently worth fixing: a code-editing task could
be scored a success having changed no file (pinned by `test_without_require_diff_prose_still_passes`).
`--require-diff` fixes that, and stays off by default. It is simply **not what produced run 2's +15.8%**.

## What this run cannot say

Amendment 3 cut the middle arm (plain scaffold at 30 steps) for cost, and that cost is now visible:
**the +15.8% cannot be attributed between the scaffolding and the gate.** The evidence above argues the
gate contributed little, which points at the scaffolding — but a clean attribution needs the arm we
dropped. Any claim here reads "scaffold **plus** gate beats the bare model", never "the gate worked".

## Failure accounting and cost

| | baseline | chimera+gate |
|---|---|---|
| solves | 19 | 19 |
| patches | 14 | 16 |
| empty (declined to edit) | 4 | 3 |
| **timeouts (1800 s)** | **1** | **0** |
| infrastructure errors | 0 | 0 |
| median / max time | 209 s / 1800 s | 609 s / 1284 s |
| total wall time | 1.6 h | 3.6 h |

Timeout asymmetry is 1 vs 0 — within the tolerance the validity gate allows, and it runs *against* the
scaffolded arm (the timed-out baseline solve counts as a baseline failure). **Cost: US$ 6.83** for run 2
including its feasibility probe; US$ 5.97 of the US$ 20 key remains. The scaffolded arm costs ~2.2× the
wall time of the baseline for its three extra resolutions.

`django__django-10097` remains excluded from the slice: the gold dry-run showed its *reference* patch
fails grading, so no agent patch could be judged fairly on it. Caught before any spend.

## Q1 and Q2, kept apart

- **Q1 — the thesis.** On real django bugs with a competent model and an adequate step budget, the
  scaffolding resolved **+3 instances, 0 losses, Δ +15.8% [−1.9%, +15.8%]** — the first externally
  graded result pointing the right way, and **not statistically significant**.
- **Q2 — the scoreboard.** 57.9% is on a *deliberately easy, single-repo slice*. It is **not a SWE-bench
  Verified score** and must never be presented as one. A Verified score needs the full 500.

## What would settle it

n=19 with 8 both-fail pairs cannot resolve a 3-pair effect. The honest next step is **more instances,
not more configurations**: the same two arms on 50–60 easy instances (≈ US$ 25–35 at run 2's rates)
would either push the CI clear of zero or show the sweep was luck. Restoring the middle arm would then
buy the attribution this run had to give up. Neither is a re-roll of run 2 — run 2 publishes as it is.
