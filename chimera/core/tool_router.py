"""A "System One" tool router: a cheap model picks the tool NAME, the strong model fills the arguments.

Study 20 §3 B4, pre-registered in `bench/tool_router/PREREGISTRATION.md`. The hypothesis is not ours
— it is the one every "fast/slow" agent video states — and it is stated precisely enough to measure:
a small model, reading almost nothing, decides *which* tool; the expensive model is then asked to
produce arguments for that one tool. The claim is fewer steps and less money at the same pass rate.

Two things make it measurable rather than plausible:

* **The router reads a SHALLOW context on purpose.** The task, the last observation (truncated), the
  tool names with one line each. Not the conversation — a router that re-reads the whole transcript
  costs what the call it is replacing costs, and the hypothesis is about a fast, shallow decision.
  This is the design choice the number is about; a deep router is a different experiment.
* **It narrows, it does not answer.** The executor still writes the call, and when the router names
  a tool that does not exist, or nothing at all, the step runs with the full tool list — recorded as
  a fallback rather than hidden, so "the router acted" is a number and not an assumption (§2r).

Off unless asked (`--tool-router MODEL`). It is a *cost* experiment: nothing about it is free, and
the router's own spend is charged to the run like any other call, or the comparison would price one
arm and not the other.

**Two modes.** ``narrow`` is the B4 router, measured and not recommended (`bench/tool_router`: −0.087 /
−0.194 / −0.307 oracle score). It broke both halves of study 22's direction rule at once — it removed
tools and it could end the loop with ``ANSWER`` — and B4 could not tell which half did the damage.
``hint`` (B4b, `bench/tool_router_hint`) keeps only what the rule allows: the router may *suggest* a
tool, the executor keeps every tool and the decision whether to act, there is no ``ANSWER``, and the
router reads the tools already used this run, so "one step from done" is not read off one output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from chimera.telemetry import get_logger

_log = get_logger("core.tool_router")

ANSWER = "ANSWER"
"""The router's word for "no tool — answer the user". It narrows the step to no tools at all."""

MODES = ("narrow", "hint")
_HISTORY_TOOLS = 12

_OBSERVATION_CHARS = 1200
_TASK_CHARS = 1200


@dataclass
class RouterStats:
    """What the router did, for the receipt. An intervention that cannot say how much it acted
    reads as "it did not help" when the truth is "it never fired" (§2r)."""

    calls: int = 0
    narrowed: int = 0
    """Steps where the executor was given exactly one tool."""
    answered: int = 0
    """Steps where the router said ANSWER and the executor was given none."""
    fallbacks: int = 0
    """Steps where the router's word matched no tool, or the call failed."""
    hinted: int = 0
    """``hint`` mode: steps where a suggestion was put in front of the executor."""
    followed: int = 0
    """``hint`` mode: hinted steps whose first tool call was the suggested tool."""
    usd: float = 0.0
    picks: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "narrowed": self.narrowed,
            "answered": self.answered,
            "hinted": self.hinted,
            "followed": self.followed,
            "fallbacks": self.fallbacks,
            "usd": round(self.usd, 6),
            "picks": dict(sorted(self.picks.items(), key=lambda kv: -kv[1])),
        }


