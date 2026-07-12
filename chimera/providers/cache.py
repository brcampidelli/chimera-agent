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
import os
import threading
import uuid
from pathlib import Path
from typing import Any


class CompletionCache:
    """A JSON-file cache of completions keyed by the request hash.

    Thread-safe: the hierarchy dispatch and the fusion panel both call the shared
    gateway (hence this cache) from multiple threads. A lock guards the dict and the
    file, and writes are atomic (tmp + replace) so an interrupted write can't truncate
    the cache file.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
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
        # Unique tmp name (pid+uuid): a FIXED ".tmp" is shared by every CompletionCache instance on
        # the same file, so two gateways writing concurrently could interleave and publish corrupt JSON.
        tmp = self.path.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)  # atomic — never leaves a half-written cache file

    @staticmethod
    def key(
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int | None,
        params: dict[str, Any] | None = None,
    ) -> str:
        # `params` folds in the OTHER response-affecting request fields (top_p, seed, stop,
        # response_format, api_base, ...) so two requests that differ only in those don't collide to
        # the same key and serve each other's answer. Non-JSON-able values fall back to repr.
        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "params": params or {},
            },
            sort_keys=True,
            ensure_ascii=False,
            default=repr,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._data.get(key)

    def put(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._data[key] = value
            self._save()

    def __len__(self) -> int:
        return len(self._data)
