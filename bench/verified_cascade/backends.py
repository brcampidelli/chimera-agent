"""The backends of the verified-cascade harness: the live ones (paid, networked) and the fake one.

``LiveBackends`` reuses the product's own seams, as §4.1 registers: the gateway (``LLMGateway``) for
luna, Sol and the graders, each pinned to its provider with fallbacks off; ``chimera.decisions`` for
the verifiers — ``OpenRouterDecisionsBackend`` (Jev) and ``LocalLogprobBackend`` (qwen3:4b on Ollama)
behind a ``Decider`` with **no map** (both are read raw, §4.1). The key is read from the settings or
``OPENROUTER_API_KEY`` and is never printed or logged; a request body is logged without it.

``FakeBackends`` answers every call from a sha256 of its input, with no network: it exists so
``run.py --dry-run`` exercises the whole pipeline — stages, gates, the ledger, resumption and the
report — end to end, and so the tests can.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

from bench.verified_cascade.common import number_check_fires, sha
from bench.verified_cascade.harness import (
    JEV,
    LOCAL,
    MAX_TOKENS,
    OPTIONS,
    PINS,
    decision_question,
    parse_label,
)


class Backends(Protocol):
    def draft(self, model: str, messages: list[dict[str, str]]) -> dict[str, Any]: ...

    def decide(self, verifier: str, state: str) -> dict[str, Any]: ...

    def grade(self, model: str, messages: list[dict[str, str]]) -> dict[str, Any]: ...


def request_spec(model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    """What a drafting or grading request carries, minus the key — logged, and checked by the wall."""
    return {
        "model": model, "messages": messages, "max_tokens": MAX_TOKENS.get(model, 1000),
        "extra_body": {"provider": {"order": [PINS.get(model, "")], "allow_fallbacks": False}, "usage": {"include": True}},
    }


class LiveBackends:
    """Paid and networked. Constructed only by ``run.py`` without ``--dry-run``."""

    def __init__(self, gateway: Any | None = None) -> None:
        from chimera.config import get_settings
        from chimera.decisions.contract import Decider
        from chimera.decisions.local import LocalLogprobBackend
        from chimera.decisions.openrouter import OpenRouterDecisionsBackend

        settings = get_settings()
        if gateway is None:
            from chimera.providers import LLMGateway

            gateway = LLMGateway(settings)
        self.gateway = gateway
        key = getattr(settings, "openrouter_api_key", None) or os.environ.get("OPENROUTER_API_KEY", "")
        self.deciders = {
            "jev": Decider(OpenRouterDecisionsBackend(str(key), JEV)),
            "local": Decider(LocalLogprobBackend(settings.ollama_base_url, LOCAL)),
        }
        self.question = decision_question()

    def _complete(self, model: str, messages: list[dict[str, str]], *, temperature: float | None) -> dict[str, Any]:
        from chimera.orchestration.receipts import price_completion

        spec = request_spec(model, messages)
        kwargs: dict[str, Any] = {"model": model, "max_tokens": spec["max_tokens"], "extra_body": spec["extra_body"]}
        if temperature is not None:
            kwargs["temperature"] = temperature
        payload: list[Any] = list(messages)
        result = self.gateway.complete(payload, **kwargs)
        cost = price_completion(result)
        billed = getattr(result, "usd", None)
        return {
            "text": result.content or "", "provider": result.provider, "served_model": result.model,
            "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens,
            "cache_read_tokens": result.cache_read_tokens, "finish_reason": result.finish_reason,
            "usd_computed": cost.usd, "usd_billed": billed,
            "usd": max(float(cost.usd or 0.0), float(billed or 0.0)), "unpriced": cost.unpriced,
        }

    def draft(self, model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        # The drafting models take no temperature (§4.1): the gateway's default is sent and the
        # route ignores it, so they sample, which is what gives d1 and d2 their floor.
        return self._complete(model, messages, temperature=None)

    def grade(self, model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        out = self._complete(model, messages, temperature=0.0)
        out["label"] = parse_label(out["text"])
        return out

    def decide(self, verifier: str, state: str) -> dict[str, Any]:
        answer = self.deciders[verifier].decide("bench.verified_cascade.grounded_answer", state, self.question)
        if answer.halt:
            raise RuntimeError(answer.halt)
        return {
            "choice": answer.choice, "p": answer.raw_p, "shares": answer.shares, "mass": answer.mass,
            "usd": float(answer.usd or 0.0), "resolved_model": answer.resolved_model,
            "seconds": round(answer.seconds, 3), "prompt_hash": answer.prompt_hash,
        }


class FakeBackends:
    """Deterministic, offline. A draft is a decline, a sentence lifted from an excerpt, or a sentence
    with an invented number; a reading and a grade are drawn from the input's hash. Every call is
    priced at the registered per-call estimate, so the ledger and the admission stop are exercised."""

    PRICE = {"luna": 0.0005, "sol": 0.013, "jev": 0.00006, "grade": 0.0003}

    def __init__(self, *, fail_every: int = 0) -> None:
        self.fail_every = fail_every
        self.calls = 0

    def _u(self, *parts: str) -> float:
        return int(sha("|".join(parts))[:12], 16) / float(16**12)

    def _maybe_fail(self) -> None:
        self.calls += 1
        if self.fail_every and self.calls % self.fail_every == 0:
            raise RuntimeError("fake transport error")

    def draft(self, model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        self._maybe_fail()
        user = messages[-1]["content"]
        u = self._u(model, user, str(self.calls))
        lines = [ln for ln in user.splitlines() if len(ln.split()) > 6 and not ln.startswith("Question:")]
        if u < 0.68:
            text = "The provided excerpts do not cover this question."
        elif u < 0.95 and lines:
            text = lines[int(u * 1000) % len(lines)].strip()
        else:
            text = f"It uses {int(u * 9000) + 1000} workers by default."
        price = self.PRICE["sol" if "sol" in model else "luna"]
        return {"text": text, "provider": PINS.get(model, ""), "served_model": model, "prompt_tokens": len(user) // 4,
                "completion_tokens": len(text) // 4, "cache_read_tokens": 0, "finish_reason": "stop",
                "usd_computed": price, "usd_billed": None, "usd": price, "unpriced": None}

    def decide(self, verifier: str, state: str) -> dict[str, Any]:
        self._maybe_fail()
        u = self._u(verifier, state)
        data = json.loads(state)
        answer, excerpts = data["answer"], data["excerpts"]
        if _declines(answer):
            shares = {"supported": 0.04, "unsupported": 0.06, "declined": 0.9}
        elif number_check_fires(answer, excerpts, data["question"]):
            shares = {"supported": 0.1, "unsupported": 0.85, "declined": 0.05}
        elif any(answer[:60] in e for e in excerpts) or _overlap(answer, excerpts) >= 0.75:
            shares = {"supported": 0.92, "unsupported": 0.06, "declined": 0.02}
        else:
            s = 0.3 + 0.65 * u
            shares = {"supported": s, "unsupported": (1 - s) * 0.9, "declined": (1 - s) * 0.1}
        choice = max(OPTIONS, key=lambda o: shares[o])
        return {"choice": choice, "p": shares["supported"], "shares": shares, "mass": 0.99 if verifier == "local" else None,
                "usd": self.PRICE["jev"] if verifier == "jev" else 0.0,
                "resolved_model": "jev-1.13-fake" if verifier == "jev" else "qwen3:4b@fake", "seconds": 0.01,
                "prompt_hash": "fake"}

    def grade(self, model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        self._maybe_fail()
        user = messages[-1]["content"]
        answer = user.rsplit("Answer to grade:\n", 1)[-1].strip()
        answerable = "The excerpts answer this question." in user
        ref = user.split("Reference answer: ", 1)[-1].split("\n", 1)[0].strip() if answerable else ""
        keys = user.split("Key facts: ", 1)[-1].split("\n", 1)[0].split("; ") if answerable else []
        if _declines(answer):
            label = "declined" if answerable else "correct"
        elif not answerable or "workers by default" in answer:
            label = "wrong"
        elif answer == ref or (all(k.casefold() in answer.casefold() for k in keys) and len(answer) <= 1.1 * len(ref)):
            label = "correct"
        else:
            label = "wrong"
        if self._u(model, user) < 0.05:  # the graders disagree now and then
            label = "incomplete" if label != "incomplete" else "wrong"
        text = json.dumps({"label": label})
        return {"text": text, "label": parse_label(text), "provider": PINS.get(model, ""), "served_model": model,
                "prompt_tokens": len(user) // 4, "completion_tokens": 5, "cache_read_tokens": 0, "finish_reason": "stop",
                "usd_computed": self.PRICE["grade"], "usd_billed": None, "usd": self.PRICE["grade"], "unpriced": None}


def _declines(answer: str) -> bool:
    low = answer.casefold()
    return any(m in low for m in ("do not cover", "don't say", "can't answer", "não cobrem", "não dizem", "não consigo responder"))


def _overlap(answer: str, excerpts: list[str]) -> float:
    words = {w.casefold() for w in answer.split() if len(w) > 3}
    if not words:
        return 0.0
    pool = " ".join(excerpts).casefold()
    return sum(1 for w in words if w in pool) / len(words)
