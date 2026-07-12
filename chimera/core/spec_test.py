"""Spec-grounded test generation for the verify-or-revert gate (arXiv 2607.06636).

When `solve` has no `--verify` command, the fitness gate falls back to an LLM judging whether the
answer "covers" the task (:class:`~chimera.core.checklist.RequirementChecklist`). That proxy has a
well-measured failure: it rubber-stamps code that is actually wrong — a **false positive** that
silently corrupts every downstream evolution decision. Grounding *executable* test generation in
the task's atomic requirement checklist catches far more real bugs and raises far fewer false
alarms (the paper measured false-alarm 33%→0%). This module turns that weak coverage proxy into
runnable pytest.

Opt-in. Any generation/write/run error degrades to a **non-blocking pass**, so — exactly like the
checklist and progress ledger — it can only ever add an executable check, never a false block from
a bad model response. The value is the *true negative*: a spec-grounded test that correctly fails
wrong code the coverage grade would have passed.
"""

from __future__ import annotations

import re
from pathlib import Path

from chimera.core.checklist import Requirement
from chimera.core.verify import CommandVerifier, VerificationResult
from chimera.providers.gateway import Message, SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("core.spec_test")
_FENCE = re.compile(r"^\s*```(?:python)?\s*|\s*```\s*$", re.IGNORECASE | re.MULTILINE)
_TEST_FILE = "test_chimera_spec.py"
_MAX_DIGEST_CHARS = 12_000

_GEN_SYSTEM = (
    "You write ONE self-contained pytest module that checks whether the code in the current "
    "directory satisfies a list of atomic requirements. Rules: import the code under test from the "
    "workspace modules shown (never redefine it); write one test function per requirement, named "
    "after it; assert the observable behaviour the requirement demands — a 'do' must happen, an "
    "'avoid' must NOT, an 'include' must be present. Prefer real calls over mocks. If a requirement "
    "genuinely cannot be checked in code, `pytest.skip(...)` it — NEVER `assert True` as filler, and "
    "never weaken an assertion just to make it pass. Output ONLY the Python file: no prose, no "
    "markdown fences."
)


def _strip_fence(text: str) -> str:
    return _FENCE.sub("", text.strip()).strip()


def workspace_digest(workspace: Path, *, max_chars: int = _MAX_DIGEST_CHARS) -> str:
    """A bounded listing of the workspace's Python source (paths + content) for the generator.

    The generated tests must import and exercise the code the agent produced, so the generator
    needs to see it. Dotfiles, the generated test file itself, and anything under a virtualenv are
    skipped; the digest stops at ``max_chars`` so a big workspace can't blow the prompt.
    """
    parts: list[str] = []
    total = 0
    for path in sorted(workspace.rglob("*.py")):
        rel = path.relative_to(workspace).as_posix()
        if path.name == _TEST_FILE or any(seg.startswith(".") for seg in rel.split("/")):
            continue
        if any(seg in ("node_modules", "venv", ".venv", "__pycache__") for seg in rel.split("/")):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        chunk = f"# --- {rel} ---\n{text}\n"
        if total + len(chunk) > max_chars:
            parts.append(f"# --- {rel} --- (omitted: digest full)\n")
            break
        parts.append(chunk)
        total += len(chunk)
    return "".join(parts)


class SpecTestGenerator:
    """Generate a pytest module grounded in a task's atomic requirements ("" on any failure)."""

    def __init__(self, backend: SupportsComplete, model: str | None = None) -> None:
        self.backend = backend
        self.model = model

    def generate(self, task: str, requirements: list[Requirement], *, code_context: str = "") -> str:
        """Return a runnable pytest module, or "" if none could be produced (non-blocking)."""
        if not requirements:
            return ""
        listing = "\n".join(f"- [{r.kind}] {r.text}" for r in requirements)
        prompt = (
            f"Task:\n{task}\n\nAtomic requirements to test:\n{listing}\n\n"
            f"Code in the workspace:\n{code_context or '(no source files found)'}"
        )
        try:
            result = self.backend.complete(
                [Message(role="system", content=_GEN_SYSTEM), Message(role="user", content=prompt)],
                model=self.model,
                temperature=0.0,
            )
        except Exception as exc:  # noqa: BLE001 — a generator must never break the run
            _log.warning("spec-test generation failed, continuing without it: %s", exc)
            return ""
        code = _strip_fence(result.content or "")
        # Guard: only trust output that actually declares a test (a bare prose reply is useless and
        # would otherwise be written to disk and fail collection, falsely blocking the attempt).
        return code if "def test" in code else ""


class SpecTestVerifier:
    """A :class:`~chimera.core.verify.Verifier` backed by spec-grounded generated tests.

    Generates the pytest module ONCE (from the extracted requirements + the workspace as it stands
    after the first attempt), then re-runs those *same* tests each verify — so retries converge the
    code onto a fixed spec rather than chasing a moving target. If nothing usable is generated it
    passes (non-blocking); a real generated test that fails is a true negative the gate should heed.
    """

    def __init__(
        self,
        generator: SpecTestGenerator,
        task: str,
        requirements: list[Requirement],
        workspace: Path,
        *,
        command: str = "python -m pytest -q {file}",
        timeout: int = 120,
    ) -> None:
        self.generator = generator
        self.task = task
        self.requirements = requirements
        self.workspace = Path(workspace)
        self.command = command
        self.timeout = timeout
        self._generated: str | None = None  # None = not attempted; "" = attempted, unusable

    def verify(self) -> VerificationResult:
        if self._generated is None:
            self._generated = self.generator.generate(
                self.task, self.requirements, code_context=workspace_digest(self.workspace)
            )
        code = self._generated
        if not code:
            # ABSTAIN, not pass: no runnable tests means no evidence. The caller must fall back to its
            # other gates (Manager, coverage checklist) — accepting on this would be a fail-open that
            # SUPPLANTS those gates with nothing.
            return VerificationResult(True, "spec-test: no runnable tests generated", abstained=True)
        test_path = self.workspace / _TEST_FILE
        try:
            test_path.write_text(code, encoding="utf-8")
        except OSError as exc:
            return VerificationResult(True, f"spec-test: could not write tests ({exc})", abstained=True)
        runner = CommandVerifier(self.command.format(file=_TEST_FILE), self.workspace, timeout=self.timeout)
        result = runner.verify()
        return VerificationResult(result.passed, f"spec-grounded tests ({_TEST_FILE}):\n{result.output}")
