"""The LLM-Fusion engine — Chimera's differentiator.

Runs the same task through a *panel* of models, has a *judge* model produce a
structured analysis of their answers (consensus, contradictions, partial coverage,
unique insights, blind spots), then a *synthesizer* writes the final answer grounded
in that analysis.

Whether the synthesis step adds anything is a hypothesis this repository has not measured.
OpenRouter Fusion reported a lift from it; our own paired test (``bench/fusion_paired``) sat at
ceiling and could not show one, and ``docs/multi-agent-policy.md`` lists "does fusion beat one model
at equal budget?" as not measured. Outside work points the other way: 2609.31563 found the best pool
below its strongest member (83.2% against 86.8%, 10 of 10 splits), and 2609.34496 found the final
answer rarely beats the best proposal. Read fusion as a way to get several answers and a
reconciliation, not as a measured gain.

``FusionEngine`` implements :class:`~chimera.providers.gateway.SupportsComplete`, so
it is a drop-in *reasoning* backend anywhere a model is expected. It does not do
tool-calling — fusion is for hard reasoning/synthesis; tool turns stay single-model
(see :mod:`chimera.fusion.router`).
"""

from __future__ import annotations

import difflib
import random
import threading
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

from chimera.config import get_settings
from chimera.core.redact import redact
from chimera.providers.gateway import CompletionResult, Message, MessageLike, SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("fusion.engine")

_JUDGE_SYSTEM = (
    "You are an impartial judge. You are given several independent answers to the "
    "same task. Analyze them — do NOT write a final answer yourself. Produce a "
    "concise, structured analysis with these sections: Consensus, Contradictions, "
    "Partial coverage, Unique insights, Blind spots."
)
_SYNTH_SYSTEM = (
    "You are a synthesizer. Using the original task and the judge's structured "
    "analysis of several candidate answers, write the single best final answer. "
    "Resolve contradictions, fold in unique insights, and avoid the blind spots. "
    "Answer the task directly; do not mention the panel or the judge."
)
_SYNTH_AGREED_SYSTEM = (
    "You are a synthesizer. Several independent answers to the task agree closely. "
    "Using the original task and those answers, write the single best final answer. "
    "Answer the task directly; do not mention that there were multiple answers."
)


def _sum_opt(values: Iterable[int | None]) -> int | None:
    """Sum the reported values; ``None`` if none were reported (never fabricate 0)."""
    reported = [v for v in values if v is not None]
    return sum(reported) if reported else None


def _normalize_ws(text: str) -> str:
    """Lowercase and collapse whitespace, for a lexical similarity comparison."""
    return " ".join(text.split()).lower()


class FusionFailed(RuntimeError):
    """Fusion has no answer it can stand behind: no panelist produced any text.

    Raised instead of synthesising. With every panelist errored or blank there is nothing to fuse
    and nothing to fall back to, and what used to happen (the judge handed a canned "no answers"
    line, the synthesiser then answering on its own) was one unpanelled model labelled ``fusion``.
    A caller sees this exactly as it would see a single model's provider error.
    """


@dataclass
class PanelResponse:
    """One panel model's answer (or its error)."""

    model: str
    content: str = ""
    error: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    finish_reason: str = ""
    """Verbatim from the provider; ``length`` means the answer was cut off at the output ceiling."""
    exc: BaseException | None = field(default=None, repr=False, compare=False)
    """The exception behind ``error``, kept so a stop the caller asked for keeps its type.

    ``error`` is redacted text for the trace. A spend ceiling or a missing key turned into text and
    then into :class:`FusionFailed` would no longer match the caller's ``except SpendExceeded``."""

    def answered(self) -> bool:
        """True when this panelist produced an answer that can be compared with another one.

        Not an error, not blank, and not cut off at the ceiling. A reasoning model that spends its
        whole budget thinking comes back ``content=""``/``finish_reason="length"``, and two of those
        are a perfect text match — which is how two empty probes used to "agree".
        """
        return self.has_text() and self.finish_reason != "length"

    def has_text(self) -> bool:
        """True when this panelist returned something to read, even if it was cut off."""
        return self.error is None and bool(self.content.strip())


@dataclass
class StageUsage:
    """Token usage for one fusion stage (a panel model, the judge, or the synthesizer)."""

    stage: Literal["panel", "judge", "synth"]
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    # Carried so a fused turn is priced like a single call: the cache share of each stage's prompt
    # at its own rate. Without them every stage billed its whole prompt at the input rate.
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


def _stage_usage(stage: Literal["judge", "synth"], result: CompletionResult) -> StageUsage:
    return StageUsage(
        stage, result.model, result.prompt_tokens, result.completion_tokens,
        result.cache_read_tokens, result.cache_write_tokens,
    )


