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
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from chimera.core.checklist import Requirement
from chimera.core.verify import CommandVerifier, VerificationResult

if TYPE_CHECKING:
    from chimera.core.checkpoint import FileSnapshot
from chimera.providers.gateway import Message, MessageLike, SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("core.spec_test")
_FENCE = re.compile(r"^\s*```(?:python)?\s*|\s*```\s*$", re.IGNORECASE | re.MULTILINE)
_TEST_FILE = "test_chimera_spec.py"
_MAX_DIGEST_CHARS = 12_000
#: One line per test in pytest's `-rA` short summary: `PASSED file::name`, `FAILED file::name - …`.
_OUTCOME = re.compile(r"^(PASSED|FAILED|ERROR|XFAIL|XPASS|SKIPPED)\s+(\S+?)::(\w+)", re.MULTILINE)


def parse_outcomes(output: str, file: str) -> dict[str, str]:
    """Per-test outcomes read off a `pytest -rA` run of ``file``: ``{test_name: PASSED|FAILED|…}``.

    Empty when pytest never reached the tests — a collection error, a missing module, a crash — and
    that emptiness is meaningful: on the base workspace it says every test "failed" before the
    change (there was nothing to import), which is the ExecCritic condition for evidence.
    """
    out: dict[str, str] = {}
    for status, path, name in _OUTCOME.findall(output):
        if path.replace("\\", "/").endswith(file):
            out[name] = status
    return out

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


#: The completion budget the generator asks for, explicitly. With none the provider's own default
#: applies, and the reasoning model behind the default tier can spend that whole default thinking
#: and return an empty `content` at 200 OK — `bench/spec_test_vacuity` (2026-09-11) saw the
#: generator return nothing on 8 of 28 tasks that had requirements, and a re-probe of one produced
#: a nine-test module of 4,064 completion tokens, most of them reasoning. `bench/blind_audit` hit
#: the same trap on the delegation path (`TaskSpec.max_tokens=8_000`).
_GEN_MAX_TOKENS = 16_000
#: Attempts before `generate` gives up. The empty reply was route- or run-dependent, not a property
#: of the task (the re-probe above), so one retry at the same settings is the cheap fix; a retry
#: after a `length` finish gets twice the budget, because that one was not chance.
_GEN_ATTEMPTS = 2


