"""After every browser action the Code screen gets a picture of the page — and nobody else pays for it.

Item 2 of the list audited on 2026-09-16 (the cockpit). The agent's browser ran headless and the
person saw its work as text — the tag tree the model reads — or, with `CHIMERA_BROWSER_HEADLESS`
off, as a second Chromium window beside the app. Neither is "watching the agent open the site and
type the dates". Measured before wiring (2026-09-17): a viewport JPEG at quality 55 is 13–96 KB and
24–106 ms to capture, so one frame per action costs about a megabyte over a turn that browsed
twenty times and nothing while the browser is idle.

Pinned: the tool announces a frame after each action, success or failure, to a sink the assembly
bound — and captures nothing when no sink is bound (a headless run never asks the driver); the
turn's stream carries the frame as base64 with the page's address, action and a count; the run log,
which exists to replay words a dropped connection lost, never stores a frame.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from chimera.api.code_api import CodeSeams, assemble_registry
from chimera.config import Settings
from chimera.providers.gateway import LLMGateway
from chimera.tools import browser as browser_mod
from chimera.tools.browser import BrowserFrame, BrowserTool, Element, FrameAnnouncer

JPEG = b"\xff\xd8\xff\xe0stub-jpeg"


class _Driver:
    """The browser test's fake driver, plus `frame()` — and a count of how often it was asked."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.frames_asked = 0
        self.url = "https://example.com/"
        self.fail_frames = False

    def navigate(self, url: str) -> list[Element]:
        self.calls.append(f"navigate:{url}")
        self.url = url
        return [Element("e1", "link", "Docs")]

    def read(self) -> list[Element]:
        return [Element("e1", "link", "Docs")]

    def click(self, ref: str) -> list[Element]:
        if ref != "e1":
            raise KeyError(f"unknown ref {ref!r}")
        return [Element("e2", "heading", "Docs")]

    def type_text(self, ref: str, text: str) -> list[Element]:
        return [Element("e1", "link", "Docs")]

    def back(self) -> list[Element]:
        return [Element("e1", "link", "Docs")]

    def page_html(self) -> str:
        return "<html><body>x</body></html>"

    def page_text(self) -> str:
        return "x"

    def screenshot(self, path: str) -> None:
        Path(path).write_bytes(b"\x89PNG")

    def frame(self) -> BrowserFrame | None:
        self.frames_asked += 1
        if self.fail_frames:
            raise RuntimeError("no frame today")
        return BrowserFrame(jpeg=JPEG, url=self.url, title="Example", width=1280, height=720)

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _offline_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    import chimera.scrape.ssrf as ssrf

    monkeypatch.setattr(ssrf, "_resolve_ips", lambda host: ["93.184.216.34"])


# ------------------------------------------------------------------ the tool


def test_a_frame_follows_every_action_success_or_failure(tmp_path: Path) -> None:
    driver = _Driver()
    tool = BrowserTool(driver=driver, workspace=tmp_path)
    seen: list[tuple[str, BrowserFrame]] = []
    tool.on_frame = lambda action, frame: seen.append((action, frame))

    tool.run(action="navigate", url="https://example.com/docs")
    out = tool.run(action="click", ref="e9")  # fails: unknown ref

    assert out.startswith("error:")
    assert [a for a, _ in seen] == ["navigate", "click"], "the failed click still shows its page"
    assert seen[0][1].url == "https://example.com/docs" and seen[0][1].jpeg == JPEG
    assert seen[0][1].width == 1280 and seen[0][1].title == "Example"


def test_without_a_sink_the_driver_is_never_asked_for_a_picture(tmp_path: Path) -> None:
    driver = _Driver()
    tool = BrowserTool(driver=driver, workspace=tmp_path)
    tool.run(action="navigate", url="https://example.com/")
    tool.run(action="read")
    assert driver.frames_asked == 0, "a headless run paid for a screen it does not have"


def test_a_capture_that_fails_never_reaches_the_observation(tmp_path: Path) -> None:
    driver = _Driver()
    driver.fail_frames = True
    tool = BrowserTool(driver=driver, workspace=tmp_path)
    seen: list[Any] = []
    tool.on_frame = lambda *a: seen.append(a)

    out = tool.run(action="navigate", url="https://example.com/")

    assert "no frame today" not in out and "[e1] link: Docs" in out
    assert seen == []


