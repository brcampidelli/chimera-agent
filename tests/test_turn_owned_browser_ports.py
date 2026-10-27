"""A browser may reach only loopback listeners started by this turn's workspace jobs."""

from __future__ import annotations

import socket
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from chimera.core.jobs import JobRegistry
from chimera.governance.governed_tool import govern_registry
from chimera.governance.kernel import TrustKernel
from chimera.governance.ledger import TaintLedger
from chimera.governance.ledger_tool import LedgeredTool, ledger_registry
from chimera.sandbox import LocalSandbox
from chimera.tools.base import Tool
from chimera.tools.browser import BrowserTool
from chimera.tools.browser_playwright import RequestGuard
from chimera.tools.browser_reach import CHIMERA, OTHER, BrowserReach
from chimera.tools.builtin import make_browser_tool
from chimera.tools.jobs import process_tree_listening_ports
from chimera.tools.registry import ToolRegistry
from chimera.tools.shell import RunShellTool


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _live_server(tmp_path: Path) -> tuple[JobRegistry, RunShellTool, str, int]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    jobs = JobRegistry(tmp_path / "home")
    shell = RunShellTool(workspace, LocalSandbox(), confirm=None, jobs=jobs)
    port = _free_port()
    script = (
        "import http.server; "
        f"http.server.ThreadingHTTPServer(('127.0.0.1', {port}), "
        "http.server.SimpleHTTPRequestHandler).serve_forever()"
    )
    command = f'"{sys.executable}" -c "{script}"'
    out = shell.run(command=command, background=True)
    assert out.startswith("job "), out
    job_id = out.split()[1]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process_tree_listening_ports(jobs, workspace, current_job_ids=shell.turn_job_ids):
            return jobs, shell, job_id, port
        time.sleep(0.05)
    pytest.fail("the run_shell background job did not open its loopback listener")


def _reach(jobs: JobRegistry, shell: RunShellTool, workspace: Path, *, owned: frozenset[int] = frozenset(), answers: Any = None) -> BrowserReach:
    return BrowserReach(
        home=workspace,
        floor=lambda _url: False,
        owned=lambda: owned,
        answers=answers or (lambda _url: OTHER),
        turn_ports=lambda: process_tree_listening_ports(
            jobs, workspace, current_job_ids=shell.turn_job_ids
        ),
    )
