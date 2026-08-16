---
name: chimera-ground-it-in-the-source
description: Take the signature from the installed version, not from memory — a plausible API is indistinguishable from a real one until it runs.
version: 0.1.0
kind: pattern
stage: build
topic: software-dev
triggers:
- calling a library I did not write
- what is the parameter called
- the API changed between versions
- writing against a framework from memory
- AttributeError on a method that should exist
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

You are about to write a call into someone else's library, framework or CLI: a method name, a
keyword argument, a config key, the shape of a return value, a flag. This applies most sharply when
recall feels *confident*, because confidence is produced the same way whether or not the API exists.

It does not apply to the language's own builtins you exercise constantly, and it is not an
instruction to fetch documentation before writing `dict.get`. The line is whether being wrong would
be caught immediately by the next thing you run.

## Do

1. Get the version that is actually installed, not the one you remember reading about:
   `uv pip show <pkg>`, or `python -c "import pkg; print(pkg.__version__)"`.
2. Read the installed thing. `python -c "import inspect, pkg; print(inspect.signature(pkg.fn))"`,
   or open the file under `site-packages`, or run `--help` on the binary that is on PATH. This is
   the authority, because it is the code that will execute.
3. When you use a blog post, a README on the web, or your own recall, treat it as a hypothesis and
   confirm it against step 2. Docs sites describe the latest release; your lockfile may not pin it.
4. Exercise the call once in a throwaway snippet and print the *actual* return value before writing
   code that indexes into your assumption of it. Nested shapes — `["choices"][0]["message"]` — are
   where memory is least reliable and least likely to fail loudly.
5. Leave the evidence next to the code: the version, and the signature or help output you read.
   A comment or the PR body is enough, and it tells the next reader what the code was written
   against.

## Avoid

Writing API-shaped text. The failure is not a typo — a typo raises immediately and gets fixed in
seconds. It is a name that obeys every one of the library's naming conventions and does not exist:

A real one from this repository, and the shape is the point — the guess was not absurd, it was
*reasonable*:

```python
# Plausible: snapshots live in checkpoint.py, so the diff of two snapshots should too.
from chimera.core.checkpoint import WorkspaceGuard, diff_snapshots   # ImportError

# Where it actually is. One grep, before writing the line, would have found it.
from chimera.core.checkpoint import WorkspaceGuard
from chimera.evolution.diff_gate import diff_snapshots
```

The cost was not the exception — an `ImportError` is loud and cheap. It is that the same confident
guess about a *behaviour* fails silently, and you only find out when the number it produced turns
out to be wrong.

Also avoid grounding at the wrong version. Reading the current docs for a package pinned two majors
back produces code that is correct about a library you are not running — and the error message,
when it comes, points at your call rather than at the mismatch.

And avoid accepting a passing type checker as grounding when the package ships no stubs. Against an
untyped dependency, `Any` swallows every attribute you invent; the check reports success because it
had nothing to check.

## Check

For each non-obvious call in the diff, can you point at where you saw it — a signature you printed,
a line in `site-packages`, a `--help` you ran? If the honest answer for any of them is "it seemed
right", that call is unverified, and saying so costs one sentence.

The stronger check is executable: run the snippet from step 4 against the installed version and
paste its output. An import that resolves and a call that returns the shape you expected are two
different facts; the second is the one you are relying on.

## Risk

Applied to everything, this turns routine code into research and slows work down for no gain. Spend
it on the unfamiliar call, the version-sensitive one, and the nested return shape — not on the
hundred lines around them.

The subtler risk is grounding *too* literally. The installed source will happily show you
`_internal_helper`, which exists, works today, and is nobody's promise. Source tells you what is
there; docs tell you what is supported. When they disagree, prefer the documented surface, and if
you knowingly reach past it, say so in the comment rather than letting the next reader assume it
was sanctioned.
