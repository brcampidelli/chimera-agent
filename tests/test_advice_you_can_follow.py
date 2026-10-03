"""Two places the product gave advice nobody could act on.

**A setting that does not exist.** `config.py` tells a reader that `wilson` mode is strict on small
panels and to "use it with panels >= ~5, or lower `CHIMERA_SKILL_MIN_TRANSFER`". There is no such
variable. `min_transfer` is a constructor default of 0.5 in `AutoSkillEvolver`, and
`build_evolution_context` never passes it — so it is unreachable by configuration. `auto_evolve.py`
logs the same advice at runtime, to a user who cannot follow it.

**Labels that promise a tool.** The settings screen lists Brave, SerpAPI and Stability beside
Tavily and ElevenLabs. Tavily and ElevenLabs have tools that auto-register the moment the key is
set. The other three have none: `chimera/tools/web.py` implements only Tavily, and grep finds no
Brave, SerpAPI or Stability call anywhere. Setting one changes nothing at all.

The first fix relabelled them "no built-in tool; import its OpenAPI spec", and that was the same
defect one level down: the OpenAPI→tool importer's only caller outside the tests is `chimera
schema-bench`, which counts schema tokens and registers nothing, so no surface lets anyone import a
spec — and nothing would hand an imported tool one of these keys if it did. They are reserved slots,
which is what `.env.example` already called Stability while the screen said "Stability (images)".
"""

from __future__ import annotations

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


def test_only_the_credentials_with_a_built_in_tool_claim_one() -> None:
    """Every label on the settings screen has to match what the key actually buys.

    Tavily and ElevenLabs auto-register a tool. Brave, SerpAPI and Stability register nothing, and
    a label like "Brave (web search)" beside "Tavily (web search)" says they are the same kind of
    thing. They are not: one works when you paste the key and the other does nothing at all.
    """
    built_in = {"TAVILY_API_KEY", "ELEVENLABS_API_KEY"}

    for env, label in _TOOL_CREDENTIALS.items():
        if env in built_in:
            assert "no built-in tool" not in label, f"{env} HAS a tool; the label denies it"
        else:
            assert "no built-in tool" in label, f"{env} has no tool and the label implies one"


def test_no_tool_module_secretly_reads_one_of_them() -> None:
    """The claim behind the labels, checked rather than assumed.

    If somebody implements a Brave tool tomorrow, this fails and the label has to be corrected —
    which is the right direction for the failure to point.
    """
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

    for field in ("brave_api_key", "serpapi_key", "stability_api_key"):
        assert field not in read, f"{field} is implemented now — fix its label"


def test_no_label_sends_the_user_to_an_importer_they_cannot_reach() -> None:
    """The second promise, the one the first fix introduced.

    "import its OpenAPI spec" names an action, and there is nowhere to perform it: the importer's
    only non-test caller is `schema-bench` (held by the test below). A reserved slot has to say it
    is reserved, not describe a route.
    """
    for env in ("BRAVE_API_KEY", "SERPAPI_API_KEY", "STABILITY_API_KEY"):
        label = _TOOL_CREDENTIALS[env]
        assert "reserved" in label, f"{env} buys nothing and the label does not say so"
        assert "openapi" not in label.lower(), f"{env} points at an import nobody can perform"


def test_the_openapi_importer_still_has_no_caller_that_gives_the_agent_a_tool() -> None:
    """The premise of the label above, checked rather than remembered.

    Every call of `tools_from_openapi` under `chimera/` is found by parsing, and the one allowed
    caller is `schema_bench`, which measures schema size. If someone wires an import into a screen
    or a command, this fails — and the reserved labels may then honestly point at it again.
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
    assert callers == {"cli/main.py::schema_bench"}, f"the importer is reachable now: {callers}"
