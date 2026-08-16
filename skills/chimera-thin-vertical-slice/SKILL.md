---
name: chimera-thin-vertical-slice
description: Build one narrow path that crosses every layer and runs, before building any layer in full — an integration that has never executed is a guess.
version: 0.1.0
kind: pattern
stage: define
topic: software-dev
triggers:
- starting a new feature
- this needs a CLI, a service and storage
- building the abstraction first
- nothing runs yet
- scaffolding a subsystem
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

You are starting work that spans layers — a command that reaches an orchestrator that calls a
provider that writes to a store — and you are deciding what to build first. The temptation is to
build the bottom layer completely, then the next, then wire it up at the end.

It does not apply to work that is genuinely one layer deep: a parser, a formatter, a pure function.
There is no vertical there, and inventing one is ceremony. It also does not apply to a change inside
a system that already runs end to end; that path exists, and the slice has already been paid for.

## Do

1. Name the observable the slice will produce, as a command someone else could type and a single
   concrete output. Not "the pipeline works" — `chimera run "hi"` prints one line that came back
   from a real provider.
2. Write that invocation first, as a script or a test, and watch it fail for the right reason.
3. Implement the shortest path through *every* layer, degenerate at each one: one provider, no
   retry, no config file, no cache, no concurrency. Where a policy will eventually live, put a
   constant.
4. Keep the layer that carries the actual unknown real. If you do not yet know what the provider
   returns, the provider is the one thing in slice one that must not be a stub.
5. Commit at the moment it runs, before generalising. That commit is the evidence that the layers
   fit together.
6. Widen one axis at a time — second provider, then configuration, then caching — and keep the
   command from step 1 working after each. If it breaks, that widening is the suspect.

## Avoid

Designing an interface against zero callers. The horizontal order produces a base class, six
adapters, a registry and a retry policy on day one, and discovers at wiring time that the shape is
wrong — because nothing had yet asked it a real question.

```python
# Day 1, horizontal: none of this has ever run.
class BaseProvider(ABC): ...
class ProviderRegistry: ...
class RetryPolicy: ...

# Day 1, vertical: ugly, hardcoded, and it answers the question.
def complete(prompt: str) -> str:
    return _post(URL, {"model": MODEL, "prompt": prompt})["choices"][0]["text"]
```

Also avoid the slice that is thin in the wrong dimension: mocking the external call and calling the
rest end to end. That proves your own layers agree with each other, which was never the doubtful
part. The integration you did not exercise is the integration that will be wrong.

## Check

Ask a binary question: can someone who did not write it, on a clean checkout, run one command and
watch the feature do its job all the way through? If the answer needs "well, once you also stub
X" or "after you paste a token into that constant" — note the second one honestly as setup, and
treat any *code* caveat as proof it is not yet a slice.

Then check `git log`: is there a commit where the command worked and the abstractions did not exist
yet? If the first working commit is also the commit that introduced the registry, the slice was
skipped.

## Risk

A slice hardcodes choices, and hardcodes survive. The usual failure is that steps 5 and 6 never
happen because the first slice "works", leaving a system whose single provider is welded in and
whose seams ended up in whatever place was convenient at hour two. Schedule the widening as work in
the same batch, not as a follow-up nobody funds.

The other cost is real: driving slice one through a live external dependency makes that first test
slow, paid for, and occasionally flaky. Record and replay it *after* it exists — recording a call
you have never made is not possible, which is the whole reason the order matters.
