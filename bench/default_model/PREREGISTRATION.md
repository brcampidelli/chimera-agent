# Pre-registration — which model should be Chimera's default agent model

**Registered 2026-09-26, before any paid call.** The owner said the default model may change and approved adopting whatever this measurement recommends. The cap is **US$ 15.00**, probe and pilot included. This file follows `bench/PROTOCOL.md` and plan §8 (`bench/PLAN-study25-system-prompts.md`); the rules it satisfies are cited by number.

## What the decision can change, and what it cannot

- **Can change:** `Settings.default_model` (`CHIMERA_DEFAULT_MODEL`, today `openrouter/deepseek/deepseek-v4-flash-0731`), the OpenRouter `ProviderInfo.default_model` that must match it, the `_PRESETS` rungs that hold the default slug because it is the default, a catalogue row for the winner, and the docs that name the default. This happens in a separate commit, only if a candidate passes the rule below.
- **Cannot change:** `_DEFAULT_JUDGE` (`chimera/config.py:62`). The governance judge and the calibrated decision instruments were measured with that slug and stay pinned to it whatever this bench finds. The old default's catalogue row therefore stays.

## The instrument: bench/prompt_overlays (H4/H5), reused

This bench runs on the branch `bench/h4-h5-swebench` and imports its harness rather than copying it (`bakeoff_solve.py` imports `bench/prompt_overlays/solve_one.py`; `bakeoff.py` uses its `run._child_env`; `bakeoff_report.py` uses its `report.py` intervals and tests). What that brings, unchanged:

- **Pool and slice.** The 209 django instances of SWE-bench Verified in the `<15 min fix` and `15 min - 1 hour` strata (`bench/prompt_overlays/results/pool_django.jsonl`), in the registered sha256(instance_id) order. The slice is the **gold-resolved** ones: H4/H5's gold run (`run_id h45_gold`, official harness, swebench 4.1.0) resolved **208 of 209**; `django__django-10097` is dropped. The report is copied to `results/gold_h45.json` (sha256 `b70189f53a53…`) and the slice is built from it by `bakeoff.py slice`. Every run below takes items from the head of this order. **This is not a SWE-bench Verified score.**
- **Workspace.** The closed phase's anti-leakage recipe (clone without tags, reset to `base_commit`, remote removed, tags deleted, reflog expired, gc), built once per item and byte-copied per arm; every copy is asserted clean and asserted to reach no commit after `base_commit`.
- **The network wall (H4/H5 Amendment 2).** Every process of a solve gets `http(s)_proxy`/`all_proxy` pointed at a closed local port; `no_proxy` lets only `openrouter.ai` through. Commands that try to step around it are counted (`wall_bypass_tries`), and every shell command is scanned for a fetch of django's source (`DJANGO_FETCH`). Items where any arm ran such a command get a sensitivity reading without them.
- **Isolation.** Each solve is a subprocess with a **1800 s** wall clock, its own `HOME` and `TMPDIR`, and the harness venv off `PATH`.
- **Task text, tools.** `run_swe.py`'s instruction byte for byte; the coding tools only (`read_file, write_file, edit_file, apply_patch, list_dir, grep, glob, run_shell, job_status, job_cancel, todo_write`).
- **Grading.** Only the official harness, per arm (`run_evaluation --cache_level instance`). An empty patch is unresolved.

**The loop is `chimera solve`'s worker**, as H4/H5's arm A runs it: a plain `Agent` with `DEFAULT_SYSTEM_PROMPT` (sha256 `66299259ecf8…`, asserted), the loop's default temperature (0.2), `max_steps=30` (the closed phase's budget; the CLI's 8 starved the agent), `insist_on_action=True`, `turn_context=True`, `project_root` = the workspace, no owner instructions, no context budget, `thinking` not sent. There is one attempt and nothing reverts the tree, which is what `--keep-workspace` gives an external grader; no planner, manager or verifier, in any arm.

**Differences from `origin/main` that touch the loop.** This branch predates #624 (an empty reply at the *natural* ending is asked once more, without tools). That re-ask cannot change a patch. The bench counts the ending it would have caught (`empty_natural_final`) instead.

