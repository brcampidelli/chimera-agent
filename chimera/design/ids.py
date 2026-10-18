"""Server-generated identifiers, and the remap that replaces the ones a model proposes.

A proposal has to name the elements it creates so that its later operations can refer to them ("add
``hero``, then update ``hero``"). Those names are the model's, and keeping them would let a reply pick
an id that collides with an element the person deleted (whose comments would then attach to the new
one), or one shaped to look like something it is not. So every id a proposal **introduces** is swapped
for a fresh server id before anything is applied, and every later reference in the same proposal is
rewritten to match. Ids the proposal only **references** — existing elements — are left alone, and a
reference to something that does not exist still fails in ``apply_operations`` as it should.

Order matters and is honoured: a reference before the operation that introduces an id means the
existing element of that name; after it, the new one.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from typing import Any

#: The prefix of a generated id says what it names, which helps anyone reading a document by hand.
PREFIXES = {"page": "pg_", "artboard": "ab_", "node": "n_", "comment": "c_", "document": "d_"}

_REFERENCE_KEYS = frozenset({"nodeId", "artboardId", "pageId", "parentId", "commentId"})
_REFERENCE_LISTS = frozenset({"childIds", "nodeIds"})


def new_id(kind: str) -> str:
    return f"{PREFIXES[kind]}{uuid.uuid4().hex[:16]}"


def _introduced(op: dict[str, Any]) -> list[tuple[str, str]]:
    """``(kind, id)`` for every id this operation creates."""
    kind = op.get("type")
    out: list[tuple[str, str]] = []
    if kind == "page.add":
        out.append(("page", op["page"]["id"]))
        out += [("artboard", a["id"]) for a in op["page"]["artboards"]]
        out += [("node", n["id"]) for n in (op.get("nodes") or [])]
    elif kind == "artboard.add":
        out.append(("artboard", op["artboard"]["id"]))
        out += [("node", n["id"]) for n in (op.get("nodes") or [])]
    elif kind == "node.add":
        out.append(("node", op["node"]["id"]))
        out += [("node", n["id"]) for n in (op.get("children") or [])]
    elif kind == "node.duplicate":
        out += [("node", value) for value in op["newIds"].values()]
    elif kind == "comment.add":
        out.append(("comment", op["comment"]["id"]))
    return out


def remap_new_ids(
    operations: Sequence[dict[str, Any]], factory: Callable[[str], str] = new_id
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Return the operations with introduced ids replaced, and the ``proposed -> generated`` map.

    Works on wire JSON dicts (camelCase, ``model.wire``) and returns new dicts; the input is not mutated.
    An id introduced twice gets two fresh ids, and references follow the most recent one.
    """
    mapping: dict[str, str] = {}
    current: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    for op in operations:
        introduced = _introduced(op)
        before = dict(current)
        for kind, old in introduced:
            current[old] = mapping[old] = factory(kind)
        out.append(_rewrite(op, before, current, introduced_now={old for _, old in introduced}))
    return out, mapping


def _rewrite(
    value: Any, before: dict[str, str], after: dict[str, str], *, introduced_now: set[str]
) -> Any:
    """Rewrite ids inside one operation.

    Inside the operation that introduces them, ids resolve to the new names (a group's ``childIds``
    naming the children it brings). The one exception is ``node.duplicate``'s ``newIds`` keys and the
    plain reference fields, which name what already exists, so they resolve against ``before`` unless
    they are one of the ids this very operation introduced.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if k == "newIds" and isinstance(v, dict):
                out[k] = {
                    _swap(old, before): after.get(new, new) for old, new in v.items()
                }
            elif k == "id" and isinstance(v, str):
                out[k] = after.get(v, v) if v in introduced_now else v
            elif k in _REFERENCE_KEYS and isinstance(v, str):
                out[k] = after.get(v, v) if v in introduced_now else _swap(v, before)
            elif k in _REFERENCE_LISTS and isinstance(v, list):
                out[k] = [
                    after.get(i, i) if i in introduced_now else _swap(i, before) for i in v
                ]
            else:
                out[k] = _rewrite(v, before, after, introduced_now=introduced_now)
        return out
    if isinstance(value, list):
        return [_rewrite(v, before, after, introduced_now=introduced_now) for v in value]
    return value


def _swap(value: Any, mapping: dict[str, str]) -> Any:
    return mapping.get(value, value) if isinstance(value, str) else value
