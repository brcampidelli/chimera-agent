"""Letting the agent run commands, for one project rather than for all of them.

The machine this app runs on already executes host commands two ways a caller can reach: the verify
command goes to `CommandVerifier` (which, at the time, called `subprocess.run(shell=True)` on the
host; it now runs where the shell runs), and the Runner panel spawns processes with no gate at all.
The only thing refused was the AGENT — so this machine would run pytest to judge the agent's work
and refuse to let the agent run pytest to correct it.

That refusal was also invisible in the right way and misleading in another: `host_exec=ask` resolves
to a refusal here because a server has no terminal to ask at, which the posture screen reports
honestly — but it meant an owner who wanted their agent to run tests had exactly one lever, and it
was global.

**Two locks.** `posture.reach` decides whether the shell tools are mounted; `allow_host_exec`
decides whether a mounted one may run on the host. Neither does anything alone, which is the
property most worth testing: a single-lock design fails open the day someone sets one of them for
an unrelated reason.

**And a record behind both.** Until study 29 (P4.3) the per-folder grant lived in the desktop's
`localStorage` and reached the server only as those two fields, so the request WAS the grant: the
desktop, the bridge's Full tier and any local process were believed. The grant is now a row in the
server's project registry, and the two locks are a request held to it — a folder nobody granted
gets the reach below `workspace_shell` and a gated tool, whatever the request says. The tests that
open both locks therefore record the grant first, the way the Folders card does; the ones that do
not are the point of the change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.api.code_api import CodeSeams, assemble_registry
from chimera.config import Settings
from chimera.core.code_projects import CodeProjectRegistry


class _Gateway:
    """Enough of a gateway for the registry to be built. Nothing here calls a model."""

    def complete(self, *_a: Any, **_kw: Any) -> Any:  # pragma: no cover - never reached
        raise AssertionError("no model call belongs in this test")


def _registro(tmp_path: Path) -> CodeProjectRegistry:
    return CodeProjectRegistry(tmp_path / "home" / "code_projects.json")


def _conceder(tmp_path: Path, folder: Path | None = None) -> None:
    """Record the owner's grant for a folder (the test's workspace by default)."""
    _registro(tmp_path).set_grant(str(folder or tmp_path), True)


def _montar(tmp_path: Path, *, ws: Path | None = None, **over: Any) -> Any:
    settings_over = over.pop("_settings", {})
    grant_root = over.pop("_grant_root", None)
    seams = CodeSeams(**over)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), **settings_over)
    registry, _ledger = assemble_registry(
        seams, ws or tmp_path, settings, _Gateway(), steps=4, surface="test",
        grant_root=grant_root,
    )
    return registry


def _tem_shell(registry: Any) -> bool:
    return "run_shell" in set(registry.names())


def _gated(registry: Any) -> bool:
    """Whether the mounted shell tool still has a confirmation gate in front of it.

    Unwrapped, because  layers the tool: the trust kernel and the taint ledger
    each wrap it, and the gate is a field on the RunShellTool at the bottom. The first version of
    this helper read the outermost object, found no  on a LedgeredTool, and reported
    every registry as ungated — including the one that was gated.
    """
    tool = getattr(registry, "_tools", {}).get("run_shell")
    if tool is None:
        return False
    for _ in range(6):
        interno = getattr(tool, "_inner", None) or getattr(tool, "inner", None) or getattr(tool, "_tool", None)
        if interno is None:
            break
        tool = interno
    return getattr(tool, "_confirm", None) is not None


#: Every request the app makes carries one. `CodeSeams.posture` defaults to None, and a None posture
#: denies nothing at all — which `Code.tsx` names in its own comment ("omitting resolves to no tool
#: denials and no pause at all, which is more permissive than any corner someone could have
#: picked") and is why that screen sends the posture on every single request. These tests send one
#: too, because a test of the default-when-nobody-sends-one is a test of a case the product does not
#: produce.
LEITURA = {"reach": "read_only"}
ESCRITA = {"reach": "workspace"}
COM_SHELL = {"reach": "workspace_shell"}


def test_neither_lock_alone_mounts_a_runnable_shell(tmp_path: Path) -> None:
    """The property a single-lock design would lose.

    Asking for host execution under a reach that does not mount the tools changes nothing: there is
    no shell tool to ungate. This is the case that fails open the day the two are collapsed into one
    field, and it is the reason they are two.
    """
    registry = _montar(tmp_path, posture=ESCRITA, allow_host_exec=True)
    assert not _tem_shell(registry), "the reach lock did not hold"


def test_the_reach_alone_mounts_the_tool_but_leaves_it_gated(tmp_path: Path) -> None:
    """Unchanged behaviour in a granted folder: a reach that mounts the shell still meets the
    confirmation gate, which on a server with no terminal is a refusal."""
    _conceder(tmp_path)
    registry = _montar(tmp_path, posture=COM_SHELL)
    assert _tem_shell(registry), "workspace_shell did not mount the shell tool"
    assert _gated(registry), "the tool was ungated without anybody asking"


def test_both_locks_together_let_the_agent_run_commands(tmp_path: Path) -> None:
    _conceder(tmp_path)
    registry = _montar(tmp_path, posture=COM_SHELL, allow_host_exec=True)
    assert _tem_shell(registry)
    assert not _gated(registry), "both locks were open and the tool was still gated"


def test_the_owner_can_refuse_system_wide(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A field on a request does not overrule `CHIMERA_HOST_EXEC=deny`.

    The per-project switch exists so somebody can grant their agent less than everything, not so a
    request can grant itself more than the machine's owner allowed.
    """
    monkeypatch.setenv("CHIMERA_HOST_EXEC", "deny")
    from chimera.config import get_settings

    get_settings.cache_clear()
    # Granted, so the only thing standing between the request and the host is the owner's `deny`.
    _conceder(tmp_path)
    try:
        seams = CodeSeams(posture=COM_SHELL, allow_host_exec=True)
        settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_HOST_EXEC="deny")
        registry, _ = assemble_registry(
            seams, tmp_path, settings, _Gateway(), steps=4, surface="test"
        )
        assert _gated(registry), "a request overrode the owner's refusal"
    finally:
        get_settings.cache_clear()


