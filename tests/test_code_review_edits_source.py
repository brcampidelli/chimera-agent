"""``/code-review`` with no git diff: the conversation's own edits are the change that is reviewed.

The edits are the ones the transcript's edit cards hold (path and unified patch). What is pinned: a
folder with no git diff reviews those edits and says where the change came from; an edit that is not a
readable diff is named in the card, never skipped silently; and a git diff, when there is one, still wins.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.providers.gateway import CompletionResult
from chimera.review.code_card import edits_review
from chimera.review.family import choose_reviewer

EDIT = """--- a/money.py
+++ b/money.py
@@ -1,2 +1,3 @@
 def total(items):
+    total = 0
     return sum(items)
"""

FINDING = (
    '{"findings": [{"file": "money.py", "line": 2, "priority": "P2", "title": "shadowed",'
    ' "evidence": "total = 0", "consequence": "wrong", "confidence": 0.9}]}'
)


class _Fake:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def complete(self, messages: Any, **_: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(content=self.text, model="fake/reviewer")


def test_edits_become_one_change_per_path_in_the_order_made() -> None:
    diff, patches, unreadable = edits_review([("money.py", EDIT), ("money.py", EDIT), ("notes.txt", "x")])
    assert [f.path for f in diff.files] == ["money.py"]
    assert diff.files[0].anchors(2)
    assert list(patches) == ["money.py"]
    assert patches["money.py"] == EDIT  # the same patch twice is kept once
    assert unreadable == ["notes.txt"]
    assert diff.base_label == "edições da conversa"


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: str, ws: Path) -> tuple[TestClient, _Fake]:
    import chimera.api.commands_api as commands_api
    from chimera.api import build_api_app
    from chimera.interface import ChatSession

    fake = _Fake(reply)
    monkeypatch.setattr(Settings, "can_answer", lambda self: True)
    monkeypatch.setattr(commands_api, "LLMGateway", lambda *_a, **_k: fake)
    monkeypatch.setattr(
        commands_api, "reviewer_for",
        lambda author, explicit="": choose_reviewer(author, explicit="other/reviewer"),
    )
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))

    class _Idle:
        def run(self, task: str, **_: Any) -> Any:  # pragma: no cover - the route never runs a turn
            raise AssertionError("/code-review must not start a turn")

    app = build_api_app(lambda: ChatSession(_Idle()), workspace=ws, settings=settings)  # type: ignore[arg-type]
    return TestClient(app), fake


def test_a_folder_with_no_git_reviews_the_conversations_edits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    client, fake = _client(tmp_path, monkeypatch, FINDING, plain)
    response = client.post(
        "/api/code/review",
        json={"workspace": str(plain), "edits": [{"path": "money.py", "patch": EDIT}]},
    )
    body = response.json()
    assert response.status_code == 200, response.text
    assert body["status"] == "findings"
    assert body["base"] == "edições da conversa"
    assert body["files"][0]["file"] == "money.py"
    assert body["files"][0]["patch"] == EDIT
    assert fake.calls >= 1


def test_an_edit_that_is_not_a_diff_is_named_and_nothing_is_reviewed_from_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    client, fake = _client(tmp_path, monkeypatch, FINDING, plain)
    body = client.post(
        "/api/code/review",
        json={"workspace": str(plain), "edits": [{"path": "notes.txt", "patch": "just prose"}]},
    ).json()
    assert body["status"] == "incomplete"
    assert "not readable as a diff: notes.txt" in body["notes"]
    assert fake.calls == 0


def test_a_git_diff_still_wins_over_the_conversations_edits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "ws"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
            cwd=repo, check=True, capture_output=True,
        )

    git("init", "-q", "-b", "main")
    (repo / "money.py").write_text("def total(items):\n    return sum(items)\n", encoding="utf-8")
    git("add", "money.py")
    git("commit", "-q", "-m", "base")
    (repo / "money.py").write_text("def total(items):\n    total = 0\n    return sum(items)\n", encoding="utf-8")
    client, _fake = _client(tmp_path, monkeypatch, FINDING, repo)
    body = client.post(
        "/api/code/review",
        json={"workspace": str(repo), "edits": [{"path": "other.py", "patch": EDIT}]},
    ).json()
    assert body["base"] == "merge base with main"
    assert [f["file"] for f in body["files"]] == ["money.py"]