class SpecTestGenerator:
    """Generate a pytest module grounded in a task's atomic requirements ("" on any failure)."""

    def __init__(
        self,
        backend: SupportsComplete,
        model: str | None = None,
        *,
        max_tokens: int = _GEN_MAX_TOKENS,
        attempts: int = _GEN_ATTEMPTS,
    ) -> None:
        self.backend = backend
        self.model = model
        self.max_tokens = max_tokens
        self.attempts = max(1, attempts)
        self.last_finish_reason: str = ""
        """`finish_reason` of the last reply `generate` read, verbatim from the provider ("" = none
        reported — which is not "it finished"). `length` is the output ceiling; an empty `content`
        with `stop` is the model declining. Read by the verifier's abstain message, so an abstain
        that used to be silent says why."""
        self.last_attempts: int = 0
        """How many calls the last `generate` made (0 = it never called: no requirements)."""

    def generate(self, task: str, requirements: list[Requirement], *, code_context: str = "") -> str:
        """Return a runnable pytest module, or "" if none could be produced (non-blocking).

        Retries once on a reply with no test in it — an empty `content` or prose — and records the
        provider's `finish_reason` and the attempt count on the instance either way.
        """
        self.last_finish_reason = ""
        self.last_attempts = 0
        if not requirements:
            return ""
        listing = "\n".join(f"- [{r.kind}] {r.text}" for r in requirements)
        prompt = (
            f"Task:\n{task}\n\nAtomic requirements to test:\n{listing}\n\n"
            f"Code in the workspace:\n{code_context or '(no source files found)'}"
        )
        messages: list[MessageLike] = [
            Message(role="system", content=_GEN_SYSTEM), Message(role="user", content=prompt),
        ]
        budget = self.max_tokens
        for attempt in range(1, self.attempts + 1):
            self.last_attempts = attempt
            try:
                result = self.backend.complete(
                    messages, model=self.model, temperature=0.0, max_tokens=budget,
                )
            except Exception as exc:  # noqa: BLE001 — a generator must never break the run
                _log.warning("spec-test generation failed, continuing without it: %s", exc)
                return ""
            self.last_finish_reason = str(getattr(result, "finish_reason", "") or "")
            code = _strip_fence(result.content or "")
            # Guard: only trust output that actually declares a test (a bare prose reply is useless
            # and would otherwise be written to disk and fail collection, falsely blocking the
            # attempt).
            if "def test" in code:
                return code
            _log.warning(
                "spec-test generation returned no test (attempt %d/%d, finish_reason=%r, %d chars)",
                attempt, self.attempts, self.last_finish_reason, len(code),
            )
            if self.last_finish_reason == "length":
                budget *= 2
        return ""


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
        command: str = "python -m pytest -q -rA {file}",
        timeout: int = 120,
    ) -> None:
        self.generator = generator
        self.task = task
        self.requirements = requirements
        self.workspace = Path(workspace)
        self.command = command
        self.timeout = timeout
        self._generated: str | None = None  # None = not attempted; "" = attempted, unusable
        #: The workspace as it was BEFORE the attempt, when the caller has one (the verify-or-revert
        #: loop snapshots it at `autonomous.py` before the worker runs). With it, a generated test
        #: is evidence only if it fails before the change and passes after (ExecCritic, arXiv
        #: 2609.09133): a test that passes on both measured nothing and is excluded from the
        #: verdict; if none remain, the verifier abstains rather than reporting a green it cannot
        #: back. Measured before this existed (`bench/spec_test_vacuity`): see RESULTS.md there.
        #: None keeps the behaviour byte-identical to before — the whole module runs on the
        #: candidate and its exit code decides.
        self.base_snapshot: FileSnapshot | None = None

    def _abstain_note(self) -> str:
        """Why there is nothing to run, with what the generator recorded — an abstain that names
        `finish_reason=length` after two attempts is a budget problem; one that names `stop` is the
        model declining; `none reported` is a backend that does not carry the field."""
        gen = self.generator
        attempts = getattr(gen, "last_attempts", 0)
        reason = getattr(gen, "last_finish_reason", "") or "none reported"
        if not attempts:
            return "spec-test: no runnable tests generated"
        return (
            f"spec-test: no runnable tests generated ({attempts} attempt{'s' if attempts != 1 else ''}, "
            f"finish_reason={reason})"
        )

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
            return VerificationResult(True, self._abstain_note(), abstained=True)
        test_path = self.workspace / _TEST_FILE
        try:
            test_path.write_text(code, encoding="utf-8")
        except OSError as exc:
            return VerificationResult(True, f"spec-test: could not write tests ({exc})", abstained=True)
        # The tests it runs are model-written, so the command is not the user's even when the
        # template is ours: on a host without an isolated sandbox it goes through the host-exec gate.
        runner = CommandVerifier(
            self.command.format(file=_TEST_FILE),
            self.workspace,
            timeout=self.timeout,
            source="spec_test",
        )
        result = runner.verify()
        # `abstained` is carried, not dropped. Every other exit from this method decides carefully
        # between passing and abstaining, and then this line — the one path where a real command
        # ran — threw the distinction away: an abstention arrives here as `passed=True`, so a run
        # that could not be checked came out as `evidence="verifier"`, a receipt naming a test that
        # never reached a verdict. Fixing the runner without this line would have made that worse,
        # by turning a wrong failure into a confident wrong pass.
        if self.base_snapshot is None or result.abstained:
            return VerificationResult(
                result.passed,
                f"spec-grounded tests ({_TEST_FILE}):\n{result.output}",
                abstained=result.abstained,
            )
        return self._against_base(code, result)

    def _against_base(self, code: str, candidate: VerificationResult) -> VerificationResult:
        """The ExecCritic gate: keep only the tests that could have failed before the change.

        The base snapshot is materialised into a temporary directory (text files only — a snapshot
        never holds binaries, and a base that cannot be rebuilt is reported, not guessed), the same
        module runs there under the same command, and each test is classified by its two outcomes:

        - passes before and after → **vacuous** for this change, excluded from the verdict;
        - fails before, passes after → **discriminating**, the evidence the receipt may name;
        - passes before, fails after → **regression**, fails the attempt;
        - fails on both → unchanged from today: the candidate did not satisfy the spec.

        No per-test line on the base (collection error, missing module) means every test failed
        before the change, which is the ordinary case for a task that creates the module.
        """
        after = parse_outcomes(candidate.output, _TEST_FILE)
        if not after:
            # The candidate run produced no per-test lines: a collection error or a crash. That is
            # a failure of the candidate as it always was, and there is nothing to classify.
            return VerificationResult(
                candidate.passed,
                f"spec-grounded tests ({_TEST_FILE}):\n{candidate.output}",
                abstained=candidate.abstained,
            )
        assert self.base_snapshot is not None
        with tempfile.TemporaryDirectory(prefix="chimera-base-") as tmp:
            root = Path(tmp)
            for rel, content in self.base_snapshot.files.items():
                if rel == _TEST_FILE:
                    continue
                target = root / rel
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                except OSError as exc:
                    return VerificationResult(
                        candidate.passed,
                        f"spec-grounded tests ({_TEST_FILE}):\n{candidate.output}\n"
                        f"(the base workspace could not be rebuilt to check the tests against it: {exc})",
                        abstained=candidate.abstained,
                    )
            (root / _TEST_FILE).write_text(code, encoding="utf-8")
            base_run = CommandVerifier(
                self.command.format(file=_TEST_FILE), root, timeout=self.timeout, source="spec_test"
            ).verify()
        before = parse_outcomes(base_run.output, _TEST_FILE)
        vacuous = sorted(t for t, s in after.items() if s == "PASSED" and before.get(t) == "PASSED")
        regressions = sorted(t for t, s in after.items() if s != "PASSED" and before.get(t) == "PASSED")
        failing = sorted(t for t, s in after.items() if s != "PASSED" and before.get(t) != "PASSED")
        discriminating = sorted(t for t, s in after.items() if s == "PASSED" and before.get(t) != "PASSED")
        summary = (
            f"spec-test against the pre-change workspace: {len(discriminating)} discriminating, "
            f"{len(vacuous)} vacuous (pass before and after — excluded), {len(regressions)} regression(s), "
            f"{len(failing)} failing"
        )
        if vacuous:
            summary += "; vacuous: " + ", ".join(vacuous)
        output = f"spec-grounded tests ({_TEST_FILE}):\n{candidate.output}\n{summary}"
        if regressions or failing:
            return VerificationResult(False, output)
        if not discriminating:
            # Every test the generator wrote passes on the base too: nothing here measured the
            # change, and a green verdict would name evidence that does not exist. ABSTAIN, so the
            # caller falls back to its other gates — the same rule as "no runnable tests".
            return VerificationResult(True, output + " — no test could have failed before the change", abstained=True)
        return VerificationResult(True, output)
