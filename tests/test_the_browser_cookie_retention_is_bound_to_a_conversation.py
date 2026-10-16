"""Cookie retention (`CHIMERA_BROWSER_COOKIE_RETENTION`) resolves to the right profile, or to none.

Three choices: one profile shared by every conversation, one per conversation, or an ephemeral
context whose cookies die with the browser. What these tests pin:

- the per-conversation folder is a digest of the id, inside the data folder's profile root — an id
  from a request body (`../..`) cannot climb out, and two conversations never share a folder;
- a surface with no conversation (cron, a bot, the terminal) browses EPHEMERAL under the
  per-conversation choice, never shared — falling back to shared would hand one conversation's
  sign-ins to all of them;
- ephemeral writes nothing under the profile root and closes the context it opened;
- deleting a conversation deletes its profile.

No real browser is started: Playwright is replaced by a recorder.
"""

from __future__ import annotations

import hashlib
import sys
import threading
import types
from pathlib import Path
from typing import Any

import pytest

from chimera.tools import browser as browser_module
from chimera.tools.browser import (
    BROWSER_CONVERSATION_ID,
    BrowserTool,
    bound_to_conversation,
    cookie_profile_dir,
    forget_conversation_profile,
)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_shared_cookie_retention_uses_one_stable_profile(tmp_path: Path) -> None:
    root = tmp_path / "profiles"

    assert cookie_profile_dir("shared", root, "conversation-a") == root / "shared"
    assert cookie_profile_dir("shared", root, "conversation-b") == root / "shared"
    assert cookie_profile_dir("shared", root, None) == root / "shared"


def test_conversation_cookie_retention_hashes_and_separates_session_ids(tmp_path: Path) -> None:
    root = tmp_path / "profiles"

    first = cookie_profile_dir("conversation", root, "conversation-a")
    second = cookie_profile_dir("conversation", root, "conversation-b")

    assert first == root / _digest("conversation-a")
    assert second == root / _digest("conversation-b")
    assert first != second
    assert "conversation-a" not in str(first)


@pytest.mark.parametrize("hostile", ["../../outside", "..\\..\\outside", "/etc/passwd", "C:\\x", "shared"])
def test_a_hostile_conversation_id_stays_inside_the_profile_root(tmp_path: Path, hostile: str) -> None:
    root = tmp_path / "profiles"

    folder = cookie_profile_dir("conversation", root, hostile)

    assert folder is not None
    assert folder.parent == root  # one level below the root, whatever the id spelled
    # And never the shared profile, even for an id that spells its name.
    assert folder != cookie_profile_dir("shared", root, None)


@pytest.mark.parametrize("conversation_id", [None, "", "  "])
def test_no_conversation_means_ephemeral_never_shared(tmp_path: Path, conversation_id: str | None) -> None:
    assert cookie_profile_dir("conversation", tmp_path, conversation_id) is None


def test_ephemeral_cookie_retention_does_not_create_a_profile(tmp_path: Path) -> None:
    assert cookie_profile_dir("ephemeral", tmp_path, "conversation-a") is None


def test_unknown_cookie_retention_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be shared, conversation, or ephemeral"):
        cookie_profile_dir("sometimes", tmp_path, "conversation-a")


def test_persistent_retention_requires_profile_root() -> None:
    with pytest.raises(ValueError, match="require a profile root"):
        cookie_profile_dir("shared", None, None)


def _record_drivers(monkeypatch: pytest.MonkeyPatch) -> list[Path | None]:
    seen: list[Path | None] = []

    def build_driver(headless: bool, **options: Any) -> object:
        seen.append(options.get("storage_dir"))
        return object()

    monkeypatch.setattr(browser_module, "_new_playwright_driver", build_driver)
    return seen


def test_the_tool_reads_the_current_conversation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _record_drivers(monkeypatch)
    tool = BrowserTool(cookie_retention="conversation", profile_root=tmp_path)
    token = BROWSER_CONVERSATION_ID.set("stable-session-id")
    try:
        tool._new_driver()
    finally:
        BROWSER_CONVERSATION_ID.reset(token)

    assert seen == [tmp_path / _digest("stable-session-id")]


