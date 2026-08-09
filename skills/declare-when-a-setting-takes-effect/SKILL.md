---
name: declare-when-a-setting-takes-effect
description: A control that saves successfully and changes nothing until a restart is worse than one that fails. Say when it applies, and derive that from where it is read.
version: 0.1.0
kind: pattern
triggers:
- adding a settings screen
- the config is cached
- why did my change not apply
- restart required
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

You are exposing a setting in a user interface, and the code that consumes it reads the value at
some point other than every use: at process start, when a session is built, or into a cached
object.

It does not apply to a setting read on every use. Those apply immediately and should say nothing —
a note about a delay that does not exist teaches the same distrust as a missing one.

## Do

1. For each setting, find the line that *reads* it. When it applies is a property of that line, not
   of the control.
2. Prefer making it read live. A property that resolves on access usually costs nothing and removes
   the problem instead of describing it.
3. Where it genuinely cannot — something is started at boot — label it, and generate the label from
   the server or the same module that knows the read site, so the label cannot drift.
4. Group the labels into meaningful classes: applies now, applies to your next conversation,
   applies after a restart.

## Avoid

A settings screen that confirms a save and quietly does nothing. It is worse than an error: an
error sends the user to look for a cause, while a success sends them to blame the feature, the
model, or themselves.

Avoid hard-coding the list of "needs a restart" in the interface. That list is a copy of knowledge
that lives elsewhere, and it goes stale the first time a read site moves — silently, which is how
the original problem was created.

Avoid a blanket "some settings require a restart" note. It is true, unhelpful, and covers the ones
that do not.

## Check

Change the setting, use the feature, and observe the behaviour — not the screen. Read a value back
from the running system, not from the form you just submitted.

The mechanical version: assert that no setting is labelled as immediate, and that every setting the
interface exposes appears in the classification the server publishes.

## Risk

Labels add noise to a settings screen, and a screen where every row has a badge is a screen where
badges mean nothing. Only the delayed ones need one.

The deeper risk is treating the label as the fix. Declaring that a control is inert until restart is
honest; making it live is better, and the label should not become a comfortable way to avoid the
harder change.
