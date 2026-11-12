"""In a conversation that runs on another machine, the local file tools refuse instead of acting.

The SSH environment (:mod:`chimera.sandbox.ssh`) moves the agent's COMMANDS to a remote host. The
files it would read or edit are over there too, and this build has no remote file layer: the file
tools work on a folder of the machine running Chimera. Left in place they would be one wrong turn
from editing the local checkout while the agent believes it is changing the server. So each of them
answers with the same sentence and does nothing, and the sentence says what to do instead.

They stay registered (a tool that vanishes reads as "I cannot do that" and invites improvising);
their description says up front that they are unavailable here. Remote files are a later piece of
work and will replace this refusal for the tools that gain a remote form.
"""

from __future__ import annotations

from typing import Any

from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry

#: Every built-in tool that reads or writes a file of THIS machine, or runs a script written there.
LOCAL_FILE_TOOLS: frozenset[str] = frozenset({
    "read_file", "write_file", "edit_file", "apply_patch", "edit_batch", "list_dir", "grep", "glob",
    "read_document", "view_image", "download_media", "render_chart", "create_document",
    "generate_image", "text_to_speech", "generate_audio", "generate_video", "transcribe_audio",
    "open_pull_request", "execute_code",
    # Chimera Design: its `export` writes into the project folder of THIS machine.
    "design",
})


def refusal(host: str) -> str:
    return (
        f"error: this conversation runs on {host}; use run_shell to read or edit files there. "
        "This tool works on a folder of the machine running Chimera, not on the remote host, so it "
        "was not run."
    )


def is_remote(sandbox: object) -> bool:
    """Whether ``sandbox`` runs commands on another machine (an SSH environment)."""
    from chimera.sandbox.ssh import SshSandbox

    return isinstance(sandbox, SshSandbox)


class RefusedLocalFileTool(Tool):
    """A local file tool, kept by name and schema, that says why it does not run here."""

    def __init__(self, original: Tool, host: str) -> None:
        self.name = original.name
        self.parameters = original.parameters
        self.description = (
            f"UNAVAILABLE in this conversation, which runs on {host}: files are on that machine. "
            "Use run_shell there instead. " + original.description
        )
        self._host = host

    def run(self, **kwargs: Any) -> str:
        return refusal(self._host)


def block_local_file_tools(registry: ToolRegistry, host: str) -> list[str]:
    """Replace each local file tool in ``registry`` with a refusing one; return the names replaced."""
    replaced: list[str] = []
    for name in sorted(LOCAL_FILE_TOOLS & set(registry.names())):
        registry.register(RefusedLocalFileTool(registry.get(name), host), replace=True)
        replaced.append(name)
    return replaced
