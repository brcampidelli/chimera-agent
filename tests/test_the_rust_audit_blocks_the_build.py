"""The Rust audit in CI blocks; the Python and npm audits stay advisory.

Decided by the owner on 2026-09-30. All three audits in the `supply-chain` job were advisory
(`continue-on-error`), so a finding left the job green. On 2026-09-17 the Rust one printed
RUSTSEC-2026-0285 (rustls, in the desktop updater's TLS path, fixed in 0.23.45) and exited 1, and
nobody saw it for two weeks. cargo audit fails only on vulnerabilities, not on its unmaintained or
unsound notices, so making it block costs no permanent red.

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


def test_the_python_and_npm_audits_stay_advisory() -> None:
    others = [s for name, s in _audit_steps().items() if "Rust" not in name]

    assert len(others) == 2
    assert all(s.get("continue-on-error") is True for s in others)
