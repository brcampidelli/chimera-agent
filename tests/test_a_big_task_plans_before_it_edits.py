"""A big task plans before it edits: the rule, the turn that enforces it, and the bridge that asks.

Three claims, each pinned where it could go wrong:

* ``is_big_task`` is a deterministic rule. Three named files or a feature/refactor makes a task
  big; the noise a naive file counter reads as files (``UI/UX``, ``e/ou``, ``Next.js``, ``v1.2``,
  ``e.g``) does not. The model never decides this.
* Through ``/api/code/turn``, a big task stops on its plan with no model turn run before the
  person answers. An explicit ``plan_gate: false`` wins over the rule. The approved plan reaches the
  model of the run, and the receipt says why the turn planned.
* The bridge's ``desktop_send`` offers ``plan_first`` and forwards it as the screen's toggle does.
  It cannot grant anything: ``plan_first`` only ever adds a stop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api.big_task import FILE_THRESHOLD, is_big_task
from chimera.api.plan_gate import _PLAN_GATE_SYSTEM
from chimera.config import Settings
from chimera.core import Agent
from chimera.core.code_session import CodeSession
from chimera.interface import ChatSession
from chimera.providers.gateway import CompletionResult
from chimera.server.desktop_mcp import DesktopMCP
from chimera.tools import ToolRegistry

PLAN = "1. read a.py\n2. edit a.py\n3. run pytest"
_OMIT = object()


@dataclass
class _Reply:
    content: str


# ------------------------------------------------------------------------------- the rule


def test_three_named_files_make_a_big_task() -> None:
    big, reason = is_big_task("edit chimera/api/a.py, tests/test_a.py and README.md")
    assert big is True
    assert reason.startswith("names 3 files:")


def test_two_named_files_do_not_make_a_big_task() -> None:
    assert is_big_task("fix chimera/api/a.py and tests/test_a.py") == (False, "")


def test_a_trailing_full_stop_does_not_hide_the_third_file() -> None:
    big, _ = is_big_task("fix a.py, b.py and c.py.")
    assert big is True
    assert FILE_THRESHOLD == 3, "the owner's threshold: two files is a small fix"


@pytest.mark.parametrize(
    "message",
    [
        "refatorar o módulo de cache",
        "refactor the cache module",
        "implementar a nova funcionalidade de exportação",
        "add a feature for CSV export",
    ],
)
def test_a_feature_or_a_refactor_makes_a_big_task_in_either_language(message: str) -> None:
    big, reason = is_big_task(message)
    assert big is True
    assert reason.startswith("a feature or a refactor:")


@pytest.mark.parametrize(
    "message",
    [
        "fix typo in README",
        "why does the parser return None here?",
        "explain the implementation of the retry loop",
        "rename the variable x to count",
        # The bare noun is not an action: a feature that works is a small fix.
        "the feature works, fix typo in README",
        "why does the feature flag return None",
        "explain the features of the app",
        "por que a funcionalidade retorna None?",
        "o que faz a funcionalidade de exportação?",
    ],
)
def test_a_small_request_is_not_big(message: str) -> None:
    assert is_big_task(message) == (False, "")


@pytest.mark.parametrize(
    "message",
    [
        "adicione uma funcionalidade de exportação",
        "adicione uma funcionalidade de exportacao",
        "Refatoracao do modulo de cache",
        "refatoração do módulo de cache",
        "crie um recurso de busca",
    ],
)
def test_portuguese_actions_and_unaccented_spellings_are_big(message: str) -> None:
    big, reason = is_big_task(message)
    assert big is True
    assert reason.startswith("a feature or a refactor:")


@pytest.mark.parametrize(
    ("message", "big"),
    [
        # The owner's own example: an imperative implement verb with an object is a feature.
        ("implemente a tela X", True),
        ("Implementar a feature de login", True),
        ("adicione uma funcionalidade de exportação", True),
        ("refatoracao do modulo", True),
        # Questions are not work, whatever words they use.
        ("why does the feature flag return None", False),
        ("explain the features of the app", False),
        # Small requests.
        ("fix typo in README", False),
        ("corrija o bug no login", False),
        ("adicione um teste", False),
        ("atualize o README", False),
    ],
)
def test_the_owners_examples_are_decided_as_the_owner_put_them(message: str, big: bool) -> None:
    assert is_big_task(message)[0] is big


def test_a_line_suffix_names_the_file_and_is_not_its_extension() -> None:
    big, reason = is_big_task("fix a.ts:42, b.ts:7 and c.py:3:9")
    assert big is True
    assert reason == "names 3 files: a.ts, b.ts, c.py"
    assert is_big_task("fix a.ts:42 only") == (False, "")


def test_the_noise_a_naive_counter_reads_as_files_is_not_counted() -> None:
    """Five false positives and one real file: still one file, so not big."""
    noise = "Compare UI/UX, e/ou, Next.js, v1.2 and e.g. the screens, then fix a.py"
    assert is_big_task(noise) == (False, "")


def test_a_product_name_with_a_source_extension_is_not_a_file() -> None:
    assert is_big_task("upgrade Next.js and Vue.js and Node.js") == (False, "")


def test_a_lowercase_js_file_counts_and_a_urls_path_does_not() -> None:
    assert is_big_task("read https://x.dev/a.py then fix b.py and c.py") == (False, "")
    big, _ = is_big_task("fix next.config.js, b.py and c.py")
    assert big is True


def test_an_extensionless_file_counts_when_it_exists_in_the_workspace(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "notes").write_text("x", encoding="utf-8")
    assert is_big_task("edit docs/notes, a.py and b.py", workspace=tmp_path) == (
        True,
        "names 3 files: docs/notes, a.py, b.py",
    )
    # Without the workspace the same words name two files only.
    assert is_big_task("edit docs/notes, a.py and b.py") == (False, "")


def test_a_path_that_escapes_the_workspace_is_not_probed(tmp_path: Path) -> None:
    outside = tmp_path / "secret"
    outside.write_text("x", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    assert is_big_task("edit ../secret, a.py and b.py", workspace=ws) == (False, "")


# ---------------------------------------------------------------- the turn, through the route


class _Gateway:
    """The turn's model. ``ORDER`` records what happened, in order, across the gate and the loop.

    The planning call is told apart by its system prompt, so the test can see that the plan was
    asked for before the person answered, and that the loop's own call came only after the answer.
    """

    plan = PLAN
    turn_messages: list[list[Any]] = []
    ORDER: list[str] = []

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    def complete(self, messages: list[Any], **_k: Any) -> CompletionResult:
        first = messages[0]
        system = first.get("content") if isinstance(first, dict) else first.content
        if system == _PLAN_GATE_SYSTEM:
            _Gateway.ORDER.append("plan")
            return CompletionResult(content=_Gateway.plan, model="test/plan",
                                    prompt_tokens=10, completion_tokens=5)
        _Gateway.ORDER.append("turn")
        _Gateway.turn_messages.append(list(messages))
        return CompletionResult(content="done", model="test/turn",
                                prompt_tokens=10, completion_tokens=5)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Everything a turn touches, reset per test: the order log, the approval, the receipts."""
    _Gateway.ORDER = []
    _Gateway.turn_messages = []
    _Gateway.plan = PLAN
    box: dict[str, Any] = {"approve": True, "receipts": [], "asked": []}

    def ask(home: Any, action: str, reason: str, **kwargs: Any) -> bool:
        _Gateway.ORDER.append("ask")
        box["asked"].append(action)
        return box["approve"]

    original = CodeSession.remember_receipt

    def record(self: CodeSession, receipt: dict[str, Any]) -> None:
        box["receipts"].append(receipt)
        original(self, receipt)

    monkeypatch.setattr(CodeSession, "remember_receipt", record)
    monkeypatch.setattr("chimera.providers.LLMGateway", _Gateway)
    monkeypatch.setattr("chimera.governance.pending.ask_durably", ask)
    return box


