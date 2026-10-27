"""MCP (Model Context Protocol) integration.

Two layers:

* a small, fully-tested *wrapping* layer that turns any MCP session (a thing that
  can ``list_tools`` and ``call_tool``) into Chimera tools, and
* :class:`StdioMCPSession` and :class:`StreamableHTTPMCPSession`, real MCP clients
  backed by the optional ``mcp`` package (install with the ``mcp`` extra). The heavy/async part is isolated here
  and lazily imported so the rest of Chimera never depends on it.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from chimera.integrations.connectors import Connector
from chimera.telemetry import get_logger
from chimera.tools.base import Tool, tool_raised

_log = get_logger("integrations.mcp")

_TOOL_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


def valid_tool_name(name: str) -> bool:
    """Whether ``name`` may be registered: 1-64 characters of letters, digits and ``_ . : -``."""
    return _TOOL_NAME.fullmatch(name) is not None


@dataclass
class MCPToolSpec:
    """A tool description advertised by an MCP server."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})


class MCPSession(Protocol):
    """Anything that can list and call MCP tools (real or fake)."""

    def list_tools(self) -> list[MCPToolSpec]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str: ...


class MCPTool(Tool):
    """A Chimera tool that proxies to a tool on an MCP server."""

    #: Output comes from a remote MCP server — the most untrusted content in the system. The name is
    #: chosen by the server, so it won't be in the static FETCH_TOOLS set; this marker tells
    #: ``LedgeredTool`` to taint-track it regardless of name.
    untrusted_output = True

    #: The arguments go to the server verbatim and the server validates them against its own schema,
    #: where an unlisted name is allowed unless ``additionalProperties`` is false (JSON Schema's
    #: default). Refusing them here would second-guess a validator that exists, with a stricter rule
    #: than the one the server published. An OpenAPI connector is the opposite case and stays closed:
    #: its schema is generated here and its ``run`` keeps only the path, query and body names.
    open_arguments = True

    def __init__(
        self,
        spec: MCPToolSpec,
        caller: Callable[[str, dict[str, Any]], str],
        *,
        name_prefix: str = "",
        error_text_mode: str | None = None,
    ) -> None:
        self.name = f"{name_prefix}{spec.name}"
        self.description = spec.description
        self.parameters = spec.input_schema or {"type": "object", "properties": {}}
        self._remote_name = spec.name
        self._caller = caller
        self._error_text_mode = error_text_mode

    def run(self, **kwargs: Any) -> str:
        """Call the remote tool and hand back its output as DATA, never as instructions.

        Fenced here, in the tool, exactly as ``scrape``/``crawl``/``extract`` do — and for a
        stronger reason than any of them, since this content comes from a server we did not write
        and did not audit.

        It was fenced only inside ``LedgeredTool``, which is reached only when ``guard_chat`` is on,
        and ``guard_chat`` then defaulted to False. So in every default configuration the output of
        a remote MCP server reached the model raw: no fence, no defanging of chat-template tokens,
        while the MCP screen stated the opposite as a property of the app. The ``untrusted_output``
        marker was doing half its job — the half that needs a ledger — and nothing at all on the
        surfaces that have no ledger, which is the surfaces MCP tools actually live on.

        That default flipped on 2026-09-10 and this fence stays exactly where it is. The surfaces
        with no ledger did not go away — ``/v1/chat/completions``, ``chimera serve`` under the
        shipped governance mode, every bench — and a fence that lived in the guard would be a fence
        those surfaces still did not have.

        The defanging runs BEFORE the fence, same order and same reason as the ledger's: content
        that can emit a chat-template token could otherwise spoof a system turn and step out of the
        data region rather than merely sitting inside it.

        Under ``guard_chat`` this is fenced again by the ledger. That is what already happens to
        every native web tool, and it is safe: ``fence`` neutralises any marker in what it wraps, so
        the outer fence holds. Skipping the second one by INSPECTING the string would not be safe —
        text that merely starts and ends with the markers can still carry a live instruction between
        two fenced blocks.

        A failure stays a failure through the fence (``fence_observation``). Fencing ``error: …``
        whole had undone the ``isError`` handling in ``StdioMCPSession.call_tool``: the answer began
        with the fence, so the loop read "connection refused" as a call that ran, on every surface.

        A raise is fenced the same way. The server writes the message of a JSON-RPC error, and on a
        surface with no ledger it reached the model raw from `Agent._run_tool`: the one reply of a
        server's that this fence did not cover.
        """
        from chimera.governance.ledger_tool import fence_observation

        if self._error_text_mode is None:
            # The cached settings: a fresh `Settings()` re-read `.env` from disk on every MCP call.
            from chimera.config import get_settings

            error_text_mode: str = get_settings().mcp_error_text_mode
        else:
            error_text_mode = self._error_text_mode

        try:
            result = self._caller(self._remote_name, kwargs)
        except Exception as exc:  # noqa: BLE001 — a server's failure is an answer, fenced like one
            _log.warning("MCP tool %s failed (%s)", self.name, type(exc).__name__)
            return fence_observation(
                tool_raised(self.name, exc), error_text_mode=error_text_mode
            )
        return (
            fence_observation(result, error_text_mode=error_text_mode)
            if result.strip()
            else result
        )


