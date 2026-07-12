"""Verify-or-revert, demonstrated: a failed change is DETECTED and ROLLED BACK, with a receipt.

This is the honest artifact — not a happy-path run on a clean project, but a *bad* change getting
caught and reverted. It drives the REAL primitives that sit under ``chimera solve`` / ``chimera
project``:

  - ``WorkspaceGuard.snapshot()`` / ``restore()``  (chimera.core.checkpoint) — the checkpoint.
  - ``CommandVerifier.verify()``                    (chimera.core.verify)     — the acceptance gate.

No model, no network — fully deterministic. It copies the sample ``workspace/`` to a temp dir so the
shipped files stay pristine, snapshots it, injects a regression into ``calc.py``, runs the check
(which fails), reverts to the snapshot, and re-checks (which passes) — printing a receipt at each step.

Run it:  ``python examples/revert_demo/demo.py``
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from chimera.core.checkpoint import WorkspaceGuard
from chimera.core.verify import CommandVerifier

# The acceptance authority: the workspace's own test must pass (stdlib unittest, exit 0 = pass).
# ``-B`` disables the bytecode cache so a stale ``.pyc`` from the injected version can't be trusted
# after the revert (Windows' coarse mtime can make the restored source and the bad .pyc collide).
_VERIFY_COMMAND = "python -B -m unittest -q"


@dataclass
class Receipt:
    """What happened, as a machine-checkable record (also what gets printed)."""

    before_passed: bool
    after_change_passed: bool
    reverted_files: int
    after_revert_passed: bool

    @property
    def change_rejected(self) -> bool:
        """The whole point: the bad change did not survive, and the workspace is good again."""
        return not self.after_change_passed and self.reverted_files > 0 and self.after_revert_passed


def run_demo(*, verbose: bool = True) -> Receipt:
    """Run the inject → detect → revert cycle in an isolated temp copy; return a Receipt."""
    src = Path(__file__).parent / "workspace"
    tmp = Path(tempfile.mkdtemp(prefix="chimera_revert_demo_"))
    try:
        ws = tmp / "workspace"
        shutil.copytree(src, ws)
        guard = WorkspaceGuard(ws)
        verifier = CommandVerifier(_VERIFY_COMMAND, ws)

        before = verifier.verify()
        snapshot = guard.snapshot()  # checkpoint the last-known-good state

        # An agent proposes a change — here, an injected regression: `a + b` becomes `a - b`.
        calc = ws / "calc.py"
        calc.write_text(calc.read_text(encoding="utf-8").replace("a + b", "a - b"), encoding="utf-8")

        after_change = verifier.verify()  # DETECT: the acceptance check now fails
        reverted = guard.restore(snapshot) if not after_change.passed else 0  # REVERT on failure
        after_revert = verifier.verify()  # confirm the good state is back

        receipt = Receipt(
            before_passed=before.passed,
            after_change_passed=after_change.passed,
            reverted_files=reverted,
            after_revert_passed=after_revert.passed,
        )
        if verbose:
            _print_receipt(receipt)
        return receipt
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _mark(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def _print_receipt(r: Receipt) -> None:
    # ASCII-only so the receipt prints identically on every console (incl. Windows cp1252).
    bar = "  +--------------------------------------------------------------------"
    lines = [
        "",
        "  Chimera verify-or-revert receipt",
        bar,
        f"  | 1. baseline check       python -m unittest  ->  {_mark(r.before_passed)}",
        "  | 2. change applied       calc.py:  `a + b`  ->  `a - b`   (injected regression)",
        f"  | 3. verify the change    python -m unittest  ->  {_mark(r.after_change_passed)}",
        "  | 4. decision             verification FAILED  ->  REVERT",
        f"  | 5. restore checkpoint   {r.reverted_files} file(s) rolled back to last-known-good",
        f"  | 6. re-verify            python -m unittest  ->  {_mark(r.after_revert_passed)}",
        bar,
        f"  | result: {'change REJECTED, workspace restored' if r.change_rejected else 'UNEXPECTED - see steps above'}",
        bar,
        "",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    result = run_demo()
    raise SystemExit(0 if result.change_rejected else 1)
