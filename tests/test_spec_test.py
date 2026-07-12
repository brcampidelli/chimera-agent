"""Tests for spec-grounded test generation (M18-1). Fakes only — no LLM, no real pytest subprocess."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.core import spec_test
from chimera.core.checklist import Requirement
from chimera.core.spec_test import SpecTestGenerator, SpecTestVerifier, workspace_digest
from chimera.core.verify import VerificationResult


def _reqs() -> list[Requirement]:
    return [Requirement(text="print hello", kind="do")]


class _Backend:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[Any] = []

    def complete(self, messages: Any, *, model: Any = None, temperature: float = 0.0, **k: Any) -> Any:
        self.calls.append(messages)
        return SimpleNamespace(content=self.content)


# --- SpecTestGenerator ------------------------------------------------------------------


def test_generate_returns_test_code() -> None:
    out = SpecTestGenerator(_Backend("def test_hello():\n    assert True")).generate("hi", _reqs(), code_context="x")
    assert out.startswith("def test_hello")


def test_generate_strips_markdown_fences() -> None:
    out = SpecTestGenerator(_Backend("```python\ndef test_x():\n    assert 1\n```")).generate("t", _reqs())
    assert out.startswith("def test_x") and "```" not in out


def test_generate_rejects_prose_without_a_test() -> None:
    assert SpecTestGenerator(_Backend("Sure! Here is how you'd test it.")).generate("t", _reqs()) == ""


def test_generate_no_llm_call_without_requirements() -> None:
    backend = _Backend("def test(): pass")
    assert SpecTestGenerator(backend).generate("t", []) == ""
    assert backend.calls == []


def test_generate_backend_error_is_nonblocking() -> None:
    class _Boom:
        def complete(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("boom")

    assert SpecTestGenerator(_Boom()).generate("t", _reqs()) == ""


# --- workspace_digest -------------------------------------------------------------------


def test_workspace_digest_includes_source_skips_noise(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("VALUE = 1", encoding="utf-8")
    (tmp_path / ".hidden.py").write_text("H = 1", encoding="utf-8")
    (tmp_path / "test_chimera_spec.py").write_text("nope", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "lib.py").write_text("V = 1", encoding="utf-8")
    digest = workspace_digest(tmp_path)
    assert "app.py" in digest and "VALUE = 1" in digest
    assert ".hidden" not in digest and "test_chimera_spec" not in digest and "V = 1" not in digest


# --- SpecTestVerifier -------------------------------------------------------------------


class _Gen:
    def __init__(self, code: str) -> None:
        self.code = code
        self.calls = 0

    def generate(self, task: str, reqs: Any, *, code_context: str = "") -> str:
        self.calls += 1
        return self.code


class _Runner:
    passed = True

    def __init__(self, command: str, workspace: Path, *, timeout: int = 120) -> None:
        self.command = command
        self.workspace = workspace

    def verify(self) -> VerificationResult:
        return VerificationResult(_Runner.passed, "pytest output")


def test_verifier_writes_tests_and_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(spec_test, "CommandVerifier", _Runner)
    _Runner.passed = True
    v = SpecTestVerifier(_Gen("def test_x():\n    assert 1"), "t", _reqs(), tmp_path)
    res = v.verify()
    assert res.passed and "spec-grounded tests" in res.output
    assert (tmp_path / "test_chimera_spec.py").read_text(encoding="utf-8").startswith("def test_x")


def test_verifier_failing_tests_propagate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(spec_test, "CommandVerifier", _Runner)
    _Runner.passed = False
    assert SpecTestVerifier(_Gen("def test_x():\n    assert 0"), "t", _reqs(), tmp_path).verify().passed is False


def test_verifier_abstains_when_nothing_generated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # No runnable tests -> ABSTAIN (not a positive pass): the caller must fall back to its other
    # gates rather than accept on an empty non-block.
    monkeypatch.setattr(spec_test, "CommandVerifier", _Runner)
    res = SpecTestVerifier(_Gen(""), "t", _reqs(), tmp_path).verify()
    assert res.abstained is True
    assert not (tmp_path / "test_chimera_spec.py").exists()


def test_verifier_generates_once_then_reruns(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(spec_test, "CommandVerifier", _Runner)
    _Runner.passed = True
    gen = _Gen("def test_x():\n    assert 1")
    v = SpecTestVerifier(gen, "t", _reqs(), tmp_path)
    v.verify()
    v.verify()
    assert gen.calls == 1  # generated once; the same fixed spec is re-run on retries
