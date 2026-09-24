"""``chimera decide`` — typed questions over a state, or over every line of a JSONL file.

Study 22, phase 4. The questions file is the request shape of `chimera/decisions/interface.py` without
the state (``{"questions": {...}, "decision": "..."}``, or the ``questions`` object alone). The backend
is the configured one — by default a small local model through Ollama, so a thousand decisions cost
nothing but time. Every answer is written to the decision log unless ``--no-log``.
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

console = Console(stderr=True)


def _questions(path: Path) -> dict[str, Any]:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        console.print(f"[red]cannot read {path}: {exc}[/red]")
        raise typer.Exit(code=2) from exc
    if isinstance(body, dict) and "questions" in body:
        return body
    return {"questions": body}


def decide(
    questions: str = typer.Option(..., "--questions", "-q", help="JSON file: the questions (request shape, no state)."),
    state: str = typer.Option("", "--state", "-s", help="The state to ask about."),
    jsonl: str | None = typer.Option(None, "--jsonl", help="Ask about every line of this JSONL file instead."),
    field: str = typer.Option("state", "--field", help="With --jsonl: the field that holds the state."),
    out: str | None = typer.Option(None, "--out", "-o", help="With --jsonl: write results here (default: stdout)."),
    decision: str = typer.Option("", "--decision", help="Name the decision (the key a calibration map is found by)."),
    no_log: bool = typer.Option(False, "--no-log", help="Do not write the answers to the decision log."),
) -> None:
    """Ask typed questions — yes/no, a choice, a score — and get probabilities back.

    Every question is read on its own, decision-first; a question the linter rejects is refused before
    any call. `noul` is P(yes); `confidence` describes how peaked the probabilities are and is not a
    probability of being right. A number is calibrated only where a map exists for exactly this question.

    Measured on a ruler we did not build (`bench/jevbench_local`, the 231 public JevBench items): the
    default local backend answers 0.619 of them right (Jev 1.13: 0.866), 0.324 on the hard tier, with a
    raw top-label ECE of 0.218. Options that share a first token cannot be read locally: name them so
    their first words differ.
    """
    from chimera.config import get_settings
    from chimera.decisions.factory import build_decider
    from chimera.decisions.interface import RequestError, parse_request
    from chimera.decisions.interface import decide as ask

    if bool(state) == bool(jsonl):
        console.print("[yellow]give --state or --jsonl, one of them[/yellow]")
        raise typer.Exit(code=2)
    template = _questions(Path(questions))
    if decision:
        template["decision"] = decision
    try:
        parse_request({**template, "state": "probe"})  # refuse a bad question before any model call
    except RequestError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    decider = build_decider(get_settings(), log=not no_log)
    if state:
        typer.echo(json.dumps(ask(decider, {**template, "state": state}), ensure_ascii=False, indent=2))
        return
    done = errors = skipped = 0
    t0 = time.perf_counter()
    with Path(out).open("w", encoding="utf-8") if out else contextlib.nullcontext(sys.stdout) as sink:
        for n, raw in enumerate(Path(jsonl or "").read_text(encoding="utf-8").splitlines(), start=1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
                text = row.get(field) if isinstance(row, dict) else None
            except ValueError:
                text = None
            # A state may be an object or a list too (the SDK's shape; the interface renders it).
            if not text or (isinstance(text, str) and not text.strip()) or not isinstance(text, str | dict | list):
                skipped += 1
                continue
            result = ask(decider, {**template, "state": text})
            errors += sum(1 for a in result["answers"].values() if "error" in a)
            ident = row.get("id", n) if isinstance(row, dict) else n
            sink.write(json.dumps({"id": ident, "answers": result["answers"]}, ensure_ascii=False) + "\n")
            done += 1
    seconds = time.perf_counter() - t0
    console.print(
        f"[dim]{done} states in {seconds:.1f}s ({seconds / max(done, 1):.2f}s each) · "
        f"{errors} question errors · {skipped} lines skipped (no '{field}')[/dim]"
    )
