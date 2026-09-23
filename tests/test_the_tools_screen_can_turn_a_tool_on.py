"""The Tools screen can turn a tool ON, not only off.

It listed only what the registry held, so a tool behind a condition — a setting off by default, a key
not configured, a package not installed — was invisible exactly when it was off. The screen could
deny a tool and could not enable one. `chimera/tools/conditional.py` names every conditional tool and
what turns it on; `GET /api/tools` lists the absent ones; a switch writes the setting.

Held here: the catalogue cannot drift from the registry's conditions (a new conditional tool fails
until it is listed); the switch writes exactly the setting that registers the tool, and the next
registry has it; a key or a package is never offered as a switch; nothing touches the real `.env`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chimera.api.config_api import ALLOWED_KEYS
from chimera.config import Settings, get_settings
from chimera.tools.conditional import CONDITIONAL_TOOLS, SWITCHABLE_SETTINGS, unavailable

BUILTIN = Path(__file__).resolve().parents[1] / "chimera" / "tools" / "builtin.py"


def _conditional_classes() -> set[str]:
    """Every Tool class `default_registry` registers inside an `if` — read from the source."""
    tree = ast.parse(BUILTIN.read_text(encoding="utf-8"))
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "default_registry")
    found: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.If):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "register" and inner.args and isinstance(inner.args[0], ast.Call)):
                ctor = inner.args[0].func
                name = ctor.id if isinstance(ctor, ast.Name) else getattr(ctor, "attr", "")
                if name:
                    found.add(name)
    return found


def test_the_catalogue_is_every_conditional_registration() -> None:
    listed = {t.cls.partition(":")[2] for t in CONDITIONAL_TOOLS}
    in_source = _conditional_classes()
    assert in_source, "the AST walk found no conditional registration — an inert guard"
    assert listed == in_source, (
        f"registered under a condition but missing from the catalogue: {sorted(in_source - listed)}; "
        f"catalogued but no longer conditional: {sorted(listed - in_source)}"
    )


def test_each_catalogued_name_is_its_class_name() -> None:
    for tool in CONDITIONAL_TOOLS:
        module, _, attr = tool.cls.partition(":")
        cls = getattr(__import__(module, fromlist=[attr]), attr)
        assert cls.name == tool.name and tool.description()


def test_only_a_setting_is_switchable_and_the_config_endpoint_accepts_exactly_those() -> None:
    assert {"CHIMERA_EDIT_BATCH", "CHIMERA_TODO_LIST", "CHIMERA_DECIDE_TOOL"} == SWITCHABLE_SETTINGS
    assert SWITCHABLE_SETTINGS <= ALLOWED_KEYS
    for tool in CONDITIONAL_TOOLS:
        assert tool.switchable is (tool.kind == "setting")
        if tool.kind == "setting":
            assert len(tool.variables) == 1


def test_a_registered_tool_is_not_listed_as_unavailable() -> None:
    rows = unavailable({"decide", "web_search"})
    names = {r["name"] for r in rows}
    assert "decide" not in names and "web_search" not in names and "edit_batch" in names


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # PATCH /api/config writes `.env` in the working directory — never the real one.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    # patch_config also exports what it writes into os.environ; owning the name first lets the
    # fixture restore it (delenv on an absent name would restore nothing).
    monkeypatch.setenv("CHIMERA_DECIDE_TOOL", "")
    monkeypatch.delenv("CHIMERA_DECIDE_TOOL")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_the_switch_turns_the_tool_on_for_the_next_registry(isolated: Path) -> None:
    from chimera.api import build_api_app

    client = TestClient(build_api_app(lambda: None, settings=Settings(CHIMERA_HOME=str(isolated / "home"))))  # type: ignore[arg-type, call-arg]
    before = client.get("/api/tools").json()
    row = next(r for r in before["unavailable"] if r["name"] == "decide")
    assert row["switchable"] is True and row["variables"] == ["CHIMERA_DECIDE_TOOL"] and row["kind"] == "setting"
    assert "decide" not in {t["name"] for t in before["tools"]}

    resp = client.patch("/api/config", json={"CHIMERA_DECIDE_TOOL": "1"})
    assert resp.status_code == 200, resp.text
    assert "CHIMERA_DECIDE_TOOL=1" in (isolated / ".env").read_text(encoding="utf-8")

    after = client.get("/api/tools").json()
    assert "decide" in {t["name"] for t in after["tools"]}
    assert "decide" not in {r["name"] for r in after["unavailable"]}


def test_a_key_or_a_package_is_named_not_offered_as_a_switch(isolated: Path) -> None:
    from chimera.api import build_api_app

    client = TestClient(build_api_app(lambda: None, settings=Settings(CHIMERA_HOME=str(isolated / "home"))))  # type: ignore[arg-type, call-arg]
    rows = {r["name"]: r for r in client.get("/api/tools").json()["unavailable"]}
    for name, row in rows.items():
        if row["kind"] != "setting":
            assert row["switchable"] is False, name
    if "calendar_events" in rows:
        assert rows["calendar_events"]["variables"] == ["CHIMERA_CALENDAR_ICS_URL"]
