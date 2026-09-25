"""The provider's own cache accounting for the requests `measure_prefix.py` builds.

The prefix measurement counts bytes a cache *could* reuse. This sends those exact requests, arm by
arm and in order, to a real provider and reads back what it says it served from cache
(`cache_read_tokens`). Everything else is pinned:
- each request asks for one output token, because only the prompt side is measured;
- each arm gets its own nonce at the head of the system message (`AgentConfig.prefix_nonce`), so
  one arm cannot warm the other's cache;
- the model's replies are the scripted ones, so both arms send the same conversation, and the only
  difference is where the per-turn notes sit.

    OPENROUTER_API_KEY=... python bench/turn_context/replay_live.py --model openrouter/deepseek/deepseek-v4-flash-0731

It costs cents: about 24 requests per arm, a few thousand prompt tokens each, one token out.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import measure_prefix  # noqa: E402

from chimera.providers import LLMGateway  # noqa: E402


def _requests(after: bool, nonce: str) -> list[list[dict[str, Any]]]:
    """The message lists `measure_prefix` sends for one arm, with a nonce heading the system."""
    captured: list[list[dict[str, Any]]] = []
    original = measure_prefix._Scripted.complete

    def spy(self: Any, messages: list[Any], **kwargs: Any) -> Any:
        captured.append([dict(m) if isinstance(m, dict) else m.as_dict() for m in messages])
        return original(self, messages, **kwargs)

    measure_prefix._Scripted.complete = spy  # type: ignore[method-assign]
    try:
        measure_prefix.run_arm(after, len(measure_prefix.TURNS), 3)
    finally:
        measure_prefix._Scripted.complete = original  # type: ignore[method-assign]
    for request in captured:
        request[0] = {**request[0], "content": f"[session {nonce}]\n\n{request[0]['content']}"}
    return captured


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--pause", type=float, default=1.0, help="seconds between requests")
    parser.add_argument("--provider", default="", help="pin one OpenRouter provider (no fallbacks)")
    parser.add_argument("--tools", action="store_true", help="send the default registry's tool schemas")
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("results-live.json"))
    args = parser.parse_args(argv)
    gateway = LLMGateway()
    extra: dict[str, Any] = {}
    if args.provider:
        # One endpoint for every request of both arms: a cache lives on one provider, and a pool that
        # spreads consecutive requests over several providers measures the routing, not the prompt.
        extra["extra_body"] = {"provider": {"order": [args.provider], "allow_fallbacks": False}}
    tools = None
    if args.tools:
        import tempfile

        from chimera.tools.builtin import default_registry

        tools = default_registry(Path(tempfile.mkdtemp())).to_openai_schema() or None
    report: dict[str, Any] = {"model": args.model, "provider": args.provider, "tools": bool(tools), "arms": {}}
    for arm, after in (("before", False), ("after", True)):
        nonce = uuid.uuid4().hex[:8]
        rows = []
        for i, messages in enumerate(_requests(after, nonce)):
            result = gateway.complete(
                messages, model=args.model, temperature=0.0, max_tokens=1, tools=tools, **extra
            )
            rows.append({
                "request": i,
                "prompt_tokens": result.prompt_tokens,
                "cached_tokens": result.cache_read_tokens,
                "provider": getattr(result, "provider", "") or "",
            })
            time.sleep(args.pause)
        prompt = sum(r["prompt_tokens"] or 0 for r in rows)
        cached = sum(r["cached_tokens"] or 0 for r in rows)
        report["arms"][arm] = {
            "nonce": nonce,
            "prompt_tokens": prompt,
            "cached_tokens": cached,
            "cached_share": round(cached / prompt, 3) if prompt else None,
            "providers": sorted({r["provider"] for r in rows}),
            "rows": rows,
        }
        print(f"{arm}: prompt {prompt}, cached {cached} ({report['arms'][arm]['cached_share']}),"
              f" providers {report['arms'][arm]['providers']}")
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
