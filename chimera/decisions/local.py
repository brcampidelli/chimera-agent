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

#: The context window every call asks for. Ollama's default is set per machine (4,096 here on
#: 2026-09-29) and it keeps only HALF of num_ctx for the prompt, cutting the rest SILENTLY: a
#: 30,314-token prompt came back as prompt_eval_count 2,050 under the default and 8,194 under 16,384.
#: Prose runs ~5.5 characters a token, so the default read at most ~11,000 characters, under the
#: verified-answers cap of 14,000; 16,384 reads up to 8,192 tokens. Every bench of this backend
#: already sent it (jevbench_local A_ctx, spot_noul, stop_gate).
NUM_CTX = 16384
PROMPT_BUDGET = NUM_CTX // 2  # what Ollama actually reads of a prompt (measured, see above)


class ContextOverflow(RuntimeError):
    """The state filled the context window: whatever the model answered was about a truncated state.
    Raised, so the Decider records a halt — never a reading of text the model did not see."""



def _ambiguous_prefix(token: str, options: tuple[str, ...] | list[str]) -> bool:
    """Whether the label token is a non-empty prefix of MORE THAN ONE option (after the same
    normalisation `label_probabilities` applies) — the first-token collision study 21 A4 named."""
    t = token.strip().strip("\"'`*_").casefold()
    if not t:
        return False
    return sum(1 for option in options if option.casefold().startswith(t)) > 1


