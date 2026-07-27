# Working in this repository

A routing document. It is short on purpose — it tells you where the real rules live and states the
few that have bitten us hard enough to be worth repeating.

Chimera is a Python agent framework (`chimera/`) with a Tauri + React desktop app
(`apps/desktop/`). ~41k lines of source, ~25k lines of tests, 1848 tests, mypy strict.

---

## Where the rules live

| Working on | Read first |
|---|---|
| Desktop UI, styling, motion | **[`apps/desktop/DESIGN.md`](apps/desktop/DESIGN.md)** |
| Anything user-facing | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| Security-sensitive code | [`SECURITY.md`](SECURITY.md) |
| Benchmarks and claims | [`bench/*/RESULTS.md`](bench/) and the matching `PREREGISTRATION.md` |

---

## Hard rules

**Never rename or delete an i18n key.** `apps/desktop/src/lib/i18n.tsx` carries seven languages, and
the component tests assert on rendered English strings. A rename breaks them silently across all
seven. Adding keys is free; renaming is not.

**A route change and its generated types ship in the same commit.** If you touch a FastAPI route in
`chimera/api/`, regenerate both or CI fails on drift:

```bash
python -m chimera.api.schema_dump > apps/desktop/openapi.json
npm --prefix apps/desktop run gen:api
```

**Run the full Python suite, not the module you touched.** Per-module runs have let regressions
reach `main` here before. On Windows, run it in WSL.

**Never weaken a test to make it pass.** If a test asserts something false, say so and rewrite it
with the reasoning in the commit message — one in `test_agent.py` asserted that a run which called
no tool had done the work. Deleting the assertion and fixing the premise look identical in a diff
and are opposites in intent.

**Numbers in `bench/` are pre-registered.** Predictions and readings are committed *before* a run
and never loosened afterwards. A retraction is published with the same prominence as the original
claim. If a result kills a claim, that is the result.

---

## Verifying

```bash
# Python — the whole suite
uv run --extra dev --extra desktop pytest -q
uv run ruff check chimera tests
uv run mypy chimera

# Desktop
npm --prefix apps/desktop run test    # includes the design-system gate
npm --prefix apps/desktop run build   # tsc --noEmit && vite build
```

The desktop test step runs `src/design/design-system.test.ts`, which enforces
[`DESIGN.md`](apps/desktop/DESIGN.md) mechanically: no arbitrary font sizes, no colour literals, no
untokened motion, and every keyframe paired with a reduced-motion answer. Some rules are ratcheted
in `src/design/ratchet.json` — a count may fall, never rise. To add an exception you must lower
another number to pay for it.

---

## Conventions

- **Comments explain why, not what.** The code already says what.
- **Commit messages carry the reasoning**, especially for a fix — what the bug actually was and how
  it hid. Conventional Commits for the subject line.
- **Absolute imports** (`@/` in the desktop app).
- **No `any`**, no `@ts-ignore`, no `# type: ignore` without a stated reason.
