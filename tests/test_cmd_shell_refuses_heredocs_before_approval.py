"""The cmd.exe host must refuse bash heredocs before asking to run an impossible command.

On Windows the local host command is sent to cmd.exe, which has no heredoc syntax. The failure
used to happen only after an approval wait, although no approval could make the command executable.
These tests force the effective-shell seam so they exercise the Windows branch on every platform.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.sandbox import LocalSandbox
from chimera.tools.shell import RunShellTool, _cmd_heredoc_refusal


@pytest.mark.parametrize(
    "command",
    [
        "python - <<EOF\nprint('ok')\nEOF",
        "python - <<'PY'\nprint('ok')\nPY",
        "python - <<-EOF\nprint('ok')\nEOF",
        'python - << "X"\nprint("ok")\nX',
    ],
)
def test_cmd_heredoc_forms_are_refused_with_alternatives(command: str) -> None:
    refusal = _cmd_heredoc_refusal(command, shell_is_cmd=True)

    assert refusal is not None
    assert "cmd.exe" in refusal
    assert "heredoc" in refusal
    assert "write_file" in refusal and "python script.py" in refusal
    assert "powershell -Command" in refusal


def test_the_same_heredoc_is_not_refused_for_sh() -> None:
    command = "python - <<'PY'\nprint('ok')\nPY"
    assert _cmd_heredoc_refusal(command, shell_is_cmd=False) is None


@pytest.mark.parametrize(
    "command",
    [
        'powershell -Command "Write-Output \'<<EOF\'"',
        'pwsh -c "Write-Output \'<<EOF\'"',
        "echo a && echo b",
        "dir > x.txt",
        'python -c "print(1<<2)"',
    ],
)
def test_cmd_detector_leaves_valid_commands_alone(command: str) -> None:
    assert _cmd_heredoc_refusal(command, shell_is_cmd=True) is None


def test_refusal_happens_before_the_host_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    def confirm(command: str) -> bool:
        asked.append(command)
        return True

    tool = RunShellTool(tmp_path, LocalSandbox(), confirm=confirm)
    monkeypatch.setattr(tool, "_uses_cmd_shell", lambda _sandbox, _command, _cwd: True)

    out = tool.run(command="python - <<'PY'\nprint('ok')\nPY")

    assert asked == []
    assert "cmd.exe" in out and "heredoc" in out


def test_detector_only_considers_unquoted_heredoc_operators() -> None:
    assert _cmd_heredoc_refusal('python -c "print(1<<2)"', shell_is_cmd=True) is None
    assert _cmd_heredoc_refusal("python -c 'print(1<<2)'", shell_is_cmd=True) is None
    assert _cmd_heredoc_refusal("python - <<'PY'\npass\nPY", shell_is_cmd=True) is not None


@pytest.mark.parametrize("command", ["echo a && echo b", "dir > x.txt"])
def test_cmd_operators_other_than_heredoc_are_not_refused(command: str) -> None:
    assert _cmd_heredoc_refusal(command, shell_is_cmd=True) is None


def test_a_heredoc_in_a_quoted_powershell_argument_is_not_refused() -> None:
    assert _cmd_heredoc_refusal(
        'powershell -Command "Write-Output \'<<EOF\'"', shell_is_cmd=True
    ) is None


def test_non_cmd_shell_does_not_receive_a_cmd_refusal() -> None:
    assert _cmd_heredoc_refusal("cat <<EOF\ntext\nEOF", shell_is_cmd=False) is None

