"""An external agent driven over ACP meets the same refusals as Chimera's own file tools.

Final review of 2026-10-04 (LOW, pre-existing). `AcpTurn` answers the agent's `fs/read_text_file`
and `fs/write_text_file` itself, and both handlers called `resolve_in_workspace` directly — so they
skipped what the native tools refuse through `resolve_for`: Chimera's own `.env` when the owner keeps
it from the agent (`CHIMERA_AGENT_READS_OWN_ENV` off), and any write to that `.env` or into the data
folder. Both handlers now make the same refusals; a refused write is recorded as refused.

(An agent with its own file tools can still read and write without asking us; the posture note on
the external-agent card already says so. This closes the door that does go through us.)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.acp.client import AcpError
from chimera.acp.turn import AcpTurn
from chimera.config import get_settings
from chimera.tools.workspace import PathEscapesWorkspaceError
from tests.test_acp_turn import _spec

KEY = "sk-or-v1-" + "acce55" * 8


def _turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, reads: str) -> AcpTurn:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FAKE_ACP_SCRIPT", "")  # `_spec` writes it; owned so the teardown restores it
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_AGENT_READS_OWN_ENV", reads)
    get_settings.cache_clear()
    (tmp_path / ".env").write_text(f"OPENROUTER_API_KEY={KEY}\n", encoding="utf-8")
    (tmp_path / "home" / "approvals").mkdir(parents=True)
    (tmp_path / "notes.md").write_text("ordinary\n", encoding="utf-8")
    return AcpTurn(_spec([]), tmp_path)


def test_a_read_of_chimeras_env_follows_the_owners_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    turn = _turn(tmp_path, monkeypatch, reads="false")
    with pytest.raises((PathEscapesWorkspaceError, AcpError)):
        turn._read_text_file({"path": ".env"})
    assert turn._read_text_file({"path": "notes.md"}) == {"content": "ordinary\n"}
    monkeypatch.setenv("CHIMERA_AGENT_READS_OWN_ENV", "true")
    get_settings.cache_clear()
    assert KEY in turn._read_text_file({"path": ".env"})["content"]  # the default, as before
    get_settings.cache_clear()


@pytest.mark.parametrize("path", [".env", "home/approvals/q1.answer.json", "home/memory.json"])
def test_a_write_to_chimeras_own_files_is_refused_and_recorded(
    path: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    turn = _turn(tmp_path, monkeypatch, reads="true")
    with pytest.raises(AcpError):
        turn._write_text_file({"path": path, "content": "CHIMERA_REACH=workspace_shell\n"})
    assert (tmp_path / ".env").read_text(encoding="utf-8") == f"OPENROUTER_API_KEY={KEY}\n"
    assert not (tmp_path / "home" / "approvals" / "q1.answer.json").exists()
    assert turn._result.refused and turn._result.edited == []
    # An ordinary write still lands.
    assert turn._write_text_file({"path": "plan.md", "content": "# plan\n"}) == {}
    assert (tmp_path / "plan.md").read_text(encoding="utf-8") == "# plan\n"
    get_settings.cache_clear()
