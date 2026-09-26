"""The provider's own cache accounting for the chat requests `measure_prefix.py` builds.

Sends both arms' exact requests, in order, to one pinned provider and reads back how many prompt
tokens it served from cache (`cache_read_tokens`):
- one output token per request, because only the prompt side is measured;
- the default registry's tool schemas plus `echo` on every request, as a chat agent sends them;
- a nonce heading each arm-run's system message, so one arm-run cannot warm the other's history;
- two orders, flat then real and real then flat, each arm-run with a fresh nonce. This provider
  serialises the tools before the system message, so an arm-run's FIRST request can find the tools
  cached by the arm-run before it; the registered share leaves each arm-run's first request out.

    OPENROUTER_API_KEY=... python bench/chat_history/replay_live.py --requests requests.json \
        --out bench/chat_history/results/cache-live.json

About 70 requests of a few thousand prompt tokens each: a few cents.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from chimera.providers import LLMGateway  # noqa: E402
from chimera.tools.builtin import EchoTool, default_registry  # noqa: E402

MODEL = "openrouter/deepseek/deepseek-v4-flash-0731"
PROVIDER = "DeepInfra"
PRICE_IN, PRICE_OUT = 0.06e-6, 0.18e-6


def _tools() -> list[dict[str, Any]]:
    registry = default_registry(Path(tempfile.mkdtemp(prefix="chat-history-")))
    if not any(t.name == "echo" for t in registry.tools()):
        registry.register(EchoTool())
    return registry.to_openai_schema() or []


def _run(gateway: LLMGateway, requests: list[list[dict[str, Any]]], tools: list[dict[str, Any]],
         pause: float) -> dict[str, Any]:
    nonce = uuid.uuid4().hex[:8]
    rows = []
    for i, messages in enumerate(requests):
        sent = [dict(m) for m in messages]
        sent[0] = {**sent[0], "content": f"[session {nonce}]\n\n{sent[0]['content']}"}
        result = gateway.complete(
            sent, model=MODEL, temperature=0.0, max_tokens=1, tools=tools,
            extra_body={"provider": {"order": [PROVIDER], "allow_fallbacks": False}},
        )
        rows.append({
            "request": i,
            "prompt_tokens": result.prompt_tokens or 0,
            "cached_tokens": result.cache_read_tokens or 0,
            "completion_tokens": result.completion_tokens or 0,
            "provider": getattr(result, "provider", "") or "",
        })
        time.sleep(pause)
    prompt = sum(r["prompt_tokens"] for r in rows)
    cached = sum(r["cached_tokens"] for r in rows)
    rest_prompt = sum(r["prompt_tokens"] for r in rows[1:])
    rest_cached = sum(r["cached_tokens"] for r in rows[1:])
    return {
        # Registered primary: what the provider processed at the full rate, first request left out.
        "uncached_tokens_without_first": rest_prompt - rest_cached,
        "nonce": nonce,
        "prompt_tokens": prompt,
        "cached_tokens": cached,
        "cached_share": round(cached / prompt, 4) if prompt else None,
        "prompt_tokens_without_first": rest_prompt,
        "cached_tokens_without_first": rest_cached,
        "cached_share_without_first": round(rest_cached / rest_prompt, 4) if rest_prompt else None,
        "providers": sorted({r["provider"] for r in rows}),
        "usd": sum(r["prompt_tokens"] * PRICE_IN + r["completion_tokens"] * PRICE_OUT for r in rows),
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=Path, required=True, help="from measure_prefix.py --dump")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pause", type=float, default=1.0)
    args = parser.parse_args(argv)
    dumped = json.loads(args.requests.read_text(encoding="utf-8"))
    gateway = LLMGateway()
    tools = _tools()
    report: dict[str, Any] = {"model": MODEL, "provider": PROVIDER, "tools": len(tools), "orders": []}
    for order in (("flat", "real"), ("real", "flat")):
        runs = {}
        for arm in order:
            runs[arm] = _run(gateway, dumped[arm]["requests"], tools, args.pause)
            got = runs[arm]
            print(f"{'>'.join(order)} {arm}: prompt {got['prompt_tokens']}, cached {got['cached_tokens']} "
                  f"({got['cached_share']}); without first: uncached "
                  f"{got['uncached_tokens_without_first']}, share {got['cached_share_without_first']}; "
                  f"providers {got['providers']}", flush=True)
        report["orders"].append({"order": list(order), "runs": runs})
    pooled: dict[str, Any] = {}
    for arm in ("flat", "real"):
        p = sum(o["runs"][arm]["prompt_tokens_without_first"] for o in report["orders"])
        c = sum(o["runs"][arm]["cached_tokens_without_first"] for o in report["orders"])
        pooled[arm] = {"prompt": p, "cached": c, "uncached": p - c,
                       "share": round(c / p, 4) if p else None}
    report["pooled_without_first"] = pooled
    report["usd"] = sum(r["usd"] for o in report["orders"] for r in o["runs"].values())
    print(json.dumps(pooled), f"US${report['usd']:.4f}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
