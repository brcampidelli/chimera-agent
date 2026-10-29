"""Typed edits to a design document, applied all-or-nothing, each with its exact inverse.

Every change to a document — a drag on the canvas, a click on undo, a model's proposal — is a list of
these operations. :func:`apply_operations` is the only function that changes a document, so the rules
live in one place:

- **Transactional.** The input document is never touched. The operations run against a JSON copy and
  the result is validated as a whole document at the end; one bad operation anywhere means no change
  at all and an error naming which one.
- **Invertible.** Each operation returns the operations that undo it, and the batch's inverse is those
  blocks in reverse order. ``apply(apply(doc, ops).doc, inverse) == doc`` is a tested property for
  every operation type, which is what lets the app give undo/redo to the model's edits as well as the
  person's.
- **Closed.** Patches name only the keys :data:`chimera.design.model.PATCHABLE` allows for that
  element type; ``id`` and ``type`` never change. In a ``node.update`` patch ``null`` removes an
  optional field (required ones refuse it), which is how the inverse of a patch that *added* a field
  takes it away again.

**The same operations as the desktop.** Type names and payload keys are the ones in
``apps/desktop/src/lib/design/model.ts`` (camelCase on the wire: ``pageId``, ``artboardId``,
``nodeId``, ``commentId``), so the canvas's optimistic batch and the server's are the same JSON. The
server accepts a superset — ``node.add`` may also place into a group and carry a group's contents,
``page.add``/``artboard.add`` may carry contents, deletes cascade — and the extras are optional keys,
so every desktop payload validates unchanged.

Fixes over the handoff's reference, each covered by a test: deleting a group deletes what it contains
(the reference left the children in the document, placed nowhere); deleting a page, artboard or
element records its position, so undo puts it back where it was rather than at the end; and the
inverse of an update holds the previous value of each patched key (``null`` where the key was absent)
instead of a whole-node snapshot its own whitelist would refuse.

What the engine does **not** decide: whether a given actor may lock or unlock elements. Callers pass
``allow_lock_changes=False`` for anything the model proposed (``propose.py`` does), so a proposal can
neither unlock an element the person protected nor lock one.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Sequence
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, ValidationError

from chimera.design import _tree
from chimera.design.html_source import DesignHtmlError, validate_artboard_html
from chimera.design.model import (
    ARTBOARD_PATCHABLE,
    MAX_NODES,
    MAX_OPS_PER_CALL,
    MAX_TITLE,
    PATCHABLE,
    REQUIRED_FIELDS,
    Artboard,
    Comment,
    DesignDocument,
    DesignError,
    DesignNode,
    DesignTokens,
    Page,
    Rect,
    SafeId,
    _Strict,
    wire,
)

Index = Annotated[int, Field(ge=0)] | None
Nodes = Annotated[list[DesignNode], Field(max_length=MAX_NODES)]


class DocumentRename(_Strict):
    type: Literal["document.rename"]
    title: Annotated[str, Field(min_length=1, max_length=MAX_TITLE)]


class PageAdd(_Strict):
    """Add a page. ``nodes`` carries every element its artboards reference (empty for a new page)."""

    type: Literal["page.add"]
    page: Page
    nodes: Nodes | None = None
    index: Index = None


class PageRename(_Strict):
    type: Literal["page.rename"]
    page_id: SafeId
    name: Annotated[str, Field(max_length=120)]


class PageDelete(_Strict):
    type: Literal["page.delete"]
    page_id: SafeId


class ArtboardAdd(_Strict):
    type: Literal["artboard.add"]
    page_id: SafeId
    artboard: Artboard
    nodes: Nodes | None = None
    index: Index = None


class ArtboardUpdate(_Strict):
    """Patch an artboard's ``name``, ``width``, ``height`` or ``background``."""

    type: Literal["artboard.update"]
    page_id: SafeId
    artboard_id: SafeId
    patch: dict[str, Any]


class ArtboardDelete(_Strict):
    type: Literal["artboard.delete"]
    page_id: SafeId
    artboard_id: SafeId


class ArtboardSetHtml(_Strict):
    type: Literal["artboard.set_html"]
    page_id: SafeId
    artboard_id: SafeId
    html: str | None = None


class ArtboardEditHtml(_Strict):
    type: Literal["artboard.edit_html"]
    page_id: SafeId
    artboard_id: SafeId
    old: str
    new: str


