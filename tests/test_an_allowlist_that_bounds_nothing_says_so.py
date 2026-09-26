"""An allowlist keeping a tool that runs arbitrary code is not a boundary, and now says so.

arXiv 2609.07360 scanned 3,171 agent repositories and found **16.0%** carrying a grant that reads as
a restriction and is not — `Bash(python:*)` being the canonical shape: a permission scoped to one
command that can run anything.

Audited against this tree, the places that could carry it are closed and already tested — our skills
declare no tools at all, `tool_call` re-applies the denial (`test_a_denied_tool_cannot_be_reached_
through_the_proxy`), so does the MCP proxy (`test_mcp_is_opened_not_handed_over`), and the explorer
is checked against the deployment ceiling despite registering after the filter
(`test_explorer_is_registered_only_when_asked_and_inherits_the_allowlist`).

What was NOT closed is the allowlist itself. `chimera/tools/code.py` already says `code_interpreter`
honours `CHIMERA_HOST_EXEC` unconditionally "because otherwise `deny` would be trivially bypassable
— the model would simply pick this tool over `run_shell`". The same sentence is true of
`allow_tools` and nothing said it: `allow_tools=["read_file", "code_interpreter"]` reads as a
session that cannot run shell commands, and is a session that can.

The fix is a sentence, not a refusal — the combination is legitimate (a data-analysis session wants
exactly it) and silently dropping the tool would break real callers. What must not happen is someone
believing the list is the boundary.
"""

from __future__ import annotations

import logging

import pytest

from chimera.governance.allowlist import ARBITRARY_CODE, restrict_registry
from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry


class _Stub(Tool):
    parameters: dict = {"type": "object", "properties": {}}

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = name

    def run(self, **kwargs: object) -> str:
        return "ok"


def _registry(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.register(_Stub(name))
    return registry


def test_a_list_naming_a_tool_the_registry_lacks_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The warning is about what the session CAN do, so it reads the tools kept, not the names the
    list spells: `code_interpreter` on the list of a registry that has none keeps nothing that runs
    code. Found by study 25's `/review` bench on this file's own history (924aaa76)."""
    recorded: list[dict] = []

    class _Audit:
        def record(self, kind: str, payload: dict) -> None:
            recorded.append(payload)

    with caplog.at_level(logging.WARNING, logger="chimera.governance.allowlist"):
        kept = restrict_registry(
            _registry("read_file"),
            allow=["read_file", "code_interpreter"],
            audit=_Audit(),  # type: ignore[arg-type]
        )
    assert kept.names() == ["read_file"]
    assert "code_interpreter" not in caplog.text
    # Nothing was excluded and nothing that runs code was kept, so there is no trail to write —
    # and above all no `arbitrary_code_kept` naming a tool the session cannot call.
    assert all(not entry["arbitrary_code_kept"] for entry in recorded)

    # The same list on a registry that DOES hold an arbitrary-code tool still warns and records it.
    recorded.clear()
    with caplog.at_level(logging.WARNING, logger="chimera.governance.allowlist"):
        restrict_registry(
            _registry("read_file", "run_shell"),
            allow=["read_file", "run_shell", "code_interpreter"],
            audit=_Audit(),  # type: ignore[arg-type]
        )
    assert "run_shell" in caplog.text
    assert recorded and recorded[0]["arbitrary_code_kept"] == ["run_shell"]


def test_the_three_tools_that_make_a_list_meaningless_are_named() -> None:
    """Pinned as a set rather than checked ad hoc: a fourth arbitrary-code tool added later has to
    be added here too, and the test that fails is the one that says why."""
    assert sorted(ARBITRARY_CODE) == ["code_interpreter", "execute_code", "run_shell"]


@pytest.mark.parametrize("escape", sorted(ARBITRARY_CODE))
def test_an_allowlist_keeping_arbitrary_code_warns(
    escape: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="chimera.governance.allowlist"):
        restrict_registry(_registry("read_file", escape), allow=["read_file", escape])
    assert escape in caplog.text
    assert "not what the session can do" in caplog.text


def test_it_still_grants_the_tool_because_the_combination_is_legitimate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A warning, never a refusal. Dropping `code_interpreter` from a list that asked for it would
    break a data-analysis session to protect a belief nobody stated."""
    with caplog.at_level(logging.WARNING, logger="chimera.governance.allowlist"):
        kept = restrict_registry(
            _registry("read_file", "code_interpreter", "run_shell"),
            allow=["read_file", "code_interpreter"],
        )
    assert set(kept.names()) == {"read_file", "code_interpreter"}
    assert "run_shell" not in kept.names(), "the allowlist still does the filtering it always did"


def test_a_denied_escape_is_not_warned_about() -> None:
    """`deny` wins over `allow`, so a name in both is gone — and a warning about a tool that is not
    in the registry would send someone looking for a hole that was already closed."""
    caplog_free = restrict_registry(
        _registry("read_file", "run_shell"), allow=["read_file", "run_shell"], deny=["run_shell"]
    )
    assert set(caplog_free.names()) == {"read_file"}


def test_no_allowlist_is_not_a_false_boundary(caplog: pytest.LogCaptureFixture) -> None:
    """`allow=None` means no list is in force, so nobody is believing anything about it. Warning
    there would fire on every ordinary session and be tuned out by the time it mattered."""
    with caplog.at_level(logging.WARNING, logger="chimera.governance.allowlist"):
        restrict_registry(_registry("read_file", "run_shell"), deny=["read_file"])
    assert "not what the session can do" not in caplog.text


def test_the_trail_records_it_even_when_the_list_excluded_nothing() -> None:
    """The warning lives in a log nobody keeps; this is what a later reader has instead.

    An allowlist that excluded nothing used to write no audit entry at all, so "a list was in force
    and it bounded nothing" left no trace anywhere.
    """
    recorded: list[tuple[str, dict]] = []

    class _Audit:
        def record(self, kind: str, payload: dict) -> None:
            recorded.append((kind, payload))

    restrict_registry(
        _registry("read_file", "code_interpreter"),
        allow=["read_file", "code_interpreter"],
        audit=_Audit(),  # type: ignore[arg-type]
    )
    assert recorded, "an allowlist that bounds nothing left no trail"
    kind, payload = recorded[0]
    assert kind == "tool_allowlist"
    assert payload["arbitrary_code_kept"] == ["code_interpreter"]
    assert payload["excluded"] == []
