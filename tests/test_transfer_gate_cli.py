"""Tests for the `chimera transfer-gate` command (exposes transfer_gated_promotion)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from chimera.cli.main import app

runner = CliRunner()


def _write(path: Path, values: list[bool]) -> str:
    path.write_text(json.dumps(values), encoding="utf-8")
    return str(path)


def test_promote_when_tuned_helps_no_holdout(tmp_path: Path) -> None:
    tb = _write(tmp_path / "tb.json", [False, False, False, False])
    tt = _write(tmp_path / "tt.json", [True, True, True, False])
    result = runner.invoke(app, ["transfer-gate", tb, tt])
    assert result.exit_code == 0
    assert "PROMOTE" in result.stdout
    assert "NOT measured" in result.stdout  # no holdout supplied


def test_block_on_negative_transfer(tmp_path: Path) -> None:
    tb = _write(tmp_path / "tb.json", [False, False, False, False])
    tt = _write(tmp_path / "tt.json", [True, True, True, True])  # big tuned gain
    hb = _write(tmp_path / "hb.json", [True, True, True, True])
    ht = _write(tmp_path / "ht.json", [False, False, True, True])  # regressed on holdout
    result = runner.invoke(
        app, ["transfer-gate", tb, tt, "--holdout-baseline", hb, "--holdout-treatment", ht]
    )
    assert result.exit_code == 1
    assert "BLOCK" in result.stdout
    assert "NEGATIVE TRANSFER" in result.stdout


def test_promote_when_generalizes(tmp_path: Path) -> None:
    tb = _write(tmp_path / "tb.json", [False, False, False, False])
    tt = _write(tmp_path / "tt.json", [True, True, True, True])
    hb = _write(tmp_path / "hb.json", [False, False, True, True])
    ht = _write(tmp_path / "ht.json", [True, True, True, True])  # improved on holdout too
    result = runner.invoke(
        app, ["transfer-gate", tb, tt, "--holdout-baseline", hb, "--holdout-treatment", ht]
    )
    assert result.exit_code == 0
    assert "PROMOTE" in result.stdout
    assert "generalizes" in result.stdout


def test_block_when_tuned_does_not_help(tmp_path: Path) -> None:
    tb = _write(tmp_path / "tb.json", [True, True, True, True])
    tt = _write(tmp_path / "tt.json", [True, True, False, False])  # tuned got worse
    result = runner.invoke(app, ["transfer-gate", tb, tt])
    assert result.exit_code == 1
    assert "BLOCK" in result.stdout


def test_holdout_needs_both_halves(tmp_path: Path) -> None:
    tb = _write(tmp_path / "tb.json", [False])
    tt = _write(tmp_path / "tt.json", [True])
    hb = _write(tmp_path / "hb.json", [True])
    result = runner.invoke(app, ["transfer-gate", tb, tt, "--holdout-baseline", hb])
    assert result.exit_code == 1
    assert "BOTH" in result.stdout
