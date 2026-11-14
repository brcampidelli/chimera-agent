"""Two ways the desktop and the API drift apart without anything failing where the change was made.

1. A response model that names another model defined LATER in the file (or in a module that has not
   been imported yet). Under ``from __future__ import annotations`` that is legal Python and pydantic
   defers it: the class imports fine, every test that never serialises it passes, and the first
   ``app.openapi()`` (the CI step that regenerates the schema) raises. It broke CI twice that way.
   The check below forces the resolution for every model the API package defines.

2. A path in ``apps/desktop/src/lib/api.ts`` that no route serves. The generated ``api-schema.ts``
   types the BODIES; the path strings next to them are hand-written, so a rename on the server leaves
   the client calling a 404 that compiles. Comparing the two against the committed ``openapi.json``
   (which CI proves equals what the app emits) closes that gap.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
import re
import sys
import types
from pathlib import Path

import pytest
from pydantic import BaseModel
from pydantic.errors import PydanticUndefinedAnnotation

import chimera.api as api_package

ROOT = Path(__file__).resolve().parents[1]
API_TS = ROOT / "apps" / "desktop" / "src" / "lib" / "api.ts"
OPENAPI = ROOT / "apps" / "desktop" / "openapi.json"


def unresolved_models(module_names: list[str]) -> list[str]:
    """Models in these modules whose annotations still cannot be resolved, by dotted name.

    ``model_rebuild(raise_errors=True)`` is a no-op for a complete model and raises for one with a
    forward reference nobody ever defined, which is exactly the state ``openapi()`` trips over.
    """
    broken: list[str] = []
    for name in module_names:
        module = sys.modules[name]
        for attr, value in vars(module).items():
            if not (isinstance(value, type) and issubclass(value, BaseModel)):
                continue
            if value.__module__ != name or value is BaseModel:
                continue  # an import of somebody else's model is checked in its own module
            try:
                value.model_rebuild(raise_errors=True)
            except PydanticUndefinedAnnotation as exc:
                broken.append(f"{name}.{attr}: {exc}")
    return sorted(broken)


def _api_modules() -> list[str]:
    names = [
        info.name
        for info in pkgutil.walk_packages(api_package.__path__, prefix="chimera.api.")
    ]
    for name in names:
        importlib.import_module(name)
    return names


def test_every_api_model_resolves_its_forward_references() -> None:
    assert unresolved_models(_api_modules()) == []


def test_the_check_sees_a_model_that_names_one_that_is_never_defined() -> None:
    """The guard has to be able to fail, or the test above is a sentence and not a check."""
    module = types.ModuleType("chimera_api_fixture_broken")
    exec(  # a fixture module built in memory: the shape under test is the source text
        "from __future__ import annotations\n"
        "from pydantic import BaseModel\n"
        "class Out(BaseModel):\n"
        "    inner: NeverDefined\n",
        module.__dict__,
    )
    sys.modules[module.__name__] = module
    try:
        broken = unresolved_models([module.__name__])
    finally:
        del sys.modules[module.__name__]
    assert len(broken) == 1 and broken[0].startswith("chimera_api_fixture_broken.Out")


def _read_literal(source: str, start: int, quote: str) -> str:
    """The path inside one string/template literal, with each `${...}` hole as `{}`.

    A hole can hold anything, quotes and braces included (`${devServerQuery(w, { name })}`), so a
    regex cannot find where it ends; this walks it with a brace depth instead. A hole that follows a
    path character rather than a `/` is a query-string suffix, not a path parameter, and ends the path.
    """
    out: list[str] = []
    i = start
    while i < len(source):
        ch = source[i]
        if ch == quote:
            break
        if quote == "`" and source.startswith("${", i):
            depth, i = 1, i + 2
            while i < len(source) and depth:
                depth += {"{": 1, "}": -1}.get(source[i], 0)
                i += 1
            if out and out[-1] != "/":
                break
            out.append("{}")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def desktop_paths(source: str) -> set[str]:
    found: set[str] = set()
    for match in re.finditer(r"""([`"'])(/api/)""", source):
        raw = _read_literal(source, match.start(2), match.group(1))
        path = raw.split("?", 1)[0]
        found.add(path.rstrip("/") or "/")
    return found


def served_paths(spec: dict[str, object]) -> set[str]:
    paths = spec["paths"]
    assert isinstance(paths, dict)
    return {re.sub(r"\{[^}]+\}", "{}", str(p)).rstrip("/") for p in paths}


def _matches_a_route(path: str, routes: set[str]) -> bool:
    if path in routes:
        return True
    # A path built by appending a segment to a variable (`/api/x/${a}${b}`) collapses its holes into
    # one; compare with every route segment-wise so adjacent holes do not read as a missing route.
    squeezed = re.sub(r"(\{\})+", "{}", path)
    return squeezed in routes


def test_the_desktop_calls_only_paths_the_api_serves() -> None:
    spec = json.loads(OPENAPI.read_text(encoding="utf-8"))
    routes = served_paths(spec)
    missing = sorted(p for p in desktop_paths(API_TS.read_text(encoding="utf-8")) if not _matches_a_route(p, routes))
    assert missing == [], (
        "api.ts calls a path openapi.json does not list; a route was renamed or removed on the "
        f"server (or the schema was not regenerated): {missing}"
    )


def test_the_path_check_catches_a_renamed_route() -> None:
    routes = served_paths({"paths": {"/api/runs": {}, "/api/code/sessions/{session_id}": {}}})
    calls = desktop_paths(
        'json("/api/runs"); json(`/api/code/sessions/${encodeURIComponent(id)}?x=1`);'
        ' json("/api/runs/renamed")'
    )
    assert sorted(p for p in calls if not _matches_a_route(p, routes)) == ["/api/runs/renamed"]


@pytest.mark.parametrize("floor", [150])
def test_the_extraction_is_not_vacuous(floor: int) -> None:
    """An empty set of paths would make the comparison above pass for the wrong reason."""
    assert len(desktop_paths(API_TS.read_text(encoding="utf-8"))) >= floor