class NodeAdd(_Strict):
    """Add an element to an artboard at ``index`` (its stacking slot; the top when absent).

    Server extras, both optional: ``parentId`` places it inside a group on that artboard, and a new
    group arrives with its contents in ``children`` — every descendant, in any order — which its
    ``childIds`` must reach exactly, never naming an element that already exists elsewhere.
    """

    type: Literal["node.add"]
    page_id: SafeId
    artboard_id: SafeId
    node: DesignNode
    index: Index = None
    parent_id: SafeId | None = None
    children: Nodes | None = None


class NodeUpdate(_Strict):
    type: Literal["node.update"]
    node_id: SafeId
    patch: dict[str, Any]


class NodeMove(_Strict):
    """Set an element's frame. ``withChildren`` (off by default, as on the desktop, which moves each
    element itself) shifts a group's contents by the same offset."""

    type: Literal["node.move"]
    node_id: SafeId
    frame: Rect
    with_children: bool | None = None


class NodeDelete(_Strict):
    """Remove an element; a group goes with everything inside it."""

    type: Literal["node.delete"]
    node_id: SafeId


class NodeDuplicate(_Strict):
    """Copy an element (and a group's contents) next to the original, under the given new ids."""

    type: Literal["node.duplicate"]
    node_id: SafeId
    new_ids: dict[SafeId, SafeId]
    offset_x: Annotated[float, Field(ge=-10_000, le=10_000)] = 16.0
    offset_y: Annotated[float, Field(ge=-10_000, le=10_000)] = 16.0


class TokensReplace(_Strict):
    type: Literal["tokens.replace"]
    tokens: DesignTokens


class CommentAdd(_Strict):
    type: Literal["comment.add"]
    comment: Comment
    index: Index = None


class CommentResolve(_Strict):
    type: Literal["comment.resolve"]
    comment_id: SafeId
    resolved: bool


class CommentDelete(_Strict):
    type: Literal["comment.delete"]
    comment_id: SafeId


DesignOperation = Annotated[
    DocumentRename
    | PageAdd
    | PageRename
    | PageDelete
    | ArtboardAdd
    | ArtboardUpdate
    | ArtboardDelete
    | ArtboardSetHtml
    | ArtboardEditHtml
    | NodeAdd
    | NodeUpdate
    | NodeMove
    | NodeDelete
    | NodeDuplicate
    | TokensReplace
    | CommentAdd
    | CommentResolve
    | CommentDelete,
    Field(discriminator="type"),
]

OPERATION_TYPES: tuple[str, ...] = (
    "document.rename", "page.add", "page.rename", "page.delete", "artboard.add",
    "artboard.update", "artboard.delete", "artboard.set_html", "artboard.edit_html",
    "node.add", "node.update", "node.move", "node.delete",
    "node.duplicate", "tokens.replace", "comment.add", "comment.resolve", "comment.delete",
)
#: HTML artboards are released (liberado após H1–H3): the validator (H1), the agent tool (H2) and
#: the sandboxed iframe render (H3) are all on main. While False, no caller could write their HTML
#: (agent, proposal, desktop route, bridge), because each of them goes through apply_operations.
HTML_ARTBOARDS_ENABLED = True
_HTML_OPS = frozenset(["artboard.set_html", "artboard.edit_html"])
#: The subset the desktop's ``DesignOperation`` names. The other four are server-only for now: page.rename, node.duplicate, artboard.set_html and artboard.edit_html.
DESKTOP_OPERATION_TYPES: tuple[str, ...] = (
    "document.rename", "page.add", "page.delete", "artboard.add", "artboard.update",
    "artboard.delete", "node.add", "node.update", "node.move", "node.delete", "tokens.replace",
    "comment.add", "comment.resolve", "comment.delete",
)

_OPERATION: TypeAdapter[Any] = TypeAdapter(DesignOperation)
_OPERATIONS: TypeAdapter[list[Any]] = TypeAdapter(list[DesignOperation])
_NODE: TypeAdapter[Any] = TypeAdapter(DesignNode)

State = dict[str, Any]


class DesignOperationError(DesignError):
    """An operation was refused. ``index`` is its position in the batch (None: the batch as a whole)."""

    def __init__(self, message: str, index: int | None = None) -> None:
        self.index = index
        super().__init__(message if index is None else f"operation {index}: {message}")


def parse_operations(raw: Sequence[Any]) -> list[Any]:
    """Validate raw operations (dicts or models) into operation models, or raise with a short reason."""
    try:
        return list(_OPERATIONS.validate_python([_as_dict(item) for item in raw]))
    except ValidationError as exc:
        raise DesignOperationError(short_errors(exc)) from None


