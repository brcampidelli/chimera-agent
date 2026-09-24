"""The optional backend: OpenRouter's Decisions API, a typed-decision model behind it.

``POST https://openrouter.ai/api/alpha/decisions`` with ``{model, state, questions}`` — the same
request shape as the vendor's own endpoint, provider-normalized by OpenRouter, reached with the key
this project already holds. Not a chat-completions route: no messages, no temperature, no logprobs,
no streaming. Measured in `bench/jev_decisions/RESULTS.md` (arm J): deterministic (0 flips on
replay across five repetitions, median std 0.005), 0.34 s p50 from São Paulo, US$ 0.000023 a call,
5–8× fewer flips under permission framing than either model backend — and over-confident in the
middle of its scale on the governance corpus (p̄ 0.60 → 27% correct; Platt fixes it), yielding to
*pressure* framing on the benign side (15–18 of 31 benign actions refused under "production is
down"), and uncalibrated on a decision it was not trained on (aacr-bench, ECE 0.405 against a
floor of 0.035). So: the same map as the others, keyed the same way, and the kernel's rule that
strips unverifiable claims before asking applies to it too.

Pinned to a build (``typesafe/jev-1.13``, which resolved to ``jev-1.13-20260917`` on 09-19), never
the moving alias. Fails closed **per call**: a 4xx/5xx, a timeout, a malformed body all raise, the
Decider records the halt, and the run keeps its other layers — this backend is never the only
one. Nothing here retries; a decision surface that wants retries measures their latency first.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from chimera.decisions.contract import Noul, Question, Reading, as_choice

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "typesafe/jev-1.13"
DEFAULT_TIMEOUT_S = 30.0


class OpenRouterDecisionsBackend:
    name = "openrouter_decisions"

    def __init__(
        self, api_key: str, model: str = DEFAULT_MODEL, *, url: str = DECISIONS_URL,
        timeout_s: float = DEFAULT_TIMEOUT_S, client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("the Decisions backend needs an OpenRouter key")
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self.model = model
        self.url = url
        self.timeout_s = timeout_s
        # Kept for the backend's lifetime: a connection per call paid a TLS handshake per decision,
        # and the ecosystem's 167–196 ms medians (study 21) were all measured over a kept connection.
        self._client = client if client is not None else httpx.Client()

    @staticmethod
    def question_body(question: Question) -> dict[str, Any]:
        """The API's shape for each kind — a Score is asked as a Choice over its levels, because the
        API's ``score`` answer shape was not measured and a Choice's probabilities give the same
        expectation."""
        if isinstance(question, Noul):
            return {"type": "noul", "instructions": question.instructions, "criteria": dict(question.criteria)}
        choice = as_choice(question)
        body: dict[str, Any] = {"type": "choice", "instructions": choice.instructions}
        # The API wants a criterion per option. A question that carries none (the governance
        # question keeps its criteria inside the judge text, so the model backends' instrument
        # stays the measured one) sends the option's name as its own criterion — a placeholder,
        # and a reason a deployment that turns this backend on writes the question with criteria.
        body["criteria"] = {o: choice.criteria.get(o, o) for o in choice.options}
        return body

    def instrument(self, question: Question) -> str:
        return json.dumps(self.question_body(question), sort_keys=True)

    def body(self, state: str, question: Question) -> dict[str, Any]:
        key = question.key
        return {"model": self.model, "state": state, "questions": {key: self.question_body(question)}}

    def ask(self, state: str, question: Question) -> Reading:
        response = self._client.post(self.url, json=self.body(state, question), headers=self._headers, timeout=self.timeout_s)
        response.raise_for_status()
        return self.read(response.json(), question)

    def read(self, data: dict[str, Any], question: Question) -> Reading:
        """The reading, or ``ValueError`` — which the Decider records as a halt.

        Strict since study 24 (item 3). The first version repaired what it could not read: an option
        missing from ``probabilities`` became 0.0, a Noul outside [0, 1] was clamped, and a written
        choice that named no option fell back to the argmax. Each repair turned a malformed body into
        a confident number — keys spelled another way read as ``p = 0``, which the REVIEW band takes
        as "not dangerous", failing OPEN. A halt keeps the run's other layers in charge instead.
        """
        choice_q = as_choice(question)
        answers = data.get("answers")
        answer = answers.get(question.key) if isinstance(answers, dict) else None
        if not isinstance(answer, dict):
            raise ValueError(f"the response carries no answer for {question.key!r}")
        usage = data.get("usage") or {}
        usd = usage.get("cost")
        # The build that answered — `typesafe/jev-1.13-20260917` behind the alias on 09-19. The bench
        # stored it from the first run; the product did not until study 21 found every gateway hiding
        # it. Empty when the route sends none.
        resolved = str(data.get("model") or "").strip()
        raw = json.dumps(answer)[:200]
        if isinstance(question, Noul):
            yes = _probability(answer.get("noul"), f"{question.key}.noul")
            return Reading(
                choice="yes" if yes >= 0.5 else "no", shares={"yes": yes, "no": 1.0 - yes}, p=yes, usd=usd,
                raw=raw, resolved_model=resolved,
            )
        probs = answer.get("probabilities")
        if not isinstance(probs, dict) or set(probs) != set(choice_q.options):
            got = sorted(probs) if isinstance(probs, dict) else probs
            raise ValueError(f"{question.key}: probabilities {got!r} do not name exactly the options {list(choice_q.options)}")
        shares = {o: _probability(probs[o], f"{question.key}.{o}") for o in choice_q.options}
        total = sum(shares.values())
        if abs(total - 1.0) > SUM_TOLERANCE:
            raise ValueError(f"{question.key}: probabilities sum to {total:.4f}, not 1")
        shares = {o: v / total for o, v in shares.items()}
        written = answer.get("choice")
        choice = max(shares, key=lambda k: shares[k])
        if written is not None:
            named = next((o for o in choice_q.options if o.casefold() == str(written).strip().casefold()), None)
            if named is None:
                raise ValueError(f"{question.key}: choice {written!r} is not one of the options")
            choice = named
        p: float | None = None
        if choice_q.event:
            p = sum(shares[o] for o in choice_q.event)
        return Reading(choice=choice, shares=shares, p=p, usd=usd, raw=raw, resolved_model=resolved)


#: How far a Choice's probabilities may sum from 1 and still be read (then renormalized) — rounding to
#: two or three decimals across up to a dozen options, the band JevBench itself adopted (RENORM_TOL
#: 2e-2) after its v1 run showed models rounding. Outside it the body is malformed, and a halt.
SUM_TOLERANCE = 0.02


def _probability(value: Any, where: str) -> float:
    """A finite number in [0, 1], or ``ValueError`` — never clamped into one."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{where}: {value!r} is not a probability")
    number = float(value)
    if not 0.0 <= number <= 1.0:  # NaN fails both comparisons and lands here too
        raise ValueError(f"{where}: {number!r} is outside [0, 1]")
    return number