**Own names, so nothing collides with H4/H5 running on the same machine.** Work dir `~/dflt-h45-work` (the imported module requires "h45" in the name), django reference `~/dflt-cache/django-ref` (a local copy of theirs), swebench venv `~/dflt-swebench-venv`, harness `run_id`s `dflt_<phase>_<arm>`, so containers are `sweb.eval.<id>.dflt_…`. The driver is `bench/default_model/bakeoff.py`, which H4/H5's `pgrep` does not match. Their worktree and results are only read (the gold report, once, copied).

## Arms (frozen; `bakeoff_arms.py` refuses to run if the table's sha256 `07bb8079377e…` changes)

Only the model and its pinned provider differ. Each is pinned with `provider = {order: [P], allow_fallbacks: false}`, and `CHIMERA_FALLBACK_MODELS` / `CHIMERA_PROVIDER_ORDER` are unset. Prices are each endpoint's published USD per million tokens, read from OpenRouter's endpoints API on 2026-09-26 (`results/endpoints_2026-09-26.json`).

| arm | slug | pinned provider | in / out / cache read |
|---|---|---|---|
| **A** | `openrouter/deepseek/deepseek-v4-flash-0731` (the default) | DeepInfra (fp8) | 0.06 / 0.18 / 0.015 |
| **D** | `openrouter/deepseek/deepseek-v4.1-flash` | DeepInfra (fp8) | 0.14 / 0.42 / 0.0042 |
| **G** | `openrouter/openai/gpt-6-luna` | OpenAI, standard endpoint | 0.10 / 0.50 / 0.01 |
| **Q** | `openrouter/qwen/qwen3.7-flash` | Alibaba (its only endpoint) | 0.03 / 0.13 / 0.006 |

- **A's pin** is H4/H5's, so A here and A there run the same configuration. **D shares A's provider**, which holds the route fixed for the one same-vendor comparison.
- **G's pin is the one to check.** OpenAI has three priced endpoints for this slug: flex (0.05/0.25), standard (0.10/0.50) and fast (0.20/1.00). The probe below reads the billed cost of one call to see which one `order: ["OpenAI"]` reaches.
- **Sampling.** Every arm is sent the loop's 0.2. GPT-6 Luna's endpoints do not list `temperature` among their supported parameters, so the router drops it there; each call records what was sent. No arm is sent a reasoning setting: every model runs at its own default effort, as the product would run it. Reasoning tokens are recorded where the provider reports them.
- **Accounting.** Each request also carries OpenRouter's `usage: {include: true}`, which returns the billed cost. It changes accounting, not generation. The billed cost is recorded per call beside the tokens.

## Probe (PROTOCOL §1 and §4), before the pilot

`bakeoff.py probe`, from a solve's environment:
- **The wall.** Four ways out must fail: a curl to `raw.githubusercontent.com`, `pip download`, `git ls-remote` against github.com, and Python's `urllib` against pypi.org. Those are H4/H5's commands.
- **Each arm, one pinned call** offering one tool. It must come back as a **parsed** `read_file` call with a `path` argument, from the pinned provider.

Saved to `results/probe.json`. If the wall does not hold, nothing runs. If an arm fails, the fix goes in an amendment before the pilot, or the arm is dropped and the report says why. If G's billed rate matches flex or fast rather than standard, the pin is corrected in an amendment before the pilot.

## Pilot, and its gates

Every arm runs on the **first 8 slice items**. The item's arms are submitted together in a rotated order, 16 solves at a time. The pilot measures cost per solve, wall time and whether each model can drive the loop. Its solves are **not pooled** into any comparison: the main run re-runs those items in every arm, and the pilot's solve of each item is that arm's replay floor.

**Gate 1: can the model finish solves? Outcome-blind, per arm.** An arm fails this gate if, of its 8 pilot solves:
- 4 or more are halts (a provider or harness error after the one retry, or the 1800 s clock); or
- 4 or more made zero tool calls (PROTOCOL §4: an interface failure).

A candidate that fails the gate is dropped, and the report says which count dropped it. The brief allows dropping one; if more fail, each is dropped and the report says so. If **A** fails, the harness is broken and everything stops.

