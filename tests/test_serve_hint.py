"""The serve path prints a friendly install hint when a messaging extra is missing (P3b gap)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import typer

from chimera.cli.main import _serve_platform
from chimera.config import get_settings
from chimera.providers import CompletionResult


class _MissingExtraAdapter:
    """A platform adapter whose start() fails as if the messaging extra weren't installed."""

    platform = "discord"

    def send(self, *args: Any, **kwargs: Any) -> None:  # SenderRegistry.register needs a sender
        pass

    def start(self, on_message: Any) -> None:
        raise ImportError("No module named 'discord'")

    def stop(self) -> None:
        pass


class _FakeBackend:
    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        return CompletionResult(content="ok", model="fake")


def test_serve_platform_hints_to_install_extra(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(typer.Exit) as exc:
        _serve_platform(
            _MissingExtraAdapter(), get_settings(), _FakeBackend(), None, 4, tmp_path, None, None
        )
    assert exc.value.exit_code == 1
    assert "uv sync --extra messaging" in capsys.readouterr().out
