---
name: chimera-list-exemptions-not-obligations
description: A gate that lists what to check fails open — the site nobody remembered to add is the one that breaks. List the exemptions instead, each with a written reason, and the default becomes fail.
version: 0.1.0
kind: pattern
stage: ship
topic: devops
triggers:
- writing a build gate
- enforcing a rule across the whole package
- a hand-maintained list of files to check
- a surface lost its guarantee
- adding a new entry point
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

You are enforcing a property that has to hold at every site of a certain kind: every unattended
surface goes through the governance profile, every writer passes the gate, every API route
regenerates its schema. The rule is one sentence, but the sites are written at different times by
different hands, and none of them calls the thing the others call.

It does **not** apply when the population is closed by construction — a rule about the three entries
of a dispatch table is already enumerated by the table. This pattern earns its cost only when a new
site can appear in a file that does not exist yet.

## Do

1. Enumerate the population mechanically. Walk every `*.py` under the package
   (`PACKAGE.rglob("*.py")`), not a hand-written list of the files you happen to know about.
2. Detect the site structurally. Parse and walk the AST for the call you care about; a `grep` cannot
   tell an assembled registry from the same words inside a docstring, and that docstring *will* exist,
   because someone will write a comment about the fix.
3. Walk nested scopes and key by the enclosing chain — `chimera/cli/main.py:solve._run_solve`. The
   one surface Chimera lost had its registry built inside a `factory()` closure
   (`chimera/server/manager.py:_gateway_on_message.factory`), invisible to a walk that only looked
   at top-level functions.
4. Make the default FAIL. Keep one `EXEMPT: dict[str, str]` from site key to written reason, and have
   the assertion list every site found that is not a key in it.
5. Write the reason as a sentence about that specific site, and separate the kinds of reason.
   "attended: interactive REPL" (a person can hit Ctrl-C) is a much stronger claim than "assembles an
   equivalent stack from its own flags" — the second marks a duplicate implementation to consolidate,
   not a settled answer.
6. Add a second test asserting every `EXEMPT` key still names a live site, so the list cannot outlive
   the code it excuses and rot into a formality.

## Avoid

The obligations list:

```python
SURFACES = ["serve", "cron", "mcp", "a2a", "platform"]   # fails OPEN
for name in SURFACES:
    assert_governed(name)
```

versus the exemptions list:

```python
EXEMPT = {"chimera/cli/main.py:chat": "attended: interactive REPL"}   # fails CLOSED
assert not [s for s in every_site_in_the_package() if s not in EXEMPT]
```

The first version of Chimera's governance gate was the top one: it parsed a single file and named
five surfaces. `chimera/server/manager.py` — the Discord bot the desktop app's Messaging toggle
starts, the same bot `serve --discord` runs — built its registry ungoverned for three weeks *after*
the sweep that was supposed to have covered it. It was never failing the gate. It was never looked
at.

Also avoid an exemption with no reason. A bare `EXEMPT: set[str]` is a list of checks to skip that
nobody can review a year later; the one sentence is the whole cost of the entry and the whole value
of it.

## Check

Feed the gate a source that violates the rule and assert it is caught, including from inside a
nested function — otherwise an AST bug makes the gate pass forever and the guarantee quietly stops
existing:

```python
src = "def serve():\n    def factory():\n        r = default_registry(ws)\n"
assert _bare_registry_calls(ast.parse(src), "m.py") == [("m.py:serve.factory", 3)]
```

Then the other half: a call already wrapped in the compliant shape must **not** be flagged, or every
conforming site would need an exemption and the list would stop meaning anything.

The binary question: delete one entry from `EXEMPT` and re-run. Does the suite go red naming exactly
that site? If not, the gate is not reading the list you think it is.

## Risk

Inverting the list moves the cost onto whoever adds a legitimate new site: an unrelated PR goes red
with an error about governance they were not thinking about. That is the intended trade, but only if
the failure message says both ways to comply — route it through the profile, *or* add the exemption
with a reason. A gate that fires on work it was not built to defend gets argued down until it fires
on nothing.

The structural detection is also narrower than it feels. It catches the one shape it knows; a surface
that assembles tools through a different call, or imports a pre-built registry from elsewhere, is
outside the walk and the green build says nothing about it. Pin the coverage of the file that
regressed by name in its own test, so a walk that silently stops covering that file fails instead of
passing.
