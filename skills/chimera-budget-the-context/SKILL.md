---
name: chimera-budget-the-context
description: Spend against a budget below the real window, and count the tool schemas you re-send on every step — they are the floor compaction cannot reach.
version: 0.1.0
kind: pattern
triggers:
- the run died on context overflow
- raising max-steps
- the agent forgot what it was doing
- adding another MCP server
- deciding what to drop from the prompt
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

You are running a loop that appends to a message list every step — a ReAct agent, a coding turn, a
tool-calling assistant — and the list only ever grows. Symptoms: the provider raises on context
length, or the run survives but starts working on the wrong file after a long stretch.

It does not apply to a single-shot call whose prompt you assembled yourself and can measure once.
There is no eviction policy to design when nothing accumulates.

## Do

1. Write down two numbers before touching the loop: the model's advertised window, and the fraction
   of it you will spend on the prompt. Chimera spends 0.6 (`DEFAULT_BUDGET_FRACTION` in
   `chimera/core/context_budget.py`). The rest pays for the completion and for the gap between your
   token estimate and the provider's real count.
2. Fire compaction on a share of the *budget*, not of the window — Chimera triggers at 0.8 of the
   budget. Compaction needs room to compact into; a trigger set at the window fires when there is
   none left.
3. Measure the fixed floor separately from the growing part. The system message plus the `tools=`
   payload are re-sent on **every** step and no amount of message compaction touches them. Run
   `chimera schema-bench` (or count the JSON yourself) before you assume the history is what is
   expensive. A verbose MCP or OpenAPI import can put tens of thousands of tokens under every single
   step of the run.
4. If the floor is a problem, shrink the floor: strip annotation-only schema keys (`examples`,
   `title`, `default`, `$comment`) and trim parameter prose to the first sentence, keeping `type`,
   `properties`, `required` and `enum` intact so tool selection and argument validity are unchanged.
   That is what `chimera/tools/schema_compact.py` does. Advertise fewer tools if it is still too big.
5. Fix the eviction order explicitly: system message never, recent turns verbatim (Chimera keeps 6),
   the older span replaced by a summary or a factual note. Never rewrite the system message — it is
   the stable prefix the prompt cache is keyed on, and editing it invalidates every cached turn
   behind it.
6. After compacting, re-inject what the run needs to still be itself: the task **verbatim**, the
   plan, the task list with status, and the file currently being edited. Re-read the file from disk
   rather than restoring a remembered copy.
7. When you estimate token size yourself, count the `tool_calls` payload as well as `content`. Tool
   arguments ride on the assistant message and are not in `content`; a size estimate that reads only
   `content` under-reports exactly the messages that grew.

## Avoid

Raising `--max-steps` because a task did not finish. It is the obvious move and it is the one that
kills the run: more steps means a longer message list against the same window.

Dropping the task statement. It arrives as a user message at the front, so it is the first thing a
naive compactor evicts and the last thing the agent can work without. The result is an agent
executing a plan whose purpose was deleted — still confident, still producing edits, no error
anywhere. A file it can re-read; an instruction it cannot.

Leaving a `tool` message at the head of the kept tail:

```python
older, recent = body[:-keep_recent], body[-keep_recent:]   # can orphan a tool result
```

versus

```python
older, recent = body[:-keep_recent], body[-keep_recent:]
while recent and recent[0].get("role") == "tool":
    older.append(recent.pop(0))                            # tail starts on a legal turn
```

Most providers reject a `tool` message whose matching assistant `tool_call` is gone, so the
compaction that was supposed to save the run is what ends it.

Also avoid tuning for maximum compression first. Calibrate recall-first: keeping too much costs
tokens, which is a bill; removing too much loses context that was needed later, and those messages
are gone. One is a cost, the other is unrecoverable.

## Check

Set the window low on purpose — point the budget at a 4K fake window, or run a task you know is
long — and confirm three things: compaction fired, the run continued past it, and the message that
followed contained the original task string. Grep the composed prompt for a distinctive phrase from
the task after compaction. If it is not there, restoration is not working, whatever the logs say.

Then check the floor with a binary question: does one step's prompt, with an empty history, already
sit above your threshold? If yes, compaction can never help — `compact()` returns
`changed=False` on a list it cannot shrink, and the honest reading of a no-op is "this did not
help", not "retry into the same wall".

## Risk

A budget set too low compacts runs that never needed it, and every compaction rewrites the prompt
suffix and throws away the cache hits behind it. On a long coding session that is a real bill, paid
to avoid an overflow that would not have happened.

Schema compaction has its own edge: trimming a parameter description to one sentence is safe for
argument validity but can remove the sentence that told the model *when* to use the tool. It changes
selection behaviour without changing any schema the validator checks, so it fails as slightly worse
tool choice rather than as an error. Measure it as an A/B, do not enable it because it saves tokens.

And the whole pattern buys nothing if the task is genuinely too large for the model. Budgeting turns
a hard ceiling into a soft one; it does not create room that is not there.
