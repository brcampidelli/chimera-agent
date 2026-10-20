"""Census reads persisted Code receipts only and never emits transcript content."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.verify_census import census


def _write_session(root: Path, name: str, receipts: list[dict[str, object]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.json").write_text(
        json.dumps({
            "session_id": name,
            "workspace": "synthetic-workspace",
            "messages": [{"role": "user", "content": "PRIVATE MESSAGE MUST NOT PRINT"}],
            "receipts": receipts,
        }),
        encoding="utf-8",
    )


def test_counts_real_receipt_shape_and_normalizes_abstentions(tmp_path: Path) -> None:
    _write_session(tmp_path / "code_sessions", "a", [
        {"verified": {"command": "pytest", "source": "inferred:pyproject.toml",
                       "state": "abstained", "output": "verify command not run: inferred"}},
        {"verified": {"command": "npm test", "source": "inferred:package.json",
                       "state": "abstained", "output": "host-exec gate declined to run it"}},
        {"verified": {"command": "pytest", "source": "user", "state": "abstained",
                       "output": "network requested, OS sandbox cannot grant it"}},
        {"verified": {"command": None, "source": "none", "state": "none"}},
        {"verified": {"command": "pytest", "source": "user", "state": "passed", "output": ""}},
        {"verified": {"command": "pytest", "source": "user", "state": "failed", "output": "failed"}},
        {"other": "legacy receipt without verifier"},
    ])
    report = census(tmp_path / "code_sessions")
    assert report["turns"] == 7
    assert report["states"] == {
        "passed": 1, "failed": 1, "abstained": 3, "none": 1, "missing": 1, "unknown": 0,
    }
    assert report["abstention_reasons"] == {
        "inferred": 1, "no sandbox": 1, "host-exec declined": 1, "no command": 1, "other": 0,
    }


def test_cli_reports_counts_but_never_transcript_or_command(tmp_path: Path) -> None:
    root = tmp_path / "data" / "code_sessions"
    _write_session(root, "private", [
        {"messages": "DO NOT LEAK", "verified": {
            "command": "private-command", "source": "inferred:Makefile",
            "state": "abstained", "output": "verify command not run: host-exec declined",
        }},
    ])
    result = subprocess.run(
        [sys.executable, "scripts/verify_census.py", str(tmp_path / "data")],
        check=True, capture_output=True, text=True,
    )
    assert "Code turns (receipts): 1" in result.stdout
    assert "host-exec declined" in result.stdout
    assert "DO NOT LEAK" not in result.stdout
    assert "private-command" not in result.stdout
    assert "verify command not run" not in result.stdout
    assert '"abstained": 1' in result.stdout


def test_invalid_files_are_counted_as_unreadable_without_content(tmp_path: Path) -> None:
    root = tmp_path / "code_sessions"
    root.mkdir()
    (root / "bad.json").write_text("not json PRIVATE", encoding="utf-8")
    (root / "valid.json").write_text(json.dumps({"receipts": []}), encoding="utf-8")
    report = census(root)
    assert report["turns"] == 0
    assert report["unreadable_session_files"] == 1


def test_default_path_obeys_app_legacy_home_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Exercise path selection without reading the user's actual home or settings.
    from scripts import verify_census

    legacy = tmp_path / ".chimera"
    legacy.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CHIMERA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "user"))
    assert verify_census.default_sessions_dir() == Path(".chimera/code_sessions")
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "configured"))
    assert verify_census.default_sessions_dir() == tmp_path / "configured/code_sessions"
    monkeypatch.delenv("CHIMERA_HOME")
    legacy.rmdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "user"))
    assert verify_census.default_sessions_dir() == tmp_path / "user/.chimera/code_sessions"
