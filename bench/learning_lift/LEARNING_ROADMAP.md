# Making Chimera learn — roadmap from the learning-lift null

This document is the follow-up to [`RESULTS.md`](RESULTS.md) (run 1, ceiling-limited null) and run 2
(informative null: DiD −5.0%, 39 skills minted, control first-half 50%). It answers the question the
null raised — *why did accumulated learning produce no measurable capability, and how do we fix it?*

Every claim below is grounded in the code as of this commit. It was produced by a grounded study
(29 agents mapping the five learning subsystems from source, then proposing and adversarially
critiquing 20 improvements; 5 survived). **Nothing here has been implemented yet** — this is the
plan to review before touching code.

---

## 1. The finding: the learn→use loop is disconnected by default

Chimera's learning machinery is real and fairly sophisticated — recurrence gates, a governance
validator, an executable smoke test, taint-probation for anti-poisoning, BM25 card retrieval, the ACE
playbook, TraceProbe, the diff-gate. But **it is write-only in the paths that matter**: artifacts are
minted and stored, and then never read back into the model's context when a later task is solved.

Run 2 minted 39 skills and injected **zero of them**. The chain, verified in code:

| step | evidence |
|---|---|
| Skill cards inject only when `use_cards` is true | `chimera/evolution/context.py:118-123` |
| `use_cards` ← `settings.skill_cards`, **default `False`** | `chimera/config.py:199` (`skill_cards_couple_read` also False, `:213`) |
| `chimera solve` exposes **no flag** to enable it, passes no override | `chimera/cli/main.py:2531-2542` |
| the learning-lift bench sets **no enabling env** | `bench/learning_lift/run_learning.py:64-69`; repo `.env` has only `OPENROUTER_API_KEY`+`CHIMERA_CHAT_MEMORY` |
| ⇒ `self.cards = None` ⇒ `card_ctx = ''` | `chimera/core/autonomous.py:291` |

So the 39 minted skills were dead JSON. **Storing 39 or 3900 is identical if none reaches the model.**
The `skills_learned=39` validity check passing is what makes this null *informative* rather than
"nothing to measure": learning demonstrably happened on the write side, and capability still did not
move — precisely because the read side was off.

## 2. The second problem: the channels that ARE on are lexical and store receipts

The only learned artifacts that actually reach the model in the learning arm are:

- **memory facts** via `_recall_facts` (`autonomous.py:296,806-834`)
- **experience lessons** via `_recall_lessons` (`autonomous.py:287,801-804`)

Both rank by **keyword/token overlap** — semantic recall is off by default (`config.py:168`). And
what they store is not a reusable procedure:

- a memory fact is a **completion receipt**: `"Accomplished: <task> — <first answer line>"`
  (`autonomous.py:857`), keyed per-task-slug so distinct tasks never consolidate;
- an experience "lesson" renders a **truncated pytest dump of an unrelated function**
  (`detail = (fb or vout)[:500]`, `autonomous.py:537`; rendered verbatim by `format_lessons`,
  `experience.py:104-109`).

On a suite of 40 domain-disjoint bugs sharing only generic vocabulary (`package`/`fix`/`function`),
keyword recall surfaces the receipt of an **unrelated** task — inert at best, a distractor at worst.
This is the concrete mechanism behind the slightly-negative DiD (learning 2nd half 0.70 < cold 0.75).

## 3. The organizing insight

The transferable thing in the hard suite is not code — it is a **process**: the four traps fixed
before authoring (`tasks_hard_fix.py` docstring — contract stated instead of symptom, bug off the
named line, naive fix breaks a second checked case, one quiet contract clause). A per-instance skill
card cannot carry that across disjoint domains, and every lexical channel misses across disjoint
vocabulary.

