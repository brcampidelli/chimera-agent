"""Where a Code conversation's commands run: saved environments, WSL, and the choice being the owner's.

What these pin, in the order a mistake would cost most:

* **WSL is the host.** It shares the disk, the user and the network with Windows, so it must go
  through the host-exec gate exactly as the local sandbox does, and an install that configured
  isolation refuses it instead of loosening it.
* **The translation never leaves the workspace.** ``/mnt/c`` is the whole drive; `..`, a sibling
  folder that shares a prefix, another drive and a UNC share are all refused.
* **Only the owner's screen chooses, and only once.** The bridge, a paired device and a share link
  cannot name an environment, the first turn fixes it, and a conversation with no record is local.

Nothing here needs WSL: the subprocess is a stub and the translation is tested on Windows path
semantics on every platform.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings, get_settings
from chimera.core import environments as envs
from chimera.core.code_session import CodeSessionStore
from chimera.sandbox import wsl as wsl_module
from chimera.sandbox.wsl import (
    WslPathError,
    WslSandbox,
    parse_distro_list,
    translate,
    wsl_argv,
)
from tests.conversation_fakes import Agent

# -- the path translation ---------------------------------------------------------------------


def test_a_folder_in_the_workspace_becomes_its_mnt_path() -> None:
    assert translate("C:\\proj\\src\\app", "C:\\proj") == "/mnt/c/proj/src/app"
    assert translate("C:\\proj", "C:\\proj") == "/mnt/c/proj"
    # Inside the workspace whatever the case; the spelling is the one the OS reported.
    assert translate("c:\\PROJ\\a", "C:\\proj") == "/mnt/c/PROJ/a"
    assert translate("D:\\work\\x", "D:\\work") == "/mnt/d/work/x"


def test_dotdot_that_stays_inside_is_folded_and_dotdot_that_leaves_is_refused() -> None:
    assert translate("C:\\proj\\a\\..\\b", "C:\\proj") == "/mnt/c/proj/b"
    for escape in ("C:\\proj\\..", "C:\\proj\\..\\other", "C:\\proj\\a\\..\\..\\Windows"):
        with pytest.raises(WslPathError):
            translate(escape, "C:\\proj")


def test_a_sibling_that_shares_a_prefix_another_drive_and_a_unc_share_are_refused() -> None:
    with pytest.raises(WslPathError):
        translate("C:\\proj2\\x", "C:\\proj")
    with pytest.raises(WslPathError):
        translate("D:\\proj", "C:\\proj")
    with pytest.raises(WslPathError):
        translate("\\\\server\\share\\x", "\\\\server\\share")
    with pytest.raises(WslPathError):
        translate("C:\\proj", "\\\\wsl$\\Ubuntu\\home")
    with pytest.raises(WslPathError):
        translate("C:proj", "C:proj")  # drive-relative: no absolute meaning


# -- the argv ---------------------------------------------------------------------------------


def test_the_argv_is_a_list_with_the_command_as_one_element() -> None:
    command = "echo 'a b' && ls; rm -rf /tmp/x"
    argv = wsl_argv(command, "/mnt/c/proj")
    assert argv == ["wsl.exe", "--cd", "/mnt/c/proj", "--exec", "bash", "-lc", command]
    named = wsl_argv("ls", "/mnt/c/proj", "Ubuntu-22.04")
    assert named[:3] == ["wsl.exe", "-d", "Ubuntu-22.04"]


@pytest.mark.parametrize("name", ["-x", "a b", "a;b", "", "x" * 65, "--help"])
def test_a_distro_that_could_read_as_a_flag_or_a_command_is_refused(name: str) -> None:
    assert not wsl_module.valid_distro(name)
    if name:
        with pytest.raises(WslPathError):
            wsl_argv("ls", "/mnt/c/p", name)
        with pytest.raises(WslPathError):
            WslSandbox("C:\\p", name)


def test_the_distro_list_is_read_from_utf16_with_or_without_a_bom() -> None:
    raw = "Ubuntu\r\nDebian\r\n".encode("utf-16-le")
    assert parse_distro_list(raw) == ["Ubuntu", "Debian"]
    assert parse_distro_list(b"\xff\xfe" + raw) == ["Ubuntu", "Debian"]
    assert parse_distro_list(b"Ubuntu\n-bad\n") == ["Ubuntu"]


# -- the sandbox ------------------------------------------------------------------------------


class _Proc:
    returncode = 0

    def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
        return b"hello\n", b""

    def poll(self) -> int:
        return 0


@pytest.fixture
def popen_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake(target: Any, **kwargs: Any) -> _Proc:
        calls.append({"target": target, **kwargs})
        return _Proc()

    monkeypatch.setattr(subprocess, "Popen", fake)
    return calls


def test_the_sandbox_runs_wsl_exe_with_an_argv_list_and_never_a_shell(
    popen_calls: list[dict[str, Any]],
) -> None:
    box = WslSandbox("C:\\proj", "Ubuntu")
    result = box.run("ls -la", timeout=5, cwd=Path("C:\\proj\\src"))
    assert result.exit_code == 0 and result.stdout == "hello\n"
    (call,) = popen_calls
    assert call["target"] == [
        "wsl.exe", "-d", "Ubuntu", "--cd", "/mnt/c/proj/src", "--exec", "bash", "-lc", "ls -la",
    ]
    assert call["shell"] is False


def test_a_working_directory_outside_the_workspace_is_not_run(
    popen_calls: list[dict[str, Any]],
) -> None:
    box = WslSandbox("C:\\proj")
    for outside in ("C:\\proj\\..", "C:\\Windows", "D:\\proj"):
        result = box.run("ls", cwd=Path(outside))
        assert result.exit_code == 1 and "not run" in result.stderr
    assert popen_calls == []


def test_wsl_is_not_isolated_and_is_not_a_local_sandbox() -> None:
    from chimera.sandbox import LocalSandbox
    from chimera.sandbox.confirm import sandbox_is_isolated

    box = WslSandbox("C:\\proj")
    assert box.is_isolated() is False
    assert sandbox_is_isolated(box) is False
    # Not a LocalSandbox: the places that adopt, wrap or kill a host process as a job must not take it.
    assert not isinstance(box, LocalSandbox)


def test_wsl_counts_as_the_host_for_the_host_exec_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate is consulted before the command reaches a WSL sandbox, and its refusal stops it."""
    from chimera.tools.shell import RunShellTool

    ran: list[str] = []

    def record(self: WslSandbox, command: str, **_: Any) -> Any:
        ran.append(command)
        from chimera.sandbox.base import SandboxResult

        return SandboxResult(exit_code=0, stdout="ok")

    monkeypatch.setattr(WslSandbox, "run", record)
    asked: list[str] = []

    def decline(command: str) -> bool:
        asked.append(command)
        return False

    tool = RunShellTool(Path.cwd(), WslSandbox("C:\\proj"), confirm=decline)
    out = tool.run(command="echo hi")
    assert asked == ["echo hi"] and ran == []
    assert "declined" in out

    allowed = RunShellTool(Path.cwd(), WslSandbox("C:\\proj"), confirm=lambda _c: True)
    assert "ok" in allowed.run(command="echo hi")
    assert ran == ["echo hi"]


