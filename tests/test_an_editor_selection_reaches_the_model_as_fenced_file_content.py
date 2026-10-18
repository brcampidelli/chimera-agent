"""The Editor's "Change this" proposes and never writes; its lines go through the one mention door.

"Ask Chimera" and "Change this" point at lines the way the composer's ``@`` does — one ``context``
item, a file with a line range (`chimera/api/turn_context.py`), read from disk and fenced as data.
A proposal adds ``propose_change``, and two properties are the server's to keep, because the client
cannot be the one trusted with them:

* the turn runs read-only whatever posture the body carried, so the only way the proposal reaches
  the file is the person accepting its diff in the Editor;
* the instruction to propose comes after the person's words and after the lines, in the app's
  words, naming exactly the range that may be replaced.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from chimera.api.code_api import CodeTurnRequest
from chimera.core.agent import AgentResult
from chimera.governance.ledger import WRITE_TOOLS
from chimera.governance.sanitize import FENCE_CLOSE, FENCE_OPEN
from tests.conversation_fakes import App

RANGE = {"kind": "file", "path": "a.py", "start_line": 2, "end_line": 2}


@pytest.mark.parametrize("sent", [None, {"reach": "workspace_shell", "approval": "never"}])
def test_a_proposal_is_read_only_whatever_the_body_said(sent: dict[str, str] | None) -> None:
    body: dict[str, Any] = {"message": "make it two", "context": [RANGE], "propose_change": True}
    if sent is not None:
        body["posture"] = sent
    req = CodeTurnRequest.model_validate(body)

    assert req.posture is not None and req.posture.reach == "read_only"
    # When to be asked is the person's choice and is kept; only how far the turn reaches narrows.
    assert req.posture.approval == (sent["approval"] if sent else "suspicious")


def test_pointing_without_proposing_keeps_the_posture_it_was_sent_with() -> None:
    req = CodeTurnRequest.model_validate(
        {"message": "why?", "context": [RANGE], "posture": {"reach": "workspace", "approval": "suspicious"}}
    )
    assert req.posture is not None and req.posture.reach == "workspace"


@pytest.mark.parametrize(
    "context",
    [
        [],
        [{"kind": "file", "path": "a.py"}],  # no range: there is nothing bounded to replace
        [{"kind": "folder", "path": "src"}],
        [RANGE, {"kind": "file", "path": "b.py", "start_line": 1, "end_line": 1}],
    ],
)
def test_a_proposal_is_about_exactly_one_ranged_file(context: list[dict[str, Any]]) -> None:
    with pytest.raises(ValidationError, match="a proposed change is about"):
        CodeTurnRequest.model_validate({"message": "x", "context": context, "propose_change": True})


def test_a_proposal_refuses_an_external_agent() -> None:
    with pytest.raises(ValidationError, match="external provider"):
        CodeTurnRequest.model_validate(
            {"message": "x", "context": [RANGE], "propose_change": True, "provider": "claude"}
        )


class _Recording:
    """A coding agent that remembers what it was asked and which tools it was handed."""

    tasks: list[str] = []
    tools: list[list[str]] = []

    def __init__(self, *args: Any, **_kw: Any) -> None:
        registry = args[1] if len(args) > 1 else None
        names = getattr(registry, "names", None)
        type(self).tools.append(list(names()) if callable(names) else [])

    def run(self, task: str, **_kw: Any) -> AgentResult:
        type(self).tasks.append(task)
        return AgentResult(
            answer="```\nx = 2\n```", steps=1, stopped_reason="final",
            transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "ok"}],
        )


def test_through_the_endpoint_words_then_lines_then_the_ask_and_no_write_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Recording.tasks, _Recording.tools = [], []
    app = App(tmp_path, monkeypatch, _Recording)
    (app.folder / "a.py").write_text("x = 0\nx = 1\nx = 3\n", encoding="utf-8")
    got = app.client.post(
        "/api/code/turn",
        json={
            "message": "make it two",
            "workspace": str(app.folder),
            "stream": False,
            "posture": {"reach": "workspace", "approval": "never"},
            "context": [RANGE],
            "propose_change": True,
        },
    )
    assert got.status_code == 200, got.text[:400]

    assert _Recording.tasks, "the agent never ran"
    task = _Recording.tasks[-1]
    assert task.startswith("make it two\n\n")
    # Only the selected line, fenced as data, by the mention door.
    assert f"{FENCE_OPEN}\nx = 1\n{FENCE_CLOSE}" in task
    assert "x = 0" not in task and "x = 3" not in task
    ask = task.index("Propose a replacement for exactly lines 2-2 of `a.py`")
    assert ask > task.index(FENCE_CLOSE)
    handed = set(_Recording.tools[-1])
    assert handed, "the registry exposed no names to check"
    assert not handed & set(WRITE_TOOLS), f"a proposal turn was handed {sorted(handed & set(WRITE_TOOLS))}"


def test_a_plain_turn_carries_no_proposal_sentence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _Recording.tasks, _Recording.tools = [], []
    app = App(tmp_path, monkeypatch, _Recording)
    (app.folder / "a.py").write_text("x = 0\nx = 1\n", encoding="utf-8")
    got = app.client.post(
        "/api/code/turn",
        json={"message": "why one?", "workspace": str(app.folder), "stream": False, "context": [RANGE]},
    )
    assert got.status_code == 200, got.text[:400]
    assert "Propose a replacement" not in _Recording.tasks[-1]