def _send(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str,
          plan_gate: Any = _OMIT) -> dict[str, Any]:
    """One Code turn over HTTP. Returns its `done` frame; the receipts are read from the fixture."""
    from chimera.api import build_api_app
    from chimera.config import get_settings

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_PREFIX_NONCE", "")
    get_settings.cache_clear()
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    settings = Settings(CHIMERA_HOME=str(home))  # type: ignore[call-arg]
    client = TestClient(build_api_app(
        lambda: ChatSession(Agent(_Gateway(), ToolRegistry())), workspace=ws, settings=settings
    ))
    body: dict[str, Any] = {"message": message, "stream": False}
    if plan_gate is not _OMIT:
        body["plan_gate"] = plan_gate
    text = client.post("/api/code/turn", json=body).text
    return _done(text)


def _done(text: str) -> dict[str, Any]:
    event, out = "", {}
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: ") and event == "done":
            out = json.loads(line[len("data: "):])
    return out


def _model_saw_the_plan(messages: list[Any]) -> bool:
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else message.content
        if isinstance(content, str) and "approved this plan" in content and "read a.py" in content:
            return True
    return False


def test_a_big_task_stops_on_its_plan_before_any_model_turn_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, harness: dict[str, Any]
) -> None:
    done = _send(tmp_path, monkeypatch, "refatorar o módulo de cache")

    assert _Gateway.ORDER == ["plan", "ask", "turn"], "the plan is asked, answered, then the work"
    assert harness["asked"], "the person was asked about the plan"
    assert _model_saw_the_plan(_Gateway.turn_messages[0]), "the approved plan reaches the run"
    assert done.get("stopped_reason") != "plan_gate:refused"
    receipt = harness["receipts"][-1]
    assert receipt["plan_first_reason"].startswith("a feature or a refactor:")