class MCPConnector(Connector):
    """Exposes an MCP server's tools as Chimera tools."""

    def __init__(self, name: str, session: MCPSession, *, name_prefix: str = "") -> None:
        self.name = name
        self._session = session
        self._name_prefix = name_prefix

    def tools(self) -> list[Tool]:
        """The server's tools, minus any whose advertised name is not a plain identifier.

        The name is the one string a server we did not write puts OUTSIDE the data fence: it goes
        into the tool list the model reads, leads the action on every card a person approves
        (``render_action``) and the audit line. Nothing checked it, so a server could name a tool
        with a newline and a sentence. Study 24, S5 (the charset is the one the plan registered).

        Only the ADVERTISED name is checked, not the prefix: the prefix is the server's name in our
        own config, typed by the person who added it, and it may hold a space. Checking the joined
        name would have dropped every tool of a server called "GitHub Tools".

        A tool that fails is dropped and logged rather than renamed: a renamed tool is a name the
        server never advertised, and calling it would need a mapping nobody can audit.
        """
        tools: list[Tool] = []
        for spec in self._session.list_tools():
            if not valid_tool_name(spec.name):
                _log.warning("MCP server %r advertised a tool with an invalid name (%r); not registered",
                             self.name, spec.name[:80])
                continue
            tools.append(MCPTool(spec, self._session.call_tool, name_prefix=self._name_prefix))
        return tools


#: How much of a server-chosen label (a MIME type, a URI, a name) goes into a placeholder. These are
#: the server's strings, so they are bounded like any other; a URI longer than this is still named.
_LABEL_CHARS = 200


def _label(value: Any) -> str:
    return str(value or "")[:_LABEL_CHARS]


