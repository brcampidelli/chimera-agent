"""Bounded file refreshes after context compaction.

Only the current workspace is read. The run state keeps target names and fingerprints, never source
contents, so each refresh reflects disk rather than a stale snapshot.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from chimera.core.context_budget import MAX_PROGRESS_RECORDS, RunState, estimate_tokens
from chimera.tools.workspace import resolve_in_workspace

MAX_REORIENT_FILES = 5
MAX_FILE_TOKENS = 5_000
MAX_REORIENTATION_TOKENS = 7_000

def reorientation_message(state: RunState, workspace: Path | None) -> str:
    """Read prioritized current files into a size-bounded note; return empty without candidates."""
    if workspace is None:
        return ""
    candidates: list[str] = []
    if state.open_file:
        candidates.append(state.open_file[0])
    candidates.extend(reversed(state.write_progress))
    candidates.extend(
        item.split(" — ", 1)[0]
        for item in reversed(state.read_progress)
    )
    ordered = list(dict.fromkeys(path for path in candidates if path))[:MAX_REORIENT_FILES]
    if not ordered:
        return ""

    known_hashes: dict[str, str] = {}
    for item in state.read_progress:
        target, separator, metadata = item.partition(" — ")
        marker = "sha256:"
        if separator and marker in metadata:
            known_hashes[target] = metadata.split(marker, 1)[1][:12]

    blocks = ["Context reorientation — use this to resume the current task."]
    task = state.task[:2_000] if state.task else ""
    if task:
        blocks.append(f"Task: {task}")
    if state.write_progress:
        blocks.append(
            "Files already written this turn: "
            + ", ".join(state.write_progress[-MAX_PROGRESS_RECORDS:])[:2_000]
        )
    if state.read_progress:
        blocks.append(
            "Successful reads: "
            + "; ".join(state.read_progress[-MAX_PROGRESS_RECORDS:])[:2_000]
        )
    budget_left = max(
        0,
        MAX_REORIENTATION_TOKENS
        - estimate_tokens([{"role": "user", "content": "\n".join(blocks)}]),
    )
    if state.open_file and state.open_file[0] not in ordered:
        open_file_text = state.open_file[1]
        open_tokens = estimate_tokens([{"role": "user", "content": open_file_text}])
        if open_tokens <= MAX_FILE_TOKENS and open_tokens <= budget_left:
            blocks.append(f"\n--- {state.open_file[0]} (saved edit context) ---\n{open_file_text}")
            budget_left -= open_tokens
    for target in ordered:
        try:
            path = resolve_in_workspace(workspace, target)
            if not path.is_file():
                continue
            if path.stat().st_size > MAX_FILE_TOKENS * 4:
                digest = sha256(path.read_bytes()).hexdigest()[:12]
                blocks.append(f"\\n{target} — sha256:{digest}; over {MAX_FILE_TOKENS} tokens, re-read as needed")
                continue
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError):
            continue
        digest = sha256(content.encode("utf-8", errors="replace")).hexdigest()[:12]
        previous = known_hashes.get(target)
        changed = previous is not None and previous != digest
        header = f"\n--- {target} (sha256:{digest}"
        if changed:
            header += "; changed since last read"
        elif state.open_file and state.open_file[0] == target:
            original_digest = sha256(
                state.open_file[1].encode("utf-8", errors="replace")
            ).hexdigest()[:12]
            if original_digest != digest:
                header += "; changed since edit context was recorded"
        header += ") ---"
        tokens = estimate_tokens([{"role": "user", "content": content}])
        if tokens > MAX_FILE_TOKENS or tokens > budget_left:
            blocks.append(f"\n{target} — {digest}; too large to refresh, re-read as needed")
        else:
            blocks.append(header + "\n" + content)
            budget_left -= tokens
    if len(blocks) == 1:
        return ""
    return "\n".join(blocks)