def short_errors(exc: ValidationError, limit: int = 8) -> str:
    """A validation error as a few ``where: what`` lines, never echoing the offending value."""
    parts = []
    for err in exc.errors(include_input=False, include_url=False)[:limit]:
        where = ".".join(str(p) for p in err.get("loc", ())) or "value"
        parts.append(f"{where}: {err.get('msg', 'invalid')}")
    more = exc.error_count() - limit
    return "; ".join(parts) + (f"; and {more} more" if more > 0 else "")


def _as_dict(item: Any) -> Any:
    return wire(item) if hasattr(item, "model_dump") else item


def _op(data: dict[str, Any]) -> Any:
    return _OPERATION.validate_python(data)


def apply_operations(
    doc: DesignDocument,
    operations: Sequence[Any],
    *,
    max_ops: int | None = MAX_OPS_PER_CALL,
    allow_lock_changes: bool = True,
) -> tuple[DesignDocument, list[Any]]:
    """Apply ``operations`` to a copy of ``doc``; return the new document and the inverse operations.

    Revision and timestamp are left alone: they belong to the store, which bumps them on save. Pass
    ``max_ops=None`` to apply an inverse produced here (a cascade or a group move can invert to more
    operations than its forward batch).
    """
    if max_ops is not None and len(operations) > max_ops:
        raise DesignOperationError(f"{len(operations)} operations, the limit is {max_ops}")
    ops = parse_operations(operations)
    if not HTML_ARTBOARDS_ENABLED:
        _refuse_unreleased_html(ops)
    state = copy.deepcopy(wire(doc))
    inverse: list[Any] = []
    for index, op in enumerate(ops):
        try:
            block = _APPLY[op.type](state, op, allow_lock_changes)
        except DesignOperationError as exc:
            raise DesignOperationError(str(exc), index) from None
        except (DesignError, ValidationError) as exc:
            reason = short_errors(exc) if isinstance(exc, ValidationError) else str(exc)
            raise DesignOperationError(reason, index) from None
        inverse[0:0] = block
    try:
        result = DesignDocument.model_validate(state)
    except ValidationError as exc:
        raise DesignOperationError(f"the result is not a valid document: {short_errors(exc)}") from None
    return result, inverse


def _refuse_unreleased_html(ops: Sequence[Any]) -> None:
    """Refuse the HTML ops, and any operation that carries non-null HTML (an artboard.add, a page)."""
    for index, op in enumerate(ops):
        if op.type in _HTML_OPS or _carries_html(wire(op)):
            raise DesignOperationError("HTML artboards are not available yet", index)


def _carries_html(node: Any) -> bool:
    if isinstance(node, dict):
        return any((key == "html" and value is not None) or _carries_html(value)
                   for key, value in node.items())
    if isinstance(node, list):
        return any(_carries_html(item) for item in node)
    return False


# ---- the operations, each on the JSON state, each returning its inverse block -----------------


def _document_rename(state: State, op: DocumentRename, _locks: bool) -> list[Any]:
    title = op.title.strip()
    if not title:
        raise DesignError("the title cannot be blank")
    before = state["title"]
    state["title"] = title
    return [_op({"type": "document.rename", "title": before})]


def _page_add(state: State, op: PageAdd, locks: bool) -> list[Any]:
    page = wire(op.page)
    if any(p["id"] == page["id"] for p in state["pages"]):
        raise DesignError(f"page {page['id']!r} already exists")
    existing_boards = {a["id"] for p in state["pages"] for a in p["artboards"]}
    for board in page["artboards"]:
        if board["id"] in existing_boards:
            raise DesignError(f"artboard {board['id']!r} already exists")
    roots = [nid for board in page["artboards"] for nid in board["nodeIds"]]
    nodes = _admit_subtree(state, roots, op.nodes or [], "the page", locks)
    _insert(state["pages"], op.index, page, "page")
    state["nodes"].update(nodes)
    return [_op({"type": "page.delete", "pageId": page["id"]})]


def _page_rename(state: State, op: PageRename, _locks: bool) -> list[Any]:
    page = _tree.page(state, op.page_id)
    before = page["name"]
    page["name"] = op.name
    return [_op({"type": "page.rename", "pageId": op.page_id, "name": before})]