def _b64_bytes(data: Any) -> int:
    """The decoded size of a base64 payload, computed from its length; nothing is decoded.

    Whitespace is not data: MIME-style base64 breaks a line every 76 characters, and counting the
    newlines overstated a 3 000-byte image as about 3 029 bytes.
    """
    text = "".join(str(data or "").split())
    return max(len(text) * 3 // 4 - text[-2:].count("="), 0)


def _block_to_text(block: Any) -> tuple[str, bool]:
    """One content block as text the model can read, and whether it was a text block.

    Never ``str(block)``. That was the old fallback for every block without a ``text``, and on a
    pydantic block it is the repr: an image arrived as its whole base64 payload — tokens the model
    cannot read, and a screenshot is hundreds of thousands of them — and a resource arrived buried
    in field names. A block we do not know is named by its type, because its fields are the
    server's and we have no idea which of them is a payload.
    """
    kind = getattr(block, "type", None)
    text = getattr(block, "text", None)
    if isinstance(text, str) and kind in (None, "text"):
        return text, True
    if kind in ("image", "audio"):
        return f"[{kind} {_label(getattr(block, 'mimeType', ''))}, "\
               f"{_b64_bytes(getattr(block, 'data', ''))} bytes]", False
    if kind == "resource":
        resource = getattr(block, "resource", None)
        uri = _label(getattr(resource, "uri", ""))
        body = getattr(resource, "text", None)
        if isinstance(body, str):
            return f"[resource {uri}]\n{body}", False
        return (f"[resource {uri} {_label(getattr(resource, 'mimeType', ''))}, "
                f"{_b64_bytes(getattr(resource, 'blob', ''))} bytes]"), False
    if kind == "resource_link":
        return f"[resource link {_label(getattr(block, 'uri', ''))} "\
               f"({_label(getattr(block, 'name', ''))})]", False
    return f"[{_label(kind) or 'unknown'} content, not shown]", False


def _serialised_in(structured: Any, texts: list[str]) -> bool:
    """Whether one of the text blocks already IS ``structured``, serialised.

    Compared as parsed JSON, so key order and spacing do not matter. Anything that does not parse
    (a human summary such as "Done, 3 rows", an empty block) is not the serialisation.
    """
    for text in texts:
        try:
            if json.loads(text) == structured:
                return True
        except ValueError:  # JSONDecodeError is a ValueError
            continue
    return False


def _cap_output(text: str, max_output_chars: int | None) -> str:
    """Bound the text a server can hand to the model, while making the loss explicit."""
    if max_output_chars is None or len(text) <= max_output_chars:
        return text
    prefix = "error: " if text.startswith("error: ") else ""
    retained = max(0, max_output_chars - len(prefix))
    return prefix + text[len(prefix):len(prefix) + retained] + (
        f"\n... [truncated, {len(text)} chars total; limit {max_output_chars} chars]"
    )



def _content_to_text(result: Any) -> str:
    """Flatten an MCP CallToolResult into the text the model reads.

    ``structuredContent`` is appended unless a text block already carries it serialised. The spec
    asks a server that returns structured content to send it in a text block as well, and when it
    does, appending it again would hand the model the same answer twice. Not every server does:
    some send a human summary ("Done, 3 rows") as the text and the rows only as structured content,
    and dropping the structured content whenever any text came hid the rows from the model. With
    no content at all, the old code returned an empty string for a tool that had answered.
    """
    parts: list[str] = []
    texts: list[str] = []
    for block in getattr(result, "content", []) or []:
        part, is_text = _block_to_text(block)
        parts.append(part)
        if is_text:
            texts.append(part)
    structured = getattr(result, "structuredContent", None)
    if structured is not None and not _serialised_in(structured, texts):
        parts.append(json.dumps(structured, ensure_ascii=False, default=str))
    return "\n".join(parts)


class StreamableHTTPMCPSession:
    """A live MCP session over streamable HTTP, using the optional MCP SDK transport."""

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        connect_timeout: float = 30.0,
        call_timeout: float = 120.0,
        catalog_timeout: float = 30.0,
        max_output_chars: int | None = None,
    ) -> None:
        self.url = url
        self.headers = headers or {}
        self.connect_timeout = connect_timeout
        self.call_timeout = call_timeout
        self.catalog_timeout = catalog_timeout
        self.max_output_chars = max_output_chars
        self._loop: Any = None
        self._thread: Any = None
        self._session: Any = None
        self._stop_event: Any = None
        self._serve_future: Any = None
        self._serve_task: Any = None
        self._ready = threading.Event()
        self._connect_error: Exception | None = None
        self._closing = False
        #: An ``httpx.Auth`` that puts a stored OAuth token on each request (refreshing it), or None.
        self.auth: Any = None

    @classmethod
    def from_config(
        cls,
        cfg: Any,
        *,
        connect_timeout: float = 30.0,
        call_timeout: float = 120.0,
        catalog_timeout: float = 30.0,
        max_output_chars: int | None = None,
        home: Any = None,
    ) -> StreamableHTTPMCPSession:
        """Resolve a configured environment token or a stored OAuth sign-in. Never signs in.

        A sign-in opens a browser, and only an owner at the app's Connect or at ``chimera mcp
        login`` may start one (:func:`chimera.integrations.mcp_oauth.login`). Every connect that
        comes through here — the pool, autoload, ``serve``, the bots, cron and the screen's Test —
        uses a stored sign-in, refreshing it when it expires, or refuses with
        :class:`~chimera.integrations.mcp_oauth.SignInRequired`. Until 2026-10-17 the Test opened
        the browser itself, and the Test is reachable through the desktop bridge: an agent driving
        the app could start a sign-in on the owner's screen.
        """
        import os
        from pathlib import Path

        headers: dict[str, str] = dict(cfg.headers)
        auth: Any = None
        if cfg.token_env:
            _require_safe_transport("url", cfg.url)
            token = os.environ.get(cfg.token_env, "")
            if not token:
                raise ValueError(f"MCP bearer token environment variable {cfg.token_env!r} is not set")
            headers["Authorization"] = f"Bearer {token}"
        else:
            from chimera.integrations import mcp_oauth

            if home is None:
                from chimera.config import get_settings

                home = get_settings().home
            home = Path(home)
            if mcp_oauth.has_record(cfg, home):
                _require_safe_transport("url", cfg.url)
                # Checked here as well as on every request, so a dead sign-in is said before the
                # transport is started rather than as a connection failure from inside it.
                if mcp_oauth.access_token_for(cfg, home) is None:
                    raise mcp_oauth.SignInRequired(cfg.name)
                auth = mcp_oauth.StoredTokenAuth(cfg, home)
            elif cfg.oauth_authorization_url:
                _require_safe_transport("url", cfg.url)
                from chimera.config_vault import read_mcp_token

                # The single token the pre-registered path stored before records existed.
                legacy = read_mcp_token(cfg.name)
                if not legacy:
                    raise mcp_oauth.SignInRequired(cfg.name)
                headers["Authorization"] = f"Bearer {legacy}"
        session = cls(
            cfg.url,
            headers=headers,
            connect_timeout=connect_timeout,
            call_timeout=call_timeout,
            catalog_timeout=catalog_timeout,
            max_output_chars=(
                max_output_chars if max_output_chars is not None else cfg.max_output_chars
            ),
        )
        session.auth = auth
        return session

    def start(self) -> StreamableHTTPMCPSession:
        import asyncio

        try:
            import mcp  # noqa: F401
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "the MCP SDK is not installed — install it with: pip install 'chimera-agent[mcp]'"
            ) from exc
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._serve_future = asyncio.run_coroutine_threadsafe(self._run_serve(), self._loop)
        if not self._ready.wait(timeout=self.connect_timeout):
            self.close()
            raise TimeoutError("MCP HTTP server did not become ready")
        if self._connect_error is not None:
            self.close()
            raise RuntimeError(f"MCP HTTP connection failed ({type(self._connect_error).__name__})")
        return self

    def _run_loop(self) -> None:
        import asyncio

        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _run_serve(self) -> None:
        import asyncio
        from contextlib import AsyncExitStack

        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        self._serve_task = asyncio.current_task()
        self._stop_event = asyncio.Event()
        try:
            async with AsyncExitStack() as stack:
                read, write, _ = await stack.enter_async_context(
                    streamablehttp_client(
                        self.url, headers=self.headers, timeout=self.connect_timeout, auth=self.auth
                    )
                )
                self._session = await stack.enter_async_context(ClientSession(read, write))
                await self._session.initialize()
                _log.debug("MCP streamable HTTP session connected")
                self._ready.set()
                await self._stop_event.wait()
        except Exception as exc:  # noqa: BLE001 — never log remote exception text or headers
            self._connect_error = exc
            self._ready.set()

    def list_tools(self) -> list[MCPToolSpec]:
        import asyncio

        try:
            result = asyncio.run_coroutine_threadsafe(self._session.list_tools(), self._loop).result(
                timeout=self.catalog_timeout
            )
        except Exception as exc:  # noqa: BLE001 — SDK errors can contain request headers
            raise RuntimeError(f"MCP HTTP list_tools failed ({type(exc).__name__})") from None
        return [MCPToolSpec(tool.name, tool.description or "", tool.inputSchema or
                            {"type": "object", "properties": {}}) for tool in result.tools]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        import asyncio

        try:
            result = asyncio.run_coroutine_threadsafe(
                self._session.call_tool(name, arguments), self._loop
            ).result(timeout=self.call_timeout)
        except Exception as exc:  # noqa: BLE001 — SDK errors can contain request headers
            raise RuntimeError(f"MCP HTTP tool call failed ({type(exc).__name__})") from None
        text = _content_to_text(result)
        if getattr(result, "isError", False):
            text = f"error: {text or 'MCP tool reported a failure'}"
        return _cap_output(text, self.max_output_chars)

    def close(self) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._shutdown)
        if self._serve_future is not None:
            from contextlib import suppress

            with suppress(Exception):
                self._serve_future.result(timeout=10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _shutdown(self) -> None:
        self._closing = True
        if self._stop_event is not None:
            self._stop_event.set()
        if not self._ready.is_set() and self._serve_task is not None:
            self._serve_task.cancel()


def _require_safe_transport(label: str, url: str | None) -> None:
    """Refuse to send a credential over cleartext HTTP to anything but this machine.

    A bearer token, an authorization code or a PKCE verifier sent over ``http://`` to a remote host
    is readable by every hop between here and there: the vault keeps it encrypted at rest only to
    have it travel in the clear. Loopback stays allowed, because it never leaves the machine and is
    how a local server and the test suite run.
    """
    from urllib.parse import urlsplit

    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and host:
        return
    if parts.scheme == "http" and host in ("127.0.0.1", "localhost", "::1"):
        return
    raise ValueError(f"MCP {label} must be https (or http on loopback) to carry a credential")


class StdioMCPSession:
    """A live MCP session over stdio (requires the optional ``mcp`` package).

    Runs the async MCP client on a dedicated background event loop so the rest of
    Chimera can call ``list_tools``/``call_tool`` synchronously.
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        *,
        connect_timeout: float = 30.0,
        call_timeout: float = 120.0,
        catalog_timeout: float = 30.0,
        max_output_chars: int | None = None,
        cwd: str | None = None,
    ) -> None:
        self.command = command
        self.args = args or []
        self.env = env
        self.connect_timeout = connect_timeout
        self.call_timeout = call_timeout
        self.catalog_timeout = catalog_timeout
        self.cwd = cwd
        self.max_output_chars = max_output_chars
        self._loop: Any = None
        self._thread: Any = None
        self._session: Any = None
        self._stop_event: Any = None
        self._serve_future: Any = None
        self._connect_error: Exception | None = None
        self._ready = threading.Event()
        # The task running `_serve`, and whether close() has been asked for: what close() needs to
        # stop a session that never became ready (see close()).
        self._serve_task: Any = None
        self._closing = False

    def start(self) -> StdioMCPSession:
        import asyncio

        # Checked HERE, before anything is spawned, and the reason is a message a user actually
        # got. The SDK is imported inside `_serve`, which runs on the background loop — so with the
        # package absent the ImportError was raised where nothing was listening, `_ready` never
        # fired, and `start` reported "did not become ready". That is the SAME sentence a command
        # which simply is not an MCP server produces, so somebody whose install lacked the optional
        # dependency was told to go and check their configuration.
        #
        # Measured against the packaged desktop app, where the extra was not bundled at all: every
        # server in the catalogue failed this way, and nothing on screen could say why.
        try:
            import mcp  # noqa: F401
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "the MCP SDK is not installed — install it with: pip install 'chimera-agent[mcp]'"
            ) from exc

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        # The session lives entirely inside _serve (one task) so the stdio client's
        # anyio cancel scopes are entered and exited in the same task.
        self._serve_future = asyncio.run_coroutine_threadsafe(self._run_serve(), self._loop)
        # A start that fails closes what it started, HERE rather than in each caller. The server has
        # already been spawned by the time the wait gives up, and a caller that writes
        # `StdioMCPSession(...).start()` never holds the session it would have to close: the probe
        # learned to wrap it in try/finally, and the pool, autoload and connect_stdio did not, so a
        # bridge waiting on a browser sign-in outlived a timed-out boot connect for the life of the
        # app. Closing on a connect error too stops the loop thread the failure left running.
        if not self._ready.wait(timeout=self.connect_timeout):
            self.close()
            raise TimeoutError(f"MCP server '{self.command}' did not become ready")
        if self._connect_error is not None:
            self.close()
            raise self._connect_error
        return self

    def _run_loop(self) -> None:
        import asyncio

        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _run_serve(self) -> None:
        import asyncio

        # Recorded so close() can cancel a connect that is still in progress. A close that lands
        # before this task first runs finds no task to cancel, so it leaves `_closing` instead.
        self._serve_task = asyncio.current_task()
        if self._closing:
            return
        await self._serve()

    async def _serve(self) -> None:
        import asyncio
        from contextlib import AsyncExitStack

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._stop_event = asyncio.Event()
        try:
            async with AsyncExitStack() as stack:
                params = StdioServerParameters(
                    command=self.command, args=self.args, env=self.env, cwd=self.cwd
                )
                read, write = await stack.enter_async_context(stdio_client(params))
                self._session = await stack.enter_async_context(ClientSession(read, write))
                # The InitializeResult (and its server `instructions`) is dropped on purpose: it is
                # untrusted server text and nothing fences it as data. Not a boundary, though — the
                # same server's tool descriptions reach the model unfenced in list_tools(). Recorded
                # in docs/mcp.md (study 30, S30-21(h)); passing it fenced under taint is open.
                await self._session.initialize()
                _log.debug("MCP stdio session connected: %s", self.command)
                self._ready.set()
                await self._stop_event.wait()  # hold the streams open in THIS task
        except Exception as exc:  # noqa: BLE001 — surface connect failures to start()
            self._connect_error = exc
            self._ready.set()

    def list_tools(self) -> list[MCPToolSpec]:
        import asyncio

        future = asyncio.run_coroutine_threadsafe(self._session.list_tools(), self._loop)
        response = future.result(timeout=self.catalog_timeout)
        return [
            MCPToolSpec(
                name=tool.name,
                description=tool.description or "",
                input_schema=tool.inputSchema or {"type": "object", "properties": {}},
            )
            for tool in response.tools
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        import asyncio

        future = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(name, arguments), self._loop
        )
        result = future.result(timeout=self.call_timeout)
        text = _content_to_text(result)
        # Per the MCP spec a tool-execution failure comes back as a normal result with isError=True
        # (not a protocol error). Surface it as an `error:` observation so the agent can tell a real
        # answer from a failure — otherwise "database connection refused" reads as a valid result.
        if getattr(result, "isError", False):
            text = f"error: {text or 'MCP tool reported a failure'}"
        return _cap_output(text, self.max_output_chars)

    def close(self) -> None:
        # Signal _serve to exit; its AsyncExitStack then unwinds in its own task.
        #
        # Setting the stop event is enough only for a session that got as far as waiting on it. One
        # that timed out in start() is still inside the handshake (`initialize`, or a bridge waiting
        # for a browser sign-in), the event is never awaited, and the old close() waited ten
        # seconds and stopped the loop under it: the task was left suspended, its stdio_client never
        # exited, and the server's process stayed alive until the app closed. A Test that timed out
        # left one of those behind per click. So a session that is not ready is CANCELLED, which
        # unwinds the same AsyncExitStack from wherever the handshake was, and that is what ends the
        # subprocess.
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._shutdown)
        if self._serve_future is not None:
            from contextlib import suppress

            with suppress(Exception):  # best-effort teardown
                self._serve_future.result(timeout=10)
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _shutdown(self) -> None:
        """Runs ON the loop: ask a ready session to stop, and cancel one still connecting."""
        self._closing = True
        if self._stop_event is not None:
            self._stop_event.set()
        if not self._ready.is_set() and self._serve_task is not None:
            self._serve_task.cancel()


def connect_stdio(
    name: str,
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    *,
    name_prefix: str = "",
) -> MCPConnector:
    """Connect to an MCP server over stdio and return a connector."""
    session = StdioMCPSession(command, args, env).start()
    return MCPConnector(name, session, name_prefix=name_prefix)
