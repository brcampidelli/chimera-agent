# Pre-registration — explorer census: how often real traces sit in the regime the explorer pays in

**Registered 2026-10-02, before the census ran on any trace.** Study 28, item MA5 / R6 of the review.
US$ 0: it reads traces already committed under `bench/` and calls no model. The analysis is
`census.py`, committed in the same commit as this file; its arithmetic is pinned by
`tests/test_explorer_census.py` on traces small enough to sum by hand. This file follows
`bench/PROTOCOL.md`; §2 (a halt is not a failure) and §3 (the cache is part of the apparatus) are the
rules that apply, and where each is met is said below. §1, §4–§6 and §8 concern live runs and do not
apply to an offline census; §7 (a split groups by what repeats) is met by reporting per arm, since the
same 199 items repeat across the four arms.

## The question

The read-only Context Explorer (`chimera/core/explorer.py`) is opt-in and unmeasured. The desktop
offers it only in the run launcher (`/api/runs`) and on the CLI (`chimera explore`); the Code turn's
request model accepts `explorer` too, but the Code screen never sends it. `bench/hierarchy_multistep`
and `bench/hierarchy_sweep` measured that isolating reading in a sub-agent cuts tokens by about
(D−1)/D in the **multi-document, multi-step** regime (66.5% at D=3, n=6), and that it **loses** in the
single-shot regime (`bench/hierarchy`, +47%). So the question that decides whether the explorer belongs
on the Code screen is not "does it save" but **how often do real turns sit in the regime where it
saves, and how much would they save**.

## What the repository holds, and what it does not

Searched before writing this: every tracked `.json`/`.jsonl` under `bench/` that mentions a tool name.

- **No attended turns with tool traces.** Nothing in the repository stores a Code-screen turn, a
  steplog or a session transcript. `bench/stop_gate` was built from this machine's run transcripts but
  commits only a redacted projection (task, final message, file-change count) with no tool sequence;
  `bench/chat_history` is a three-arm chat run with no tool traces. **So the census cannot read the
  attended population.** It reads the closest population that is stored, and says which.
- **Primary — tokens per call:** `bench/default_model/results/main_solves.jsonl`, 804 solves of
  `chimera solve`'s worker loop (the same `Agent` loop, coding tools, `turn_context`, no context
  budget, no compaction) on SWE-bench Verified django items, four models (arms A/D/G/Q). Each row
  stores every model call's prompt and cache-read tokens, each call's tool count, the flat ordered
  list of tool names, and the patch. It does **not** store tool arguments, so the files a call read
  are not named.
- **Secondary — tool sequences only, no per-call tokens:** `bench/directive_boundary/results/run.json`
  (questions and directives over a small fixture repo) and
  `bench/unattended_claims/results/pilot_fixtures.jsonl` (small fixture repos). They can say how many
  reads precede the first action; they cannot price anything. They are the closest thing stored to a
  small interactive turn.

**What I had seen before writing this**, to keep the predictions honest: the first row of
`main_solves.jsonl` (to learn its schema: an arm-G solve of 13 calls whose opening was
`todo_write, grep, grep, glob, read_file ×4, grep`), arm A's line in `bench/default_model/results/summary.json`
(mean 27 steps, 140 of 196 solves ending at `max_steps`, mean prompt ~285k tokens per solve,
cache-read share 0.86), the first row of each secondary file (one `unattended_claims` run opened with
four reads), and `bench/hierarchy_multistep` / `hierarchy_sweep` results. No per-trace count, phase
length or saving had been computed.

## Definitions (as `census.py` implements them)

- **Usable trace.** Not halted (PROTOCOL §2: `halted` set → out of every denominator); has calls; the
  per-call tool counts sum to the length of the tool list (otherwise which call read what is unknown,
  and the trace is out, counted as `misaligned`); and **append-only** — no call's prompt is smaller
  than the previous one's (the counterfactual subtracts what was read from every later prompt, which is
  only right if the loop kept it; a trace that shrank is out, counted as `not_append_only`).
- **Opening read-only phase** (`e` calls). The longest opening run of calls whose every tool is in
  {`read_file`, `grep`, `glob`, `list_dir`} or the neutral `todo_write`; a call with no tool does not
  end it unless it is the last call (the answer). It ends at the first call that emits anything else —
  `edit_file`, `write_file`, `apply_patch`, `run_shell` (even a read-only shell command: conservative,
  it shortens the phase), `job_*`. This is the localisation the explorer was built to take
  (FastContext's split of exploring from solving). Reads after the first action stay in the main loop
  in both arms.
- **D (reads).** `read_file` calls in the phase. Without arguments, repeated reads of one file count
  twice, so D is an **upper bound** on distinct files.
- **Post calls** (`M`). Calls after the phase, including the final answer.
- **The regime.** `D ≥ 3` and `M ≥ 3` — `bench/hierarchy_multistep`'s own setting (three documents,
  three turns that carry them).