def test_the_announcer_drops_frames_until_a_screen_binds() -> None:
    announcer = FrameAnnouncer()
    announcer("navigate", BrowserFrame(JPEG, "u", "t", 1, 1))  # nobody bound: dropped, no error
    got: list[Any] = []
    announcer.emit = lambda action, frame: got.append(action)
    announcer("click", BrowserFrame(JPEG, "u", "t", 1, 1))
    assert got == ["click"]


# ------------------------------------------------------------------ through the registry the Code screen builds


def _assemble(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sink: Any) -> tuple[Any, _Driver]:
    driver = _Driver()
    monkeypatch.setattr(browser_mod, "_new_playwright_driver", lambda headless: driver)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))  # type: ignore[call-arg]
    registry, _ = assemble_registry(
        CodeSeams(), ws, settings, LLMGateway(), steps=4, surface="api:turn", frame_sink=sink
    )
    return registry, driver


def test_the_assembly_hands_the_browser_its_sink_only_when_there_is_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = FrameAnnouncer()
    got: list[str] = []
    sink.emit = lambda action, frame: got.append(action)
    registry, driver = _assemble(tmp_path, monkeypatch, sink)

    registry.get("browser").run(action="navigate", url="https://example.com/")
    assert got == ["navigate"] and driver.frames_asked == 1

    (tmp_path / "b").mkdir()
    headless, driver2 = _assemble(tmp_path / "b", monkeypatch, None)
    headless.get("browser").run(action="navigate", url="https://example.com/")
    assert driver2.frames_asked == 0


# ------------------------------------------------------------------ the turn's stream, and the run log


def test_the_turn_streams_the_frame_and_the_run_log_keeps_no_picture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from chimera.api import build_api_app
    from chimera.core.agent import AgentResult
    from chimera.interface import ChatSession
    from chimera.orchestration import runlog

    driver = _Driver()
    monkeypatch.setattr(browser_mod, "_new_playwright_driver", lambda headless: driver)

    class _Browsing:
        """An agent whose one step navigates through the registry it was handed."""

        def __init__(self, *_a: Any, **_k: Any) -> None:
            self.tools = _a[1] if len(_a) > 1 else _k.get("tools")

        def run(self, task: str, **_: Any) -> AgentResult:
            self.tools.get("browser").run(action="navigate", url="https://example.com/docs")
            return AgentResult(
                answer="browsed",
                steps=1,
                stopped_reason="final",
                transcript=[
                    {"role": "user", "content": task},
                    {"role": "assistant", "content": "browsed"},
                ],
                model="test/model",
            )

    import chimera.core

    monkeypatch.setattr(chimera.core, "Agent", _Browsing, raising=True)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    from chimera.config import get_settings

    get_settings.cache_clear()
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))  # type: ignore[call-arg]
    client = TestClient(
        build_api_app(lambda: ChatSession(_Browsing()), workspace=ws, settings=settings)
    )

    response = client.post("/api/code/turn", json={"message": "open the docs"})
    event, frames, turn_id = "", [], ""
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            payload = json.loads(line[len("data: ") :])
            if event == "session":
                turn_id = payload.get("turn_id", "")
            if event == "browser":
                frames.append(payload)

    assert len(frames) == 1, "one action, one frame"
    frame = frames[0]
    assert frame["action"] == "navigate" and frame["url"] == "https://example.com/docs"
    assert base64.b64decode(frame["jpeg"]) == JPEG
    assert frame["width"] == 1280 and frame["height"] == 720 and frame["n"] == 1
    assert frame["seq"] > 0

    # The run log — replay for a dropped connection — holds the words and never the picture.
    logged = [f for f in runlog.frames(Path(settings.home), turn_id, area="code")]
    kinds = [f.get("event") for f in logged]
    assert "done" in kinds and "browser" not in kinds, kinds
    assert not any("jpeg" in json.dumps(f) for f in logged)
