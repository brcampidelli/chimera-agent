"""Measure how often trace steps batch both read and write tools.

Reads existing ``traces.jsonl`` steplogs only; it does not run an agent or modify trace files.
By default the root is Chimera's configured home. Pass ``--root`` to inspect another data folder
or a single JSONL trace.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Keep this explicit: an unlisted tool is reported as unknown, never silently assigned to a side.
TOOL_KINDS: dict[str, str] = {
    # Read-only tools
    "read_file": "read",
    "read_document": "read",
    "read_email": "read",
    "list_dir": "read",
    "glob": "read",
    "grep": "read",
    "find": "read",
    "search": "read",
    "http_get": "read",
    "web_search": "read",
    "web_fetch": "read",
    "scrape": "read",
    "extract": "read",
    "map": "read",
    "crawl": "read",
    "browser": "read",
    "youtube_transcript": "read",
    "arxiv_search": "read",
    "recall_history": "read",
    "calendar_events": "read",
    "todo_write": "read",
    "tool_list": "read",
    "tool_describe": "read",
    "mcp_list": "read",
    "mcp_describe": "read",
    "echo": "read",
    "ask_user": "read",
    # Tools that can change files, state, or an external system
    "write_file": "write",
    "edit_file": "write",
    "apply_patch": "write",
    "edit_batch": "write",
    "run_shell": "write",
    "execute_code": "write",
    "code_interpreter": "write",
    "download_media": "write",
    "transcribe_audio": "write",
    "render_chart": "write",
    "click": "write",
    "type": "write",
    "mcp_call": "write",
    "tool_call": "write",
    "open_pull_request": "write",
    "send_email": "write",
    "create_document": "write",
    "decide": "write",
    "design": "write",
    "schedule_once": "write",
    "job_status": "read",
    "job_cancel": "write",
    "generate_image": "write",
    "text_to_speech": "write",
    "report_defect": "write",
}

TRACE_NAMES = frozenset(
    {"traces.jsonl", "traces.jsonl.1", "cron_traces.jsonl", "cron_traces.jsonl.1"}
)


@dataclass
class ModelCounts:
    steps: int = 0
    batch_steps: int = 0
    mixed_steps: int = 0


@dataclass
class Census:
    total_steps: int = 0
    batch_steps: int = 0
    mixed_steps: int = 0
    models: dict[str, ModelCounts] = field(default_factory=lambda: defaultdict(ModelCounts))
    unknown_tools: Counter[str] = field(default_factory=Counter)
    unreadable_files: int = 0
    unreadable_directories: int = 0
    unreadable_lines: int = 0
    files_read: int = 0


def default_root() -> Path:
    """Resolve the same home Chimera's trace writer uses, without creating it."""
    from chimera.config import get_settings

    return Path(get_settings().home)


def _trace_files(root: Path, census: Census) -> list[Path]:
    if root.is_file():
        return [root]
    if not root.is_dir():
        return []
    found: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False) and entry.name in TRACE_NAMES:
                            found.append(Path(entry.path))
                    except OSError:
                        # The entry could not be inspected; don't let it disappear from the report.
                        census.unreadable_files += 1
        except OSError:
            # We cannot know how many trace files an inaccessible directory contains.
            census.unreadable_directories += 1
    return sorted(found)


def _tool_name(value: Any) -> str:
    if not isinstance(value, dict):
        return "<invalid-tool-record>"
    name = value.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else "<missing-name>"


def _read_trace(path: Path, census: Census) -> None:
    try:
        with path.open("rb") as trace:
            census.files_read += 1
            for raw_line in trace:
                if not raw_line.strip():
                    continue
                try:
                    run = json.loads(raw_line.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    census.unreadable_lines += 1
                    continue
                if not isinstance(run, dict) or not isinstance(run.get("steps", []), list):
                    census.unreadable_lines += 1
                    continue
                for step in run.get("steps", []):
                    if not isinstance(step, dict):
                        census.unreadable_lines += 1
                        continue
                    tools = step.get("tools") or []
                    if not isinstance(tools, list):
                        census.unreadable_lines += 1
                        continue
                    names = [_tool_name(tool) for tool in tools]
                    kinds = set()
                    for name in names:
                        kind = TOOL_KINDS.get(name)
                        if kind is None:
                            census.unknown_tools[name] += 1
                        else:
                            kinds.add(kind)
                    model = step.get("model")
                    model_name = model.strip() if isinstance(model, str) and model.strip() else "?"
                    counts = census.models[model_name]
                    counts.steps += 1
                    census.total_steps += 1
                    if len(names) >= 2:
                        counts.batch_steps += 1
                        census.batch_steps += 1
                        if kinds == {"read", "write"}:
                            counts.mixed_steps += 1
                            census.mixed_steps += 1
    except OSError:
        census.unreadable_files += 1


def collect(root: Path) -> Census:
    census = Census()
    files = _trace_files(root, census)
    if not files and root.is_file():
        census.unreadable_files += 1
    for path in files:
        _read_trace(path, census)
    return census


def _fraction(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator:.2%}" if denominator else "n/a"


def report(census: Census, root: Path) -> str:
    rows = [
        f"Trace root: {root}",
        f"Files read: {census.files_read}",
        f"Total steps: {census.total_steps}",
        f"Steps with batch (>=2 tool calls): {census.batch_steps}",
        f"Mixed read+write batch steps: {census.mixed_steps}",
        f"Mixed / all steps: {_fraction(census.mixed_steps, census.total_steps)}",
        f"Mixed / batch steps: {_fraction(census.mixed_steps, census.batch_steps)}",
        "By model:",
    ]
    for model, counts in sorted(census.models.items()):
        rows.append(
            f"  {model}: steps={counts.steps}, batches={counts.batch_steps}, "
            f"mixed={counts.mixed_steps}, mixed/all={_fraction(counts.mixed_steps, counts.steps)}, "
            f"mixed/batches={_fraction(counts.mixed_steps, counts.batch_steps)}"
        )
    rows.append("Unknown tools (call counts):")
    if census.unknown_tools:
        rows.extend(f"  {name}: {count}" for name, count in sorted(census.unknown_tools.items()))
    else:
        rows.append("  none")
    rows.extend(
        [
            f"Unreadable files: {census.unreadable_files}",
            f"Unreadable directories: {census.unreadable_directories}",
            f"Unreadable lines/records: {census.unreadable_lines}",
        ]
    )
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None, help="trace file or Chimera data directory")
    args = parser.parse_args(argv)
    root = (args.root or default_root()).expanduser()
    try:
        exists = root.exists()
    except OSError:
        exists = False
    if not exists:
        print(f"Trace root does not exist or cannot be inspected: {root}")
        print(report(Census(unreadable_files=1), root))
        return 1
    census = collect(root)
    print(report(census, root))
    if census.files_read == 0 and census.unreadable_files == 0:
        print("No trace files found.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
