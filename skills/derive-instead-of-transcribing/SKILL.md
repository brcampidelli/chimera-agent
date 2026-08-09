---
name: derive-instead-of-transcribing
description: Anything copied by hand from another file is correct exactly once. If it can be generated, generate it and fail the build when the copy is stale.
version: 0.1.0
kind: pattern
triggers:
- documenting a command list
- copying values between projects
- keeping two files in sync
- writing a reference page
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

Two places in your system hold the same information: a config and its documentation, a schema and
its client types, a palette and the site that uses it, a command list and a reference page.

It does not apply when the second copy is deliberately different — a curated getting-started guide
is *not* a stale copy of the reference, it is prose with a different job.

## Do

1. Pick which one is the source. Usually the one the program actually reads at runtime.
2. Generate the other, committing the generated file so diffs are reviewable.
3. Add a CI step that regenerates and fails if the committed copy differs, with the exact command
   to run in the error message.
4. Stamp the generated file with the version or commit it was generated from, so a reader knows
   what it describes.

## Avoid

Keeping the copy in sync by remembering. That works while one person holds both files in their
head, and stops the first week somebody else touches one of them.

Avoid also the halfway version: generating the file but not gating it. An artefact that is
*sometimes* regenerated is worse than a hand-written one, because it carries the authority of
being generated while being just as stale.

And avoid generating prose. Reference material generates well; explanation does not, and a page of
mechanically-expanded field descriptions is a page nobody reads.

## Check

Change the source, do not regenerate, and push. CI must go red and tell you what to run.

Then read the generated file for the thing you changed and confirm it is there — the gate proves
the file is current, not that the generator captured the field you care about.

## Risk

A generated artefact that nobody can read defeats the purpose; if the output is only meaningful to
a machine, it needs a rendering layer, which is more code to maintain.

There is also a coupling cost. The consumer now depends on the producer's shape, and a refactor on
one side breaks a build on the other. That is usually the point — but it means the gate has to be
easy to satisfy, or someone will disable it during an unrelated refactor and forget.