class LocalLogprobBackend:
    name = "local_logprob"

    def __init__(
        self,
        base_url: str,
        model: str = DEFAULT_MODEL,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        client: httpx.Client | None = None,
        render_mode: str = "default",
        rotation: int = 0,
    ) -> None:
        if render_mode not in {"default", "numeric", "letters", "swap"}:
            raise ValueError("render_mode must be 'default', 'numeric', 'letters', or 'swap'")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.render_mode = render_mode
        self.rotation = rotation
        self._client = client if client is not None else httpx.Client()
        self._resolved: str | None = None

    def _rendered(self, question: Choice) -> tuple[Choice, dict[str, str]]:
        if self.render_mode == "default":
            return question, {option: option for option in question.options}
        if self.render_mode == "numeric":
            # Ids 1..9 are single digits and none is a prefix of another. From ten options on, any
            # spelling shares a first digit ("01".."09", or "1" and "10"), the label token is
            # ambiguous, and every reading would come back unread: refuse rather than measure that.
            if len(question.options) > 9:
                raise ValueError("numeric rendering supports at most 9 options (prefix-free single digits)")
            labels = tuple(str(i) for i in range(1, len(question.options) + 1))
        else:
            labels = tuple(chr(ord("A") + i) for i in range(len(question.options)))
        if not 0 <= self.rotation < len(question.options):
            raise ValueError("rotation must be between 0 and the number of options minus one")
        if self.render_mode == "swap":
            # The registered label swap: the first two LABELS trade places while every definition
            # stays where it was. Moving the options instead (the first draft) is a position change,
            # identical to rotation 1 on a binary question, and cannot separate label from order.
            if self.rotation:
                raise ValueError("swap rendering takes no rotation")
            swapped = list(labels)
            swapped[0], swapped[1] = swapped[1], swapped[0]
            labels = tuple(swapped)
            rotated = question.options
        else:
            rotated = question.options[self.rotation :] + question.options[: self.rotation]
        mapping = dict(zip(labels, rotated, strict=True))
        criteria = {label: question.criteria.get(option, option) for label, option in mapping.items()}
        rendered = Choice(
            key=question.key,
            instructions=question.instructions,
            options=labels,
            criteria=criteria,
            event=tuple(label for label, option in mapping.items() if option in question.event),
            event_name=question.event_name,
        )
        return rendered, mapping

    # -- the instrument -------------------------------------------------------------------------
    def system_text(self, question: Choice) -> str:
        rendered, _ = self._rendered(question)
        return rendered.instructions + render_criteria(rendered)

    def user_suffix(self, question: Choice) -> str:
        rendered, _ = self._rendered(question)
        if self.render_mode == "numeric":
            return "\n\nChoose the best option by its numeric id. Reply only with the number in brackets."
        return f'\n\nAnswer as JSON: {{"{rendered.key}": {option_words(rendered.options)}}}'

    def schema(self, question: Choice) -> dict[str, Any]:
        rendered, _ = self._rendered(question)
        return {
            "type": "object",
            "properties": {rendered.key: {"type": "string", "enum": list(rendered.options)}},
            "required": [rendered.key],
        }

    def instrument(self, question: Question) -> str:
        question = as_choice(question)
        if self.render_mode == "numeric":
            return self.system_text(question) + "\n---\n" + self.user_suffix(question) + "\n---\nBest answer: ["
        return self.system_text(question) + "\n---\n" + self.user_suffix(question) + "\n---\n" + json.dumps(self.schema(question), sort_keys=True)

    def body(self, state: str, question: Choice) -> dict[str, Any]:
        """The request. ``state`` goes in as given: it is NOT passed through
        :func:`chimera.governance.sanitize.sanitize_untrusted`, so a chat-template token inside it
        (``<|im_start|>``, ``</think>``, DeepSeek's ``<｜User｜>``) reaches the model as written, and
        Qwen tokenizers stayed 84.7% forgeable even with the split-special-tokens flag on (arXiv
        2609.16984); the local default is qwen3:4b. Left as it is on purpose
        for now: the shipped map was fitted on raw state, so sanitising would change the instrument
        it calibrates. It matters wherever a caller feeds text it did not write, and there are three:
        the REVIEW band (off by default), which sends an action the agent wrote; the agent-callable
        ``decide`` tool (``CHIMERA_DECIDE_TOOL``, off by default), whose purpose is to classify text
        the agent already holds, fetched pages included; and verified answers
        (:func:`chimera.fusion.verified.grounded_state`, ON by default), whose ``answer`` field is
        model output and goes in unsanitised — its excerpts come from attachments, which were
        sanitised when they were saved. Open, needs measurement (study 30, S30-21(d)): each caller's
        numbers with sanitised state, before sanitising is switched on or the band is.
        """
        messages = [
            {"role": "system", "content": self.system_text(question)},
            {"role": "user", "content": state + self.user_suffix(question)},
        ]
        if self.render_mode == "numeric":
            messages.append({"role": "assistant", "content": "Best answer: ["})
        # Key order kept as it was before render modes existed, so the default request serialises to
        # the same bytes; numeric drops ``format`` because the prefill is its constraint.
        body: dict[str, Any] = {
            "model": self.model, "think": False, "stream": False, "logprobs": True, "top_logprobs": TOP_LOGPROBS,
        }
        if self.render_mode != "numeric":
            body["format"] = self.schema(question)
        body["options"] = {"temperature": 0, "num_predict": NUM_PREDICT, "num_ctx": NUM_CTX}
        body["messages"] = messages
        return body

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
        data = response.json()
        used = int(data.get("prompt_eval_count") or 0)
        if used >= PROMPT_BUDGET:
            raise ContextOverflow(
                f"the state filled the {PROMPT_BUDGET}-token prompt budget of a {NUM_CTX} context "
                f"({used} tokens read)"
            )
        return self.read(data, question, resolved_model=self.resolved_model())

    def read(self, data: dict[str, Any], question: Choice, *, resolved_model: str = "") -> Reading:
        """The reading off one response body — separable so the bench and the tests share it."""
        original = question
        question, mapping = self._rendered(question)
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
        if self.render_mode == "numeric":
            import re
            match = re.search(r"\b([1-9])\b", content)
            written = match.group(1) if match else ""
        else:
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
        if idx is not None and _ambiguous_prefix(str(logprobs[idx]["token"]), question.options):  # type: ignore[index]
            # Study 21 A4. The label token located is a prefix of more than one option (`RE` for
            # both `REVIEW` and `REFUSE`): `label_probabilities` refuses to assign it, so the
            # reading would be built from whatever residual mass the OTHER candidates carried —
            # a number about the wrong token. No read is the honest outcome.
            idx = None
        if idx is not None:
            read = label_probabilities(SimpleNamespace(logprobs=logprobs), list(question.options), position=idx)
            # Zero mass on the labels is NO signal, not a signal of zero. `label_probabilities`
            # returns all-zero shares then, and summing them gave p = 0.0 — which the REVIEW band
            # reads as a confident "not dangerous" (study 22, phase 0).
            if read is not None and read.mass > 0.0:
                shares, mass = read.shares, read.mass
                if question.event:
                    p = sum(shares.get(o, 0.0) for o in question.event)
        if self.render_mode != "default":
            choice = mapping.get(choice, "") or None if choice is not None else None
            shares = {mapping[k]: v for k, v in shares.items()} if shares else shares
            if original.event and shares:
                p = sum(shares.get(option, 0.0) for option in original.event)
        return Reading(
            choice=choice, shares=shares, p=p, mass=mass, usd=0.0, raw=content[:200], logprobs_came=bool(logprobs),
            resolved_model=resolved_model,
        )
