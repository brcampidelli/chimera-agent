# Addendum 02 — what the smoke run found, and what it means for reading the result

**Written 2026-08-28, after a one-task smoke run of six solves and before the pilot.** The smoke run
existed to separate "the ruler does not work" from "the model does not solve". It found both a broken
ruler and something more important about the mechanism.

## What the smoke run proved

Six solves ran end to end and all six passed. All three panel members are alive on this corpus
(`claude-opus-5`, `gpt-5.5`, `gemini-3.1-pro-preview`, 1/1 each), so the ADDENDUM-01 screen finds
nothing to exclude. The workspace fork, the pytest gate and `assert_discriminating` all work.

## 1. The cost reader returned a confident zero for six paid runs

`read_cost` read `usage.jsonl`, and printed `0 tokens, $0.00, ratio=inf` for six runs that cost real
money. Two independent reasons, either of which alone was fatal:

- **Wrong file.** `append_usage` is called only from the API layer (`chimera/api/app.py`,
  `chimera/api/code_api.py`). A CLI `solve` writes nothing there — the newest row in that file was
  three days old. The CLI's receipt lives on the **attempts** of `runs.jsonl`, one level below where
  a reader naturally looks. This project has made that exact level error before.
- **Wrong directory.** `settings.home` defaults to `Path(".chimera")` — **relative**, resolved
  against the process's cwd, which for a solve is the repo root. The reader looked in
  `~/.chimera`, which does not exist on this machine.

Fixed by joining on the **workspace path** instead of a time window. The fork directory is unique per
cell and is recorded on the row, so the join is exact. The window version compared an ISO timestamp
against `str(time.time())` as strings — a comparison that cannot be right in either direction.

**And a guard, because a zero that looks like a measurement is the thing that just happened:** a cell
whose join finds no rows records tokens as **unknown**, never as zero, and the pilot refuses to print
a full-run estimate when any arm reads unknown.

## 2. The mechanism: on this corpus, fusion acts on the PLANNING turn only

This is the finding, and it is read off the code rather than inferred from a number.

`RoutedBackend.complete` (`chimera/fusion/router.py:173`) opens with:

```python
# Tool-calling turns must go to a single model (fusion doesn't tool-call).
if tools:
    return self.single.complete(...)
```

The `solve` worker is a tool-calling agent — it writes the files. So **every worker turn passes
tools and therefore never fuses.** The editor is excluded a second time and deliberately, at
`chimera/cli/main.py:3415`: *"The EDITOR's model. Never fused: synthesising three patches produces
one that applies cleanly and means nothing."* What `--fuse` does add is the planner, wired straight
to the engine at `main.py:3320`.

The smoke run agrees: arm A's attempt recorded `model=openrouter/deepseek/deepseek-chat-v3.1` — the
default worker model — while arms B and C recorded the pinned `claude-opus-5`.

**So arm A is: fused planning, single-model tool-calling worker.** That is the shipped product and it
is what a user gets; it is not "three models writing the patch".

### What this does to the reading — the `§2q` statement, before the numbers

The comparison stays exactly as registered. What changes is what a result may be said to mean:

- **A loses to B** ⇒ *fusion as shipped does not beat spending the same budget on a better model, on
  gated code tasks.* That is a decision about the default route, and it is the question worth having.
  It is **not** evidence that panel aggregation fails — the panel never wrote a line of the patch
  here, so this corpus cannot say anything about that.
- **A matches B at a lower token ratio** ⇒ fused planning buys the same outcome more cheaply, which
  is a stronger result for fusion than the design anticipated, and criterion 3 is what shows it.
- **A wins** ⇒ a planning turn worth three models, with a cheap worker. Also worth knowing, and
  narrower than the headline the design would otherwise invite.

Nothing here can speak to fusion on prose, where turns are tool-free and the router fuses them —
which is where `ADDENDUM-01`'s and the pre-registration's limits already pointed.

## 3. An activation guard, because "on and inert" reads as "did not help"

The pre-registration asks for a token ratio as a price ceiling. The same number doubles as proof the
intervention acted: if arm A and arm C spend the **same** tokens, fusion did not fire and every
comparison in the run is void rather than null.

The runner now prints `A/C` beside the verdict and says so in words when the ratio sits inside
±10% of 1.0. An intervention that reports how much it acted is the only kind whose null can be read.
