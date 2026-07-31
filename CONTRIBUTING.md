# Contributing to Chimera

Thanks for your interest! Chimera is in early alpha — the architecture is settling, so issues and
design discussion are especially welcome.

**New here?** Two maps of the codebase:
- **[Architecture](docs/architecture.md)** — where each subsystem lives and the research it builds on.
- **[Extending guide](docs/extending.md)** — how to add your own **tool, skill, or recipe** with
  complete, copy-paste examples — the fastest way to make your first contribution.

**Want more than a one-off PR?** Chimera has exactly one maintainer, which is its largest risk.
**[GOVERNANCE.md](GOVERNANCE.md)** says so plainly and describes how decisions get made, how to
become a maintainer (there is no application process — land a few changes and ask), which areas most
need a second pair of hands, and what happens to the project if the maintainer disappears.

## Good first issues — where to start

Issues labelled [`good first issue`](https://github.com/brcampidelli/chimera-agent/labels/good%20first%20issue)
are self-contained, have a clear finish line, and touch code with an existing pattern to copy. If the
label list is empty, these areas are reliably newcomer-friendly and each is a real, wanted contribution
— open an issue proposing one and it'll be labelled:

| Area | What | Pattern to copy |
|---|---|---|
| **A new reference tool** | Add a small, credential-free tool (e.g. a units/temperature converter), register it in `default_registry` | `EchoTool` / `HttpGetTool` in [`chimera/tools/`](chimera/tools/), `docs/extending.md` |
| **Grow a tool's tests** | Pick a tool in `chimera/tools/` with thin coverage and add edge-case tests | any `tests/test_*.py` — small, isolated, `tmp_path`-based |
| **A worked recipe** | Add an end-to-end example under `examples/` (e.g. "summarise an RSS feed to a file") | the existing dirs in [`examples/`](examples/) + `docs/recipes.md` |
| **A local-model quickstart** | Document running Chimera against a local **Ollama** model via the existing LiteLLM routing | `docs/recipes.md`, `chimera/providers/gateway.py` model resolution |
| **Friendlier errors / `--help`** | Improve a confusing CLI message or command help string | `chimera/cli/main.py` |
| **Extend the mutation gate** | Add a 6th critical module to `[tool.mutmut]` and kill/allowlist its survivors | [`MUTATION.md`](MUTATION.md) + `scripts/mutation_gate.py` |

Every one ships with the same quality gate below (lint + type + a test). Ask in the issue if you're
unsure about scope — narrowing it down *is* part of the help.

## What's open, and what isn't

Most projects only publish the invitation. That leaves you to discover the boundary by having a pull
request go quiet, which is a bad way to find out and a worse way to spend a weekend. So here is the
map. `.github/CODEOWNERS` says the same thing where GitHub will show it to you — on a pull request's
Files tab, before you write the code.

**Open — send the pull request.** No issue needed, no permission to ask for.

| Where | What |
|---|---|
| `skills/` | A skill card (`SKILL.md`). It is data, not code — reviewing it is reading a markdown file. |
| `examples/` | A worked recipe. CI loads and validates every example, so yours proves itself. |
| `docs/` | Anything clearer than what is there. |
| `README.*.md`, `apps/desktop/src/lib/i18n.tsx` | Translations, including finishing a language that is only partly done. |
| `chimera/providers/catalog.py` | Model slugs, prices, context sizes. It is a data table and it goes stale; correcting it is a real contribution. |
| `tests/` | More coverage for existing behaviour — added assertions, not changed ones. |
| `bench/` | **Replication.** Run an existing `PREREGISTRATION.md` and post what you got, even (especially) if it disagrees with ours. |

**Open, with an issue first.** Not a hurdle — a way to agree the shape before you spend the evening,
because these have constraints that are invisible until you hit them.

- A new tool. Its capability has to be classified in `chimera/governance/ledger.py`, or the taint and
  idempotency guards silently do not apply to it.
- A new CLI command, a desktop screen, a new provider.
- Anything that changes behaviour a user could already be relying on.

**Protected — talk to us before writing a patch.** These carry invariants that a reasonable-looking
change can quietly break:

- `chimera/governance/`, `chimera/sandbox/`, `chimera/tools/base.py`, `shell.py`, `code.py` — the
  security kernel and everything that executes on the host.
- `chimera/config.py` — credential resolution and the `trust_workspace` default.
- `chimera/api/` and its generated `openapi.json` / `api-schema.ts`.
- `bench/*/PREREGISTRATION.md` and `bench/*/RESULTS.md` — a number is registered *before* the run.
- `.github/workflows/`, `ratchet.json`, `i18n-pending.json` — the gates, and the counters that would
  otherwise be free to raise their own ceiling.

To be explicit, because this is the part that gets misread: **a bug report against any of these is
always welcome, and a proof-of-concept for a vulnerability is welcome anywhere.** What is closed is
the unsolicited *patch*, not the *finding*. Security issues go to
[SECURITY.md](SECURITY.md), privately, and get answered.

**How a pull request is handled.** Triage happens weekly. If something is out of scope you will get a
reason in writing and a label, not silence — a closed pull request with an explanation is a worse
outcome than a merge and a much better one than a queue you are still checking a month later.

**Contributions are licensed inbound under Apache-2.0**, per section 5 of the licence. There is no CLA
to sign.

**Wrote it with an AI?** Fine, and say so in the description — this project is an agent framework,
pretending otherwise would be strange. Two conditions: run the checks below yourself before pushing,
and be able to explain the diff in review. You are the author; the model was a tool.

## Dev setup

```bash
make install            # = uv sync --extra dev
uv run chimera doctor
```

## Quality gate (run before every PR)

```bash
make check              # lint + type + test, the whole gate in one command
```

Prefer the raw commands? They are:

```bash
uv run --extra dev --extra desktop ruff check .
uv run --extra dev --extra desktop mypy chimera
uv run --extra dev --extra desktop pytest -q      # the WHOLE suite, not the module you touched
```

On Windows, run these in WSL. The suite passes there and has surprised people on native Windows.

**Touched anything under `apps/desktop/`?** The Python gate does not cover it. Also run:

```bash
npm --prefix apps/desktop run test     # component tests + the design-system and i18n gates
npm --prefix apps/desktop run build    # tsc --noEmit && vite build
```

**Touched a route in `chimera/api/`?** Regenerate the types in the *same* commit, or CI fails on drift:

```bash
uv run python -m chimera.api.schema_dump > apps/desktop/openapi.json
npm --prefix apps/desktop run gen:api
```

`make help` lists the other targets (`fmt`, `cov`, `docs`, `clean`).

> **The rules that actually fail CI live in [AGENTS.md](AGENTS.md).** The name says agents, but it is
> the shortest description of this repository's hard constraints and it is worth five minutes: never
> rename an i18n key, never weaken a test to make it pass, numbers in `bench/` are pre-registered.
> Every one of those is a rule someone learned the expensive way.

- **Type-safe**: `mypy --strict` clean; avoid `Any`.
- **Small units**: functions ≤ 40 lines, files ≤ 300 lines where practical.
- **Tests**: new logic ships with tests; aim for ≥ 80% coverage on new code.
- **Imports**: absolute imports within the package (`from chimera.x import y`).

## Commit messages

Conventional Commits: `feat`, `fix`, `perf`, `refactor`, `test`, `docs`, `chore`, `security`.

## Architecture principles

1. **State lives outside the LLM context** (git + DB) — this is core to resisting evolution degradation.
2. **Self-modification is gated**: structured edit surface + static validator + verify-or-revert.
3. **Fusion is selective**: fuse when it pays (deep/hard/high-stakes), single model otherwise.
4. **Never hard-block a benign action** in the governance kernel.
