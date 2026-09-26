"""Every conversation a person has with Chimera reads ``CHIMERA_CHAT_REAL_HISTORY``.

The setting is one line in `.env`, and whether it is flipped is a decision taken on a measurement
(`bench/chat_history`). A surface that built its `ChatSession` without passing it would keep the
flattened form after the flip with nothing to say so. That is how the owner's identity came to be
missing from 13 surfaces (`tests/test_the_owner_is_heard_on_every_surface.py`), and why this check
is structural: every `ChatSession(...)` in the package passes ``real_history=`` or says why not.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Built without the setting, each with the reason.
EXEMPT: dict[tuple[str, str], str] = {
    ("chimera/api/schema_dump.py", "main"): "builds an app to print its schema; no conversation",
    ("chimera/cli/main.py", "_right_hand_builder"): "the scenarios bench; the session is the instrument",
}


def _chat_sessions() -> list[tuple[str, str, ast.Call]]:
    found: list[tuple[str, str, ast.Call]] = []
    for path in sorted((ROOT / "chimera").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for top in tree.body:
            name = getattr(top, "name", None)
            if name is None:
                continue
            for node in ast.walk(top):
                func = getattr(node, "func", None)
                called = getattr(func, "id", None) or getattr(func, "attr", None)
                if isinstance(node, ast.Call) and called == "ChatSession":
                    found.append((rel, name, node))
    return found


def test_every_chat_session_passes_the_history_setting_or_says_why_not() -> None:
    sessions = _chat_sessions()
    assert len(sessions) >= 8, "the scan found too few sessions to be scanning the package"
    missing = [
        f"{rel}:{node.lineno} in {name}"
        for rel, name, node in sessions
        if (rel, name) not in EXEMPT and not any(kw.arg == "real_history" for kw in node.keywords)
    ]
    assert not missing, f"ChatSession built without real_history=: {missing}"


def test_every_exemption_still_names_a_session_that_exists() -> None:
    present = {(rel, name) for rel, name, _node in _chat_sessions()}
    assert set(EXEMPT) <= present, f"stale exemptions: {set(EXEMPT) - present}"