def test_a_wsl_turn_offers_no_background_job(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.core.jobs import jobs_for
    from chimera.tools.shell import RunShellTool

    tool = RunShellTool(
        Path.cwd(), WslSandbox("C:\\proj"), confirm=lambda _c: True, jobs=jobs_for(Path.cwd() / ".j")
    )
    out = tool.run(command="sleep 1", background=True)
    assert "not available in the WSL environment" in out


def test_execute_code_runs_the_distros_python_not_a_windows_path() -> None:
    from chimera.tools.code import python_command

    command = python_command(WslSandbox("C:\\proj"), "script.py")
    assert command is not None and "command -v python3" in command and "C:" not in command


def test_the_registry_hands_the_chosen_sandbox_to_the_shell_and_code_tools(tmp_path: Path) -> None:
    from chimera.tools import default_registry

    box = WslSandbox(tmp_path)
    registry = default_registry(tmp_path, host_exec_confirm=None, sandbox=box)
    assert registry.get("run_shell")._sandbox is box  # type: ignore[attr-defined]
    assert registry.get("execute_code")._sandbox is box  # type: ignore[attr-defined]


# -- the saved list ---------------------------------------------------------------------------


@pytest.fixture
def wsl_here(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wsl_module, "wsl_supported", lambda: True)


def test_environments_are_saved_listed_and_removed(tmp_path: Path, wsl_here: None) -> None:
    registry = envs.registry_for(tmp_path)
    assert registry.entries() == []
    assert registry.get("local") is envs.LOCAL
    made = registry.add("Ubuntu dev", "wsl", {"distro": "Ubuntu-22.04"})
    assert made.kind == "wsl" and made.params == {"distro": "Ubuntu-22.04"}
    assert [e.name for e in registry.entries()] == ["Ubuntu dev"]
    assert registry.get(made.id) == made
    assert registry.remove(made.id) is True
    assert registry.remove(made.id) is False
    assert registry.entries() == []


def test_a_bad_environment_is_refused_with_a_sentence(tmp_path: Path, wsl_here: None) -> None:
    registry = envs.registry_for(tmp_path)
    for name, kind, params in [
        ("", "wsl", {}),
        ("x", "docker", {}),
        ("x", "local", {}),
        ("x", "wsl", {"distro": "-bad"}),
        ("x", "wsl", {"host": "a"}),
    ]:
        with pytest.raises(ValueError):
            registry.add(name, kind, params)
    registry.add("one", "wsl")
    with pytest.raises(ValueError):
        registry.add("ONE", "wsl")
    with pytest.raises(ValueError):
        registry.add("Local", "wsl")
    with pytest.raises(ValueError):
        registry.remove("local")


def test_wsl_cannot_be_saved_where_it_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wsl_module, "wsl_supported", lambda: False)
    with pytest.raises(ValueError, match="not available"):
        envs.registry_for(tmp_path).add("x", "wsl")


def test_a_project_default_is_kept_and_dies_with_its_environment(
    tmp_path: Path, wsl_here: None
) -> None:
    registry = envs.registry_for(tmp_path)
    made = registry.add("u", "wsl")
    project = str(tmp_path / "proj")
    registry.set_default(project, made.id)
    assert registry.default_for(project) == made.id
    with pytest.raises(ValueError):
        registry.set_default(project, "nope")
    registry.remove(made.id)
    assert registry.default_for(project) == ""


