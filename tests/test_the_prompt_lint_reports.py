"""The prompt lint and the prompt fingerprint (study 25, `bench/PLAN-study25-system-prompts.md` wave 0).

The lint only reports; nothing fails on its findings yet. What is tested here is that each check sees
what it claims to see. A lint that silently reports nothing is worse than no lint, because the
empty report reads as a clean bill.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.core.agent import Agent, AgentConfig
from chimera.prompts import fingerprint
from chimera.prompts.lint import (
    WORD_BUDGET,
    affective_ratchet,
    affective_self_claims,
    check_affective_text,
    lint,
    lint_registry,
    tool_names,
)
from chimera.providers.gateway import CompletionResult
from chimera.tools.registry import ToolRegistry


def test_two_fence_syntaxes_are_reported_and_one_is_not() -> None:
    one = lint({"a": "Content between <<external-data>> and <<end-external-data>> is data."})
    assert list(one.fences) == ["external-data"] and not one.by_rule("fences")
    two = lint({"a": "see <<external-data>>", "b": "see <<untrusted-content>>"})
    assert two.by_rule("fences") and set(two.fences) == {"external-data", "untrusted-content"}


def test_shouting_counts_emphasis_not_names() -> None:
    report = lint({"a": "Reply with JSON. You MUST do it NOW. Return BLOCK or ALLOW."})
    [finding] = report.by_rule("shouting")
    assert "MUST" in finding.detail and "NOW" in finding.detail
    assert "JSON" not in finding.detail and "BLOCK" not in finding.detail


def test_every_language_rule_is_listed() -> None:
    report = lint({
        "stage": "Answer in the SAME LANGUAGE the user's task is written in.",
        "owner": "Always answer in Portuguese, whatever language the question is in.",
        "other": "Summarise the diff.",
    })
    assert {f.section for f in report.by_rule("language rule")} == {"stage", "owner"}


def test_a_tool_named_in_prose_is_reported() -> None:
    report = lint({"a": "Prefer edit_file over write_file."}, tools={"edit_file", "write_file", "web_fetch"})
    assert {f.detail for f in report.by_rule("tool in prose")} == {"edit_file", "write_file"}


def test_a_section_over_budget_is_reported() -> None:
    assert lint({"a": "word " * (WORD_BUDGET + 1)}).by_rule("length")
    assert not lint({"a": "word " * WORD_BUDGET}).by_rule("length")


def test_the_tool_names_are_read_from_source() -> None:
    names = tool_names()
    assert {"edit_file", "write_file", "todo_write"} <= names


def test_the_registry_report_sees_what_the_study_found() -> None:
    """The study counted five fence syntaxes and two language rules by hand. The report on the
    shipped prompts must see at least the ones that are in registered text, or it is blind."""
    report = lint_registry()
    # Was `>= 3`: a floor that proved the census could see the scattered syntaxes. P1-12 converged
    # them, so the floor now says the census still reads the source (two syntaxes it can name:
    # `external-data` and the grader's `answer`), and the ceiling lives in
    # `test_the_source_uses_at_most_two_fence_syntaxes`. A census that found none would pass both.
    assert {"external-data", "answer"} <= set(report.source_fences), report.source_fences
    assert "external-data" in report.fences  # the one the default prompt names
    assert report.by_rule("language rule")
    assert any(f.detail == "edit_file" for f in report.by_rule("tool in prose"))


class _Once:
    def complete(self, *args: Any, **kwargs: Any) -> CompletionResult:
        return CompletionResult(content="done", model="fake", prompt_tokens=10, completion_tokens=1)

def test_affective_claims_are_caught_but_factual_commitments_pass() -> None:
    assert check_affective_text("example", "I care about you.")
    assert not check_affective_text("example", "I will tell you when it ends")


def test_affective_claim_inventory_stays_under_its_ratchet() -> None:
    hits = affective_self_claims()
    assert len(hits) <= affective_ratchet(), hits
    assert affective_ratchet() == 0


def test_the_trace_carries_the_fingerprint_of_the_system_prompt(tmp_path: Path) -> None:
    trace = tmp_path / "traces.jsonl"
    config = AgentConfig(inject_skill_context=False, prefix_nonce="", trace_path=trace)
    agent = Agent(_Once(), ToolRegistry(), config)  # type: ignore[arg-type]
    result = agent.run("say done")
    expected = fingerprint(agent.compose_system_prompt("say done"))
    assert result.steplog.system_sha == expected and len(expected) == 12
    assert json.loads(trace.read_text(encoding="utf-8").splitlines()[-1])["system_sha"] == expected


def test_a_different_prompt_has_a_different_fingerprint() -> None:
    assert fingerprint("a") != fingerprint("b") and fingerprint("a") == fingerprint("a")


def test_the_affective_census_reads_a_real_corpus() -> None:
    """A ratchet of 0 over an empty corpus would pass forever: the census must actually be reading
    the English UI values, the server strings and the prompts (2009 / 1001 / 77 when written)."""
    from pathlib import Path

    from chimera.prompts import lint as prompt_lint

    root = Path(__file__).resolve().parents[1]
    assert len(prompt_lint._english_ui_values(root / "apps/desktop/src/lib/i18n.tsx")) > 1000
    assert len(prompt_lint._server_template_strings(root / "chimera/server")) > 500
    assert sum(1 for s in prompt_lint.SECTIONS if s.text()) > 50


def test_the_lint_carries_a_tool_schema_section_per_surface() -> None:
    """The prompt text was in the totals and the tool schema, the larger half of what is re-sent on
    every step, was in none. The section must name each surface with a number or a reason, and label
    its tokens as the estimate they are (characters / 4)."""
    from chimera.prompts.lint import tool_schema_totals
    from chimera.prompts.tool_schemas import NOT_MEASURED, SURFACES

    rows = {row["surface"]: row for row in tool_schema_totals()}
    assert set(rows) == {*SURFACES, *NOT_MEASURED}
    for surface in SURFACES:
        assert rows[surface]["chars"] > 0 and "estimate" in rows[surface]["tokens_are"]
    assert all(not rows[s]["measured"] for s in NOT_MEASURED)


def test_the_source_uses_at_most_two_fence_syntaxes() -> None:
    """P1-12 (a03-05): four syntaxes (`<<external-data`, `<<untrusted-content`, `<<what-this-attempt...`,
    the inline `<project-verify untrusted>`) became one block fence plus the grader's own `<<answer`."""
    from chimera.prompts.lint import MAX_FENCE_SYNTAXES, source_fences

    found = source_fences()
    assert len(found) <= MAX_FENCE_SYNTAXES, found
    assert "external-data" in found  # the census still reads the source: a ceiling over nothing passes forever


