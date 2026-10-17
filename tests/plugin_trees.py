"""Real Claude Code plugin trees for the install tests — written to disk, nothing downloaded.

The shapes are the ones Claude Code documents (https://code.claude.com/docs/en/plugins-reference):
a manifest, `skills/<name>/SKILL.md`, `commands/*.md`, `agents/*.md`, `hooks/hooks.json`, `.mcp.json`
and `bin/`. Every executable part is booby-trapped: if anything ran it, a marker file would appear.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

#: What a hook, a monitor, a `bin/` script or a skill's `!` line would create if it were ever run.
MARKER = "PWNED"


def write(root: Path, rel: str, content: str | bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def full_plugin(parent: Path, name: str = "my-plugin") -> Path:
    """A plugin with one of everything, ``defaultEnabled: true`` (which Chimera ignores)."""
    root = parent / name
    marker = (parent / MARKER).as_posix()
    write(root, ".claude-plugin/plugin.json", json.dumps({
        "name": name, "version": "1.0.0", "description": "Everything a plugin can bring.",
        "author": {"name": "Someone Else"}, "license": "MIT", "defaultEnabled": True,
        "userConfig": {"api_key": {"type": "string", "title": "API key", "description": "The key.",
                                   "sensitive": True}},
    }))
    write(root, "skills/greet/SKILL.md",
          "---\nname: greet\ndescription: Greets people warmly.\nallowed-tools: Bash(touch:*)\n---\n"
          f"Before greeting, the context is:\n!`touch {marker}-skill`\n\nThen say hello.\n")
    write(root, "commands/hello.md",
          "---\ndescription: Say hello\nallowed-tools: Bash\nargument-hint: <who>\nmodel: claude-opus-4\n---\n"
          "Say hello to $ARGUMENTS.\n")
    write(root, "agents/reviewer.md",
          "---\nname: reviewer\ndescription: Reviews code.\ntools: read_file, Bash\nmodel: opus\n---\n"
          "You review code carefully.\n")
    write(root, "hooks/hooks.json", json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": f"touch {marker}-hook"}]},
    ]}}))
    write(root, ".mcp.json", json.dumps({"mcpServers": {
        "files": {"command": "${CLAUDE_PLUGIN_ROOT}/server.sh", "args": ["--root", "${CLAUDE_PLUGIN_ROOT}"],
                  "env": {"TOKEN": "${MY_TOKEN}"}},
        "configured": {"command": "node", "args": ["--key", "${user_config.api_key}"]},
    }}))
    write(root, "server.sh", f"#!/bin/sh\ntouch {marker}-server\n")
    write(root, "bin/tool.sh", f"#!/bin/sh\ntouch {marker}-bin\n")
    return root


def text_plugin(parent: Path, name: str = "notes") -> Path:
    """Instructions only: one skill and one command."""
    root = parent / name
    write(root, ".claude-plugin/plugin.json", json.dumps({
        "name": name, "version": "0.1.0", "description": "Only words.", "author": {"name": "A"},
    }))
    write(root, "skills/summarise/SKILL.md", "---\nname: summarise\ndescription: Summarises.\n---\nSummarise.\n")
    write(root, "commands/brief.md", "---\ndescription: A brief\n---\nWrite a brief about $1.\n")
    return root


def zipped(root: Path, *, top: str = "plugin-abc123", extra: dict[str, bytes] | None = None) -> bytes:
    """A GitHub-style archive of ``root``: every file under one top folder."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            prefix = f"{top}/" if top else ""
            archive.writestr(f"{prefix}{path.relative_to(root).as_posix()}", path.read_bytes())
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()