Exactly one learned channel carries process **without a lexical gate**: the **ACE playbook**. It
injects its top-20 bullets by score **wholesale and unconditionally** (`chimera/evolution/playbook.py:171-180`;
`playbook_ctx = self.playbook.render()` at `autonomous.py:306`), and its curator is *instructed to
write general, cross-task bullets and reject task constants* (`playbook.py:206-207`). The bench never
turned it on (`--playbook` absent from the scaffold), and its curator is currently **blind to why a
task failed** — it receives only verdict + final answer (`cli/main.py:2636`), not the failing test or
the fixing diff.

---

## 4. The roadmap — 5 proposals that survived adversarial critique

Grouped by level. Cost is S/M/L. "Moves run-2?" is the adversarial verifier's honest read of whether
the change would plausibly have shifted the −5pp null — and every "yes/partly" is qualified by the
same power caveat (§6).

### Level 1 — Reconnect what is already built (cheap, and the necessary condition)

**P1 — Turn on the ACE playbook in the learning arm.** *(cost S; the #1 lever)*
- **Mechanism:** add `--playbook` to `_SCAFFOLD` (`bench/learning_lift/run_learning.py:64`) so **both**
  arms carry it identically — cold's fresh-home-per-task keeps its playbook empty; learning's one
  shared home accumulates it, preserving "the only difference is whether anything survives between
  tasks". This activates the closed ACE loop already wired in `chimera solve`: `_load_playbook()`
  (`cli/main.py:2473`) → `playbook.render()` at `autonomous.py:306` → `PlaybookCurator(...).curate(...)`
  saves to `home/playbook.json` (`cli/main.py:2630-2643`). **No product-code change.**
- **Why it helps:** the playbook is the only learned artifact that is *both* curated to be general
  *and* reaches the prompt of all 40 tasks regardless of vocabulary, because it is injected ungated.
  It is the mechanism most able to convert accumulated successes into DiD>0 on this exact suite.
- **How to measure:** re-run `BENCH_SUITE=hard`; primary = DiD vs −0.05. Dump `home/playbook.json`
  after the learning arm and verify the bullets encode the difficulty-spec heuristics (e.g. "read the
  docstring for ordering/stability/boundary clauses before patching"); log active-bullet count per
  task index to confirm accumulation, not fluff churn.
- **Risk:** playbook deprecation is driven by the model's own reflection, **not** by verify-or-revert,
  so a misleading bullet is not auto-retired on a failed outcome — the channel can accumulate generic
  filler and net to zero. Keep the identical flag on the cold arm so the DiD still isolates only
  cross-task survival.

**P5 — Turn card reading ON as an explicit arm + per-artifact attribution logging.** *(cost S/M)*
- **Mechanism:** (a) add a `--skill-cards` option to `solve` that overrides `skill_cards`, and add a
  `learning+cards` arm (env `CHIMERA_SKILL_CARDS=1` in the learning arm's `_solve` env only —
  `run_learning.py:~88`; cold has `--no-evolve-skills` so its `skills.json` is empty and `card_ctx`
  stays `''`, keeping the DiD clean). (b) **Attribution:** `CardRetriever` already tracks
  `last_retrieved`/`record_outcome` (`card_retrieval.py:122,134`; `autonomous.py:795-799`), and
  `_recall_facts`/`_recall_lessons` know their hits — surface, per task, which artifact ids were
  injected and the resulting pass/fail into `learning.json`.
- **Why it helps:** you cannot measure whether an artifact helps if it never reaches the prompt, and
  you cannot diagnose a null if you cannot see which artifact was present. `RESULTS.md` was forced to
  *speculate* that the cards "may be adding noise" — but they were never injected. This makes the
  counted artifact the injected artifact, and turns a black-box DiD into
  "card X injected on 6 tasks: helped 1, hurt 2, irrelevant 3".
- **How to measure:** three-arm run (cold / learning / learning+cards) with the paired estimator
  (§5-deferred); report the per-artifact helped/hurt/irrelevant table and token spend per arm.
- **Risk:** more arms multiply spend; the prior curated card A/B found **+16.7pp non-significant at
  +300% tokens** (`config.py:207-215`). A still-null result **with hit-rate > 0** is itself
  informative (retrieval fired but did not transfer → motivates P3/P4).

### Level 2 — Learn from errors properly (the "aprender com os erros" fix)

**P3 — Seed the always-injected playbook FROM verified error corrections.** *(cost M)*
- **Mechanism:** enrich the curator's `outcome_text` (`cli/main.py:2636-2639`) — today only
  verdict+final-answer, hence blind to *why* it failed — with the error evidence already sitting in
  `result.attempts`: the failing verifier output (`vout`) and the failed→passed diff
  (`attempt.diff_summary`, populated at `autonomous.py:528`). Feed the last failed attempt's
  diagnostics + the winning diff into the reflect step so the curator distills
  `section:"pitfall"|"check"` bullets like *"when the prompt states only a contract, run the given
  test first to find the failing case"*, *"after the obvious fix, re-check a second non-obvious
  case"*, *"re-read the docstring for ordering/stability/aliasing/empty clauses"*.
- **Why it helps:** the suite's shared structure is process-level, which is exactly the altitude the
  curator is told to write at — and because `render()` is unconditional, an accumulated "run the
  failing test first / check the second case" bullet reaches **every** later task's prompt regardless
  of vocabulary, sidestepping the BM25 disjoint-vocab miss that kills card retrieval. It converts
  errors (failing test + correction diff) into a durable, always-consulted checklist.
- **How to measure:** ablate error-enriched `outcome_text` vs today's verdict-only, holding P1 fixed;
  success = DiD moves positive **and** the bullets read as generic debugging process, not
  "in merge_ranges use `<=`".
- **Risk:** the curator can emit platitudes; mitigated by helpful/harmful scoring + `deprecate_at`
  (`playbook.py:146`) and by feeding concrete diff/test evidence. If bullets leak task constants they
  add noise to unrelated tasks — inspect and tighten the system prompt if so.

**P4 — Replace truncated-pytest experience details with distilled failure reflections.** *(cost M)*
- **Mechanism:** on a verified failed→passed transition (already detected at `autonomous.py:730-734`)
  and on terminal failure (`auto_evolve.py:657-661`), reuse the existing distillation machinery
  (`SkillEvolver.distill_correction` / `propose_failure_card`) to produce a **general one-line
  reflection** ("failure mode: naive fix passed the obvious case but dropped empty-input; lesson:
  verify empty/boundary before returning") and store THAT as the experience `detail` (or a new
  `lesson` field on `Experience`, `experience.py:26-33`) instead of the raw pytest dump.
- **Why it helps:** this is the highest-leverage change that needs **no retrieval flag flipped** — it
  improves the content of the channel already reaching the model in both the default `solve` path and
  the bench. Today a "lesson" is a truncated assert about `lru_cache` injected into a `word_wrap`
  task: at best inert, at worst a distractor. A distilled task-agnostic failure mode turns the live
  channel from noise into transferable error knowledge.
- **How to measure:** A/B the experience `detail` content (raw `vout` vs distilled reflection) on the
  hard suite, holding retrieval fixed; report DiD + a recovery-rate (tasks that failed attempt-1 and
  later passed) + a face-validity sample of injected lessons.
- **Risk:** one bounded distillation call per failed→passed/terminal-failure task; a wrong lesson is
  advisory only (verify-or-revert still decides) but can mislead a retry — keep it short, favour the
  concrete diff over prose.

### Deferred (real but bigger, or measurement-only)

- **Per-task paired + dose-response estimator** (reuse `chimera/eval/paired.py`) — the arms are
  genuinely paired (same task from an identical workspace, `run_learning.py:72-83`) but the whole-suite
  DiD throws that pairing away. A McNemar/Newcombe paired estimate is the honest n/power fix. *Weak on
  its own* (it does not change what is learned) but belongs with P5's attribution.
- **Recovery-rate telemetry + spaced re-practice arm** — give accumulated error-knowledge a task to
  prove itself on. Needs a new bench arm, does not touch the pre-registered DiD.

---

## 5. Recommended sequencing

1. **P1 + P5 together** (cost S): add `--playbook` and a `learning+cards` arm with attribution
   logging, re-run the hard suite. Cheapest, attacks the root cause, and for the first time measures
   the loop **connected**. Write a run-3 pre-registration amendment first (same discipline as run 2).
2. **P3 + P4** (cost M): route error evidence into the always-injected playbook, and upgrade the live
   experience channel from raw dumps to distilled reflections. This is the actual "learn from your own
   mistakes" fix — it changes product code, not just the bench.
3. **Paired estimator + more seeds** so a real +5–10pp effect can clear the noise the whole-suite DiD
   drowns at n=40.

## 6. Honest caveats (do not skip)

- **Connecting the channels is necessary, not sufficient.** Every "moves run-2" verdict is qualified:
  at n=40 in halves of 20 with a weak 24B model, even a real +5–10pp lift may not clear the noise.
  Expect the first honest outcome to be *"now the loop is connected and the number is interpretable"*,
  not a decisive positive DiD.
- **Default-OFF is defensible.** The card A/B measured +300% tokens for non-significant accuracy on a
  *curated* suite (`config.py:207-215`). The recommendation is **not** to flip the global default
  blind — it is to run the real experiment with it ON, on a work-like suite, with attribution, before
  deciding the default. That is exactly the larger real-task A/B the token-economics study said was
  required before any flip.
- **Multiple seeds required.** Single-seed-per-cell means per-task pass is noisy; the −5pp itself sits
  inside that noise. Any claimed effect must survive multiple seeds.

---

## Appendix — the 15 proposals that did NOT survive, and why

Recorded so the rejected space is not re-proposed. All were grounded; most failed the
"would it move the null?" bar because the mechanism they fix was already off, already shipped, or
targets a confound the committed data refutes.

| verdict | proposal | why rejected (short) |
|---|---|---|
| weak | Drop shared-shape stopwords from the retrieval key | real fix to the live keyword scorer, but only removes a distractor — best case nudges DiD marginally |
| weak | Skill-precondition contracts + task-type retrieval | fixes the write-only failure but the whole card channel was off in run-2, so inert until P5 |
| wrong | Held-out sibling transfer test at mint time | mis-grounded: run-2's 39 skills came via `_evolve_single`, not the collective/transfer path |
| wrong | Store a PROCEDURE on success + consolidate per task-type | UPDATE overwrites, does not accumulate; consolidation never runs in the `solve` path |
| weak | Failure-shape taxonomy + "have I failed this shape before?" | good idea, but still routes through a retrieval path that misses on disjoint vocab |
| weak | Recovery-rate telemetry + spaced re-practice arm | does not touch the pre-registered DiD; deferred, not rejected outright |
| weak | Add `--skill-cards` flag + instrument injection | subsumed by P5 (which also adds the arm and attribution) |
| weak | Per-task paired + dose-response estimator | correct n/power fix but changes nothing learned; pair it with P5 |
| weak | Concept-tagged curriculum + spaced repetition | replaces the run rather than moving it; large; needs a concept field the schema lacks |
| wrong | Close the online loop in `run_evolution` via StagnationDetector | the continuous solver is stateless by protocol, so rounds cannot improve regardless |
| weak | GUIClaw precondition contract + repair-before-extract | inert for the null (card channel off); marginal even when on |
| wrong | SEA per-candidate multiplicity budget on the collective gate | the collective gate never ran in run-2 (`_evolve_single` path) |
| wrong | Richer capability telemetry as sensitivity layer | diagnosis false vs committed data: run-2 was not headroom-limited (cold swung +25pp) |
| weak | AutoMem Phase-1 sampled consolidation of memory | `autoconsolidate` is wired only to interactive chat exit, never the `solve` path |
| — | Retrieval-quality bench, zero model calls | verifier returned no verdict; worth revisiting as a cheap offline probe |