class ToolRouter:
    """Asks a cheap model which tool the next step should use."""

    def __init__(self, backend: Any, model: str, *, temperature: float = 0.0, mode: str = "narrow") -> None:
        if mode not in MODES:
            raise ValueError(f"router mode must be one of {MODES}, got {mode!r}")
        self.backend = backend
        self.model = model
        self.temperature = temperature
        self.mode = mode
        self.stats = RouterStats()

    # --- the prompt -------------------------------------------------------------------------

    def _menu(self, schemas: list[dict[str, Any]]) -> list[tuple[str, str]]:
        menu: list[tuple[str, str]] = []
        for schema in schemas:
            fn = schema.get("function") or schema
            name = str(fn.get("name") or "")
            if not name:
                continue
            line = str(fn.get("description") or "").strip().splitlines()
            menu.append((name, line[0][:120] if line else ""))
        return menu

    def _last_observation(self, messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            if message.get("role") in ("tool", "assistant") and message.get("content"):
                text = str(message["content"])
                return text[-_OBSERVATION_CHARS:]
        return ""

    def _history(self, messages: list[dict[str, Any]]) -> list[str]:
        """The tool names called so far this run, oldest first — the progress a shallow reader lacked."""
        names: list[str] = []
        for message in messages:
            for call in message.get("tool_calls") or []:
                # The loop stores OpenAI-shaped dicts ({"function": {"name": ...}}); a gateway
                # `ToolCall` carries `.name` itself. Both are read, nothing else is guessed.
                if isinstance(call, dict):
                    fn = call.get("function")
                    name = fn.get("name") if isinstance(fn, dict) else call.get("name")
                else:
                    name = getattr(call, "name", None)
                if name:
                    names.append(str(name))
        return names[-_HISTORY_TOOLS:]

    def _prompt(self, task: str, messages: list[dict[str, Any]], menu: list[tuple[str, str]]) -> list[dict[str, str]]:
        tools = "\n".join(f"- {name}: {line}" for name, line in menu)
        last = self._last_observation(messages)
        if self.mode == "hint":
            history = ", ".join(self._history(messages)) or "(none yet)"
            return [
                {
                    "role": "system",
                    "content": (
                        "You advise one step of a coding agent. Given the task, the tools it has used so "
                        "far and what just happened, name the ONE tool the next step would most likely "
                        "need. The agent decides for itself whether to use it. Reply with exactly one "
                        "word: a tool name from the list."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Task:\n{task[:_TASK_CHARS]}\n\nTools used so far: {history}\n\n"
                        f"Last output:\n{last or '(nothing yet — this is the first step)'}\n\n"
                        f"Tools:\n{tools}\n\nOne word:"
                    ),
                },
            ]
        return [
            {
                "role": "system",
                "content": (
                    "You route one step of a coding agent. Given the task and what just happened, "
                    "name the ONE tool the next step should use — or "
                    f"{ANSWER} if the task is done and the agent should reply to the user. "
                    "Reply with exactly one word: a tool name from the list, or " + ANSWER + "."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Task:\n{task[:_TASK_CHARS]}\n\n"
                    f"Last output:\n{last or '(nothing yet — this is the first step)'}\n\n"
                    f"Tools:\n{tools}\n\nOne word:"
                ),
            },
        ]

    # --- the decision -----------------------------------------------------------------------

    def pick(
        self,
        task: str,
        messages: list[dict[str, Any]],
        schemas: list[dict[str, Any]],
        *,
        usage: Any = None,
        spend: Any = None,
    ) -> str | None:
        """The tool name to narrow this step to, :data:`ANSWER`, or ``None`` for "do not narrow".

        ``None`` is the honest outcome of a router that could not decide: the step then runs with
        every tool, exactly as it would without a router. A failure here must never fail the run —
        the router is an optimisation, and an optimisation that can break the loop is a liability.
        """
        menu = self._menu(schemas)
        if not menu:
            return None
        names = {name for name, _ in menu}
        self.stats.calls += 1
        try:
            result = self.backend.complete(
                self._prompt(task, messages, menu), model=self.model, temperature=self.temperature
            )
        except Exception as exc:  # noqa: BLE001 - an optimisation may not take the run down
            _log.debug("tool router call failed: %s", exc)
            self.stats.fallbacks += 1
            return None
        if usage is not None:
            usage.add(result)
        if spend is not None:
            spend.record_result(result)
        # Priced the way every other call in this repository is priced. `CompletionResult` carries
        # TOKENS, not money — `result.usd` is not a field, so reading it would have made the
        # router's cost read 0.00 on every row while it really spent, and the arm this experiment
        # exists to compare would have been the cheap one by construction.
        from chimera.orchestration.receipts import price_completion

        self.stats.usd += float(price_completion(result).usd or 0.0)
        word = self._word(str(getattr(result, "content", "") or ""), names)
        if word is None:
            self.stats.fallbacks += 1
            return None
        self.stats.picks[word] = self.stats.picks.get(word, 0) + 1
        if self.mode == "hint":
            if word == ANSWER:  # not offered in this mode; a model that says it anyway is ignored
                self.stats.fallbacks += 1
                return None
            self.stats.hinted += 1
            return word
        if word == ANSWER:
            self.stats.answered += 1
        else:
            self.stats.narrowed += 1
        return word

    def record_follow(self, suggested: str, first_call: str | None) -> None:
        """``hint`` mode: whether the executor's first call this step was the suggested tool."""
        if first_call is not None and first_call == suggested:
            self.stats.followed += 1

    def _word(self, content: str, names: set[str]) -> str | None:
        """The tool name in the reply, or None.

        Read by containment rather than by equality: a small model asked for one word answers
        `read_file`, `"read_file"`, `Use read_file.` and `read_file — to see the module` about
        equally often, and a parser that accepts only the first of those turns a correct decision
        into a fallback. The longest matching name wins, so `read_file` is not read out of
        `read_file_lines` when both exist.
        """
        text = content.strip()
        if not text:
            return None
        lowered = text.lower()
        if ANSWER.lower() in lowered:
            # Only when no tool name is also present: "ANSWER" inside a sentence that names a tool
            # is the model explaining itself, and the tool is the decision.
            named = [n for n in names if n.lower() in lowered]
            if not named:
                return ANSWER
        candidates = [n for n in names if n.lower() in lowered]
        if not candidates:
            return None
        return max(candidates, key=len)


def hint_message(name: str) -> dict[str, str]:
    """The suggestion, as a message put in front of ONE step and not kept in the history."""
    return {
        "role": "system",
        "content": (
            f"Hint from a fast router: the next step may need `{name}`. It is only a suggestion — use "
            "any tool, or none, as the task requires."
        ),
    }


def narrow(schemas: list[dict[str, Any]], name: str) -> list[dict[str, Any]] | None:
    """The schema list narrowed to one tool — or ``None`` when the name is :data:`ANSWER`."""
    if name == ANSWER:
        return None
    kept = [s for s in schemas if str(((s.get("function") or s).get("name")) or "") == name]
    return kept or schemas
