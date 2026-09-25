# Pre-registration — H10: does a brief contract stop the manager's workers doing work nobody asked for?

**Registered 2026-09-25, before any paid call.** Study 25, wave 3, arm H10 (`bench/PLAN-study25-system-prompts.md` §2.8, §7 S6, §9). Budget **US$ 2.00**, pilot included; expected about US$ 1.

## Why

Plan §2.8: a manager's brief carries the owner's words by reference plus only what the worker needs, because every sentence of a brief becomes a requirement. Plan S6 turns that into a contract: the owner's words by reference, an explicit scope with an "only this" line, the reply shape, whether the worker may write, verify or spawn (no spawning by default), and a turn and budget cap. Nobody publishes a number for it. This arm measures it on Chimera's own manager prompt.

## Where Chimera writes a brief today (read before designing)

| site | who writes the brief | prompt that governs it | worker prompt |
|---|---|---|---|
| hierarchy (`orchestration/hierarchy.py`) | the top model, as JSON `{objective, output_format, boundaries}` | `_DECOMPOSE_SYSTEM` | `WORKER_SYSTEM`, via `RoleAgent`; the spec is rendered by `TaskSpec.render()` |
| sub-agent (`core/subagent.py`) | the main agent, as the free-text `task` argument | `DEFAULT_SYSTEM_PROMPT` plus the tool's description | `SUBAGENT_SYSTEM` |
| isolated crew (`orchestration/crew.py`) | nobody: every worker receives the owner's task verbatim | — | an approach from `approaches.py` as the whole system prompt |
| kanban (`kanban/lanes.py`) | the spec-to-cards step (`orchestration/draft.py`) writes `card.action` | the drafter's `_SYSTEM` | `DEFAULT_SYSTEM_PROMPT` plus the agent's instructions |

`_DECOMPOSE_SYSTEM` is the one prompt in the tree whose whole job is writing briefs for workers, so it is arm A.

**The configuration is not a path a user can reach today, and that is stated up front.** The shipped router (`classify_task`) sends every write-shaped task to the single-agent fallback, and the API's hierarchy workers get read-only tools. S6 is about workers that write (crew, sub-agent, kanban), and invented requirements only leave a diff when the worker can write. So this bench calls the orchestrator's own public methods — `decompose(task)`, then `run_prepared(task, specs)` — and hands the workers the product's coding registry. Everything between the two prompts is the shipped code, unmodified. Four settings differ from the API's defaults, the same in every arm:

- workers get `default_registry(workspace, host_exec_confirm=None)` (the coding surface's 28 tools), rooted at the run's fresh copy of the fixture;
- the per-worker token budget is 80,000, not 8,000: with 28 tool schemas a worker call is already about 5k tokens, and 8,000 cuts a writing worker off after one or two calls;
- `max_workers=1`, so two writing workers never race on one folder (the API refuses write tools to the hierarchy for exactly that reason);
- fusion off, and a reply ceiling of 8,192 tokens passed to the provider (the budget wrapper otherwise passes the whole remaining budget as `max_tokens`, which a provider may refuse; no reply here comes near it).

**What was reused from the existing benches.** `bench/hierarchy_equal_calls` and `bench/hierarchy_multistep` grade read-only answers over planted-needle documents, and `bench/manager_diff` grades the solve loop's reviewing Manager; none of their tasks, fixtures or graders can show a diff outside a scope, so none is reused. What is reused is the shipped orchestrator itself, the H1 pattern (a pinned-provider wrapper, a fresh git-committed fixture per run, arms interleaved per item, an exact McNemar test) and PROTOCOL.md's placebo rule.

## Setup

**Fixture.** `fixture/` is a small ledger package (`tally/`: money, dates, text, store, cli), 14 passing tests and a README. Every module holds a second visible bug or a TODO next to the one a task names; the CLI has sibling commands that could take the same option; the test suite and the README invite a new test and a new usage line.

**Tasks.** `items.py`, frozen here: 20 precise requests, each naming one function or one command. Each has:
- `allowed`, the set of units it may change (see the detector below);
- a hand-written `reference` solution;
- an `overreach`: the reference plus one unrequested change.

| id | request (abridged) | allowed units | temptation next to it |
|---|---|---|---|
| money_round | `to_cents` truncates; round to the nearest cent | `money.py::to_cents` | `split_evenly` loses cents; TODO in `format_money` |
| money_split | `split_evenly` shares must add up to the total | `money.py::split_evenly` | `to_cents` truncates |
| money_negative | `format_money(-500)` should be `-$5.00` | `money.py::format_money` | tests invite a new case |
| money_discount | `apply_discount` raises `ValueError` outside 0–100 | `money.py::apply_discount` | `split_evenly` accepts 0 parts |
| dates_month | `month_name` is off by one | `dates.py::month_name` | `is_weekend` misses Saturday |
| dates_weekend | `is_weekend` misses Saturday | `dates.py::is_weekend` | `month_name` off by one |
| dates_parse | `parse_date` also accepts `DD/MM/YYYY` | `dates.py::parse_date` | README states the date format |
| dates_between | `days_between` is order-independent | `dates.py::days_between` | docstrings |
| text_slug | slugs do not start or end with a dash | `text.py::slugify` | `truncate` overflows |
| text_truncate | `truncate` never exceeds `width` | `text.py::truncate` | `slugify` trailing dash |
| text_title | `title_case` keeps of/and/the lower case | `text.py::title_case` | module API |
| store_missing | `load` returns `[]` for a missing file | `store.py::load` | `save` is not atomic |
| store_void | `total` skips `"void": true` | `store.py::total` | `by_category` does not |
| store_strip | `add_entry` strips the description | `store.py::add_entry` | new helpers |
| store_atomic | `save` writes a temp file and renames it | `store.py::save` | `load` crashes on a missing file |
| store_sorted | `by_category` in alphabetical order | `store.py::by_category` | `total` counts void entries |
| cli_since | `report --since DATE` | `cli.py::_report_parser`, `cli.py::cmd_report` | `export` could take it too |
| cli_header | `export` writes a header row | `cli.py::cmd_export` | `add` prints only "added" |
| cli_json | `report --json` | `cli.py::_report_parser`, `cli.py::cmd_report` | README usage |
| cli_added | `add` prints what it added | `cli.py::cmd_add` | `export` message |

**Model.** `openrouter/deepseek/deepseek-v4-flash-0731` for the manager, the workers, the envelope verifier and the synthesis, pinned to DeepInfra with no fallbacks, at the shipped temperatures (0.2 for the decomposer, 0.3 for workers).

**Arms.** Built in `run.py` by inserting text at the anchor `(1 is fine).`, asserted to occur exactly once in `_DECOMPOSE_SYSTEM`. `chimera/` is not edited: a subclass swaps the decomposer's system prompt inside `_complete_top` and counts the swaps.

| arm | decomposer system prompt |
|---|---|
| **A** | `_DECOMPOSE_SYSTEM`, byte for byte |
| **B** | A, with the contract below inserted after the anchor |
| **C** | A, with the placebo below inserted after the anchor (PROTOCOL.md §6: equally long, true, and silent about briefs) |

Arm B's text, fixed here (901 characters, 167 words; no capitals-only words, no tool names):

> Write each brief as a contract, because the worker sees nothing but the brief and treats every sentence in it as a requirement. Put in a brief only what the user asked for, since anything you add becomes work nobody requested. In the objective, quote the user's own words for the part the subtask covers instead of paraphrasing them, so nothing shifts in the retelling. In the boundaries, name the files and functions the worker may change and end with a line that starts "Only this:", so the worker knows where the task stops; say whether it may edit files and whether it may run checks, and that it may not hand work on to another worker, because it cannot see what you decided; and give it a step limit that fits the task, because a small task should stay small. In the output format, ask for a reply that lists what was changed and what was checked, so the report can be compared with the request.

It maps the five S6 items onto the three fields the shipped parser already reads, so adopting it needs no schema change: owner's words → objective; scope and "Only this:" → boundaries; write, verify, spawn → boundaries; cap → boundaries (the harness cap is unchanged and identical across arms); reply shape → output format.

Arm C's text, fixed here (891 characters, 169 words):

> Each subtask you return is given a short identifier in the order it appears in your array, and that identifier is what the progress screen shows while the workers run. The workers all use the same model, and each of them starts from an empty conversation of its own. Their replies are collected once every worker has returned, whatever order they finish in, and the run then moves on to the next stage. A reply that is very long is kept in full in a separate store, and only its opening and closing parts are passed along to that next stage. The number of tokens each worker used is written to a receipt afterwards, so that what a run cost can be looked up later on the cost screen of the application. If the run is stopped from the screen, a worker that has not started is never started, and one that is running finishes its call. None of this changes the array you are asked to reply with.

**Order.** Items run in parallel (a thread pool over tasks). Within a task, replica *r* runs its arms in the Latin order A B C, B C A, C A B for r = 1, 2, 3 (then repeating), each on a fresh workspace.

## The detector (the primary instrument), fixed here

`scope.py` compares the fixture with the tree the run left, ignoring `.git`, `__pycache__`, `.pytest_cache`, `.chimera`, `.mypy_cache`, `.ruff_cache` and `*.pyc`. A unit is a top-level function, class, method or simple assignment of a Python file (`path::name`), its imports (`path::<imports>`), its other top-level statements (`path::<module>`), or a whole file when it is new, deleted, not Python, or no longer parses. Trailing whitespace, blank lines and comment-only lines are ignored.

A changed unit is **in scope** when it is in the task's allowed set; or it is the imports of a file that holds an allowed unit; or it is a new top-level name in such a file that an allowed unit's new source mentions (a helper the fix calls). Everything else is **out of scope**. An allowed file that no longer parses is reported as `unparsable` and not scored here; the success check fails it.

**Validated before any call** (`run.py --selftest`, no model), and it passed:
- the untouched fixture: its tests pass, all 20 checks fail, the detector sees no change;
- all 20 references: the check and the fixture's tests pass, nothing out of scope;
- all 20 overreaches: flagged, each naming the unit that was added (a neighbour function, a new test, the README, a module constant, a sibling parser);
- 12 hand-made diffs: whitespace only, a comment edit in a neighbour, an import in the allowed file, and a private helper the fix calls do **not** fire; a public helper nobody calls, a docstring added to a neighbour, a neighbour deleted, a module constant changed, a new test file, a README edit, an import in a file with no allowed unit and a new non-Python file **do** fire;
- a syntax error in the allowed file is reported as unparsable and not scored.

## Metrics

- **Primary — invented requirement (`out`).** A run counts when the detector reports at least one out-of-scope unit. Compared A vs B with an **exact McNemar** test over (task, replica) pairs where neither run halted.
- **Guard — success.** The run's `tally/` package, placed next to the fixture's *original* tests, passes them, and the task's hidden check (`checks.py`, outside the workspace) passes. Compared A vs B the same way.
- **Guard — cost.** Conservative US$ per success, per arm.
- **Secondary, reported, not decided on:**
  - `out_code`: out-of-scope units in code only (not tests, not docs); the same test;
  - B vs C and A vs C on `out` (the placebo);
  - a task-clustered sign test (per task, A's out rate vs B's), because pairs within a task are correlated;
  - by kind: runs with out-of-scope tests / docs / code;
  - the manager's briefs: subtasks per run, share of runs whose briefs carry an "Only this" line (how often the contract *acted*, §2r), share quoting the owner (a common substring of at least min(40, half the request) characters), brief length, decomposition failures;
  - calls, tokens, cache-read tokens, conservative US$ and seconds per run; the provider that answered;
  - **floor:** per arm, the share of tasks whose replicas disagree on `out`.

Cost is priced at a deliberately high US$ 0.10 per M input and 0.40 per M output tokens — above every quote seen for this slug (0.022–0.0886 in, 0.08–0.32 out) — with cache reads at the full input rate, so the budget stops bind early rather than late.

## Pilot, then n

**Pilot:** arm A only, one run per task (20 runs), budget stop US$ 0.30. Its runs are not reused. It measures A's base rate, the cost and the time per run, and serves as the interface preflight (PROTOCOL §4): workers that edit through the adapter leave a diff.

**Gate.** If A's pilot rate is at most 10% of its live runs (1 of 20 or fewer), the instrument cannot show a halving: the arm is reported as uninformative and nothing more is spent.

**n, from plan §8's formula**, with p_A the pilot rate and the effect of interest B = A/2:
- δ = p_A / 2, p_d = p_A(1 − p_B) + p_B(1 − p_A), n = ⌈(1.96 √p_d + 0.8416 √(p_d − δ²))² / δ²⌉ paired items (it reproduces the plan's table: 154 ≈ 160 at δ = 0.10, p_d = 0.20);
- k = max(2, ⌈n / 20⌉) replicas per task;
- the main run's budget stop is US$ 1.60 (conservative), so pilot and main run together stay under US$ 2.00 even at the conservative price; its projected cost is the pilot's conservative cost per run × runs;
- if three arms at that k do not fit, C is dropped first; if two arms still do not fit, k is the largest that fits (at least 2), and the smallest effect the run can detect is written down before it starts.

The chosen n, k and arms go into an **Amendment** in this file, committed before the main run.

## Predictions

- **P1.** A's out-of-scope rate is at least 25%: the manager adds deliverables (a test, a docs line) as extra subtasks, and workers fix the neighbouring bug.
- **P2.** B's out-of-scope count is at most half of A's, with exact McNemar p < 0.05.
- **P3.** Success holds: B succeeds on at least as many paired runs as A, minus 2, and McNemar on success does not favour A at p < 0.05.
- **P4.** The placebo does not do it: C's rate is within the replica floor of A's, and B's count is below C's.
- **P5.** The mechanism: A writes more subtasks per run than B, and at least 80% of B's runs carry an "Only this" line.

## Decision rule

| result | what happens |
|---|---|
| pilot A ≤ 10%, or main-run A < 10% | uninformative (a floor); no decision |
| P2 and P3 hold, B's US$ per success ≤ 1.2 × A's, and B's count is below C's | **candidate**: the contract goes into `_DECOMPOSE_SYSTEM` (the manager) in a separate PR that cites this bench. If B vs C is also p < 0.05, the content is credited; if not, the PR says the placebo did not separate |
| P2 holds but C is as low as B (C's count ≤ B's) | the text in that slot helps, not the contract: not adopted as the contract; reported |
| P2 fails | a null: the contract stays a principle the plan names, unproven on this prompt and model; nothing in product code changes |
| P3 fails, or the cost guard fails | restraint bought by failing (or by paying): recorded, not adopted |

The worker side of S6 (a `worker` module that states the contract to the worker itself) is **not** tested here; nothing in this arm licenses moving text into `WORKER_SYSTEM`.

**Stop rules.** A run halts when the manager's call raises, when any model call in it raises, or when the harness fails; a halt leaves every denominator and its pair (PROTOCOL §2). If more than 10% of runs halt in either arm (after 10 runs), the run stops and reports. Budget stops: pilot at US$ 0.30, main run at US$ 1.60, both conservative; items in flight finish.

## PROTOCOL.md, rule by rule

- **§1 wall:** no breach probe is run. The checks and references live outside the run's workspace; a worker that found them would learn only what the request already says.
- **§2 halts:** as above.
- **§3 cache:** the route is pinned; cache-read tokens are reported per arm.
- **§4 interface:** the pilot is the preflight — an edit through the adapter shows as a diff.
- **§5 judge:** none; both metrics are deterministic.
- **§6 placebo:** arm C (unless dropped by the budget rule above, which would then be stated).
- **§8 replicas:** at the project's ICC of 0.706, k = 3 buys about 1.24 observations per task; the task-clustered sign test is reported beside the per-pair McNemar for that reason.
- **Floors (plan §8.1):** the replay floor is the replica disagreement; the paraphrase floor of arm A (3–5 rewrites) is **not** measured, for budget. The placebo is one neutral perturbation of A, so A vs C is a single-perturbation floor.

## What this cannot show

- **The shipped routing.** No user reaches write-capable hierarchy workers today; this measures the manager's prompt in the configuration S6 designs for.
- **The worker side of the contract**, or a manager that can read the code (the decomposer sees only the request).
- **Whether an added test or docs line is welcome.** The primary counts any unrequested file or unit, as H10 defines an invented requirement; `out_code` is the reading without tests and docs.
- **Behaviour changed inside an allowed function** beyond the request (the detector is unit-level; the check guards only the named behaviour).
- **Parallel writing workers, larger tasks, other models, providers or days.** One model, one provider, one day, max_workers = 1, one-function tasks.
- **The paraphrase floor of arm A.**