def test_a_refused_big_task_runs_no_model_turn_and_no_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, harness: dict[str, Any]
) -> None:
    harness["approve"] = False

    done = _send(tmp_path, monkeypatch, "refatorar o módulo de cache")

    assert _Gateway.ORDER == ["plan", "ask"], "nothing after the refusal, not even one model turn"
    assert done["stopped_reason"] == "plan_gate:refused"


def test_an_explicit_false_turns_the_rule_off_for_a_big_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, harness: dict[str, Any]
) -> None:
    _send(tmp_path, monkeypatch, "refatorar o módulo de cache", plan_gate=False)

    assert _Gateway.ORDER == ["turn"], "the caller said no, and the rule does not override that"
    assert harness["asked"] == []
    assert "plan_first_reason" not in harness["receipts"][-1]


def test_a_small_task_runs_as_it_always_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, harness: dict[str, Any]
) -> None:
    _send(tmp_path, monkeypatch, "fix typo in README")

    assert _Gateway.ORDER == ["turn"]
    assert "plan_first_reason" not in harness["receipts"][-1]


def test_an_explicit_true_plans_a_small_task_and_says_who_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, harness: dict[str, Any]
) -> None:
    _send(tmp_path, monkeypatch, "fix typo in README", plan_gate=True)

    assert _Gateway.ORDER == ["plan", "ask", "turn"]
    assert harness["receipts"][-1]["plan_first_reason"] == "asked by the caller"


def test_three_files_named_in_the_workspace_make_a_big_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, harness: dict[str, Any]
) -> None:
    _send(tmp_path, monkeypatch, "edit a.py, b.py and c.py")

    assert _Gateway.ORDER[:2] == ["plan", "ask"]
    assert harness["receipts"][-1]["plan_first_reason"].startswith("names 3 files:")


# ----------------------------------------------------------------------------- the bridge


def _bridge(calls: list[Any]) -> DesktopMCP:
    def http(method: str, url: str, token: str, body: Any, timeout: float) -> tuple[int, Any]:
        calls.append(body)
        return 200, {"status": 200, "job": {"done": True}}

    found = {"url": "http://127.0.0.1:1", "token": "t", "full": False}
    return DesktopMCP(discover=lambda: found, http=http)


