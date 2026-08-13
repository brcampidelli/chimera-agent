"""A scripted ACP agent, for testing the client against a real conversation.

Run as a child process by :mod:`tests.test_acp_turn`. It speaks the protocol for real — JSON-RPC
frames on stdio, `initialize`, `session/new`, `session/prompt`, notifications and callbacks — but
what it *does* comes from a scenario handed to it in ``FAKE_ACP_SCRIPT``.

The alternative was mocking :class:`~chimera.acp.client.AcpConnection`, which would have tested the
mock. Everything worth checking here is an interaction: a notification arriving mid-call, a callback
issued while our own request is outstanding, an agent that dies without answering. A fake that talks
can do all three; a stubbed method cannot do any.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

#: Scenario actions, in order, performed while a `session/prompt` is in flight.
#:
#: ``notify``  — send one `session/update` with this payload as the `update` object
#: ``call``    — call a method back on the client and keep the answer
#: ``stop``    — the `stopReason` to answer the prompt with (default "end_turn")
#: ``exit``    — die with this code instead of answering, to test a mid-turn death
#: ``noise``   — print a non-JSON line, the way npm and adapter banners do
#: ``sleep``   — pause, for the timeout and cancellation tests


def _send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


class FakeAgent:
    def __init__(self, script: list[dict[str, Any]]) -> None:
        self.script = script
        self.next_id = 1000
        self.session = "sess_fake"
        #: Answers to the calls we made back at the client, so a scenario can assert on them.
        self.answers: list[Any] = []
        self.cancelled = False

    def call(self, method: str, params: dict[str, Any]) -> Any:
        """Call the client and block for its answer, reading past any frames that arrive first."""
        ident = self.next_id
        self.next_id += 1
        _send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})
        while True:
            line = sys.stdin.readline()
            if not line:
                return None
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == ident:
                if "error" in message:
                    return {"error": message["error"]}
                return message.get("result")
            self._maybe_cancel(message)

    def _maybe_cancel(self, message: dict[str, Any]) -> None:
        if message.get("method") == "session/cancel":
            self.cancelled = True

    def run_script(self) -> str:
        stop = "end_turn"
        for step in self.script:
            if "notify" in step:
                _send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {"sessionId": self.session, "update": step["notify"]},
                    }
                )
            elif "call" in step:
                spec = step["call"]
                self.answers.append(self.call(spec["method"], spec.get("params", {})))
            elif "noise" in step:
                sys.stdout.write(str(step["noise"]) + "\n")
                sys.stdout.flush()
            elif "sleep" in step:
                import time

                time.sleep(float(step["sleep"]))
            elif "exit" in step:
                sys.exit(int(step["exit"]))
            elif "stop" in step:
                stop = str(step["stop"])
            elif "echo_answers" in step:
                _send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": self.session,
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": json.dumps(self.answers)},
                            },
                        },
                    }
                )
        return stop

    def serve(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            method = message.get("method")
            ident = message.get("id")
            if method == "initialize":
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": ident,
                        "result": {
                            "protocolVersion": 1,
                            "agentCapabilities": {"loadSession": False},
                            "agentInfo": {"name": "fake", "version": "0"},
                            "authMethods": [],
                        },
                    }
                )
            elif method == "session/new":
                # Echoed back so a test can assert the cwd we were given is the workspace, absolute.
                _send({"jsonrpc": "2.0", "id": ident, "result": {"sessionId": self.session,
                                                                 "_cwd": message["params"].get("cwd")}})
            elif method == "session/prompt":
                stop = self.run_script()
                _send({"jsonrpc": "2.0", "id": ident, "result": {"stopReason": stop}})
            elif method == "session/cancel":
                self.cancelled = True
            elif ident is not None:
                _send({"jsonrpc": "2.0", "id": ident,
                       "error": {"code": -32601, "message": f"no such method: {method}"}})


def main() -> None:
    raw = os.environ.get("FAKE_ACP_SCRIPT", "[]")
    FakeAgent(json.loads(raw)).serve()


if __name__ == "__main__":
    main()
