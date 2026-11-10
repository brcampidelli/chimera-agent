"""Twenty-two tool schemas went out on every step; four tools did all the work.

Measured on twenty-eight sessions of an installed 0.48.0 — 33 tool calls in all — `read_file` (14),
`write_file` (10), `list_dir` (7) and `edit_file` (2) account for every one. The eighteen never
called cost ~2,745 tokens of the ~3,205-token schema, re-sent on each step of each turn.

`chimera.integrations.mcp_defer` already answers this shape for MCP servers, and says why it is off
by default: the saving is in tokens and the risk is in selection accuracy — a model that has to look
a tool up may choose worse than one handed the list, and nobody had measured that.

**The core is what keeps this from taking that risk where it would hurt.** Files, search and shell
stay declared in full; only the rest defers. For all 33 observed calls the declared set is unchanged,
so the saving comes from schemas the model never reached for.

The tests below split in two, and the second half is the one that matters. Saving tokens is the
point of the feature; not becoming a hole in the governance is the condition for shipping it.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.tools.base import Tool
from chimera.tools.defer import CORE, defer_builtins, describe_saving
from chimera.tools.registry import ToolRegistry

pytestmark = pytest.mark.product_default  # these are about deferral, which is on by default


class _Falsa(Tool):
    """A tool that reports being run, so a proxy reaching it is observable rather than inferred."""

    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self, nome: str) -> None:
        self.name = nome
        self.description = f"does {nome}"
        self.executada = False

    def run(self, **kwargs: Any) -> str:
        self.executada = True
        return f"{self.name} ran with {sorted(kwargs)}"


def _registro(*nomes: str) -> ToolRegistry:
    reg = ToolRegistry()
    for nome in nomes:
        reg.register(_Falsa(nome))
    return reg


# ------------------------------------------------------------------ what it is for


def test_the_core_stays_declared_and_the_rest_does_not() -> None:
    # `render_chart` is TURN_BOUND (it needs its canvas sink), so it stays declared with the core.
    reg = _registro("read_file", "write_file", "scrape", "crawl", "render_chart")

    enxuto, n = defer_builtins(reg)

    assert n == 2
    assert set(enxuto.names()) == {
        "read_file",
        "write_file",
        "render_chart",
        "tool_list",
        "tool_describe",
        "tool_call",
    }


def test_a_deferred_tool_is_still_reachable() -> None:
    """Deferral moves the schema out of the prompt; it must not move the capability out of reach."""
    reg = _registro("read_file", "scrape")
    alvo = reg.get("scrape")

    enxuto, _ = defer_builtins(reg)
    saida = enxuto.get("tool_call").run(tool="scrape", arguments={"url": "http://x"})

    assert alvo.executada is True  # type: ignore[attr-defined]
    assert "url" in saida


def test_the_listing_names_what_was_deferred() -> None:
    reg = _registro("read_file", "scrape", "crawl")

    enxuto, _ = defer_builtins(reg)
    listagem = enxuto.get("tool_list").run()

    assert "scrape" in listagem and "crawl" in listagem
    assert "read_file" not in listagem, "the core is already declared; listing it twice is noise"


def test_a_name_that_does_not_exist_suggests_instead_of_only_refusing() -> None:
    """A bare refusal leaves the model guessing between a typo and a missing capability, and those
    want different next moves."""
    reg = _registro("read_file", "scrape")
    enxuto, _ = defer_builtins(reg)

    assert "scrape" in enxuto.get("tool_describe").run(tool="scrap")
    assert "tool_list" in enxuto.get("tool_call").run(tool="inventada")


def test_nothing_outside_the_core_means_nothing_to_defer() -> None:
    """Zero deferred and the registry untouched — distinguishable from deferral not having run.

    An intervention that cannot tell "on and there was nothing to do" from "did not run" gets read
    as "on and useless" the first time it saves nothing.
    """
    reg = _registro("read_file", "write_file")

    enxuto, n = defer_builtins(reg)

    assert n == 0
    assert set(enxuto.names()) == {"read_file", "write_file"}
    assert "tool_list" not in enxuto


def test_it_reports_what_it_would_save_on_the_real_registry() -> None:
    """Measured against the shipped tools, because that is what an install pays for."""
    from pathlib import Path

    from chimera.tools import default_registry

    economia = describe_saving(default_registry(Path(".")))

    assert economia["core"] + economia["deferred"] == economia["tools"]
    assert economia["deferred_chars"] < economia["declared_chars"], economia


def test_deferral_can_COST_more_and_says_so() -> None:
    """Three proxies are not free, so below a threshold deferral is a loss — and the number reports
    it rather than being clamped to zero.

    This test exists because the first version of the one above asserted a saving against four
    trivial tools and went red: 1,482 characters of proxy replacing 552 of schema. That was the
    measurement being right and the expectation being wrong. Clamping it, or only ever measuring the
    favourable case, is how a feature gets turned on somewhere it makes things worse — the
    documentation would promise a saving the arithmetic never had.
    """
    reg = _registro("read_file", "scrape", "crawl")

    economia = describe_saving(reg)

    assert economia["deferred_chars"] > economia["declared_chars"], (
        "with three trivial tools the proxies cost more than the schemas they replace, "
        "and describe_saving must be able to say so"
    )


# ------------------------------------------------------------------ what it must not become
#
# Deferral takes the names out of the registry, so the restriction filter — which matches registry
# names — has nothing left to match. A denylist naming a deferred tool would silently stop removing
# it while one proxy could still run it. The MCP proxy documents this hole about itself; these are
# the tests that say this one does not have it.


def test_a_denied_tool_cannot_be_reached_through_the_proxy() -> None:
    """The security property of the whole module.

    Without this, turning deferral on would quietly widen every deployment's tool surface: the
    denylist would keep matching names that are no longer there, report success, and change nothing.
    """
    reg = _registro("read_file", "scrape")
    alvo = reg.get("scrape")

    enxuto, _ = defer_builtins(reg, denied=frozenset({"scrape"}))
    saida = enxuto.get("tool_call").run(tool="scrape", arguments={})

    assert alvo.executada is False, "a denied tool ran through the proxy"  # type: ignore[attr-defined]
    assert "no tool named" in saida
    assert "scrape" not in enxuto.get("tool_list").run()


def test_an_allowlist_bounds_what_the_proxy_can_see() -> None:
    reg = _registro("read_file", "scrape", "crawl")

    enxuto, _ = defer_builtins(reg, allowed=frozenset({"scrape"}))
    listagem = enxuto.get("tool_list").run()

    assert "scrape" in listagem
    assert "crawl" not in listagem
    assert "no tool named" in enxuto.get("tool_call").run(tool="crawl", arguments={})


def test_the_proxy_is_untrusted_output_whatever_it_runs() -> None:
    """The taint layer reads this flag before anyone knows which tool the proxy will run, and the
    deferred set holds `scrape`, `crawl`, `browser` and `http_get` — every one of them a way for the
    open web to reach the model. Mirroring the target's own flag would resolve, at the moment it is
    read, to "unknown"."""
    from chimera.tools.base import is_untrusted_output

    reg = _registro("read_file", "scrape")
    enxuto, _ = defer_builtins(reg)

    assert is_untrusted_output(enxuto.get("tool_call")) is True


def test_the_real_registry_keeps_every_core_tool() -> None:
    """Against the shipped registry, not a double.

    `CORE` is a list of names, and a name that no longer exists would silently shrink the declared
    set — the failure would look like the model losing a capability, one release after somebody
    renamed a tool.
    """
    from pathlib import Path

    from chimera.tools import default_registry

    reg = default_registry(Path("."))
    ausentes = sorted(nome for nome in CORE if nome not in reg)

    assert ausentes == [], f"CORE names a tool the registry does not have: {', '.join(ausentes)}"


# ------------------------------------------------------------------ that the switch reaches the app


def test_the_setting_changes_what_a_real_request_gets(tmp_path: Any, monkeypatch: Any) -> None:
    """Through `assemble_registry`, the function a coding turn actually calls.

    Every test above builds a registry by hand. A flag read nowhere on the request path is the
    failure this repository keeps finding in its own code — a capability with tests, wired to
    nothing — and asserting the module in isolation cannot tell that apart from working.
    """
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.config import Settings

    class _Gateway:
        def complete(self, *_a: Any, **_kw: Any) -> Any:  # pragma: no cover - never reached
            raise AssertionError("no model call belongs in this test")

    def monta(**over: Any) -> ToolRegistry:
        settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), **over)
        registry, _ = assemble_registry(
            CodeSeams(), tmp_path, settings, _Gateway(), steps=4, surface="test"
        )
        return registry

    # The default is on since 2026-11-10; the declared arm pins it off so the comparison is two arms.
    # This test's subject is the default, so the environment's pin (conftest) is removed for it.
    declarado = monta(CHIMERA_DEFER_TOOLS="0")
    deferido = monta(CHIMERA_DEFER_TOOLS="1")
    assert "tool_list" in monta(), "and the default is the deferred arm"

    assert "scrape" in declarado and "tool_list" not in declarado
    assert "tool_list" in deferido and "scrape" not in deferido
    assert len(deferido) < len(declarado)
    assert CORE & set(deferido.names()) == CORE & set(declarado.names()), (
        "the core must be identical either way — that is what keeps the saving free of risk "
        "for the tools observed use actually reaches"
    )


def test_the_cli_path_defers_too(tmp_path: Any, monkeypatch: Any) -> None:
    """`solve` and `run` route their tool governance through `_apply_tool_allowlist`, not through
    the API's `assemble_registry`.

    This was wired to the API only, and the gap surfaced the way gaps like this always do — by
    trying to MEASURE the feature and finding the bench would have exercised nothing. Ten call sites
    build a registry in `cli/main.py`; the toggle belongs at the one place they all route their
    governance through, after the restriction filter rather than before it.
    """
    from pathlib import Path

    from chimera.cli.main import _apply_tool_allowlist
    from chimera.config import Settings
    from chimera.tools import default_registry

    def monta(**over: Any) -> ToolRegistry:
        return _apply_tool_allowlist(
            default_registry(Path(tmp_path)),
            allow=None,
            deny=None,
            settings=Settings(CHIMERA_HOME=str(tmp_path / "home"), **over),
        )

    assert "tool_list" not in monta(CHIMERA_DEFER_TOOLS="0")
    assert "tool_list" in monta(CHIMERA_DEFER_TOOLS="1")
    assert "tool_list" in monta(), "the default is on since 2026-11-10"


def test_deferral_still_obeys_a_cli_denylist(tmp_path: Any) -> None:
    """Deferral runs after the filter, so `--deny-tools scrape` still removes `scrape` — and the
    proxy does not hand it back.

    The order is the whole property: deferral before the filter would leave the filter matching
    names that are no longer in the registry, reporting success and removing nothing.
    """
    from pathlib import Path

    from chimera.cli.main import _apply_tool_allowlist
    from chimera.config import Settings
    from chimera.tools import default_registry

    registry = _apply_tool_allowlist(
        default_registry(Path(tmp_path)),
        allow=None,
        deny="scrape",
        settings=Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_DEFER_TOOLS="1"),
    )

    assert "scrape" not in registry
    assert "scrape" not in registry.get("tool_list").run()
    assert "no tool named" in registry.get("tool_call").run(tool="scrape", arguments={})


def test_a_policy_change_after_deferral_still_denies_the_proxy(tmp_path: Any, monkeypatch: Any) -> None:
    """The owner edits `policy.json` with a conversation already open: the catalogue was built before,
    so the proxy has to ask again at call time, as `ToolRegistry.run` does for a declared tool."""
    import json

    from chimera.config import get_settings

    reg = _registro("read_file", "scrape")
    alvo = reg.get("scrape")
    enxuto, _ = defer_builtins(reg)
    assert "scrape" in enxuto.get("tool_list").run()  # permitted when deferred

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    home.mkdir(parents=True, exist_ok=True)
    (home / "policy.json").write_text(
        json.dumps({"rules": [{"action": "tool.use", "resource": "scrape", "effect": "deny"}]}),
        encoding="utf-8",
    )
    try:
        saida = enxuto.get("tool_call").run(tool="scrape", arguments={})
        assert alvo.executada is False, "the proxy ran a tool the policy now denies"  # type: ignore[attr-defined]
        assert "denied" in saida or "no tool named" in saida
        assert "scrape" not in enxuto.get("tool_list").run()
        assert "no tool named" in enxuto.get("tool_describe").run(tool="scrape")
    finally:
        get_settings.cache_clear()


def test_the_proxy_runs_through_the_registry_door() -> None:
    """One execution path: an argument the schema does not declare is refused by the shared door."""
    class _Tipada(_Falsa):
        parameters: dict[str, Any] = {"type": "object", "properties": {"url": {"type": "string"}}}

    reg = ToolRegistry()
    reg.register(_Falsa("read_file"))
    alvo = _Tipada("scrape")
    reg.register(alvo)
    enxuto, _ = defer_builtins(reg)

    saida = enxuto.get("tool_call").run(tool="scrape", arguments={"nope": 1})

    assert alvo.executada is False  # type: ignore[attr-defined]
    assert "nope" in saida


def test_a_turn_ledger_judges_the_target_behind_the_proxy() -> None:
    """`tool_call(todo_write)` is not a fetch and must not taint the turn; `tool_call(web_search)` is."""
    from chimera.governance import TaintLedger, ledger_registry

    class _Web(_Falsa):
        untrusted_output = True

    reg = ToolRegistry()
    reg.register(_Falsa("read_file"))
    reg.register(_Falsa("todo_write"))
    reg.register(_Web("web_search"))
    enxuto, _ = defer_builtins(reg)

    def turno(alvo: str) -> bool:
        ledger = TaintLedger()
        guarded = ledger_registry(enxuto, ledger, approve=lambda *_a, **_k: True)
        guarded.run("tool_call", tool=alvo, arguments={})
        return ledger.taint_epoch > 0

    assert turno("todo_write") is False
    assert turno("web_search") is True


def test_a_chat_turn_with_the_real_default_hides_and_refuses(tmp_path: Any) -> None:
    """The chat surface as `serve.py` builds it: default settings (deferral on), the guard's denials
    handed to the deferral, then `guard_chat_registry`."""
    from chimera.api.posture import chat_guard_denials, guard_chat_registry
    from chimera.config import Settings
    from chimera.tools import default_registry

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    assert settings.defer_tools is True
    for negada in chat_guard_denials():
        registro, _ = defer_builtins(default_registry(tmp_path), denied=frozenset(chat_guard_denials()))
        guarded, _ledger = guard_chat_registry(registro)  # a fresh ledger per call: no taint carries over
        assert "tool_call" in guarded
        assert negada not in guarded.names()
        assert "no tool named" in guarded.run("tool_call", tool=negada, arguments={})


def test_a_pack_that_denies_a_tool_is_refused_through_assemble_registry(tmp_path: Any) -> None:
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.config import Settings

    class _Gateway:
        def complete(self, *_a: Any, **_kw: Any) -> Any:  # pragma: no cover
            raise AssertionError("no model call belongs in this test")

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_TOOL_DENYLIST="scrape")
    registry, _ = assemble_registry(CodeSeams(), tmp_path, settings, _Gateway(), steps=4, surface="test")

    assert "tool_call" in registry
    assert "scrape" not in registry.declared_names()
    assert "no tool named" in registry.run("tool_call", tool="scrape", arguments={"url": "http://x"})