def _page_delete(state: State, op: PageDelete, _locks: bool) -> list[Any]:
    page = _tree.page(state, op.page_id)
    index = state["pages"].index(page)
    ids = [nid for board in page["artboards"] for nid in _tree.subtree(state, board["nodeIds"])]
    _refuse_locked(state, ids)
    nodes = [state["nodes"].pop(nid) for nid in ids]
    state["pages"].pop(index)
    inverse: dict[str, Any] = {"type": "page.add", "page": page, "index": index}
    if nodes:
        inverse["nodes"] = nodes
    return [_op(inverse)]


def _artboard_add(state: State, op: ArtboardAdd, locks: bool) -> list[Any]:
    page = _tree.page(state, op.page_id)
    board = wire(op.artboard)
    if any(a["id"] == board["id"] for p in state["pages"] for a in p["artboards"]):
        raise DesignError(f"artboard {board['id']!r} already exists")
    nodes = _admit_subtree(state, board["nodeIds"], op.nodes or [], "the artboard", locks)
    _insert(page["artboards"], op.index, board, "artboard")
    state["nodes"].update(nodes)
    return [_op({"type": "artboard.delete", "pageId": page["id"], "artboardId": board["id"]})]


def _artboard_update(state: State, op: ArtboardUpdate, _locks: bool) -> list[Any]:
    page, board = _artboard_on_page(state, op.page_id, op.artboard_id)
    _whitelist(op.patch, ARTBOARD_PATCHABLE, "artboard")
    if any(value is None for value in op.patch.values()):
        raise DesignError("an artboard patch cannot remove a field")
    before = {key: board[key] for key in op.patch}
    merged = wire(Artboard.model_validate({**board, **op.patch}))
    board.update({key: merged[key] for key in op.patch})
    return [_op({"type": "artboard.update", "pageId": page["id"], "artboardId": board["id"],
                 "patch": before})]


def _artboard_delete(state: State, op: ArtboardDelete, _locks: bool) -> list[Any]:
    page, board = _artboard_on_page(state, op.page_id, op.artboard_id)
    index = page["artboards"].index(board)
    ids = _tree.subtree(state, board["nodeIds"])
    _refuse_locked(state, ids)
    nodes = [state["nodes"].pop(nid) for nid in ids]
    page["artboards"].pop(index)
    inverse: dict[str, Any] = {"type": "artboard.add", "pageId": page["id"], "artboard": board,
                               "index": index}
    if nodes:
        inverse["nodes"] = nodes
    return [_op(inverse)]


def _artboard_set_html(state: State, op: ArtboardSetHtml, _locks: bool) -> list[Any]:
    page, board = _artboard_on_page(state, op.page_id, op.artboard_id)
    if op.html is not None:
        try:
            validate_artboard_html(op.html)
        except DesignHtmlError as exc:
            raise DesignError(str(exc)) from None
    before = board.get("html")
    if op.html is None:
        board.pop("html", None)
    else:
        board["html"] = op.html
    return [_op({"type": "artboard.set_html", "pageId": page["id"],
                 "artboardId": board["id"], "html": before})]


def _artboard_edit_html(state: State, op: ArtboardEditHtml, _locks: bool) -> list[Any]:
    page, board = _artboard_on_page(state, op.page_id, op.artboard_id)
    before = board.get("html")
    if before is None:
        raise DesignError("artboard has no HTML source")
    occurrences = before.count(op.old)
    if occurrences != 1:
        raise DesignError(f"old text occurs {occurrences} times; expected exactly once")
    after = before.replace(op.old, op.new, 1)
    try:
        validate_artboard_html(after)
    except DesignHtmlError as exc:
        raise DesignError(str(exc)) from None
    board["html"] = after
    return [_op({"type": "artboard.set_html", "pageId": page["id"],
                 "artboardId": board["id"], "html": before})]


def _node_add(state: State, op: NodeAdd, locks: bool) -> list[Any]:
    _, board = _artboard_on_page(state, op.page_id, op.artboard_id)
    node = wire(op.node)
    nodes = _admit_subtree(state, [node["id"]], [op.node, *(op.children or [])], "the element",
                           locks)
    if op.parent_id is None:
        target = board["nodeIds"]
    else:
        parent = _tree.node(state, op.parent_id)
        if parent["type"] != "group":
            raise DesignError(f"{op.parent_id!r} is not a group")
        if _tree.artboard_of(state, op.parent_id) != op.artboard_id:
            raise DesignError(f"group {op.parent_id!r} is not on artboard {op.artboard_id!r}")
        if parent["locked"]:
            raise DesignError(f"group {op.parent_id!r} is locked")
        target = parent["childIds"]
    _insert(target, op.index, node["id"], "element")
    state["nodes"].update(nodes)
    return [_op({"type": "node.delete", "nodeId": node["id"]})]


