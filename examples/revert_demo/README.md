# Verify-or-revert demo — a *failed* change caught and rolled back

A clean-project happy path proves nothing. The convincing demo is a **bad** change getting detected
and reverted, with a receipt. That is what this shows, driving the real primitives underneath
`chimera solve` / `chimera project`:

- **`WorkspaceGuard.snapshot()` / `restore()`** (`chimera.core.checkpoint`) — the checkpoint.
- **`CommandVerifier.verify()`** (`chimera.core.verify`) — the acceptance gate (a command; exit 0 = pass).

No model, no network — fully deterministic. It copies `workspace/` to a temp dir (the shipped files
stay pristine), snapshots it, injects a regression into `calc.py` (`a + b` → `a - b`), runs the
workspace's own test (which fails), reverts to the snapshot, and re-checks (which passes).

## Run it

```console
$ python examples/revert_demo/demo.py

  Chimera verify-or-revert receipt
  +--------------------------------------------------------------------
  | 1. baseline check       python -m unittest  ->  PASS
  | 2. change applied       calc.py:  `a + b`  ->  `a - b`   (injected regression)
  | 3. verify the change    python -m unittest  ->  FAIL
  | 4. decision             verification FAILED  ->  REVERT
  | 5. restore checkpoint   1 file(s) rolled back to last-known-good
  | 6. re-verify            python -m unittest  ->  PASS
  +--------------------------------------------------------------------
  | result: change REJECTED, workspace restored
  +--------------------------------------------------------------------
```

Exit code is `0` only when the change was rejected **and** the workspace ended in the good state — so
this doubles as a check: if verify-or-revert ever failed to protect the tree, the demo would exit `1`.

## Why it matters

This is the boundary a self-evolving / autonomous agent needs: a change is kept only if an
**executable** check accepts it, and any change that fails is rolled back to the last-known-good
state. The same snapshot→verify→revert loop runs inside `chimera solve` (per attempt) and
`chimera project` (per card, with the spec's drift-check as the acceptance authority).

## Files

- `workspace/calc.py` — the code under test (last-known-good: `add` returns `a + b`).
- `workspace/test_calc.py` — the acceptance check (stdlib `unittest`, no extra deps).
- `demo.py` — snapshots, injects the regression, verifies, reverts, prints the receipt.