def test_a_hand_edited_row_that_the_form_would_refuse_is_not_read(
    tmp_path: Path, wsl_here: None
) -> None:
    (tmp_path / envs.FILE_NAME).write_text(
        json.dumps({"environments": [
            {"id": "a1", "name": "ok", "kind": "wsl", "params": {"distro": "Ubuntu"}},
            {"id": "a2", "name": "evil", "kind": "wsl", "params": {"distro": "--exec"}},
            {"id": "local", "name": "shadow", "kind": "wsl", "params": {}},
            {"id": "a3", "name": "ssh", "kind": "ssh", "params": {}},
        ]}), encoding="utf-8",
    )
    assert [e.id for e in envs.registry_for(tmp_path).entries()] == ["a1"]


def test_an_environment_that_would_loosen_the_installs_isolation_is_refused(
    tmp_path: Path, wsl_here: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = envs.Environment("e1", "u", "wsl", {})
    monkeypatch.setattr(envs, "_install_isolates", lambda _s: True)
    with pytest.raises(envs.EnvironmentRefused) as refused:
        envs.sandbox_for(env, tmp_path, Settings(CHIMERA_HOME=str(tmp_path)))
    assert refused.value.status == 409 and "isolated" in refused.value.detail
    monkeypatch.setattr(envs, "_install_isolates", lambda _s: False)
    assert isinstance(envs.sandbox_for(env, tmp_path, Settings(CHIMERA_HOME=str(tmp_path))), WslSandbox)
    assert envs.sandbox_for(envs.LOCAL, tmp_path, Settings(CHIMERA_HOME=str(tmp_path))) is None


def test_docker_configured_refuses_wsl_even_when_the_daemon_is_down(tmp_path: Path) -> None:
    settings = Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_SANDBOX="docker")
    assert envs._install_isolates(settings) is True


# -- the decision of one turn -----------------------------------------------------------------


def _registry(tmp_path: Path, wsl_on: bool = True) -> tuple[envs.EnvironmentRegistry, envs.Environment]:
    registry = envs.registry_for(tmp_path)
    return registry, registry.add("u", "wsl")


def test_a_first_turn_from_the_screen_chooses_and_fixes(tmp_path: Path, wsl_here: None) -> None:
    registry, made = _registry(tmp_path)
    got = envs.decide(registry, requested=made.id, stored=None, locked=False, may_choose=True)
    assert got.env == made and got.fix_now is True
    plain = envs.decide(registry, requested=None, stored=None, locked=False, may_choose=True)
    assert plain.env is envs.LOCAL and plain.fix_now is True


def test_a_first_turn_that_may_not_choose_is_refused_a_choice_and_otherwise_local(
    tmp_path: Path, wsl_here: None
) -> None:
    registry, made = _registry(tmp_path)
    with pytest.raises(envs.EnvironmentRefused) as refused:
        envs.decide(registry, requested=made.id, stored=None, locked=False, may_choose=False)
    assert refused.value.status == 403
    registry.set_default(str(tmp_path / "p"), made.id)
    unattended = envs.decide(
        registry, requested=None, stored=None, locked=False, may_choose=False, project=str(tmp_path / "p")
    )
    assert unattended.env is envs.LOCAL  # a default is the owner's screen's convenience, not a door
    screen = envs.decide(
        registry, requested=None, stored=None, locked=False, may_choose=True, project=str(tmp_path / "p")
    )
    assert screen.env == made
    explicit = envs.decide(
        registry, requested="local", stored=None, locked=False, may_choose=True, project=str(tmp_path / "p")
    )
    assert explicit.env is envs.LOCAL


def test_after_the_first_turn_it_cannot_change_and_a_removed_one_ends_the_commands(
    tmp_path: Path, wsl_here: None
) -> None:
    registry, made = _registry(tmp_path)
    stored = made.record()
    same = envs.decide(registry, requested=made.id, stored=stored, locked=True, may_choose=True)
    assert same.env == made and same.fix_now is False
    quiet = envs.decide(registry, requested=None, stored=stored, locked=True, may_choose=False)
    assert quiet.env == made
    for wanted in ("local", "other"):
        with pytest.raises(envs.EnvironmentRefused) as refused:
            envs.decide(registry, requested=wanted, stored=stored, locked=True, may_choose=True)
        assert refused.value.status == 409
    registry.remove(made.id)
    with pytest.raises(envs.EnvironmentRefused) as gone:
        envs.decide(registry, requested=None, stored=stored, locked=True, may_choose=False)
    assert gone.value.status == 409 and "removed" in gone.value.detail


def test_a_conversation_with_no_record_is_local_and_cannot_become_wsl(
    tmp_path: Path, wsl_here: None
) -> None:
    registry, made = _registry(tmp_path)
    got = envs.decide(registry, requested=None, stored=None, locked=True, may_choose=True)
    assert got.env is envs.LOCAL
    with pytest.raises(envs.EnvironmentRefused):
        envs.decide(registry, requested=made.id, stored=None, locked=True, may_choose=True)


# -- through the app --------------------------------------------------------------------------

SECRET = "screen-secret"