def test_the_default_is_unchanged(tmp_path: Path) -> None:
    """The compatibility promise, asserted rather than assumed: the posture the screen actually
    sends gets the app it had before — no shell tool at all."""
    registry = _montar(tmp_path, posture=ESCRITA)
    assert not _tem_shell(registry)


def test_read_only_stays_read_only_however_loudly_asked(tmp_path: Path) -> None:
    """The strictest reach is not negotiable by a field further down the same request — nor by the
    folder's grant: the grant answers "may it run commands HERE", not "may it do more than the
    owner's standing reach"."""
    _conceder(tmp_path)
    registry = _montar(tmp_path, posture=LEITURA, allow_host_exec=True)
    assert not _tem_shell(registry)


# ---------------------------------------------------------------- the record behind the locks


def test_a_client_claim_without_a_server_grant_gets_no_shell(tmp_path: Path) -> None:
    """THE change. Both locks open in the request, nothing recorded for the folder: no shell tool.

    Before, this was exactly the request the desktop sent for a folder its `localStorage` listed —
    and the server had nothing to check it against, so any client that sent it was granted.
    """
    registry = _montar(tmp_path, posture=COM_SHELL, allow_host_exec=True)
    assert not _tem_shell(registry), "a request granted itself the shell"


def test_no_posture_and_a_host_exec_claim_still_meets_the_gate(tmp_path: Path) -> None:
    """The other door: a request with NO posture denies nothing, so the shell is mounted — and
    `allow_host_exec` alone used to ungate it. Without a recorded grant it stays gated."""
    registry = _montar(tmp_path, allow_host_exec=True)
    assert _tem_shell(registry)
    assert _gated(registry), "allow_host_exec ungated the shell with no grant on record"


def test_a_grant_on_another_folder_does_not_carry_over(tmp_path: Path) -> None:
    outro = tmp_path / "outro"
    outro.mkdir()
    _conceder(tmp_path, outro)
    registry = _montar(tmp_path, posture=COM_SHELL, allow_host_exec=True)
    assert not _tem_shell(registry)


