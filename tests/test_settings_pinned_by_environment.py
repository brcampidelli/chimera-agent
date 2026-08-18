"""The settings the Settings screen cannot actually change.

`.env` loses to a real environment variable — that is pydantic-settings' precedence for every field
in `Settings`, and `_export_env_file_credentials` deliberately mirrors it with `setdefault`. But
`patch_config` writes `.env` AND `os.environ`, so a save for a key the process inherited succeeds,
reports success, holds for the whole session, and reverts at the next launch.

That delay is what makes it worse than a refusal: by the time the old value is back, nobody connects
it to the save, and the screen has spent its credibility on a control that reported a change it could
not keep. It is reachable in practice because the desktop app can be pointed at a server somebody
else deployed — a container started with `-e CHIMERA_REACH=read_only`, a systemd unit with
`Environment=`. So the server names the keys and the screen says so on the row.

The load-bearing test here is `test_a_value_written_by_patch_config_is_not_reported_as_pinned`: the
obvious implementation — "is it in `os.environ` right now" — passes every other test in this file and
fails that one, because `patch_config` puts every key it writes into `os.environ`. It would mark
every saved setting as pinned, which is the same lie in the opposite direction.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import chimera.config as config_module
from chimera.api.config_api import patch_config, read_config
from chimera.config import Settings, pinned_by_environment


@pytest.fixture(autouse=True)
def _clean_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from an empty inherited environment.

    The real snapshot is taken when `chimera.config` is imported, which under pytest is long before
    any test runs — so a machine that happens to export `CHIMERA_*` would otherwise change the
    answers here. Replacing it is also the only way to simulate the thing under test, since
    `monkeypatch.setenv` by definition happens after import.
    """
    monkeypatch.setattr(config_module, "_STARTUP_ENV", frozenset())


def test_a_key_the_process_inherited_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "_STARTUP_ENV", frozenset({"CHIMERA_REACH"}))

    assert pinned_by_environment({"CHIMERA_REACH", "CHIMERA_SANDBOX"}) == ["CHIMERA_REACH"]


def test_a_key_nobody_exported_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "_STARTUP_ENV", frozenset({"PATH", "HOME"}))

    assert pinned_by_environment({"CHIMERA_REACH", "OPENROUTER_API_KEY"}) == []


def test_a_lower_case_export_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Settings` declares `case_sensitive=False`, so pydantic honours `chimera_reach=…`.

    Matching case-sensitively here would miss a pin that is genuinely in force — a false negative on
    the one screen whose job is to say when it cannot keep a promise.
    """
    monkeypatch.setattr(config_module, "_STARTUP_ENV", frozenset({"CHIMERA_REACH"}))

    assert pinned_by_environment({"chimera_reach"}) == ["chimera_reach"]


def test_a_value_written_by_patch_config_is_not_reported_as_pinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The reading that separates a snapshot from a live look at `os.environ`.

    `patch_config` writes the process environment on purpose, so the running gateway sees the new
    value without a restart. A pinned-check that read `os.environ` at request time would therefore
    call every setting the user just saved "pinned by the environment" — advice to go and edit a unit
    file that does not mention it.
    """
    monkeypatch.chdir(tmp_path)
    patch_config({"CHIMERA_SANDBOX": "docker"}, env_path=tmp_path / ".env")

    import os

    assert os.environ["CHIMERA_SANDBOX"] == "docker"  # the patch really did write the environment
    assert pinned_by_environment({"CHIMERA_SANDBOX"}) == []


def test_the_config_response_carries_the_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reported by the server rather than worked out by the client: only the process that was
    started knows what it was started with, and the client may be talking to someone else's box."""
    monkeypatch.setattr(
        config_module, "_STARTUP_ENV", frozenset({"CHIMERA_APPROVAL", "OPENROUTER_API_KEY"})
    )

    pinned = read_config(Settings(CHIMERA_HOME=str(tmp_path)))["pinned"]  # type: ignore[arg-type]

    assert pinned == ["CHIMERA_APPROVAL", "OPENROUTER_API_KEY"]


def test_credential_pools_are_covered_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`CHIMERA_OPENROUTER_KEYS` is not in `ALLOWED_KEYS` — the pool endpoints write it, not
    `patch_config` — but it goes to the same `.env` and meets the same override, so leaving it out
    would make the Key pools card the one place on the screen that still lies quietly."""
    monkeypatch.setattr(config_module, "_STARTUP_ENV", frozenset({"CHIMERA_OPENROUTER_KEYS"}))

    pinned = read_config(Settings(CHIMERA_HOME=str(tmp_path)))["pinned"]  # type: ignore[arg-type]

    assert pinned == ["CHIMERA_OPENROUTER_KEYS"]


def test_nothing_is_pinned_on_an_ordinary_install(tmp_path: Path) -> None:
    """The normal answer, and the reason the note is silent rather than a standing banner."""
    assert read_config(Settings(CHIMERA_HOME=str(tmp_path)))["pinned"] == []  # type: ignore[arg-type]
