"""A key save writes the key to `.env` or the vault, and nowhere else.

Final review of 2026-10-04 (MEDIUM, a regression of 8b0b80c2). `config_api.check_parses` parses
candidate values from a scratch `.env` in the temp folder, and its probe took every Settings field —
provider keys included — so every key save wrote the key in clear text to `%TEMP%`, for good if the
process died before the cleanup, which is what `CHIMERA_KEY_VAULT` exists to prevent. Credentials
never enter the probe now; ordinary settings still do.
"""

from __future__ import annotations

import contextlib
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from chimera.api.config_api import check_parses, patch_config
from chimera.config import get_settings

DUMMY = "sk-or-v1-" + "d00d" * 12


@contextlib.contextmanager
def _kept_scratch(root: Path) -> Iterator[str]:
    """A TemporaryDirectory that is never deleted, so the test can see what was written to it."""
    root.mkdir(parents=True, exist_ok=True)
    yield tempfile.mkdtemp(dir=root)


def _files_holding(root: Path, text: str) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and text in p.read_text("utf-8", "ignore")]


def test_saving_a_key_leaves_no_copy_of_it_in_the_temp_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch = tmp_path / "temp"
    monkeypatch.setattr(tempfile, "TemporaryDirectory", lambda *a, **k: _kept_scratch(scratch))
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("CHIMERA_OPENROUTER_KEYS", "")
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    monkeypatch.setenv("CHIMERA_KEY_VAULT", "false")
    get_settings.cache_clear()
    env = tmp_path / ".env"

    patch_config({"OPENROUTER_API_KEY": DUMMY}, env_path=env)
    patch_config({"CHIMERA_SERVER_TOKEN": DUMMY + "-token"}, env_path=env)
    check_parses({"CHIMERA_OPENROUTER_KEYS": f"{DUMMY},{DUMMY}2"})

    assert _files_holding(scratch, DUMMY) == []
    assert DUMMY in env.read_text(encoding="utf-8")
    get_settings.cache_clear()


def test_an_ordinary_setting_is_still_probed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe still runs for what can fail to parse — and that is what it writes."""
    scratch = tmp_path / "temp"
    monkeypatch.setattr(tempfile, "TemporaryDirectory", lambda *a, **k: _kept_scratch(scratch))
    with pytest.raises(ValueError, match="CHIMERA_CASCADE"):
        check_parses({"CHIMERA_CASCADE": "maybe", "OPENROUTER_API_KEY": DUMMY})
    assert _files_holding(scratch, "CHIMERA_CASCADE")
    assert _files_holding(scratch, DUMMY) == []
