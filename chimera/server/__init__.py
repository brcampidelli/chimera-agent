"""Messaging gateway + HTTP server.

The gateway routes per-chat messages into ChatSessions; adapters (local + Discord, HTTP)
are its transports.
"""

from chimera.server.discord_adapter import DiscordAdapter
from chimera.server.gateway import (
    Adapter,
    InboundMessage,
    LocalAdapter,
    MessageGateway,
)
from chimera.server.http import handle, make_server

__all__ = [
    "InboundMessage",
    "MessageGateway",
    "Adapter",
    "LocalAdapter",
    "DiscordAdapter",
    "handle",
    "make_server",
]
