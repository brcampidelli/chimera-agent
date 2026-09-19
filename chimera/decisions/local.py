"""The local backend: a small instruct model through Ollama, decision-first, read by logprobs.

The instrument `bench/jev_decisions/run.py` measured as arm L (RESULTS.md §7b), and the reasons each
piece is the way it is, all probed before an item was scored (§2ad):

* **Ollama's native ``/api/chat``**, not the OpenAI-compatible route — the native one takes ``think``
  and ``format`` together, and returns ``logprobs`` with ``top_logprobs`` (from 0.12.11).
* **``think: false``.** The decision token after a reasoning trace is extraction, not decision
  (arXiv 2601.13284): the same model with the trace on collapsed to a binary ``p`` and lost nine
  points of AUROC on the ambiguous slice (arm L2, 0.871 → 0.782). Decision-first is the reading.
* **``format`` = a JSON schema with the options as an enum.** Without it ``qwen3:4b`` writes
  "Okay, we are given…" first and the mass on the options at the first token is zero — no reading
  at all. With it the content is ten tokens and the label token's ``top_logprobs`` show a real
  distribution over the options with the non-option alternatives still listed, so ``mass`` stays
  informative.
* **The label token is found from the END.** The route's logprobs cover every generated token, so
  "the first token" is ``{``; the option sits at the last entry whose token is a prefix of the
  option the JSON carries. ``label_probabilities(..., position=)`` reads it renormalized.
* **``temperature: 0``, ``num_predict: 24``** — the answer is a ten-token object; a budget that
  small is what keeps a call at 0.75 s on an RTX 5070 and makes a runaway impossible.

``p`` is the sum of the event options' shares — ``BLOCK + REVIEW`` on the governance question — as
the bench read it. Raw, the model is saturated (Brier 0.268, bin 1.00 → 0.91); the shipped map is
what makes it a probability. A server that is off, or a model that is not pulled, raises — the
Decider records the halt and the caller keeps its other layers.

Two things study 21 added. **The build on the receipt**: a tag is whatever was last pulled, and
quantisation moved an open 4B's balanced accuracy by 2.4 points (SemIf, q4 against bf16), so the
backend asks ``/api/show`` once and names the answer ``<tag>@<quantization_level>`` — the string the
shipped map was fitted on, and the string the Decider compares before applying it. **One client for
the backend's lifetime**: the first version opened a connection per call, and the 0.75 s the bench
measured includes that handshake; the floor is re-read with the client kept.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx

from chimera.decisions.contract import (
    Choice,
    Question,
    Reading,
    as_choice,
    option_words,
    render_criteria,
)
from chimera.providers.decision import label_probabilities
from chimera.providers.ollama import _connect_budget

DEFAULT_MODEL = "qwen3:4b"
DEFAULT_TIMEOUT_S = 30.0
NUM_PREDICT = 24
TOP_LOGPROBS = 10


class LocalLogprobBackend:
    name = "local_logprob"

    def __init__(
        self,
        base_url: str,
        model: str = DEFAULT_MODEL,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self._client = client if client is not None else httpx.Client()
        self._resolved: str | None = None

    # -- the instrument -------------------------------------------------------------------------
    def system_text(self, question: Choice) -> str:
        return question.instructions + render_criteria(question)

    def user_suffix(self, question: Choice) -> str:
        return f'\n\nAnswer as JSON: {{"{question.key}": {option_words(question.options)}}}'

    def schema(self, question: Choice) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {question.key: {"type": "string", "enum": list(question.options)}},
            "required": [question.key],
        }

    def instrument(self, question: Question) -> str:
        question = as_choice(question)
        return self.system_text(question) + "\n---\n" + self.user_suffix(question) + "\n---\n" + json.dumps(self.schema(question), sort_keys=True)

    def body(self, state: str, question: Choice) -> dict[str, Any]:
        return {
            "model": self.model, "think": False, "stream": False, "logprobs": True, "top_logprobs": TOP_LOGPROBS,
            "format": self.schema(question), "options": {"temperature": 0, "num_predict": NUM_PREDICT},
            "messages": [
                {"role": "system", "content": self.system_text(question)},
                {"role": "user", "content": state + self.user_suffix(question)},
            ],
        }

    # -- the call -------------------------------------------------------------------------------
    def _budget(self) -> httpx.Timeout:
        return httpx.Timeout(self.timeout_s, connect=_connect_budget(self.base_url, self.timeout_s))

    def resolved_model(self) -> str:
        """``<tag>@<quantization_level>`` from ``/api/show``, asked once; ``""`` when the server does
        not say (an older Ollama, a model without details) — then no build check can be made, and the
        receipt shows the tag alone."""
        if self._resolved is None:
            try:
                response = self._client.post(f"{self.base_url}/api/show", json={"model": self.model}, timeout=self._budget())
                response.raise_for_status()
                details = (response.json() or {}).get("details") or {}
                quant = str(details.get("quantization_level") or "").strip()
                self._resolved = f"{self.model}@{quant}" if quant else ""
            except Exception:  # noqa: BLE001 — the build is a fact for the receipt, never a reason to fail the call
                self._resolved = ""
        return self._resolved

    def ask(self, state: str, question: Question) -> Reading:
        question = as_choice(question)
        response = self._client.post(f"{self.base_url}/api/chat", json=self.body(state, question), timeout=self._budget())
        response.raise_for_status()
        return self.read(response.json(), question, resolved_model=self.resolved_model())

    def read(self, data: dict[str, Any], question: Choice, *, resolved_model: str = "") -> Reading:
        """The reading off one response body — separable so the bench and the tests share it."""
        message = data.get("message") or {}
        content = str(message.get("content") or "")
        entries = data.get("logprobs") or message.get("logprobs") or []
        logprobs: list[dict[str, Any]] | None = [
            {
                "token": str(e.get("token", "")), "logprob": float(e.get("logprob", 0.0)),
                "top_logprobs": [
                    {"token": str(t.get("token", "")), "logprob": float(t.get("logprob", 0.0))}
                    for t in (e.get("top_logprobs") or [])
                ],
            }
            for e in entries
        ] or None
        choice: str | None = None
        try:
            written = str(json.loads(content).get(question.key) or "")
        except (ValueError, AttributeError):
            written = ""
        for option in question.options:
            if written.strip().casefold() == option.casefold():
                choice = option
                break
        idx: int | None = None
        if choice and logprobs:
            for i in range(len(logprobs) - 1, -1, -1):
                tok = str(logprobs[i]["token"]).strip().strip('"').casefold()
                if tok and choice.casefold().startswith(tok):
                    idx = i
                    break
        shares: dict[str, float] | None = None
        mass: float | None = None
        p: float | None = None
        if idx is not None:
            read = label_probabilities(SimpleNamespace(logprobs=logprobs), list(question.options), position=idx)
            if read is not None:
                shares, mass = read.shares, read.mass
                if question.event:
                    p = sum(shares.get(o, 0.0) for o in question.event)
        return Reading(
            choice=choice, shares=shares, p=p, mass=mass, usd=0.0, raw=content[:200], logprobs_came=bool(logprobs),
            resolved_model=resolved_model,
        )
