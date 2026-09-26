"""Three study-25 modules the Settings screen can now switch, and none of them switched on.

`CHIMERA_BROWSER_SITUATION`, `CHIMERA_RESEARCH_AGENT` and `CHIMERA_EXPLORER_CONTRACT` were wired and
read by the product, and reachable only by editing `.env`: `patch_config` refused all three and
`GET /api/config` did not report them. Their measurements did not recommend them, which is a reason
to keep them OFF, not a reason to hide the choice.

So the tests hold both halves: the screen can read and write them, and nothing about making them
reachable turned any of them on. No browser, model or sub-agent runs — the subject is the wiring.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.api.config_api import APPLIES_WHEN, is_editable, patch_config, read_config
from chimera.api.schemas import ConfigOut, ExperimentalCfgOut
from chimera.config import Settings, get_settings

# Env var -> the Settings field that reads it, and the key it is reported under.
EXPERIMENTAL = {
    "CHIMERA_BROWSER_SITUATION": "browser_situation",
    "CHIMERA_RESEARCH_AGENT": "research_agent",
    "CHIMERA_EXPLORER_CONTRACT": "explorer_contract",
}


@pytest.mark.parametrize("env", sorted(EXPERIMENTAL))
def test_the_screen_may_write_each_one(env: str) -> None:
    """A refused key is invisible to the user: the row would save nothing and say it had."""
    assert is_editable(env)


def test_all_three_are_off_when_nobody_chose_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for env in EXPERIMENTAL:
        monkeypatch.delenv(env, raising=False)
    settings = Settings(CHIMERA_HOME=str(tmp_path))  # type: ignore[arg-type]

    assert read_config(settings)["experimental"] == {field: False for field in EXPERIMENTAL.values()}
    # And a server that predates the block reads as all-off, not as a validation error.
    assert "experimental" in ConfigOut.model_fields
    assert ExperimentalCfgOut().model_dump() == {field: False for field in EXPERIMENTAL.values()}


@pytest.mark.parametrize(("env", "field"), sorted(EXPERIMENTAL.items()))
def test_the_screen_reads_each_one_back_when_it_is_on(
    env: str, field: str, tmp_path: Path
) -> None:
    on = Settings(CHIMERA_HOME=str(tmp_path), **{env: "true"})  # type: ignore[arg-type]

    reported = read_config(on)["experimental"]
    assert reported[field] is True
    assert [k for k, v in reported.items() if v] == [field]  # the other two did not move with it


@pytest.mark.parametrize(("env", "field"), sorted(EXPERIMENTAL.items()))
def test_a_save_writes_that_key_to_the_env_file_and_the_reader_sees_it(
    env: str, field: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Through `get_settings()`, the same cache clear the endpoint relies on — the step a Settings
    built by hand would skip, and the one a stale-cache regression would break."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    # patch_config writes os.environ for real; own the name first so the teardown restores it.
    monkeypatch.setenv(env, "false")
    get_settings.cache_clear()
    env_file = tmp_path / ".env"
    env_file.write_text("CHIMERA_DEFAULT_MODEL=some/model\n", encoding="utf-8")

    patch_config({env: "true"}, env_path=env_file)

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert lines == ["CHIMERA_DEFAULT_MODEL=some/model", f"{env}=true"]
    assert getattr(get_settings(), field) is True

    patch_config({env: "false"}, env_path=env_file)  # switching it off again rewrites, not appends
    assert env_file.read_text(encoding="utf-8").splitlines()[1:] == [f"{env}=false"]
    assert getattr(get_settings(), field) is False
    get_settings.cache_clear()


def test_the_browser_module_declares_it_waits_for_the_next_conversation() -> None:
    """Read when the registry builds the browser tool and when the agent is built, as headless is;
    a chat holds both for its lifetime. The other two are read per Code turn: the next call."""
    assert APPLIES_WHEN["CHIMERA_BROWSER_SITUATION"] == "next_conversation"
    assert "CHIMERA_RESEARCH_AGENT" not in APPLIES_WHEN
    assert "CHIMERA_EXPLORER_CONTRACT" not in APPLIES_WHEN