## The counterfactual

Prompt tokens of the solve as it ran, against the same solve with its opening phase handed to an
explorer, call by call (`census.counterfactual`):

1. the main loop's first call asks the explorer: the size of call 0;
2. the explorer replays the phase's calls **at the size the main loop paid** — conservative, since its
   system prompt and tool list are smaller;
3. the explorer's closing call sees everything it read: the size of call `e`;
4. every later main call carries the block instead of the reading:
   `prompt_i − R + X + B + RR`, with `R = prompt_e − prompt_0` (all the phase added), `X = 60` (the
   explore call message), `B` the returned block, `RR` the re-reads below;
5. **re-reads:** the main loop must open again the files it edits — `min(D, max(1, files in the
   patch))` files, each of this trace's median single-read size (the prompt growth after a call whose
   only tool was one `read_file`, which includes the request message: an overstatement, the
   conservative side; the arm's pooled median when the trace has none). Each re-read is also one extra
   call, of the size of the first post-explorer call.

Completion tokens are excluded from both arms (the same work in both). Readings: `B ∈ {300, 1000}`
(the plain contract's eight lines; the S12 contract's findings and gaps) × re-reads on/off. **The
decision reads the conservative cell: B = 1000, re-reads on.**

**Cache (PROTOCOL §3).** The traces carry cache-read tokens per call, so the census also prices the
input side with each arm's pinned rates (`bench/default_model/PREREGISTRATION.md`), assuming what the
explorer removes from a later prompt is its **oldest, cached** part and what it adds is uncached. This
is reported beside the token saving and never replaces it; with a cache share near 0.86 the dollar
saving can be far smaller than the token one, or negative.

## Readings

Pooled over the four arms and per arm: usable traces and each exclusion count; the regime's share;
**histograms** of D and of M (not just means); median phase length and read size; per cell the pooled
token saving (Σcf/Σbase), the median per-trace saving, the number of traces where the explorer costs
more, and the cache-weighted input-dollar saving — over all usable traces and within the regime. The
law's own reading, the median of (D−1)/D over regime traces, is printed for comparison only. For the
secondary files: runs, the share with ≥ 3 reads before the first action, and its histogram.

## Predictions (written before the census ran)

1. **Regime share** (primary, pooled): **≈ 50%**, inside 30–70%. Django fixes usually read several
   files before patching, and most solves run long.
2. **Median saving within the regime** (conservative cell): **≈ 25%**, inside 10–40% — well under the
   law's (D−1)/D, because the main loop keeps reading after its first action and that context is not
   delegated.
3. **Pooled saving over all usable traces** (conservative cell): **≈ 15%**.
4. **Cache-weighted input dollars** (conservative cell, pooled): **under 10%, and less than half of
   the token saving**.
5. **Secondary** (small fixtures): the share with ≥ 3 reads before the first action is **≤ 25%** in
   each file — small interactive turns rarely reach the regime.

## The recommendation rule (frozen; `census.verdict`)

- **VOID** if fewer than 100 traces are usable, or more than 25% of the non-halted traces fail the
  alignment or append-only check. Nothing is recommended either way.
- **RECOMMEND** only if all three hold in the conservative cell:
  regime share **≥ 25%**; median saving within the regime **≥ 20%**; pooled saving over all usable
  traces **≥ 10%**.
- Otherwise **DO NOT RECOMMEND**: the explorer stays where it is.

Whatever the verdict, it carries two labels when they apply, printed with it: **"proxy population"**
(always: unattended SWE-bench solves, not attended Code turns), and **"token-only"** when the
cache-weighted dollar saving is under 5%. And **RECOMMEND means "offer it on the Code screen as an
opt-in, and run a paired live A/B before any default"** — never "turn it on": the census sees tokens,
not whether the main loop solves as well with a `path:line` block as with the files it read.

## What this cannot show

- **Quality.** Whether a solve with a delegated phase still resolves — the explorer can miss the
  location, and a cheap explorer model can miss more. Only a paired live run reads that.
- **The attended population.** Interactive Code turns are shorter, smaller and steered by a person;
  the secondary files are the only window on them and carry no tokens. A census over this machine's own
  transcripts would need an extractor that keeps the tool sequence and per-call tokens, which no stored
  projection has.
- **Distinct files.** D counts reads, not files.
- **Which calls the agent would really delegate.** The counterfactual delegates the whole opening phase
  when it has any read or search; an agent offered the tool may call it less, more, or mid-solve.
- **The explorer's own model.** It is priced at the main loop's model and size; the role is meant for a
  cheaper model, so its real cost is lower — another conservative side.
