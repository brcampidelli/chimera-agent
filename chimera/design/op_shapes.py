"""The shape of each design operation, written out once, so a model is not left to guess it.

The first live use of Chimera Design (0.79.0) had a model draw a landing page with the operation
schema in front of it only as a field list. It guessed ``{"type": "page.add", "name": "Home"}``, then
a ``node.add`` with a made-up ``rect`` type, then text nested the wrong way, and six of ten applies
were refused. Each refusal named a field, not the shape it should have had, so every retry was
another guess.

The examples below are the wire shapes the models in :mod:`chimera.design.ops` and
:mod:`chimera.design.model` accept, and they travel in two places: the ``design`` tool's description
(one line per operation, so the model sees them on every design turn) and the error an ``apply``
returns when one operation fails (the shape of that operation, so the retry has the answer).

They are data, checked by a test that validates every example against the real models. An example
that stops validating after a schema change fails that test instead of misleading the model.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from chimera.design.model import DesignError
from chimera.design.ops import parse_operations

#: Keyed by the operation's wire name. ``node.add`` has one example per element type, since the
#: element is the part a model gets wrong: a text element is not a shape with a ``text`` field.
OPERATION_SHAPES: dict[str, dict[str, Any]] = {
    "page.add": {
        "type": "page.add",
        "page": {"id": "home", "name": "Home", "artboards": []},
    },
    "artboard.add": {
        "type": "artboard.add",
        "pageId": "home",
        "artboard": {
            "id": "desktop", "name": "Desktop", "width": 1440, "height": 900,
            "background": "#F5F5F5",
        },
    },
    "node.add.text": {
        "type": "node.add",
        "pageId": "home",
        "artboardId": "desktop",
        "node": {
            "type": "text", "id": "title", "name": "Title",
            "frame": {"x": 80, "y": 96, "width": 800, "height": 120},
            "text": "Headline", "fontToken": "display", "colorToken": "text",
        },
    },
    "node.add.shape": {
        "type": "node.add",
        "pageId": "home",
        "artboardId": "desktop",
        "node": {
            "type": "shape", "id": "cta", "name": "CTA button",
            "frame": {"x": 80, "y": 260, "width": 240, "height": 56},
            "shape": "rectangle", "style": {"fill": "#333333", "radius": 8},
        },
    },
    "node.add.image": {
        "type": "node.add",
        "pageId": "home",
        "artboardId": "desktop",
        "node": {
            "type": "image", "id": "hero-photo", "name": "Hero photo",
            "frame": {"x": 760, "y": 96, "width": 600, "height": 560},
            "assetId": "hero-photo", "alt": "Product photo", "fit": "cover",
        },
    },
    "node.update": {
        "type": "node.update",
        "nodeId": "title",
        "patch": {"text": "Updated headline", "fontToken": "display"},
    },
    "tokens.replace": {
        "type": "tokens.replace",
        "tokens": {
            "colors": {"primary": "#333333", "text": "#111111", "background": "#F5F5F5"},
            "typography": {
                "display": {"family": "Georgia, serif", "size": 56, "weight": 700, "lineHeight": 1.1},
                "body": {"family": "Arial, sans-serif", "size": 18, "weight": 400},
            },
            "spacing": {"md": 16},
            "radii": {"md": 8},
        },
    },
}

#: The design system a ``create`` takes: the same ``tokens`` object a ``tokens.replace`` carries.
TOKENS_SHAPE: dict[str, Any] = OPERATION_SHAPES["tokens.replace"]["tokens"]


def _line(key: str, example: dict[str, Any]) -> str:
    return f"{key}: {json.dumps(example, ensure_ascii=False, separators=(',', ':'))}"


def shape_block() -> str:
    """The lines the ``design`` tool description carries: one operation shape per line."""
    lines = [
        "Operation shapes (camelCase; one object per operation; colours are #RRGGBB, never names; an"
        " image needs an asset the owner imported, so a placeholder is a shape with a fill, not text):",
    ]
    lines.extend(_line(key, example) for key, example in OPERATION_SHAPES.items())
    return "\n".join(lines)


def shape_for(raw: Any) -> str | None:
    """The example for one raw operation's type, or ``None`` when its type is unknown.

    A ``node.add`` is matched by its element type, so the hint for a text element that was refused
    shows a text element, not a rectangle.
    """
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    if kind == "node.add":
        node = raw.get("node")
        element = node.get("type") if isinstance(node, dict) else None
        key = f"node.add.{element}" if element in ("text", "shape", "image") else None
        if key is None:
            return "\n".join(_line(k, OPERATION_SHAPES[k]) for k in ("node.add.text", "node.add.shape", "node.add.image"))
        return _line(key, OPERATION_SHAPES[key])
    if isinstance(kind, str) and kind in OPERATION_SHAPES:
        return _line(kind, OPERATION_SHAPES[kind])
    return None


def invalid_operation_hint(raw: Sequence[Any]) -> str:
    """Say which operation of a refused batch is wrong, and show its shape.

    Validated one at a time, so the index named is the operation that failed, not the first error
    the batch reports. Returns an empty string when every operation validates on its own (the batch
    failed for another reason, for example its size).
    """
    for index, item in enumerate(raw):
        try:
            parse_operations([item])
        except DesignError:
            kind = item.get("type") if isinstance(item, dict) else None
            shape = shape_for(item)
            if shape is None:
                known = ", ".join(OPERATION_SHAPES)
                return (f"operation {index} has type {kind!r}, which is not one of: {known} "
                        "(node.add is one type with three element kinds: text, shape, image).")
            return f"operation {index} ({kind}) must look like: {shape}"
    return ""