@dataclass
class FusionTrace:
    """Full record of a fusion run, for inspection and the CLI."""

    panel: list[PanelResponse]
    judge_analysis: str
    final: str
    usage: list[StageUsage] = field(default_factory=list)
    early_stopped: bool = False  # selective mode: probe agreed, panel+judge short-circuited
    aggregation: Literal["synth", "vote", "fallback"] = (
        "synth"  # task-typed routing: synthesize vs majority-vote; fallback = a stage failed
    )
    fallback_stage: Literal["judge", "synth"] | None = None
    """When ``aggregation == "fallback"``: the stage that failed (raised, or came back empty after
    its retry), so the answer shipped is a panel answer rather than a synthesis."""
    fallback_reason: str = ""
    """What that stage said, redacted and bounded like a panel error; empty on a normal run."""
    finish_reasons: dict[str, str] = field(default_factory=dict)
    """`finish_reason` of the judge and synthesiser calls, verbatim from the provider, by stage
    (``judge`` / ``synth``); a stage that was retried after an empty reply carries a second key,
    ``<stage>_first``, with the reason the first reply gave. Empty means the provider reported
    nothing, which is not "it finished"."""
    shown_order: list[int] | None = None
    """How the judge (or the agreed-path synthesiser) saw the panel, when it was shown blind.

    ``shown_order[p]`` is the index into ``panel`` of the answer presented at position ``p`` as
    ``Answer A``, ``Answer B``, … — the permutation that lets a receipt attribute a blind label back
    to the model that wrote it. ``None`` means the panel was shown named and in panel order, which
    is what ``FusionConfig.blind_panel=False`` does."""

    def successful_panel(self) -> list[PanelResponse]:
        return [r for r in self.panel if r.error is None]

    def panel_diversity(self) -> float | None:
        """Mean pairwise dissimilarity (0..1) of the successful panel answers — the panel-independence
        axis (MALLM / blind-panel, arXiv 2607.05477 + 2607.02507).

        The fusion panel is blind by construction — each model answers the same prompt with no sight of
        the others (:meth:`FusionEngine._run_panel`). That makes the calls separate, not the answers
        independent: models trained on overlapping data err together, and ``bench/panel_correlation``
        measured ICC(1) +0.527 on three frontier models over AIME, so three members carried 1.46
        effective votes, not three. This number is lexical dissimilarity, and high diversity means the
        texts differ, not that their errors do; low diversity means it converged (agreement — the cheap
        early-stop / vote territory). ``None`` with fewer than two answers to compare.

        Only answers count (:meth:`PanelResponse.answered`): two blank replies are a perfect text
        match, and a blank beside a real answer is maximal "disagreement" — neither says anything
        about whether the panel converged, which is the same defect S30-01 removed from ``_agree``.
        """
        texts = [_normalize_ws(r.content) for r in self.panel if r.answered()]
        if len(texts) < 2:
            return None
        dissims = [
            1.0 - difflib.SequenceMatcher(None, a, b).ratio()
            for i, a in enumerate(texts)
            for b in texts[i + 1 :]
        ]
        return sum(dissims) / len(dissims)

    def prompt_tokens(self) -> int | None:
        """Total input tokens across stages, or ``None`` if no stage reported usage."""
        return _sum_opt(u.prompt_tokens for u in self.usage)

    def completion_tokens(self) -> int | None:
        """Total output tokens across stages, or ``None`` if no stage reported usage."""
        return _sum_opt(u.completion_tokens for u in self.usage)

    def total_tokens(self) -> int | None:
        """Prompt + completion tokens, or ``None`` if usage was never reported."""
        p, c = self.prompt_tokens(), self.completion_tokens()
        if p is None and c is None:
            return None
        return (p or 0) + (c or 0)

    def by_stage(self) -> dict[str, tuple[int, int]]:
        """Aggregate ``(prompt, completion)`` tokens per stage name."""
        agg: dict[str, tuple[int, int]] = {}
        for u in self.usage:
            p, c = agg.get(u.stage, (0, 0))
            agg[u.stage] = (p + (u.prompt_tokens or 0), c + (u.completion_tokens or 0))
        return agg


def _vendor_of(model: str) -> str:
    """The lab a slug comes from — ``openrouter/anthropic/claude-opus-4-8`` -> ``anthropic``.

    Deliberately crude: it reads the segment after a leading gateway prefix and gives up rather than
    guessing. Two models from one lab are not two independent votes, and a wrong guess here would
    label a receipt with a confidence nobody measured.
    """
    parts = [p for p in model.split("/") if p]
    if not parts:
        return ""
    if len(parts) >= 3 and parts[0] in {"openrouter", "litellm", "openai_like"}:
        return parts[1].lower()
    return (parts[0] if len(parts) > 1 else "").lower()


