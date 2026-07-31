# Skill library

A **skill card** is a short markdown file that tells the agent how to approach a recurring kind of
task: when it applies, what to do, what to avoid, how to check the result, and what could go wrong.

It is **data, not code.** Nothing here is imported or executed. That is the whole point — it makes
this the one place you can contribute something useful without touching a line of Python, and it
makes reviewing your contribution a matter of reading a page rather than auditing a diff.

## Contributing one

Open a pull request. No issue needed — `skills/` is an *open* area (see
[CONTRIBUTING.md](../CONTRIBUTING.md)).

1. Create `skills/<your-skill-name>/SKILL.md`. The directory name and the `name:` in the frontmatter
   must match.
2. Copy the shape from [`verify-before-claiming/SKILL.md`](verify-before-claiming/SKILL.md).
3. Run `uv run pytest tests/test_skill_library.py` — it checks the structure so a reviewer can spend
   their attention on whether the advice is *good*.

## The shape

```markdown
---
name: your-skill-name
description: One line. This is what the agent reads when deciding whether the skill is relevant.
version: 0.1.0
kind: pattern          # or anti_pattern, for "here is what not to do"
triggers: [a phrase, another phrase]   # when this should come to mind
provenance: clean      # see below
status: active
license: Apache-2.0
---

## Trigger
When this applies — and, just as usefully, when it does not.

## Do
The actual procedure. Concrete steps beat principles.

## Avoid
The tempting wrong move. Usually the most valuable section.

## Check
How to tell it worked. A command, an observable outcome — not a feeling.

## Risk
What breaks if this is applied where it does not belong.
```

All five sections are required, in that order.

## What makes a good one

The `Do` section is the easy half. The value is usually in `Avoid` and `Check`: an agent that knows
the tempting wrong move, and how to tell whether it actually succeeded, behaves very differently from
one that only knows the happy path.

Write from something you have actually seen fail. A skill card distilled from one real incident beats
five written from general principle.

## `provenance`, and the honest risk

Skills contributed here carry `provenance: clean`, meaning a human reviewed them. Skills the agent
imports from elsewhere at runtime are marked `tainted` and land in `status: pending` until approved.

The distinction matters more than it looks. A skill card does not execute — but it is *instruction an
agent will follow*, which is its own kind of power, and a persuasive card that gives bad advice is a
real attack surface. That is why this directory is owned in
[CODEOWNERS](../.github/CODEOWNERS) and why `clean` is something a reviewer confers rather than
something a file claims about itself.

## Using them

The library is versioned here for review and reuse; the agent reads skills from its own store:

```bash
chimera skill import skills/verify-before-claiming
```
