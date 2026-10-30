---
name: do-not-widen-types-to-silence-the-checker
description: When a type checker rejects a call or return value, narrow the value or handle the missing case instead of widening the annotation to Any, object or an unchecked union.
version: 0.1.0
kind: pattern
stage: build
topic: software-dev
triggers:
- type checker error on an argument or return value
- tempted to add Any or cast to make mypy or pyright pass
- union includes None and caller expects a concrete type
- widening a signature just to satisfy one call site
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

A static type checker (`mypy`, `pyright`, `tsc`, `cargo check`) reports an incompatible type — for
example passing `str | None` where `str` is required, or returning a broader union than the function
signature declares — and the fastest way to make the diagnostic disappear is to widen the parameter
or return annotation (`Any`, `object`, `unknown`, `# type: ignore`, or adding `| None` to an
interface that never meant to accept it).

## Do

1. Read what the checker is actually proving: which branch can produce the unexpected type, and
   whether that branch is reachable at runtime.
2. If the broader value is a bug or an unhandled edge case at the call site, narrow it before the
   call — guard on `None`, validate the input at the boundary, or raise a clear error early.
3. If the function genuinely supports the broader type, update its implementation to handle every
   member of the wider type before widening its signature.
4. Keep the boundary narrow: parse or refine untyped data (`dict[str, Any]`, raw JSON) once where it
   enters the module rather than propagating wide types inward.

## Avoid

Treating the type checker as an obstacle to appease rather than a proof that a branch is unhandled.

The classic failure mode is changing `def load_user(user_id: str) -> User` to accept `str | None` or
return `User | Any` because one caller forgot to check whether `user_id` was present. The checker goes
green immediately, the unhandled `None` moves from a static error at the boundary to a runtime
`AttributeError` three frames deeper, and every downstream caller now either inherits the wider union
or loses checking altogether.

Equally bad is slapping `cast(...)` or `# type: ignore` on a line without an invariant in the
immediately preceding code that makes the cast true.

## Check

Run the type checker on the touched module and inspect the diff of every changed signature:

- Any newly widened parameter or return type has an explicit branch or test covering the added case.
- No `Any`, `cast`, or `# type: ignore` was introduced solely to silence a mismatch that could be
  resolved by narrowing at the boundary.
- Call sites downstream did not have to add defensive checks for a type the producer never needed to
  emit.

## Risk

Sometimes a library stub is genuinely wrong or incomplete, or an external payload truly is arbitrary
until a later stage inspects it. In those cases a localized, documented `# type: ignore[code]` or a
narrow boundary adapter is better than contorting runtime code with dead branches just to satisfy a
broken stub.