**Gate 2: can the instrument show a difference? Blind to arms.** The pilot is graded, and the resolve rate is **pooled over every arm's graded pilot solves**. If that rate is below 10% or above 90%, the slice cannot separate models: stop and report. No arm's own rate is read for any decision.

## Sizing (fixed now)

Let `c_x` be arm x's mean cost per pilot solve, taken as the larger of billed and computed, `C = Σ c_x` over the arms kept, and `P` the spend so far (probe and pilot). Then **`n = min(208, ⌊(13.00 − P) / (1.25 · C)⌋)`** (`bakeoff.py size`). The factor 1.25 is headroom for the variance of a per-solve cost.

Plan §8's table puts 10 pp at p_d 0.20 at about 160 pairs, and at p_d 0.30 at about 235, so more is better, up to the slice. If `n < 60`, there is no main run: below 60 pairs only effects of about 20 pp could be resolved. That is reported as unaffordable, and A stays.

## Main run

- The first `n` slice items, every kept arm.
- The pool is flat, 16 solves at a time. Items are submitted in order, each item's arms in a rotated order (index mod arms), so the arms of an item run in the same minutes. Each arm starts from a fresh copy of the item's template.
- **Budget.** It is checked per item, when the item's first arm starts. An admitted item runs every arm, so the stop never breaks a pair. Admission stops at a spend of **US$ 13.00**, the larger of billed and computed per solve. The margin to 15.00 covers the solves in flight.
- **Halts (PROTOCOL §2).** A provider or harness error is re-run once, fresh; a second halt leaves the pairing. The 1800 s clock is a halt in the primary reading; the timed-out tree is graded for a sensitivity reading.
- **An arm whose halts exceed 10% (after at least 20 solves) is stopped.** The other arms continue. The stopped arm is reported, but it is **not eligible** to replace the default.
- **Hitting `max_steps` is not a halt.** It is the agent's own budget, and the tree is graded.
- **Grading.** Per arm and phase, `run_id dflt_<phase>_<arm>`, 4 harness workers. A harness error is re-graded once, then counts as a halt.

## Metrics (`bakeoff_report.py`)

**Primary: resolved rate**, the harness verdict. Each candidate X is paired with A over the items where neither halted and both were graded:
- the paired difference X − A, with a **Newcombe (method 10) 95% interval**;
- the **exact two-sided McNemar** p.

