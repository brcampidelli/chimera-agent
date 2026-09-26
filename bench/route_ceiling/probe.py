"""Which route does OpenRouter pick for the preset weak rung, at which ``max_tokens``?

The question behind `CatalogEntry.max_output`. OpenRouter lists each route's largest completion
at ``/api/v1/models/<slug>/endpoints`` (public, no key); this sends tiny requests (a few hundred
tokens on a US$ 0.075/M model) with and without a tool, at several ``max_tokens``, and records
which provider answered and every non-200 reply. Raw HTTP, stdlib only, so the status and body
are OpenRouter's own with no client library in between.

    OPENROUTER_API_KEY=… python bench/route_ceiling/probe.py out.json [reps]

`endpoints-2026-09-26.json` is that listing for every catalogue row on the day, as
``slug -> routes`` (max completion tokens, context, whether ``tools`` is listed); the live check in
`tests/test_catalog_is_live.py` reads the same listing.

What it found on 2026-09-26 (`results-2026-09-26.json`): at 32,000 every tool-free call went to
Parasail, the one route listing as much (32,768), and half came back 429 from its shared pool; at
16,384 or with no ``max_tokens`` they went to Venice; tool calls were served at every value. It
cannot show how often a pinned route fails on another day — a 429 is the provider's load at the
time — only that the ceiling narrowed the pool to one route with nothing to fall back to.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "mistralai/mistral-small-3.2-24b-instruct"
TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a text file from the workspace.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
}
CASES = [(False, 32000), (False, 16384), (False, None), (True, 32000), (True, 20000), (True, 16384)]


def call(model: str, *, tools: bool, max_tokens: int | None) -> dict[str, object]:
    """One request; the provider that answered, or the status and body when it failed."""
    body: dict[str, object] = {
        "model": model,
        "messages": [{"role": "user", "content": "Read the file notes.txt."}],
        "temperature": 0,
    }
    if tools:
        body["tools"] = [TOOL]
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    req = urllib.request.Request(
        URL,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())
            return {
                "status": resp.status,
                "provider": data.get("provider"),
                "cost": (data.get("usage") or {}).get("cost"),
                "seconds": round(time.time() - started, 1),
            }
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")
        masked = re.sub(r'"user_id":"[^"]*"', '"user_id":"<masked>"', text)
        return {"status": exc.code, "body": masked}


def main() -> None:
    reps = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    rows = []
    for tools, max_tokens in CASES:
        for rep in range(reps):
            row = {"tools": tools, "max_tokens": max_tokens, "rep": rep}
            row.update(call(MODEL, tools=tools, max_tokens=max_tokens))
            rows.append(row)
            shown = ("tools", "max_tokens", "status", "provider")
            print(json.dumps({k: row.get(k) for k in shown}))
    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1)


if __name__ == "__main__":
    main()