@dataclass
class FusionConfig:
    """Which models play each role, and how the panel runs."""

    panel: list[str]
    judge: str
    synthesizer: str
    max_workers: int = 4
    temperature: float = 0.3
    # Per-panelist decode spread (diversity sampling): when non-empty, panelist i samples at
    # panel_temperatures[i % len] instead of the single ``temperature`` — one low-temp correctness
    # anchor + higher-temp explorers widen the candidate set the judge/synthesizer selects from, at
    # near-zero extra cost. Empty (default) = every panelist at ``temperature`` (behaviour-preserving).
    panel_temperatures: list[float] = field(default_factory=list)
    mode: Literal["full", "selective"] = "full"
    probe_k: int = 2
    agreement_threshold: float = 0.8
    # Task-typed aggregation (MALLM, arXiv 2607.05477): when on, a logic/single-answer task on which
    # the panel reaches a clear majority is aggregated by VOTE (skipping judge+synth) rather than
    # synthesized — a correct minority answer isn't averaged away, and it's cheaper. Off by default:
    # every other task, and any logic task without a majority, still uses judge -> synthesizer.
    task_typed: bool = False
    vote_threshold: float = 0.85
    # Blind presentation (arXiv 2609.08016): the judge and the agreed-path synthesiser see the panel
    # as ``Answer A / B / C`` in a shuffled order, never as ``Answer 1 (model <vendor slug>)`` in
    # arrival order — the vendor name and the position are not evidence about an answer, and a judge
    # given them uses them. The permutation is kept on the trace (``shown_order``) so the receipt
    # still attributes every answer. On by default: ``bench/judge_blind`` (2026-09-11, 360 runs)
    # measured its cost at zero — 240/240 named, 119/120 blind, the one miss a unit — on a corpus
    # the judge could solve alone, so it could not show the bias either; a label the judge does not
    # need is a label it should not be shown. ``False`` restores the named, ordered presentation.
    blind_panel: bool = True
    # The judge's and the synthesiser's completion budgets, explicit. `bench/judge_blind_qa`
    # (2026-09-12, 341 runs): the median judge + synthesiser reply was 1,901 completion tokens, the
    # 90th percentile 12,628 — and 29 runs (8.5%) ran past 16k, one of them to 146k, carrying 71%
    # of the run's cost and passing 8 of 29 where the rest passed two in three. A reasoning judge
    # with no bound spends the provider's ceiling thinking; these bound it at the far end of what
    # a converged reply needs. An empty reply is asked once more (twice the budget after `length`).
    judge_max_tokens: int = 16_000
    synth_max_tokens: int = 16_000

    def role_kinship(self) -> dict[str, object]:
        """How independent the judge actually is from the panel it grades.

        Fusion is this project's claim to an *independent* signal rather than a self-report, and the
        shipped default contradicted it: ``_DEFAULT_JUDGE`` was ``_DEFAULT_PANEL[0]``, the same slug
        verbatim, so the judge graded its own answer. The default is fixed; this reports the case
        that remains, because a user with one provider key has no way to avoid overlap and deserves
        a labelled receipt rather than a crash.

        Two degrees, and the weaker one is easy to miss: ``judge_is_panelist`` is the judge grading
        its own answer, and ``judge_shares_vendor_with`` is the judge and a panelist coming from the
        same lab — not the same model, but not two independent votes either.
        """
        vendor = _vendor_of(self.judge)
        kin = [m for m in self.panel if m != self.judge and _vendor_of(m) == vendor]
        return {
            "judge_is_panelist": self.judge in self.panel,
            "judge_shares_vendor_with": kin,
            "independent": self.judge not in self.panel and not kin,
        }

    def temperature_for(self, model: str) -> float:
        """Sampling temperature for one panelist — its slot in the spread, else the single default.

        Keyed by the model's position in the FULL ``panel`` (not the sublist a call may receive in
        selective mode's probe/rest split), so the same panelist always samples at the same
        temperature regardless of which stage requested it.
        """
        if not self.panel_temperatures or model not in self.panel:
            return self.temperature
        return self.panel_temperatures[self.panel.index(model) % len(self.panel_temperatures)]

    @classmethod
    def from_settings(cls, settings: Any = None) -> FusionConfig:
        """Every fusion field the settings carry, from ``settings`` or the process-wide instance.

        The models here are ``CHIMERA_FUSION_PANEL`` and friends verbatim — the frontier default when
        nobody set them. A caller that is choosing a panel for a user wants
        :func:`chimera.fusion.factory.fusion_config`, which draws it from the user's tier ladder.
        """
        s = settings if settings is not None else get_settings()
        mode: Literal["full", "selective"] = "selective" if s.fusion_mode == "selective" else "full"
        return cls(
            panel=list(s.fusion_panel),
            judge=s.fusion_judge,
            synthesizer=s.fusion_synthesizer,
            mode=mode,
            probe_k=s.fusion_probe_k,
            agreement_threshold=s.fusion_agreement_threshold,
            task_typed=s.fusion_task_typed,
            panel_temperatures=list(s.fusion_panel_temperatures),
            blind_panel=s.fusion_blind_panel,
        )


def _finish_reasons(judge: CompletionResult | None, synth: CompletionResult | None) -> dict[str, str]:
    """What the provider said about how each stage stopped, and whether the stage was asked twice."""
    out: dict[str, str] = {}
    for stage, result in (("judge", judge), ("synth", synth)):
        if result is None:
            continue
        out[stage] = str(getattr(result, "finish_reason", "") or "")
        retried = (result.route_meta or {}).get("retried_after")
        if retried:
            out[f"{stage}_first"] = str(retried)
    return out


