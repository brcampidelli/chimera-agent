---
name: build-the-gate-before-the-content
description: A check added after the thing it guards is a check somebody switches off to ship. Wire it shut while there is nothing to block.
version: 0.1.0
kind: pattern
triggers:
- planning a migration
- starting a new surface
- we will add the lint later
- ordering the work
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

You are about to build something that will need a rule enforced across it — a style constraint, a
freshness check, a schema, a link validator, a budget. The natural order is to build the thing and
add the check once there is something to check.

It does not apply to a check you are adding to code that already exists; that is a different and
harder job, and the answer there is a ratchet, not a wall.

## Do

1. Add the check in the first commit, before there is any content for it to fail on. It passes
   trivially, which is the point: it starts life green.
2. Give it a failure message that says what to run. A gate whose output is `assert False` gets
   deleted by the next person who hits it at 2am.
3. Write the check so it fails on the *first* violation, not the hundredth. Ratchets are for
   legacy; greenfield starts at zero.
4. When the check does fire, fix the content. Every time you change the check instead, note in the
   commit message which case forced it.

## Avoid

"We will turn this on once the pages are written." By then the check has a backlog, turning it on
means a day of unrelated fixes, and the cheapest path is a `skip` list that never shrinks.

Avoid also loosening a gate in the same commit as the feature that tripped it. The loosening is
invisible in a large diff, and it is the exact moment a gate stops being one — so it belongs in its
own commit with its own explanation.

## Check

Introduce a violation on purpose and confirm the build goes red — then remove it. Do this on the
day you write the gate, not later.

A gate nobody has watched fail is not a gate; it is a file that claims to be one.

## Risk

A gate written before the content encodes an assumption about content that does not exist yet, and
some of those assumptions turn out wrong. Expect to narrow it once or twice early on.

That narrowing is the dangerous moment. Each one makes the rule weaker, and a rule narrowed three
times without anybody noticing is a rule that no longer covers the case it was written for. Pin
every narrowing with the failing example that caused it.
