"""The Rust audit in CI blocks; Python blocks fixable CVEs; npm stays advisory.

Decided by the owner on 2026-09-30. All three audits in the `supply-chain` job were advisory
(`continue-on-error`), so a finding left the job green. On 2026-09-17 the Rust one printed
RUSTSEC-2026-0285 (rustls, in the desktop updater's TLS path, fixed in 0.23.45) and exited 1, and
nobody saw it for two weeks. cargo audit fails only on vulnerabilities, not on its unmaintained or
unsound notices, so making it block costs no permanent red.

Revised on 2026-11-14 (audit plan P0-06): the Python audit now BLOCKS, but only on advisories
that have a published fix, via `scripts/ci_pip_audit.py`. The owner's reason for keeping it advisory
was that a CVE with no fix would make red the normal state; that class is still reported and still
does not fail the job, so the reason is kept and only the fixable class, which has a version bump as
its answer, now turns the build red. The old assertion here pinned the mechanism (advisory) rather
than that reason.

Pinned here because the difference is one line of YAML, invisible in a diff that "tidies" the three
steps into the same shape.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CI = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _audit_steps() -> dict[str, dict[str, Any]]:
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["supply-chain"]["steps"]
    return {str(s.get("name", "")): s for s in steps if str(s.get("name", "")).startswith("Known vulnerabilities")}


def test_the_rust_audit_blocks_and_its_scanner_is_pinned() -> None:
    [rust] = [s for name, s in _audit_steps().items() if "Rust" in name]

    assert not rust.get("continue-on-error"), "the Rust audit went back to advisory"
    assert "cargo install cargo-audit --locked --version " in rust["run"], "the scanner is not pinned"
    assert "cargo audit --file apps/desktop/src-tauri/Cargo.lock" in rust["run"]


def test_the_python_audit_blocks_only_through_the_fixable_filter() -> None:
    [python] = [s for name, s in _audit_steps().items() if "Python" in name]

    assert not python.get("continue-on-error"), "the Python audit went back to advisory"
    assert "|| true" not in python["run"], "the Python audit can no longer fail"
    # Blocking on EVERY advisory is the red-by-default state the owner refused; the filter is what
    # keeps the no-fix class consultative.
    assert "python scripts/ci_pip_audit.py" in python["run"]
    assert "--all-extras" in python["run"], "the audit no longer covers the extras users install"


def test_the_npm_audit_stays_advisory() -> None:
    [npm] = [s for name, s in _audit_steps().items() if "npm" in name]

    assert npm.get("continue-on-error") is True