def test_registry_mounts_without_psutil_and_turn_ports_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import builtins

    from chimera.tools.builtin import default_registry

    original_import = builtins.__import__

    def without_psutil(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "psutil":
            raise ImportError("psutil unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_psutil)
    registry = default_registry(tmp_path, host_exec_confirm=None)
    assert registry.get("browser") is not None
    jobs = JobRegistry(tmp_path / "home")
    assert process_tree_listening_ports(jobs, tmp_path, current_job_ids=set()) == frozenset()


def test_registry_browser_adds_turn_reach_lazily_and_recreates_driver(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from chimera.config import Settings
    from chimera.tools import browser as browser_module

    settings = Settings(CHIMERA_HOME=str(tmp_path))  # type: ignore[arg-type]
    live_ports: set[int] = set()
    created: list[_LazyReachDriver] = []

    def fake_driver(headless: bool, **options: Any) -> _LazyReachDriver:
        driver = _LazyReachDriver(options.get("reach"))
        created.append(driver)
        return driver

    monkeypatch.setattr(browser_module, "_new_playwright_driver", fake_driver)
    tool = make_browser_tool(
        tmp_path,
        settings,
        turn_ports=lambda: frozenset(live_ports),
    )
    assert isinstance(tool, BrowserTool)
    assert tool.reach is None

    # First use retains the old unrestricted driver behavior while this turn has no server.
    tool.run(action="read")
    assert len(created) == 1 and created[0].reach is None
    port = 43219
    live_ports.add(port)

    tool.run(action="navigate", url=f"http://localhost:{port}/")
    assert tool.reach is not None
    assert port in tool.reach._declared_ports()
    assert len(created) == 2
    assert created[0].closed
    assert created[1].reach is tool.reach
    # There is no listener in this fake, so the reach refuses navigation after the guarded
    # replacement driver is installed; that refusal is itself evidence the new guard is active.
    assert "nothing answered" in tool.run(action="navigate", url=f"http://localhost:{port}/")
    assert created[1].navigated == []


def test_site_only_driver_is_recreated_when_turn_port_appears(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from chimera.config import Settings
    from chimera.tools import browser as browser_module

    monkeypatch.setenv("CHIMERA_BROWSER_SITES", "example.com")
    settings = Settings(CHIMERA_HOME=str(tmp_path))  # type: ignore[arg-type]
    live_ports: set[int] = set()
    created: list[_LazyReachDriver] = []

    def fake_driver(headless: bool, **options: Any) -> _LazyReachDriver:
        driver = _LazyReachDriver(options.get("reach"))
        created.append(driver)
        return driver

    monkeypatch.setattr(browser_module, "_new_playwright_driver", fake_driver)
    tool = make_browser_tool(tmp_path, settings, turn_ports=lambda: frozenset(live_ports))
    assert isinstance(tool, BrowserTool)
    tool.run(action="read")
    assert len(created) == 1 and created[0]._track_frames is False

    live_ports.add(43220)
    tool.run(action="read")
    assert len(created) == 2
    assert created[0].closed
    assert created[1].reach is tool.reach
    assert created[1]._track_frames is True


def test_a_live_workspace_job_opens_its_os_listener_and_stopping_it_closes_the_exception(
    tmp_path: Path,
) -> None:
    jobs, shell, job_id, port = _live_server(tmp_path)
    workspace = tmp_path / "workspace"
    reach = _reach(jobs, shell, workspace)
    url = f"http://127.0.0.1:{port}/"
    try:
        assert port in process_tree_listening_ports(jobs, workspace, current_job_ids=shell.turn_job_ids)
        assert reach.permits(url)
        # A process that happens to listen, but is not recorded among this turn's jobs, is not granted.
        untracked = BrowserReach(floor=lambda _url: False, owned=frozenset, answers=lambda _url: OTHER)
        assert not untracked.permits(url)
        # Recorded jobs whose cwd is outside the workspace are not a source of local reach.
        outside_workspace = _reach(jobs, shell, tmp_path / "elsewhere")
        assert not outside_workspace.permits(url)
        # The owner-port check and the OPTIONS probe remain authoritative for dynamic ports.
        assert not _reach(jobs, shell, workspace, owned=frozenset({port})).permits(url)
        chimera = _reach(jobs, shell, workspace, answers=lambda _url: CHIMERA)
        assert not chimera.permits(url)
    finally:
        jobs.cancel(job_id)
    assert not reach.permits(url)


class _LazyReachDriver:
    def __init__(self, reach: BrowserReach | None) -> None:
        self.reach = reach
        self._track_frames = bool(reach is not None and (reach.local_ports or reach._turn_ports()))
        self.closed = False
        self.navigated: list[str] = []

    def navigate(self, url: str) -> list[Any]:
        self.navigated.append(url)
        return []

    def read(self) -> list[Any]:
        return []

    def click(self, ref: str) -> list[Any]:
        return []

    def type_text(self, ref: str, text: str) -> list[Any]:
        return []

    def back(self) -> list[Any]:
        return []

    def page_html(self) -> str:
        return ""

    def page_text(self) -> str:
        return ""

    def screenshot(self, path: str) -> None:
        return None

    def frame(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _Session:
    def __init__(self) -> None:
        self.verdicts: dict[str, str] = {}

    def send(self, method: str, params: dict[str, Any]) -> None:
        self.verdicts[str(params["requestId"])] = method


class _Driver:
    """A minimal driver that feeds the same intercepted requests as Playwright into its guard."""

    def __init__(
        self, guard: RequestGuard, *, outside_resource: bool = False, outside_layout: bool = False
    ) -> None:
        self.guard = guard
        self.url = ""
        self.outside_resource = outside_resource
        self.outside_layout = outside_layout
        self.session = _Session()

    def navigate(self, url: str) -> list[Any]:
        self.url = url
        with self.guard.expecting(url, "main"):
            self.guard.on_paused(
                self.session,
                {"requestId": "top", "request": {"url": url}, "resourceType": "Document", "frameId": "main", "networkId": "top"},
                main_frame="main",
            )
        self.guard.frame_navigated({"frame": {"id": "main", "url": url}})
        if self.outside_resource:
            self.guard.on_paused(
                self.session,
                {"requestId": "script", "request": {"url": "https://outside.invalid/script.js"}, "resourceType": "Script", "frameId": "main"},
                main_frame="main",
            )
        return []

    def layout_check(self, width: int) -> dict[str, object]:
        if self.outside_layout:
            self.guard.on_paused(
                self.session,
                {
                    "requestId": f"layout-{width}",
                    "request": {"url": "https://outside.invalid/resized.css"},
                    "resourceType": "Stylesheet",
                    "frameId": "main",
                },
                main_frame="main",
            )
        return {
            "scroll_width": width,
            "client_width": width,
            "overflowing": [],
            "stylesheet_count": 1,
            "rule_count": 1,
            "failed_stylesheets": 0,
            "body_defaults": False,
        }

    def frame(self) -> None:
        return None

    def read(self) -> list[Any]:
        return []

    def page_text(self) -> str:
        return "local content"


class _Shell(Tool):
    name = "run_shell"
    description = "shell"
    parameters = {"type": "object", "properties": {"command": {"type": "string"}}}

    def run(self, **kwargs: Any) -> str:
        return "shell executed"

def _ledgered_browser(
    port: int, *, outside_resource: bool, outside_layout: bool = False
) -> tuple[LedgeredTool, TaintLedger, _Driver]:
    reach = BrowserReach(
        local_ports={port}, owned=frozenset, answers=lambda _url: OTHER,
        floor=lambda _url: False, turn_ports=lambda: frozenset({port}),
    )
    guard = RequestGuard(
        reach.permits, cache=False, local=reach.declared_local,
        turn_local=reach.declared_turn_local,
    )
    driver = _Driver(guard, outside_resource=outside_resource, outside_layout=outside_layout)
    tool = BrowserTool(driver=driver, reach=reach)  # type: ignore[arg-type]
    ledger = TaintLedger()
    return LedgeredTool(tool, ledger), ledger, driver

def test_turn_port_page_can_only_request_its_own_port() -> None:
    reach = BrowserReach(
        local_ports={43130}, owned=frozenset, answers=lambda _url: OTHER,
        floor=lambda _url: False, turn_ports=lambda: frozenset({43131, 43132}),
    )
    guard = RequestGuard(
        reach.permits, cache=False, local=reach.declared_local,
        turn_local=reach.declared_turn_local, turn_page=reach.declared_turn_only_local,
        begin_request=reach.begin_turn_port_check, end_request=reach.end_turn_port_check,
    )
    session = _Session()
    guard.frame_navigated({"frame": {"id": "main", "url": "http://localhost:43131/"}})
    for request_id, target, expected in (
        ("owner", "http://localhost:43130/", "Fetch.failRequest"),
        ("other-turn", "http://localhost:43132/", "Fetch.failRequest"),
        ("same-turn", "http://localhost:43131/", "Fetch.continueRequest"),
    ):
        guard.on_paused(
            session,
            {"requestId": request_id, "request": {"url": target}, "resourceType": "Fetch", "frameId": "main"},
        )
        assert session.verdicts[request_id] == expected


def test_external_websocket_marks_turn_page_as_not_local_only() -> None:
    reach = BrowserReach(
        owned=frozenset, answers=lambda _url: OTHER, floor=lambda _url: False,
        turn_ports=lambda: frozenset({43133}),
    )
    guard = RequestGuard(
        reach.permits, cache=False, local=reach.declared_local,
        turn_local=reach.declared_turn_local,
    )
    guard.on_websocket_created({"url": "wss://outside.example/socket"})
    assert not guard.local_only_since(0, "http://localhost:43133/")


def test_fetch_from_a_page_confined_to_its_turn_port_is_recorded_cleanly() -> None:
    browser, ledger, _ = _ledgered_browser(43121, outside_resource=False)
    browser.run(action="navigate", url="http://localhost:43121/")
    assert browser.inner.last_local_only is True  # type: ignore[attr-defined]
    assert not ledger.run_tainted()
    assert ledger.events[-1].detail.startswith("local-loopback-only")

def test_layout_check_keeps_turn_port_page_untainted() -> None:
    browser, ledger, _ = _ledgered_browser(43129, outside_resource=False)
    browser.run(action="navigate", url="http://localhost:43129/")

    result = browser.run(action="layout_check", width=390)
    cached = browser.run(action="layout_check", width=390)

    assert "layout_check width=390:" in result
    assert "(cached)" in cached
    assert browser.inner.last_local_only is True  # type: ignore[attr-defined]
    assert not ledger.run_tainted()
    assert ledger.events[-1].detail.startswith("local-loopback-only")


def test_external_request_during_layout_resize_taints_turn() -> None:
    browser, ledger, driver = _ledgered_browser(
        43128, outside_resource=False, outside_layout=True
    )
    browser.run(action="navigate", url="http://localhost:43128/")

    browser.run(action="layout_check", width=390)
    cached = browser.run(action="layout_check", width=390)

    assert "(cached)" in cached
    assert driver.session.verdicts["layout-390"] == "Fetch.failRequest"
    assert browser.inner.last_local_only is False  # type: ignore[attr-defined]
    assert ledger.run_tainted()
    assert ledger.events[-1].detail != "local-loopback-only"


def test_an_external_script_from_a_turn_page_still_taints_and_narrows_shell() -> None:
    browser, ledger, _ = _ledgered_browser(43122, outside_resource=True)
    browser.run(action="navigate", url="http://localhost:43122/")
    assert browser.inner.last_local_only is False  # type: ignore[attr-defined]
    assert ledger.run_tainted()
    asks: list[bool] = []
    shell = LedgeredTool(_Shell(), ledger, narrow_on_taint=True, approve=lambda _assessment: asks.append(True) or False)
    assert "The tool did NOT run" in shell.run(command="echo nope")
    assert asks == [True]


def test_governed_registry_ledger_tracks_local_and_external_browser_output() -> None:
    port = 43125
    reach = BrowserReach(
        local_ports={port}, owned=frozenset, answers=lambda _url: OTHER,
        floor=lambda _url: False, turn_ports=lambda: frozenset({port}),
    )
    guard = RequestGuard(
        reach.permits, cache=False, local=reach.declared_local,
        turn_local=reach.declared_turn_local,
    )
    ledger = TaintLedger()

    for external, expected_taint in ((False, False), (True, True)):
        browser_tool = BrowserTool(
            driver=_Driver(guard, outside_resource=external), reach=reach  # type: ignore[arg-type]
        )
        registry = ToolRegistry()
        registry.register(browser_tool)
        governed = govern_registry(registry, TrustKernel())
        wrapped = ledger_registry(governed, ledger)
        assert type(wrapped.get("browser")).__name__ == "LedgeredTool"
        assert type(wrapped.get("browser").inner).__name__ == "GovernedTool"  # type: ignore[union-attr]
        wrapped.get("browser").run(action="navigate", url=f"http://localhost:{port}/")  # type: ignore[union-attr]
        assert ledger.run_tainted() is expected_taint
        if expected_taint:
            break
        ledger = TaintLedger()


def test_a_browser_early_return_clears_previous_local_only_and_taints_nothing() -> None:
    # The early return is a refusal made before any page was reached, so it delivers no content and taints
    # nothing (the rule of #1122, applied to this path too). What must still hold is that the previous
    # call's local-only verdict does not survive into this one.
    browser, ledger, _ = _ledgered_browser(43126, outside_resource=False)
    browser.run(action="navigate", url="http://localhost:43126/")
    assert browser.inner.last_local_only is True  # type: ignore[attr-defined]

    browser.run(action="google_search", query="not available in this engine")
    assert browser.inner.last_local_only is False  # type: ignore[attr-defined]
    assert not ledger.run_tainted()


def test_local_only_probe_is_cached_once_per_port_per_check() -> None:
    calls: list[int] = []

    def local(url: str) -> bool:
        port = int(url.rsplit(":", 1)[1].rstrip("/"))
        calls.append(port)
        return True

    guard = RequestGuard(allowed=lambda _url: True, turn_local=local)
    guard._request_log.extend(
        [("http://localhost:43127/", True), ("http://localhost:43127/style.css", True)]
    )
    assert guard.local_only_since(0, "http://localhost:43127/")
    assert calls == [43127]


def test_turn_port_check_reports_a_chimera_probe_refusal(tmp_path: Path) -> None:
    jobs, shell, job_id, port = _live_server(tmp_path)
    reach = _reach(jobs, shell, tmp_path / "workspace", answers=lambda _url: CHIMERA)
    try:
        with pytest.raises(ValueError, match="answered by Chimera itself"):
            reach.check(f"http://localhost:{port}/")
    finally:
        jobs.cancel(job_id)


def test_job_cwd_and_non_job_listener_do_not_gain_a_dynamic_port() -> None:
    """Predicate-level negative case without starting another process or requiring Chromium."""
    live = {43123}
    reach = BrowserReach(
        floor=lambda _url: False, owned=frozenset, answers=lambda _url: OTHER,
        turn_ports=lambda: frozenset(live),
    )
    url = "http://127.0.0.1:43123/"
    assert reach.permits(url)
    live.clear()
    assert not reach.permits(url)


def test_a_turn_port_does_not_change_other_local_ports() -> None:
    reach = BrowserReach(
        floor=lambda _url: False, owned=frozenset, answers=lambda _url: OTHER,
        turn_ports=lambda: frozenset({43124}),
    )
    assert reach.permits("http://localhost:43124/")
    assert not reach.permits("http://localhost:43125/")