def test_desktop_send_offers_plan_first_as_a_boolean() -> None:
    send = next(s for s in _bridge([]).tool_specs() if s["name"] == "desktop_send")
    prop = send["inputSchema"]["properties"]["plan_first"]
    assert prop["type"] == "boolean"
    assert "three or more files" in prop["description"]
    assert "plan_first" not in send["inputSchema"].get("required", [])


@pytest.mark.parametrize("value", [True, False])
def test_desktop_send_forwards_plan_first_as_the_screens_plan_gate(value: bool) -> None:
    calls: list[Any] = []
    _bridge(calls).dispatch("desktop_send", {"message": "go", "plan_first": value})
    assert calls[0]["route"] == "conversations.send"
    assert calls[0]["body"]["plan_gate"] is value


def test_desktop_send_without_plan_first_leaves_the_rule_to_decide() -> None:
    calls: list[Any] = []
    _bridge(calls).dispatch("desktop_send", {"message": "go"})
    assert "plan_gate" not in calls[0]["body"]


def test_desktop_send_refuses_a_plan_first_that_is_not_a_boolean() -> None:
    calls: list[Any] = []
    text = _bridge(calls).dispatch("desktop_send", {"message": "go", "plan_first": "false"})
    assert text == "plan_first must be true or false."
    assert calls == [], "a string never reaches the app, where 'false' would read as true"


# ----------------------------------------------------- only an attended turn is ever stopped by the rule


def test_the_rule_never_stops_an_unattended_turn(tmp_path: Path) -> None:
    """A scheduled, channel, background or author turn has nobody to ask: no automatic stop."""
    from chimera.api.code_api import CodeTurnRequest, _plan_first_decision

    req = CodeTurnRequest.model_validate({"message": "refatore o módulo de cache"})
    assert _plan_first_decision(req, tmp_path, attended=False) == (False, "")
    assert _plan_first_decision(req, tmp_path, attended=True)[0] is True


def test_an_explicit_true_still_plans_an_unattended_turn(tmp_path: Path) -> None:
    from chimera.api.code_api import CodeTurnRequest, _plan_first_decision

    req = CodeTurnRequest.model_validate({"message": "fix a.py", "plan_gate": True})
    assert _plan_first_decision(req, tmp_path, attended=False) == (True, "asked by the caller")


def test_a_spoken_big_task_does_not_stop_on_the_plan(tmp_path: Path) -> None:
    """A voice turn's answer is read aloud and nobody watches the card: no automatic stop, even
    when the turn is attended. The explicit true still plans it, as with any turn."""
    from chimera.api.code_api import CodeTurnRequest, _plan_first_decision

    spoken = CodeTurnRequest.model_validate(
        {"message": "refatorar o módulo de cache", "spoken": True}
    )
    assert _plan_first_decision(spoken, tmp_path, attended=True) == (False, "")
    asked = CodeTurnRequest.model_validate(
        {"message": "refatorar o módulo de cache", "spoken": True, "plan_gate": True}
    )
    assert _plan_first_decision(asked, tmp_path, attended=True)[0] is True


# ----------------------------------------------------------- the reason shows on the card


def test_the_card_says_why_the_rule_stopped_the_turn() -> None:
    from chimera.api import plan_gate

    class _Plan:
        def complete(self, messages: list[Any], **_k: Any) -> _Reply:
            return _Reply("1. read a.py\n2. edit a.py")

    seen: list[str] = []

    def ask(home: Any, action: str, reason: str, **kwargs: Any) -> bool:
        seen.append(action)
        return True

    plan_gate.gate(
        "refatore", home=Path("."), backend=_Plan(), wait_seconds=1.0, ask=ask,
        why="Plan first: a feature or a refactor: 'refatorar' (big-task rule).",
    )
    assert seen[0].startswith("Plan first: a feature or a refactor")
    assert "1. read a.py" in seen[0], "the plan itself is still on the card"