def _node_update(state: State, op: NodeUpdate, locks: bool) -> list[Any]:
    node = _tree.node(state, op.node_id)
    allowed = PATCHABLE[node["type"]]
    _whitelist(op.patch, allowed, f"{node['type']} element")
    removed = sorted(k for k, v in op.patch.items() if v is None and k in REQUIRED_FIELDS)
    if removed:
        raise DesignError(f"{removed} cannot be removed, only changed")
    if "locked" in op.patch and bool(op.patch["locked"]) != node["locked"] and not locks:
        raise DesignError("locking and unlocking elements is left to the owner")
    # As on the desktop: a locked element takes a patch of its lock state and nothing else.
    if node["locked"] and set(op.patch) != {"locked"}:
        raise DesignError(f"element {op.node_id!r} is locked")
    before = {key: copy.deepcopy(node.get(key)) for key in op.patch}
    candidate = {k: v for k, v in node.items() if not (k in op.patch and op.patch[k] is None)}
    candidate.update({k: v for k, v in op.patch.items() if v is not None})
    merged = wire(_NODE.validate_python(candidate))
    node.clear()
    node.update(merged)
    return [_op({"type": "node.update", "nodeId": op.node_id, "patch": before})]


def _node_move(state: State, op: NodeMove, _locks: bool) -> list[Any]:
    node = _tree.node(state, op.node_id)
    moved = [op.node_id]
    if node["type"] == "group" and op.with_children:
        moved = _tree.subtree(state, [op.node_id])
    _refuse_locked(state, moved)
    frame = wire(op.frame)
    dx, dy = frame["x"] - node["frame"]["x"], frame["y"] - node["frame"]["y"]
    inverse = [
        _op({"type": "node.move", "nodeId": nid, "frame": copy.deepcopy(state["nodes"][nid]["frame"])})
        for nid in moved
    ]
    node["frame"] = frame
    for nid in moved[1:]:
        child = state["nodes"][nid]["frame"]
        child["x"], child["y"] = child["x"] + dx, child["y"] + dy
    return inverse


def _node_delete(state: State, op: NodeDelete, _locks: bool) -> list[Any]:
    _tree.node(state, op.node_id)
    ids = _tree.subtree(state, [op.node_id])
    _refuse_locked(state, ids)
    page_id, board_id = _tree.location_of(state, op.node_id)
    parent_id, siblings = _tree.parent_list(state, op.node_id)
    index = siblings.index(op.node_id)
    siblings.pop(index)
    nodes = [state["nodes"].pop(nid) for nid in ids]
    inverse: dict[str, Any] = {"type": "node.add", "pageId": page_id, "artboardId": board_id,
                               "node": nodes[0], "index": index}
    if parent_id is not None:
        inverse["parentId"] = parent_id
    if len(nodes) > 1:
        inverse["children"] = nodes[1:]
    return [_op(inverse)]


def _node_duplicate(state: State, op: NodeDuplicate, locks: bool) -> list[Any]:
    _tree.node(state, op.node_id)
    ids = _tree.subtree(state, [op.node_id])
    if set(op.new_ids) != set(ids):
        raise DesignError("newIds must map the element and every element inside it, and nothing else")
    fresh = list(op.new_ids.values())
    if len(set(fresh)) != len(fresh):
        raise DesignError("newIds repeats an id")
    copies = []
    for nid in ids:
        item = copy.deepcopy(state["nodes"][nid])
        item["id"] = op.new_ids[nid]
        item["locked"] = False
        item["frame"]["x"] += op.offset_x
        item["frame"]["y"] += op.offset_y
        if item["type"] == "group":
            item["childIds"] = [op.new_ids[c] for c in item["childIds"]]
        if nid != op.node_id and "parentId" in item:
            item["parentId"] = op.new_ids[item["parentId"]]
        copies.append(item)
    root = op.new_ids[op.node_id]
    nodes = _admit_subtree(state, [root], copies, "the copy", locks)
    _, siblings = _tree.parent_list(state, op.node_id)
    siblings.insert(siblings.index(op.node_id) + 1, root)
    state["nodes"].update(nodes)
    return [_op({"type": "node.delete", "nodeId": root})]


def _tokens_replace(state: State, op: TokensReplace, _locks: bool) -> list[Any]:
    before = state["tokens"]
    state["tokens"] = wire(op.tokens)
    return [_op({"type": "tokens.replace", "tokens": before})]