def test_a_turn_on_its_own_thread_still_knows_its_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Code turn runs on `threading.Thread(target=work)`; a new thread starts with an empty
    context, so without `bound_to_conversation` it would browse as "no conversation"."""
    seen = _record_drivers(monkeypatch)
    tool = BrowserTool(cookie_retention="conversation", profile_root=tmp_path)

    plain = threading.Thread(target=tool._new_driver)
    plain.start()
    plain.join()
    bound = threading.Thread(target=bound_to_conversation("turn-session", tool._new_driver))
    bound.start()
    bound.join()

    assert seen == [None, tmp_path / _digest("turn-session")]
    assert BROWSER_CONVERSATION_ID.get() is None  # nothing leaked into this thread's context


def test_an_ephemeral_unrestricted_browser_makes_the_call_it_always_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[bool, dict[str, Any]]] = []

    def build_driver(headless: bool, **options: Any) -> object:
        calls.append((headless, options))
        return object()

    monkeypatch.setattr(browser_module, "_new_playwright_driver", build_driver)
    BrowserTool(profile_root=tmp_path)._new_driver()

    assert calls == [(True, {})]


class _FakeContext:
    def __init__(self, log: list[str], pages: int) -> None:
        self.log = log
        self.pages: list[Any] = [_FakePage(log) for _ in range(pages)]

    def on(self, event: str, handler: Any) -> None:
        pass

    def new_page(self) -> Any:
        page = _FakePage(self.log)
        self.pages.append(page)
        return page

    def new_cdp_session(self, page: Any) -> Any:
        return types.SimpleNamespace(send=lambda *a, **k: None, on=lambda *a, **k: None)

    def close(self) -> None:
        self.log.append("context.close")


class _FakePage:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def close(self) -> None:
        self.log.append("page.close")

    def route(self, *args: Any, **kwargs: Any) -> None:
        pass

    def on(self, *args: Any, **kwargs: Any) -> None:
        pass


def _fake_playwright(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    log: list[str] = []

    class _Browser:
        def new_context(self) -> _FakeContext:
            log.append("new_context")
            return _FakeContext(log, pages=0)

        def close(self) -> None:
            log.append("browser.close")

    class _Chromium:
        def launch(self, headless: bool) -> _Browser:
            log.append("launch")
            return _Browser()

        def launch_persistent_context(self, user_data_dir: str, headless: bool) -> _FakeContext:
            log.append(f"persistent:{user_data_dir}")
            return _FakeContext(log, pages=1)  # Chromium opens a blank page of its own

    class _Started:
        chromium = _Chromium()

        def stop(self) -> None:
            log.append("stop")

    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = lambda: types.SimpleNamespace(start=lambda: _Started())  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    return log


def test_clear_on_close_writes_no_profile_and_closes_what_it_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.tools.browser_playwright import PlaywrightDriver

    log = _fake_playwright(monkeypatch)
    root = tmp_path / "browser-profiles"
    storage = cookie_profile_dir("ephemeral", root, "conversation-a")

    driver = PlaywrightDriver(headless=True, storage_dir=storage)
    driver.close()

    assert log[:2] == ["launch", "new_context"]  # a non-persistent context: nothing on disk
    assert not root.exists()
    assert log[-3:] == ["context.close", "browser.close", "stop"]


def test_a_conversation_profile_is_created_inside_the_root_and_its_stray_page_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.tools.browser_playwright import PlaywrightDriver

    log = _fake_playwright(monkeypatch)
    root = tmp_path / "browser-profiles"
    storage = cookie_profile_dir("conversation", root, "conversation-a")
    assert storage is not None

    driver = PlaywrightDriver(headless=True, storage_dir=storage)

    assert log[0] == f"persistent:{storage}"
    assert storage.is_dir() and storage.parent == root
    assert "page.close" in log  # the persistent context's own unguarded blank page is gone
    driver.close()
    assert log[-2:] == ["context.close", "stop"]


def test_deleting_a_conversation_deletes_its_profile_and_only_its_profile(tmp_path: Path) -> None:
    root = tmp_path / browser_module.PROFILES_DIRNAME
    mine = cookie_profile_dir("conversation", root, "mine")
    theirs = cookie_profile_dir("conversation", root, "theirs")
    shared = cookie_profile_dir("shared", root, None)
    for folder in (mine, theirs, shared):
        assert folder is not None
        (folder / "Default").mkdir(parents=True)
        (folder / "Default" / "Cookies").write_bytes(b"session")

    assert forget_conversation_profile(tmp_path, "mine") is True
    assert mine is not None and not mine.exists()
    assert theirs is not None and theirs.is_dir()
    assert shared is not None and shared.is_dir()
    assert forget_conversation_profile(tmp_path, "mine") is False  # a second delete finds nothing
    assert forget_conversation_profile(tmp_path, "") is False


# --- the routes: a Code turn browses as its conversation, and a delete takes the profile along ---


class _Recording:
    seen: list[str | None] = []

    def __init__(self, *_a: Any, **_kw: Any) -> None:
        pass

    def run(self, task: str, **_kw: Any) -> Any:
        from chimera.core.agent import AgentResult

        type(self).seen.append(BROWSER_CONVERSATION_ID.get())
        return AgentResult(
            answer="ok", steps=1, stopped_reason="final",
            transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "ok"}],
        )


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Path, Path]:
    from fastapi.testclient import TestClient

    import chimera.core
    from chimera.api import build_api_app
    from chimera.config import Settings, get_settings
    from chimera.interface import ChatSession

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _Recording, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")
    app = build_api_app(lambda: ChatSession(_Recording()), workspace=ws, settings=settings)
    return TestClient(app), home, ws


def _code_turn(client: Any, ws: Path) -> str:
    import re

    text = client.post("/api/code/turn", json={"message": "hi", "workspace": str(ws), "stream": False}).text
    return re.search(r'"session_id": ?"([^"]+)"', text).group(1)  # type: ignore[union-attr]


def _plant_profile(home: Path, conversation_id: str) -> Path:
    folder = cookie_profile_dir("conversation", home / browser_module.PROFILES_DIRNAME, conversation_id)
    assert folder is not None
    (folder / "Default").mkdir(parents=True)
    return folder


def test_a_code_turn_browses_as_its_conversation_and_deleting_it_drops_the_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Recording.seen = []
    client, home, ws = _client(tmp_path, monkeypatch)
    session_id = _code_turn(client, ws)

    assert _Recording.seen == [session_id]  # the worker thread was handed its conversation
    folder = _plant_profile(home, session_id)
    other = _plant_profile(home, "someone-else")

    assert client.delete(f"/api/code/sessions/{session_id}").json() == {"ok": True}
    assert not folder.exists()
    assert other.is_dir()


def test_deleting_a_project_drops_the_profiles_of_its_conversations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, home, ws = _client(tmp_path, monkeypatch)
    session_id = _code_turn(client, ws)
    folder = _plant_profile(home, session_id)

    assert client.delete("/api/code/projects", params={"workspace": str(ws)}).json()["deleted"] >= 1
    assert not folder.exists()


def test_deleting_a_chat_session_drops_its_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, home, _ws = _client(tmp_path, monkeypatch)
    session_id = client.post("/api/sessions").json()["id"]
    folder = _plant_profile(home, session_id)

    client.delete(f"/api/sessions/{session_id}")
    assert not folder.exists()


def test_a_chat_turn_browses_as_its_conversation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _Recording.seen = []
    client, _home, _ws = _client(tmp_path, monkeypatch)
    session_id = client.post("/api/sessions").json()["id"]

    client.post("/api/chat/stream", json={"message": "hi", "session_id": session_id})

    assert _Recording.seen == [session_id]


# --- the setting: saved only with a value the browser reads, and read into the data folder ---


def test_the_screen_cannot_save_a_retention_the_browser_would_refuse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from chimera.api.config_api import patch_config, read_config
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("CHIMERA_BROWSER_COOKIE_RETENTION", "ephemeral")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="shared, conversation, or ephemeral"):
        patch_config({"CHIMERA_BROWSER_COOKIE_RETENTION": "forever"}, env_path=tmp_path / ".env")
    assert not (tmp_path / ".env").exists()
    patch_config({"CHIMERA_BROWSER_COOKIE_RETENTION": "conversation"}, env_path=tmp_path / ".env")
    assert read_config(get_settings())["browser"]["cookie_retention"] == "conversation"
    get_settings.cache_clear()


def test_the_registry_keeps_profiles_inside_the_data_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.config import get_settings
    from chimera.tools import default_registry

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_BROWSER_COOKIE_RETENTION", "shared")
    get_settings.cache_clear()
    try:
        tool = default_registry(tmp_path).get("browser")
    finally:
        get_settings.cache_clear()

    assert isinstance(tool, BrowserTool)
    assert tool.cookie_retention == "shared"
    assert tool.profile_root == home / browser_module.PROFILES_DIRNAME
