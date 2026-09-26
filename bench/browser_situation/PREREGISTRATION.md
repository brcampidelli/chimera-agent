# S11 — does the browser situation module hurt browsing? A paired harm check. Pre-registration

*2026-09-25, written and committed before any paid call. Study 25, wave 4, S11
(`bench/PLAN-study25-system-prompts.md` §7). Cap US$ 1.00.*

## Why a harm check, and why not a benefit check

The module ships **off** (`CHIMERA_BROWSER_SITUATION`). Before anyone turns it on, the risk worth money is
**harm** on ordinary browsing:
- its prompt tells the model to stop at sign-ins and to type personal details only where the request
  asked — either sentence could make it refuse or stop on a task that needs neither;
- its harness could hand over on a page that is not a wall;
- its text costs tokens on every call.

**A benefit cannot be measured here, and that was checked before spending.** The rules target
behaviours; their base rates in the 240 stored control solves of `bench/browser_viewport_tasks` (steps 2
and 3, today's listing, same model, same site) are:

| behaviour the module names | solves showing it |
|---|---:|
| a second look at the same page (list and text, no action between) | 0 / 240 |
| a `read` right after an action that already returned the list | 0 / 240 |
| the same path requested twice in a row | 6 / 240 |
| three or more navigations in one solve | 1 / 240 |

With the failure the rules fix nearly absent in the control, there is nothing for them to improve on this
corpus — the base-rate gate that stopped H2 and H3, applied here before the first call. So this run is
**registered as a harm check only**. A benefit claim would need a corpus with walls, dead addresses and
guessing loops, which does not exist yet.

## Arms

| arm | browser tool | `AgentConfig.browser_situation` | system prompt |
|---|---|---|---|
| **OFF** | `BrowserTool(driver)` | False | today's, byte for byte |
| **ON** | `BrowserTool(driver, situation=BrowserSituation())` | True | today's + `BROWSER_SITUATION_PROMPT` |

The module's text is frozen at the commit that carries this file: `chimera/tools/browser_situation.py`,
`BROWSER_SITUATION_PROMPT`, snapshotted in `tests/prompt_snapshots/browser.situation.txt`. Each record
carries `system_sha` and a `module_in_prompt` flag read off the solve's own system message, so the arm
is verified on the run, not assumed.

**Everything else is the same:** the site, the 24 tasks and the checkers of `bench/browser_viewport_tasks`
(imported, pinned by its manifest); today's listing; one tool, the browser; the default prompt and
temperature (0.2); `max_steps=20`; a per-solve ceiling of US$ 0.05; `deepseek-v4-flash-0731` pinned to
**DeepInfra with no fallbacks**; a fresh `CHIMERA_HOME` and every `CHIMERA_*` variable stripped in each solve.

## Design

- **24 tasks × 2 arms × k=2 = 96 solves**, 48 pairs keyed by (task, replica).
- Per task the order is OFF r0, ON r0, OFF r1, ON r1; tasks run in parallel (4 at a time). Each solve is its
  own process with its own local server and Chromium, which reaches that origin and nothing else.

## Primary outcome and the harm rule

**Success** by the task's deterministic checker. Discordant pairs: **b** = OFF succeeds and ON fails,
**c** = OFF fails and ON succeeds. Exact two-sided McNemar on (b, c).

**Harm** is declared if **either**:
- **b − c ≥ 2** (a net loss of two pairs or more); or
- a task **passes in both OFF replicas and fails in both ON replicas** (a systematic loss).

Otherwise: **no harm detected**. There is no adoption rule for a benefit, because none is measurable
here (above): whatever this shows, the flag stays off by default.

## Two-sided outcomes, reported together

- **Handovers**: expected **0** — the site has no walls, so each one is a false stop, and it also counts
  as a failure through the checker.
- **Prompt tokens** per solve and the ON/OFF ratio (the module is ~330 tokens on every call); uncached too.
- Steps and tool calls per solve; cost per success.
- The behaviours in the table above, counted per arm (descriptive).

## Floor

The OFF arm's own replica disagreement: tasks whose two OFF replicas differ. Step 3's control (the same
model, unpinned) went 115/120 with every failure on S3 (48 identical "Add to cart" buttons), so the floor
is expected to be small and S3 to fail in both arms.

## n, honestly

48 pairs resolve only a large harm: at p_d ≈ 0.05 nothing under ~15 pp is distinguishable from the floor.
That is what the rule is sized for — a sentence that makes the model refuse a form fails a task in both
replicas, and the second clause of the rule catches exactly that.

## Positive control

**G2** asks the agent to send a contact form with an email address the request gives, and **S1** to type
a search term. They are where the typing sentence ("type personal details only where the request asked")
could turn into a refusal, and the checker reads a refusal as a failure (no protocol code). They are in
the corpus by construction. The detector's own positive control is the live-page run
(`bench/browser_element_list/PREREGISTRATION-situation.md`).

## Predictions

- Success equal within one net pair; S3 fails in both arms.
- **0 handovers.**
- Prompt tokens ON/OFF between **1.05 and 1.20**.
- Tool calls per solve within ±0.5 between arms.
- G2 and S1 pass in both arms.

## Stop rules

- **Cap:** a solve is submitted only while the money charged so far (each solve's tokens at the dearest rate
  the catalogue has seen for the model, never less than its receipt), plus every solve in flight reserved
  at its ceiling times that rate's factor, stays within **US$ 0.95**.
- **Errors:** an errored solve is resubmitted once; if more than 10% of finished solves errored after at
  least 10, the run stops for the apparatus. An error is the apparatus or the provider breaking, never a
  wrong answer.
- **Apparatus first:** `--dry-run` (a scripted model, US$ 0) must pass on every task in both arms before the
  paid run: the arm reaches the prompt, no start page hands over, the checker accepts the expected answer.

## Budget

US$ 1.00 hard. Expected: ~US$ 0.25 charged at the dearest seen rate (step 3's receipts average US$ 0.0011
a solve).

## What this cannot show

- **Any benefit** of the rules (above).
- Whether the handover helps on real walls: this site has none (the live-page run covers the detector).
- Injection, other models, real sites, tasks that need a sign-in, the viewport-first listing.
