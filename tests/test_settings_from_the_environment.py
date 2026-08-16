"""Settings read the way a deployment sets them: from the environment.

Thirty-eight tests covered the tool fence and every one of them was green while
`CHIMERA_TOOL_DENYLIST=run_shell` made `chimera --help` exit 1. They all built
`Settings(CHIMERA_TOOL_DENYLIST="...")` by keyword, and a keyword goes through pydantic-settings'
InitSettingsSource, which does no JSON decoding. The env goes through EnvSettingsSource, which runs
`json.loads` on the raw string unless the field is annotated `NoDecode` — and those two fields were
the only two list fields in the class without it.

So the bug was not in the fence. It was in the seam between the fence and the only way anybody
turns it on, and the suite could not see the seam because it never crossed it.

Everything here goes through `monkeypatch.setenv`. That is the point of the file, not an
implementation detail: a test that passes a keyword is testing a code path no deployment uses.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from chimera.config import Settings


def _from_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    """Build Settings the way a process started from a shell or a `.env` file does."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


# --- the fence, set the documented way ------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("run_shell", ["run_shell"]),
        ("run_shell,write_file", ["run_shell", "write_file"]),
        (" run_shell , write_file ", ["run_shell", "write_file"]),
        ("", []),
    ],
)
def test_the_denylist_accepts_what_the_example_file_shows(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    """`.env.example:80` shows `CHIMERA_TOOL_DENYLIST=`, so a comma-separated list is THE form.

    Pre-fix every non-empty value here raised `SettingsError` out of `get_settings()`.
    """
    assert _from_env(monkeypatch, CHIMERA_TOOL_DENYLIST=raw).tool_denylist == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("read_file", ["read_file"]), ("read_file,write_file", ["read_file", "write_file"]), ("", [])],
)
def test_the_allowlist_accepts_the_same_form(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    assert _from_env(monkeypatch, CHIMERA_TOOL_ALLOWLIST=raw).tool_allowlist == expected


def test_a_json_array_still_works_for_anyone_who_wrote_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """The splitting validator handles a bracketed string too, so nobody who guessed JSON is broken
    by the fix. Worth pinning: `NoDecode` means WE parse it, and we must parse both shapes."""
    settings = _from_env(monkeypatch, CHIMERA_TOOL_DENYLIST='["run_shell", "write_file"]')

    assert "run_shell" in settings.tool_denylist


# --- the property that actually failed ------------------------------------------------------------


_LIST_FIELDS_SET_FROM_ENV = [
    ("CHIMERA_TOOL_ALLOWLIST", "tool_allowlist"),
    ("CHIMERA_TOOL_DENYLIST", "tool_denylist"),
    ("CHIMERA_OPENROUTER_KEYS", "openrouter_keys"),
    ("CHIMERA_OPENAI_KEYS", "openai_keys"),
    ("CHIMERA_FUSION_PANEL", "fusion_panel"),
    ("CHIMERA_FALLBACK_MODELS", "fallback_models"),
]


@pytest.mark.parametrize(("env_name", "field"), _LIST_FIELDS_SET_FROM_ENV)
def test_no_list_field_explodes_on_a_bare_comma_separated_value(
    monkeypatch: pytest.MonkeyPatch, env_name: str, field: str
) -> None:
    """The general rule, so the next list field added does not repeat this.

    Ten of the twelve list fields carried `NoDecode` and two did not, which is exactly why nothing
    looked wrong: the odd ones out were in the minority and nobody sets them in a unit test.
    """
    settings = _from_env(monkeypatch, **{env_name: "alpha,beta"})

    assert getattr(settings, field) == ["alpha", "beta"], f"{env_name} did not split"


def test_the_cli_opens_with_a_fence_configured(tmp_path: Any) -> None:
    """The end of the chain, and the only assertion that would have caught this.

    Every entry point builds settings before it does anything, so a `SettingsError` there is not a
    degraded feature — it is a product that does not start. A subprocess because that is the only
    way to prove the import-time path; in-process the module is already loaded.
    """
    done = subprocess.run(
        [sys.executable, "-c", "from chimera.config import get_settings; get_settings(); print('ok')"],
        capture_output=True,
        text=True,
        timeout=120,
        env={
            "PATH": __import__("os").environ.get("PATH", ""),
            "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
            "CHIMERA_TOOL_DENYLIST": "run_shell,write_file",
            "CHIMERA_TOOL_ALLOWLIST": "read_file",
        },
    )

    assert done.returncode == 0, f"settings refused to load with a fence set:\n{done.stderr[-800:]}"
    assert "ok" in done.stdout
