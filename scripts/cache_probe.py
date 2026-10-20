#!/usr/bin/env python3
"""Opt-in, two-call prefix-cache probe for the Luna, GLM and DeepSeek OpenRouter routes.

Dry-run is the default. Set OPENROUTER_API_KEY, review the model/route selections and run:

    python scripts/cache_probe.py --run --output cache-probe.jsonl

Each route gets the same long system prefix twice, with a fixed tiny user prompt and no tools. A
provider order may be supplied to pin a real route; when supplied, fallbacks are disabled. The
script records provider-reported cache reads as null when absent (not zero). It never prints keys.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MODELS = {
    "luna": "openrouter/openai/gpt-6-luna",
    "glm": "openrouter/z-ai/glm-5.3-flash",
    "deepseek": "openrouter/deepseek/deepseek-v4-flash-0731",
}
# Repetition makes a cacheable prefix much longer than the providers' minimum cache blocks, without
# depending on a generated fixture file or changing the request between calls.
PREFIX = "You are a cache instrumentation probe. Treat the following fixed reference as context.\n" + (
    "Stable reference: the quick brown fox crosses the quiet field; record no action.\n" * 512
)
USER = "Reply with exactly: ok"


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def cache_read_tokens(usage: Any) -> int | None:
    """Read supported provider usage formats, retaining the distinction between missing and zero."""
    for key in ("cache_read_input_tokens", "cache_read_tokens"):
        value = _field(usage, key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    details = _field(usage, "prompt_tokens_details")
    for key in ("cached_tokens", "cache_read_tokens"):
        value = _field(details, key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def prompt_tokens(usage: Any) -> int | None:
    value = _field(usage, "prompt_tokens")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def run_probe(
    *,
    output: Path,
    models: dict[str, str],
    provider_order: str = "",
    completion: Callable[..., Any] | None = None,
) -> None:
    if completion is None:
        try:
            import litellm
        except ImportError as exc:  # pragma: no cover - environment-specific
            raise SystemExit("Install the project provider dependencies before probing (litellm missing).") from exc
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise SystemExit("OPENROUTER_API_KEY is required; no request was sent.")
        completion = litellm.completion
    route = [part.strip() for part in provider_order.split(",") if part.strip()]
    kwargs: dict[str, Any] = {"temperature": 0, "max_tokens": 8}
    if route:
        kwargs["extra_body"] = {"provider": {"order": route, "allow_fallbacks": False}}

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        for name, model in models.items():
            for repetition in (1, 2):
                response = completion(
                    model=model,
                    messages=[{"role": "system", "content": PREFIX}, {"role": "user", "content": USER}],
                    **kwargs,
                )
                usage = _field(response, "usage")
                row = {
                    "ts": datetime.now(UTC).isoformat(),
                    "route": name,
                    "model": model,
                    "provider_order": route,
                    "repetition": repetition,
                    "prompt_tokens": prompt_tokens(usage),
                    "cache_read_tokens": cache_read_tokens(usage),
                }
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                print(json.dumps(row, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="send six live requests; omitted means dry-run")
    parser.add_argument("--output", type=Path, default=Path("cache-probe.jsonl"))
    parser.add_argument("--provider-order", default="", help="optional OpenRouter route pin, comma-separated")
    parser.add_argument("--only", choices=["all", *MODELS], default="all")
    args = parser.parse_args()
    selected = MODELS if args.only == "all" else {args.only: MODELS[args.only]}
    if not args.run:
        print(json.dumps({"status": "dry-run", "will_send_requests": False,
                          "models": selected, "calls": len(selected) * 2,
                          "prefix_chars": len(PREFIX), "provider_order": args.provider_order}))
        return 0
    run_probe(output=args.output, models=selected, provider_order=args.provider_order)
    return 0


if __name__ == "__main__":
    sys.exit(main())