def test_the_grant_follows_the_folder_not_the_spelling(tmp_path: Path) -> None:
    """Recorded as one string, asked for as another that resolves to the same directory. The grant
    is about where a command runs, and that is the resolved folder."""
    projeto = tmp_path / "loja"
    projeto.mkdir()
    _registro(tmp_path).set_grant(str(tmp_path / "loja" / ".." / "loja"), True)
    registry = _montar(tmp_path, ws=projeto, posture=COM_SHELL, allow_host_exec=True)
    assert _tem_shell(registry) and not _gated(registry)


def test_a_revoked_grant_takes_the_shell_away(tmp_path: Path) -> None:
    _conceder(tmp_path)
    _registro(tmp_path).set_grant(str(tmp_path), False)
    registry = _montar(tmp_path, posture=COM_SHELL, allow_host_exec=True)
    assert not _tem_shell(registry)


def test_hiding_a_folder_revokes_its_grant(tmp_path: Path) -> None:
    """A folder taken out of the lists is one nobody can see a grant on to take it back."""
    _conceder(tmp_path)
    _registro(tmp_path).set_flags(str(tmp_path), hidden=True)
    registry = _montar(tmp_path, posture=COM_SHELL, allow_host_exec=True)
    assert not _tem_shell(registry)


def test_the_owners_reach_everywhere_is_a_grant_everywhere(tmp_path: Path) -> None:
    """`CHIMERA_REACH=workspace_shell` is the owner granting every folder at once, which is what the
    bridge has always sent `allow_host_exec` for. No per-folder row is needed."""
    registry = _montar(
        tmp_path, posture=COM_SHELL, allow_host_exec=True,
        _settings={"CHIMERA_REACH": "workspace_shell"},
    )
    assert _tem_shell(registry) and not _gated(registry)


def test_a_worker_in_a_copy_uses_the_projects_grant(tmp_path: Path) -> None:
    """A crew worker runs in its own worktree, a folder no grant names. The project it was cut from
    is the one the owner granted, and `grant_root` is how the server says so — never the request."""
    _conceder(tmp_path)
    copia = tmp_path / "worktree"
    copia.mkdir()
    sem = _montar(tmp_path, ws=copia, posture=COM_SHELL, allow_host_exec=True)
    com = _montar(
        tmp_path, ws=copia, posture=COM_SHELL, allow_host_exec=True, _grant_root=tmp_path
    )
    assert not _tem_shell(sem)
    assert _tem_shell(com) and not _gated(com)


def test_the_grant_root_cannot_be_sent_by_a_request() -> None:
    """The worktree's root is server state. A request field named like it is ignored, not obeyed —
    otherwise any request could borrow the grant of any folder it could name."""
    seams = CodeSeams.model_validate({"_grant_root": "/somewhere/granted", "grant_root": "/x"})
    assert seams._grant_root is None


def test_a_batch_task_carries_the_projects_folder_to_the_grant(tmp_path: Path) -> None:
    """`POST /api/agents` runs each task in a worktree cut from the project. The server stamps the
    project on the task's seams so the grant is looked up there, and a request cannot set it."""
    from fastapi.testclient import TestClient

    from chimera.api import build_api_app
    from chimera.interface.session import ChatSession

    ws = tmp_path / "plain"
    ws.mkdir()
    seen: list[Any] = []

    class _Done:
        def run(self, task: str) -> Any:
            from chimera.core.autonomous import AutonomousResult

            return AutonomousResult(answer="ok", success=True)

    def factory(req: Any, run_ws: Any, *_rest: Any) -> Any:
        seen.append(req._grant_root)
        return _Done()

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(
        build_api_app(lambda: ChatSession(_Done()), settings=settings, solve_agent_factory=factory)
    )
    client.post(
        "/api/agents",
        json={"tasks": [{"task": "t"}], "workspace": str(ws), "_grant_root": "/elsewhere"},
    )
    assert seen == [ws.resolve()]
