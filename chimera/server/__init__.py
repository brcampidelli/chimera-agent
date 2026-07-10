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
    chunk_text,
    run_with_indicator,
)
from chimera.server.http import WebhookHandler, handle, make_server
from chimera.server.mcp_server import CHIMERA_MCP_TOOLS, ChimeraMCP
from chimera.server.signal_adapter import SignalAdapter
from chimera.server.slack_adapter import SlackAdapter
from chimera.server.telegram_adapter import TelegramAdapter
from chimera.server.whatsapp import WhatsAppSender, WhatsAppWebhook

__all__ = [
    "InboundMessage",
    "MessageGateway",
    "Adapter",
    "LocalAdapter",
    "DiscordAdapter",
    "TelegramAdapter",
    "SlackAdapter",
    "SignalAdapter",
    "WhatsAppSender",
    "WhatsAppWebhook",
    "chunk_text",
    "run_with_indicator",
    "handle",
    "make_server",
    "WebhookHandler",
    "ChimeraMCP",
    "CHIMERA_MCP_TOOLS",
]
