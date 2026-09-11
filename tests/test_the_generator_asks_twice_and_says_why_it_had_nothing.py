"""`bench/spec_test_vacuity` (2026-09-11): the generator returned nothing on 8 of 28 tasks that had
requirements, and a re-probe of one produced a nine-test module — the empty reply was route- or
run-dependent, and the shipped path treated it as a silent abstain. Now: an explicit completion
budget, one retry on a reply with no test in it, twice the budget after a `length` finish, and the
provider's `finish_reason` recorded so the abstain says why. Fakes only — no model, no pytest run."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from chimera.core.checklist import Requirement
from chimera.core.spec_test import _GEN_MAX_TOKENS, SpecTestGenerator, SpecTestVerifier

_MODULE = "def test_hello():\n    assert True\n"


def _reqs() -> list[Requirement]:
    return [Requirement(text="print hello", kind="do")]


class _Replies:
    """A backend that answers from a script of (content, finish_reason) and records every call."""

    def __init__(self, *replies: tuple[str, str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages: Any, *, model: Any = None, temperature: float = 0.0, **k: Any) -> Any:
        self.calls.append({"messages": messages, "model": model, "temperature": temperature, **k})
        content, finish = self.replies.pop(0)
        return SimpleNamespace(content=content, finish_reason=finish)


def test_an_empty_reply_is_asked_again_and_the_second_module_is_kept() -> None:
    backend = _Replies(("", "stop"), (_MODULE, "stop"))
    gen = SpecTestGenerator(backend)
    assert gen.generate("t", _reqs()) == _MODULE.strip()
    assert len(backend.calls) == 2
    assert gen.last_attempts == 2
    assert gen.last_finish_reason == "stop"


def test_a_good_first_reply_is_never_asked_twice() -> None:
    backend = _Replies((_MODULE, "stop"), ("", "stop"))
    gen = SpecTestGenerator(backend)
    assert gen.generate("t", _reqs()) == _MODULE.strip()
    assert len(backend.calls) == 1
    assert gen.last_attempts == 1


def test_the_budget_is_explicit_and_doubles_after_a_length_finish() -> None:
    backend = _Replies(("", "length"), (_MODULE, "stop"))
    gen = SpecTestGenerator(backend)
    gen.generate("t", _reqs())
    assert [c["max_tokens"] for c in backend.calls] == [_GEN_MAX_TOKENS, 2 * _GEN_MAX_TOKENS]


def test_the_budget_does_not_move_when_the_model_simply_stopped() -> None:
    # The re-probe that produced a module ran at the same settings: an empty `stop` is chance on
    # the route, and the retry asks the same question.
    backend = _Replies(("Sure, here is how you would test it.", "stop"), (_MODULE, "stop"))
    gen = SpecTestGenerator(backend, max_tokens=4_000)
    gen.generate("t", _reqs())
    assert [c["max_tokens"] for c in backend.calls] == [4_000, 4_000]


def test_two_empty_replies_give_nothing_and_the_last_reason_is_kept() -> None:
    backend = _Replies(("", "length"), ("", "length"), (_MODULE, "stop"))
    gen = SpecTestGenerator(backend)
    assert gen.generate("t", _reqs()) == ""
    assert len(backend.calls) == 2  # `attempts` bounds it: the third reply is never asked for
    assert gen.last_attempts == 2
    assert gen.last_finish_reason == "length"


def test_a_backend_without_the_field_reads_as_none_reported() -> None:
    class _Bare:
        def complete(self, *a: Any, **k: Any) -> Any:
            return SimpleNamespace(content="")

    gen = SpecTestGenerator(_Bare())
    assert gen.generate("t", _reqs()) == ""
    assert gen.last_finish_reason == ""
    assert gen.last_attempts == 2


def test_a_provider_error_is_not_retried_and_leaves_the_run_alone() -> None:
    class _Boom:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, *a: Any, **k: Any) -> Any:
            self.calls += 1
            raise RuntimeError("boom")

    backend = _Boom()
    gen = SpecTestGenerator(backend)
    assert gen.generate("t", _reqs()) == ""
    assert backend.calls == 1  # an error is the gateway's business (its own fallback chain), not ours


def test_no_requirements_means_no_call_and_no_attempt_recorded() -> None:
    backend = _Replies((_MODULE, "stop"))
    gen = SpecTestGenerator(backend)
    assert gen.generate("t", []) == ""
    assert backend.calls == [] and gen.last_attempts == 0


def test_the_abstain_says_how_many_attempts_and_the_finish_reason(tmp_path: Path) -> None:
    backend = _Replies(("", "length"), ("", "length"))
    verifier = SpecTestVerifier(SpecTestGenerator(backend), "t", _reqs(), tmp_path)
    res = verifier.verify()
    assert res.abstained is True and res.passed is True
    assert res.output == "spec-test: no runnable tests generated (2 attempts, finish_reason=length)"


def test_the_abstain_with_no_field_says_none_reported(tmp_path: Path) -> None:
    class _Bare:
        def complete(self, *a: Any, **k: Any) -> Any:
            return SimpleNamespace(content="prose")

    res = SpecTestVerifier(SpecTestGenerator(_Bare()), "t", _reqs(), tmp_path).verify()
    assert res.output == "spec-test: no runnable tests generated (2 attempts, finish_reason=none reported)"


def test_the_abstain_for_no_requirements_is_the_old_sentence(tmp_path: Path) -> None:
    res = SpecTestVerifier(SpecTestGenerator(_Replies()), "t", [], tmp_path).verify()
    assert res.output == "spec-test: no runnable tests generated"
