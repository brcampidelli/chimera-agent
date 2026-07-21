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

## Dev setup

```bash
make install            # = uv sync --extra dev
uv run chimera doctor
```

## Quality gate (run before every PR)

```bash
make check              # lint + type + test, the whole gate in one command
```

Prefer the raw commands (or on native Windows, where `make` may be absent)? They are:

```bash
uv run ruff check .
uv run mypy chimera
uv run pytest
```

`make help` lists the other targets (`fmt`, `cov`, `docs`, `clean`).

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