class _Rig:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import chimera.api.code_api as code_api
        import chimera.core
        from chimera.api import build_api_app
        from chimera.api.screen_credential import HEADER_NAME, ScreenCredential
        from chimera.interface import ChatSession

        self.home = tmp_path / "home"
        monkeypatch.setenv("CHIMERA_HOME", str(self.home))
        get_settings.cache_clear()
        monkeypatch.setattr(chimera.core, "Agent", Agent, raising=True)
        monkeypatch.setattr(wsl_module, "wsl_supported", lambda: True)
        monkeypatch.setattr(envs, "_install_isolates", lambda _s: False)
        self.sandboxes: list[Any] = []
        real = code_api.assemble_registry

        def spy(*args: Any, **kwargs: Any) -> Any:
            self.sandboxes.append(kwargs.get("sandbox"))
            return real(*args, **kwargs)

        monkeypatch.setattr(code_api, "assemble_registry", spy)
        self.folder = tmp_path / "proj"
        self.folder.mkdir()
        settings = Settings(CHIMERA_HOME=str(self.home), CHIMERA_MEMORY_BACKEND="json")
        self.app = build_api_app(
            lambda: ChatSession(Agent()), workspace=self.folder, settings=settings,
            screen=ScreenCredential(SECRET),
        )
        self.header = {HEADER_NAME: SECRET}
        self.screen = TestClient(self.app, headers=self.header)
        self.bare = TestClient(self.app)

    def marked(self, marker: dict[str, Any], *, screen: bool = False) -> TestClient:
        app = self.app

        async def wrapped(scope, receive, send):  # type: ignore[no-untyped-def]
            if scope["type"] == "http":
                scope = {**scope, **marker}
            await app(scope, receive, send)

        return TestClient(wrapped, headers=self.header if screen else None)

    def make_env(self, name: str = "Ubuntu", distro: str = "Ubuntu") -> str:
        response = self.screen.post(
            "/api/code/environments", json={"name": name, "kind": "wsl", "params": {"distro": distro}}
        )
        assert response.status_code == 200, response.text
        return next(e["id"] for e in response.json()["environments"] if e["name"] == name)

    def turn(self, client: TestClient, session_id: str | None = None, **extra: Any) -> Any:
        body: dict[str, Any] = {
            "message": f"hi [{self.folder}]", "workspace": str(self.folder), "stream": False, **extra,
        }
        if session_id:
            body["session_id"] = session_id
        return client.post("/api/code/turn", json=body)

    @staticmethod
    def sid(response: Any) -> str:
        found = re.search(r'"session_id": ?"([0-9a-f]+)"', response.text)
        assert found, response.text[:300]
        return found.group(1)

    def stored(self, session_id: str) -> dict[str, Any]:
        return json.loads((self.home / "code_sessions" / f"{session_id}.json").read_text("utf-8"))


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    made = _Rig(tmp_path, monkeypatch)
    yield made
    made.screen.close()
    made.bare.close()
    get_settings.cache_clear()


def test_the_owners_screen_creates_lists_and_removes_environments(rig: _Rig) -> None:
    first = rig.screen.get("/api/code/environments").json()
    assert [e["id"] for e in first["environments"]] == ["local"]
    assert first["environments"][0]["builtin"] is True
    assert {k["kind"] for k in first["kinds"]} == {"wsl", "ssh"}
    env_id = rig.make_env()
    listed = rig.screen.get("/api/code/environments").json()["environments"]
    assert [e["name"] for e in listed] == ["Local", "Ubuntu"]
    assert rig.screen.post(
        "/api/code/environments", json={"name": "bad", "kind": "wsl", "params": {"distro": "-x"}}
    ).status_code == 400
    assert rig.screen.delete("/api/code/environments/local").status_code == 400
    assert rig.screen.delete("/api/code/environments/nope").status_code == 404
    assert rig.screen.delete(f"/api/code/environments/{env_id}").status_code == 200
    assert [e["id"] for e in rig.screen.get("/api/code/environments").json()["environments"]] == ["local"]


def test_a_project_default_is_set_through_the_screen(rig: _Rig) -> None:
    env_id = rig.make_env()
    set_ = rig.screen.put(
        "/api/code/environments/default", json={"project": str(rig.folder), "environment": env_id}
    )
    assert set_.status_code == 200 and list(set_.json()["defaults"].values()) == [env_id]
    asked = rig.screen.get("/api/code/environments", params={"project": str(rig.folder)}).json()
    assert asked["project_default"] == env_id
    assert rig.screen.get("/api/code/environments").json()["project_default"] == ""
    cleared = rig.screen.put(
        "/api/code/environments/default", json={"project": str(rig.folder), "environment": ""}
    )
    assert cleared.json()["defaults"] == {}


def test_the_bridge_a_paired_device_and_a_bare_caller_cannot_use_the_environment_routes(rig: _Rig) -> None:
    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY
    from chimera.api.devices import DEVICE_SCOPE_KEY

    body = {"name": "x", "kind": "wsl", "params": {}}
    for client in (
        rig.marked({VIA_BRIDGE_SCOPE_KEY: True}, screen=True),
        rig.marked({DEVICE_SCOPE_KEY: "d1"}, screen=True),
    ):
        assert client.get("/api/code/environments").status_code == 403
        assert client.get("/api/code/environments/wsl-distros").status_code == 403
        assert client.post("/api/code/environments", json=body).status_code == 403
        assert client.put(
            "/api/code/environments/default", json={"project": "p", "environment": ""}
        ).status_code == 403
        assert client.delete("/api/code/environments/x").status_code == 403
    assert rig.bare.post("/api/code/environments", json=body).status_code == 403
    assert rig.screen.get("/api/code/environments").json()["environments"][1:] == []


