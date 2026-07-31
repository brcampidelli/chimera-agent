<!--
Thanks for this. Two things worth knowing before you fill it in:

  * Triage is weekly. If this needs to be narrowed or is out of scope you will get a reason in
    writing, not silence.
  * The rules that actually fail CI are in AGENTS.md — they are short, and the checklist below is
    the version of them that applies to this diff.
-->

## What & why

<!-- What changes, and what problem it solves. If there is an issue, the "why" can live there. -->

## Scope

<!-- Tick one. See "What's open, and what isn't" in CONTRIBUTING.md. -->

- [ ] **Open area** — `skills/`, `examples/`, `docs/`, translations, `providers/catalog.py`, added tests, a `bench/` replication
- [ ] **Agreed in an issue first** — a new tool, CLI command, desktop screen or provider → issue #
- [ ] **Touches a protected path** — governance, sandbox, `tools/base|shell|code.py`, `config.py`, `chimera/api/`, `bench/*/PREREGISTRATION.md`, workflows, ratchet → discussed in #

**Deliberately out of scope** (what you chose *not* to change, so review stays on what is here):

## How to test

<!-- The command a reviewer runs to see this working, not a description of it. -->

## Checks

- [ ] `uv run --extra dev --extra desktop pytest -q` — the **whole** suite, not the module I touched (on Windows: in WSL)
- [ ] `uv run --extra dev --extra desktop ruff check .` and `mypy chimera` are clean
- [ ] New logic ships with a test
- [ ] I did not weaken or delete an existing assertion to make something pass

Only if they apply:

- [ ] Touched `apps/desktop/` → `npm --prefix apps/desktop run test` **and** `run build` pass
- [ ] Touched a route in `chimera/api/` → regenerated `openapi.json` + `api-schema.ts` **in this commit**
- [ ] Touched `i18n.tsx` → added keys only; renamed or deleted none
- [ ] Added a UI string → either translated all nine languages, or listed the key in `i18n-pending.json`
- [ ] Touched `bench/` → the prediction was registered *before* the run, and nothing was loosened after
- [ ] Added a tool → its capability is classified in `chimera/governance/ledger.py`
- [ ] Docs/README updated if behaviour a user relies on changed

## Anything else

<!--
Optional. Useful here: a decision you were unsure about, a tradeoff you made, something you
want the review to look at hardest.

If an AI wrote part of this, just say so — it is fine, and this project would be a strange place to
mind. Only asks: you ran the checks yourself, and you can explain the diff.
-->

Closes #
