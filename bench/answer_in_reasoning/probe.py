"""A confirmation probe: does the product gateway see what H11 saw, on the route H11 saw it?

`bench/review_judge/RESULTS-h11.md` (S5) found `deepseek-r1` on Novita returning `content` empty with
`finish_reason: stop` on 37-43% of calls, the answer filed under the reasoning field. The fix adds
`CompletionResult.reasoning` / `answer_in_reasoning` and a reader for structured callers
(`chimera.providers.thinking.answer_at_end_of_reasoning`). This probe sends the hosted decision
backend's exact request (its system text for the governance question, the state, temperature 0.3,
2000 tokens, the model's default reasoning) through the product gateway, pinned to Novita with no
fallbacks, and records per call:

- what LiteLLM put where (the raw message's `reasoning_content`, `provider_specific_fields` keys; per
  streamed chunk, which delta fields carried reasoning) — captured by wrapping `litellm.completion`
  in this process, never by changing product code;
- what the gateway made of it (`answer_in_reasoning`, content empty, reasoning length, provider);
- what the backend's reader recovers from a flagged reply, and the reading it gives.

Pre-registration: `PREREGISTRATION.md` beside this file, committed before the first call.

    python bench/answer_in_reasoning/probe.py --dry-run   # prints the plan, no call
    python bench/answer_in_reasoning/probe.py --run        # the paid probe (cap US$ 0.30)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from bench.governance_judge.corpus_ambiguous import corpus  # noqa: E402

MODEL = "openrouter/deepseek/deepseek-r1"
PROVIDER = "Novita"
PIN = {"provider": {"order": [PROVIDER], "allow_fallbacks": False}}
BATCH_ITEMS = 20
STREAM_ITEMS = 6
WORKERS = 6
#: OpenRouter's listed price for this model on Novita (2026-09-25, `PREREGISTRATION-h11.md`), used
#: only when a call's billed `usage.cost` is absent (the stream).
PRICE_PROMPT, PRICE_COMPLETION = 0.70e-6, 2.50e-6
CAP_USD, STOP_USD = 0.30, 0.25
#: H11 saw one call run to 39,443 completion tokens over 28 minutes on this route, past the
#: gateway's 32k ceiling, so the provider's honouring of `max_tokens` is not assumed. A call still
#: open after this long is ended and recorded as a halt.
TIMEOUT_S = 300
OUT = Path(__file__).resolve().parent / "results"

_RAW = threading.local()
_SPENT = [0.0]
#: Amendment 1: keep the whole reasoning of a flagged call (batch route only).
KEEP_FULL = [False]
_LOCK = threading.Lock()


def _install_capture() -> None:
    """Keep each thread's raw LiteLLM answer: the message for a batch call, per-chunk facts for a
    stream (the chunks are passed through unchanged)."""
    import litellm

    original = litellm.completion

    def capturing(*args: Any, **kwargs: Any) -> Any:
        response = original(*args, **kwargs)
        if not kwargs.get("stream"):
            _RAW.message = response.choices[0].message
            _RAW.cost = getattr(getattr(response, "usage", None), "cost", None)
            return response
        _RAW.chunks = []

        def tee() -> Any:
            for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                rc = getattr(delta, "reasoning_content", None) if delta is not None else None
                psf = getattr(delta, "provider_specific_fields", None) if delta is not None else None
                _RAW.chunks.append({
                    "reasoning_content": bool(isinstance(rc, str) and rc),
                    "psf_keys": sorted(psf) if isinstance(psf, dict) else [],
                    "content": bool(getattr(delta, "content", None)) if delta is not None else False,
                })
                yield chunk

        return tee()

    litellm.completion = capturing


def plan() -> list[dict[str, Any]]:
    items = corpus()
    calls = [{"route": "batch", "id": it.id, "label": it.label, "state": it.action}
             for it in items[:BATCH_ITEMS]]
    calls += [{"route": "stream", "id": it.id, "label": it.label, "state": it.action}
              for it in items[:STREAM_ITEMS]]
    return calls


def one(call: dict[str, Any]) -> dict[str, Any]:
    from chimera.decisions.governance import DANGER
    from chimera.decisions.hosted import HostedVerbalizedBackend
    from chimera.providers.gateway import LLMGateway
    from chimera.providers.thinking import answer_at_end_of_reasoning

    with _LOCK:
        if _SPENT[0] >= STOP_USD:
            return {**call, "skipped": "budget stop"}
    backend = HostedVerbalizedBackend(LLMGateway(), MODEL)
    messages = [{"role": "system", "content": backend.system_text(DANGER)},
                {"role": "user", "content": call["state"]}]
    _RAW.message, _RAW.cost, _RAW.chunks = None, None, []
    started = time.time()
    try:
        if call["route"] == "stream":
            result = backend.gateway.stream_complete(
                messages, model=MODEL, temperature=backend.temperature,
                max_tokens=backend.max_tokens, extra_body=PIN, timeout=TIMEOUT_S)
        else:
            result = backend.gateway.complete(
                messages, model=MODEL, temperature=backend.temperature,
                max_tokens=backend.max_tokens, extra_body=PIN, timeout=TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — a halt, recorded as one
        return {**call, "halt": f"{type(exc).__name__}: {str(exc)[:200]}"}
    prompt = int(result.prompt_tokens or 0)
    completion = int(result.completion_tokens or 0)
    billed = _RAW.cost if isinstance(_RAW.cost, (int, float)) else None
    usd = float(billed) if billed is not None else prompt * PRICE_PROMPT + completion * PRICE_COMPLETION
    with _LOCK:
        _SPENT[0] += usd
    raw: dict[str, Any] = {}
    message = _RAW.message
    if message is not None:
        rc = getattr(message, "reasoning_content", None)
        psf = getattr(message, "provider_specific_fields", None) or {}
        raw = {
            "content_is_none": message.content is None,
            "reasoning_content_chars": len(rc) if isinstance(rc, str) else None,
            "psf_keys": sorted(psf) if isinstance(psf, dict) else [],
            "psf_reasoning_equals": isinstance(psf, dict) and psf.get("reasoning") == rc,
        }
    else:
        chunks = _RAW.chunks
        raw = {
            "chunks": len(chunks),
            "chunks_with_reasoning_content": sum(c["reasoning_content"] for c in chunks),
            "chunks_with_content": sum(c["content"] for c in chunks),
            "delta_psf_keys": sorted({k for c in chunks for k in c["psf_keys"]}),
        }
    recovered = ""
    if result.answer_in_reasoning:
        recovered = answer_at_end_of_reasoning(result.reasoning, backend.answer_keys(DANGER))
    text = result.content if result.content.strip() else recovered
    reading = backend.read(text, DANGER)
    row = {
        **{k: v for k, v in call.items() if k != "state"},
        "provider": result.provider, "generation_id": result.generation_id,
        "finish_reason": result.finish_reason, "content_empty": not result.content.strip(),
        "answer_in_reasoning": result.answer_in_reasoning, "reasoning_chars": len(result.reasoning),
        "recovered": bool(recovered), "recovered_text": recovered,
        "reasoning_tail": result.reasoning[-400:] if result.answer_in_reasoning else "",
        "choice": reading.choice, "p": reading.p,
        "prompt": prompt, "completion": completion, "usd": usd, "billed": billed is not None,
        "seconds": round(time.time() - started, 1), "raw": raw,
    }
    if KEEP_FULL[0] and result.answer_in_reasoning:
        return amendment_row(row, result.reasoning)
    return row


def last_object_anywhere(reasoning: str, keys: tuple[str, ...]) -> tuple[str, int]:
    """Amendment 1: H11's rule, for comparison only — the last valid object carrying ``keys``
    anywhere in the reasoning, and where it ends. ("", -1) when there is none."""
    decoder = json.JSONDecoder(strict=False)
    pos = reasoning.rfind("{")
    while pos != -1:
        try:
            obj, end = decoder.raw_decode(reasoning, pos)
        except json.JSONDecodeError:
            obj, end = None, pos
        if isinstance(obj, dict) and all(k in obj for k in keys):
            return reasoning[pos:end], end
        pos = reasoning.rfind("{", 0, pos)
    return "", -1


def amendment_row(row: dict[str, Any], reasoning: str) -> dict[str, Any]:
    """What precedes a prose tail: is there an object of the schema earlier, and what follows it."""
    import re

    from chimera.decisions.governance import DANGER
    from chimera.decisions.hosted import HostedVerbalizedBackend

    keys = HostedVerbalizedBackend.answer_keys(DANGER)
    found, end = last_object_anywhere(reasoning, keys)
    after = reasoning[end:] if found else ""
    words = re.findall(r"\b(BLOCK|REVIEW|ALLOW)\b", after)
    verdict = ""
    if found:
        verdict = str(json.loads(found, strict=False).get(DANGER.key) or "").upper()
    return {
        **row, "reasoning": reasoning,
        "object_anywhere": found, "chars_after_object": len(after.strip()) if found else None,
        "last_word_after_object": words[-1] if words else "",
        "after_restates_verdict": bool(found and words and words[-1] == verdict),
        "json_anywhere": "{" in reasoning,
    }


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    z, p = 1.96, k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    done = [r for r in rows if "halt" not in r and "skipped" not in r]
    out: dict[str, Any] = {"calls": len(rows), "answered_calls": len(done),
                           "halts": sum("halt" in r for r in rows),
                           "usd": round(sum(r.get("usd", 0.0) for r in done), 5)}
    for route in ("batch", "stream"):
        sub = [r for r in done if r["route"] == route]
        flagged = [r for r in sub if r["answer_in_reasoning"]]
        lo, hi = wilson(len(flagged), len(sub))
        out[route] = {
            "n": len(sub),
            "flagged": len(flagged), "flagged_ci95": [round(lo, 3), round(hi, 3)],
            "empty_not_flagged": sum(r["content_empty"] and not r["answer_in_reasoning"] for r in sub),
            "recovered_of_flagged": sum(r["recovered"] for r in flagged),
            "providers": sorted({r["provider"] for r in sub}),
            "finish_reasons": sorted({r["finish_reason"] for r in sub}),
        }
    batch = [r for r in done if r["route"] == "batch"]
    out["batch"]["reasoning_content_present"] = sum(
        (r["raw"].get("reasoning_content_chars") or 0) > 0 for r in batch)
    out["batch"]["psf_reasoning_equals"] = sum(bool(r["raw"].get("psf_reasoning_equals")) for r in batch)
    stream = [r for r in done if r["route"] == "stream"]
    out["stream"]["calls_with_reasoning_content_deltas"] = sum(
        r["raw"].get("chunks_with_reasoning_content", 0) > 0 for r in stream)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--amendment1", action="store_true",
                        help="the 20 batch calls again, keeping a flagged call's whole reasoning")
    parser.add_argument("--prior-usd", type=float, default=0.0,
                        help="spend already made under this pre-registration, counted to the cap")
    args = parser.parse_args()
    calls = plan()
    out = OUT
    if args.amendment1:
        calls = [c for c in calls if c["route"] == "batch"]
        KEEP_FULL[0] = True
        out = OUT / "amendment1"
    _SPENT[0] = args.prior_usd
    worst = args.prior_usd + sum(600 * PRICE_PROMPT + 2000 * PRICE_COMPLETION for _ in calls)
    print(f"{len(calls)} calls to {MODEL} pinned to {PROVIDER}; prior US$ {args.prior_usd:.4f}; "
          f"worst case ~US$ {worst:.3f} (cap {CAP_USD}, stop {STOP_USD})")
    if worst > CAP_USD:
        print("worst case above the cap; not running")
        return 2
    if not args.run:
        for c in calls:
            print(f"  {c['route']:6} {c['id']}")
        return 0
    _install_capture()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        rows = list(pool.map(one, calls))
    out.mkdir(parents=True, exist_ok=True)
    (out / "rows.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    result = summary(rows)
    if args.amendment1:
        result = {k: v for k, v in result.items() if k != "stream"}
        flagged = [r for r in rows if r.get("answer_in_reasoning")]
        result["amendment1"] = {
            "flagged": len(flagged),
            "end_rule_recovered": sum(r["recovered"] for r in flagged),
            "object_anywhere": sum(bool(r["object_anywhere"]) for r in flagged),
            "object_anywhere_not_at_end": sum(
                bool(r["object_anywhere"]) and not r["recovered"] for r in flagged),
            "of_those_prose_restates_verdict": sum(
                bool(r["object_anywhere"]) and not r["recovered"] and r["after_restates_verdict"]
                for r in flagged),
            "no_brace_at_all": sum(not r["json_anywhere"] for r in flagged),
        }
    (out / "summary.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    code = main()
    # H11's driver hung at interpreter exit on LiteLLM's logging threads after its rows were written;
    # everything this run owes is on disk by now.
    sys.stdout.flush()
    os._exit(code)
