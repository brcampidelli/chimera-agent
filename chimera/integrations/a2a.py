"""A2A (Agent2Agent) adapter — let Chimera exchange tasks with other agents.

A2A won the agent->agent layer (Linux Foundation, native in LangGraph/CrewAI/AutoGen). Two
pieces make an agent A2A-reachable: an **Agent Card** (a JSON document advertising identity +
skills at ``/.well-known/agent.json``) and a **task lifecycle** over JSON-RPC (``message/send``
creates a task; ``tasks/get`` polls it; ``tasks/cancel`` stops it). This module implements both
against the injected Chimera ``solve`` callable — dependency-free and pure, so it's fully
unit-testable and the HTTP transport is a thin wrapper.

Scope, honestly: this is the synchronous core of the spec — agent card, task states, and the
three core JSON-RPC methods. Streaming (``message/stream``) and push notifications are out of
scope for now; a caller polls ``tasks/get``. That's enough to be reached by a LangGraph/CrewAI
client and hand back a completed task.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = [
    "AgentSkill",
    "chimera_agent_card",
    "TaskState",
    "A2ATask",
    "A2AServer",
]

# A2A task states (subset used by the synchronous flow).
TaskState = Literal["submitted", "working", "completed", "failed", "canceled"]

_JSONRPC_METHOD_NOT_FOUND = -32601
_JSONRPC_INVALID_PARAMS = -32602
_A2A_TASK_NOT_FOUND = -32001


@dataclass
class AgentSkill:
    """One advertised capability on the agent card."""

    id: str
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "examples": self.examples,
        }


def chimera_agent_card(url: str, *, version: str, name: str = "Chimera") -> dict[str, Any]:
    """The A2A Agent Card for this Chimera instance (served at /.well-known/agent.json).

    ``url`` is the base endpoint other agents POST JSON-RPC to. Skills mirror the MCP tools —
    the same engine, advertised to the agent->agent layer instead of the agent->tool one.
    """
    skills = [
        AgentSkill(
            id="solve",
            name="Autonomous solve",
            description="Solve a task with plan + verify-or-revert; returns the final answer.",
            tags=["autonomous", "agentic", "verify"],
            examples=["Refactor this module and keep the tests green."],
        ),
        AgentSkill(
            id="fuse",
            name="LLM-Fusion answer",
            description="Answer through a panel -> judge -> synthesizer for hard reasoning.",
            tags=["fusion", "reasoning"],
            examples=["Compare two architectures and recommend one, with trade-offs."],
        ),
    ]
    return {
        "protocolVersion": "0.2.0",
        "name": name,
        "description": "A self-evolving agent with governance and LLM-Fusion. Evolves, measurably.",
        "url": url,
        "version": version,
        "capabilities": {"streaming": True, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [skill.to_dict() for skill in skills],
    }


def _text_of(message: dict[str, Any]) -> str:
    """Concatenate the text parts of an A2A message (ignoring non-text parts)."""
    parts = message.get("parts", []) if isinstance(message, dict) else []
    chunks = [
        str(part.get("text", ""))
        for part in parts
        if isinstance(part, dict) and part.get("kind", "text") == "text"
    ]
    return "\n".join(c for c in chunks if c).strip()


def _agent_message(text: str) -> dict[str, Any]:
    return {
        "role": "agent",
        "parts": [{"kind": "text", "text": text}],
        "messageId": uuid.uuid4().hex,
        "kind": "message",
    }


@dataclass
class A2ATask:
    """An A2A task: an id, a context, a state, and the message history."""

    id: str
    context_id: str
    state: TaskState = "submitted"
    history: list[dict[str, Any]] = field(default_factory=list)
    result_message: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        status: dict[str, Any] = {"state": self.state}
        if self.result_message is not None:
            status["message"] = self.result_message
        return {
            "id": self.id,
            "contextId": self.context_id,
            "status": status,
            "history": self.history,
            "kind": "task",
        }


class A2AServer:
    """Handles A2A JSON-RPC against an injected ``solve`` callable.

    ``solve(text) -> answer``. ``message/send`` runs it synchronously and returns a completed
    (or failed) task; the task is kept in-memory so ``tasks/get`` can fetch it afterwards.
    """

    def __init__(self, solve: Callable[[str], str]) -> None:
        self._solve = solve
        self._tasks: dict[str, A2ATask] = {}

    def _new_task(self, params: dict[str, Any]) -> tuple[A2ATask, str]:
        """Validate the message and register a fresh working task. Raises ValueError if empty."""
        message = params.get("message")
        if not isinstance(message, dict):
            raise ValueError("params.message is required")
        text = _text_of(message)
        if not text:
            raise ValueError("message has no text part")
        task = A2ATask(
            id=uuid.uuid4().hex,
            context_id=str(params.get("contextId") or uuid.uuid4().hex),
            history=[message],
            state="working",
        )
        self._tasks[task.id] = task
        return task, text

    def _run_into(self, task: A2ATask, text: str) -> None:
        """Run solve synchronously and fold the result (or failure) into ``task``."""
        try:
            answer = self._solve(text)
        except Exception as exc:  # noqa: BLE001 — a failed run is a failed task, not a crash
            task.state = "failed"
            task.result_message = _agent_message(f"error: {exc}")
        else:
            task.state = "completed"
            task.result_message = _agent_message(answer)
        if task.result_message is not None:
            task.history.append(task.result_message)

    def message_send(self, params: dict[str, Any]) -> dict[str, Any]:
        task, text = self._new_task(params)
        self._run_into(task, text)
        return task.to_dict()

    def stream(self, request: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """JSON-RPC ``message/stream``: yield SSE payloads (working task -> final task).

        A minimal but conformant stream: the caller receives the task in ``working`` state
        immediately, then the completed/failed task once solve returns. Each payload is a full
        JSON-RPC response carrying the request id, so an A2A client tracks it by ``taskId``.
        """
        req_id = request.get("id")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            yield self._error(req_id, _JSONRPC_INVALID_PARAMS, "params must be an object")
            return
        try:
            task, text = self._new_task(params)
        except ValueError as exc:
            yield self._error(req_id, _JSONRPC_INVALID_PARAMS, str(exc))
            return
        yield {"jsonrpc": "2.0", "id": req_id, "result": task.to_dict()}  # initial: working
        self._run_into(task, text)
        yield {"jsonrpc": "2.0", "id": req_id, "result": task.to_dict()}  # final: completed/failed

    def tasks_get(self, params: dict[str, Any]) -> dict[str, Any]:
        task = self._tasks.get(str(params.get("id", "")))
        if task is None:
            raise KeyError("task not found")
        return task.to_dict()

    def tasks_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        task = self._tasks.get(str(params.get("id", "")))
        if task is None:
            raise KeyError("task not found")
        if task.state in ("submitted", "working"):
            task.state = "canceled"
        return task.to_dict()

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        """Route one JSON-RPC 2.0 request to the matching method; return a JSON-RPC response."""
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            return self._error(req_id, _JSONRPC_INVALID_PARAMS, "params must be an object")
        handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "message/send": self.message_send,
            "tasks/get": self.tasks_get,
            "tasks/cancel": self.tasks_cancel,
        }
        handler = handlers.get(str(method))
        if handler is None:
            return self._error(req_id, _JSONRPC_METHOD_NOT_FOUND, f"unknown method {method!r}")
        try:
            result = handler(params)
        except KeyError as exc:
            return self._error(req_id, _A2A_TASK_NOT_FOUND, str(exc))
        except ValueError as exc:
            return self._error(req_id, _JSONRPC_INVALID_PARAMS, str(exc))
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
