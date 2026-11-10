"""The commands axis that lets "Accept edits" edit freely and still stop before each command.

The Code screen's permission modes map onto the two posture axes (reach, approval). One combination
needed a third fact: a workspace that may run commands, where each command asks first. Before this
axis, ``allow_host_exec`` made every granted command run ungated, so the combination could not be
expressed. These tests pin the rule the turn gate now applies: ``commands="asks"`` never runs a
command ungated, and an absent posture keeps the request's own answer exactly.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from chimera.api.code_api import granted_posture
from chimera.api.posture import (
    DEFAULT_COMMANDS,
    Posture,
    commands_run_without_asking,
)


def test_commands_default_to_granted_so_old_callers_are_unchanged() -> None:
    assert DEFAULT_COMMANDS == "granted"
    assert Posture().commands == "granted"


def test_commands_accepts_only_its_two_values() -> None:
    assert Posture(commands="asks").commands == "asks"
    with pytest.raises(ValidationError):
        Posture(commands="sometimes")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("posture", "allow_host_exec", "expected"),
    [
        # No posture: the request decides, as it did before the axis existed.
        (None, True, True),
        (None, False, False),
        # "granted" leaves the grant in charge.
        (Posture(reach="workspace_shell", approval="never"), True, True),
        (Posture(reach="workspace_shell", approval="never"), False, False),
        # "asks" stops every command, even when the project granted commands.
        (Posture(reach="workspace_shell", approval="never", commands="asks"), True, False),
        (Posture(reach="workspace_shell", approval="never", commands="asks"), False, False),
    ],
)
def test_asks_is_never_ungated(posture: Posture | None, allow_host_exec: bool, expected: bool) -> None:
    assert commands_run_without_asking(posture, allow_host_exec) is expected


def test_the_commands_axis_survives_the_grant_clamp() -> None:
    # A folder without a recorded shell grant drops workspace_shell to workspace, and must keep the
    # question: the clamp narrows reach, it must not quietly drop the commands axis.
    asked = Posture(reach="workspace_shell", approval="never", commands="asks")
    clamped = granted_posture(asked, granted=False)
    assert clamped is not None
    assert clamped.reach == "workspace"
    assert clamped.commands == "asks"


def test_a_command_asks_the_turns_card_in_the_card_shape() -> None:
    # The card's first line is what the approval rules match: "run_shell: <command>". A command asked
    # through the turn's approver must arrive in that shape, or "always allow this command" could not
    # remember it.
    from chimera.api.code_api import _ask_command_on_card

    asked: list[tuple[str, str]] = []

    def approver(question: object, action: str) -> bool:
        asked.append((getattr(question, "action", ""), action))
        return True

    ask = _ask_command_on_card([approver])
    assert ask("npm test") is True
    assert asked == [("run_shell: npm test", "run_shell: npm test")]


def test_a_command_with_nobody_bound_to_answer_is_refused() -> None:
    # Before the turn binds its approver, and when nothing does, a question is refused: the same
    # outcome an unanswered question always had, never a command run on silence.
    from chimera.api.code_api import _ask_command_on_card

    assert _ask_command_on_card([None])("npm test") is False


def test_the_owners_refusal_is_the_answer_to_a_command() -> None:
    from chimera.api.code_api import _ask_command_on_card

    assert _ask_command_on_card([lambda question, action: False])("rm -rf build") is False