def _content_text(content: object) -> str:
    """The TEXT of a message's content — never a stringified multimodal list.

    A vision turn's content is a list of parts ({"type":"text",...}, {"type":"image_url",...} with a
    base64 data URL). ``str()`` on that would dump the base64 blob into the judge/synth prompt (token
    blow-up + nonsense). Join only the text parts instead.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(p.get("text", ""))
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        ).strip()
    return str(content) if content else ""


#: Appended to the caller's system message for the panel only.
#:
#: The caller is usually the agent loop, and its default prompt says to use the provided tools.
#: The panel is given none, and fusion answers only the steps that offer none. A model told to
#: use tools it was not given tends to describe the calls it would make instead of answering
#: (study 25, defect 5).
_NO_TOOLS_NOTE = (
    "This step has no tools. Answer from what is already in the conversation, and do not describe "
    "tool calls you cannot make."
)


def _without_tools(messages: list[MessageLike]) -> list[MessageLike]:
    """``messages`` with :data:`_NO_TOOLS_NOTE` on the system message (one is added if there is none)."""
    if messages:
        first = messages[0]
        data = first.as_dict() if isinstance(first, Message) else dict(first)
        if data.get("role") == "system":
            data["content"] = f"{_content_text(data.get('content', ''))}\n\n{_NO_TOOLS_NOTE}"
            return [data, *messages[1:]]
    return [{"role": "system", "content": _NO_TOOLS_NOTE}, *messages]


def _conversation_text(messages: list[MessageLike]) -> str:
    lines: list[str] = []
    for message in messages:
        data = message.as_dict() if isinstance(message, Message) else message
        role = str(data.get("role", "user"))
        content = _content_text(data.get("content", ""))
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


@dataclass
class _Settled:
    """How a panel became one answer: the stage results, and the failed stage if it fell back."""

    analysis: str
    final: str
    aggregation: Literal["synth", "vote", "fallback"]
    judge: CompletionResult | None = None
    synth: CompletionResult | None = None
    shown: list[int] | None = None
    fallback_stage: Literal["judge", "synth"] | None = None
    fallback_reason: str = ""


def _must_propagate(exc: BaseException) -> bool:
    """True for a failure that is a stop, not a fault: the run's spend or token ceiling, or no key.

    ``SpendCappedBackend`` raises :class:`SpendExceeded` because the person set a ceiling and the run
    reached it; ``orchestration_api`` catches exactly that type to say so. A missing or rejected key
    fails every call the same way, every time. Neither is the judge "failing" in the sense the panel
    fallback exists for, and swallowing them turned a working cap into a reported success and a
    configuration error into a silent single-panelist answer on every turn.
    """
    # Imported here: ``chimera.orchestration`` imports the fusion package on its way in.
    from chimera.orchestration.budget import BudgetExceeded
    from chimera.providers.gateway import MissingCredentialsError

    return isinstance(exc, (BudgetExceeded, MissingCredentialsError))


def _require_text(panel: list[PanelResponse]) -> None:
    """Raise :class:`FusionFailed` when no panelist produced any text: there is nothing to fuse.

    If a panelist stopped on a ceiling or a missing key, that exception is raised instead, with its
    own type: the panel came back empty because the run was told to stop, not because fusion failed.
    """
    if any(r.has_text() for r in panel):
        return
    stop = next((r.exc for r in panel if r.exc is not None and _must_propagate(r.exc)), None)
    if stop is not None:
        raise stop
    why = "; ".join(
        f"{r.model}: {r.error}"
        if r.error is not None
        else f"{r.model}: empty reply (finish_reason={r.finish_reason or 'none reported'})"
        for r in panel
    )
    raise FusionFailed(f"no panel model produced an answer ({why or 'the panel is empty'})")


def _stage_error(exc: BaseException) -> str:
    """A judge or synthesiser exception as the trace keeps it: redacted and bounded, like a panel error."""
    return redact(f"{type(exc).__name__}: {exc}")[:200]


def _empty_reason(result: CompletionResult) -> str:
    reason = str(getattr(result, "finish_reason", "") or "") or "none reported"
    return f"empty reply after one retry (finish_reason={reason})"


class FusionEngine:
    """Orchestrates panel -> judge -> synthesizer over a model backend."""

    def __init__(
        self,
        backend: SupportsComplete,
        config: FusionConfig | None = None,
        *,
        source: Callable[[], FusionConfig] | None = None,
    ) -> None:
        self.backend = backend
        # Pinned when the caller supplied one, and that is load-bearing: `_cast_for_turn` overlays
        # the request's own cast, `fusion_for_role` builds a role's panel, and a bench comparing two
        # casts is asking for exactly this. Everything else re-reads at the start of a run.
        self._pinned = config
        # What an unpinned engine re-reads. The default is the raw settings — the frontier panel
        # unless one was named — so every product surface passes the factory's ladder-aware source
        # instead (`chimera.fusion.factory.fusion_engine`); a bare engine is left for benches that
        # measure the default panel on purpose.
        self._source: Callable[[], FusionConfig] = source or FusionConfig.from_settings
        self.config = config or self._source()
        # Guards the re-read below. The app hands ONE engine to every surface, so two fused turns
        # can be inside `run` at once.
        self._lock = threading.Lock()
        self._in_flight = 0

    @contextmanager
    def _current_cast(self) -> Iterator[None]:
        """Hold the cast steady for one run, and pick up an edited one between runs.

        The app builds ONE engine at boot (`cli/main.py`) and keeps it for the life of the process,
        and `FusionConfig` is a plain dataclass with no lazy re-read. So `CHIMERA_FUSION_PANEL`,
        `_JUDGE` and `_SYNTHESIZER` landed in `.env`, the Fusion screen confirmed "New conversations
        start with this cast", and every fused turn until the next relaunch still used the launch
        cast. The confirmation is what turned it into a lie.

        Re-read only when nothing else is running, because `self.config` is read fourteen times
        across a run: a save landing mid-run would otherwise change the panel between the probe and
        the rest of it, and a turn assembled from two different casts is a worse answer than one
        assembled from a slightly stale one. A run that starts during another simply keeps the cast
        already in force and picks up the new one next time.
        """
        with self._lock:
            if self._pinned is None and self._in_flight == 0:
                self.config = self._source()
            self._in_flight += 1
        try:
            yield
        finally:
            with self._lock:
                self._in_flight -= 1

    def run(self, messages: list[MessageLike]) -> FusionTrace:
        with self._current_cast():
            if self.config.mode == "selective" and len(self.config.panel) >= 2:
                return self._run_selective(messages)
            return self._run_full(messages)

    def _run_full(self, messages: list[MessageLike]) -> FusionTrace:
        _log.debug("fusion engaged: %d-model panel -> judge -> synthesizer", len(self.config.panel))
        panel = self._run_panel(messages)
        return self._trace(panel, self._settle(messages, panel))

    def _trace(self, panel: list[PanelResponse], settled: _Settled, *, early: bool = False) -> FusionTrace:
        trace = FusionTrace(
            panel=panel,
            judge_analysis=settled.analysis,
            final=settled.final,
            usage=self._collect_usage(panel, settled.judge, settled.synth),
            early_stopped=early,
            aggregation=settled.aggregation,
            shown_order=settled.shown,
            finish_reasons=_finish_reasons(settled.judge, settled.synth),
            fallback_stage=settled.fallback_stage,
            fallback_reason=settled.fallback_reason,
        )
        self._log_usage(trace)
        return trace

    def _aggregate(
        self, messages: list[MessageLike], panel: list[PanelResponse]
    ) -> tuple[
        str,
        str,
        Literal["synth", "vote"],
        CompletionResult | None,
        CompletionResult | None,
        list[int] | None,
    ]:
        """Aggregate the panel into a final answer, routing by task type when enabled.

        Returns ``(judge_analysis, final, aggregation, judge_result, synth_result, shown_order)``. For a
        logic-typed task on which the panel reaches a clear majority, aggregates by VOTE (no judge
        or synthesizer call — the majority answer *is* the final); otherwise runs the judge ->
        synthesizer path. The vote branch is conservative: it needs ≥2 successful panel answers and a
        real majority cluster, else it falls through to synthesis (today's behaviour).

        This is the raw stage: a judge or synthesiser error propagates, which is what the judge
        benches (`bench/judge_blind*`) retry on. A run goes through :meth:`_settle`, which keeps
        the panel when a stage fails.
        """
        winner = self._vote(messages, panel)
        if winner is not None:
            _log.debug("fusion task-typed: logic task with panel majority -> vote (skipped judge+synth)")
            return "", winner, "vote", None, None, None
        judge, shown = self._run_judge(messages, panel)
        synth = self._run_synth(messages, judge.content)
        return judge.content, synth.content, "synth", judge, synth, shown

    def _vote(self, messages: list[MessageLike], panel: list[PanelResponse]) -> str | None:
        """The task-typed vote, when it applies and the panel reached a clear majority."""
        ok = [r for r in panel if r.error is None]
        if not (self.config.task_typed and len(ok) >= 2):
            return None
        from chimera.fusion.task_type import classify_task_type

        if classify_task_type(messages) != "logic":
            return None
        from chimera.fusion.consistency import majority

        # A blank or cut-off answer was asked but did not vote: "" keeps it in the denominator and
        # out of every cluster, so it can only make a majority harder.
        votes = [r.content if r.answered() else "" for r in ok]
        return majority(votes, threshold=self.config.vote_threshold)

    def _settle(self, messages: list[MessageLike], panel: list[PanelResponse]) -> _Settled:
        """:meth:`_aggregate` for a real run: a judge or synthesiser failure keeps the panel.

        ``_aggregate`` raises whatever a stage raises, and the judge benches rely on that: they
        retry the whole aggregation on a provider error. A user's turn has no such loop. Before
        this, a judge 429 threw away every panel answer already paid for, and a synthesiser empty
        after its retry shipped ``final=""``. ``verified.py`` already states the rule (a verifier
        failure never loses the answer) and this is that rule for fusion; 2610.01110 measured
        keeping an available candidate when judging fails at 61/500 submissions recovered for no
        extra cost.
        """
        _require_text(panel)
        winner = self._vote(messages, panel)
        if winner is not None:
            _log.debug("fusion task-typed: logic task with panel majority -> vote (skipped judge+synth)")
            return _Settled("", winner, "vote")
        try:
            judge, shown = self._run_judge(messages, panel)
        except Exception as exc:  # noqa: BLE001 - a judge failure must not lose the panel
            if _must_propagate(exc):
                raise
            return self._fallback(panel, "judge", _stage_error(exc))
        if not judge.content.strip():
            return self._fallback(panel, "judge", _empty_reason(judge), judge=judge, shown=shown)
        try:
            synth = self._run_synth(messages, judge.content)
        except Exception as exc:  # noqa: BLE001 - same rule for the synthesiser
            if _must_propagate(exc):
                raise
            return self._fallback(panel, "synth", _stage_error(exc), judge=judge, shown=shown)
        if not synth.content.strip():
            return self._fallback(
                panel, "synth", _empty_reason(synth), judge=judge, synth=synth, shown=shown
            )
        return _Settled(judge.content, synth.content, "synth", judge, synth, shown)

    def _fallback(
        self,
        panel: list[PanelResponse],
        stage: Literal["judge", "synth"],
        reason: str,
        *,
        judge: CompletionResult | None = None,
        synth: CompletionResult | None = None,
        shown: list[int] | None = None,
    ) -> _Settled:
        """The panel's own answer when aggregation failed: its majority, else the first that answered.

        The majority uses the vote threshold and the vote's rule (a blank or cut-off answer never
        wins). Without one, the first complete answer in panel order; only if none was complete,
        the first with any text. :func:`_require_text` has already ruled out a panel with nothing
        in it, so this always returns text.
        """
        from chimera.fusion.consistency import majority

        _log.warning("fusion %s failed (%s); shipping a panel answer instead", stage, reason)
        ok = [r for r in panel if r.error is None]
        votes = [r.content if r.answered() else "" for r in ok]
        final = majority(votes, threshold=self.config.vote_threshold)
        if final is None:
            final = next((r.content for r in ok if r.answered()), None) or next(
                r.content for r in ok if r.has_text()
            )
        return _Settled(
            judge.content if judge is not None else "",
            final,
            "fallback",
            judge,
            synth,
            shown,
            fallback_stage=stage,
            fallback_reason=reason,
        )

    def _bounded_call(
        self, messages: list[MessageLike], *, model: str, temperature: float, budget: int, stage: str
    ) -> CompletionResult:
        """One judge or synthesiser call under an explicit budget, asked once more if it came back
        empty — the generator's rule (`chimera/core/spec_test.py`): the empty reply is a runaway
        that belongs to the draw, not the prompt. The reason the first reply gave is kept on the
        result's ``route_meta`` so the trace can say the stage was retried."""
        result = self.backend.complete(messages, model=model, temperature=temperature, max_tokens=budget)
        if (result.content or "").strip():
            return result
        first = str(getattr(result, "finish_reason", "") or "")
        _log.warning("fusion %s returned nothing (finish_reason=%r); asking once more", stage, first)
        again = self.backend.complete(
            messages, model=model, temperature=temperature,
            max_tokens=budget * 2 if first == "length" else budget,
        )
        return again.model_copy(update={"route_meta": {**(again.route_meta or {}), "retried_after": first or "none reported"}})

    def _run_selective(self, messages: list[MessageLike]) -> FusionTrace:
        """Probe a few models first; short-circuit on agreement, else escalate to full.

        Agreement is a cheap local text-similarity check (no extra model call), so a
        disagreeing turn costs exactly the same as full fusion while an agreeing turn
        skips the rest of the panel and the judge. The synthesis step is always kept, so
        the early stop changes the cost and not the shape of the answer (whether that step
        adds anything is unmeasured here; see the module docstring).
        """
        k = max(2, min(self.config.probe_k, len(self.config.panel)))
        probe = self._run_panel(messages, self.config.panel[:k])
        ok = [r for r in probe if r.error is None]
        if len(ok) >= 2 and self._agree(ok):
            _log.debug("fusion early-stop: %d probe models agreed", len(ok))
            try:
                agreed, shown = self._run_synth_agreed(messages, ok)
            except Exception as exc:  # noqa: BLE001 - the agreeing probe is still an answer
                if _must_propagate(exc):
                    raise
                return self._trace(probe, self._fallback(probe, "synth", _stage_error(exc)), early=True)
            if not agreed.content.strip():
                settled = self._fallback(probe, "synth", _empty_reason(agreed), synth=agreed, shown=shown)
                return self._trace(probe, settled, early=True)
            return self._trace(probe, _Settled("", agreed.content, "synth", None, agreed, shown), early=True)
        rest = self._run_panel(messages, self.config.panel[k:])
        panel = probe + rest
        return self._trace(panel, self._settle(messages, panel))

    def _agree(self, responses: list[PanelResponse]) -> bool:
        """True when every pair of probe answers is at least ``agreement_threshold`` similar.

        Only answers can agree. ``SequenceMatcher(None, "", "").ratio()`` is 1.0, so two blank
        probes used to be a perfect match and skip the panel and the judge; a replay of
        ``bench/judge_blind_hard`` stopped early on 8 of 50 problems this way, all 8 on empty pairs.
        A blank, errored or cut-off probe means the check has nothing to compare — escalate.
        """
        if not all(r.answered() for r in responses):
            return False
        texts = [_normalize_ws(r.content) for r in responses]
        ratios = [
            difflib.SequenceMatcher(None, a, b).ratio()
            for i, a in enumerate(texts)
            for b in texts[i + 1 :]
        ]
        return bool(ratios) and min(ratios) >= self.config.agreement_threshold

    # -- SupportsComplete --------------------------------------------------
    def complete(
        self,
        messages: list[MessageLike],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        """Run the fusion pipeline and return the synthesized answer.

        ``tools`` is ignored — fusion is a reasoning backend, not a tool-caller. ``temperature`` and
        ``max_tokens`` are ALSO ignored on purpose: fusion is a multi-stage pipeline with its own
        per-stage sampling (a diverse panel, a low-temperature judge, a synthesizer — all from
        ``self.config``), so a single protocol-level temperature has no coherent meaning here. They
        stay in the signature only for :class:`SupportsComplete` compatibility.

        **``model`` is ignored too, and that one gets a warning rather than a debug line.** A panel
        has no single model to honour, so the argument cannot be obeyed — but a caller who passes
        one believes they chose the model, and until now they were wrong in silence. That silence
        cost real money: role routing built a bare engine, passed it the role's model, and got the
        frontier default panel instead; the only symptom was a benchmark arm that ran inexplicably
        slowly. If you want a specific set of models, build the ``FusionConfig`` — see
        :func:`chimera.api.roles.fusion_for_role`.
        """
        if tools:
            _log.debug("fusion ignores %d tool schema(s); use a single model for tools", len(tools))
        if model:
            _log.warning(
                "fusion ignores model=%r — a panel has no single model. The panel actually running "
                "is %s (judge=%s). Set FusionConfig.panel to choose.",
                model,
                self.config.panel,
                self.config.judge,
            )
        trace = self.run(messages)
        route_meta = {
            "kind": "fusion",
            "aggregation": trace.aggregation,
            "fallback_stage": trace.fallback_stage,
            "fallback_reason": trace.fallback_reason,
            "early_stopped": trace.early_stopped,
            "diversity": trace.panel_diversity(),
            "panel": [
                {
                    "model": r.model,
                    "content": r.content,
                    "error": r.error,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "finish_reason": r.finish_reason,
                    "temperature": self.config.temperature_for(r.model),
                }
                for r in trace.panel
            ],
            "judge_analysis": trace.judge_analysis,
            "shown_order": trace.shown_order,
            "finish_reasons": dict(trace.finish_reasons),
            "stages": [
                {
                    "stage": u.stage,
                    "model": u.model,
                    "prompt_tokens": u.prompt_tokens,
                    "completion_tokens": u.completion_tokens,
                    "cache_read_tokens": u.cache_read_tokens,
                    "cache_write_tokens": u.cache_write_tokens,
                }
                for u in trace.usage
            ],
        }
        return CompletionResult(
            content=trace.final,
            model="fusion",
            prompt_tokens=trace.prompt_tokens(),
            completion_tokens=trace.completion_tokens(),
            route_meta=route_meta,
        )

    # -- stages ------------------------------------------------------------
    def _run_panel(
        self, messages: list[MessageLike], models: list[str] | None = None
    ) -> list[PanelResponse]:
        panel_models = models if models is not None else self.config.panel
        messages = _without_tools(messages)

        def call(model: str) -> PanelResponse:
            try:
                result = self.backend.complete(
                    messages, model=model, temperature=self.config.temperature_for(model)
                )
                return PanelResponse(
                    model=model,
                    content=result.content,
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    finish_reason=str(getattr(result, "finish_reason", "") or ""),
                    cache_read_tokens=result.cache_read_tokens,
                    cache_write_tokens=result.cache_write_tokens,
                )
            except Exception as exc:  # one model failing must not sink the panel
                _log.warning("panel model %s failed: %s", model, exc)
                # Redacted and bounded, because this one is SERVED: it rides `route_meta` out to the
                # desktop app and to `/v1/chat/completions`, and it was the only error surface in the
                # app with no limit at all — one message per panel model, verbatim. A provider's
                # error body is its own prose and can quote our prompt back at us.
                #
                # Kept as text rather than reduced to a category: which model said what is the reason
                # anyone reads a fusion trace, and `FailoverReason` alone would flatten five distinct
                # failures into one word. The first 200 characters carry the sentence that matters.
                return PanelResponse(model=model, error=redact(str(exc))[:200], exc=exc)

        workers = max(1, min(self.config.max_workers, len(panel_models)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(call, panel_models))

    def _present(self, panel: list[PanelResponse]) -> tuple[str, list[int] | None]:
        """The panel as the judge or synthesiser will read it, and the order it was shown in.

        Named (the default): ``Answer 1 (model <slug>)`` in panel order — the reader knows the vendor
        and the position. Blind (``config.blind_panel``): ``Answer A / B / C`` in a fresh random
        order, and the permutation comes back so the trace can attribute each letter to its model.
        Errored panelists are never shown either way.
        """
        shown = [i for i, r in enumerate(panel) if r.error is None]
        # A shorter answer adds no new content when it is an exact prefix of another member.
        # Keep the longest rendering; for duplicate longest strings, keep the first panelist.
        contents = [panel[index].content for index in shown]
        keep: list[int] = []
        seen: set[str] = set()
        for index in shown:
            content = panel[index].content
            if content in seen:
                continue
            seen.add(content)
            if any(other.startswith(content) and len(other) > len(content) for other in contents):
                continue
            keep.append(index)
        shown = keep
        if not self.config.blind_panel:
            text = "\n\n".join(
                f"--- Answer {p} (model {panel[i].model}) ---\n{panel[i].content}"
                for p, i in enumerate(shown, 1)
            )
            return text, None
        random.shuffle(shown)
        text = "\n\n".join(
            f"--- Answer {chr(ord('A') + p)} ---\n{panel[i].content}" for p, i in enumerate(shown)
        )
        return text, shown

    def _run_judge(
        self, messages: list[MessageLike], panel: list[PanelResponse]
    ) -> tuple[CompletionResult, list[int] | None]:
        _require_text(panel)  # a judge is never asked to analyse an empty panel
        answers, shown = self._present(panel)
        user = f"Task and context:\n{_conversation_text(messages)}\n\nCandidate answers:\n{answers}"
        result = self._bounded_call(
            [Message(role="system", content=_JUDGE_SYSTEM), Message(role="user", content=user)],
            model=self.config.judge, temperature=0.1, budget=self.config.judge_max_tokens, stage="judge",
        )
        return result, shown

    def _run_synth(self, messages: list[MessageLike], judge_analysis: str) -> CompletionResult:
        user = (
            f"Original task and context:\n{_conversation_text(messages)}\n\n"
            f"Judge's analysis:\n{judge_analysis}"
        )
        return self._bounded_call(
            [Message(role="system", content=_SYNTH_SYSTEM), Message(role="user", content=user)],
            model=self.config.synthesizer, temperature=self.config.temperature,
            budget=self.config.synth_max_tokens, stage="synth",
        )

    def _run_synth_agreed(
        self, messages: list[MessageLike], answers: list[PanelResponse]
    ) -> tuple[CompletionResult, list[int] | None]:
        """Synthesize directly from agreeing probe answers (no judge step)."""
        joined, shown = self._present(answers)
        user = (
            f"Original task and context:\n{_conversation_text(messages)}\n\n"
            f"Agreeing answers:\n{joined}"
        )
        result = self._bounded_call(
            [
                Message(role="system", content=_SYNTH_AGREED_SYSTEM),
                Message(role="user", content=user),
            ],
            model=self.config.synthesizer, temperature=self.config.temperature,
            budget=self.config.synth_max_tokens, stage="synth",
        )
        return result, shown

    # -- telemetry ---------------------------------------------------------
    def _collect_usage(
        self,
        panel: list[PanelResponse],
        judge: CompletionResult | None,
        synth: CompletionResult | None,
    ) -> list[StageUsage]:
        usage: list[StageUsage] = [
            StageUsage(
                "panel", r.model, r.prompt_tokens, r.completion_tokens,
                r.cache_read_tokens, r.cache_write_tokens,
            )
            for r in panel
            if r.error is None
        ]
        if judge is not None:
            usage.append(_stage_usage("judge", judge))
        if synth is not None:
            usage.append(_stage_usage("synth", synth))
        return usage

    def _log_usage(self, trace: FusionTrace) -> None:
        by = trace.by_stage()

        def fmt(stage: str) -> str:
            p, c = by.get(stage, (0, 0))
            return f"{p}/{c}"

        _log.info(
            "fusion tokens (in/out) panel=%s judge=%s synth=%s total=%s",
            fmt("panel"),
            fmt("judge"),
            fmt("synth"),
            trace.total_tokens(),
        )
