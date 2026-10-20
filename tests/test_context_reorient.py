from __future__ import annotations

from pathlib import Path

from chimera.core.context_budget import RunState
from chimera.core.context_reorient import reorientation_message


def test_reorientation_rereads_changed_in_progress_file(tmp_path: Path) -> None:
    target = tmp_path / "src.py"
    target.write_text("before = 1\n", encoding="utf-8")
    state = RunState(task="finish the edit", open_file=("src.py", "before = 1\n"))
    state.record_tool_success("read_file", {"path": "src.py"}, "before = 1\n")
    target.write_text("after = 2\n", encoding="utf-8")

    message = reorientation_message(state, tmp_path)

    assert "finish the edit" in message
    assert "after = 2" in message
    assert "changed since last read" in message


def test_reorientation_is_empty_without_files_or_workspace(tmp_path: Path) -> None:
    assert reorientation_message(RunState(), tmp_path) == ""
    assert reorientation_message(RunState(open_file=("src.py", "x")), None) == ""


def test_reorientation_bounds_file_refresh(tmp_path: Path) -> None:
    target = tmp_path / "large.py"
    target.write_text("x " * 30_000, encoding="utf-8")
    state = RunState(open_file=("large.py", "x"), task="resume")

    message = reorientation_message(state, tmp_path)

    assert "over 5000 tokens, re-read as needed" in message
    assert len(message) < 2_000

def test_reorientation_refreshes_open_then_written_then_recent_files_and_caps_count(
    tmp_path: Path,
) -> None:
    from chimera.core.context_reorient import MAX_REORIENT_FILES

    names = ["open.py", "written.py", "recent.py", "older.py", "extra.py", "last.py"]
    for name in names:
        (tmp_path / name).write_text(f"content of {name}", encoding="utf-8")
    state = RunState(task="edit files", open_file=("open.py", "stale"))
    state.write_progress = ["written.py"]
    state.read_progress = [f"{name} — 10 chars, sha256:oldhash" for name in names[2:]]

    message = reorientation_message(state, tmp_path)

    prioritized = names[:2] + list(reversed(names[2:]))
    selected = prioritized[:MAX_REORIENT_FILES]
    positions = [message.index(f"--- {name}") for name in selected]
    assert positions == sorted(positions)
    assert "--- recent.py" not in message
    assert "last.py" in message and "extra.py" in message and "older.py" in message


def test_reorientation_setting_defaults_off() -> None:
    from chimera.core.agent import AgentConfig

    assert AgentConfig().reorient_on_context_drift is False
    assert AgentConfig(reorient_on_context_drift=True).reorient_on_context_drift is True
