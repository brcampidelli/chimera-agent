"""Structural-vs-parametric edit diagnostic for evolved source (EvoPolicyGym, 2607.02440).

Labels a revision of a *code* artifact as:
- ``structural`` — the AST shape changed (new mechanism / control flow),
- ``parametric`` — same AST shape, only numeric literal constants changed (a tuning tweak),
- ``noop`` — the parsed AST is identical (or the text is byte-identical),
- ``unknown`` — not parseable as Python (e.g. a prose skill card), where AST topology is
  meaningless.

Deterministic, stdlib ``ast`` only, model-free — it works on source the agent already
emits, so it fits Chimera's black-box-API world (nothing here touches model internals).
Only NUMERIC literals are treated as parameters (per the paper's numeric-constant
stripping); strings/None/bools are kept, since they usually carry structural meaning.

Use it as a telemetry LABEL, never as a standalone accept/reject gate: AST topology is a
proxy that can call two behaviourally-different programs the same (the paper is explicit
about this).
"""

from __future__ import annotations

import ast
import copy
from typing import Literal

EditClass = Literal["structural", "parametric", "noop", "unknown"]


class _NumericStripper(ast.NodeTransformer):
    """Normalize every numeric literal to 0 so only the AST topology remains."""

    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        if isinstance(node.value, bool):
            return node  # True/False are structural, not tunable parameters
        if isinstance(node.value, (int, float, complex)):
            return ast.copy_location(ast.Constant(value=0), node)
        return node  # strings / None / bytes carry structural meaning — keep them


def _parse(src: str) -> ast.Module | None:
    try:
        return ast.parse(src)
    except SyntaxError:
        return None


def _topology(tree: ast.Module) -> str:
    stripped = _NumericStripper().visit(copy.deepcopy(tree))
    ast.fix_missing_locations(stripped)
    return ast.dump(stripped)


def topology_key(src: str) -> str | None:
    """The AST shape of ``src`` with numeric constants normalized away; None if not Python."""
    tree = _parse(src)
    return _topology(tree) if tree is not None else None


def classify_edit(prev_src: str, new_src: str) -> EditClass:
    """Classify a prev -> new source revision (see the module docstring)."""
    if prev_src == new_src:
        return "noop"
    prev_tree, new_tree = _parse(prev_src), _parse(new_src)
    if prev_tree is None or new_tree is None:
        return "unknown"  # non-Python (prose card): AST topology does not apply
    if ast.dump(prev_tree) == ast.dump(new_tree):
        return "noop"  # identical AST — only formatting/whitespace changed
    if _topology(prev_tree) == _topology(new_tree):
        return "parametric"  # same shape, only numeric constants changed
    return "structural"  # the mechanism changed
