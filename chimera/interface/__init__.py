"""Conversational interface core, shared by the chat REPL, TUI and messaging.

A single :class:`ChatSession` holds multi-turn state and optional long-term
memory; every front-end (CLI ``chat``, the TUI, the messaging gateway) is a thin
shell over it, so conversation behaviour is defined once.
"""

from chimera.interface.session import ChatSession, ChatTurn

__all__ = ["ChatSession", "ChatTurn"]
