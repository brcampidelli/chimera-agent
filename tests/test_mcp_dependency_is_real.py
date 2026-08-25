"""The MCP screen is decoration unless the SDK actually ships, and unless it says so when it does not.

Three things went wrong together, and each hid the next:

1. The desktop installer built the backend with ``--extra desktop --extra documents --extra stt``
   and **not** ``--extra mcp``, so the packaged app could not speak MCP at all. Verified by
   searching the frozen binary for the SDK's module names, with ``chimera.integrations.mcp_client``
   and ``litellm.utils`` as controls to prove the search finds what IS present.
2. The SDK is imported inside ``_serve``, which runs on a background loop. With the package absent
   the ImportError was raised where nothing was listening, ``_ready`` never fired, and ``start``
   reported *"did not become ready"* — the SAME sentence a command that simply is not an MCP server
   produces. So the one user who could have diagnosed this was told to check their config.
3. ``mcp>=1.0`` had no ceiling and resolves to 2.x, which removed the low-level decorator API
   ``ChimeraMCP.build`` is written against. The CLIENT half is fine on both; the SERVER half dies
   with ``AttributeError: 'Server' object has no attribute 'list_tools'``.

These are cheap to assert and were expensive to find, which is exactly the trade a test is for.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def test_the_installer_bundles_the_sdk() -> None:
    """Without this the catalogue offers servers the shipped app can never connect to.

    Asserted against the workflow rather than a built binary, because a test cannot freeze one —
    but the line it checks is the line that was missing, and it is one word.
    """
    fluxo = (RAIZ / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")
    sync = [linha for linha in fluxo.splitlines() if "uv sync --extra" in linha]

    assert sync, "the sidecar build no longer syncs extras — this guard is now looking at nothing"
    assert any("--extra mcp" in linha for linha in sync), (
        "the desktop sidecar is built without the MCP SDK, so every catalogue entry fails to "
        f"connect in the packaged app. Extras line: {sync}"
    )


def test_the_sdk_pin_has_a_ceiling() -> None:
    """2.x removed the API `ChimeraMCP.build` uses, and `>=1.0` resolves straight into it."""
    dados = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
    extras = dados["project"]["optional-dependencies"]["mcp"]
    mcp = [d for d in extras if d.startswith("mcp")]

    assert mcp, "the mcp extra no longer pins the SDK"
    assert re.search(r"<\s*2", mcp[0]), (
        f"{mcp[0]!r} admits mcp 2.x, where `@server.list_tools()` raises AttributeError and "
        "`chimera mcp-serve` dies on start"
    )


def test_a_missing_sdk_says_so_rather_than_timing_out() -> None:
    """The message is the fix.

    A user whose install lacks the optional dependency has to be told THAT, not handed the same
    sentence a wrong command produces — the two have opposite remedies and only one of them is
    about their configuration.
    """
    import chimera.integrations.mcp_client as mod

    real = __import__

    def sem_mcp(nome: str, *args: object, **kw: object):
        if nome == "mcp" or nome.startswith("mcp."):
            raise ModuleNotFoundError("No module named 'mcp'")
        return real(nome, *args, **kw)  # type: ignore[arg-type]

    sessao = mod.StdioMCPSession("python", ["-c", "pass"], None, connect_timeout=1.0)
    import builtins

    original = builtins.__import__
    builtins.__import__ = sem_mcp  # type: ignore[assignment]
    try:
        with pytest.raises(ModuleNotFoundError) as erro:
            sessao.start()
    finally:
        builtins.__import__ = original

    texto = str(erro.value)
    assert "chimera-agent[mcp]" in texto, f"the message does not say how to fix it: {texto!r}"
    assert "did not become ready" not in texto, (
        "a missing dependency is still reported as a server that failed to start"
    )


def test_it_still_fails_normally_when_the_sdk_is_there() -> None:
    """Guarding the guard: raising ModuleNotFoundError unconditionally would satisfy the test above
    and break every working install. With the SDK present, a command that is not a server must
    still reach the ordinary timeout."""
    pytest.importorskip("mcp")
    import chimera.integrations.mcp_client as mod

    sessao = mod.StdioMCPSession("python", ["-c", "pass"], None, connect_timeout=2.0)
    with pytest.raises((TimeoutError, Exception)) as erro:
        sessao.start()

    assert not isinstance(erro.value, ModuleNotFoundError), (
        "a working install is being told the SDK is missing"
    )
