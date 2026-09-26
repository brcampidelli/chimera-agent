# Pre-registration — H4 (a de-capitalised, reason-attached L0) and H5 (the vendor's temperature), on SWE-bench

**Registered 2026-09-25, before any paid call.** Study 25, wave 3, arms H4 and H5 (`bench/PLAN-study25-system-prompts.md` §2.3, §6, §9). The owner approved **US$ 25.00** for both arms together, pilot included. This file follows `bench/PROTOCOL.md`; the rules it satisfies are cited by number.

The directory is `bench/prompt_overlays/` because both arms test a field of the plan's model overlay (§6): `emphasis` (H4) and `temperature`/`top_p` (H5).

## Why SWE-bench, and why this slice

The corpora built for wave 3 sat at their ceiling, and LoopsBench has 8 tasks (1/8 solved, about US$ 25 a run), so neither can resolve a 10 pp difference. The SWE-bench django slice of the closed phase (`bench/swe_bench/RESULTS.md`) resolved in the 34–58% band, graded only by the official harness, which is the band where paired items disagree and a paired test has something to count.

Plan §8's table says a 10 pp effect needs about 160 paired items at p_d = 0.20 and about 235 at p_d = 0.30. The easy stratum alone has 92 django instances, so the pool is widened to the next one:

- **Pool:** every django instance of SWE-bench Verified in the `<15 min fix` (92) and `15 min - 1 hour` (117) strata: **209 candidates** (`pool.py`; the 22 `1-4 hours` instances are left out, where a floor is likely).
- **Gold validation first, US$ 0:** each candidate is graded with its own reference patch by the official harness (`swebench` 4.1.0, Docker, prebuilt `swebench/` images). A candidate whose gold patch does not resolve is dropped as an infrastructure exclusion, before any model call.
- **Order:** the slice is the gold-resolved candidates in **sha256(instance_id) order** (`pool.slice_order`), fixed here. It is blind to outcome and spreads the first items across versions and strata. Every run below takes items from the head of this order.
- **This is not a SWE-bench Verified score** and is never reported as one: one repository, two strata, one model.

## Model, endpoint and harness (identical in every arm)

