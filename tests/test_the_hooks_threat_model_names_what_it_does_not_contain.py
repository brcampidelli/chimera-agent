"""What the hooks do NOT contain is measured here, and the threat model has to say it.

An adversarial review of S30-65 found four places where `docs/hooks-threat-model.md` (and the audit's
row 13) claimed more than the code does. Each was true behaviour and a false sentence, so each test
below does two things: it pins the behaviour, and it holds the document to naming it. If a later
change closes one of these gaps, the behavioural half turns red and the sentence must change with
it — a residual that stays written after it is fixed is a false sentence too, in the other direction.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.core.queue_fence import reaches_hooks_file
from chimera.governance.hooks import HOOKS_FILE_NAME, HookRunner, hook_registry, load_hooks
from chimera.sandbox.base import SandboxResult
from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
THREAT_MODEL = (ROOT / "docs" / "hooks-threat-model.md").read_text(encoding="utf-8")
ROW_13 = next(
    line for line in (ROOT / "docs" / "audits" / "sleeper-channels.md")
    .read_text(encoding="utf-8").splitlines()
    if line.startswith("| 13 |")
)


def _flat(text: str) -> str:
    return " ".join(text.split())


class _Recorder(Tool):
    def __init__(self) -> None:
        self.name = "run_shell"
        self.description = "records"
        self.parameters = {"type": "object", "properties": {}}

    def run(self, **_kwargs: Any) -> str:
        return "ran"


# --- A1: code_interpreter is in-process host execution ------------------------------------------

IN_PROCESS_BYPASSES = [
    "import sys; sys.modules['chimera.governance.hooks'].HookedTool.run = "
    "lambda s, **k: s.inner.run(**k)",
    "import chimera.governance as g; g.hooks.apply_hooks = lambda r, **k: r",
    "importlib.import_module('chimera.governance.' + 'hooks')",
    "import chimera.config as c; c.get_settings().hooks = False",
]


@pytest.mark.parametrize("program", IN_PROCESS_BYPASSES)
def test_the_fence_does_not_see_in_process_code_that_reaches_the_hooks(
    program: str, tmp_path: Path
) -> None:
    """The limit, pinned: none of these is an import the pattern can see."""
    assert reaches_hooks_file(program, home=tmp_path, cwd=tmp_path) is None


def test_the_threat_model_says_code_interpreter_is_not_contained_by_the_fence() -> None:
    flat = _flat(THREAT_MODEL)
    assert "**Residual, `code_interpreter`.**" in flat
    assert "in the server's own process" in flat
    assert "not the fence" in flat
    assert "`code_interpreter` runs in the server's process and is not contained" in ROW_13


# --- A4: no ledger on governed_profile surfaces in mode off ------------------------------------


def test_a_governed_profile_surface_in_mode_off_has_no_ledger_around_the_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ledger `governed_profile` builds is dropped when the mode is off, so the hooks are the
    outermost layer and a shell hook's words have nothing to taint."""
    from chimera.governance.hooks import HookedTool
    from chimera.governance.profile import governed_profile

    home = tmp_path / "home"
    home.mkdir()
    (home / HOOKS_FILE_NAME).write_text(
        json.dumps({"hooks": [{"id": "n", "event": "pre_tool", "decision": "annotate",
                               "note": "x"}]}), encoding="utf-8")
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_HOOKS", "true")
    get_settings.cache_clear()
    try:
        registry = ToolRegistry()
        registry.register(_Recorder())
        wrapped, _ = governed_profile(
            registry, settings=get_settings(), home=home, mode="off", surface="cron"
        )
        assert isinstance(wrapped.get("run_shell"), HookedTool)
    finally:
        get_settings.cache_clear()
    flat = _flat(THREAT_MODEL)
    assert "no taint ledger wraps the registry" in flat
    assert "taint nothing" in flat
    assert "a shell hook's words are fenced but taint nothing" in ROW_13


# --- A5: the working folder holds only event.json ----------------------------------------------


class _Listing:
    def __init__(self) -> None:
        self.seen: list[list[str]] = []

    def is_isolated(self) -> bool:
        return True

    def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
        assert cwd is not None
        self.seen.append(sorted(os.listdir(cwd)))
        return SandboxResult(exit_code=0, stdout="", timed_out=False)


def test_a_shell_hooks_working_folder_holds_only_the_event_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / HOOKS_FILE_NAME).write_text(
        json.dumps({"hooks": [{"id": "l", "event": "pre_tool", "command": "x"}]}),
        encoding="utf-8",
    )
    box = _Listing()
    runner = HookRunner(load_hooks(home), sandbox=lambda: box, host_exec=False)
    registry = ToolRegistry()
    registry.register(_Recorder())
    assert hook_registry(registry, runner).get("run_shell").run(command="ls") == "ran"
    assert box.seen == [["event.json"]]


def test_the_threat_model_says_the_hooks_folder_is_empty_and_names_programs_absolutely() -> None:
    flat = _flat(THREAT_MODEL)
    assert "it holds **only** `event.json`" in flat
    assert "Name the hook's program by absolute path" in flat
    assert "`CHIMERA_SANDBOX=docker`" in flat
    # The example the document gives must be one that can run from that folder.
    assert '"command": "python lint_hook.py"' not in THREAT_MODEL


# --- where hooks apply --------------------------------------------------------------------------


def test_the_threat_model_and_row_13_list_every_surface_that_runs_without_hooks() -> None:
    flat = _flat(THREAT_MODEL)
    residual = flat.split("**Residual — where hooks do not run.**", 1)[1].split("## The file", 1)[0]
    for surface in ("`chimera agent`", "`chimera solve`", "`CHIMERA_GUARD_CHAT` off",
                    "`/v1/chat/completions`"):
        assert surface in residual, surface
        assert surface in ROW_13, surface
    assert "`chimera agent --guard`" in flat and "`profile.owner_hooks`" in flat


def test_the_settings_hint_no_longer_promises_every_tool_call() -> None:
    i18n = (ROOT / "apps" / "desktop" / "src" / "lib" / "i18n.tsx").read_text(encoding="utf-8")
    english = next(line for line in i18n.splitlines() if line.startswith('  "settings.hint.hooks":'))
    assert "each guarded tool call" in english and "OpenAI-compatible endpoint" in english
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "/v1/chat/completions run without them" in _flat(env.replace("#", ""))
