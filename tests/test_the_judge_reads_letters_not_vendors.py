"""`FusionConfig.blind_panel`: the judge reads `Answer A / B / C` in a shuffled order, and the trace
keeps the permutation so a receipt can still say who wrote what.

Off (`blind_panel=False`), nothing changes — byte for byte the prompt the judge always got. The
tests read the prompt the judge actually received, because that is the only place the claim can be
checked. On is the default since `bench/judge_blind` measured the cost at zero (2026-09-11)."""

from __future__ import annotations

import random
from typing import Any

from chimera.fusion import FusionConfig, FusionEngine
from chimera.providers import CompletionResult

PANEL = ["openrouter/vendor-a/big", "openrouter/vendor-b/mid", "openrouter/vendor-c/small"]


class Recording:
    """Answers per model; keeps every prompt so the judge's view can be read back."""

    def __init__(self) -> None:
        self.prompts: dict[str, list[str]] = {}

    def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
        user = next(m for m in messages if (m.get("role") if isinstance(m, dict) else m.role) == "user")
        content = user["content"] if isinstance(user, dict) else user.content
        self.prompts.setdefault(str(model), []).append(content)
        if model == "judge":
            return CompletionResult(content="JUDGE", model="judge")
        if model == "synth":
            return CompletionResult(content="FINAL", model="synth")
        return CompletionResult(content=f"text written by {str(model).split(chr(47))[-1]}", model=str(model))


def _config(**kw: Any) -> FusionConfig:
    return FusionConfig(panel=list(PANEL), judge="judge", synthesizer="synth", **kw)


def test_blind_is_the_default() -> None:
    assert FusionConfig(panel=list(PANEL), judge="judge", synthesizer="synth").blind_panel is True


def test_named_when_switched_off_the_judge_sees_slugs_in_panel_order_and_no_permutation_is_recorded() -> None:
    backend = Recording()
    trace = FusionEngine(backend, _config(blind_panel=False)).run([{"role": "user", "content": "q"}])
    judge_view = backend.prompts["judge"][0]
    assert "--- Answer 1 (model openrouter/vendor-a/big) ---" in judge_view
    assert "--- Answer 3 (model openrouter/vendor-c/small) ---" in judge_view
    assert "Answer A" not in judge_view
    assert trace.shown_order is None


def test_blind_the_judge_sees_letters_and_never_a_vendor_and_the_trace_keeps_the_permutation() -> None:
    backend = Recording()
    engine = FusionEngine(backend, _config(blind_panel=True))
    trace = engine.run([{"role": "user", "content": "q"}])
    judge_view = backend.prompts["judge"][0]
    for slug in PANEL:
        assert slug not in judge_view
    assert "(model " not in judge_view
    for letter in "ABC":
        assert f"--- Answer {letter} ---" in judge_view
    assert trace.shown_order is not None
    assert sorted(trace.shown_order) == [0, 1, 2]
    # The permutation is not decorative: the letter at position p carries the text of panel[shown[p]].
    for p, idx in enumerate(trace.shown_order):
        letter = chr(ord("A") + p)
        block = judge_view.split(f"--- Answer {letter} ---")[1].split("---")[0]
        assert f"text written by {PANEL[idx].split(chr(47))[-1]}" in block


def test_blind_actually_shuffles_across_runs() -> None:
    random.seed(11)
    backend = Recording()
    engine = FusionEngine(backend, _config(blind_panel=True))
    orders = {tuple(engine.run([{"role": "user", "content": "q"}]).shown_order or ()) for _ in range(12)}
    assert len(orders) > 1, "twelve blind runs showed the panel in one order — nothing was shuffled"


def test_blind_skips_errored_panelists_and_the_permutation_only_names_the_shown() -> None:
    class Flaky(Recording):
        def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
            if model == PANEL[1]:
                raise RuntimeError("down")
            return super().complete(messages, model=model, **kwargs)

    backend = Flaky()
    trace = FusionEngine(backend, _config(blind_panel=True)).run([{"role": "user", "content": "q"}])
    assert sorted(trace.shown_order or []) == [0, 2]
    assert "Answer C" not in backend.prompts["judge"][0]


def test_route_meta_carries_the_permutation() -> None:
    backend = Recording()
    result = FusionEngine(backend, _config(blind_panel=True)).complete([{"role": "user", "content": "q"}])
    assert result.route_meta is not None
    assert sorted(result.route_meta["shown_order"]) == [0, 1, 2]
    named = FusionEngine(Recording(), _config(blind_panel=False)).complete([{"role": "user", "content": "q"}])
    assert named.route_meta is not None and named.route_meta["shown_order"] is None


def test_agreed_path_is_blind_too() -> None:
    class Agreeing(Recording):
        def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
            if model in PANEL:
                self.prompts.setdefault(str(model), []).append("")
                return CompletionResult(content="the same answer", model=str(model))
            return super().complete(messages, model=model, **kwargs)

    backend = Agreeing()
    trace = FusionEngine(backend, _config(blind_panel=True, mode="selective")).run(
        [{"role": "user", "content": "q"}]
    )
    assert trace.early_stopped
    synth_view = backend.prompts["synth"][0]
    assert "(model " not in synth_view and "--- Answer A ---" in synth_view
    assert sorted(trace.shown_order or []) == [0, 1]