- **Model** `openrouter/deepseek/deepseek-v4-flash-0731`, the product default, **pinned to DeepInfra with no fallbacks** (`extra_body.provider = {order: [DeepInfra], allow_fallbacks: false}`, the pattern of `bench/directive_boundary`); `CHIMERA_FALLBACK_MODELS` and `CHIMERA_PROVIDER_ORDER` are unset for the solve. DeepInfra's published price (OpenRouter endpoints API, 2026-09-25): US$ 0.06 per M prompt tokens, 0.18 per M completion, 0.015 per M cache read. Every call's served provider is recorded (PROTOCOL §3).
- **Agent:** a plain `chimera.core.agent.Agent` with the solve worker's settings: `max_steps=30` (the budget the closed phase settled on; run 1's 8 starved the agent), `insist_on_action=True`, `turn_context=True`, `project_root` = the workspace, `prefix_nonce=""`, no owner instructions, `thinking` not sent (the model's default). No planner, manager, verifier or governance wrappers.
- **Tools:** `default_registry(ws, host_exec_confirm=None)` restricted to the coding tools: `read_file, write_file, edit_file, apply_patch, list_dir, grep, glob, run_shell, job_status, job_cancel, todo_write`. The web tools are left out so that `run_shell` is the one route to the network (see the wall, below).
- **Task text:** `bench/swe_bench/run_swe.py`'s `_INSTRUCTION`, byte for byte (its "Do NOT" is in the user turn and identical in every arm).
- **Workspace:** the closed phase's anti-leakage recipe (clone without tags from a local django reference, `reset --hard base_commit`, remote removed, tags deleted, reflog expired, `gc --prune=now`), built once per item and byte-copied for each arm. Every copy is asserted clean and asserted to reach **no** commit after `base_commit` (`rev-list --all ^base` = 0).
- **Environment:** each solve is a subprocess with a **1800 s wall clock**, its own `HOME`, and the Python venv left off `PATH`, so a `pip install` the agent runs cannot change the harness's interpreter or leak into another solve.
- **Patch:** `git diff base_commit` of tracked files, as in the closed phase. **Grading:** only the official harness, per arm (`run_evaluation --cache_level instance --namespace swebench`).

## Arms (frozen)

| arm | system prompt | temperature | top_p |
|---|---|---:|---|
| **A** | `DEFAULT_SYSTEM_PROMPT`, byte for byte (sha256 `66299259ecf8…`) | 0.2 | not sent |
| **B** | A's instructions, CAPS removed, each rule with its reason (sha256 `f9523908fb44…`) | 0.2 | not sent |
| **C** | A, byte for byte | **1.0** | **0.95** |

`arms.py` holds the texts and refuses to run if either hash changes.

### B, as a diff against A (sentence by sentence; `python arms.py` prints it)

```diff
-Your job is to DO the task, not to describe how to do it.
+Your job is to do the task, not to describe how to do it, because the result is what was asked for.
-Investigating or explaining the solution is not enough: if you know what to do, DO it with the tools before you finish.
-A final answer that only tells the user what they 'can' or 'should' do is a failure.
-Give a concise final answer only after the change has actually been made, then stop calling tools.
-One exception, and it is deliberately narrow: when the request does not contain enough to begin — no technology, no audience, and nowhere for the result to live — ask the few questions that actually block you, at most three, and stop without writing anything.
-Only when a guess would produce the WRONG thing rather than merely a different one.
+Investigating or explaining the solution is not enough: if you know what to do, do it with the tools before you finish, because an explanation leaves the change unmade.
+A final answer that only tells the user what they 'can' or 'should' do is a failure, because it hands the work back to them.
+Give a concise final answer only after the change has actually been made, then stop calling tools, so the answer reports what was done.
+One exception, and it is deliberately narrow: when the request does not contain enough to begin — no technology, no audience, and nowhere for the result to live — ask the few questions that actually block you, at most three, and stop without writing anything, because files built on a guess are files nobody asked for.
+Only when a guess would produce the wrong thing rather than merely a different one.
-If the request names what to build and where, do not ask — build it.
-To change an existing file, prefer edit_file (or apply_patch for several edits) over write_file — edit in place instead of rewriting the whole file.
+If the request names what to build and where, do not ask — build it, because then a question only delays the work.
+To change an existing file, prefer edit_file (or apply_patch for several edits) over write_file — edit in place instead of rewriting the whole file, so the lines you did not mean to change stay as they were.
```

Unchanged: "You are Chimera, a capable autonomous agent.", "Use the provided tools to actually carry it out — run the commands, make the edits, create the files.", the bakery sentence (which already is the exception's reason), and the fence sentence.

- **CAPS words:** A has `DO, DO, WRONG, DATA`; B has `DATA`. Words: A 245, B 307.
- **Nothing added or dropped.** Every rule of A is in B with the same scope; each "because/so" clause states a reason and names no new behaviour. No vendor text.
- **The fence sentence keeps "DATA", in both arms, on purpose.** `Agent.compose_system_prompt` appends `UNTRUSTED_DATA_RULE` verbatim to any system prompt that does not contain it. A de-capitalised copy would therefore be sent *beside* the original, not instead of it. Keeping the constant byte-identical is the only way B sends one fence sentence without touching product code. The deviation is one word, in a sentence this task never exercises (nothing in a SWE-bench solve is fenced).
- **Composed prompt:** after the arm's text, all three arms get the same appended `TODO_PROMPT` (todo_write is registered); skills and environment facts go to the turn context. `solve_one.py` records the sha256 of every composed system message, and the reader checks that A and C are identical and that each starts with its arm's text.

### Why C is DeepSeek at 1.0 / 0.95 (the vendor docs, checked 2026-09-25)

The task was to pick the family whose vendor guidance is furthest from the loop's 0.2 and whose model is in `chimera/providers/catalog.py`. Checked against the vendors' current documents rather than the plan's summary:

| family (catalog slug) | vendor guidance | distance from 0.2 |
|---|---|---:|
| Google Gemini 3 (`gemini-3.8-flash`, `gemini-3.1-pro-preview`) | ai.google.dev "Gemini 3" guide (updated 2026-09-23): keep 1.0 for all Gemini 3 models; below 1.0 "may lead to unexpected behavior, such as looping or degraded performance" | 0.8 |
| **DeepSeek V4 Flash (`deepseek-v4-flash-0731`)** | HF model card of DeepSeek-V4-Flash-0731: temperature 1.0, **top_p 0.95 for agentic scenarios** (1.0 otherwise), for deployments of the weights | **0.8** |
| Zhipu GLM-5 (`glm-5.3`, `glm-5.3-flash`) | z.ai docs: defaults 1.0 / top_p 0.95; the GLM-5.3 card evaluates agentic benchmarks at 1.0 | 0.8 |
| Qwen3-Coder (`qwen3-coder`) | 0.7, top_p 0.8 | 0.5 |
| Moonshot Kimi K2 (`kimi-k2`) | 0.6 (instruct) | 0.4 |

Three families tie at 1.0. **DeepSeek is chosen**, for three reasons:

1. It is the product default, so the finding moves the setting most solves actually run with. The plan's summary did not list it at all.
2. Its endpoint is the one H4 already pins, so **arm A is H5's control too.** H5 then costs one arm rather than two, and the three arms of an item run interleaved on the same route in the same minutes. Under a cap where H4 has priority, that decides it.
3. Gemini 3.8 Flash costs 12.5× per input token and 21× per output token against this endpoint. A Gemini H5 with its own 0.2 control would not fit the cap at a useful n beside H4.

Gemini's explicit looping warning makes it the stronger prior for an effect. It is recorded as the next family to run if funded.

**The conflict inside DeepSeek's own guidance, stated rather than resolved by preference.** DeepSeek's API guide ("parameter settings") recommends 0.0 for coding, with no model named, for its own hosted API, which historically rescales the temperature it receives. The model card is specific to this checkpoint and to deployments of its weights, which is what DeepInfra serves. The card is taken as the applicable guidance, and C sends the card's full agentic pair (1.0 with top_p 0.95), because the overlay field (§6) is the pair. **C therefore changes two knobs at once, and this bench cannot say which of them any difference comes from.**

## Pilot (arm A only), and its gate

**Setup.** Arm A runs on the first **10** slice items, 10 at a time. It measures:
- the resolve rate;
- the computed cost per solve (`c`), and cost per resolved solve;
- wall time per solve;
- the interface preflight (PROTOCOL §4): the solves must show tool calls parsed and executed. Zero tool calls across the pilot is an adapter reading, not a result, and stops the arm.

**Gate.** Halts are excluded; the rate is over the graded solves.

| A resolves | what happens |
|---|---|
| 3 to 7 of 10 (25–75%) | the slice discriminates on this model; the main run follows |
| outside that band | the slice does not discriminate on this model. **Stop and report**; no main run. |

The pilot's solves are used only for the gate, the sizing below and the replica floor. **They are not pooled into any comparison:** the main run re-runs those 10 items in every arm.

## Main run: sizing rule, fixed now

Let `N` be the slice size, `c` the pilot's mean computed cost per arm-A solve, `P` the pilot's spend, and `S = 22.00` (the driver's stop, below).

1. **H4 is the priority.** A and B run on every slice item if `P + 2·N·1.25·c ≤ S`. If not, they run on the first `N' = ⌊(S − P) / (2·1.25·c)⌋` items and H5 is not run.
2. **H5 takes what is left.** C runs on the first `M = min(N, ⌊(S − P − 2·N·1.25·c) / (1.25·c)⌋)` items. If `M < 40`, H5 is not run: below 40 pairs it could only detect a difference of about 25 pp.
3. The factor 1.25 is headroom for B's longer prompt and for C's sampling.

**Interleaving.** Items run 12 at a time. Within an item, its arms run one after another in a rotated order (ABC, BCA, CAB, … by item index; AB/BA for items without C), so no arm is always first. Each arm starts from a fresh copy of the item's template.

## Metrics and analysis (`report.py`)

**Outcome.** A solve is **resolved** when the official harness lists it in `resolved_ids`. An empty patch is unresolved.

**Halts (PROTOCOL §2).** A provider or harness error is re-run once, fresh. If it halts again, it is a halt, and a halt leaves the pairing. A harness grading error is re-graded once, then counts as a halt. The 1800 s wall clock is also a halt (primary); a sensitivity reading grades the timed-out tree and is reported beside it. Hitting `max_steps` is **not** a halt: it is the agent's own budget, and the tree is graded.

### H4 — B against A, over items where neither halted

- **Primary (success, non-inferiority).** The paired difference in resolve rate (B − A), with a **Newcombe (1998, method 10) 95% interval**. **Non-inferior** when its lower bound is ≥ **−10 pp**. The exact McNemar p is reported beside it, as is `chimera/eval/paired.py`'s interval, for continuity with the closed phase.
- **Primary (cost), "cheaper in tokens".** Per item, the total tokens (prompt + completion, every call) of B against A. An exact two-sided sign test counts the items where B used fewer and those where it used more. **Cheaper** when p < 0.05 and B used fewer on more items.
- **Reported:**
  - the ratio of mean tokens and of mean cost (B/A), with a paired bootstrap 95% CI (10,000 resamples, seed 25);
  - cost per resolved solve B/A, bootstrapped the same way;
  - mean steps;
  - `max_steps` and loop-breaker endings;
  - empty patches;
  - cache-read share.

**Why −10 pp and not the plan's −2 pp.** The plan's default adoption rule asks for a paired lower bound ≥ −2 pp. At p_d = 0.25 that needs about (1.96 + 0.84)² · 0.25 / 0.02² ≈ **4,900** pairs, which is more instances than SWE-bench Verified has. At about 200 pairs and p_d = 0.25, a −10 pp margin has power about 0.8 if the true difference is 0 (plan §8's table: 10 pp, p_d 0.25, about 200). So **−10 pp is the smallest margin this budget and this pool can test.** A pass at −10 pp is reported as exactly that, never as the plan's bar.

### H5 — C against A, over items where neither halted

- **Primary.** The paired difference in resolve rate (C − A), exact two-sided McNemar at α = 0.05, with the Newcombe interval.
- **Reported:** the same cost and token figures as H4, plus the loop-breaker (`tool_loop`) and `max_steps` endings per arm. Gemini's warning is about looping, so looping is watched even on DeepSeek.

**Multiplicity.** H4 and H5 share arm A. They are two registered hypotheses with separate decisions, each tested once at 0.05; no correction is applied, and this is stated in the results.

### Floor, guards and checks

- **Replay floor (PROTOCOL §5, plan §8.1).** Replica disagreement of arm A: the pilot's solve against the main run's solve on the same 10 items. This is the only floor bought. A paraphrase floor (3–5 rewrites of A) is not affordable at this n, and that matters for H4: B is itself one rewrite of A (see "What this cannot show").
- **Route.** Every call's `provider` must be DeepInfra; any other value is counted and reported.
- **Sampling.** Every call's temperature and `top_p` are recorded, and must match the arm.
- **Prompt.** The system hash per arm (A = C, B different; each starts with its arm's text).
- **Network scan.** The shell commands of every solve are scanned for network access and for any fetch of django's source (see the wall, below).
- **Tool calls.** Tool calls per solve; zero across an arm is an interface failure (PROTOCOL §4).

## The wall (PROTOCOL §1)

- **Git: enforced and probed.** Every workspace copy is checked for commits after `base_commit` before the agent starts, and the solve refuses to run if one is reachable.
- **Network: not enforced, and this bench says so.** This WSL has no bubblewrap, so `run_shell` runs on the host with the network up. The agent process itself needs the network to reach the model, so it cannot be cut around the solve.
- **What stands in for it:**
  - the web tools are removed from the registry;
  - every `run_shell` command is kept and scanned for network use and for any fetch of django's own source (a django `git clone`, `pip install django`, a github.com/django URL).

  Solves that fetched django source are counted per arm. If there are any, the comparisons are also reported with those items removed.

## Predictions (written before any call)

- **Pilot.** A resolves **4–7 of 10**. The pool mixes the easy stratum, where the closed phase's weaker model scored 34–58%, with a harder one.
- **H4.**
  - B's resolve rate is within ±5 pp of A's, and **non-inferiority at −10 pp holds** (the registered power is about 0.8 if the true difference is 0).
  - **"Cheaper in tokens" does not hold.** B's prompt is 62 words longer, and nothing in the rewrite asks for fewer steps. The sign test is not significant.
  - So the compound H4 is predicted to **fail on its cost half.**
- **H5.**
  - C − A within ±7 pp, not significant.
  - `tool_loop` endings are not more frequent at 0.2 than at 1.0: the vendor's looping warning is Gemini's, not DeepSeek's.
- **Validity.** Halts under 5% per arm, timeouts at most 3 per arm, and every call served by DeepInfra.

## Decision rules

### H4

| result | decision |
|---|---|
| lower bound ≥ −10 pp **and** cheaper in tokens **and** cost per resolved solve B/A ≤ 1.20 | H4 holds at this bench's resolution. Reported to the coordinator as an L0 wording candidate on this model. It is adopted, if at all, in a separate PR, which states that only −10 pp was tested. |
| lower bound ≥ −10 pp, but not cheaper | non-inferior and not cheaper: **H4 as stated fails.** No adoption on H4's grounds, since a wording change with no cost gain and an untested −2 to −10 pp band is not worth the churn. Reported. |
| lower bound > 0 | B is better on success; reported as superiority, whatever the cost result |
| lower bound < −10 pp, upper bound ≥ 0 | inconclusive at this n; not adopted |
| upper bound < 0 | B is worse; rejected |

### H5

| result | decision |
|---|---|
| C − A > 0 with exact McNemar p < 0.05, **and** cost per resolved solve C/A ≤ 1.20 | 1.0 / 0.95 is recommended as the DeepSeek overlay; the coordinator adopts it in a separate PR |
| C − A < 0 with p < 0.05 | the vendor setting hurts on this harness; 0.2 stays, and the result is recorded |
| otherwise | a null; 0.2 stays (no change without evidence). The result is published. |

## Stop rules and budget

- **Budget.** The cap is **US$ 25.00** for both arms, pilot included. The driver computes every solve's cost from its recorded tokens at the published price. It **stops submitting at a computed US$ 22.00**; solves in flight finish. The margin covers those solves and any gap between the price and the bill.
- **Pilot gate.** As above.
- **Halts.** If more than 10% of an arm's solves halt (after at least 20 solves in that arm), the run stops and reports.
- **Interface.** Zero tool calls across the pilot stops everything (PROTOCOL §4).
- **Resuming.** Long runs go in blocks under `timeout 3000`, with a resumable driver. A relaunch skips every (item, arm) already on file, and no driver is relaunched while another is alive (`pgrep`). A solve cut off by the end of a block is re-run from scratch; nothing partial is kept.
- **Amendments.** A design problem found at the first launch stops the run. An amendment is committed here before relaunching, and the partial data is discarded.

## What this cannot show

- **Other models, providers, repositories or days.** One model, one endpoint, django only, one run per arm. None of it is a SWE-bench Verified score.
- **Which half of B acts.** B changes two things at once: the capitals and the added reasons.
  - There is no placebo arm of equally long irrelevant text (PROTOCOL §6), so "the reasons helped" and "more words in that slot helped" cannot be told apart.
  - There is no paraphrase floor, so a B − A difference inside the paraphrase noise of A is indistinguishable from it.
  - A null for H4 says "this rewrite does not move success at −10 pp resolution". It does not say "capitals do nothing".
- **The plan's −2 pp bar.** Untestable here (about 4,900 pairs).
- **H5's two knobs.** C changes temperature and top_p together (the vendor's pair). It measures 0.2 against 1.0, not a curve, and on one family. A result says nothing about Gemini, whose warning motivated the hypothesis.
- **Other surfaces.** The L0 is measured in the solve loop only, not on chat, Discord or voice.
- **Correct fixes the agent could verify.** Django's test dependencies are not installed in the agent's environment, so the agent can rarely run the suite. This is the same in every arm, and the same as in the closed phase.
- **An enforced network wall.** See the wall above; it is scanned, not enforced.
- **An effect-size positive control.** None was affordable. The grader's controls are the gold validation (every slice item resolves with its reference patch) and the empty patch (never resolved). No published number exists for this model on this instrument (§2aa), so arm A's replay on the pilot items is the only reproduction check.

## Amendment 1 — 2026-09-25, before any paid call: the pilot does not wait for the whole pool's gold run

**What changed.** Gold validation of the 209 candidates is running at 20–45 s per instance: the machine is shared with other arms' type checks and browsers (load average 25 on 12 cores). The harness works in id order, and the pilot's items come first in sha256 order, so most of them would be validated last. The pilot therefore starts once **its own items** are validated, by a separate gold run over the head of the registered order (`run_id h45_gold_head`, the first 16 candidates in sha256 order).

**What does not change.**
- The pilot's items are still the first 10 gold-resolved candidates in sha256 order. Which items those are depends only on that order and on the gold status of the items ahead of them, not on the rest of the pool.
- Every candidate is still gold-validated before the main run, and before any model call on it.
- The slice is frozen from the full gold report (`h45_gold`), which covers the head again. If a head item's two gold verdicts disagree, that is reported as grader flakiness, and the item is dropped from the main run.
- No model call has been made.
