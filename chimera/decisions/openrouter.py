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
        self._client = client

    @staticmethod
    def question_body(question: Question) -> dict[str, Any]:
        """The API's shape for each kind — a Score is asked as a Choice over its levels, because the
        API's ``score`` answer shape was not measured and a Choice's probabilities give the same
        expectation."""
        if isinstance(question, Noul):
            return {"type": "noul", "instructions": question.instructions, "criteria": dict(question.criteria)}
        choice = as_choice(question)
        body: dict[str, Any] = {"type": "choice", "instructions": choice.instructions}
        body["criteria"] = {o: choice.criteria.get(o, o) for o in choice.options}
        return body

    def instrument(self, question: Question) -> str:
        return json.dumps(self.question_body(question), sort_keys=True)

    def body(self, state: str, question: Question) -> dict[str, Any]:
        key = question.key
        return {"model": self.model, "state": state, "questions": {key: self.question_body(question)}}

    def ask(self, state: str, question: Question) -> Reading:
        client = self._client or httpx.Client()
        try:
            response = client.post(self.url, json=self.body(state, question), headers=self._headers, timeout=self.timeout_s)
            response.raise_for_status()
            data = response.json()
        finally:
            if self._client is None:
                client.close()
        return self.read(data, question)

    def read(self, data: dict[str, Any], question: Question) -> Reading:
        choice_q = as_choice(question)
        answers = data.get("answers") or {}
        answer = answers.get(question.key) or {}
        usage = data.get("usage") or {}
        usd = usage.get("cost")
        if "noul" in answer:
            yes = min(max(float(answer["noul"]), 0.0), 1.0)
            return Reading(choice="yes" if yes >= 0.5 else "no", shares={"yes": yes, "no": 1.0 - yes}, p=yes, usd=usd, raw=json.dumps(answer)[:200])
        probs = answer.get("probabilities") or {}
        shares: dict[str, float] | None = {o: float(probs.get(o, 0.0)) for o in choice_q.options} if probs else None
        written = str(answer.get("choice") or "")
        choice = next((o for o in choice_q.options if o.casefold() == written.strip().casefold()), None)
        if choice is None and shares:
            choice = max(shares, key=lambda k: shares[k])
        p: float | None = None
        if shares is not None and choice_q.event:
            p = min(max(sum(shares.get(o, 0.0) for o in choice_q.event), 0.0), 1.0)
        return Reading(choice=choice, shares=shares, p=p, usd=usd, raw=json.dumps(answer)[:200])
