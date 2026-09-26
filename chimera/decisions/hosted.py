"""The hosted backend: the configured chat model asked to write its probability.

The instrument `bench/jev_decisions/run.py` measured as arm V (RESULTS.md §4–§6): the question's
framing, then the over-confidence advisory (Lindfors' recipe — "actively look for a reason you might
be wrong"), then an instruction to answer with one JSON object carrying ``p_<event>`` and the option.
On the ambiguous governance slice it ranked with the vendor (AUROC 0.886 against 0.903) and was the
best-calibrated arm raw (Brier 0.097, ECE 0.051, honest bins). It is the number worth having from a
hosted model that returns no logprobs.

**The model was reasoning when those numbers were taken.**
- The bench asked for ``thinking=False`` through ``LLMGateway.complete``. Until 2026-09-25 that method accepted the flag and dropped it (only ``stream_complete`` forwarded it), so every arm-V call ran with the model's default reasoning.
- Hence the empty answers: 155/394 at a budget of 400 and still 79/394 at 600 were reasoning spending the budget, on every route. They were not routes ignoring the switch.

So this backend asks for exactly what was measured:
- the model's own reasoning default (no ``thinking`` argument);
- a **wide budget** of 2000 tokens;
- **one re-ask on an empty answer**.

A reasoning-off call is a different instrument, and it is unmeasured: switching to it needs a run of its own before any number above applies to it. The count of answers that still came back without a number is on the receipt of each call (``p`` None, ``raw`` empty), never folded into a verdict.

**One output instruction (arm V′, 2026-09-25).** Arm V's text kept the judge's "Reply with
exactly one word" before asking for JSON. This backend now sends the question's
:meth:`~chimera.decisions.contract.Choice.framing`, without that sentence. Measured against a fresh
V in `bench/jev_decisions/RESULTS-one-schema.md` (55 items × 2, pinned, reasoning at default):
- unparsed answers 0/110 in both arms;
- ΔAUROC −0.015 [−0.046, +0.009], non-inferior at the registered 0.05 margin;
- Brier 0.072 → 0.078 and ECE 0.059 → 0.037, both inside the floor.

The numbers above are V's.

Temperature 0.3 because that is what was measured (the judge's own setting); a caller that wants a
deterministic decision passes 0 and measures again. Shares are not available on this backend: the
model writes one number and one word, and the word is the choice.

**An answer the route filed as reasoning.** Some routes return a reasoning model's whole reply as
reasoning, with ``content`` empty and ``finish_reason`` "stop" (``deepseek-r1`` on Novita, 37–43% of
calls in `bench/review_judge/RESULTS-h11.md`; the gateway flags it as
``CompletionResult.answer_in_reasoning``). With ``answer_from_reasoning`` on
(``CHIMERA_ANSWER_FROM_REASONING``), such a reply is read from the object its reasoning ENDS with,
when that object carries this question's keys, and the call is not repeated; the reading says
``answer_from="reasoning"``. Anything else, including an object followed by more reasoning, falls
through to the re-ask. Off (the default), the re-ask happens as before, and a reading whose last
reply was still filed that way says ``answer_from="reasoning_unread"`` rather than looking like a
model that answered nothing.
"""

from __future__ import annotations

import json
import re
from typing import Any

from chimera.decisions.contract import (
    Choice,
    Question,
    Reading,
    as_choice,
    option_words,
    render_criteria,
)

ADVISORY = (
    "\n\nBefore assigning a high probability, actively look for a reason you might be wrong; you have "
    "been over-confident in past evaluations. Reply with ONLY a JSON object on one line: "
)
DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 2000
_JSON = re.compile(r"\{.*?\}", re.S)


class HostedVerbalizedBackend:
    name = "hosted_verbalized"

    def __init__(
        self, gateway: Any, model: str, *, temperature: float = DEFAULT_TEMPERATURE, max_tokens: int = DEFAULT_MAX_TOKENS,
        answer_from_reasoning: bool = False,
    ) -> None:
        self.gateway = gateway
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.answer_from_reasoning = answer_from_reasoning

    def system_text(self, question: Choice) -> str:
        spec = f'{{"{question.key}": {option_words(question.options)}}}'
        if question.event:
            spec = f'{{"p_{question.p_name}": <number between 0 and 1>, "{question.key}": {option_words(question.options)}}}'
        # The framing, not the instructions: this backend's JSON is the one output instruction.
        return question.framing() + render_criteria(question) + ADVISORY + spec

    def instrument(self, question: Question) -> str:
        question = as_choice(question)
        return self.system_text(question) + f"\n---\ntemperature={self.temperature}"

    def ask(self, state: str, question: Question) -> Reading:
        question = as_choice(question)
        from chimera.orchestration.receipts import price_completion
        from chimera.providers.thinking import answer_at_end_of_reasoning

        usd = 0.0
        text = ""
        resolved = ""
        answer_from = ""
        for _attempt in range(2):
            result = self.gateway.complete(
                [{"role": "system", "content": self.system_text(question)}, {"role": "user", "content": state}],
                # No `thinking`: the measured instrument ran with the model's default (see the module
                # docstring), and asking for reasoning off now that the gateway forwards it would
                # quietly swap in an instrument nobody has measured.
                model=self.model, temperature=self.temperature, max_tokens=self.max_tokens,
            )
            cost = price_completion(result)
            if not cost.unpriced:
                usd += cost.usd
            text = result.content or ""
            resolved = str(getattr(result, "model", "") or "")
            answer_from = ""
            if text.strip():
                break
            if getattr(result, "answer_in_reasoning", False):
                # Read back before re-asking, never after choosing: a re-ask until `content` is
                # non-empty would select answers by the form the route gave them.
                if self.answer_from_reasoning:
                    thought = str(getattr(result, "reasoning", "") or "")
                    recovered = answer_at_end_of_reasoning(thought, self.answer_keys(question))
                    if recovered:
                        text, answer_from = recovered, "reasoning"
                        break
                answer_from = "reasoning_unread"
        return self.read(text, question, usd=usd, resolved_model=resolved, answer_from=answer_from)

    @staticmethod
    def answer_keys(question: Choice) -> tuple[str, ...]:
        """The keys this backend's JSON must carry: the option, and the probability when asked."""
        if question.event:
            return (question.key, f"p_{question.p_name}")
        return (question.key,)

    def read(
        self, text: str, question: Choice, *, usd: float | None = None, resolved_model: str = "",
        answer_from: str = "",
    ) -> Reading:
        p: float | None = None
        choice: str | None = None
        written = ""
        m = _JSON.search(text)
        if m:
            try:
                obj = json.loads(m.group(0))
                if question.event and obj.get(f"p_{question.p_name}") is not None:
                    p = min(max(float(obj[f"p_{question.p_name}"]), 0.0), 1.0)
                written = str(obj.get(question.key) or "")
            except (ValueError, TypeError, AttributeError):
                p = None
        for option in question.options:
            if written.strip().casefold() == option.casefold():
                choice = option
                break
        if choice is None:
            # The word, wherever it was written — the bench's fallback when the object did not parse.
            pattern = re.compile(r"\b(" + "|".join(re.escape(o) for o in question.options) + r")\b", re.I)
            found = pattern.findall(text)
            if found:
                last = found[-1].casefold()
                choice = next((o for o in question.options if o.casefold() == last), None)
        return Reading(
            choice=choice, shares=None, p=p, usd=usd, raw=text[:200], logprobs_came=None,
            resolved_model=resolved_model, answer_from=answer_from,
        )
