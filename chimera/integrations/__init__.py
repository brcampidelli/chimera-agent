"""Integrations: MCP client + OpenAPI->tool importer + connector registry.

Lets the agent reach many platforms (via MCP) and register arbitrary REST APIs
as tools.
"""

from chimera.integrations.a2a import (
    A2AServer,
    A2ATask,
    AgentSkill,
    chimera_agent_card,
)
from chimera.integrations.connectors import Connector, ConnectorRegistry
from chimera.integrations.mcp_client import (
    MCPConnector,
    MCPSession,
    MCPTool,
    MCPToolSpec,
    StdioMCPSession,
    connect_stdio,
)
from chimera.integrations.messaging import (
    MessageSender,
    SenderRegistry,
    SendMessageTool,
)
from chimera.integrations.openapi import (
    OpenAPIConnector,
    RestApiTool,
    load_spec,
    tools_from_openapi,
)

__all__ = [
    "Connector",
    "ConnectorRegistry",
    "MessageSender",
    "SenderRegistry",
    "SendMessageTool",
    "OpenAPIConnector",
    "RestApiTool",
    "tools_from_openapi",
    "load_spec",
    "MCPConnector",
    "MCPSession",
    "MCPTool",
    "MCPToolSpec",
    "StdioMCPSession",
    "connect_stdio",
    "A2AServer",
    "A2ATask",
    "AgentSkill",
    "chimera_agent_card",
]
