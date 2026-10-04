# Project packs — does narrowing the agent's skills and servers pay for itself?

Committed before any harness exists or runs (study 29, P7.6). It fixes what counts as an answer, so
the answer cannot be chosen after seeing the numbers. **Nothing here has been run.** The feature it
measures, `CHIMERA_PROJECT_PACK`, ships **off** until this bench reads.

## The question

Installed skill bundles and configured MCP servers are per home, so every project receives all of
them: their prompt lines (bundles) and their schemas (MCP tools, unless deferred). A project's
`.chimera/pack.json` keeps only some. Two things can happen, and they pull in opposite directions:

- **the saving**: fewer prompt tokens per step, and fewer irrelevant tools to choose among;
- **the risk**: a pack that leaves out something a task needed, so the turn picks a worse tool or
  cannot do the thing.

> **With a pack that names what the project uses, do turns in that project spend fewer prompt tokens
> without choosing worse tools?**

## What is measured

Primary, two readings, both required:

1. **prompt tokens per turn**, summed across steps from the provider's own usage;
2. **tool-selection accuracy**: the fraction of a task's tool calls that land on a tool in the task's
   labelled acceptable set, and **completed** (the task's verifier exits 0), per task, per arm.

Secondary, never the headline: steps per turn; whether a turn called a tool the pack removed (it
cannot, by construction — a non-zero count is a defect in the harness or the feature, and stops the
reading).

## Arms

| arm | `CHIMERA_PROJECT_PACK` | pack in the task folder |
|---|---|---|
| A | off | present, ignored — today's behaviour |
| B | on, pack accepted | names the project's own skills and servers |

Same model, same tasks, same fixtures, same home (the same installed bundles and configured servers
in both arms), same `max_steps`. Paired: every task runs in both arms; the comparison is within-task.
**Three seeds per arm** — two alert, three decide.

## Tasks

At least twelve, in a home with at least six active bundles and three configured MCP servers, so the
pack has something to remove. Two thirds need only what the pack keeps; one third is a **trap**: the
task needs a tool or skill the pack leaves out on purpose. The traps are what make the risk visible —
a bench of only in-pack tasks could not show the pack hurting, and would read as a refutation of a
risk it was unable to observe.

## Decision rule, fixed now

Adopt as default (`CHIMERA_PROJECT_PACK` on) only if **all** hold:

- prompt tokens per turn fall by at least 10% on in-pack tasks (median of paired differences, three
  seeds);
- completion on in-pack tasks does not fall by more than the seed spread measured in arm A;
- tool-selection accuracy on in-pack tasks does not fall (paired, McNemar at α = 0.05);
- on trap tasks, the run says what it could not do rather than claiming success — a false success on
  a trap task blocks adoption whatever the token saving.

Anything else: stays off, the result is published with the same prominence as a positive one, and
the switch remains the owner's choice on the Settings screen.

## Not measured here

Whether a third-party repository's pack is trustworthy. That is a consent question, answered by the
owner accepting the exact bytes per folder (`chimera/core/project_pack.py`), not a number.
