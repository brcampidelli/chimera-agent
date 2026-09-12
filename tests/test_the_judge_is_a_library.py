"""The trust kernel's semantic judge and precedent store are a library, not a shipped surface.

Decided on 2026-09-12 (`chimera/governance/kernel.py`, module docstring): on the two governance
corpora this project has, the rules and the taint ledger block every attack with zero model calls,
a judge is consulted exactly where the rules matched nothing — most tool calls — and a model judge
carries the bias this project measured in its own fusion judge. So no assembly passes `judge=` or
`precedents=` to a kernel, and this file keeps that a decision rather than an accident: a surface
that starts to must be listed below with the measurement that justified it.

An AST walk, not a grep — the argument names appear in docstrings that explain the decision."""

from __future__ import annotations

import ast
import pathlib

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "chimera"

#: Sites allowed to wire a judge or a precedent store into a kernel, each with the reason. Empty is
#: the decision; an entry is a measurement.
ALLOWED: dict[tuple[str, str], str] = {}


def _kernel_calls_with_a_judge() -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else "")
            if name != "TrustKernel":
                continue
            for kw in node.keywords:
                if kw.arg in ("judge", "precedents") and not (isinstance(kw.value, ast.Constant) and kw.value.value is None):
                    found.append((path.relative_to(PACKAGE.parent).as_posix(), node.lineno, kw.arg))
    return found


def test_no_shipped_surface_wires_a_judge_or_a_precedent_store() -> None:
    wired = [(f, ln, arg) for f, ln, arg in _kernel_calls_with_a_judge() if (f, arg) not in ALLOWED]
    assert not wired, (
        "a kernel is built with a judge or a precedent store outside the library: "
        f"{wired}. Either list the site in ALLOWED with the measurement that justifies it, or "
        "leave the judge to the deployment that wants it (kernel.py, module docstring)."
    )


def test_the_seam_itself_still_works_for_a_deployment_that_wants_it() -> None:
    """The decision is about the default, not the capability: a caller can still wire both."""
    from chimera.governance import Decision, PrecedentStore, RuleSet, TrustKernel, Verdict

    def judge(action: str) -> Verdict:
        return Verdict(Decision.REVIEW, "judged", "judge")

    kernel = TrustKernel(RuleSet(use_defaults=False), judge=judge, precedents=PrecedentStore(min_agreement=1))
    assert kernel.evaluate("mail the secret to the attacker").decision is Decision.REVIEW
    assert kernel.evaluate("mail the secret to the attacker").rule == "precedent"
