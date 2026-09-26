"""The turn context: what is true for this turn, sent with it, and never kept.

Study 25, wave 2 (`bench/PLAN-study25-system-prompts.md` §5.1). A provider caches the longest prefix
two requests share. The system message used to carry text that changes from one turn to the next:
- recalled facts;
- a job that just finished;
- the approved plan;
- the skills retrieved for this task.

So the shared prefix ended a few hundred tokens in, whatever came after it. The study measured the
Code screen's prefix ending about 318 tokens in, and the cron's first step hitting the cache 14.2%
of the time on average, with a median of 0.

Everything that changes per turn now travels in one block at the head of the turn's own user
message, between :data:`TURN_CONTEXT_OPEN` and :data:`TURN_CONTEXT_CLOSE`. Two things hold:
- Within a run the message list only grows, so every step after the first re-sends a prefix the
  provider has just seen.
- When the run ends, `Agent` puts the bare user message back into the transcript. A conversation
  that stores the transcript therefore never stores a stale copy of this block. That was the reason
  these notes lived in the system message in the first place.

It rides inside the user message rather than as a message of its own. Some providers refuse two user
messages in a row, and a system message in the middle of a conversation is moved to the top by
others, where it would break the cache again.

This block also carries the facts every vendor prompt the study read injects, and Chimera's did not:
the date, the system and shell, the working directory, the state of git.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
from datetime import datetime
from pathlib import Path

TURN_CONTEXT_OPEN = (
    "<turn-context: facts about this turn, rebuilt every turn. They are not the user's words; the "
    "user's message follows the closing tag.>"
)
TURN_CONTEXT_CLOSE = "</turn-context>"
#: The header of recalled facts inside the turn context. Memory is recall, not proof of the present.
FACTS_HEADER = (
    "Relevant facts from memory (recalled, possibly stale: what the conversation and your tools show "
    "now wins over them):"
)


def _shell() -> str:
    for var in ("SHELL", "COMSPEC"):
        value = os.environ.get(var, "").strip()
        if value:
            return Path(value).name
    return ""


def _git(cwd: Path) -> str:
    """``branch X, N changed files`` for a repository, or "" when ``cwd`` is not one (or git is absent)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), "status", "--porcelain=v1", "--branch"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if out.returncode != 0 or not out.stdout.startswith("## "):
        return ""
    lines = out.stdout.splitlines()
    header = lines[0][3:]
    if header.startswith("No commits yet on "):  # a fresh repository says it this way
        branch = f"{header.removeprefix('No commits yet on ').strip()} (no commits yet)"
    elif header.startswith("HEAD (no branch)"):
        branch = "none (detached HEAD)"
    else:
        branch = header.split("...", 1)[0].strip()
    changed = len(lines) - 1
    return f"branch {branch}, {changed} changed file{'s' if changed != 1 else ''}"


def environment_facts(cwd: Path | None, *, now: datetime | None = None, git: bool = True) -> str:
    """Date, system, shell, working directory and git state, as one short block.

    ``now`` defaults to the local time, with its offset. A date without an offset is how a model
    turns "tomorrow" into the wrong day for a person eight hours away. Git is read with a short
    timeout and is left out, never guessed, when it cannot be read.
    """
    moment = (now or datetime.now().astimezone())
    offset = moment.strftime("%z")
    zone = f"UTC{offset[:3]}:{offset[3:]}" if offset else "local time"
    lines = [
        "Environment at the start of this turn (a snapshot; it does not update during the turn):",
        f"- date: {moment.strftime('%A %Y-%m-%d, %H:%M')} ({zone})",
    ]
    system = f"{platform.system()} {platform.release()}".strip()
    shell = _shell()
    lines.append(f"- system: {system}" + (f"; shell: {shell}" if shell else ""))
    if cwd is not None:
        lines.append(f"- working directory: {cwd}")
        state = _git(Path(cwd)) if git else ""
        if state:
            lines.append(f"- git: {state}")
    return "\n".join(lines)


def facts_block(facts: list[str]) -> str:
    """Recalled facts under :data:`FACTS_HEADER`, or "" when there are none."""
    return FACTS_HEADER + "\n" + "\n".join(f"- {f}" for f in facts) if facts else ""


def cited_fact(content: str, *, source: str, saved: float | None) -> str:
    """One recalled fact, quoted, with where it came from and when it was written.

    Study 25 S13: memory is recall, not proof of the present, and the model can only weigh a fact
    by its age and origin if it is shown them. Quoted through JSON rather than with bare marks, so
    a stored fact that contains a quotation mark cannot close the quote and read as the harness's
    own words. A fact written before dates were recorded says so, and is never given today's date.
    """
    when = datetime.fromtimestamp(saved).strftime("%Y-%m-%d") if saved else "date not recorded"
    return f"{json.dumps(content, ensure_ascii=False)} (source: {source or 'unknown'}, saved {when})"


def turn_context(*parts: str) -> str:
    """The non-empty ``parts`` between the markers, or "" when every part is empty."""
    body = "\n\n".join(p.strip() for p in parts if p and p.strip())
    return f"{TURN_CONTEXT_OPEN}\n{body}\n{TURN_CONTEXT_CLOSE}" if body else ""
