"""Run messaging adapters (Discord/Telegram) inside a long-lived process — the desktop app.

`chimera serve --discord` runs an adapter in the FOREGROUND and blocks. The desktop app is already
serving the HTTP API in its main thread, so to also reach you on a chat platform it must run the
adapter in a BACKGROUND thread and start/stop it at runtime from the UI. This manager owns exactly
that: one daemon thread per platform, wired to the same real agent stack (a session per chat, with
the send_message tool so the agent can reply and reach out), plus honest status — configured (a token
is present), running (the thread is alive), and the last error if the adapter died (e.g. a bad token).

The adapter is injectable so the wiring is testable without discord.py or the network.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.telemetry import get_logger

if TYPE_CHECKING:
    from chimera.config import Settings
    from chimera.memory import MemoryGraph, MemoryManager
    from chimera.providers import SupportsComplete

_log = get_logger("server.manager")

# Platforms this manager can run, and the settings attribute holding each one's token.
_TOKEN_ATTR: dict[str, str] = {
    "discord": "discord_bot_token",
    "telegram": "telegram_bot_token",
}


class _Running:
    """A live adapter and the thread serving it; ``error`` is set if that thread died."""

    def __init__(self, adapter: Any, thread: threading.Thread) -> None:
        self.adapter = adapter
        self.thread = thread
        self.error: str | None = None


class MessagingManager:
    """Start/stop chat-platform adapters in background threads and report their status."""

    def __init__(
        self,
        *,
        settings: Settings,
        backend: SupportsComplete,
        model: str | None,
        max_steps: int,
        workspace: Path,
        memory: MemoryManager | None = None,
        graph: MemoryGraph | None = None,
        adapter_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._backend = backend
        self._model = model
        self._max_steps = max_steps
        self._workspace = workspace
        self._memory = memory
        self._graph = graph
        self._adapter_factory = adapter_factory or self._default_adapter
        self._running: dict[str, _Running] = {}
        self._lock = threading.Lock()

    # --- introspection -------------------------------------------------------------------------
    def platforms(self) -> tuple[str, ...]:
        return tuple(_TOKEN_ATTR)

    def configured(self, platform: str) -> bool:
        """True when a token for ``platform`` is present (so it CAN be started)."""
        attr = _TOKEN_ATTR.get(platform)
        return bool(attr and getattr(self._settings, attr, None))

    def is_running(self, platform: str) -> bool:
        rec = self._running.get(platform)
        return bool(rec and rec.thread.is_alive())

    def status(self) -> dict[str, dict[str, Any]]:
        """Per-platform {configured, running, error} — the honest view the UI shows."""
        out: dict[str, dict[str, Any]] = {}
        for platform in _TOKEN_ATTR:
            rec = self._running.get(platform)
            out[platform] = {
                "configured": self.configured(platform),
                "running": self.is_running(platform),
                "error": rec.error if rec else None,
            }
        return out

    # --- lifecycle -----------------------------------------------------------------------------
    def _default_adapter(self, platform: str) -> Any:
        """Build a real adapter from the configured token, or raise ValueError if not configured."""
        attr = _TOKEN_ATTR.get(platform)
        token = getattr(self._settings, attr, None) if attr else None
        if not token:
            raise ValueError(f"{platform} is not configured (no token set)")
        if platform == "discord":
            from chimera.server import DiscordAdapter

            return DiscordAdapter(token)
        if platform == "telegram":
            from chimera.server import TelegramAdapter

            return TelegramAdapter(token)
        raise ValueError(f"unknown messaging platform: {platform!r}")

    def _gateway_on_message(self, adapter: Any) -> Callable[[Any], str]:
        """A MessageGateway.on_message wired to the real agent stack, with send_message registered
        so the agent can reply and reach out. Mirrors the CLI's `_serve_platform` factory."""
        from chimera.core import Agent, AgentConfig
        from chimera.integrations import SenderRegistry, SendMessageTool
        from chimera.interface import ChatSession
        from chimera.server import MessageGateway
        from chimera.tools import default_registry

        senders = SenderRegistry()
        senders.register(adapter)
        send_tool = SendMessageTool(senders)

        def factory() -> ChatSession:
            registry = default_registry(self._workspace)
            registry.register(send_tool)
            runner = Agent(
                self._backend, registry, AgentConfig(model=self._model, max_steps=self._max_steps)
            )
            return ChatSession(
                runner,
                memory=self._memory,
                graph=self._graph,
                remember_from_chat=self._settings.remember_from_chat,
            )

        return MessageGateway(factory).on_message

    def start(self, platform: str) -> None:
        """Start ``platform`` in a background thread. Idempotent; raises ValueError if not configured
        or unknown. A crash of the adapter thread (e.g. a bad token) is captured in status, not raised."""
        with self._lock:
            if self.is_running(platform):
                return
            adapter = self._adapter_factory(platform)  # ValueError propagates (not configured/unknown)
            on_message = self._gateway_on_message(adapter)
            rec = _Running(adapter, threading.Thread())  # placeholder thread, replaced below

            def _serve() -> None:
                try:
                    adapter.start(on_message)  # blocking until adapter.stop()
                except Exception as exc:  # noqa: BLE001 — a dead adapter must not crash the app
                    rec.error = f"{type(exc).__name__}: {exc}"
                    _log.warning("messaging adapter '%s' stopped: %s", platform, exc)

            rec.thread = threading.Thread(target=_serve, name=f"chimera-msg-{platform}", daemon=True)
            self._running[platform] = rec
            rec.thread.start()

    def stop(self, platform: str) -> None:
        with self._lock:
            rec = self._running.pop(platform, None)
        if rec is not None:
            rec.adapter.stop()

    def stop_all(self) -> None:
        for platform in list(self._running):
            self.stop(platform)
