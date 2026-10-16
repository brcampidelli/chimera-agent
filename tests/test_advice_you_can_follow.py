"""Two places the product gave advice nobody could act on.

**A setting that does not exist.** `config.py` tells a reader that `wilson` mode is strict on small
panels and to "use it with panels >= ~5, or lower `CHIMERA_SKILL_MIN_TRANSFER`". There is no such
variable. `min_transfer` is a constructor default of 0.5 in `AutoSkillEvolver`, and
`build_evolution_context` never passes it — so it is unreachable by configuration. `auto_evolve.py`
logs the same advice at runtime, to a user who cannot follow it.

**Labels that promise a tool.** The search-provider keys now activate the multi-provider
`web_search` adapter; ElevenLabs activates TTS. SerpAPI and Stability remain reserved for explicitly
configured OpenAPI connectors. These tests keep the configuration labels aligned with those actual
capabilities and check that the connector route remains reachable for its reserved credentials.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.api.config_api import _TOOL_CREDENTIALS
from chimera.config import Settings


def test_the_transfer_threshold_is_a_real_setting() -> None:
    # The variable the comment names, by the name the comment gives it.
    assert Settings().skill_min_transfer == pytest.approx(0.5)
    assert (
        Settings.model_fields["skill_min_transfer"].validation_alias
        == "CHIMERA_SKILL_MIN_TRANSFER"
    )


def test_setting_it_changes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_SKILL_MIN_TRANSFER", "0.3")

    assert Settings().skill_min_transfer == pytest.approx(0.3)


def test_the_evolver_is_built_with_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A field nothing reads is the same defect wearing a name.

    This is the half that matters: the setting existing does not make the advice followable — the
    evolver has to be constructed with it. `build_evolution_context` was passing nothing, so the
    0.5 default was hard-coded no matter what anyone configured.
    """
    seen: dict[str, object] = {}

    class _Spy:
        def __init__(self, *_a: object, **kw: object) -> None:
            seen.update(kw)

    monkeypatch.setattr("chimera.evolution.context.AutoSkillEvolver", _Spy)
    monkeypatch.setenv("CHIMERA_SKILL_MIN_TRANSFER", "0.25")
    # A cunhagem passou a seguir a leitura, e este teste e' sobre o valor CHEGAR ao construtor —
    # nao sobre quando ele e' chamado. Ligar os cartoes aqui mantem o teste sobre o que ele testa.
    monkeypatch.setenv("CHIMERA_SKILL_CARDS", "1")

    from chimera.evolution.context import build_evolution_context

    build_evolution_context(
        gateway=object(), model="m", home=__import__("pathlib").Path("."),
        settings=Settings(), evolve_skills=True,
    )

    assert seen.get("min_transfer") == pytest.approx(0.25)


def test_only_credentials_with_a_built_in_tool_claim_one() -> None:
    """Search-provider keys and ElevenLabs each activate their described built-in capability."""
    built_in = {
        "TAVILY_API_KEY", "FIRECRAWL_API_KEY", "EXA_API_KEY", "BRAVE_API_KEY",
        "ELEVENLABS_API_KEY",
    }

    for env, label in _TOOL_CREDENTIALS.items():
        if env in built_in:
            assert "no built-in tool" not in label, f"{env} HAS a tool; the label denies it"
        else:
            assert "no built-in tool" in label, f"{env} has no tool and the label implies one"


def test_search_provider_labels_match_the_available_web_search_capability() -> None:
    for env in ("TAVILY_API_KEY", "FIRECRAWL_API_KEY", "EXA_API_KEY", "BRAVE_API_KEY"):
        label = _TOOL_CREDENTIALS[env].lower()
        assert "web search" in label
        assert "no built-in tool" not in label
        assert "openapi" not in label


def test_provider_tool_fields_are_not_mistaken_for_reserved_openapi_credentials() -> None:
    import ast
    from pathlib import Path

    tools = Path(__file__).resolve().parent.parent / "chimera" / "tools"
    read = {
        node.attr
        for path in tools.glob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Attribute)
    }
    assert {"firecrawl_api_key", "exa_api_key", "brave_api_key"} <= read


def test_no_tool_module_secretly_reads_one_of_them() -> None:
    """Reserved SerpAPI and Stability credentials must not accidentally gain tool consumers."""
    import ast
    from pathlib import Path

    # Attribute READS, found by parsing — not a substring search over the file.
    #
    # The first version of this grepped for the names and failed on `web.py`'s own docstring, which
    # says "Brave/SerpAPI follow the same shape". Prose about a capability is not the capability,
    # and a check that cannot tell them apart is the same mistake this file is about.
    tools = Path(__file__).resolve().parent.parent / "chimera" / "tools"
    read: set[str] = set()
    for path in tools.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute):
                read.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                continue  # a string literal is not an access

    for field in ("serpapi_key", "stability_api_key"):
        assert field not in read, f"{field} is implemented now — fix its label"


def test_a_reserved_label_points_at_an_importer_that_exists_and_can_read_the_key(
    tmp_path: Path,
) -> None:
    """The second promise, made true rather than withdrawn (study 29, P7.5).

    This test used to assert that no label mentioned OpenAPI, because the importer had no caller
    that gave the agent a tool. That premise is gone — Connections › OpenAPI adds a connector — so
    the label points there again, and this checks the two halves the old label lacked: the route
    the label sends the owner to exists, and a connector there may actually read this key.
    """
    from typing import cast

    from chimera.api.app import build_api_app
    from chimera.integrations.openapi_store import allowed_key_envs
    from chimera.interface import ChatSession
    from chimera.interface.session import SupportsRun

    app = build_api_app(
        lambda: ChatSession(cast(SupportsRun, None)),
        settings=Settings(CHIMERA_HOME=str(tmp_path / "home")),  # type: ignore[call-arg]
        workspace=tmp_path,
    )
    routes = {
        (method, getattr(route, "path", ""))
        for route in app.routes
        for method in getattr(route, "methods", set()) or set()
    }
    assert ("POST", "/api/connectors") in routes, "the label sends the owner to a screen with no door"
    for env in ("SERPAPI_API_KEY", "STABILITY_API_KEY"):
        label = _TOOL_CREDENTIALS[env]
        assert "no built-in tool" in label, f"{env} still buys nothing on its own and must say so"
        assert "OpenAPI" in label and "Connections" in label, f"{env} does not say where it is used"
        assert env in allowed_key_envs("anything"), f"no connector may read {env}, so the label lies"


def test_the_openapi_importer_reaches_the_agent_only_through_the_owners_store() -> None:
    """The importer's callers, checked rather than remembered.

    Two callers and no third: `schema_bench`, which measures schema size, and the store's
    `connector_tools`, which builds tools only for connectors the owner added and switched on. A
    call anywhere else would be a way to hand the agent a spec's tools that skips the owner, the
    pinned snapshot and the GET-only rule — so it fails here first.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "chimera"
    callers: set[str] = set()
    for path in root.rglob("*.py"):
        if path.parts[-2] == "integrations" and path.name == "openapi.py":
            continue  # the definition, not a use
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "tools_from_openapi"
                ):
                    callers.add(f"{path.relative_to(root).as_posix()}::{func.name}")

    assert callers, "found no caller at all — the scan is inert, not the importer unused"
    assert callers == {
        "cli/commands/bench.py::schema_bench",
        "integrations/openapi_store.py::connector_tools",
    }, f"the importer is reachable another way now: {callers}"
