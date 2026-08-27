"""Letting the agent run commands, for one project rather than for all of them.

The machine this app runs on already executes host commands two ways a caller can reach: the verify
command goes to `CommandVerifier`, which calls `subprocess.run(shell=True)`, and the Runner panel
spawns processes with no gate at all. The only thing refused was the AGENT — so this machine would
run pytest to judge the agent's work and refuse to let the agent run pytest to correct it.

That refusal was also invisible in the right way and misleading in another: `host_exec=ask` resolves
to a refusal here because a server has no terminal to ask at, which the posture screen reports
honestly — but it meant an owner who wanted their agent to run tests had exactly one lever, and it
was global.

**Two locks.** `posture.reach` decides whether the shell tools are mounted; `allow_host_exec`
decides whether a mounted one may run on the host. Neither does anything alone, which is the
property most worth testing: a single-lock design fails open the day someone sets one of them for
an unrelated reason.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.api.code_api import CodeSeams, assemble_registry
from chimera.config import Settings


class _Gateway:
    """Enough of a gateway for the registry to be built. Nothing here calls a model."""

    def complete(self, *_a: Any, **_kw: Any) -> Any:  # pragma: no cover - never reached
        raise AssertionError("no model call belongs in this test")


def _montar(tmp_path: Path, **over: Any) -> Any:
    seams = CodeSeams(**over)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), **over.pop("_settings", {}))
    registry, _ledger = assemble_registry(
        seams, tmp_path, settings, _Gateway(), steps=4, surface="test"
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
    """Unchanged behaviour: a reach that mounts the shell still meets the confirmation gate, which
    on a server with no terminal is a refusal. This is what every install did before, and does."""
    registry = _montar(tmp_path, posture=COM_SHELL)
    assert _tem_shell(registry), "workspace_shell did not mount the shell tool"
    assert _gated(registry), "the tool was ungated without anybody asking"


def test_both_locks_together_let_the_agent_run_commands(tmp_path: Path) -> None:
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
    """The strictest reach is not negotiable by a field further down the same request."""
    registry = _montar(tmp_path, posture=LEITURA, allow_host_exec=True)
    assert not _tem_shell(registry)
