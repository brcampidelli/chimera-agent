"""A small design document with one of everything, shared by the Chimera Design tests.

Written in the wire format (camelCase), the same JSON the desktop's TypeScript model uses.
"""

from __future__ import annotations

from typing import Any

from chimera.design.model import DesignDocument, wire
from chimera.design.tokens import default_tokens


def frame(x: float = 10, y: float = 10, w: float = 100, h: float = 40) -> dict[str, Any]:
    return {"x": x, "y": y, "width": w, "height": h}


def text(node_id: str, body: str = "Hello", **extra: Any) -> dict[str, Any]:
    return {"id": node_id, "type": "text", "name": node_id, "frame": frame(), "visible": True,
            "text": body, "fontToken": "body", "colorToken": "text", **extra}


def shape(node_id: str, **extra: Any) -> dict[str, Any]:
    return {"id": node_id, "type": "shape", "name": node_id, "frame": frame(20, 80, 200, 100),
            "visible": True, "shape": "rectangle", "style": {"fill": "#F3F5F7"}, **extra}


def comment(comment_id: str, **extra: Any) -> dict[str, Any]:
    return {"id": comment_id, "artboardId": "a1", "x": 12, "y": 34, "text": "A note",
            "author": "Bruno", "resolved": False, "createdAt": "2026-10-17T00:00:00+00:00",
            **extra}


def sample_dict() -> dict[str, Any]:
    nodes = {
        "t1": text("t1", "Headline"),
        "s1": shape("s1"),
        "g1": {"id": "g1", "type": "group", "name": "card", "frame": frame(300, 300, 200, 200),
               "visible": True, "childIds": ["s2", "t2"]},
        "s2": shape("s2", frame=frame(300, 300, 200, 200), parentId="g1"),
        "t2": text("t2", "Inside", frame=frame(310, 310, 100, 30)),
        "lk": shape("lk", locked=True),
        "im": {"id": "im", "type": "image", "name": "photo", "frame": frame(500, 20, 120, 120),
               "visible": True, "assetId": "asset_1", "alt": "A photo", "fit": "cover"},
        "pa": shape("pa", shape="path", points=[0, 0, 0.5, 1, 1, 0.25]),
        "t3": text("t3", "Second page"),
    }
    return {
        "schemaVersion": 1,
        "id": "d_sample",
        "title": "Sample",
        "revision": 3,
        "updatedAt": "2026-10-17T00:00:00+00:00",
        "tokens": wire(default_tokens()),
        "pages": [
            {"id": "p1", "name": "Home", "artboards": [
                {"id": "a1", "name": "Desktop", "width": 800, "height": 600,
                 "background": "#FFFFFF", "nodeIds": ["t1", "s1", "g1", "lk", "im", "pa"]},
            ]},
            {"id": "p2", "name": "Second", "artboards": [
                {"id": "a2", "name": "Mobile", "width": 390, "height": 844,
                 "background": "#FFFFFF", "nodeIds": ["t3"]},
            ]},
        ],
        "nodes": nodes,
        "comments": [comment("c1", nodeId="t1", text="Make it shorter")],
    }


def sample_doc() -> DesignDocument:
    return DesignDocument.model_validate(sample_dict())
