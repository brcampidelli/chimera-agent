---
name: assert-what-the-generator-found
description: A generator that crashes tells you it failed; one that emits plausible output tells you nothing. Test what it found, never that it ran.
version: 0.1.0
kind: pattern
triggers:
- wrote a code generator
- testing a schema dump
- the output looks fine
- extracting structure from a library
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

You have written something that reads one representation and emits another: a schema dump, a
reference extractor, a migration, a scraper, an index builder. Anything whose output nobody reads
in full because there is too much of it.

It does not apply to a function returning one value you can eyeball. The risk here is specific to
output that is large enough that "it looks right" is the only review it will ever get.

## Do

1. Before writing the test, name the count. How many commands, rows, files, or fields *should* come
   out? Get that number from somewhere other than the generator — the source, the docs, a manual
   count.
2. Assert the count, or a floor on it. `assert len(groups) >= 10`, not `assert result`.
3. Assert the presence of specific, named items you know must exist. Three or four is enough, and
   pick ones from different shapes of input.
4. If the generator classifies things, assert that each class is non-empty. A classifier that puts
   everything in one bucket is the failure this catches.

## Avoid

`assert build()` — it passes when the generator returns an empty list, a partial list, or a list
where every item is subtly the wrong kind.

Also avoid trusting `isinstance` when walking a third-party structure. Libraries vendor their
dependencies: a `TyperGroup` is not an instance of the `click.Group` your file imported, because
Typer ships its own copy of Click. Ask whether the object *has* the thing you need — a `commands`
mapping, an `items` method — rather than what class it claims to be. Duck-typing survives a
vendored dependency and a major version bump; a class check survives neither, and fails by
misclassifying rather than by raising.

## Check

Break the generator on purpose and watch the test fail. Comment out the recursion into
subcommands, or make the type check reject everything, and run the suite.

If the suite still passes, the test asserts that the generator ran, and you have written the test
that this skill exists to prevent.

## Risk

Hard-coding an exact count makes the test a maintenance chore: every legitimately added command
turns it red. Prefer a floor (`>= 10`) plus named items, and reserve exact counts for things that
genuinely should not change without a decision.

There is also a limit. These assertions catch a generator that dropped a whole category. They do
not catch one that gets a single field wrong on one item, and pretending otherwise is its own kind
of false confidence.
