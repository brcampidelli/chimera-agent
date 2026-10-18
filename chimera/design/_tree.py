"""Lookups over a document's JSON state, shared by the operations.

They scan rather than index: a document holds at most a few thousand elements and a batch at most a
couple of hundred operations, and an index would have to be kept right across every mutation — the
kind of bookkeeping that is wrong exactly once.
"""

from __future__ import annotations

from typing import Any

from chimera.design.model import DesignError


def page(state: dict[str, Any], page_id: str) -> dict[str, Any]:
    for item in state["pages"]:
        if item["id"] == page_id:
            return dict_of(item)
    raise DesignError(f"page {page_id!r} not found")


def artboard(state: dict[str, Any], artboard_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    for item in state["pages"]:
        for board in item["artboards"]:
            if board["id"] == artboard_id:
                return dict_of(item), dict_of(board)
    raise DesignError(f"artboard {artboard_id!r} not found")


def node(state: dict[str, Any], node_id: str) -> dict[str, Any]:
    found = state["nodes"].get(node_id)
    if found is None:
        raise DesignError(f"element {node_id!r} not found")
    return dict_of(found)


def comment(state: dict[str, Any], comment_id: str) -> dict[str, Any]:
    for item in state["comments"]:
        if item["id"] == comment_id:
            return dict_of(item)
    raise DesignError(f"comment {comment_id!r} not found")


def parent_list(state: dict[str, Any], node_id: str) -> tuple[str | None, list[str]]:
    """The group id (None for an artboard) and the list that places ``node_id``."""
    for item in state["nodes"].values():
        if item["type"] == "group" and node_id in item["childIds"]:
            return str(item["id"]), list_of(item["childIds"])
    for item in state["pages"]:
        for board in item["artboards"]:
            if node_id in board["nodeIds"]:
                return None, list_of(board["nodeIds"])
    raise DesignError(f"element {node_id!r} is not placed anywhere")


def location_of(state: dict[str, Any], node_id: str) -> tuple[str, str]:
    """The page and artboard an element is on, walking up through its groups."""
    current = node_id
    for _ in range(len(state["nodes"]) + 1):
        parent_id, _ = parent_list(state, current)
        if parent_id is None:
            for item in state["pages"]:
                for board in item["artboards"]:
                    if current in board["nodeIds"]:
                        return str(item["id"]), str(board["id"])
        else:
            current = parent_id
    raise DesignError(f"element {node_id!r} has no artboard")  # pragma: no cover - a cycle


def artboard_of(state: dict[str, Any], node_id: str) -> str:
    """The artboard an element is on."""
    return location_of(state, node_id)[1]


def subtree(state: dict[str, Any], roots: list[str]) -> list[str]:
    """``roots`` and everything inside them, parents before children."""
    out: list[str] = []
    stack = list(reversed(roots))
    while stack:
        nid = stack.pop()
        out.append(nid)
        item = state["nodes"][nid]
        if item["type"] == "group":
            stack.extend(reversed(item["childIds"]))
    return out


def dict_of(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):  # pragma: no cover - the state is a validated dump
        raise DesignError("malformed document state")
    return value


def list_of(value: Any) -> list[str]:
    if not isinstance(value, list):  # pragma: no cover - the state is a validated dump
        raise DesignError("malformed document state")
    return value
