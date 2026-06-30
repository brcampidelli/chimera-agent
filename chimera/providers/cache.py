"""Exact-match completion cache (HORIZON-style prompt caching).

Reasoning turns (tool-free) repeat a lot across a session — the fusion panel/judge/
synthesizer, the planner, the reviewer, and re-runs of benchmarks all re-issue the same
prompt. This JSON-backed cache returns a stored completion for an identical
``(model, messages, temperature, max_tokens)`` request, skipping the API call entirely.

It is **opt-in** (``CHIMERA_CACHE=on``) and used only for tool-free turns, so tool/
action calls always hit the model live. Stores plain dicts to avoid importing the
gateway (which would be circular).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class CompletionCache:
    """A JSON-file cache of completions keyed by the request hash."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            self._data = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def key(
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int | None,
    ) -> str:
        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        return self._data.get(key)

    def put(self, key: str, value: dict[str, Any]) -> None:
        self._data[key] = value
        self._save()

    def __len__(self) -> int:
        return len(self._data)
