# Contributing to Chimera

Thanks for your interest! Chimera is in early alpha — the architecture is settling, so issues and
design discussion are especially welcome.

## Dev setup

```bash
uv sync --extra dev
uv run chimera doctor
```

## Quality gate (run before every PR)

```bash
uv run ruff check .
uv run mypy chimera
uv run pytest
```

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