def test_the_first_turn_fixes_the_environment_and_runs_in_it(rig: _Rig) -> None:
    env_id = rig.make_env()
    first = rig.turn(rig.screen, environment=env_id)
    assert first.status_code == 200, first.text
    sid = rig.sid(first)
    assert isinstance(rig.sandboxes[-1], WslSandbox) and rig.sandboxes[-1].distro == "Ubuntu"
    assert rig.stored(sid)["environment"] == {"id": env_id, "name": "Ubuntu", "kind": "wsl"}
    # No parameters are copied into the conversation: the saved entry stays the authority.
    assert "params" not in rig.stored(sid)["environment"]
    # A later turn that says nothing keeps it; one that repeats it is fine; one that changes it is refused.
    assert rig.turn(rig.screen, sid).status_code == 200
    assert isinstance(rig.sandboxes[-1], WslSandbox)
    assert rig.turn(rig.screen, sid, environment=env_id).status_code == 200
    changed = rig.turn(rig.screen, sid, environment="local")
    assert changed.status_code == 409 and "fixed" in changed.json()["detail"]


def test_a_conversation_from_before_environments_is_local_and_stays_local(rig: _Rig) -> None:
    env_id = rig.make_env()
    sessions = rig.home / "code_sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / "old1.json").write_text(
        json.dumps({"session_id": "old1", "workspace": str(rig.folder),
                    "messages": [{"role": "user", "content": "oi"}, {"role": "assistant", "content": "e"}]}),
        encoding="utf-8",
    )
    assert rig.turn(rig.screen, "old1").status_code == 200
    assert rig.sandboxes[-1] is None
    assert "environment" not in rig.stored("old1")
    assert rig.turn(rig.screen, "old1", environment="local").status_code == 200
    assert rig.turn(rig.screen, "old1", environment=env_id).status_code == 409


def test_a_local_first_turn_records_nothing(rig: _Rig) -> None:
    sid = rig.sid(rig.turn(rig.screen))
    assert "environment" not in rig.stored(sid)
    assert rig.sandboxes[-1] is None


def test_the_bridge_and_a_bare_caller_cannot_name_an_environment_on_a_turn(rig: _Rig) -> None:
    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY
    from chimera.api.devices import DEVICE_SCOPE_KEY

    env_id = rig.make_env()
    for client in (
        rig.marked({VIA_BRIDGE_SCOPE_KEY: True}, screen=True),
        rig.marked({DEVICE_SCOPE_KEY: "d1"}, screen=True),
        rig.bare,
    ):
        refused = rig.turn(client, environment=env_id)
        assert refused.status_code == 403, refused.text
    assert rig.sandboxes == []
    assert list((rig.home / "code_sessions").glob("*.json")) == [] if (rig.home / "code_sessions").exists() else True


def test_a_bridge_turn_in_a_wsl_conversation_keeps_running_in_it_without_naming_it(rig: _Rig) -> None:
    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY

    env_id = rig.make_env()
    sid = rig.sid(rig.turn(rig.screen, environment=env_id))
    bridged = rig.marked({VIA_BRIDGE_SCOPE_KEY: True}, screen=True)
    assert rig.turn(bridged, sid).status_code == 200
    assert isinstance(rig.sandboxes[-1], WslSandbox)
    assert rig.turn(bridged, sid, environment=env_id).status_code == 403


def test_a_bridge_first_turn_is_local_even_when_the_project_has_a_default(rig: _Rig) -> None:
    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY

    env_id = rig.make_env()
    rig.screen.put(
        "/api/code/environments/default", json={"project": str(rig.folder), "environment": env_id}
    )
    bridged = rig.marked({VIA_BRIDGE_SCOPE_KEY: True}, screen=True)
    assert rig.turn(bridged).status_code == 200
    assert rig.sandboxes[-1] is None
    # ...while the owner's own screen, naming nothing, gets the default.
    assert rig.turn(rig.screen).status_code == 200
    assert isinstance(rig.sandboxes[-1], WslSandbox)


def test_removing_an_environment_ends_the_commands_of_the_conversations_that_ran_in_it(rig: _Rig) -> None:
    env_id = rig.make_env()
    sid = rig.sid(rig.turn(rig.screen, environment=env_id))
    rig.screen.delete(f"/api/code/environments/{env_id}")
    gone = rig.turn(rig.screen, sid)
    assert gone.status_code == 409 and "removed" in gone.json()["detail"]