def _comment_add(state: State, op: CommentAdd, _locks: bool) -> list[Any]:
    comment = wire(op.comment)
    if any(c["id"] == comment["id"] for c in state["comments"]):
        raise DesignError(f"comment {comment['id']!r} already exists")
    _insert(state["comments"], op.index, comment, "comment")
    return [_op({"type": "comment.delete", "commentId": comment["id"]})]


def _comment_resolve(state: State, op: CommentResolve, _locks: bool) -> list[Any]:
    comment = _tree.comment(state, op.comment_id)
    before = comment["resolved"]
    comment["resolved"] = op.resolved
    return [_op({"type": "comment.resolve", "commentId": op.comment_id, "resolved": before})]


def _comment_delete(state: State, op: CommentDelete, _locks: bool) -> list[Any]:
    comment = _tree.comment(state, op.comment_id)
    index = state["comments"].index(comment)
    state["comments"].pop(index)
    return [_op({"type": "comment.add", "comment": comment, "index": index})]


_APPLY: dict[str, Callable[[State, Any, bool], list[Any]]] = {
    "document.rename": _document_rename,
    "page.add": _page_add,
    "page.rename": _page_rename,
    "page.delete": _page_delete,
    "artboard.add": _artboard_add,
    "artboard.update": _artboard_update,
    "artboard.delete": _artboard_delete,
    "artboard.set_html": _artboard_set_html,
    "artboard.edit_html": _artboard_edit_html,
    "node.add": _node_add,
    "node.update": _node_update,
    "node.move": _node_move,
    "node.delete": _node_delete,
    "node.duplicate": _node_duplicate,
    "tokens.replace": _tokens_replace,
    "comment.add": _comment_add,
    "comment.resolve": _comment_resolve,
    "comment.delete": _comment_delete,
}


# ---- shared checks ------------------------------------------------------------------------------


def _artboard_on_page(
    state: State, page_id: str, artboard_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    page, board = _tree.artboard(state, artboard_id)
    if page["id"] != page_id:
        raise DesignError(f"artboard {artboard_id!r} is not on page {page_id!r}")
    return page, board


def _whitelist(patch: dict[str, Any], allowed: tuple[str, ...], what: str) -> None:
    if not patch:
        raise DesignError("the patch is empty")
    unknown = sorted(set(patch) - set(allowed))
    if unknown:
        raise DesignError(f"a {what} patch cannot set {unknown}; allowed: {list(allowed)}")


def _refuse_locked(state: State, ids: list[str]) -> None:
    locked = [nid for nid in ids if state["nodes"][nid]["locked"]]
    if locked:
        raise DesignError(f"locked elements are in the way: {locked[:5]}")


def _insert(target: list[Any], index: int | None, value: Any, what: str) -> None:
    if index is None:
        target.append(value)
    elif index > len(target):
        raise DesignError(f"{what} index {index} is past the end ({len(target)})")
    else:
        target.insert(index, value)


def _admit_subtree(
    state: State, roots: list[str], incoming: Sequence[Any], owner: str, locks: bool
) -> dict[str, dict[str, Any]]:
    """Check that ``incoming`` elements are exactly what ``roots`` reach, all new, each placed once.

    This is the rule that stops a payload from claiming an element that already lives elsewhere (a
    group whose ``childIds`` name somebody else's text) and from carrying stowaways nothing places.
    """
    nodes: dict[str, dict[str, Any]] = {}
    for item in incoming:
        data = wire(_NODE.validate_python(_as_dict(item)))
        if data["id"] in nodes:
            raise DesignError(f"element {data['id']!r} appears twice in {owner}")
        if data["id"] in state["nodes"]:
            raise DesignError(f"element {data['id']!r} already exists")
        if data["locked"] and not locks:
            raise DesignError("locking elements is left to the owner")
        nodes[data["id"]] = data
    reached: set[str] = set()
    stack = list(roots)
    while stack:
        nid = stack.pop()
        if nid not in nodes:
            raise DesignError(f"{owner} references {nid!r}, which it does not carry")
        if nid in reached:
            raise DesignError(f"{owner} references {nid!r} more than once")
        reached.add(nid)
        if nodes[nid]["type"] == "group":
            stack.extend(nodes[nid]["childIds"])
    stray = sorted(set(nodes) - reached)
    if stray:
        raise DesignError(f"{owner} carries elements nothing places: {stray[:5]}")
    return nodes
