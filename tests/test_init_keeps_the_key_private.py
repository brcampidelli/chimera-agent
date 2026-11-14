"""`chimera init` keeps the key out of sight: no echo at the prompt, and a `.env` only its owner reads.

Before the fix the prompt echoed the key, and the file came out with the umask's mode (0644 on a
usual Linux or macOS account). The file-mode check reads the mode bits, so it only means something
on POSIX and is skipped on Windows, where the bits are not the protection.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from chimera.cli.main import app

# Concatenated so the literal is not a key-shaped string for the secret scanner.
FAKE_KEY = "sk-or-" + "fake-for-a-test-0123456789"


def test_the_key_prompt_hides_its_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def fake_prompt(text: str, **kwargs: Any) -> str:
        seen.append({"text": text, **kwargs})
        return str(kwargs.get("default", ""))

    monkeypatch.setattr(typer, "prompt", fake_prompt)
    result = CliRunner().invoke(app, ["init", "--home", str(tmp_path)])
    assert result.exit_code == 0, result.output
    key_prompts = [call for call in seen if "API key" in call["text"]]
    assert key_prompts, "init never asked for the key"
    assert all(call.get("hide_input") is True for call in key_prompts)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits; Windows has no such protection")
def test_init_writes_the_env_file_as_owner_only(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["init", "--yes", "--key", FAKE_KEY, "--home", str(tmp_path)])
    assert result.exit_code == 0, result.output
    mode = stat.S_IMODE((tmp_path / ".env").stat().st_mode)
    assert mode == 0o600, oct(mode)
    assert FAKE_KEY in (tmp_path / ".env").read_text(encoding="utf-8")
    assert FAKE_KEY not in result.output, "the key was echoed to the terminal"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits; Windows has no such protection")
def test_the_env_is_owner_only_when_the_flow_is_interrupted_after_the_first_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The key is written before the cost-mode prompt. If that prompt is interrupted (Ctrl+C, a
    # crash), init never reaches its end, so the mode must already be 0600 at that point.
    def interrupted(text: str, **kwargs: Any) -> str:
        if "Cost mode" in text:
            raise RuntimeError("interrupted at the cost prompt")
        return str(kwargs.get("default", ""))

    monkeypatch.setattr(typer, "prompt", interrupted)
    CliRunner().invoke(app, ["init", "--key", FAKE_KEY, "--home", str(tmp_path)])
    env = tmp_path / ".env"
    assert env.exists()
    assert FAKE_KEY in env.read_text(encoding="utf-8"), "the first write never happened"
    mode = stat.S_IMODE(env.stat().st_mode)
    assert mode == 0o600, oct(mode)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits; Windows has no such protection")
def test_an_existing_env_is_restricted_before_the_first_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text("# left over from an earlier install\n", encoding="utf-8")
    os.chmod(env, 0o644)
    seen: dict[str, int] = {}

    def stop_at_the_first_prompt(text: str, **kwargs: Any) -> str:
        seen["mode"] = stat.S_IMODE(env.stat().st_mode)
        raise RuntimeError("stopped before any answer")

    monkeypatch.setattr(typer, "prompt", stop_at_the_first_prompt)
    CliRunner().invoke(app, ["init", "--home", str(tmp_path)])
    assert seen.get("mode") == 0o600, oct(seen.get("mode", -1))