def test_an_install_that_isolates_its_commands_refuses_the_wsl_environment(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_id = rig.make_env()
    monkeypatch.setattr(envs, "_install_isolates", lambda _s: True)
    refused = rig.turn(rig.screen, environment=env_id)
    assert refused.status_code == 409 and "isolated" in refused.json()["detail"]


def test_an_unknown_environment_id_is_a_422(rig: _Rig) -> None:
    assert rig.turn(rig.screen, environment="deadbeef0000").status_code == 422


def test_the_session_file_round_trips_the_environment(tmp_path: Path) -> None:
    store = CodeSessionStore(tmp_path / "s")
    store.fix_environment("abc", {"id": "e1", "name": "n", "kind": "wsl"})
    assert store.stored_environment("abc") == {"id": "e1", "name": "n", "kind": "wsl"}
    assert store.has_turns("abc") is False
    assert store.stored_environment("missing") is None
    session = store.load("abc", Agent())
    assert session.environment == {"id": "e1", "name": "n", "kind": "wsl"}
    session.messages = [{"role": "user", "content": "x"}]
    store.save(session)
    assert store.stored_environment("abc") == {"id": "e1", "name": "n", "kind": "wsl"}
    assert store.has_turns("abc") is True


def test_the_conversation_row_carries_its_environment_only_when_it_has_one(rig: _Rig) -> None:
    env_id = rig.make_env()
    wsl_sid = rig.sid(rig.turn(rig.screen, environment=env_id))
    local_sid = rig.sid(rig.turn(rig.screen))
    rows = {row["id"]: row for row in rig.screen.get("/api/code/sessions").json()}
    assert rows[wsl_sid]["environment"] == {"id": env_id, "name": "Ubuntu", "kind": "wsl"}
    assert rows[local_sid].get("environment") is None


def test_the_environment_is_fixed_by_compare_and_set_under_the_conversations_lock(tmp_path: Path) -> None:
    from chimera.core.code_session import EnvironmentFixedError

    store = CodeSessionStore(tmp_path / "s")
    one = {"id": "e1", "name": "n", "kind": "wsl"}
    store.fix_environment("c1", one)
    store.fix_environment("c1", one)  # the same one again: nothing to refuse
    with pytest.raises(EnvironmentFixedError):
        store.fix_environment("c1", {"id": "e2", "name": "m", "kind": "wsl"})
    started = store.load("c2", Agent())
    started.messages = [{"role": "user", "content": "x"}]
    store.save(started)
    with pytest.raises(EnvironmentFixedError):
        store.fix_environment("c2", one)
    assert store.stored_environment("c2") is None


def test_a_conflict_found_when_the_turn_fixes_its_environment_is_a_409(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Between the decision and the write another message fixed another one: refused, not overwritten."""
    from chimera.core.code_session import EnvironmentFixedError

    env_id = rig.make_env()

    def lost(self: CodeSessionStore, session_id: str, record: dict[str, str]) -> None:
        raise EnvironmentFixedError("the conversation's environment was fixed by another message")

    monkeypatch.setattr(CodeSessionStore, "fix_environment", lost)
    refused = rig.turn(rig.screen, environment=env_id)
    assert refused.status_code == 409 and "another message" in refused.json()["detail"]


@pytest.mark.skipif(__import__("shutil").which("git") is None, reason="needs git")
def test_a_new_worktree_still_gets_the_default_of_the_project_it_came_from(rig: _Rig) -> None:
    """`worktree: "new"` moves the turn into a folder nobody set a default for; the repository's applies."""
    repo = rig.folder.parent / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["-c", "user.name=T", "-c", "user.email=t@x.io", "commit", "-q", "--allow-empty", "-m", "i"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    env_id = rig.make_env()
    assert rig.screen.post("/api/code/workspaces", json={"path": str(repo)}).status_code == 200
    rig.screen.put("/api/code/environments/default", json={"project": str(repo), "environment": env_id})
    response = rig.screen.post(
        "/api/code/turn",
        json={"message": "hi", "project": str(repo), "worktree": "new", "stream": False},
    )
    assert response.status_code == 200, response.text
    assert isinstance(rig.sandboxes[-1], WslSandbox)
    sid = rig.sid(response)
    assert rig.stored(sid)["environment"]["id"] == env_id


# -- the fences that guard Chimera's own files, read through /mnt/<letter> -----------------------

from chimera.sandbox.wsl import windows_view  # noqa: E402


def test_mnt_paths_are_put_back_as_windows_paths() -> None:
    assert windows_view("rm /mnt/c/Users/x/.chimera/audit.jsonl") == "rm C:/Users/x/.chimera/audit.jsonl"
    # The drive ROOT, with its slash: `D:` alone is the drive-relative "current folder of D".
    assert windows_view("cd /mnt/d && ls") == "cd D:/ && ls"
    assert windows_view("echo x>/mnt/c/a") == "echo x>C:/a"
    # The tail of another name is not a mount.
    assert windows_view("ls /usr/mnt/c/x /tmp/amnt/c/y") == "ls /usr/mnt/c/x /tmp/amnt/c/y"


windows_only = pytest.mark.skipif(
    __import__("os").name != "nt", reason="the host's own path rules are the ones under test"
)


def _mnt(path: Path) -> str:
    text = str(path.resolve())
    return "/mnt/" + text[0].lower() + text[2:].replace("\\", "/")


@pytest.fixture
def chimera_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "chimera-home"
    (home / "approvals").mkdir(parents=True)
    (home / "audit.jsonl").write_text("{}", encoding="utf-8")
    (home / "chimera-hooks.json").write_text("{}", encoding="utf-8")
    (home / "code_bypass_switches.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    yield home
    get_settings.cache_clear()


@windows_only
@pytest.mark.parametrize(
    ("template", "fence"),
    [
        ("echo x > {h}/approvals/a.json", "queue"),
        ("cd {h} && rm audit.jsonl", "audit"),
        ("rm {h}/aud*", "audit"),
        ("rm {h}/chimera-hook*", "queue"),
        ("rm {h}/code_bypass_*", "queue"),
        ("cat {h}/appr\"\"ovals/x", "queue"),
    ],
)
def test_a_wsl_command_cannot_reach_chimeras_own_files_by_their_mnt_spelling(
    chimera_home: Path, tmp_path: Path, template: str, fence: str
) -> None:
    from chimera.tools.workspace import audit_refusal, queue_refusal

    command = template.format(h=_mnt(chimera_home))
    check = queue_refusal if fence == "queue" else audit_refusal
    # Without the WSL reading the same command passes: that is the hole, and this proves the test sees it.
    assert check("run_shell", command, tmp_path) is None
    refused = check("run_shell", command, tmp_path, wsl=True)
    assert refused is not None and "did NOT run" in refused


@windows_only
def test_an_ordinary_wsl_command_in_the_workspace_is_not_refused(chimera_home: Path, tmp_path: Path) -> None:
    from chimera.tools.workspace import audit_refusal, queue_refusal

    command = f"ls {_mnt(tmp_path)} && cat {_mnt(tmp_path)}/README.md"
    assert queue_refusal("run_shell", command, tmp_path, wsl=True) is None
    assert audit_refusal("run_shell", command, tmp_path, wsl=True) is None


@windows_only
def test_the_wsl_shell_and_code_tools_apply_the_fences_before_anything_runs(
    chimera_home: Path, tmp_path: Path, popen_calls: list[dict[str, Any]]
) -> None:
    from chimera.tools.code import ExecuteCodeTool
    from chimera.tools.shell import RunShellTool

    workspace = tmp_path / "ws"
    workspace.mkdir()
    box = WslSandbox(workspace)
    shell = RunShellTool(workspace, box, confirm=lambda _c: True)
    for command in (
        f"echo x > {_mnt(chimera_home)}/approvals/a.json",
        f"cd {_mnt(chimera_home)} && rm audit.jsonl",
        f"rm {_mnt(chimera_home)}/aud*",
    ):
        assert "did NOT run" in shell.run(command=command)
    code = ExecuteCodeTool(workspace, box, confirm=lambda _c: True)
    out = code.run(code=f"import os; os.remove('{_mnt(chimera_home)}/audit.jsonl')")
    assert "did NOT run" in out
    assert popen_calls == []


# -- every spelling of /mnt/<letter>, on any host ------------------------------------------------

SPELLINGS = [
    "cat /mnt/c/{p}",
    "cat /mnt//c/{p}",
    "cat /mnt/./c/{p}",
    "cat //mnt/c/{p}",
    "cat /./mnt/c/{p}",
    "cat /usr/../mnt/c/{p}",
    "cat /mnt/../mnt/c/{p}",
    "cd /mnt/c && cat {p}",
    "cd / && cat mnt/c/{p}",
    "cat ../../../mnt/c/{p}",
    "cd /usr/.. && cat mnt/c/{p}",
]
WSL_CWD = "/mnt/c/ws"


def test_every_spelling_of_a_drive_path_reads_as_the_same_host_path() -> None:
    for template in SPELLINGS:
        view = windows_view(template.format(p="Users/x/.env"), WSL_CWD)
        assert "C:/Users/x/.env" in view, (template, view)
    # The drive root keeps its slash whatever follows it, so a `cd` lands on the drive and not on
    # the drive-relative "current folder of C:".
    assert windows_view("cd /mnt/c", WSL_CWD) == "cd C:/"
    # A word that is not on a drive is left exactly as written, and nothing is invented for it.
    assert windows_view("ls /usr/local /mnt /home/u/mnt/c/x ../y", "/home/u") == (
        "ls /usr/local /mnt /home/u/mnt/c/x ../y"
    )


@pytest.fixture
def drive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A synthetic drive C: on any host: `/mnt/c/<x>` is read as `<tmp>/c/<x>`, with Chimera's home on it."""
    root = tmp_path / "c"
    home = root / "chimera-home"
    for folder in (home / "approvals", home / "mcp_oauth", root / "app", root / "ws"):
        folder.mkdir(parents=True)
    (home / "audit.jsonl").write_text("{}", encoding="utf-8")
    (root / "app" / ".env").write_text("A=1", encoding="utf-8")
    monkeypatch.setattr(
        wsl_module, "drive_root",
        lambda letter: root.as_posix() + "/" if letter.lower() == "c" else f"{letter.upper()}:/",
    )
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.chdir(root / "app")
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


@pytest.mark.parametrize("template", SPELLINGS)
@pytest.mark.parametrize(
    "target", ["chimera-home/approvals/x.json", "chimera-home/approvals", "chimera-home/mcp_oauth/h.json", "app/.env"]
)
def test_no_spelling_of_a_drive_path_gets_a_wsl_command_to_the_queue_the_mcp_store_or_the_env(
    drive: Path, template: str, target: str
) -> None:
    from chimera.tools.workspace import queue_refusal

    command = template.format(p=target)
    refused = queue_refusal("run_shell", command, drive / "ws", wsl=True, wsl_cwd=WSL_CWD)
    assert refused is not None and "did NOT run" in refused, command


@pytest.mark.parametrize("template", SPELLINGS)
def test_no_spelling_of_a_drive_path_gets_a_wsl_command_to_the_audit_log(drive: Path, template: str) -> None:
    from chimera.tools.workspace import audit_refusal

    command = template.format(p="chimera-home/aud*")
    refused = audit_refusal("run_shell", command, drive / "ws", wsl=True, wsl_cwd=WSL_CWD)
    assert refused is not None and "did NOT run" in refused, command


def test_a_wsl_command_that_stays_in_the_workspace_is_not_refused_on_any_host(drive: Path) -> None:
    from chimera.tools.workspace import audit_refusal, queue_refusal

    for command in ("cat /mnt/c/ws/src/main.py", "cd /mnt/c/ws && ls src", "cat ../ws/a.txt", "ls /usr/bin"):
        assert queue_refusal("run_shell", command, drive / "ws", wsl=True, wsl_cwd=WSL_CWD) is None, command
        assert audit_refusal("run_shell", command, drive / "ws", wsl=True, wsl_cwd=WSL_CWD) is None, command


def test_the_failed_first_message_is_named_in_the_conflict_and_local_is_checked_too(tmp_path: Path) -> None:
    from chimera.core.code_session import EnvironmentFixedError

    store = CodeSessionStore(tmp_path / "s")
    store.fix_environment("c1", {"id": "e1", "name": "n", "kind": "wsl"})
    # A turn that chose local after another message fixed WSL is refused, and says what to do.
    with pytest.raises(EnvironmentFixedError, match="open a new conversation"):
        store.fix_environment("c1", None)
    # Local on a conversation with no record writes nothing and passes.
    store.fix_environment("c2", None)
    assert store.stored_environment("c2") is None


# -- cd with bare words, and options that enter a folder ------------------------------------------

#: (first component under the drive, the rest) of what the fences guard.
SENSITIVE = [
    ("chimera-home", "approvals/x.json"),
    ("chimera-home", "mcp_oauth/h.json"),
    ("app", ".env"),
]
BARE_CD = [
    "cd /mnt; cd c; cat {p}",
    "cd /mnt/c; cd {first}; cat {rest}",
    "cd /mnt/c && cd {first} && cat {rest}",
    "pushd /mnt/c; pushd {first}; cat {rest}",
    "cd /mnt\ncd c\ncd {first}\ncat {rest}",
]
ENTERS_FOLDER = [
    "env -C /mnt/c cat {p}",
    "tar -C /mnt/c -cf - {p}",
    "tar -C/mnt/c -cf - {p}",
    "env -C /mnt/c/{first} cat {rest}",
    "tar --directory /mnt/c/{first} -cf - {rest}",
    "tar --directory=/mnt/c/{first} -cf - {rest}",
    "make -C /mnt/c/{first} {rest}",
]


@pytest.mark.parametrize("template", [*BARE_CD, *ENTERS_FOLDER])
@pytest.mark.parametrize(("first", "rest"), SENSITIVE)
def test_a_cd_into_bare_words_or_a_dash_c_folder_still_reaches_the_guarded_files(
    drive: Path, template: str, first: str, rest: str
) -> None:
    from chimera.tools.workspace import queue_refusal

    command = template.format(p=f"{first}/{rest}", first=first, rest=rest)
    refused = queue_refusal("run_shell", command, drive / "ws", wsl=True, wsl_cwd=WSL_CWD)
    assert refused is not None and "did NOT run" in refused, command


@pytest.mark.parametrize(
    "template",
    ["cd /mnt; cd c; cd chimera-home; rm aud*", "env -C /mnt/c/chimera-home rm aud*", "tar -C/mnt/c -cf - chimera-home/aud*"],
)
def test_the_same_holds_for_the_audit_log(drive: Path, template: str) -> None:
    from chimera.tools.workspace import audit_refusal

    refused = audit_refusal("run_shell", template, drive / "ws", wsl=True, wsl_cwd=WSL_CWD)
    assert refused is not None and "did NOT run" in refused, template


HARMLESS = [
    "ls",
    "cat src/x.py",
    "cd .. && ls",
    "cd src && cat x.py",
    "grep -r foo .",
    "git status",
    "cd /mnt/c && ls",
    "ls /mnt/d",
    "grep -C 3 foo src/x.py",
    "env -C /mnt/c/ws ls",
    "tar -C /mnt/c/ws -cf - src",
    "cd /mnt; cd c; cd ws; cat src/x.py",
]


@pytest.mark.parametrize("command", HARMLESS)
def test_ordinary_wsl_commands_are_not_refused_by_any_reading(drive: Path, command: str) -> None:
    from chimera.tools.workspace import audit_refusal, queue_refusal

    assert queue_refusal("run_shell", command, drive / "ws", wsl=True, wsl_cwd=WSL_CWD) is None, command
    assert audit_refusal("run_shell", command, drive / "ws", wsl=True, wsl_cwd=WSL_CWD) is None, command


def test_bare_cd_targets_and_dash_c_move_the_folder_later_words_are_read_against() -> None:
    assert windows_view("cd /mnt; cd c; cat Users/x", "/mnt/c/ws").endswith("cat C:/Users/x")
    # `-C` holds for ONE command only.
    view = windows_view("env -C /mnt/c ls; cat Users/x", "/mnt/c/ws", follow_cd=True)
    assert view.endswith("cat C:/ws/Users/x")
    assert windows_view("tar -C/mnt/c -cf - a", "").startswith("tar -CC:/")
    # Absolute words only: the relative tail is left exactly as written.
    assert windows_view("env -C /mnt/c cat Users/x", "", follow_cd=False) == "env -C C:/ cat Users/x"
