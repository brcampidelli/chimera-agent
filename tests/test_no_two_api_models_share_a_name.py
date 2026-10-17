"""No two API models share a name.

The OpenAPI document names a component after the model class (`#/components/schemas/<Name>`). Two
different classes with the same `__name__` collapse into ONE component, and FastAPI does it
without an error: the second definition replaces the first in `apps/desktop/openapi.json`, the
generated TypeScript types follow, and the screen that read the first model is typed against the
second. It happened while the worktree cleanup routes were written (#967): a new
`WorktreePruneOut` replaced the storage card's model of the same name, and only reading the
schema diff caught it.

This walks every route's request and response models the way `chimera.api.schema_dump` builds the
app, follows nested models and enums (enums become components too), and fails on a name held by
two different classes.
"""

from __future__ import annotations

import enum
import typing
from collections.abc import Iterator
from typing import Any, cast

from fastapi.routing import APIRoute
from pydantic import BaseModel

from chimera.api import build_api_app
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun


def _component_types(annotation: Any) -> Iterator[type]:
    """The classes that become OpenAPI components inside one annotation, nested types included."""
    if isinstance(annotation, type) and (
        issubclass(annotation, BaseModel) or issubclass(annotation, enum.Enum)
    ):
        yield annotation
        return
    for arg in typing.get_args(annotation):
        yield from _component_types(arg)


def _walk(model: type, seen: set[type]) -> Iterator[type]:
    if model in seen:
        return
    seen.add(model)
    yield model
    if issubclass(model, BaseModel):
        for field in model.model_fields.values():
            for nested in _component_types(field.annotation):
                yield from _walk(nested, seen)


def _route_models(route: APIRoute) -> Iterator[Any]:
    yield route.response_model
    for param in route.dependant.body_params:
        yield param.field_info.annotation
    for param in route.dependant.query_params + route.dependant.path_params:
        yield param.field_info.annotation


def _names_held_by_several_classes() -> dict[str, list[str]]:
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    seen: set[type] = set()
    by_name: dict[str, set[type]] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for annotation in _route_models(route):
            for top in _component_types(annotation):
                for model in _walk(top, seen):
                    by_name.setdefault(model.__name__, set()).add(model)
    return {
        name: sorted(f"{c.__module__}.{c.__qualname__}" for c in classes)
        for name, classes in by_name.items()
        if len(classes) > 1
    }


def test_no_two_api_models_share_a_name() -> None:
    clashes = _names_held_by_several_classes()
    assert not clashes, (
        "two different classes would share one OpenAPI component, and the second would replace "
        f"the first in openapi.json without any error; rename one of each pair: {clashes}"
    )


def test_the_walk_reaches_nested_models() -> None:
    # The guard is only as good as its walk: a clash inside a nested field must be seen. A model
    # every desktop build reads (the session list) nests others; if the walk stopped at the top
    # level this set would hold only route-level models.
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    seen: set[type] = set()
    names: set[str] = set()
    for route in app.routes:
        if isinstance(route, APIRoute):
            for annotation in _route_models(route):
                for top in _component_types(annotation):
                    names.update(m.__name__ for m in _walk(top, seen))
    components = set(app.openapi().get("components", {}).get("schemas", {}))
    # Every component FastAPI emitted for a model of ours was found by the walk (FastAPI adds its own
    # validation components, which are not ours).
    ours = {c for c in components if not c.startswith(("HTTPValidationError", "ValidationError", "Body_"))}
    missing = sorted(ours - names)
    assert not missing, f"components the walk did not reach: {missing[:20]}"