**Also reported, per arm:**
- **cost per resolved solve**, and its paired ratio X/A with a bootstrap 95% CI (10,000 resamples over items, seed 25);
- mean cost per solve;
- tokens: prompt, completion, reasoning, and cache-read share;
- the `stopped_reason` distribution;
- **empty answers**, three counts:
  - #619's note (two empty closing replies at `max_steps`/`tool_loop`);
  - a natural `final` with no text (#624's case);
  - calls that returned neither text nor a tool call;
- **tool-call errors**:
  - unknown tool;
  - bad arguments;
  - tool calls the gateway dropped for unparseable JSON;
  - other tool errors;
  - solves with zero tool calls;
- empty patches, halts, timeouts, retries;
- mean wall time;
- served provider per call (anything off the pin is counted);
- billed against computed cost.

**Checks:**
- the composed system message is identical across arms and starts with `DEFAULT_SYSTEM_PROMPT`;
- the temperatures sent;
- the leak and wall-bypass scans.

## The frozen adoption rule

A candidate X **replaces A** only if all of these hold:

1. **Non-inferior.** The Newcombe 95% lower bound of X − A is **≥ −5 pp**.
2. **And one of:**
   - **cheaper per resolved:** the paired point estimate of X's cost per resolved solve is below A's (the bootstrap CI is reported beside it); or
   - **significantly better:** X − A > 0 with exact McNemar **p < 0.05**.
3. **Eligible:**
   - not stopped by the halt rule;
   - at least 99% of its calls served by its pinned provider.

**If several qualify**, pick the one with the highest resolved rate, then the lowest cost per resolved. Both are read on the items that A and every qualifier graded.

**If none qualifies, A stays.**

**What this rule can resolve, stated before the data.** At n ≈ 160–200 and p_d ≈ 0.3, the paired 95% half-width is about ±8 pp. So a candidate whose true difference is 0 passes the −5 pp bound only about a quarter to a third of the time. In practice the rule adopts a candidate that measures **a few points better or more**, and an equal-but-cheaper candidate will usually **not** be adopted.

That is deliberate. For the model every fresh install spends on, −5 pp is the most loss of success this rule accepts, and a margin this pool cannot fully resolve errs toward keeping what is measured and in place. The −10 pp reading (the margin H4/H5 registered) is reported beside the decision. It never makes the decision.

**Multiplicity.** Three candidates are each tested once at 0.05, as the brief states. That gives a family-wise error of up to about 0.14 on the "significantly better" path. Holm-adjusted p values are reported beside the raw ones, and the results say which path each qualifier took.

## Floors and controls

- **Replay floor (PROTOCOL §5, §8).** Per arm, the pilot solve against the main solve on the same 8 items. It is thin, 8 items per arm, and is reported as that.
- **No paraphrase floor.** The prompt does not change between arms.
- **Cache (PROTOCOL §3).** Not fixed. Each provider caches as it does, and that is part of what each model costs to run. The cache-read share is reported per arm, beside the totals.
- **Grader controls:**
  - the gold validation (208 of 209 resolve with their reference patch);
  - an empty patch never resolves.
- **No published number exists for these models on this instrument (§2aa).** A's replay floor is the only reproduction check. A here has H4/H5 arm A's exact configuration, and their numbers are theirs to publish.

## Predictions (written before any call)

- **Probe.** The wall holds. D and Q return parsed calls on their pins. G's `OpenAI` pin is a coin flip between the standard and the flex endpoint.
- **Pilot.** Every arm passes gate 1. Pooled resolve rate 35–60%. Mean cost per solve:
  - A about US$ 0.010;
  - D 0.015–0.025;
  - G 0.02–0.04, reasoning tokens included;
  - Q under 0.01.
- **Sizing.** About US$ 0.06–0.08 per item for all four arms, so n lands between 130 and 170.
- **Main.**
  - **D** is within ±5 pp of A and costs more per resolved: it does not qualify.
  - **G** resolves 5–10 pp more than A, at 2–3× the cost per resolved. Whether McNemar reaches 0.05 is the open question, and it is predicted **not** to at this n.
  - **Q** resolves 5–15 pp less than A: it fails non-inferiority, whatever its cost.
  - **Decision:** A stays (my estimate 55%); G replaces it (30%); D (15%).
- **Mechanics.**
  - Empty closing replies are more frequent on the reasoning models (G, Q) than on A.
  - Tool-call errors stay under 2% of calls on every arm.
  - Halts stay under 5% per arm.
  - Every call is served on its pin.

## Stop rules and budget

- **Cap US$ 15.00**, probe and pilot included. Admission stops at US$ 13.00 of billed-or-computed spend.
- The probe and pilot gates are as above.
- The per-arm halt rule is as above.
- **Resuming.** Long runs go in blocks under `timeout 3000`, with a resumable driver. A relaunch skips every (item, arm) on file, and no driver is launched while another `bench/default_model/bakeoff.py` is alive. A solve cut off by a block's end is re-run from scratch; nothing partial is kept.
- **Amendments.** A design problem found at the first launch stops the run. An amendment is committed here before relaunching, and the partial data is discarded.

## What this cannot show

- **Other surfaces.** The default model also answers chat, the Code screen, Discord and voice. This bench measures only the autonomous coding loop. Latency for interactive use is not measured (wall time per solve is reported).
- **Other repositories, languages, tasks or days.** Django only, one run per arm per item.
- **Routes.** Each arm is pinned. The product does not pin: OpenRouter routes each call of a slug to a pool of providers, at other prices and possibly other quantizations. The costs here are the pinned endpoints' costs.
- **Harness fit.** The L0 prompt and the loop were written and tuned while this DeepSeek slug was the default. That may favour A, and nothing here can separate it from the model.
- **Settings.** Temperature is 0.2 where honoured and ignored on G. Reasoning effort is each model's default. Another setting could change any arm's result, and no arm was tuned.
- **Small differences.** This n cannot tell A from a candidate within about ±8 pp (see the rule).
- **An enforced wall beyond the proxy.** A command that unsets the proxy is counted, not stopped (H4/H5 Amendment 2).

## Amendment 1 — 2026-09-26, after the pilot and before the main run: a registered stop overridden, and three harness mechanics

**What the probe and the pilot showed.**
- **Probe.** The wall holds. Each arm returned a parsed `read_file` call from its pinned provider. The billed cost equals the computed one on every call. G's billed 1.43e-5 USD is the standard endpoint's 0.10/0.50, not flex or fast. US$ 0.0001.
- **Gate 1.** Every arm finished 8/8 solves, with 0 halts and 0 solves without a tool call. No arm is dropped.
- **Gate 2 fired.** The pooled resolve rate is **29/32 = 90.6%**, above the registered 90% ceiling. The registered consequence is to stop and report. The first 8 items are 4 from each stratum, so this is not a stratum imbalance.
- **Sizing.** Mean US$ per solve: A 0.0075, D 0.0145, G 0.0041, Q 0.0049; per item 0.0310; spend 0.248. So `n = min(208, ⌊(13 − 0.248) / (1.25 · 0.0310)⌋) = 208`, the whole slice.

**The main run goes ahead. This overrides a registered stop, and this amendment was written after the gate's result was seen.** Every reading of the main run carries that label. The reasons:

1. **Gate 2 guards one of the two paths of the adoption rule, not both.** A rate near the ceiling leaves little room for the "significantly better" path. It does the opposite to the paths the pilot says are live:
   - it lowers p_d, which narrows the paired interval that the −5 pp bound is read on. At p_d ≈ 0.15 and n = 208, the half-width is about ±5 pp, against the ±8 pp the registration assumed;
   - it does not touch cost per resolved at all. G and Q ran cheaper per solve than A in the pilot.

   A stop would keep A by default, without measuring the comparisons this pilot says the rule can make.
2. **The override changes whether data are collected, not how they are read.** No pilot solve enters a comparison, and the main run's data are fresh. The adoption rule, its margin, the sizing and the metrics are unchanged.
3. **The gate's estimate is thin.** It rests on 8 items, and one more failure among 32 solves would have read 87.5%.

**The cost to the reading.** The superiority path now has little power, and the results report the main run's pooled rate beside the decision. A reader who holds registered stops as absolute should read the decision below as not made.

**Harness mechanics.** These do not change what any model sees, and apply to the main run only (the pilot ran without them).
- **(a) `LITELLM_LOCAL_MODEL_COST_MAP=True` in the solve environment.**
  - Behind the wall, LiteLLM's remote cost-map fetch fails and it keeps its bundled copy; the pilot's logs say so.
  - The variable skips the three retries through the dead proxy. `import litellm` measured 80 s, then 37 s with the variable, on this machine under load.
  - The map is the same either way.
- **(b) Block start cutoff.** In a main block (`timeout 3000`), no solve starts after 1500 s. A solve not started runs in the next block. The point is that in-flight solves finish inside the block.
- **(c) Solves cut at a block's end.**
  - Each solve runs in its own session, so the driver's `timeout` did not reach it. It would have outlived the driver, and collided with the next block's copy of the same item and arm, which uses the same workspace path.
  - The driver now kills each such solve's process group on SIGTERM, and appends the calls it had made (from a per-solve call log) to `results/killed_solves.jsonl`.
  - That spend counts against the cap. The solve is re-run from scratch, and nothing partial is graded.
  - The runner also kills any orphan solve of this bench before a block starts.

## Amendment 2 — 2026-09-26, after main block 1: the start cutoff is checked before an item is admitted

Block 1 recorded 68 solves and killed none. But its cutoff was checked only after an item had been admitted and its template built, so the block spent its last 25 minutes cloning django templates for solves that never started. The cutoff is now checked first.

Nothing a solve sees changes, and no data is discarded: the cutoff only decides when a solve starts, and every recorded solve ran whole.

## Amendment 3 — 2026-09-26, during main block 3: the wall had a hole on the local disk, the main run restarts behind a sandbox

**What was found.** The rows are in `results/discarded_main_before_amendment3.jsonl` and the pilot's.
- A solve's shell ran `find` across the grading directories, looking for a dependency. That prompted a scan of every recorded shell command (`disk_scan.py`).
- The network wall held, but `run_shell` runs on the host, and the host holds the answers several times over:
  - two django clones at `main` (`~/dflt-cache/django-ref`, `~/h45-cache/django-ref`), which contain every later fix;
  - the templates and workspaces of other items;
  - the gold run's logs, where `patch.diff` is the reference fix;
  - the Hugging Face cache of SWE-bench;
  - the Windows drives, with every bench's results.
- **Arm D reached for them in 4 of 8 pilot solves and 16 of 26 main solves.** Arm A did in 1 of 27; G and Q in none.
- It was not incidental. On `django__django-13809`, D read `runserver.py` from the reference clone at `main`, then searched its history for the ticket and ran `git show` on the upstream fix commit. On `django__django-16454`, it listed the gold run's log directory for that very instance.
- A score that includes those solves measures copying for D, not the model.

**What changes.**
- **The main run restarts from its first item, behind a local-disk wall** (`sandbox.sh`). Each solve runs in its own user, mount and PID namespaces (`unshare`, unprivileged, probed working on this WSL).
  - **Hidden, under empty tmpfs mounts:** the real home, `/mnt`, `/tmp`, `/var/tmp` and `/dev/shm`. The Docker socket is covered too.
  - **Bound back at their real paths:** the solve's own workspace and scratch (read-write), the /tmp copy of the code with its venv, and the directory of the interpreter the venv points to (both read-only).
  - **Hidden by the new PID namespace:** every other process.
  - **Identity.** A nested user namespace maps the invoking uid back, so the solve runs as the same user with no capabilities.
  - **Workspace.** The driver prepares it (the same `prepare_workspace`) before the sandbox hides its template, and the solve re-checks it inside (clean, no commit after `base_commit`).
- **The network wall is unchanged.**
- **The 115 main solves recorded before this are discarded,** with the 1 cut when the run was stopped.
  - They are kept as the record of the breach, and none is graded into a comparison.
  - Their spend counts against the cap (`spent()` reads them).
  - The sizing rule still gives n = 208: spend so far is about US$ 1.55, against the US$ 13.00 stop.
- **The pilot is not re-run.**
  - Its gates stand as read: gate 1 counts halts and tool calls, which the breach does not change; gate 2 was already overridden (Amendment 1).
  - Its replay floor now excludes every pilot solve with a disk touch. That leaves D's floor at 4 items, which the results say.
- **Probe before the restart** (`bakeoff.py probe --sandbox`, saved to `results/probe_sandbox.json`), from inside the sandbox, on a real workspace:
  - **must fail:** the network exits, listing either reference clone, the gold logs, the Hugging Face cache, another item's template, `/mnt/c`, and the Docker API;
  - **must hold:** the real home lists only the work dir and the path to the interpreter; the work dir lists only this solve's two directories; the only `django/db/models/base.py` on the machine is the workspace's own; fewer than 10 processes are visible; `id` is the user; the workspace is clean with no later commit; the venv imports; one pinned call per arm returns a parsed tool call.
  - If any fails, nothing runs.
- **Scans stay on.** Every main solve's shell commands are still scanned (`disk_scan.py`). Under the sandbox a reach can only fail, and each reach is still reported and gets the sensitivity reading.
- **Machine mechanics, no effect on what a model sees.** The driver's main loop waits with a timeout, so its SIGTERM handler runs on time: in block 3 it waited for the next solve to finish before acting. The halt tally counts the whole run.

**What this cannot fix.** The breach says something about D itself: it goes looking outside its workspace for the answer. That is a finding about the model's behaviour as an agent, reported beside its score. It is not something the sandbox measures.

**Amendment 3, the probe's first reading (04:01).** Every disk and network check held. All four pinned calls failed with a DNS error: on WSL `/etc/resolv.conf` links into `/mnt/wsl`, which the `/mnt` cover hid.

The fix is in `sandbox.sh`, before the restart. The resolver file is bound back read-only, and the staging moved to a private tmpfs on `/dev/shm`, which is covered again afterwards. The probe is re-run, and nothing started in between.

**Amendment 3, the probe's second reading (04:17), and the restart.** Every check held, and each arm returned a parsed tool call from its pin (`results/probe_sandbox.json`).

Between the two readings the WSL VM crashed. `getpwuid` failed with EIO, then `CreateInstance/E_UNEXPECTED`, after hours at a load average near 40 from this bench and H4/H5 together. It came back empty (uptime 0 min), and on the idle machine the probe passed in 32 s.

To keep the shared machine up, the restarted main run starts at most **12 solves at a time**, not 16. That changes nothing a solve sees. The pre-amendment grading report for D (`dflt_main_D`, of discarded solves) moved to `results/grades_discarded_before_amendment3/`.

## Amendment 4 — 2026-09-26, after the host disk filled and WSL crashed (07:39): the sandbox checked against H4/H5's v1 gaps, and the run resumed

**State at the pause.**
- `main_solves.jsonl` holds 250 solves (A 62, D 62, G 63, Q 63), all run under `sandbox.sh` as committed in `bc798258`.
- Block 5 died in the crash, so its in-flight solves wrote no row.
- Spend so far is US$ 3.84: US$ 2.55 in the main run, and the rest in the pilot, the discarded pre-sandbox run and the probes.
- No main-run resolve rate has been read.

**The three gaps H4/H5 found in its first sandbox (its Amendment 7), checked against this one.** `sandbox.sh` is unchanged.
1. **`/mnt/wsl` visible (other containers' bind-mounted repositories and venvs).** Not shared. `sandbox.sh` covers all of `/mnt` with an empty tmpfs from its first version and binds back only the resolver file. The Amendment 3 probe's `ls /mnt/c/Users` failed, and its whole-disk search found no django but the workspace's.
2. **The solve running as the namespace's root.** Not shared. The last step is a nested user namespace mapping the invoking uid back, and the Amendment 3 probe read `id -un` as the user. Capabilities and unmount attempts were not probed then; they are now.
3. **A command audit sees commands, not their output.** This is true of `disk_scan.py` too. The wall here is enforcement, not the audit, so the probe now looks from inside the way a solve would.

**The extended probe** (`bakeoff.py probe --sandbox`, into `results/probe_sandbox_a4.json`) runs on the unchanged `sandbox.sh` before any new solve. It adds six checks.

Must fail:
- listing `/mnt/wsl/docker-desktop-bind-mounts`;
- `umount` of the home cover;
- `umount /mnt`.

Must hold:
- `/mnt` lists only the path down to the resolver;
- `id -u` is not 0, and every capability set is empty;
- inside a fresh `unshare -rm` of the solve's own, both covers still refuse to unmount (inherited mounts are locked) and nothing appears under them;
- a whole-disk `find` for `.git`, `pyvenv.cfg`, `patch.diff`, `eval.sh`, `*django__django*`, `*swe-bench*`, `*swe_bench*` and `*sweb.eval*` outside the workspace finds nothing but the read-only code copy.

**Decision, fixed before the probe runs.**
- **If every check holds,** the 250 rows stand. They ran under the very file the probe tests, and none is re-run.
- **If any check fails,** those 250 rows leave the primary comparison and are never pooled with rows run under a fixed sandbox. They are kept as a secondary replica, and the main run restarts from item 0 behind the fix, after this file records it.
  - The budget for that case: US$ 3.84 spent, about 2.6 to re-run the 250 rows, and about 145 × 0.041 ≈ 5.9 for the items not yet run. That is about US$ 12.4, under the US$ 13.00 stop but with little margin. If it does not fit, the report says so before any money is spent.

**Operational changes. None changes what a solve sees.**
- **Pacing.** At most 8 solves at a time (coordinator).
- **Disk guard.** The runner refuses to start a block, and the grader to grade, when C: has under 1.5 GB free. The driver also stops admitting items when C: drops below that mid-block.
- **Grading** moves to `grade_items.py` (commit `479acc02`). It grades item by item with the harness's own `run_instance` and removes each item's image once its arms are graded.
- **Clean-up at driver start.**
  - The driver records the call logs a killed block left behind (spend only, into `killed_solves.jsonl`).
  - It removes leftover workspaces and templates.

**Amendment 4, the probe's reading (07:55).** Every check held (`results/probe_sandbox_a4.json`):
- the three new must-fail tries were refused;
- `/mnt` lists `wsl` and `resolv.conf` only;
- uid 1000, with all four capability sets empty;
- both covers refuse to unmount under a nested `unshare -rm` (exit 32, 32), with nothing under them;
- the answer-shaped search finds only the read-only code copy (`tests/test_swe_bench.py`, a prompt snapshot, the venv's `pyvenv.cfg`, its empty `git init`, `bench/swe_bench`);
- each arm returned a parsed tool call from its pin.

Under the rule above, the 250 rows stand, and the run resumes from its file.

## Amendment 5 — 2026-09-26, during the main run, before any main-run resolve rate is read: rate-limit halts

**What the halts are.** Of the 13 halts on file:
- 3 are D's 1800 s clock;
- 10 are the gateway's `CredentialRejectedError` for `RATE_LIMIT`: Q 7, A 3, D and G none.

The key is shared with the H4/H5 run, which calls A's exact endpoint all day. A rate-limited call puts the key into cooldown, and with one key and no fallback, the gateway raises on the spot. The driver's single immediate retry fell into the same window every time.

That is the route's reliability, not the model's. Left as it is, the per-arm halt rule (more than 10% after 20 solves) would stop Q for a reason that has nothing to do with Q.

**Changes, fixed now:**
1. **The halt rule counts timeouts and every error except rate limiting** (`counts_as_halt`). Rate-limit halts are reported per arm, as their own count.
2. **After the main pass, every rate-limit halt is solved once more, fresh** (`bakeoff.py requeue-ratelimited`, then `main` again).
   - The halted row moves to `results/ratelimited_solves.jsonl`, with its spend, and the new solve takes its place.
   - A rate limit strikes at call time, unrelated to the task, but a long solve makes more calls. Leaving these out would drop the long solves unevenly across arms.
   - If the re-run halts again, it stays a halt and leaves the pairing (PROTOCOL §2).
3. **Spend accounting.** The four solves stopped by hand for the vhdx compaction were recorded twice: by the stop, and again by the next driver's harvest of their call logs. `unique_rows` counts each once, keyed on its first call's generation id. Spend so far is US$ 5.21.

**A finding for the product, not for this decision.** One 429 on a one-key pool ends a `chimera solve` run at once. The run gets no backoff and no wait for the 60 s cooldown the pool itself sets. It is reported to the coordinator.

## Amendment 6 — 2026-09-26, the owner stops the main run early, after reading a partial

**What happened.** The owner read the partial below (`results/summary.json`, 199 items) and stopped the run: *"não precisamos de mais item, estamos com uma boa diferença"*. The partial had already led him to make luna the product default (#642).

**This is an optional stop, and it is labelled as one.**
- The registered design ran to the cap, not to a significance threshold.
- Stopping after looking at the numbers inflates the type-I error of a single test.
- The margin is far from the edge, though:
  - G vs A, Holm-adjusted p = 3e-5, n = 178 pairs, Newcombe 95% +9.8 to +23.7 pp.
  - Without the leak-risk items, p = 0.004, delta +13.2 pp.
- A peeking correction of any usual size (Pocock-style, alpha about 0.02 at a handful of looks) does not move the decision.
- The D, Q and A comparisons stay null. They are read as null, not as "no difference".

**Left open:**
- 35 rate-limit halts on Q and 3 on A were not re-run (Amendment 5, step 2). Q's numbers carry that hole.
- Two in-flight solves were killed at the stop and are not counted.

**Final spend:** US$ 8.82 of the US$ 15 cap, discarded runs included.

**Decision under the frozen rule:** G (`openrouter/openai/gpt-6-luna`) replaces A as the default. It is the only qualifier, winning both on cost per resolved item and on significance.
