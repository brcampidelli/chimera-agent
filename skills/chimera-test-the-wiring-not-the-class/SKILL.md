---
name: chimera-test-the-wiring-not-the-class
description: A class assembled by hand in a test proves the class works, not that anything reaches it — cover the path production actually takes.
version: 0.1.0
kind: pattern
stage: verify
topic: software-dev
triggers:
- feature works in tests but not in the app
- added a component behind a factory or registry
- the flag defaults to off
- green suite, broken behaviour
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

You built a component that production reaches indirectly: through a factory, a registry, a plugin
loader, a config flag, a router, a CLI entrypoint, a dependency-injection container. Your tests
construct it directly and call its methods.

Every one of those tests can pass while the component is unreachable in the running system —
because nothing in them exercises the registration, the default value of the flag, or the branch
in the assembler that decides whether to include it at all.

It does **not** apply to a pure function that callers import and call directly. There, the import
*is* the wiring, and a unit test covers it. It also does not apply when you are deliberately
testing an algorithm in isolation — those tests are correct and should stay; this card says they
are not sufficient on their own.

## Do

1. Name the entrypoint a user actually hits: the CLI subcommand, the HTTP route, the agent's run
   loop, the scheduled job. Write it down before writing the test.
2. Write at least one test that starts *there* and passes no constructor arguments for your
   component. If the test has to name your class to make the feature happen, it is not testing the
   wiring.
3. Build the object the way production builds it — call the real factory or config loader:

   ```python
   # WRONG — proves the class, not the wiring: the component is handed to the thing under test,
   # so the test passes whether or not anything in production ever hands it over.
   assert "reminder" in render(feature=Feature(text="reminder"))

   # RIGHT — build it the way the entry point builds it, then look for the same observable
   assert "reminder" in build_the_real_way(config).render()
   ```

   Chimera's own case is worth naming because the class was never broken. Skill cards had a
   working retriever, a working store and a working injector — and `chimera/config.py:244` reads
   `skill_cards: bool = Field(default=False, ...)`, so on a stock deployment nothing was ever
   injected. Every unit test passed. The measurement that eventually caught it counted skills
   minted against skills injected and found 39 against zero.

4. Assert on an observable that can only appear if the component was reached: text in the rendered
   prompt, a row written, a log line, an exit code.
5. Assert the **default**. If the feature ships behind a flag, add a separate test that reads the
   default value with no overrides and asserts what it is. A suite that only ever runs with the
   flag forced on cannot tell you what users get.

## Avoid

Hand-assembling the collaborators that production wires up. The failure shape is a handler class
with full unit coverage that the router never registers, or a capability whose config key defaults
to off — the class is correct, the tests are correct, and the feature does nothing in the product.
Nothing is red, so nothing gets investigated, and the gap survives until a human tries the feature
by hand.

Also avoid faking the seam you are trying to cover. Patching the factory, or stubbing the config
loader to return an object containing your component, deletes exactly the code the test existed to
exercise:

```python
# WRONG — the patch is the wiring; the test now asserts its own setup
monkeypatch.setattr(mod, "load_plugins", lambda: [MyPlugin()])
```

Fake at the *outermost* boundary instead — the network client, the clock, the LLM call — and let
everything between the entrypoint and your component be real.

## Check

Delete the wiring and run the suite. Comment out the registration line: the `@register` decorator,
the entry in the dispatch dict, the `include_router(...)` call, the default in the config schema.

Then the binary question: **did a test go red, and was it a test that never mentions your class by
name?**

If the suite stays green, your coverage is class-level only and the wiring is untested. If the only
red test is the one that constructs the class directly, same answer. Restore the line afterwards
and confirm green — and `git diff` before committing, so the deleted registration does not ship.

## Risk

Entrypoint tests are slower, harder to debug, and give worse localisation: when one fails you know
the feature is broken but not which of ten components broke it. That is a real cost, and the wrong
response to this card is deleting your unit tests in favour of end-to-end ones. Keep both — the
wiring test tells you *that* it broke, the unit test tells you *what*.

Going through the real entrypoint can also touch things you do not want touched in CI: a paid API,
a live database, a filesystem outside the sandbox. If the only way to reach the entrypoint is to
spend money or mutate production, do not force it; cover the assembler function directly instead
and accept that you are one step short of the true path.

And this card catches reachability, not correctness. A feature can be perfectly wired to the
entrypoint and still produce the wrong answer, so a green wiring test is not licence to skip
asserting what the output actually says.
