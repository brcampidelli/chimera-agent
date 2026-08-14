"""Talking to somebody else's coding agent.

The Agent Client Protocol (ACP, from Zed) is JSON-RPC 2.0 over a child process's stdio. It is how
an editor drives a coding agent it did not write — Claude Code, Codex, Gemini CLI — and this package
is Chimera on the CLIENT side of it: we are the editor, the other agent is the worker.

**Why be a client at all.** Chimera's value is not that its loop is the only loop. It is the
governance around a loop — the taint ledger, the write region, the checkpoint, the verifier, the
receipt. Those apply to any worker, and refusing to drive a worker somebody already trusts would be
insisting on the least interesting half of the product.

**And the mirror.** :mod:`chimera.acp.server` is the same protocol from the other side: `chimera acp`
speaks it on stdio so an editor that did not write Chimera can drive it. Distribution rather than
capability — nothing there makes the agent better, it changes who can reach one.

**What this cannot promise, and says so.** An ACP agent declares which client capabilities it will
use, and we declare which we offer — but the agents worth driving have file and shell tools of their
own. Claude Code writes through the Claude Agent SDK, not necessarily through our `fs/write_text_file`
handler. So a write region cannot *prevent* a write here the way it does for a native run. What still
holds is the checkpoint and the revert: we know what the workspace looked like before, and we can put
it back. That is a real guarantee and a smaller one, and :mod:`chimera.api.posture` has to say the
smaller thing.
"""

from chimera.acp.agents import AcpAgentSpec, available_agents, spec_for
from chimera.acp.client import AcpConnection, AcpError
from chimera.acp.server import AcpServer
from chimera.acp.turn import AcpTurn, AcpTurnResult

__all__ = [
    "AcpAgentSpec",
    "AcpConnection",
    "AcpError",
    "AcpServer",
    "AcpTurn",
    "AcpTurnResult",
    "available_agents",
    "spec_for",
]