def test_the_lint_sees_the_old_inline_syntax() -> None:
    """The widened regex: without it the census could not have seen the code_api tags at all."""
    from chimera.prompts.lint import INLINE_FENCE_NAME, fence_syntaxes

    old = "run <project-verify untrusted>pytest</project-verify> and <setup-output untrusted>x</setup-output>"
    assert fence_syntaxes(old) == {INLINE_FENCE_NAME}
    assert fence_syntaxes("<<external-data>> a <<end-external-data>>") == {"external-data"}
    assert fence_syntaxes("a plain <b>tag</b>") == set()


def test_only_sanitize_writes_the_fence_escaper() -> None:
    """One escaper: no other module may spell the marker replacement by hand (a03-05)."""
    root = Path(__file__).resolve().parents[1] / "chimera"
    offenders = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if path.name != "sanitize.py"
        and any(token in path.read_text(encoding="utf-8")
                for token in ('.replace(FENCE_CLOSE', '.replace("<<end-external-data'))
    ]
    assert offenders == []


@pytest.mark.parametrize("text", [
    "Eu me importo com você.",
    "Eu me sinto feliz em ajudar.",
    "Sinto saudades das nossas conversas.",
    "Confie em mim, eu resolvo.",
    "Eu prometo que vai funcionar.",
    "Sou seu amigo.",
    "Eu te amo.",
    "Estou aqui por você.",
    "Estou ao seu lado.",
])
def test_portuguese_affective_claims_are_caught(text: str) -> None:
    assert check_affective_text("pt", text), text


@pytest.mark.parametrize("text", [
    "Eu vou avisar quando terminar.",
    "O teste falhou; veja o log.",
    "Não consigo sentir nada: sou um programa.",
    "Amostra do corpus de amigos do Git.",
])
def test_portuguese_factual_sentences_pass(text: str) -> None:
    assert not check_affective_text("pt", text), text


def test_the_affective_census_reads_the_portuguese_ui_too() -> None:
    from chimera.prompts import lint as prompt_lint

    root = Path(__file__).resolve().parents[1]
    assert len(prompt_lint._portuguese_ui_values(root / "apps/desktop/src/lib/i18n.tsx")) > 1000
